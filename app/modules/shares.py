"""Recursos compartidos: Samba (smb.conf, usuarios smbpasswd) y NFS (/etc/exports)."""
import grp
import ipaddress
import json
import os
import posixpath
import pwd
import re
import shutil
import stat
import tempfile
from urllib.parse import urlsplit
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..distro import detect
from ..modules.packages import apt_get
from ..util import run

shares_bp = Blueprint("shares", __name__)

SMB_CONF = "/etc/samba/smb.conf"
EXPORTS = "/etc/exports"
BAK = ".matipanel.bak"

SHARE_RE = re.compile(r"^[A-Za-z0-9_$-]{1,40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}\$?$")       # usuario/grupo Linux
NETBIOS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,14}$")
TEXT_RE = re.compile(r"^[\w .,:()/@'+&%-]{0,120}$")                   # comment, server string
SMB_PATH_RE = re.compile(r"^/[A-Za-z0-9_./ -]*$")
NFS_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]*$")
MASK_RE = re.compile(r"^0?[0-7]{3}$")
MODE_RE = re.compile(r"^[0-7]{3,4}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                     r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
TESTPARM_ERR = re.compile(r"(?i)\berror\b|unknown parameter")
# ponytail: rutas del sistema que nunca se comparten ni se crean desde el panel
PROTECTED = ("/proc", "/sys", "/dev", "/boot", "/etc", "/run", "/bin", "/sbin", "/lib", "/lib64",
             "/usr", "/root")
PROTECTED_EXACT = {"/", "/var"}

RESERVED_SHARES = {"global", "homes", "printers", "ipc$"}
SPECIAL_SHARES = {"homes", "printers"}
SECURITY = ("user", "auto", "ads", "domain")
MAP_TO_GUEST = ("Never", "Bad User", "Bad Password", "Bad Uid")
MIN_PROTOCOLS = ("", "SMB2", "SMB2_10", "SMB3", "SMB3_11")
GLOBAL_DEFAULTS = {"security": "user", "maptoguest": "never", "serverminprotocol": ""}

NFS_FLAGS = {"rw", "ro", "sync", "async", "no_subtree_check", "subtree_check", "root_squash",
             "no_root_squash", "all_squash", "insecure", "secure"}
ANON_RE = re.compile(r"^(anonuid|anongid)=(\d{1,10})$")
NFS_CONFLICTS = [("rw", "ro"), ("sync", "async"), ("subtree_check", "no_subtree_check"),
                 ("root_squash", "no_root_squash"), ("secure", "insecure")]

SERVICES = {"samba": ["smbd", "nmbd"], "nfs": ["nfs-server"]}


# ── utilidades ───────────────────────────────────────────────────────

def _err(msg, code=400, **extra):
    return jsonify({"ok": False, "error": msg, **extra}), code


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write(path, text, like=None):
    """Escritura atómica (temporal + rename en el mismo directorio) con el modo y dueño de `like`:
    un fallo a mitad nunca deja /etc/exports o smb.conf truncados."""
    st = os.stat(like) if like and os.path.exists(like) else None
    fd, tmp = tempfile.mkstemp(prefix=".matipanel.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
            f.write(text)
        if st:
            os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, stat.S_IMODE(st.st_mode) if st else 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _backup(path):
    if os.path.exists(path):
        shutil.copy2(path, path + BAK)


def _active(unit):
    _, out, _ = run(["systemctl", "is-active", unit], timeout=10)
    return out.strip() or "unknown"


def _user_exists(name):
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _group_exists(name):
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def _uname(uid):
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except (KeyError, ValueError, TypeError):
        return str(uid)


def _perm(path):
    try:
        st = os.stat(path)
    except OSError:
        return ""
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    return f"{_uname(st.st_uid)}:{group} {oct(st.st_mode & 0o7777)[2:].zfill(4)}"


def _bool(v):
    v = str(v or "").strip().lower()
    return True if v in ("yes", "true", "1", "on") else False if v in ("no", "false", "0", "off") else None


def valid_path(p, nfs=False):
    """Ruta absoluta normalizada, sin '..' ni rutas del sistema."""
    if not p or len(p) > 255 or not (NFS_PATH_RE if nfs else SMB_PATH_RE).fullmatch(p):
        return False
    if posixpath.normpath(p) != p or "//" in p or p != p.strip():
        return False
    return not _protected(p)


def _protected(p):
    return p in PROTECTED_EXACT or any(p == b or p.startswith(b + "/") for b in PROTECTED)


def real_path_ok(p):
    """valid_path() es léxico: un enlace simbólico (p. ej. /home/ana/datos -> /etc, creado por un
    usuario) lo esquivaría. La ruta real, resolviendo enlaces, tampoco puede ser del sistema."""
    return not _protected(os.path.realpath(p))


def _clean_path(raw):
    p = str(raw or "").strip()
    return p.rstrip("/") or p


def _make_dir(path, spec):
    """Crea la carpeta con propietario/grupo/modo elegidos. Retorna (creadas, error)."""
    spec = spec if isinstance(spec, dict) else {}
    owner = str(spec.get("owner", "root")).strip()
    group = str(spec.get("group", "root")).strip()
    mode = str(spec.get("mode", "0755")).strip()
    if not NAME_RE.fullmatch(owner) or not _user_exists(owner):
        return None, f"El usuario «{owner}» no existe"
    if not NAME_RE.fullmatch(group) or not _group_exists(group):
        return None, f"El grupo «{group}» no existe"
    if not MODE_RE.fullmatch(mode):
        return None, "Permisos inválidos (usa octal, p. ej. 0775 o 2775)"
    created, p = [], path
    while p and p != "/" and not os.path.exists(p):
        created.insert(0, p)
        p = posixpath.dirname(p)
    try:
        os.makedirs(path)
        # chown/chmod por descriptor, no por ruta: si la carpeta está dentro de la de un usuario,
        # este podría cambiarla por un enlace a /etc/shadow justo después de crearla.
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.readlink(f"/proc/self/fd/{fd}") != path:
                raise OSError("la ruta cambió mientras se creaba (¿un enlace simbólico?)")
            os.fchown(fd, pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid)
            os.fchmod(fd, int(mode, 8))
        finally:
            os.close(fd)
    except (OSError, LookupError, KeyError) as e:
        _undo_dir(created)
        return None, f"No se pudo crear la carpeta: {e}"
    return created, None


def _undo_dir(created):
    for p in reversed(created or []):
        try:
            os.rmdir(p)
        except OSError:
            break


def _ensure_dir(path, data):
    """Comprueba que la ruta exista o la crea si el usuario lo pidió.
    Retorna (creadas, respuesta_error|None)."""
    if os.path.isdir(path):
        return [], None
    if os.path.exists(path):
        return None, _err("La ruta existe pero no es una carpeta")
    if not data.get("create"):
        return None, _err("La carpeta no existe", 409, missing_path=True)
    created, e = _make_dir(path, data.get("create"))
    return (None, _err(e)) if e else (created, None)


def _server_host():
    return urlsplit("//" + request.host).hostname or "servidor"


def _install(pkgs):
    if detect()["pkg"] != "apt":
        return _err("La instalación automática solo está disponible en Debian/Ubuntu")
    ok, out, msg = apt_get("install", *pkgs, timeout=900)
    if not ok:
        return _err(msg or "apt-get falló", 500, output=out[-4000:])
    return jsonify({"ok": True, "output": out[-4000:]})


# ── smb.conf: análisis y edición preservando comentarios ─────────────

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")
CANON = {
    "path": "path", "comment": "comment", "readonly": "read only", "guestok": "guest ok",
    "browseable": "browseable", "validusers": "valid users", "writelist": "write list",
    "createmask": "create mask", "directorymask": "directory mask", "workgroup": "workgroup",
    "serverstring": "server string", "netbiosname": "netbios name", "security": "security",
    "maptoguest": "map to guest", "serverminprotocol": "server min protocol",
}
ALIASES = {"path": {"directory"}, "guestok": {"public"}, "browseable": {"browsable"},
           "createmask": {"createmode"}, "directorymask": {"directorymode"},
           "serverminprotocol": {"minprotocol"}, "readonly": {"writeable", "writable", "writeok"},
           "printable": {"printok"}}
INVERTED = {"writeable", "writable", "writeok"}   # sinónimos con sentido contrario a "read only"


def _norm(key):
    """Samba ignora mayúsculas y espacios en los nombres de parámetros."""
    return re.sub(r"\s+", "", key).lower()


def smb_parse(text):
    """Retorna (líneas, secciones). Cada sección: name, start, last (última línea con
    parámetro), params [{key, value, first, last}]."""
    lines = text.split("\n")
    sections, cur, i = [], None, 0
    while i < len(lines):
        start, s = i, lines[i].strip()
        if not s or s[0] in "#;":
            i += 1
            continue
        while s.endswith("\\") and i + 1 < len(lines):   # continuación de línea
            i += 1
            s = s[:-1].rstrip() + " " + lines[i].strip()
        m = SECTION_RE.match(s)
        if m:
            cur = {"name": m.group(1).strip(), "start": start, "last": i, "params": []}
            sections.append(cur)
        elif "=" in s and cur is not None:
            k, v = s.split("=", 1)
            cur["params"].append({"key": _norm(k), "value": v.strip(), "first": start, "last": i})
            cur["last"] = i
        i += 1
    return lines, sections


def _find(sections, name):
    return next((s for s in sections if s["name"].lower() == str(name).lower()), None)


def _get(sec, canon):
    """Valor efectivo (el último gana, como en Samba). Los sinónimos invertidos se traducen."""
    names, val = {canon} | ALIASES.get(canon, set()), None
    for p in sec["params"]:
        if p["key"] in names:
            val = p["value"]
            if p["key"] in INVERTED:
                b = _bool(val)
                val = None if b is None else ("no" if b else "yes")
    return val


def smb_set(text, section, updates):
    """Cambia parámetros de una sección. updates: clave canónica → valor (None = quitar)."""
    lines, sections = smb_parse(text)
    sec = _find(sections, section)
    if sec is None:
        raise KeyError(section)
    indent = re.match(r"\s*", lines[sec["params"][0]["first"]]).group(0) if sec["params"] else "   "
    drop, repl, add = set(), {}, []
    for canon, value in updates.items():
        names = {canon} | ALIASES.get(canon, set())
        matches = [p for p in sec["params"] if p["key"] in names]
        new = None if value is None else f"{indent}{CANON[canon]} = {value}"
        for n, p in enumerate(matches):
            if n == 0 and new is not None:
                repl[p["first"]] = new
                drop.update(range(p["first"] + 1, p["last"] + 1))
            else:
                drop.update(range(p["first"], p["last"] + 1))
        if new is not None and not matches:
            add.append(new)
    out = []
    for idx, line in enumerate(lines):
        if idx not in drop:
            out.append(repl.get(idx, line))
        if idx == sec["last"]:
            out.extend(add)
    return "\n".join(out)


def smb_add(text, name, updates):
    body = "".join(f"   {CANON[k]} = {v}\n" for k, v in updates.items() if v is not None)
    head = text.rstrip("\n")
    return (head + "\n\n" if head else "") + f"[{name}]\n" + body


def smb_delete(text, name):
    lines, sections = smb_parse(text)
    sec = _find(sections, name)
    if sec is None:
        raise KeyError(name)
    out = lines[:sec["start"]] + lines[sec["last"] + 1:]
    s = sec["start"]
    if 0 < s < len(out) and not out[s - 1].strip() and not out[s].strip():
        del out[s]
    return "\n".join(out)


def smb_shares(text):
    _, sections = smb_parse(text)
    res = []
    for sec in sections:
        name = sec["name"]
        if name.lower() == "global":
            continue
        g = lambda k: _get(sec, k)  # noqa: E731
        res.append({
            "name": name, "special": name.lower() in SPECIAL_SHARES,
            "path": g("path") or "", "comment": g("comment") or "",
            "read_only": _bool(g("readonly")) is not False,
            "guest_ok": _bool(g("guestok")) is True,
            "browseable": _bool(g("browseable")) is not False,
            "printable": _bool(g("printable")) is True,
            "valid_users": g("validusers") or "", "write_list": g("writelist") or "",
            "create_mask": g("createmask") or "", "directory_mask": g("directorymask") or "",
        })
    return res


def smb_global(text):
    _, sections = smb_parse(text)
    sec = _find(sections, "global") or {"params": []}
    mtg = (_get(sec, "maptoguest") or "never").lower()
    return {
        "workgroup": _get(sec, "workgroup") or "WORKGROUP",
        "server_string": _get(sec, "serverstring") or "",
        "netbios_name": _get(sec, "netbiosname") or "",
        "security": (_get(sec, "security") or "user").lower(),
        "map_to_guest": next((m for m in MAP_TO_GUEST if m.lower() == mtg), _get(sec, "maptoguest")),
        "min_protocol": (_get(sec, "serverminprotocol") or "").upper(),
    }


def user_list(raw):
    """'juan, @contabilidad' → ('juan @contabilidad', None). Valida contra pwd/grp."""
    toks = [t for t in re.split(r"[\s,]+", str(raw or "").strip()) if t]
    if len(toks) > 50:
        return None, "Demasiados usuarios o grupos (máximo 50)"
    for t in toks:
        if t in ("%S", "%U"):
            continue
        if t[0] in "@+":
            if not NAME_RE.fullmatch(t[1:]) or not _group_exists(t[1:]):
                return None, f"El grupo «{t[1:]}» no existe en Linux"
        elif not NAME_RE.fullmatch(t) or not _user_exists(t):
            return None, f"El usuario «{t}» no existe en Linux (usa @grupo para grupos)"
    return " ".join(toks), None


def share_params(d, special=False):
    """Valida el formulario de un recurso → (updates, error)."""
    up = {}
    path = _clean_path(d.get("path"))
    if path or not special:
        if not valid_path(path):
            return None, ("Ruta inválida: debe ser absoluta (p. ej. /srv/compartido), sin '..' "
                          "ni caracteres especiales, y no puede ser una carpeta del sistema")
        up["path"] = path
    comment = str(d.get("comment") or "").strip()
    if not TEXT_RE.fullmatch(comment):
        return None, "Descripción inválida (máx. 120 caracteres, sin comillas ni barras invertidas)"
    up["comment"] = comment or None
    up["readonly"] = "no" if d.get("writable") else "yes"
    up["guestok"] = "yes" if d.get("guest_ok") else "no"
    up["browseable"] = "yes" if d.get("browseable", True) else "no"
    for key, field in (("validusers", "valid_users"), ("writelist", "write_list")):
        val, e = user_list(d.get(field))
        if e:
            return None, e
        up[key] = val or None
    for key, field in (("createmask", "create_mask"), ("directorymask", "directory_mask")):
        m = str(d.get(field) or "").strip()
        if m and not MASK_RE.fullmatch(m):
            return None, "Máscara inválida (octal de 3 o 4 dígitos, p. ej. 0664)"
        up[key] = m.zfill(4) if m else None
    return up, None


def global_params(d, current):
    """Valida la configuración global; solo retorna las claves que cambian."""
    wg = str(d.get("workgroup") or "").strip()
    nb = str(d.get("netbios_name") or "").strip()
    ss = str(d.get("server_string") or "").strip()
    sec, mtg, mp = d.get("security"), d.get("map_to_guest"), d.get("min_protocol") or ""
    if not NETBIOS_RE.fullmatch(wg):
        return None, "Grupo de trabajo inválido (1-15 letras, números, - o _)"
    if nb and not NETBIOS_RE.fullmatch(nb):
        return None, "Nombre NetBIOS inválido (1-15 letras, números, - o _)"
    if not TEXT_RE.fullmatch(ss):
        return None, "Descripción del servidor inválida"
    if sec not in SECURITY or mtg not in MAP_TO_GUEST or mp not in MIN_PROTOCOLS:
        return None, "Valor no permitido"
    new = {"workgroup": wg, "serverstring": ss or None, "netbiosname": nb or None,
           "security": sec, "maptoguest": mtg, "serverminprotocol": mp or None}
    cur = {"workgroup": current["workgroup"], "serverstring": current["server_string"],
           "netbiosname": current["netbios_name"], "security": current["security"],
           "maptoguest": current["map_to_guest"], "serverminprotocol": current["min_protocol"]}
    out = {}
    for k, v in new.items():
        if (v or "").lower() == (cur[k] or "").lower():
            continue
        if v is not None and v.lower() == GLOBAL_DEFAULTS.get(k) and not cur[k]:
            continue
        out[k] = v
    return out, None


def _smb_test(path):
    code, out, err = run(["testparm", "-s", path], timeout=30)
    ok = code == 0 and not TESTPARM_ERR.search(err)
    return ok, (err.strip() or out.strip()[-2000:]) if not ok else err.strip()


def _smb_reload():
    if run(["systemctl", "is-active", "--quiet", "smbd"])[0] != 0:
        return "smbd no está activo: los cambios se aplicarán al iniciarlo."
    code, o, e = run(["smbcontrol", "all", "reload-config"], timeout=20)
    if code != 0:
        code, o, e = run(["systemctl", "reload", "smbd"], timeout=30)
        if code != 0:
            return "Guardado, pero no se pudo recargar Samba: " + (e or o).strip()
    return "Configuración de Samba recargada."


def _smb_apply(new_text):
    """Prueba el texto con testparm en un archivo temporal; si es válido hace respaldo,
    lo instala y recarga. Si no, smb.conf no se toca."""
    tmp = SMB_CONF + ".matipanel.tmp"
    try:
        _write(tmp, new_text, like=SMB_CONF)
        ok, out = _smb_test(tmp)
        if not ok:
            os.remove(tmp)
            return False, out
        _backup(SMB_CONF)
        os.replace(tmp, SMB_CONF)
    except OSError as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, str(e)
    return True, (out + "\n" + _smb_reload()).strip()


# ── smbstatus / pdbedit ───────────────────────────────────────────────

def parse_smbstatus_json(d):
    sessions = [{"user": s.get("username", ""), "group": s.get("groupname", ""),
                 "machine": s.get("remote_machine") or s.get("hostname", ""),
                 "protocol": s.get("session_dialect", ""),
                 "encryption": (s.get("encryption") or {}).get("degree", "")}
                for s in (d.get("sessions") or {}).values()]
    shares = [{"service": t.get("service", ""), "machine": t.get("machine", ""),
               "connected_at": t.get("connected_at", "")}
              for t in (d.get("tcons") or {}).values() if t.get("service", "").upper() != "IPC$"]
    files = []
    for key, f in (d.get("open_files") or {}).items():
        path = posixpath.join(f["service_path"], f["filename"]) if f.get("service_path") and f.get("filename") else key
        opens = list((f.get("opens") or {}).values())
        files.append({"path": path, "users": sorted({_uname(o.get("uid")) for o in opens}), "count": len(opens)})
    return {"sessions": sessions, "shares": shares, "files": files}


def _rows(text):
    """Filas de una tabla de smbstatus (después de la línea de guiones)."""
    rows, on = [], False
    for line in (text or "").splitlines():
        if re.match(r"^-{5,}\s*$", line.strip()):
            on = True
        elif on and line.strip():
            rows.append(line)
    return rows


def parse_smbstatus_text(brief, shares_txt, locks):
    sessions, shares, files = [], [], {}
    for row in _rows(brief):
        m = re.match(r"^\s*(\d+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\([^)]*\))?\s*(\S*)", row)
        if m:
            sessions.append({"user": m.group(2), "group": m.group(3), "machine": m.group(4),
                             "protocol": m.group(5), "encryption": ""})
    for row in _rows(shares_txt):
        m = re.match(r"^\s*(\S+)\s+(\d+)\s+(\S+)\s+(.*?)(?:\s{2,}|$)", row)
        if m and m.group(1).upper() != "IPC$":
            shares.append({"service": m.group(1), "machine": m.group(3), "connected_at": m.group(4)})
    for row in _rows(locks):
        m = re.match(r"^\s*\d+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+\S+\s+(/.*?)\s{2,}(.*?)(?:\s{2,}|$)", row)
        if m:
            path = posixpath.join(m.group(2), m.group(3))
            f = files.setdefault(path, {"path": path, "users": set(), "count": 0})
            f["users"].add(_uname(m.group(1)))
            f["count"] += 1
    return {"sessions": sessions, "shares": shares,
            "files": [{**f, "users": sorted(f["users"])} for f in files.values()]}


def smb_connections():
    code, out, _ = run(["smbstatus", "-j"], timeout=15)
    if code == 0:
        try:
            return parse_smbstatus_json(json.loads(out))
        except (ValueError, AttributeError, KeyError, TypeError):
            pass
    outs = [run(["smbstatus", flag], timeout=15)[1] for flag in ("-b", "-S", "-L")]
    return parse_smbstatus_text(*outs)


def parse_pdbedit(text):
    users, cur = [], {}
    for line in (text or "").splitlines() + ["---------------"]:
        if re.match(r"^-{3,}\s*$", line):
            if cur.get("Unix username"):
                flags = cur.get("Account Flags", "").strip("[] ").replace(" ", "")
                users.append({"username": cur["Unix username"], "full_name": cur.get("Full Name", ""),
                              "flags": flags, "disabled": "D" in flags,
                              "pwd_last_set": cur.get("Password last set", "")})
            cur = {}
            continue
        k, sep, v = line.partition(":")
        if sep:
            cur.setdefault(k.strip(), v.strip())
    return users


# ── /etc/exports ─────────────────────────────────────────────────────

def valid_client(h):
    h = str(h or "").strip()
    if h == "*":
        return True
    if h.startswith("*."):
        return bool(HOST_RE.fullmatch(h[2:]))
    try:
        ipaddress.ip_network(h, strict=False) if "/" in h else ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    return bool(HOST_RE.fullmatch(h)) and not re.fullmatch(r"[\d.]+", h)


def nfs_options(raw):
    opts = raw if isinstance(raw, list) else str(raw or "").split(",")
    opts = list(dict.fromkeys(str(o).strip() for o in opts if str(o).strip()))
    anon = {}
    for o in opts:
        m = ANON_RE.fullmatch(o)
        if m:
            if int(m.group(2)) > 4294967294 or anon.setdefault(m.group(1), m.group(2)) != m.group(2):
                return None, f"Valor inválido o repetido: {o}"
        elif o not in NFS_FLAGS:
            return None, f"Opción no permitida: {o}"
    for a, b in NFS_CONFLICTS:
        if a in opts and b in opts:
            return None, f"Opciones incompatibles: {a} y {b}"
    return opts, None


def export_clients(raw):
    if not isinstance(raw, list) or not raw:
        return None, "Agrega al menos un cliente"
    if len(raw) > 50:
        return None, "Demasiados clientes (máximo 50)"
    res = []
    for c in raw:
        c = c if isinstance(c, dict) else {}
        host = str(c.get("host") or "").strip()
        if not valid_client(host):
            return None, f"Cliente inválido: «{host}» (usa IP, red CIDR, nombre, *.dominio o *)"
        opts, e = nfs_options(c.get("options"))
        if e:
            return None, f"{host}: {e}"
        res.append((host, opts))
    return res, None


def export_line(path, clients):
    return path + "\t" + " ".join(h + (f"({','.join(o)})" if o else "") for h, o in clients)


def _export_entry(s):
    if s.startswith('"'):
        end = s.find('"', 1)
        path, rest = (s[1:end], s[end + 1:]) if end > 0 else (s, "")
    else:
        path, _, rest = s.replace("\t", " ").partition(" ")
    clients, editable = [], True
    for tok in rest.split():
        if tok.startswith("-"):
            editable = False
            clients.append({"host": tok, "options": []})
            continue
        m = re.fullmatch(r"([^()]*)\(([^()]*)\)", tok)
        host, opts = (m.group(1), [o for o in m.group(2).split(",") if o]) if m else (tok, [])
        clients.append({"host": host or "*", "options": opts})
        if not host or not valid_client(host) or nfs_options(opts)[1]:
            editable = False
    if not valid_path(path, nfs=True) or not clients:
        editable = False
    return {"path": path, "clients": clients, "editable": editable}


def exports_parse(text):
    lines, entries, i = text.split("\n"), [], 0
    while i < len(lines):
        start, s = i, lines[i].split("#", 1)[0].strip()
        while s.endswith("\\") and i + 1 < len(lines):
            i += 1
            s = s[:-1].rstrip() + " " + lines[i].split("#", 1)[0].strip()
        if s:
            entries.append({**_export_entry(s), "first": start, "last": i,
                            "raw": "\n".join(lines[start:i + 1])})
        i += 1
    return entries


def exports_replace(text, entry, line=None):
    lines = text.split("\n")
    lines[entry["first"]:entry["last"] + 1] = [line] if line is not None else []
    return "\n".join(lines)


def exports_append(text, line):
    head = text.rstrip("\n")
    return (head + "\n" if head else "") + line + "\n"


def parse_exportfs(out):
    res, cur = [], None
    for tok in (out or "").split():
        if tok.startswith("/"):
            cur = {"path": tok, "clients": []}
            res.append(cur)
        elif cur is not None:
            m = re.fullmatch(r"(.*?)\((.*)\)", tok)
            if m:
                cur["clients"].append({"host": "*" if m.group(1) == "<world>" else m.group(1),
                                       "options": m.group(2).split(",")})
    return res


def parse_ss(out, port="2049"):
    peers = {}
    for line in (out or "").splitlines():
        cols = line.split()
        for i, c in enumerate(cols[:-1]):
            if c.endswith(":" + port):
                host = cols[i + 1].rsplit(":", 1)[0].strip("[]")
                host = host[7:] if host.startswith("::ffff:") else host
                peers[host] = peers.get(host, 0) + 1
                break
    return [{"address": h, "connections": n} for h, n in sorted(peers.items())]


def nfs_clients():
    code, out, _ = run(["ss", "-tnH", "state", "established", "(", "sport", "=", ":2049", ")"], timeout=10)
    if code == 0:
        return parse_ss(out)
    code, out, _ = run(["showmount", "-a", "--no-headers"], timeout=10)
    hosts = {}
    for line in out.splitlines() if code == 0 else []:
        h = line.split(":", 1)[0].strip()
        if h:
            hosts[h] = hosts.get(h, 0) + 1
    return [{"address": h, "connections": n} for h, n in sorted(hosts.items())]


def _exports_apply(new, old):
    try:
        _backup(EXPORTS)
        _write(EXPORTS, new, like=EXPORTS)
    except OSError as e:
        return False, str(e)
    code, o, e = run(["exportfs", "-ra"], timeout=60)
    out = (o + e).strip()
    if code != 0:
        _write(EXPORTS, old, like=EXPORTS)
        run(["exportfs", "-ra"], timeout=60)
        return False, out or f"exportfs terminó con código {code}"
    return True, out


# ── rutas ────────────────────────────────────────────────────────────

@shares_bp.route("/")
@login_required
def index():
    return render_template("shares.html", server=_server_host(), smb_conf=SMB_CONF,
                           exports=EXPORTS, apt=detect()["pkg"] == "apt")


@shares_bp.route("/api/<svc>/install", methods=["POST"])
@login_required
def install(svc):
    if svc == "samba":
        extra = ["smbclient"] if (request.get_json(silent=True) or {}).get("smbclient", True) else []
        return _install(["samba", *extra])
    if svc == "nfs":
        return _install(["nfs-kernel-server"])
    return _err("Servicio desconocido", 404)


@shares_bp.route("/api/<svc>/service", methods=["POST"])
@login_required
def service(svc):
    if svc not in SERVICES:
        return _err("Servicio desconocido", 404)
    act = (request.get_json(silent=True) or {}).get("action", "")
    if act not in ("start", "stop", "restart"):
        return _err("Acción no permitida")
    if svc == "samba" and act != "stop" and os.path.exists(SMB_CONF):
        ok, out = _smb_test(SMB_CONF)
        if not ok:
            return _err("smb.conf tiene errores; no se aplicó", 409, output=out)
    log = []
    for n, unit in enumerate(SERVICES[svc]):
        code, o, e = run(["systemctl", act, unit], timeout=60)
        if code != 0:
            if n == 0:
                return _err((e or o).strip() or "Fallo desconocido", 500)
            log.append(f"{unit}: {(e or o).strip()}")   # nmbd es opcional
    return jsonify({"ok": True, "output": "\n".join(log)})


# Samba

@shares_bp.route("/api/samba/status")
@login_required
def smb_status():
    res = {"installed": shutil.which("smbd") is not None, "can_install": detect()["pkg"] == "apt"}
    if not res["installed"]:
        return jsonify(res)
    _, out, err = run(["smbd", "-V"], timeout=10)
    res.update(smbd=_active("smbd"), nmbd=_active("nmbd"), version=(out or err).strip(),
               conf_exists=os.path.isfile(SMB_CONF), **smb_connections())
    return jsonify(res)


@shares_bp.route("/api/samba/config")
@login_required
def smb_config():
    text = _read(SMB_CONF)
    shares = smb_shares(text)
    for s in shares:
        s["exists"] = bool(s["path"]) and os.path.isdir(s["path"])
        s["perm"] = _perm(s["path"]) if s["exists"] else ""
    return jsonify({"global": smb_global(text), "shares": shares})


@shares_bp.route("/api/samba/global", methods=["POST"])
@login_required
def smb_save_global():
    text = _read(SMB_CONF)
    up, e = global_params(request.get_json(silent=True) or {}, smb_global(text))
    if e:
        return _err(e)
    if not up:
        return jsonify({"ok": True, "output": "Sin cambios."})
    if _find(smb_parse(text)[1], "global") is None:
        text = "[global]\n" + text
    ok, out = _smb_apply(smb_set(text, "global", up))
    if not ok:
        return _err("testparm rechazó la configuración; no se guardó", 409, output=out)
    return jsonify({"ok": True, "output": out})


@shares_bp.route("/api/samba/share", methods=["POST"])
@login_required
def smb_save_share():
    d = request.get_json(silent=True) or {}
    original = str(d.get("original") or "")
    text = _read(SMB_CONF)
    sections = smb_parse(text)[1]
    if original:
        # [global] no es un recurso: "guest ok", "path"… ahí cambiarían los valores por defecto de todos
        if original.lower() == "global" or not _find(sections, original):
            return _err("El recurso no existe", 404)
        special = original.lower() in SPECIAL_SHARES
    else:
        name = str(d.get("name") or "").strip()
        if not SHARE_RE.fullmatch(name):
            return _err("Nombre inválido: 1-40 letras, números, _, - o $ (sin espacios)")
        if name.lower() in RESERVED_SHARES:
            return _err("Ese nombre está reservado por Samba")
        if _find(sections, name):
            return _err("Ya existe un recurso con ese nombre", 409)
        special = False
    up, e = share_params(d, special)
    if e:
        return _err(e)
    created = []
    if up.get("path"):
        if not real_path_ok(up["path"]):
            return _err("La ruta (o un enlace simbólico en ella) apunta a una carpeta del sistema")
        created, resp = _ensure_dir(up["path"], d)
        if resp:
            return resp
    new = smb_set(text, original, up) if original else smb_add(text, name, up)
    ok, out = _smb_apply(new)
    if not ok:
        _undo_dir(created)
        return _err("testparm rechazó la configuración; no se guardó", 409, output=out)
    return jsonify({"ok": True, "output": out})


@shares_bp.route("/api/samba/share/delete", methods=["POST"])
@login_required
def smb_delete_share():
    name = str((request.get_json(silent=True) or {}).get("name") or "")
    text = _read(SMB_CONF)
    if not name or name.lower() == "global" or not _find(smb_parse(text)[1], name):
        return _err("El recurso no existe", 404)
    ok, out = _smb_apply(smb_delete(text, name))
    if not ok:
        return _err("testparm rechazó la configuración; no se guardó", 409, output=out)
    return jsonify({"ok": True, "output": out})


@shares_bp.route("/api/samba/users")
@login_required
def smb_users():
    code, out, err = run(["pdbedit", "-L", "-v"], timeout=20)
    if code == 127:
        return _err("pdbedit no está disponible", 500)
    users = parse_pdbedit(out)
    names = {u["username"] for u in users}
    for u in users:
        u["linux"] = _user_exists(u["username"])
    candidates = sorted(p.pw_name for p in pwd.getpwall()
                        if 1000 <= p.pw_uid < 65534 and p.pw_name not in names)
    return jsonify({"users": users, "candidates": candidates,
                    "error": (err.strip() or "pdbedit falló") if code != 0 else ""})


@shares_bp.route("/api/samba/user", methods=["POST"])
@login_required
def smb_user_action():
    d = request.get_json(silent=True) or {}
    act, user, pw = d.get("action", ""), str(d.get("user") or "").strip(), str(d.get("password") or "")
    flags = {"enable": "-e", "disable": "-d", "delete": "-x"}
    if act not in ("add", "passwd", *flags):
        return _err("Acción no permitida")
    if not NAME_RE.fullmatch(user):
        return _err("Nombre de usuario inválido")
    inp = None
    if act in ("add", "passwd"):
        if not 8 <= len(pw) <= 127 or any(c in pw for c in "\r\n\x00"):
            return _err("La contraseña debe tener entre 8 y 127 caracteres")
        if act == "add" and not _user_exists(user):
            return _err("Ese usuario no existe en Linux; créalo primero en Usuarios y grupos")
        cmd, inp = ["smbpasswd", "-s", *(["-a"] if act == "add" else []), user], f"{pw}\n{pw}\n"
    else:
        cmd = ["smbpasswd", flags[act], user]
    code, out, err = run(cmd, timeout=20, input=inp)
    if code != 0:
        return _err((err or out).strip() or "smbpasswd falló", 500)
    return jsonify({"ok": True, "output": (out + err).strip()})


# NFS

@shares_bp.route("/api/nfs/status")
@login_required
def nfs_status():
    res = {"installed": shutil.which("exportfs") is not None, "can_install": detect()["pkg"] == "apt"}
    if not res["installed"]:
        return jsonify(res)
    versions = _read("/proc/fs/nfsd/versions").split()
    code, out, err = run(["exportfs", "-v"], timeout=15)
    res.update(active=_active("nfs-server"),
               versions=[v[1:] for v in versions if v.startswith("+")],
               exported=parse_exportfs(out) if code == 0 else [],
               exportfs_error=(err.strip() if code != 0 else ""),
               clients=nfs_clients())
    return jsonify(res)


@shares_bp.route("/api/nfs/exports")
@login_required
def nfs_exports():
    entries = exports_parse(_read(EXPORTS))
    for x in entries:
        x["exists"] = os.path.isdir(x["path"])
        x["perm"] = _perm(x["path"]) if x["exists"] else ""
        del x["first"], x["last"]
    return jsonify({"exports": entries, "exists": os.path.exists(EXPORTS)})


@shares_bp.route("/api/nfs/export", methods=["POST"])
@login_required
def nfs_save_export():
    d = request.get_json(silent=True) or {}
    original = str(d.get("original") or "")
    path = _clean_path(d.get("path"))
    if not valid_path(path, nfs=True):
        return _err("Ruta inválida: absoluta, sin espacios ni '..', y no puede ser una carpeta del sistema")
    clients, e = export_clients(d.get("clients"))
    if e:
        return _err(e)
    text = _read(EXPORTS)
    entries = exports_parse(text)
    find = lambda p: next((x for x in entries if x["path"] == p), None)  # noqa: E731
    entry = find(original) if original else None
    if original and not entry:
        return _err("La exportación no existe", 404)
    if path != original and find(path):
        return _err("Esa carpeta ya está exportada; edita la entrada existente", 409)
    if not real_path_ok(path):
        return _err("La ruta (o un enlace simbólico en ella) apunta a una carpeta del sistema")
    created, resp = _ensure_dir(path, d)
    if resp:
        return resp
    line = export_line(path, clients)
    new = exports_replace(text, entry, line) if entry else exports_append(text, line)
    ok, out = _exports_apply(new, text)
    if not ok:
        _undo_dir(created)
        return _err("exportfs rechazó la configuración; se restauró /etc/exports", 409, output=out)
    return jsonify({"ok": True, "output": out})


@shares_bp.route("/api/nfs/export/delete", methods=["POST"])
@login_required
def nfs_delete_export():
    path = str((request.get_json(silent=True) or {}).get("path") or "")
    text = _read(EXPORTS)
    entry = next((x for x in exports_parse(text) if x["path"] == path), None)
    if not path or not entry:
        return _err("La exportación no existe", 404)
    ok, out = _exports_apply(exports_replace(text, entry), text)
    if not ok:
        return _err("exportfs falló; se restauró /etc/exports", 409, output=out)
    return jsonify({"ok": True, "output": out})
