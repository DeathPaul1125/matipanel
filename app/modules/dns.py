"""DNS: BIND 9 (named) — zonas primarias, inversas y secundarias, registros, reenviadores y pruebas con dig.

Solo administra las zonas declaradas en /etc/bind/named.conf.local (layout Debian). Cada cambio:
respaldo <archivo>.matipanel.bak → escritura atómica → named-checkconf / named-checkzone →
si falla se revierte y se muestra la salida; si pasa, rndc reload (o systemctl reload named).
"""
import datetime
import hashlib
import ipaddress
import os
import re
import shutil
import stat
import tempfile
import threading
from collections import namedtuple
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..distro import detect
from ..util import run
from .packages import apt_get

dns_bp = Blueprint("dns", __name__)

UNIT = "named"  # en Debian bind9.service es un alias
BIND_DIR = "/etc/bind"
CONF_LOCAL = "/etc/bind/named.conf.local"
CONF_OPTIONS = "/etc/bind/named.conf.options"
ZONES_DIR = "/etc/bind/zones"
CACHE_DIR = "/var/cache/bind"
PACKAGES = ("bind9", "bind9-utils", "bind9-dnsutils")
ZONE_FILE_BASES = ("/etc/bind/", "/var/cache/bind/", "/var/lib/bind/")
BAK = ".matipanel.bak"
DEFAULT_TTL = 3600
SOA_TIMERS = {"refresh": 3600, "retry": 900, "expire": 1209600, "minimum": 300}
EDIT_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA", "PTR")
DIG_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA", "SRV", "CAA", "PTR", "ANY")
CAA_TAGS = ("issue", "issuewild", "iodef")
TXT_MAX = 4000

_LBL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
DOMAIN_RE = re.compile(rf"^(?=.{{1,253}}$)(?:{_LBL}\.)+{_LBL}$")          # zonas nuevas: estricto
_ZL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
ZONE_RE = re.compile(rf"^(?=.{{1,253}}$)(?:{_ZL}\.)*{_ZL}$")             # zonas existentes
_HL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
HOST_RE = re.compile(rf"^(?=.{{1,254}}$)(?:{_HL}\.)*{_HL}\.?$")
OWNER_RE = re.compile(rf"^(?:\*|(?:\*\.)?(?:{_HL}\.)*{_HL})$")
EMAIL_RE = re.compile(r"^([A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64})*)@([A-Za-z0-9.-]{1,253})$")
TTL_RE = re.compile(r"^(?:\d+|(?:\d+[smhdw])+)$", re.I)
SRV_OWNER_RE = re.compile(r"^_[a-z0-9-]+\._[a-z0-9-]+\.")
CAA_VALUE_RE = re.compile(r'^[\x20-\x7e]{0,255}$')

_LOCK = threading.Lock()  # gevent parchea threading: serializa lecturas-modificación-escritura


class DnsError(ValueError):
    """Error de validación u operación; el blueprint lo convierte en {"ok": false, "error", "output"}."""

    def __init__(self, msg, code=400, output=""):
        super().__init__(msg)
        self.msg, self.code, self.output = msg, code, output


@dns_bp.errorhandler(DnsError)
def _dns_error(e):
    body = {"ok": False, "error": e.msg}
    if e.output:
        body["output"] = e.output[-20000:]
    return jsonify(body), e.code


# ── named.conf: tokenizador y parser ─────────────────────────────────

def conf_tokens(text):
    """Tokens de named.conf: (tipo, valor, inicio, fin). Tipos: word, str, {, }, ;  (sin comentarios)."""
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "#" or text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            toks.append(("str", text[i + 1:min(j, n)], i, min(j + 1, n)))
            i = j + 1
        elif c in "{};":
            toks.append((c, c, i, i + 1))
            i += 1
        else:
            j = i
            while (j < n and not text[j].isspace() and text[j] not in '{};"#'
                   and not text.startswith("//", j) and not text.startswith("/*", j)):
                j += 1
            toks.append(("word", text[i:j], i, j))
            i = j
    return toks


def _conf_stmts(toks, i):
    out = []
    while i < len(toks):
        if toks[i][0] == "}":
            return out, i
        if toks[i][0] == ";":
            i += 1
            continue
        st = {"args": [], "blocks": [], "start": toks[i][2], "end": toks[i][3]}
        while i < len(toks) and toks[i][0] not in (";", "}"):
            kind, val, s, e = toks[i]
            if kind == "{":
                sub, i = _conf_stmts(toks, i + 1)
                close = toks[i][2] if i < len(toks) else None
                st["blocks"].append({"stmts": sub, "open": s, "close": close})
                if i < len(toks):
                    st["end"] = toks[i][3]
                    i += 1
            else:
                st["args"].append(val)
                st["end"] = e
                i += 1
        if i < len(toks) and toks[i][0] == ";":
            st["end"] = toks[i][3]
            i += 1
        out.append(st)
    return out, i


def conf_parse(text):
    """Sentencias de primer nivel: {"args": [...], "blocks": [{"stmts", "open", "close"}], "start", "end"}."""
    return _conf_stmts(conf_tokens(text), 0)[0]


def _items(block):
    return [" ".join(s["args"]) for s in block["stmts"] if s["args"]]


def _find(stmts, key):
    return next((s for s in stmts if s["args"][:1] == [key]), None)


def parse_zones(text):
    """Bloques zone de primer nivel de named.conf.local."""
    zones = []
    for st in conf_parse(text):
        a = st["args"]
        if len(a) < 2 or a[0] != "zone" or not st["blocks"]:
            continue
        z = {"name": a[1].lower().rstrip("."), "type": "", "file": "", "primaries": [],
             "allow_transfer": [], "also_notify": [], "dynamic": False,
             "start": st["start"], "end": st["end"]}
        for s in st["blocks"][0]["stmts"]:
            key, rest = (s["args"] or [""])[0], s["args"][1:]
            if key == "type" and rest:
                z["type"] = {"master": "primary", "slave": "secondary"}.get(rest[0], rest[0])
            elif key == "file" and rest:
                z["file"] = rest[0]
            elif key in ("masters", "primaries") and s["blocks"]:
                z["primaries"] = _items(s["blocks"][0])
            elif key == "allow-transfer" and s["blocks"]:
                z["allow_transfer"] = _items(s["blocks"][0])
            elif key == "also-notify" and s["blocks"]:
                z["also_notify"] = _items(s["blocks"][0])
            elif key == "update-policy" or (key == "allow-update" and s["blocks"]
                                            and _items(s["blocks"][0]) != ["none"]):
                z["dynamic"] = True
        zones.append(z)
    return zones


def _expand_line(text, a, b):
    """Amplía [a, b) a líneas completas si la sentencia ocupa sola su(s) línea(s)."""
    ls = text.rfind("\n", 0, a) + 1
    if text[ls:a].strip() == "":
        m = re.compile(r"[ \t]*(?:\n|$)").match(text, b)
        if m:
            return ls, m.end()
    return a, b


def remove_span(text, a, b):
    a, b = _expand_line(text, a, b)
    if text[:a].endswith("\n\n") and text[b:b + 1] == "\n":  # no dejar dos líneas en blanco seguidas
        b += 1
    return text[:a] + text[b:]


def set_stmt(text, block, key, new_stmt, indent="\t"):
    """Reemplaza, inserta o elimina (new_stmt=None) la sentencia `key` dentro de `block`."""
    st = _find(block["stmts"], key)
    if st:
        if new_stmt is None:
            return remove_span(text, st["start"], st["end"])
        return text[:st["start"]] + new_stmt + text[st["end"]:]
    if new_stmt is None:
        return text
    close = block["close"]
    if close is None:
        raise DnsError("Bloque sin cerrar en la configuración")
    ls = text.rfind("\n", 0, close) + 1
    if text[ls:close].strip() == "":
        return text[:ls] + indent + new_stmt + "\n" + text[ls:]
    return text[:close] + "\n" + indent + new_stmt + "\n" + text[close:]


