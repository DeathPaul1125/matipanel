"""Correo: Postfix (MTA) y Dovecot (IMAP/POP3) — estado, main.cf, cola, alias, Dovecot, registro y prueba."""
import ipaddress
import json
import os
import posixpath
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
from email.message import EmailMessage
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..distro import detect
from ..modules.packages import apt_get
from ..util import run

mail_bp = Blueprint("mail", __name__)

MAIN_CF = "/etc/postfix/main.cf"
ALIASES = "/etc/aliases"
DOVECOT_DIR = "/etc/dovecot"
DOVECOT_SSL_CONF = "/etc/dovecot/conf.d/10-ssl.conf"
MAIL_LOG = "/var/log/mail.log"
BAK = ".matipanel.bak"
MAIL_PORTS = (25, 465, 587, 110, 995, 143, 993)
LOG_LINES = 300
POSTCAT_MAX = 200 * 1024
QUEUE_MAX = 2000
QUEUE_BYTES = 32 * 1024 * 1024

POSTFIX_PKGS = ("postfix",)
DOVECOT_PKGS = ("dovecot-imapd", "dovecot-pop3d")

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
HOST_RE = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")
# Elemento de mydestination: host, $variable o host.$variable (ej. localhost.$mydomain)
DEST_TOKEN_RE = re.compile(rf"^(?:{_LABEL}\.)*(?:{_LABEL}|\$[a-z_]+)$")
RELAY_RE = re.compile(rf"^(?:\[({_LABEL}(?:\.{_LABEL})*)\]|({_LABEL}(?:\.{_LABEL})*))(?::(\d{{1,5}}))?$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+-]{1,250}$")
MAILBOX_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}(?:/[A-Za-z0-9._-]{1,64}){0,4}/?$")
QID_SHORT_RE = re.compile(r"^[0-9A-F]{6,20}$")
QID_LONG_RE = re.compile(r"^[0-9A-Za-z]{10,20}$")
ALIAS_RE = re.compile(r"^[a-z0-9_][a-z0-9._+-]{0,63}$")
LOCAL_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]{0,63}$")
EMAIL_RE = re.compile(rf"^[A-Za-z0-9_][A-Za-z0-9._%+-]{{0,63}}@{_LABEL}(?:\.{_LABEL})*$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Parámetros de main.cf editables: (clave, etiqueta, ayuda, marcador)
POSTFIX_PARAMS = [
    ("myhostname", "Nombre del servidor (myhostname)",
     "Nombre completo (FQDN) con el que el servidor se presenta, ej. mail.ejemplo.gob.gt.", "mail.ejemplo.gob.gt"),
    ("mydomain", "Dominio (mydomain)", "Dominio de correo principal, ej. ejemplo.gob.gt.", "ejemplo.gob.gt"),
    ("myorigin", "Origen (myorigin)",
     "Dominio que se agrega a los correos enviados por usuarios locales: $myhostname, $mydomain, /etc/mailname o un dominio.",
     "/etc/mailname"),
    ("mydestination", "Destinos locales (mydestination)",
     "Dominios para los que este servidor entrega el correo localmente, separados por coma.",
     "$myhostname, localhost.$mydomain, localhost"),
    ("relayhost", "Relay de salida (relayhost)",
     "Vacío = entrega directa por DNS (MX). O un relay: [smtp.ejemplo.com]:587.", "[smtp.ejemplo.com]:587"),
    ("inet_interfaces", "Interfaces de escucha (inet_interfaces)",
     "all, loopback-only, localhost o lista de IP. Cambiarlo reinicia Postfix.", "all"),
    ("inet_protocols", "Protocolos IP (inet_protocols)", "all, ipv4 o ipv6. Cambiarlo reinicia Postfix.", ""),
    ("message_size_limit", "Tamaño máximo de mensaje (bytes)",
     "Límite por mensaje, incluidos adjuntos. 10240000 ≈ 10 MB; 0 = sin límite.", "10240000"),
    ("mailbox_size_limit", "Tamaño máximo del buzón (bytes)",
     "Solo para buzones mbox. 0 = sin límite. Debe ser mayor o igual al tamaño de mensaje.", "0"),
    ("home_mailbox", "Buzón en el home (home_mailbox)",
     "Vacío = /var/mail/usuario (mbox). Maildir/ = formato Maildir en el home (recomendado con Dovecot).", "Maildir/"),
    ("smtpd_tls_cert_file", "Certificado TLS (smtpd_tls_cert_file)",
     "Ruta del certificado, ej. /etc/letsencrypt/live/mail.ejemplo.gob.gt/fullchain.pem.", "/etc/ssl/certs/ssl-cert-snakeoil.pem"),
    ("smtpd_tls_key_file", "Llave privada TLS (smtpd_tls_key_file)",
     "Ruta de la llave privada, ej. /etc/letsencrypt/live/mail.ejemplo.gob.gt/privkey.pem.", "/etc/ssl/private/ssl-cert-snakeoil.key"),
]
PARAM_KEYS = [p[0] for p in POSTFIX_PARAMS]
INET_PROTOCOLS = ("all", "ipv4", "ipv6", "ipv4, ipv6")
RESTART_PARAMS = {"inet_interfaces", "inet_protocols"}

SERVICES = {
    "postfix": {"unit": "postfix", "check": ["postfix", "check"], "label": "Postfix"},
    "dovecot": {"unit": "dovecot", "check": ["doveconf", "-n"], "label": "Dovecot"},
}


# ── Utilidades puras (probadas en __main__) ──────────────────────────

def _split_list(value):
    return [t for t in re.split(r"[,\s]+", str(value).strip()) if t]


def validate_param(key, value):
    """Valida y normaliza un parámetro de main.cf. Retorna (valor, error)."""
    v = str(value if value is not None else "").strip()
    if CTRL_RE.search(v) or len(v) > 500:
        return None, "contiene caracteres no permitidos"
    if key in ("myhostname", "mydomain"):
        return (v.lower(), None) if HOST_RE.fullmatch(v) else (None, "nombre de host inválido")
    if key == "myorigin":
        if v in ("$myhostname", "$mydomain", "/etc/mailname") or HOST_RE.fullmatch(v):
            return v.lower() if HOST_RE.fullmatch(v) else v, None
        return None, "usa $myhostname, $mydomain, /etc/mailname o un dominio"
    if key == "mydestination":
        toks = _split_list(v)
        bad = [t for t in toks if not DEST_TOKEN_RE.fullmatch(t)]
        return (None, f"elemento inválido: {bad[0]}") if bad else (", ".join(toks), None)
    if key == "relayhost":
        if not v:
            return "", None
        m = RELAY_RE.fullmatch(v)
        if not m or (m.group(3) and not 1 <= int(m.group(3)) <= 65535):
            return None, "usa host, [host] o [host]:puerto"
        return v.lower(), None
    if key == "inet_interfaces":
        toks = _split_list(v)
        if len(toks) == 1 and toks[0] in ("all", "loopback-only", "localhost"):
            return toks[0], None
        if not toks:
            return None, "no puede quedar vacío"
        for t in toks:
            try:
                ipaddress.ip_address(t)
            except ValueError:
                if not HOST_RE.fullmatch(t):
                    return None, f"interfaz inválida: {t}"
        return ", ".join(toks), None
    if key == "inet_protocols":
        toks = _split_list(v)
        if toks == ["all"]:
            return "all", None
        if toks and len(set(toks)) == len(toks) and set(toks) <= {"ipv4", "ipv6"}:
            return ", ".join(sorted(toks)), None
        return None, "usa all, ipv4, ipv6 o ipv4, ipv6"
    if key in ("message_size_limit", "mailbox_size_limit"):
        if not re.fullmatch(r"\d{1,12}", v) or int(v) > 2**40:
            return None, "debe ser un número entero de bytes"
        return str(int(v)), None
    if key == "home_mailbox":
        if not v:
            return "", None
        if MAILBOX_RE.fullmatch(v) and ".." not in v.split("/"):
            return v, None
        return None, "ruta relativa al home, ej. Maildir/"
    if key in ("smtpd_tls_cert_file", "smtpd_tls_key_file"):
        if not v:
            return "", None
        if not PATH_RE.fullmatch(v) or ".." in v.split("/") or posixpath.normpath(v) != v:
            return None, "ruta absoluta inválida"
        return v, None
    return None, "parámetro no permitido"


def validate_params(data):
    """Valida un dict de parámetros. Retorna (normalizados, errores)."""
    out, errors = {}, {}
    for key, value in (data or {}).items():
        if key not in PARAM_KEYS:
            errors[key] = "parámetro no permitido"
            continue
        val, err = validate_param(key, value)
        if err:
            errors[key] = err
        else:
            out[key] = val
    msl, mbl = out.get("message_size_limit"), out.get("mailbox_size_limit")
    if msl is not None and mbl is not None and int(mbl) and int(msl) > int(mbl):
        errors["mailbox_size_limit"] = "debe ser 0 o mayor o igual al tamaño máximo de mensaje"
    return out, errors


def parse_postconf(out):
    """`postconf k1 k2` → {k: v}."""
    res = {}
    for line in out.splitlines():
        k, sep, v = line.partition(" = ")
        if sep:
            res[k.strip()] = v.strip()
        elif line.strip().endswith(" ="):
            res[line.strip()[:-2].strip()] = ""
    return res


def valid_queue_id(qid):
    qid = str(qid or "")
    return bool(QID_SHORT_RE.fullmatch(qid) or QID_LONG_RE.fullmatch(qid))


def parse_queue(out):
    """`postqueue -j` (un objeto JSON por línea) → lista de mensajes."""
    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        qid = str(o.get("queue_id", ""))
        if not valid_queue_id(qid):
            continue
        rcpts = o.get("recipients") or []
        reasons = [r.get("delay_reason", "") for r in rcpts if isinstance(r, dict) and r.get("delay_reason")]
        items.append({
            "id": qid,
            "queue": str(o.get("queue_name", "")),
            "sender": str(o.get("sender", "")) or "<>",
            "recipients": [str(r.get("address", "")) for r in rcpts if isinstance(r, dict)],
            "size": int(o.get("message_size") or 0),
            "arrival": int(o.get("arrival_time") or 0),
            "reason": "; ".join(dict.fromkeys(reasons)),
        })
    items.sort(key=lambda m: m["arrival"], reverse=True)
    return items


def _alias_entries(lines):
    entries, cur = [], None
    for idx, line in enumerate(lines):
        s = line.strip()
        if cur and line[:1] in (" ", "\t") and s and not s.startswith("#"):
            cur["dest"] += " " + s
            cur["end"] = idx + 1
            continue
        cur = None
        if not s or s.startswith("#") or line[:1] in (" ", "\t"):
            continue
        m = re.match(r"^([^:\s#]+)\s*:(.*)$", line)
        if m:
            cur = {"name": m.group(1), "dest": m.group(2).strip(), "start": idx, "end": idx + 1}
            entries.append(cur)
    return entries


def parse_aliases(text):
    """/etc/aliases → [{name, dest:[...], line}] (comentarios ignorados)."""
    return [{"name": e["name"],
             "dest": [d.strip() for d in e["dest"].split(",") if d.strip()],
             "line": e["start"] + 1} for e in _alias_entries(text.split("\n"))]


def validate_alias_dest(dest):
    """Destinos separados por coma: usuario, correo, /ruta/archivo o :include:/ruta. Sin tuberías."""
    toks = [t.strip() for t in str(dest or "").replace("\n", ",").split(",") if t.strip()]
    if not toks or len(toks) > 100:
        return None
    for t in toks:
        path = t[len(":include:"):] if t.startswith(":include:") else t
        if path.startswith("/"):
            if not PATH_RE.fullmatch(path) or ".." in path.split("/"):
                return None
        elif t.startswith(":include:") or not (LOCAL_RE.fullmatch(t) or EMAIL_RE.fullmatch(t)):
            return None
    return ", ".join(toks)


def aliases_set(text, orig, name, dest):
    """Agrega (orig vacío) o reemplaza la entrada `orig`. Retorna (texto, error)."""
    lines = text.split("\n")
    entries = _alias_entries(lines)
    target = next((e for e in entries if orig and e["name"].lower() == orig.lower()), None)
    if orig and not target:
        return None, "El alias ya no existe; refresca la lista"
    if any(e["name"].lower() == name.lower() and e is not target for e in entries):
        return None, f"Ya existe el alias {name}"
    new = f"{name}: {dest}"
    if target:
        lines[target["start"]:target["end"]] = [new]
        return "\n".join(lines), None
    if text and not text.endswith("\n"):
        text += "\n"
    return text + new + "\n", None


def aliases_delete(text, name):
    lines = text.split("\n")
    target = next((e for e in _alias_entries(lines) if e["name"].lower() == name.lower()), None)
    if not target:
        return None, "El alias no existe"
    del lines[target["start"]:target["end"]]
    return "\n".join(lines), None


def parse_doveconf_top(out):
    """Ajustes de primer nivel (sin sangría, fuera de bloques) de `doveconf -n`."""
    res, depth = {}, 0
    for line in out.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if depth == 0 and not s.endswith("{"):
            k, sep, v = s.partition(" = ")
            if sep:
                res[k.strip()] = v.strip()
            elif s.endswith(" ="):
                res[s[:-2].strip()] = ""
        depth += s.count("{") - s.count("}")
        depth = max(depth, 0)
    return res


def set_dovecot_ssl(text, value):
    """Reemplaza la línea `ssl = ...` de primer nivel, o la agrega al final."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^ssl\s*=", line):
            lines[i] = f"ssl = {value}"
            return "\n".join(lines)
    return text.rstrip("\n") + f"\nssl = {value}\n"


def parse_ss_ports(out):
    """`ss -ltn` → set de puertos TCP en escucha."""
    ports = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "LISTEN":
            port = parts[3].rsplit(":", 1)[-1]
            if port.isdigit():
                ports.add(int(port))
    return ports


def build_test_message(to, subject, body):
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg["X-Mailer"] = "MatiPanel"
    msg.set_content(body, cte="quoted-printable")
    return msg.as_string()


# ── Helpers de sistema ───────────────────────────────────────────────

def _err(msg, code=400, **extra):
    return jsonify({"ok": False, "error": msg, **extra}), code


def _has(binary):
    return bool(shutil.which(binary) or os.path.exists("/usr/sbin/" + binary))


def _postfix_ok():
    return _has("postconf") and os.path.isfile(MAIN_CF)


def _dovecot_ok():
    return _has("doveconf") and os.path.isdir(DOVECOT_DIR)


def _run_raw(cmd, timeout=30, cap=None):
    """Como util.run pero decodifica bytes con reemplazo (correos y logs pueden traer bytes no UTF-8)
    y, con `cap`, deja de leer (y mata el proceso) al pasar de cap bytes: la memoria queda acotada."""
    with tempfile.TemporaryFile() as errf:
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf)
        except FileNotFoundError:
            return 127, "", f"{cmd[0]} no está instalado", False
        expired = []
        timer = threading.Timer(timeout, lambda: (expired.append(1), p.kill()))
        timer.start()
        try:
            data = p.stdout.read(cap + 1) if cap else p.stdout.read()
            cut = bool(cap) and len(data) > cap
            if cut:
                p.kill()
            p.stdout.close()
            code = p.wait()
        finally:
            timer.cancel()
        errf.seek(0)
        err = errf.read(64 * 1024).decode("utf-8", "replace")
    if expired and not cut:
        return 124, "", "Tiempo de espera agotado", False
    return code, data[:cap].decode("utf-8", "replace") if cap else data.decode("utf-8", "replace"), err, cut


def _unit_state(unit):
    active = run(["systemctl", "is-active", unit], timeout=10)[1].strip() or "unknown"
    enabled = run(["systemctl", "is-enabled", unit], timeout=10)[1].strip() or "unknown"
    return active, enabled


def _check(svc):
    code, out, err = run(SERVICES[svc]["check"], timeout=30)
    text = (out + err).strip()
    if svc == "dovecot" and code == 0:
        text = "doveconf -n: configuración válida"  # la salida completa se ve en la pestaña Dovecot
    return code == 0, text


def _backup(path):
    shutil.copy2(path, path + BAK)


def _restore(path):
    shutil.copy2(path + BAK, path)


def _atomic_write(path, text):
    """Temporal + rename en el mismo directorio, conservando dueño y permisos (nuevo: 0644).
    Un fallo a mitad nunca deja /etc/aliases o 10-ssl.conf truncados."""
    st = os.stat(path) if os.path.exists(path) else None
    fd, tmp = tempfile.mkstemp(prefix=".matipanel.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if st:
            os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, stat.S_IMODE(st.st_mode) if st else 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _fqdn():
    name = run(["hostname", "-f"], timeout=10)[1].strip().lower()
    if not HOST_RE.fullmatch(name):
        name = socket.getfqdn().lower()
    return name if HOST_RE.fullmatch(name) else "localhost.localdomain"


# ── Página y estado ──────────────────────────────────────────────────

@mail_bp.route("/")
@login_required
def index():
    return render_template("mail.html", params=POSTFIX_PARAMS, inet_protocols=INET_PROTOCOLS,
                           main_cf=MAIN_CF, aliases_path=ALIASES)


@mail_bp.route("/api/status")
@login_required
def status():
    res = {"apt": detect()["pkg"] == "apt", "hostname": _fqdn()}
    for svc, installed in (("postfix", _postfix_ok()), ("dovecot", _dovecot_ok())):
        info = {"installed": installed, "active": "", "enabled": "", "version": ""}
        if installed:
            info["active"], info["enabled"] = _unit_state(SERVICES[svc]["unit"])
            if svc == "postfix":
                info["version"] = run(["postconf", "-h", "mail_version"], timeout=10)[1].strip()
            else:
                info["version"] = run(["dovecot", "--version"], timeout=10)[1].strip()
        res[svc] = info
    code, out, _ = run(["ss", "-ltn"], timeout=10)
    listening = parse_ss_ports(out) if code == 0 else set()
    res["ports"] = [{"port": p, "listening": p in listening} for p in MAIL_PORTS]
    return jsonify(res)


@mail_bp.route("/api/install", methods=["POST"])
@login_required
def install():
    what = (request.get_json(silent=True) or {}).get("what")
    if what not in ("postfix", "dovecot"):
        return _err("Paquete inválido")
    pkgs = POSTFIX_PKGS if what == "postfix" else DOVECOT_PKGS
    if detect()["pkg"] != "apt":
        return _err("La instalación automática solo está disponible en Debian/Ubuntu. Instala a mano: "
                    + " ".join(pkgs))
    log = []
    if what == "postfix":
        fqdn = _fqdn()
        sel = (f"postfix postfix/main_mailer_type select Internet Site\n"
               f"postfix postfix/mailname string {fqdn}\n")
        code, out, err = run(["debconf-set-selections"], input=sel, timeout=30)
        if code != 0:
            return _err("debconf-set-selections falló: " + (err or out).strip(), 500)
        log.append(f"Preconfigurado: Internet Site, mailname = {fqdn}")
    ok, out, msg = apt_get("install", *pkgs, timeout=900)
    log.append(out[-6000:])
    if not ok:
        return _err(msg or "apt-get falló", 500, output="\n".join(log))
    return jsonify({"ok": True, "output": "\n".join(log)})


@mail_bp.route("/api/service", methods=["POST"])
@login_required
def service():
    data = request.get_json(silent=True) or {}
    svc, act = data.get("service"), data.get("action")
    if svc not in SERVICES or act not in ("check", "start", "stop", "restart", "reload"):
        return _err("Acción no permitida")
    if not (_postfix_ok() if svc == "postfix" else _dovecot_ok()):
        return _err(SERVICES[svc]["label"] + " no está instalado", 404)
    if act != "stop":
        ok, out = _check(svc)
        if not ok:
            return _err("La configuración tiene errores; no se aplicó", 409, output=out)
        if act == "check":
            return jsonify({"ok": True, "output": out or "Configuración válida (sin avisos)"})
    else:
        out = ""
    code, o, e = run(["systemctl", act, SERVICES[svc]["unit"]], timeout=60)
    if code != 0:
        return _err((e or o).strip() or "Fallo desconocido", 500, output=out)
    return jsonify({"ok": True, "output": out})


# ── Postfix: main.cf ─────────────────────────────────────────────────

def _read_params():
    code, out, err = run(["postconf", *PARAM_KEYS], timeout=15)
    if code != 0:
        return None, (err or out).strip() or "postconf falló"
    return parse_postconf(out), None


@mail_bp.route("/api/postfix/config")
@login_required
def postfix_config():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    values, error = _read_params()
    if error:
        return _err(error, 500)
    return jsonify({"values": values})


@mail_bp.route("/api/postfix/config", methods=["POST"])
@login_required
def postfix_config_save():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    values, errors = validate_params((request.get_json(silent=True) or {}).get("params"))
    if errors:
        return _err("Revisa los campos marcados", 400, fields=errors)
    for key in ("smtpd_tls_cert_file", "smtpd_tls_key_file"):
        if values.get(key) and not os.path.isfile(values[key]):
            return _err("Revisa los campos marcados", 400, fields={key: "el archivo no existe"})
    current, error = _read_params()
    if error:
        return _err(error, 500)
    changed = {k: v for k, v in values.items() if current.get(k) != v}
    if not changed:
        return jsonify({"ok": True, "changed": [], "output": "Sin cambios."})
    try:
        _backup(MAIN_CF)
    except OSError as e:
        return _err(f"No se pudo respaldar {MAIN_CF}: {e}", 500)
    code, o, e = run(["postconf", "-e", *[f"{k}={v}" for k, v in changed.items()]], timeout=30)
    if code != 0:
        _restore(MAIN_CF)
        return _err("postconf -e falló; se restauró main.cf", 500, output=(o + e).strip())
    ok, out = _check("postfix")
    if not ok:
        _restore(MAIN_CF)
        return _err("postfix check falló; se restauró main.cf y no se recargó", 409, output=out)
    log = [f"{k} = {v}" for k, v in changed.items()]
    if out:
        log.append(out)
    if run(["systemctl", "is-active", "--quiet", "postfix"], timeout=10)[0] != 0:
        log.append("(Postfix inactivo: no se recargó)")
    else:
        act = "restart" if RESTART_PARAMS & changed.keys() else "reload"
        code, o, e = run(["systemctl", act, "postfix"], timeout=60)
        if code != 0:
            return _err(f"Guardado, pero systemctl {act} postfix falló", 500,
                        output="\n".join(log + [(o + e).strip()]))
        log.append(f"systemctl {act} postfix: OK")
    return jsonify({"ok": True, "changed": list(changed), "output": "\n".join(log)})


# ── Postfix: cola ────────────────────────────────────────────────────

@mail_bp.route("/api/queue")
@login_required
def queue():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    # Una cola enorme (spam, rebotes) puede dar cientos de MB de JSON: se corta en QUEUE_BYTES
    code, out, err, cut = _run_raw(["postqueue", "-j"], timeout=30, cap=QUEUE_BYTES)
    if code != 0 and not cut:
        return _err((err or out).strip() or "postqueue falló", 500)
    if cut:
        out = out.rsplit("\n", 1)[0]  # la última línea puede estar incompleta
    items = parse_queue(out)
    return jsonify({"total": len(items), "items": items[:QUEUE_MAX], "truncated": cut})


@mail_bp.route("/api/queue/action", methods=["POST"])
@login_required
def queue_action():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    data = request.get_json(silent=True) or {}
    act, qid = data.get("action"), str(data.get("id") or "")
    if act == "flush":
        cmd = ["postqueue", "-f"]
    elif act == "delete_all":
        if data.get("confirm") != "ELIMINAR":
            return _err("Falta la confirmación")
        cmd = ["postsuper", "-d", "ALL"]
    elif act in ("delete", "hold", "release"):
        if not valid_queue_id(qid):
            return _err("ID de mensaje inválido")
        cmd = ["postsuper", {"delete": "-d", "hold": "-h", "release": "-H"}[act], qid]
    else:
        return _err("Acción no permitida")
    code, out, err = run(cmd, timeout=60)
    if code != 0:
        return _err((err or out).strip() or "Fallo desconocido", 500)
    return jsonify({"ok": True, "output": (out + err).strip()})


@mail_bp.route("/api/queue/message")
@login_required
def queue_message():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    qid = request.args.get("id", "")
    if not valid_queue_id(qid):
        return _err("ID de mensaje inválido")
    code, out, err, truncated = _run_raw(["postcat", "-q", qid], timeout=30, cap=POSTCAT_MAX)
    if code != 0 and not out:
        return _err((err or "postcat falló").strip(), 404 if "not found" in err.lower() else 500)
    return jsonify({"ok": True, "id": qid, "content": out, "truncated": truncated})


# ── Postfix: alias ───────────────────────────────────────────────────

def _read_aliases():
    try:
        with open(ALIASES, encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write_aliases(text):
    """Respalda, escribe y ejecuta newaliases; si falla, restaura el respaldo."""
    existed = os.path.exists(ALIASES)
    if existed:
        _backup(ALIASES)
    _atomic_write(ALIASES, text)
    code, out, err = run(["newaliases"], timeout=60)
    if code != 0:
        if existed:
            _restore(ALIASES)
            run(["newaliases"], timeout=60)
        return False, (out + err).strip() or "newaliases falló"
    return True, (out + err).strip()


@mail_bp.route("/api/aliases")
@login_required
def aliases():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    return jsonify({"path": ALIASES, "aliases": parse_aliases(_read_aliases())})


@mail_bp.route("/api/aliases", methods=["POST"])
@login_required
def aliases_save():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    data = request.get_json(silent=True) or {}
    act = data.get("action")
    name = str(data.get("name") or "").strip().lower()
    orig = str(data.get("orig") or "").strip()
    text = _read_aliases()
    if act == "delete":
        if not name or len(name) > 200 or CTRL_RE.search(name):
            return _err("Alias inválido")
        new, error = aliases_delete(text, name)
    elif act == "save":
        if not ALIAS_RE.fullmatch(name):
            return _err("Nombre de alias inválido (letras minúsculas, números, . _ + -)")
        if orig and (len(orig) > 200 or CTRL_RE.search(orig)):
            return _err("Alias original inválido")
        dest = validate_alias_dest(data.get("dest"))
        if not dest:
            return _err("Destinos inválidos: usa usuarios locales, correos, /ruta/archivo o :include:/ruta "
                        "separados por coma (las tuberías |comando se editan a mano)")
        new, error = aliases_set(text, orig, name, dest)
    else:
        return _err("Acción no permitida")
    if error:
        return _err(error, 409)
    try:
        ok, out = _write_aliases(new)
    except OSError as e:
        return _err(f"No se pudo escribir {ALIASES}: {e}", 500)
    if not ok:
        return _err("newaliases falló; se restauró el archivo anterior", 500, output=out)
    return jsonify({"ok": True, "output": out})


# ── Dovecot ──────────────────────────────────────────────────────────

def _doveconf_value(key):
    code, out, _ = run(["doveconf", "-h", key], timeout=10)
    return out.strip() if code == 0 else None


@mail_bp.route("/api/dovecot")
@login_required
def dovecot():
    if not _dovecot_ok():
        return _err("Dovecot no está instalado", 404)
    code, out, err = run(["doveconf", "-n"], timeout=20)
    top = parse_doveconf_top(out) if code == 0 else {}

    def val(key):
        return top[key] if key in top else _doveconf_value(key)

    location = val("mail_location")  # Dovecot 2.3
    if not location:  # Dovecot 2.4: mail_driver + mail_path
        driver, path = val("mail_driver"), val("mail_path")
        location = ":".join(filter(None, (driver, path))) or None
    conf_d = os.path.join(DOVECOT_DIR, "conf.d")
    files = [os.path.join(DOVECOT_DIR, f) for f in ("dovecot.conf", "local.conf")
             if os.path.isfile(os.path.join(DOVECOT_DIR, f))]
    if os.path.isdir(conf_d):
        files += [os.path.join(conf_d, f) for f in sorted(os.listdir(conf_d))
                  if f.endswith((".conf", ".ext")) and os.path.isfile(os.path.join(conf_d, f))]
    return jsonify({
        "valid": code == 0, "config": out if code == 0 else (out + err).strip(),
        "protocols": val("protocols") or "", "mail_location": location or "",
        "ssl": val("ssl") or "", "ssl_conf": DOVECOT_SSL_CONF if os.path.isfile(DOVECOT_SSL_CONF) else "",
        "files": files,
    })


@mail_bp.route("/api/dovecot/ssl", methods=["POST"])
@login_required
def dovecot_ssl():
    if not _dovecot_ok():
        return _err("Dovecot no está instalado", 404)
    value = (request.get_json(silent=True) or {}).get("ssl")
    if value not in ("yes", "no", "required"):
        return _err("Valor inválido")
    if not os.path.isfile(DOVECOT_SSL_CONF):
        return _err(f"No existe {DOVECOT_SSL_CONF}", 404)
    try:
        with open(DOVECOT_SSL_CONF, encoding="utf-8", errors="replace") as f:
            text = f.read()
        _backup(DOVECOT_SSL_CONF)
        _atomic_write(DOVECOT_SSL_CONF, set_dovecot_ssl(text, value))
    except OSError as e:
        return _err(f"No se pudo escribir {DOVECOT_SSL_CONF}: {e}", 500)
    code, out, err = run(["doveconf", "-n"], timeout=20)
    if code != 0:
        _restore(DOVECOT_SSL_CONF)
        return _err("doveconf -n falló; se restauró 10-ssl.conf", 409, output=(out + err).strip())
    if run(["systemctl", "is-active", "--quiet", "dovecot"], timeout=10)[0] != 0:
        return jsonify({"ok": True, "output": f"ssl = {value}\n(Dovecot inactivo: no se reinició)"})
    code, o, e = run(["systemctl", "restart", "dovecot"], timeout=60)
    if code != 0:
        return _err("Guardado, pero no se pudo reiniciar Dovecot", 500, output=(o + e).strip())
    return jsonify({"ok": True, "output": f"ssl = {value}\nsystemctl restart dovecot: OK"})


# ── Registro y prueba ────────────────────────────────────────────────

def _tail_file(path, lines):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 512 * 1024))
        data = f.read().decode("utf-8", "replace")
    return "\n".join(data.splitlines()[-lines:])


@mail_bp.route("/api/log")
@login_required
def log():
    source = request.args.get("source", "auto")
    if source not in ("auto", "file", "journal"):
        return _err("Origen inválido")
    if source in ("auto", "file") and os.path.isfile(MAIL_LOG) and os.path.getsize(MAIL_LOG) > 0:
        try:
            return jsonify({"source": MAIL_LOG, "log": _tail_file(MAIL_LOG, LOG_LINES)})
        except OSError as e:
            return _err(str(e), 500)
    if source == "file":
        return jsonify({"source": MAIL_LOG, "log": "", "note": f"{MAIL_LOG} no existe o está vacío"})
    base = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(LOG_LINES)]
    code, out, err, _ = _run_raw(base + ["--facility=mail"], timeout=30)
    label = "journalctl --facility=mail"
    if code != 0 or not out.strip() or out.strip() == "-- No entries --":
        units = ["-u", "postfix.service", "-u", "postfix@-.service", "-u", "dovecot.service"]
        code, out, err, _ = _run_raw(base + units, timeout=30)
        label = "journalctl -u postfix -u dovecot"
    if code != 0:
        return _err((err or "journalctl falló").strip(), 500)
    return jsonify({"source": label, "log": out})


@mail_bp.route("/api/test", methods=["POST"])
@login_required
def send_test():
    if not _postfix_ok():
        return _err("Postfix no está instalado", 404)
    data = request.get_json(silent=True) or {}
    to = str(data.get("to") or "").strip()
    subject = str(data.get("subject") or "").strip() or "Prueba de MatiPanel"
    body = str(data.get("body") or "").replace("\r\n", "\n")
    if not EMAIL_RE.fullmatch(to) or len(to) > 254:
        return _err("Correo de destino inválido")
    if CTRL_RE.search(subject) or len(subject) > 200:
        return _err("Asunto inválido (máx. 200 caracteres, sin saltos de línea)")
    if len(body) > 20000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", body):
        return _err("Mensaje inválido (máx. 20 000 caracteres)")
    try:
        msg = build_test_message(to, subject, body or "Correo de prueba enviado desde MatiPanel.")
    except (ValueError, TypeError) as e:
        return _err(f"No se pudo construir el mensaje: {e}")
    sendmail = shutil.which("sendmail") or "/usr/sbin/sendmail"
    code, out, err = run([sendmail, "-t", "-i"], input=msg, timeout=30)
    if code != 0:
        return _err((err or out).strip() or "sendmail falló", 500)
    return jsonify({"ok": True, "output": (out + err).strip()})


if __name__ == "__main__":
    # Autoprueba de la lógica pura (ejecutar con: python -m app.modules.mail)
    v, e = validate_param("myhostname", "Mail.Ejemplo.gob.gt")
    assert v == "mail.ejemplo.gob.gt" and not e
    assert validate_param("myhostname", "-bad.com")[1] and validate_param("myhostname", "a b")[1]
    assert validate_param("myorigin", "$mydomain") == ("$mydomain", None)
    assert validate_param("myorigin", "$foo")[1]
    assert validate_param("mydestination", "$myhostname,  localhost.$mydomain localhost") == \
        ("$myhostname, localhost.$mydomain, localhost", None)
    assert validate_param("mydestination", "") == ("", None)
    assert validate_param("mydestination", "a;b")[1]
    for ok in ("", "[smtp.gmail.com]:587", "relay.local", "[10.0.0.5]", "smtp.x.com:25"):
        assert validate_param("relayhost", ok)[1] is None, ok
    for bad in ("[smtp.x.com]:99999", "smtp x", "[a]:", "a;b"):
        assert validate_param("relayhost", bad)[1], bad
    assert validate_param("inet_interfaces", "all") == ("all", None)
    assert validate_param("inet_interfaces", "127.0.0.1, ::1") == ("127.0.0.1, ::1", None)
    assert validate_param("inet_interfaces", "all, x;y")[1]
    assert validate_param("inet_protocols", "ipv6,ipv4") == ("ipv4, ipv6", None)
    assert validate_param("inet_protocols", "ipv5")[1]
    assert validate_param("message_size_limit", "010") == ("10", None)
    assert validate_param("message_size_limit", "-1")[1]
    assert validate_param("home_mailbox", "Maildir/") == ("Maildir/", None)
    assert validate_param("home_mailbox", "/etc/x")[1] and validate_param("home_mailbox", "../x")[1]
    assert validate_param("smtpd_tls_cert_file", "/etc/letsencrypt/live/a.com/fullchain.pem")[1] is None
    assert validate_param("smtpd_tls_cert_file", "/etc/../x")[1]
    assert validate_param("smtpd_tls_cert_file", "/etc/x\nrelayhost=evil")[1]
    _, errs = validate_params({"message_size_limit": "100", "mailbox_size_limit": "50", "foo": "x"})
    assert set(errs) == {"mailbox_size_limit", "foo"}
    assert not validate_params({"message_size_limit": "100", "mailbox_size_limit": "0"})[1]

    pc = parse_postconf("myhostname = mail.x.com\nrelayhost =\nmydestination = $myhostname, localhost\n")
    assert pc == {"myhostname": "mail.x.com", "relayhost": "", "mydestination": "$myhostname, localhost"}

    assert valid_queue_id("4F9D21C3A1") and valid_queue_id("4dGh0q1Xyz9zz")
    assert not valid_queue_id("ALL") and not valid_queue_id("-d") and not valid_queue_id("12345")
    q = parse_queue(
        '{"queue_name": "deferred", "queue_id": "4F9D21C3A1", "arrival_time": 1700000000, '
        '"message_size": 1234, "forced_expire": false, "sender": "root@x.com", "recipients": '
        '[{"address": "a@y.com", "delay_reason": "connect to y.com: timed out"}, {"address": "b@y.com", '
        '"delay_reason": "connect to y.com: timed out"}]}\n'
        '{"queue_name": "hold", "queue_id": "5A1B2C3D4E", "arrival_time": 1700000100, '
        '"message_size": 99, "sender": "", "recipients": [{"address": "c@z.com"}]}\nbasura\n')
    assert [m["id"] for m in q] == ["5A1B2C3D4E", "4F9D21C3A1"]
    assert q[1]["recipients"] == ["a@y.com", "b@y.com"] and q[1]["reason"] == "connect to y.com: timed out"
    assert q[0]["sender"] == "<>" and q[0]["queue"] == "hold" and q[0]["size"] == 99

    al = ("# See man 5 aliases\npostmaster:    root\nwebmaster: root,\n  admin@x.com\n\n"
          "# fin\nlogs: \"|/usr/bin/foo bar\"\n")
    pa = parse_aliases(al)
    assert [a["name"] for a in pa] == ["postmaster", "webmaster", "logs"]
    assert pa[1]["dest"] == ["root", "admin@x.com"] and pa[1]["line"] == 3
    t, e = aliases_set(al, "webmaster", "webmaster", "mati")
    assert not e and "webmaster: mati\n\n# fin" in t and "admin@x.com" not in t and t.startswith("# See")
    t, e = aliases_set(al, "", "soporte", "root, a@b.com")
    assert t.endswith("soporte: root, a@b.com\n") and "# fin" in t
    assert aliases_set(al, "", "postmaster", "x")[1]
    assert aliases_set(al, "webmaster", "postmaster", "x")[1]
    assert aliases_set(al, "nadie", "nadie", "x")[1]
    t, e = aliases_delete(al, "webmaster")
    assert not e and "admin@x.com" not in t and "postmaster:    root" in t and "# fin" in t
    assert aliases_delete(al, "nadie")[1]
    assert aliases_set("root: mati", "", "x", "y")[0] == "root: mati\nx: y\n"
    assert validate_alias_dest("root, a@b.com ,/var/log/x.mbox, :include:/etc/lista") == \
        "root, a@b.com, /var/log/x.mbox, :include:/etc/lista"
    for bad in ("", "|/bin/sh", "a b", "root, /etc/../x", ":include:rel", "-x"):
        assert validate_alias_dest(bad) is None, bad

    dc = parse_doveconf_top("# 2.3.21 (47349e2482): /etc/dovecot/dovecot.conf\nmail_location = maildir:~/Maildir\n"
                            "protocols = \" imap pop3\"\nnamespace inbox {\n  inbox = yes\n  mailbox Drafts {\n"
                            "    special_use = \\Drafts\n  }\n}\nssl = required\nssl_cert = </etc/x.pem\n")
    assert dc["mail_location"] == "maildir:~/Maildir" and dc["ssl"] == "required" and "inbox" not in dc
    assert dc["protocols"] == '" imap pop3"' and "special_use" not in dc
    assert set_dovecot_ssl("# x\nssl = yes\nssl_cert = <a\n", "no") == "# x\nssl = no\nssl_cert = <a\n"
    assert set_dovecot_ssl("#ssl = yes\n", "required") == "#ssl = yes\nssl = required\n"

    ss = ("State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
          "LISTEN 0      100          0.0.0.0:25        0.0.0.0:*\n"
          "LISTEN 0      100             [::]:993          [::]:*\n"
          "LISTEN 0      4096   127.0.0.53%lo:53        0.0.0.0:*\n")
    assert parse_ss_ports(ss) == {25, 993, 53}

    m = build_test_message("a@b.com", "Prueba ñandú", "Hola ñ")
    assert "To: a@b.com" in m and "=?utf-8?" in m and "Hola =C3=B1" in m and m.isascii()
    try:
        build_test_message("a@b.com", "x\nBcc: evil@x.com", "y")
        raise AssertionError("inyección de cabecera aceptada")
    except ValueError:
        pass
    print("ok")