def options_block(text):
    st = next((s for s in conf_parse(text) if s["args"][:1] == ["options"] and s["blocks"]), None)
    if not st:
        raise DnsError("No se encontró el bloque options { … }; en " + CONF_OPTIONS)
    return st


def zone_block(text, name):
    st = next((s for s in conf_parse(text) if len(s["args"]) >= 2 and s["args"][0] == "zone"
               and s["args"][1].lower().rstrip(".") == name and s["blocks"]), None)
    if not st:
        raise DnsError("La zona no está en " + CONF_LOCAL, 404)
    return st


def _addr_list(ips):
    return "{ " + " ".join(f"{ip};" for ip in ips) + " }"


def zone_conf(name, ztype, path, extra=()):
    lines = [f'zone "{name}" {{', f"\ttype {ztype};", f'\tfile "{path}";'] + [f"\t{x}" for x in extra]
    return "\n".join(lines + ["};"]) + "\n"


def append_block(text, block):
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text.strip() else "") + block


# ── Archivos de zona: tokenizador, parser y serializador ────────────

Tok = namedtuple("Tok", "text ln cs ce quoted")


def zone_lex(lines):
    """Entradas lógicas (agrupa paréntesis multilínea).
    Cada una: {"toks": [Tok], "lead_ws", "comments": [str], "first", "last"}."""
    out, cur, depth = [], None, 0
    for ln, line in enumerate(lines):
        if cur is None:
            cur = {"toks": [], "lead_ws": line[:1] in (" ", "\t"), "comments": [], "first": ln, "last": ln}
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if c in " \t\r":
                i += 1
            elif c == ";":
                cur["comments"].append(line[i + 1:].strip())
                break
            elif c in "()":
                depth = depth + 1 if c == "(" else max(0, depth - 1)
                i += 1
            elif c == '"':
                j = i + 1
                while j < n and line[j] != '"':
                    j += 2 if line[j] == "\\" else 1
                cur["toks"].append(Tok(line[i:min(j + 1, n)], ln, i, min(j + 1, n), True))
                i = j + 1
            else:
                j = i
                while j < n and line[j] not in ' \t\r;()"':
                    j += 2 if line[j] == "\\" else 1
                j = min(j, n)
                cur["toks"].append(Tok(line[i:j], ln, i, j, False))
                i = j
        cur["last"] = ln
        if depth == 0:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out


def fqdn(name):
    return name.strip().lower().rstrip(".") + "."


def abs_name(name, origin):
    if name == "@":
        return origin
    if name.endswith(".") and not name.endswith("\\."):
        return name.lower()
    return (name + "." + origin if origin != "." else name + ".").lower()


def rel_name(name, origin):
    if name == origin:
        return "@"
    if origin != "." and name.endswith("." + origin):
        return name[:-len(origin) - 1]
    return name


_NAME_FIELDS = {"CNAME": (0,), "NS": (0,), "PTR": (0,), "DNAME": (0,), "MX": (1,), "SRV": (3,)}


def parse_zone(text, zone):
    """Parsea un archivo de zona. Header = todo hasta el final del SOA (se conserva literal);
    el resto son registros que se regeneran en formato canónico al guardar."""
    origin = fqdn(zone)
    lines = text.replace("\r\n", "\n").split("\n")
    cur_origin, ttl_default, last_owner = origin, None, None
    soa, header_end, header_origin, header_ttl = None, -1, origin, None
    fixed, body, pre, readonly = [], [], [], ""
    for e in zone_lex(lines):
        toks = e["toks"]
        if not toks:
            raw = "\n".join(lines[e["first"]:e["last"] + 1]).rstrip()
            if raw or not pre or pre[-1] != "":  # colapsa líneas en blanco seguidas
                pre.append(raw)
            continue
        first = toks[0].text
        if not e["lead_ws"] and first.startswith("$"):
            d = first.upper()
            if d == "$TTL" and len(toks) > 1 and TTL_RE.match(toks[1].text):
                ttl_default = toks[1].text
            elif d == "$ORIGIN" and len(toks) > 1:
                cur_origin = abs_name(toks[1].text, cur_origin)
            else:
                readonly = readonly or f"El archivo usa {d}; edítalo a mano."
            continue
        i = 0
        if e["lead_ws"]:
            owner = last_owner or cur_origin
        else:
            owner, i = abs_name(first, cur_origin), 1
        ttl = cls = None
        while i < len(toks) and not toks[i].quoted:
            t = toks[i].text
            if cls is None and t.upper() in ("IN", "CH", "HS", "CS"):
                cls = t.upper()
            elif ttl is None and TTL_RE.match(t):
                ttl = t
            else:
                break
            i += 1
        if i >= len(toks):
            readonly = readonly or f"Línea {e['first'] + 1} no reconocida; edita el archivo a mano."
            continue
        rtype, rd = toks[i].text.upper(), toks[i + 1:]
        last_owner = owner
        rdata = [t.text for t in rd]
        for k in _NAME_FIELDS.get(rtype, ()):
            if k < len(rdata) and not rd[k].quoted:
                rdata[k] = abs_name(rdata[k], cur_origin)
        rec = {"owner": owner, "ttl": ttl, "dttl": ttl_default, "cls": cls or "IN", "type": rtype,
               "rdata": rdata, "comment": " ".join(c for c in e["comments"] if c) or None,
               "pre": pre, "origin": cur_origin, "line": e["first"] + 1}
        pre = []
        if rtype == "SOA" and soa is None:
            if len(rd) < 7:
                readonly = readonly or "El registro SOA está incompleto."
            soa, header_end, header_origin, header_ttl = rec, e["last"], cur_origin, ttl_default
            soa["toks"] = rd
            continue
        (fixed if soa is None else body).append(rec)
    if soa is None:
        readonly = readonly or "No se encontró el registro SOA."
    for r in body:  # $TTL cambiado a mitad del archivo: se fija explícito para no alterar el TTL efectivo
        if r["ttl"] is None and r["dttl"] is not None and r["dttl"] != header_ttl:
            r["ttl"] = r["dttl"]
    for r in fixed:
        r["fixed"] = True
    return {"origin": origin, "lines": lines, "soa": soa, "header_end": header_end,
            "header_origin": header_origin, "header_ttl": header_ttl, "fixed": fixed,
            "records": body, "trailing": pre, "readonly": readonly}


def soa_values(z):
    s = z["soa"]
    if not s or len(s["toks"]) < 7:
        return None
    v = [t.text for t in s["toks"][:7]]
    keys = ("mname", "rname", "serial", "refresh", "retry", "expire", "minimum")
    out = dict(zip(keys, v))
    out["mname"] = abs_name(out["mname"], s["origin"])
    out["email"] = rname_to_email(abs_name(out["rname"], s["origin"]))
    return out


def next_serial(old, today=None):
    """Formato AAAAMMDDnn: siempre mayor que el anterior."""
    base = int((today or datetime.date.today()).strftime("%Y%m%d")) * 100
    new = max(old + 1, base)
    return new if new <= 0xFFFFFFFF else ((old + 1) % (1 << 32)) or 1


def fmt_rr(owner, ttl, cls, rtype, rdata):
    rd = " ".join(rdata)
    if rtype == "TXT" and len(rdata) > 1:  # TXT largos (DKIM): una cadena por línea
        rd = "( " + "\n\t\t\t\t\t".join(rdata) + " )"
    return f"{owner:<23} {ttl or '':<7} {cls:<3} {rtype:<6} {rd}".rstrip()


def render_zone(z, soa_set=None):
    """soa_set: {índice del campo SOA (0-6): nuevo texto}. Mantiene el header literal salvo esos tokens."""
    lines = list(z["lines"][:z["header_end"] + 1])
    edits = sorted(((z["soa"]["toks"][k], v) for k, v in (soa_set or {}).items()),
                   key=lambda x: (x[0].ln, x[0].cs), reverse=True)
    for tok, val in edits:
        ln = lines[tok.ln]
        lines[tok.ln] = ln[:tok.cs] + val + ln[tok.ce:]
    cur = z["header_origin"]
    for r in z["records"]:
        lines.extend(r["pre"])
        if r["origin"] != cur:
            lines.append(f"$ORIGIN {r['origin']}")
            cur = r["origin"]
        line = fmt_rr(rel_name(r["owner"], cur), r["ttl"], r["cls"], r["type"], r["rdata"])
        lines.append(line + (" ; " + r["comment"] if r["comment"] else ""))
    lines.extend(z["trailing"])
    return "\n".join(lines).rstrip("\n") + "\n"


def bump_serial(z, soa_set=None):
    soa_set = dict(soa_set or {})
    old = z["soa"]["toks"][2].text
    if not old.isdigit():
        raise DnsError("El serial del SOA no es numérico")
    new = next_serial(int(old))
    soa_set[2] = str(new)
    return soa_set, new


# ── TXT / CAA ────────────────────────────────────────────────────────

def unquote(tok):
    """Texto de zona (con o sin comillas, escapes \\X y \\DDD) → bytes."""
    s = tok[1:-1] if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"' else tok
    raw, out, i = s.encode("utf-8", "surrogateescape"), bytearray(), 0
    while i < len(raw):
        if raw[i] == 0x5C and i + 1 < len(raw):
            d = raw[i + 1:i + 4]
            if len(d) == 3 and d.isdigit():
                out.append(int(d) & 0xFF)
                i += 4
                continue
            out.append(raw[i + 1])
            i += 2
            continue
        out.append(raw[i])
        i += 1
    return bytes(out)


def quote_bytes(b):
    out = []
    for ch in b:
        if ch in (0x22, 0x5C):
            out.append("\\" + chr(ch))
        elif 0x20 <= ch <= 0x7E:
            out.append(chr(ch))
        else:
            out.append(f"\\{ch:03d}")
    return '"' + "".join(out) + '"'


def txt_encode(text):
    """Texto → cadenas entre comillas de hasta 255 bytes cada una."""
    data = text.encode("utf-8")
    return [quote_bytes(data[i:i + 255]) for i in range(0, len(data), 255)] or ['""']


def txt_decode(rdata):
    return b"".join(unquote(x) for x in rdata).decode("utf-8", "replace")


# ── Validación de nombres y construcción de registros ───────────────

def _is_ip(v):
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def valid_domain(d):
    d = str(d or "").strip().lower().rstrip(".")
    if not DOMAIN_RE.match(d):
        raise DnsError("Dominio inválido (ej. ejemplo.com; solo letras, números, guiones y puntos)")
    return d


def email_to_rname(email, fallback_domain):
    email = str(email or "").strip() or f"hostmaster@{fallback_domain}"
    m = EMAIL_RE.match(email)
    if not m or not DOMAIN_RE.match(m.group(2).lower()):
        raise DnsError("Correo del administrador inválido")
    return m.group(1).replace(".", "\\.") + "." + m.group(2).lower() + "."


def rname_to_email(rname):
    m = re.match(r"^((?:[^.\\]|\\.)*)\.(.*)$", rname)
    if not m:
        return rname
    return m.group(1).replace("\\.", ".") + "@" + m.group(2).rstrip(".")


def owner_abs(name, origin, rtype=""):
    n = str(name or "").strip().lower()
    if n in ("", "@"):
        return origin
    if rtype == "PTR" and origin.endswith(("in-addr.arpa.", "ip6.arpa.")) and _is_ip(n):
        n = ipaddress.ip_address(n).reverse_pointer + "."
    if n.endswith("."):
        a = n
    elif n == origin[:-1] or n.endswith("." + origin[:-1]):
        a = n + "."  # escribieron el nombre completo sin punto final
    else:
        a = n + "." + origin
    if a != origin and not a.endswith("." + origin):
        raise DnsError(f"El nombre debe pertenecer a la zona {origin[:-1]}")
    if a != origin and not OWNER_RE.match(a[:-len(origin) - 1]):
        raise DnsError("Nombre inválido: usa letras, números, guiones, _ y puntos (o * al inicio)")
    if len(a) > 254:
        raise DnsError("Nombre demasiado largo")
    return a


def target_abs(value, origin, allow_root=False):
    """Destino de CNAME/MX/NS/SRV/PTR. Con punto final o con algún punto = nombre completo;
    una sola etiqueta (ej. «mail») = dentro de la zona; «@» = la zona."""
    v = str(value or "").strip()
    if allow_root and v == ".":
        return "."
    if v == "@":
        return origin
    if _is_ip(v.rstrip(".")):
        raise DnsError("Aquí va un nombre de host, no una dirección IP")
    if not HOST_RE.match(v):
        raise DnsError(f"Nombre de host inválido: {v[:80]}")
    v = v.lower()
    if v.endswith("."):
        return v
    return v + "." if "." in v else v + "." + origin


def _int(v, lo, hi, label):
    s = str(v if v is not None else "").strip()
    if not s.isdigit() or not lo <= int(s) <= hi:
        raise DnsError(f"{label}: número entre {lo} y {hi}")
    return str(int(s))


def build_rdata(rtype, f, origin, owner):
    if rtype == "A":
        try:
            return [str(ipaddress.IPv4Address(str(f.get("address", "")).strip()))]
        except ValueError:
            raise DnsError("Dirección IPv4 inválida")
    if rtype == "AAAA":
        try:
            return [ipaddress.IPv6Address(str(f.get("address", "")).strip()).compressed]
        except ValueError:
            raise DnsError("Dirección IPv6 inválida")
    if rtype == "CNAME":
        if owner == origin:
            raise DnsError("No se puede poner un CNAME en la raíz de la zona (@)")
        return [target_abs(f.get("target"), origin)]
    if rtype in ("NS", "PTR"):
        return [target_abs(f.get("target"), origin)]
    if rtype == "MX":
        return [_int(f.get("priority"), 0, 65535, "Prioridad"), target_abs(f.get("target"), origin)]
    if rtype == "SRV":
        if not SRV_OWNER_RE.match(owner):
            raise DnsError("El nombre de un SRV debe ser _servicio._protocolo (ej. _sip._tcp)")
        return [_int(f.get("priority"), 0, 65535, "Prioridad"), _int(f.get("weight"), 0, 65535, "Peso"),
                _int(f.get("port"), 0, 65535, "Puerto"), target_abs(f.get("target"), origin, allow_root=True)]
    if rtype == "TXT":
        text = str(f.get("text", ""))
        if len(text) > TXT_MAX:
            raise DnsError(f"Texto demasiado largo (máx. {TXT_MAX} caracteres)")
        if any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise DnsError("El texto no puede tener saltos de línea ni caracteres de control")
        return txt_encode(text)
    if rtype == "CAA":
        flags = _int(f.get("flags", 0), 0, 255, "Flags")
        tag = str(f.get("tag", "")).strip().lower()
        if tag not in CAA_TAGS:
            raise DnsError("Etiqueta CAA inválida (issue, issuewild o iodef)")
        val = str(f.get("value", "")).strip()
        if not CAA_VALUE_RE.match(val) or '"' in val or "\\" in val:
            raise DnsError("Valor CAA inválido (texto ASCII sin comillas, máx. 255)")
        if tag == "iodef" and not re.match(r"^(mailto:|https?://)\S+$", val):
            raise DnsError("iodef debe ser mailto:correo o una URL https://")
        if tag != "iodef" and val and not re.match(r"^[A-Za-z0-9.-]*(\s*;.*)?$", val):
            raise DnsError("Para issue/issuewild usa el dominio de la CA (ej. letsencrypt.org) o ;")
        return [flags, tag, f'"{val}"']
    raise DnsError("Tipo de registro no soportado")


def build_record(data, origin):
    rtype = str(data.get("type", "")).strip().upper()
    if rtype not in EDIT_TYPES:
        raise DnsError("Tipo de registro no soportado")
    owner = owner_abs(data.get("name", ""), origin, rtype)
    ttl = str(data.get("ttl", "") or "").strip()
    if ttl:
        ttl = _int(ttl, 0, 2147483647, "TTL (segundos)")
    return {"owner": owner, "ttl": ttl or None, "dttl": None, "cls": "IN", "type": rtype,
            "rdata": build_rdata(rtype, data, origin, owner), "comment": None, "pre": [],
            "origin": origin}


def record_fields(r):
    """Campos para el formulario de edición; None si el panel no sabe editarlo."""
    t, d = r["type"], r["rdata"]
    if r["cls"] != "IN":
        return None
    shapes = {"A": 1, "AAAA": 1, "CNAME": 1, "NS": 1, "PTR": 1, "MX": 2, "SRV": 4, "CAA": 3}
    if t in shapes and len(d) != shapes[t]:
        return None
    if t in ("A", "AAAA"):
        return {"address": d[0]}
    if t in ("CNAME", "NS", "PTR"):
        return {"target": d[0]}
    if t == "MX":
        return {"priority": d[0], "target": d[1]}
    if t == "SRV":
        return dict(zip(("priority", "weight", "port", "target"), d))
    if t == "TXT" and d:
        return {"text": txt_decode(d)}
    if t == "CAA":
        return {"flags": d[0], "tag": d[1].lower(), "value": unquote(d[2]).decode("utf-8", "replace")}
    return None


def record_key(r):
    raw = "|".join([r["owner"], r["ttl"] or "", r["type"], " ".join(r["rdata"]).lower()])
    return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def _same(a, b):
    return (a["owner"] == b["owner"] and a["type"] == b["type"]
            and " ".join(a["rdata"]).lower() == " ".join(b["rdata"]).lower())


def apply_record(z, new, idx=None, key=None):
    """Inserta (idx=None) o reemplaza un registro en z["records"] con validaciones de coherencia."""
    recs = z["records"]
    if idx is not None:
        if not (0 <= idx < len(recs)) or record_key(recs[idx]) != key:
            raise DnsError("La zona cambió desde que la abriste; recarga la lista", 409)
    others = [r for i, r in enumerate(recs + z["fixed"]) if i != idx]
    if any(_same(new, r) for r in others):
        raise DnsError("Ya existe un registro idéntico")
    at_owner = [r for r in others if r["owner"] == new["owner"]]
    if new["type"] == "CNAME" and at_owner:
        raise DnsError("Ese nombre ya tiene otros registros; un CNAME no puede convivir con ellos")
    if new["type"] != "CNAME" and any(r["type"] == "CNAME" for r in at_owner):
        raise DnsError("Ese nombre ya es un CNAME; no puede tener otros registros")
    if idx is not None:
        new["pre"], new["comment"], new["origin"] = recs[idx]["pre"], recs[idx]["comment"], recs[idx]["origin"]
        recs[idx] = new
        return
    pos = max((i for i, r in enumerate(recs) if r["owner"] == new["owner"]), default=len(recs) - 1) + 1
    recs.insert(pos, new)


def delete_record(z, idx, key):
    recs = z["records"]
    if not (0 <= idx < len(recs)) or record_key(recs[idx]) != key:
        raise DnsError("La zona cambió desde que la abriste; recarga la lista", 409)
    gone = recs.pop(idx)
    pre = [p for p in gone["pre"] if p]
    if pre:  # conserva comentarios de sección (ej. "; --- Correo ---") en el siguiente registro
        (recs[idx]["pre"] if idx < len(recs) else z["trailing"])[:0] = gone["pre"]


def new_zone_text(domain, ns, rname, records, today=None):
    serial = int((today or datetime.date.today()).strftime("%Y%m%d")) * 100 + 1
    t = SOA_TIMERS
    head = [
        f"; Zona {domain} - creada con MatiPanel el {(today or datetime.date.today()).isoformat()}",
        f"$TTL {DEFAULT_TTL}",
        f"@       IN  SOA {ns} {rname} (",
        f"            {serial:<10} ; serial (AAAAMMDDnn)",
        f"            {t['refresh']:<10} ; refresh",
        f"            {t['retry']:<10} ; retry",
        f"            {t['expire']:<10} ; expire",
        f"            {t['minimum']:<10} ; minimum (TTL de respuestas negativas)",
        "            )",
        "",
    ]
    origin = fqdn(domain)
    body = [fmt_rr(rel_name(o, origin), None, "IN", ty, rd) for o, ty, rd in records]
    return "\n".join(head + body) + "\n"


def _addr_rr(ip):
    return ("AAAA" if ipaddress.ip_address(ip).version == 6 else "A"), [str(ipaddress.ip_address(ip))]


# ── Sistema: archivos, comprobaciones y recarga ──────────────────────

def _installed():
    return os.path.isdir(BIND_DIR) and bool(shutil.which("named") or os.path.exists("/usr/sbin/named"))


def _need():
    if not _installed():
        raise DnsError("BIND 9 no está instalado", 404)


def _read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def _write(path, text):
    """Escritura atómica conservando dueño y permisos (archivos nuevos: root:bind 0644)."""
    st = os.stat(path) if os.path.exists(path) else None
    fd, tmp = tempfile.mkstemp(prefix=".matipanel.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
            f.write(text)
        if st:
            os.chown(tmp, st.st_uid, st.st_gid)
            os.chmod(tmp, stat.S_IMODE(st.st_mode))
        else:
            os.chmod(tmp, 0o644)
            try:
                shutil.chown(tmp, "root", "bind")
            except (LookupError, OSError):
                pass
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _backup(path):
    if os.path.exists(path):
        shutil.copy2(path, path + BAK)


def _out(code, out, err):
    return (out + ("\n" if out and err else "") + err).strip()


def _checkconf():
    code, out, err = run(["named-checkconf"], timeout=30)
    return code == 0, _out(code, out, err) or ("named-checkconf: OK" if code == 0 else f"código {code}")


def _checkzone(zone, path):
    # -i local: como named, solo comprueba integridad de nombres dentro de la zona (sin consultas externas)
    code, out, err = run(["named-checkzone", "-i", "local", zone, path], timeout=30)
    return code == 0, _out(code, out, err)


def _active():
    return run(["systemctl", "is-active", "--quiet", UNIT], timeout=10)[0] == 0


def _reload(zone=None):
    """(ok, mensaje). Con named detenido no recarga: se aplicará al iniciarlo."""
    if not _active():
        return True, "named no está en ejecución: los cambios se aplicarán cuando lo inicies."
    code, out, err = run(["rndc", "reload"] + ([zone] if zone else []), timeout=30)
    if code == 0:
        return True, _out(code, out, err) or "rndc reload: OK"
    code2, out2, err2 = run(["systemctl", "reload", UNIT], timeout=60)
    if code2 == 0:
        return True, "systemctl reload named: OK"
    return False, "No se pudo recargar named:\n" + _out(code, out, err) + "\n" + _out(code2, out2, err2)


def _bind_directory():
    try:
        st = _find(options_block(_read(CONF_OPTIONS))["blocks"][0]["stmts"], "directory")
        if st and len(st["args"]) > 1 and os.path.isabs(st["args"][1]):
            return st["args"][1]
    except (OSError, DnsError):
        pass
    return CACHE_DIR


def _zone_path(z, directory):
    f = z["file"]
    if not f:
        return ""
    return os.path.normpath(f if os.path.isabs(f) else os.path.join(directory, f))


def _path_ok(path):
    real = os.path.realpath(path)
    return bool(path) and real.startswith(ZONE_FILE_BASES) and os.path.isfile(real)


def _local_zones():
    text = _read(CONF_LOCAL) if os.path.exists(CONF_LOCAL) else ""
    zones, directory = parse_zones(text), _bind_directory()
    for z in zones:
        z["path"] = _zone_path(z, directory)
    return text, zones


def _get_zone(name, primary=True):
    name = str(name or "").strip().lower().rstrip(".")
    if not ZONE_RE.match(name):
        raise DnsError("Nombre de zona inválido")
    text, zones = _local_zones()
    z = next((z for z in zones if z["name"] == name), None)
    if not z:
        raise DnsError("La zona no existe en " + CONF_LOCAL, 404)
    if primary:
        if z["type"] != "primary":
            raise DnsError("Solo se pueden editar registros de zonas primarias")
        if not _path_ok(z["path"]):
            raise DnsError("El archivo de zona no existe o está fuera de /etc/bind, /var/cache/bind o /var/lib/bind", 404)
    return text, z


def _load_zone(z):
    zp = parse_zone(_read(z["path"]), z["name"])
    if z["dynamic"]:
        zp["readonly"] = "Zona dinámica (allow-update / update-policy): edítala con nsupdate."
    return zp


def _commit_zone(z, old_text, new_text):
    """Escribe, valida con named-checkzone (revierte si falla) y recarga la zona."""
    path = z["path"]
    _backup(path)
    _write(path, new_text)
    ok, out = _checkzone(z["name"], path)
    if not ok:
        _write(path, old_text)
        raise DnsError("named-checkzone rechazó el cambio; se restauró el archivo anterior", 409, out)
    rok, rmsg = _reload(z["name"])
    return {"ok": True, "output": (out + "\n\n" + rmsg).strip(), "warning": "" if rok else rmsg}


def _commit_conf(path, old_text, new_text, cleanup=()):
    """Escribe una config, valida con named-checkconf (revierte y borra `cleanup` si falla) y recarga."""
    existed = os.path.exists(path)
    _backup(path)
    _write(path, new_text)
    ok, out = _checkconf()
    if not ok:
        if existed:
            _write(path, old_text)
        else:
            os.unlink(path)
        for p in cleanup:
            if os.path.exists(p):
                os.unlink(p)
        raise DnsError("named-checkconf rechazó el cambio; se restauró la configuración anterior", 409, out)
    rok, rmsg = _reload()
    return {"ok": True, "output": (out + "\n\n" + rmsg).strip(), "warning": "" if rok else rmsg}


def _ensure_zones_dir():
    if not os.path.isdir(ZONES_DIR):
        os.makedirs(ZONES_DIR, mode=0o755)
        try:
            shutil.chown(ZONES_DIR, "root", "bind")
        except (LookupError, OSError):
            pass


def _create_primary_file(name, text):
    """Crea /etc/bind/zones/db.<zona>, la valida y la añade a named.conf.local."""
    conf, zones = _local_zones()
    if any(z["name"] == name for z in zones):
        raise DnsError(f"La zona {name} ya existe", 409)
    path = os.path.join(ZONES_DIR, "db." + name)
    if os.path.exists(path):
        raise DnsError(f"Ya existe el archivo {path}; bórralo o renómbralo antes", 409)
    _ensure_zones_dir()
    _write(path, text)
    ok, out = _checkzone(name, path)
    if not ok:
        os.unlink(path)
        raise DnsError("named-checkzone rechazó la zona; no se creó nada", 409, out)
    block = zone_conf(name, "primary", path, ["allow-transfer { none; };"])
    res = _commit_conf(CONF_LOCAL, conf, append_block(conf, block), cleanup=[path])
    res["output"] = (out + "\n" + res["output"]).strip()
    res["path"] = path
    return res


def _data():
    return request.get_json(silent=True) or {}


def _ip_list(raw, label, max_n=10):
    items = raw if isinstance(raw, list) else re.split(r"[\s,;]+", str(raw or ""))
    out = []
    for it in (str(x).strip() for x in items):
        if not it:
            continue
        try:
            ip = str(ipaddress.ip_address(it))
        except ValueError:
            raise DnsError(f"{label}: «{it[:60]}» no es una IP válida")
        if ip not in out:
            out.append(ip)
    if len(out) > max_n:
        raise DnsError(f"{label}: máximo {max_n} direcciones")
    return out


# ── Rutas ────────────────────────────────────────────────────────────

@dns_bp.route("/")
@login_required
def index():
    return render_template("dns.html", conf_local=CONF_LOCAL, conf_options=CONF_OPTIONS,
                           packages=" ".join(PACKAGES))


def _listening():
    code, out, _ = run(["ss", "-H", "-lntup"], timeout=10)
    seen, res = set(), []
    for line in out.splitlines() if code == 0 else []:
        p = line.split()
        if len(p) < 5 or not p[4].endswith(":53"):
            continue
        m = re.search(r'\(\("([^"]+)"', line)
        item = (p[0], p[4], m.group(1) if m else "")
        if item not in seen:
            seen.add(item)
            res.append({"proto": item[0], "addr": item[1], "proc": item[2]})
    return res


@dns_bp.route("/api/status")
@login_required
def status():
    d = detect()
    if not _installed():
        return jsonify({"installed": False, "can_install": d["pkg"] == "apt", "packages": list(PACKAGES)})
    _, active, _ = run(["systemctl", "is-active", UNIT], timeout=10)
    _, enabled, _ = run(["systemctl", "is-enabled", UNIT], timeout=10)
    code, out, err = run(["named", "-v"], timeout=10)
    m = re.search(r"BIND\s+\S+", out + err)
    _, ips, _ = run(["hostname", "-I"], timeout=10)
    try:
        nzones = len(_local_zones()[1])
    except OSError:
        nzones = 0
    return jsonify({"installed": True, "active": active.strip() or "unknown", "enabled": enabled.strip(),
                    "version": m.group(0) if m else (out + err).strip()[:80], "listen": _listening(),
                    "ips": [i for i in ips.split() if not i.startswith(("127.", "fe80", "::1"))],
                    "zones": nzones})


@dns_bp.route("/api/install", methods=["POST"])
@login_required
def install():
    if detect()["pkg"] != "apt":
        raise DnsError("La instalación automática solo está disponible en Debian/Ubuntu. "
                       "Instala BIND 9 con el gestor de paquetes de tu distribución.")
    ok, out, msg = apt_get("install", "--", *PACKAGES, timeout=900)
    if not ok:
        raise DnsError(msg or "apt-get falló", 500, out[-6000:])
    return jsonify({"ok": True, "output": out[-6000:]})


@dns_bp.route("/api/service", methods=["POST"])
@login_required
def service():
    _need()
    act = _data().get("action", "")
    if act not in ("start", "stop", "restart", "reload", "test"):
        raise DnsError("Acción no permitida")
    out = ""
    if act != "stop":
        ok, out = _checkconf()
        if not ok:
            raise DnsError("La configuración tiene errores; no se aplicó", 409, out)
        if act == "test":
            return jsonify({"ok": True, "output": out})
    code, o, e = run(["systemctl", act, UNIT], timeout=90)
    if code != 0:
        raise DnsError(_out(code, o, e) or "systemctl falló", 500, out)
    return jsonify({"ok": True, "output": out})


@dns_bp.route("/api/log")
@login_required
def log():
    _need()
    code, out, err = run(["journalctl", "-u", UNIT, "-n", "200", "--no-pager", "-o", "short-iso"], timeout=15)
    return jsonify({"ok": code == 0, "log": out if code == 0 else err})


@dns_bp.route("/api/zones")
@login_required
def zones():
    _need()
    try:
        _, zs = _local_zones()
    except OSError as e:
        raise DnsError(f"No se pudo leer {CONF_LOCAL}: {e}", 500)
    res = []
    for z in zs:
        item = {k: z[k] for k in ("name", "type", "file", "path", "primaries", "allow_transfer",
                                  "also_notify", "dynamic")}
        item.update(records=None, serial="", exists=bool(z["path"]) and os.path.exists(z["path"]),
                    editable=False, error="")
        if z["type"] == "primary" and item["exists"]:
            try:
                zp = _load_zone(z)
                soa = soa_values(zp)
                item["records"] = len(zp["records"]) + len(zp["fixed"]) + (1 if zp["soa"] else 0)
                item["serial"] = soa["serial"] if soa else ""
                item["editable"] = _path_ok(z["path"]) and not zp["readonly"]
                item["error"] = zp["readonly"]
            except (OSError, UnicodeError) as e:
                item["error"] = str(e)
        res.append(item)
    return jsonify({"zones": res})


@dns_bp.route("/api/zones/create", methods=["POST"])
@login_required
def create_zone():
    _need()
    d = _data()
    domain = valid_domain(d.get("domain"))
    if domain.endswith(".arpa"):
        raise DnsError("Para zonas inversas usa «+ Inversa»")
    origin = fqdn(domain)
    ns = target_abs(d.get("ns") or f"ns1.{domain}", origin)
    rname = email_to_rname(d.get("email"), domain)
    ns_ip = str(d.get("ns_ip") or "").strip()
    web_ip = str(d.get("web_ip") or "").strip()
    for ip, label in ((ns_ip, "IP del servidor de nombres"), (web_ip, "IP del sitio web")):
        if ip and not _is_ip(ip):
            raise DnsError(f"{label}: IP inválida")
    ns_inside = ns == origin or ns.endswith("." + origin)
    if ns_inside and not ns_ip:
        raise DnsError(f"{ns[:-1]} está dentro de la zona: indica su IP para crear el registro de dirección")
    recs = [(origin, "NS", [ns])]
    if ns_inside:
        recs.append((ns, *_addr_rr(ns_ip)))
    if web_ip:
        for owner in (origin, "www." + origin):
            rr = (owner, *_addr_rr(web_ip))
            if rr not in recs:
                recs.append(rr)
    with _LOCK:
        res = _create_primary_file(domain, new_zone_text(domain, ns, rname, recs))
    return jsonify(res)


def reverse_zone_name(network):
    try:
        net = ipaddress.ip_network(str(network or "").strip(), strict=False)
    except ValueError:
        raise DnsError("Red inválida (ej. 192.168.1.0/24)")
    if net.version != 4 or net.prefixlen not in (8, 16, 24):
        raise DnsError("Solo redes IPv4 /8, /16 o /24")
    octets = str(net.network_address).split(".")[:net.prefixlen // 8]
    return net, ".".join(reversed(octets)) + ".in-addr.arpa"


@dns_bp.route("/api/zones/reverse", methods=["POST"])
@login_required
def create_reverse():
    _need()
    d = _data()
    net, name = reverse_zone_name(d.get("network"))
    origin = fqdn(name)
    if not str(d.get("ns") or "").strip():
        raise DnsError("Indica el servidor de nombres (ej. ns1.ejemplo.com)")
    ns = target_abs(d.get("ns"), origin)
    if "." not in ns[:-1]:
        raise DnsError("El servidor de nombres debe ser un nombre completo (ej. ns1.ejemplo.com)")
    rname = email_to_rname(d.get("email"), ns[:-1].split(".", 1)[-1])
    recs, seen = [(origin, "NS", [ns])], set()
    with _LOCK:
        if d.get("auto_ptr"):
            _, zones_ = _local_zones()
            for z in zones_:
                if z["type"] != "primary" or z["name"].endswith(".arpa") or not _path_ok(z["path"]):
                    continue
                try:
                    zp = parse_zone(_read(z["path"]), z["name"])
                except (OSError, UnicodeError):
                    continue
                for r in zp["records"] + zp["fixed"]:
                    if r["type"] != "A" or r["owner"].startswith("*") or len(r["rdata"]) != 1:
                        continue
                    try:
                        ip = ipaddress.IPv4Address(r["rdata"][0])
                    except ValueError:
                        continue
                    if ip in net and ip not in seen:
                        seen.add(ip)
                        recs.append((ip.reverse_pointer + ".", "PTR", [r["owner"]]))
        res = _create_primary_file(name, new_zone_text(name, ns, rname, recs))
    res["ptr"] = len(seen)
    return jsonify(res)


@dns_bp.route("/api/zones/secondary", methods=["POST"])
@login_required
def create_secondary():
    _need()
    d = _data()
    domain = str(d.get("domain") or "").strip().lower().rstrip(".")
    if not (DOMAIN_RE.match(domain)):
        raise DnsError("Dominio inválido")
    primaries = _ip_list(d.get("primaries"), "Primarios")
    if not primaries:
        raise DnsError("Indica al menos la IP de un servidor primario")
    with _LOCK:
        conf, zs = _local_zones()
        if any(z["name"] == domain for z in zs):
            raise DnsError(f"La zona {domain} ya existe", 409)
        block = zone_conf(domain, "secondary", os.path.join(CACHE_DIR, "db." + domain),
                          [f"primaries {_addr_list(primaries)};"])
        res = _commit_conf(CONF_LOCAL, conf, append_block(conf, block))
    return jsonify(res)


@dns_bp.route("/api/zones/delete", methods=["POST"])
@login_required
def delete_zone():
    _need()
    d = _data()
    with _LOCK:
        conf, z = _get_zone(d.get("zone"), primary=False)
        st = zone_block(conf, z["name"])
        res = _commit_conf(CONF_LOCAL, conf, remove_span(conf, st["start"], st["end"]))
        removed = []
        if d.get("delete_file") and z["path"] and _path_ok(z["path"]):
            for p in (z["path"], z["path"] + ".jnl"):
                if os.path.isfile(p):
                    os.unlink(p)
                    removed.append(p)
    res["removed"] = removed
    return jsonify(res)


@dns_bp.route("/api/zones/transfer", methods=["POST"])
@login_required
def zone_transfer():
    """allow-transfer + also-notify de una zona primaria (para servidores secundarios)."""
    _need()
    d = _data()
    ips = _ip_list(d.get("ips"), "Secundarios")
    with _LOCK:
        conf, z = _get_zone(d.get("zone"), primary=False)
        if z["type"] != "primary":
            raise DnsError("Solo aplica a zonas primarias")
        new = set_stmt(conf, zone_block(conf, z["name"])["blocks"][0], "allow-transfer",
                       f"allow-transfer {_addr_list(ips) if ips else '{ none; }'};")
        new = set_stmt(new, zone_block(new, z["name"])["blocks"][0], "also-notify",
                       f"also-notify {_addr_list(ips)};" if ips else None)
        res = _commit_conf(CONF_LOCAL, conf, new)
    return jsonify(res)


@dns_bp.route("/api/zonestatus")
@login_required
def zonestatus():
    _need()
    _, z = _get_zone(request.args.get("zone"), primary=False)
    code, out, err = run(["rndc", "zonestatus", z["name"]], timeout=20)
    return jsonify({"ok": code == 0, "output": _out(code, out, err)})


@dns_bp.route("/api/records")
@login_required
def records():
    _need()
    _, z = _get_zone(request.args.get("zone"))
    zp = _load_zone(z)
    origin = zp["origin"]
    out = []
    for idx, r in enumerate(zp["fixed"] + zp["records"]):
        fixed = r.get("fixed", False)
        fields = record_fields(r)
        value = fields["text"] if r["type"] == "TXT" and fields else " ".join(r["rdata"])
        out.append({"idx": idx - len(zp["fixed"]), "key": record_key(r), "name": rel_name(r["owner"], origin),
                    "fqdn": r["owner"], "ttl": r["ttl"] or "", "type": r["type"], "value": value,
                    "fields": fields, "comment": r["comment"] or "",
                    "editable": bool(fields) and not fixed and not zp["readonly"],
                    "deletable": not fixed and not zp["readonly"]})
    return jsonify({"zone": z["name"], "path": z["path"], "soa": soa_values(zp), "ttl": zp["header_ttl"] or "",
                    "readonly": zp["readonly"], "records": out})


def _zone_for_write(name):
    _, z = _get_zone(name)
    old = _read(z["path"])
    zp = parse_zone(old, z["name"])
    if z["dynamic"]:
        raise DnsError("Zona dinámica (allow-update / update-policy): edítala con nsupdate.")
    if zp["readonly"]:
        raise DnsError(zp["readonly"])
    return z, old, zp


def _idx(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise DnsError("Registro inválido")


@dns_bp.route("/api/records/save", methods=["POST"])
@login_required
def record_save():
    _need()
    d = _data()
    with _LOCK:
        z, old, zp = _zone_for_write(d.get("zone"))
        rec = build_record(d.get("record") or {}, zp["origin"])
        idx = None if d.get("idx") in (None, "") else _idx(d.get("idx"))
        apply_record(zp, rec, idx, str(d.get("key", "")))
        soa_set, serial = bump_serial(zp)
        res = _commit_zone(z, old, render_zone(zp, soa_set))
    res["serial"] = serial
    return jsonify(res)


@dns_bp.route("/api/records/delete", methods=["POST"])
@login_required
def record_delete():
    _need()
    d = _data()
    with _LOCK:
        z, old, zp = _zone_for_write(d.get("zone"))
        delete_record(zp, _idx(d.get("idx")), str(d.get("key", "")))
        soa_set, serial = bump_serial(zp)
        res = _commit_zone(z, old, render_zone(zp, soa_set))
    res["serial"] = serial
    return jsonify(res)


@dns_bp.route("/api/soa", methods=["POST"])
@login_required
def soa_save():
    _need()
    d = _data()
    with _LOCK:
        z, old, zp = _zone_for_write(d.get("zone"))
        origin = zp["origin"]
        soa_set = {0: target_abs(d.get("mname"), origin), 1: email_to_rname(d.get("email"), z["name"])}
        for i, (key, label) in enumerate((("refresh", "Refresh"), ("retry", "Retry"),
                                          ("expire", "Expire"), ("minimum", "Minimum")), start=3):
            soa_set[i] = _int(d.get(key), 1, 2147483647, label)
        soa_set, serial = bump_serial(zp, soa_set)
        res = _commit_zone(z, old, render_zone(zp, soa_set))
    res["serial"] = serial
    return jsonify(res)


def file_forwarders(text):
    blk = options_block(text)["blocks"][0]
    fw = _find(blk["stmts"], "forwarders")
    mode = _find(blk["stmts"], "forward")
    ips = [i.split()[0] for i in _items(fw["blocks"][0])] if fw and fw["blocks"] else []
    return ips, (mode["args"][1] if mode and len(mode["args"]) > 1 else "")


def set_forwarders(text, ips, mode):
    text = set_stmt(text, options_block(text)["blocks"][0], "forwarders",
                    f"forwarders {_addr_list(ips)};" if ips else None)
    return set_stmt(text, options_block(text)["blocks"][0], "forward",
                    f"forward {mode};" if ips and mode else None)


@dns_bp.route("/api/options")
@login_required
def options():
    _need()
    code, out, err = run(["named-checkconf", "-p"], timeout=20)
    if code != 0:
        raise DnsError("named-checkconf encontró errores en la configuración", 409, _out(code, out, err))
    try:
        st = options_block(out)
    except DnsError:
        st = None
    values = {}
    for s in st["blocks"][0]["stmts"] if st else []:
        if s["args"]:
            values[s["args"][0]] = {"value": " ".join(s["args"][1:]),
                                    "list": _items(s["blocks"][0]) if s["blocks"] else None}
    try:
        fw, mode = file_forwarders(_read(CONF_OPTIONS))
        fw_error = ""
    except (OSError, DnsError) as e:
        fw, mode, fw_error = [], "", getattr(e, "msg", str(e))
    return jsonify({"values": values, "raw": out[st["start"]:st["end"]] if st else "",
                    "forwarders": fw, "forward": mode, "forwarders_error": fw_error})


@dns_bp.route("/api/options/forwarders", methods=["POST"])
@login_required
def save_forwarders():
    _need()
    d = _data()
    ips = _ip_list(d.get("forwarders"), "Reenviadores", max_n=8)
    mode = str(d.get("forward") or "")
    if mode not in ("", "first", "only"):
        raise DnsError("Modo de reenvío inválido")
    with _LOCK:
        old = _read(CONF_OPTIONS)
        res = _commit_conf(CONF_OPTIONS, old, set_forwarders(old, ips, mode))
    return jsonify(res)


@dns_bp.route("/api/dig", methods=["POST"])
@login_required
def dig():
    d = _data()
    name = str(d.get("name") or "").strip()
    qtype = str(d.get("type") or "A").upper()
    if qtype not in DIG_TYPES:
        raise DnsError("Tipo de consulta no permitido")
    try:
        server = str(ipaddress.ip_address(str(d.get("server") or "127.0.0.1").strip()))
    except ValueError:
        raise DnsError("Servidor: indica una dirección IP")
    cmd = ["dig", "+time=3", "+tries=1", "@" + server]
    if qtype == "PTR" and _is_ip(name):
        cmd += ["-x", str(ipaddress.ip_address(name))]
    elif HOST_RE.match(name) or name == ".":
        cmd += [name, qtype]
    else:
        raise DnsError("Nombre inválido")
    if d.get("short"):
        cmd.append("+short")
    code, out, err = run(cmd, timeout=20)
    if code == 127:
        raise DnsError("dig no está instalado (paquete bind9-dnsutils)", 500)
    text = _out(code, out, err)
    if not text and d.get("short"):
        text = "(sin respuesta: el nombre no existe o no tiene registros de ese tipo)"
    return jsonify({"ok": True, "cmd": " ".join(cmd), "output": text, "code": code})


if __name__ == "__main__":  # autocomprobación del parser (ejecutar con python -m app.modules.dns)
    sample = """; zona de prueba
$TTL 3600
$ORIGIN ejemplo.com.
@   IN SOA ns1 hostmaster.ejemplo.com. (
        2026102501 ; serial
        3600 900 1209600 300 )
    IN  NS  ns1
ns1     IN A 192.0.2.1
; --- Correo ---
@ 300 IN MX 10 mail
mail  IN A 192.0.2.2 ; servidor de correo
www IN CNAME @
@ IN TXT "v=spf1 mx -all"
_sip._tcp IN SRV 10 5 5060 sip.ejemplo.com.
@ IN CAA 0 issue "letsencrypt.org"
$TTL 600
$ORIGIN sub.ejemplo.com.
host IN A 192.0.2.3
"""
    z = parse_zone(sample, "ejemplo.com")
    assert not z["readonly"], z["readonly"]
    assert soa_values(z)["serial"] == "2026102501" and soa_values(z)["email"] == "hostmaster@ejemplo.com"
    assert soa_values(z)["mname"] == "ns1.ejemplo.com."
    r = {x["owner"] + x["type"]: x for x in z["records"]}
    assert r["ejemplo.com.NS"]["rdata"] == ["ns1.ejemplo.com."]
    assert r["ejemplo.com.MX"]["rdata"] == ["10", "mail.ejemplo.com."] and r["ejemplo.com.MX"]["ttl"] == "300"
    assert r["www.ejemplo.com.CNAME"]["rdata"] == ["ejemplo.com."]
    assert r["mail.ejemplo.com.A"]["comment"] == "servidor de correo"
    assert r["host.sub.ejemplo.com.A"]["ttl"] == "600"
    assert record_fields(r["ejemplo.com.TXT"]) == {"text": "v=spf1 mx -all"}
    assert record_fields(r["ejemplo.com.CAA"]) == {"flags": "0", "tag": "issue", "value": "letsencrypt.org"}
    soa_set, serial = bump_serial(z)
    out = render_zone(z, soa_set)
    assert f"{serial} ; serial" in out and out.startswith("; zona de prueba\n$TTL 3600")
    assert "; --- Correo ---" in out and "$ORIGIN sub.ejemplo.com." in out
    z2 = parse_zone(out, "ejemplo.com")  # ida y vuelta estable
    assert [(x["owner"], x["type"], x["rdata"], x["ttl"]) for x in z2["records"]] == \
           [(x["owner"], x["type"], x["rdata"], x["ttl"]) for x in z["records"]]
    assert render_zone(z2) == out
    # alta, edición y borrado
    o = z2["origin"]
    apply_record(z2, build_record({"type": "A", "name": "ftp", "address": "192.0.2.9"}, o))
    apply_record(z2, build_record({"type": "TXT", "name": "_dmarc", "text": "x" * 300}, o))
    try:
        apply_record(z2, build_record({"type": "A", "name": "www", "address": "192.0.2.9"}, o))
        raise AssertionError("CNAME + A permitido")
    except DnsError:
        pass
    txt = next(x for x in z2["records"] if x["owner"] == "_dmarc.ejemplo.com.")
    assert len(txt["rdata"]) == 2 and record_fields(txt)["text"] == "x" * 300
    i = next(i for i, x in enumerate(z2["records"]) if x["owner"] == "ftp.ejemplo.com.")
    apply_record(z2, build_record({"type": "A", "name": "ftp.ejemplo.com", "address": "192.0.2.10"}, o),
                 i, record_key(z2["records"][i]))
    assert z2["records"][i]["rdata"] == ["192.0.2.10"]
    j = next(i for i, x in enumerate(z2["records"]) if x["type"] == "MX")
    delete_record(z2, j, record_key(z2["records"][j]))
    out3 = render_zone(z2)
    assert "; --- Correo ---" in out3 and " MX " not in out3
    z3 = parse_zone(out3, "ejemplo.com")
    assert len(z3["records"]) == len(z2["records"]) and not z3["readonly"]
    # validaciones
    for bad in ({"type": "A", "name": "x", "address": "300.1.1.1"},
                {"type": "MX", "name": "@", "priority": "10", "target": "192.0.2.1"},
                {"type": "CNAME", "name": "@", "target": "x.com"},
                {"type": "A", "name": "otro.com.", "address": "1.1.1.1"},
                {"type": "SRV", "name": "sip", "priority": 1, "weight": 1, "port": 1, "target": "x.com"},
                {"type": "TXT", "name": "a", "text": "a\nb"},
                {"type": "A", "name": "a b", "address": "1.1.1.1"},
                {"type": "CAA", "name": "@", "flags": 0, "tag": "issue", "value": 'x"y'}):
        try:
            build_record(bad, o)
            raise AssertionError(f"aceptó {bad}")
        except DnsError:
            pass
    assert target_abs("mail", o) == "mail.ejemplo.com." and target_abs("mx.otro.net", o) == "mx.otro.net."
    assert owner_abs("10", "1.168.192.in-addr.arpa.", "PTR") == "10.1.168.192.in-addr.arpa."
    assert owner_abs("192.168.1.10", "1.168.192.in-addr.arpa.", "PTR") == "10.1.168.192.in-addr.arpa."
    assert txt_encode('a"b\\ñ') == ['"a\\"b\\\\\\195\\177"'] and txt_decode(txt_encode('a"b\\ñ')) == 'a"b\\ñ'
    assert next_serial(2026102501, datetime.date(2026, 10, 25)) == 2026102502
    assert next_serial(2, datetime.date(2026, 10, 25)) == 2026102500
    assert next_serial(2026102699, datetime.date(2026, 10, 25)) == 2026102700
    assert email_to_rname("juan.perez@ejemplo.com", "x") == "juan\\.perez.ejemplo.com."
    assert rname_to_email("juan\\.perez.ejemplo.com.") == "juan.perez@ejemplo.com"
    assert reverse_zone_name("192.168.1.0/24")[1] == "1.168.192.in-addr.arpa"
    nz = new_zone_text("nuevo.com", "ns1.nuevo.com.", "hostmaster.nuevo.com.",
                       [("nuevo.com.", "NS", ["ns1.nuevo.com."]), ("ns1.nuevo.com.", "A", ["192.0.2.1"])],
                       datetime.date(2026, 10, 26))
    pz = parse_zone(nz, "nuevo.com")
    assert soa_values(pz)["serial"] == "2026102601" and soa_values(pz)["minimum"] == "300"
    assert [x["owner"] for x in pz["records"]] == ["nuevo.com.", "ns1.nuevo.com."]
    # named.conf.local
    conf = """// local
zone "ejemplo.com" {
\ttype master;
\tfile "/etc/bind/zones/db.ejemplo.com";
\tallow-transfer { 192.0.2.50; };
};
zone "otro.org" IN { type slave; file "db.otro.org"; masters { 192.0.2.7; 192.0.2.8 port 53; }; };
/* zone "viejo.com" { type master; file "x"; }; */
"""
    zs = parse_zones(conf)
    assert [(x["name"], x["type"]) for x in zs] == [("ejemplo.com", "primary"), ("otro.org", "secondary")]
    assert zs[0]["allow_transfer"] == ["192.0.2.50"] and zs[1]["primaries"] == ["192.0.2.7", "192.0.2.8 port 53"]
    st = zone_block(conf, "ejemplo.com")
    conf2 = remove_span(conf, st["start"], st["end"])
    assert "ejemplo.com" not in conf2 and conf2.startswith("// local\nzone \"otro.org\"")
    conf3 = append_block(conf2, zone_conf("n.com", "secondary", "/var/cache/bind/db.n.com",
                                          [f"primaries {_addr_list(['192.0.2.1'])};"]))
    assert [x["name"] for x in parse_zones(conf3)] == ["otro.org", "n.com"]
    b = zone_block(conf, "ejemplo.com")["blocks"][0]
    conf4 = set_stmt(conf, b, "also-notify", "also-notify { 192.0.2.50; };")
    assert parse_zones(conf4)[0]["also_notify"] == ["192.0.2.50"]
    opts = """options {
\tdirectory "/var/cache/bind";
\t// forwarders {
\t//      0.0.0.0;
\t// };
\tdnssec-validation auto;
\tlisten-on-v6 { any; };
};
"""
    o2 = set_forwarders(opts, ["1.1.1.1", "8.8.8.8"], "first")
    assert file_forwarders(o2) == (["1.1.1.1", "8.8.8.8"], "first")
    o3 = set_forwarders(o2, ["9.9.9.9"], "")
    assert file_forwarders(o3) == (["9.9.9.9"], "") and "forward " not in o3
    assert set_forwarders(o3, [], "") == opts
    print("ok")
