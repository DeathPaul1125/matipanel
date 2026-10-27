"""Seguridad del servidor: lista de comprobación de endurecimiento (SSH, firewall, actualizaciones,
reinicio pendiente, puertos expuestos, cuentas) con puntuación, y gestión básica de fail2ban.

Cada corrección escribe un archivo propio del panel de forma atómica (temporal + rename, sin
seguir enlaces simbólicos), lo valida con la herramienta del servicio (`sshd -t`, `apt-config dump`,
`fail2ban-client -t`) y solo entonces recarga; si algo falla se restaura el contenido anterior.
"""
import ipaddress
import os
import re
import stat
import tempfile
import threading

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from ..distro import detect
from ..util import run
from .packages import apt_get, PM_ENV

hardening_bp = Blueprint("hardening", __name__)

# Rutas como constantes del módulo (las pruebas las apuntan a un directorio temporal)
SSHD_DROPIN = "/etc/ssh/sshd_config.d/00-matipanel.conf"  # 00: sshd usa el primer valor que lee; así gana a 50-cloud-init.conf y otros
F2B_JAIL = "/etc/fail2ban/jail.d/matipanel.conf"
AUTO_UPGRADES = "/etc/apt/apt.conf.d/20auto-upgrades"
UU_BIN = "/usr/bin/unattended-upgrade"
REBOOT_REQ = "/var/run/reboot-required"
REBOOT_PKGS = "/var/run/reboot-required.pkgs"
PASSWD, SHADOW, GROUP = "/etc/passwd", "/etc/shadow", "/etc/group"
AUTH_LOGS = ("/var/log/auth.log", "/var/log/secure")

JAIL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
F2B_TIME_RE = re.compile(r"(?:-1|\d{1,7}[smhdw]?)")       # 600, 10m, 1h, 1d, 1w; bantime -1 = permanente
KEY_LINE_RE = re.compile(r"^\s*(?:\S+\s+)?(?:ssh-|ecdsa-|sk-)\S+\s+[A-Za-z0-9+/=]{20,}", re.M)
ADMIN_GROUPS = ("sudo", "wheel", "admin")
SSH_VALUES = {"PermitRootLogin": {"no", "prohibit-password", "yes"},
              "PasswordAuthentication": {"yes", "no"}, "X11Forwarding": {"yes", "no"}}
SSH_KEYS = ("PermitRootLogin", "PasswordAuthentication", "Port", "MaxAuthTries", "X11Forwarding",
            "KbdInteractiveAuthentication")
HEADER = "# Gestionado por MatiPanel (Seguridad del servidor). Los cambios manuales se sobrescriben.\n"

_lock = threading.Lock()  # una corrección a la vez (escribir → validar → recargar)


class HardeningError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


@hardening_bp.errorhandler(HardeningError)
def _handle(e):
    return jsonify({"ok": False, "error": str(e)}), e.code


# ── Archivos: lectura y escritura atómica sin seguir enlaces ──────────────────

def _read(path, limit=1 << 20):
    """Contenido de `path` o None si no existe. Nunca sigue un enlace simbólico
    (O_NOFOLLOW) ni se cuelga en un FIFO (O_NONBLOCK)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if os.path.islink(path):
            raise HardeningError(f"{path} es un enlace simbólico; revísalo a mano", 409)
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise HardeningError(f"No se pudo leer {path}: {e.strerror}", 500)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise HardeningError(f"{path} no es un archivo normal", 409)
        return f.read(limit).decode("utf-8", "replace")


def _write(path, text, mode=0o644):
    """Temporal + os.replace en el mismo directorio: nunca queda a medias y, si `path` fuera un
    enlace simbólico, se reemplaza el enlace en vez de escribir en su destino."""
    d = os.path.dirname(path)
    if os.path.islink(d) or not os.path.isdir(d):
        raise HardeningError(f"El directorio {d} no existe", 501)
    fd, tmp = tempfile.mkstemp(prefix=".matipanel.", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _restore(path, old):
    """Deja `path` como estaba: con su contenido anterior o, si no existía, sin archivo."""
    if old is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    else:
        _write(path, old)


def _tail(code, out, err, default):
    lines = (err.strip() or out.strip()).splitlines()
    return "\n".join(lines[-6:]) or f"{default} (código {code})"


# ── Cuentas ────────────────────────────────────────────────────────────────

def _passwd():
    users = []
    for line in (_read(PASSWD) or "").splitlines():
        p = line.split(":")
        if len(p) >= 7 and p[2].isdigit() and p[3].isdigit():
            users.append({"name": p[0], "uid": int(p[2]), "gid": int(p[3]), "home": p[5], "shell": p[6]})
    return users


def _shadow_state():
    """usuario -> 'empty' | 'locked' | 'set'. El hash nunca sale de esta función."""
    res = {}
    try:
        text = _read(SHADOW) or ""
    except HardeningError:
        text = ""
    for line in text.splitlines():
        p = line.split(":")
        if len(p) >= 2:
            h = p[1]
            res[p[0]] = "empty" if h == "" else "locked" if h[:1] in ("!", "*") else "set"
    return res


def _admins(users):
    """Miembros de sudo/wheel/admin (lista secundaria o grupo principal) → {usuario: grupo}."""
    res = {}
    for line in (_read(GROUP) or "").splitlines():
        p = line.split(":")
        if len(p) >= 4 and p[0] in ADMIN_GROUPS and p[2].isdigit():
            for m in filter(None, p[3].split(",")):
                res.setdefault(m, p[0])
            for u in users:
                if u["gid"] == int(p[2]):
                    res.setdefault(u["name"], p[0])
    return res


def _key_count(home):
    """Llaves en ~/.ssh/authorized_keys (solo se cuentan líneas con forma de llave pública)."""
    if not home.startswith("/") or ".." in home:
        return 0
    try:
        text = _read(os.path.join(home, ".ssh", "authorized_keys"), limit=256 * 1024) or ""
    except HardeningError:
        return 0
    return len(KEY_LINE_RE.findall(text))


def _accounts():
    users = _passwd()
    shadow = _shadow_state()
    admins = _admins(users)
    rows = []
    for u in users:
        login = not u["shell"].endswith(("nologin", "false", "sync", "halt", "shutdown"))
        flagged = u["uid"] == 0 or shadow.get(u["name"]) == "empty" or u["name"] in admins
        if not (flagged or (login and 1000 <= u["uid"] < 65534)):
            continue
        rows.append({"user": u["name"], "uid": u["uid"], "shell": u["shell"], "login": login,
                     "admin": admins.get(u["name"], "root" if u["uid"] == 0 else ""),
                     "password": shadow.get(u["name"], "unknown"), "keys": _key_count(u["home"])})
    return rows


# ── SSH ────────────────────────────────────────────────────────────────────

def _sshd_effective():
    """Configuración efectiva (`sshd -T`): dict en minúsculas; 'port' es lista. None si no hay sshd."""
    code, out, err = run(["sshd", "-T"], timeout=15)
    if code == 127:
        return None
    if code != 0:
        raise HardeningError("sshd -T falló: " + _tail(code, out, err, "error"), 500)
    cfg = {"port": []}
    for line in out.splitlines():
        k, _, v = line.strip().partition(" ")
        if k == "port":
            cfg["port"].append(v)
        elif k == "permitrootlogin" and v == "without-password":  # alias antiguo
            cfg[k] = "prohibit-password"
        elif k and k not in cfg:
            cfg[k] = v
    return cfg


def _ssh_unit():
    return "sshd" if detect()["family"] == "rhel" else "ssh"


def _parse_dropin(text):
    vals = {}
    for line in (text or "").splitlines():
        k, _, v = line.strip().partition(" ")
        if k in SSH_KEYS and v.strip():
            vals[k] = v.strip()
    return vals


def _ssh_checks(cfg, accounts):
    if cfg is None:
        return [_item("ssh", "Servidor SSH", "info", "sshd no está instalado.",
                      "Nada que revisar: sin SSH no hay acceso remoto por consola.")]
    root = cfg.get("permitrootlogin", "")
    pwd = cfg.get("passwordauthentication", "")
    tries = int(cfg["maxauthtries"]) if cfg.get("maxauthtries", "").isdigit() else 6
    x11 = cfg.get("x11forwarding", "")
    ports = cfg.get("port") or ["22"]
    keys = [a["user"] for a in accounts if a["keys"] and (a["admin"] or a["uid"] == 0)]
    items = [
        _item("ssh-root", "SSH: acceso directo de root",
              "err" if root == "yes" else "ok",
              f"PermitRootLogin = {root or '?'}",
              "Usa «no» (o «prohibit-password» para permitir root solo con llave) y entra con un usuario administrador.",
              fix="ssh", key="PermitRootLogin"),
        _item("ssh-password", "SSH: inicio de sesión con contraseña",
              "ok" if pwd == "no" else "warn",
              f"PasswordAuthentication = {pwd or '?'}. "
              + (f"Administradores con llave SSH: {', '.join(keys)}." if keys else "Ningún administrador tiene llaves en ~/.ssh/authorized_keys."),
              "Con llaves SSH los ataques de fuerza bruta no sirven. Antes de desactivar las contraseñas "
              "comprueba que puedes entrar con tu llave, o te quedarás fuera.",
              fix="ssh", key="PasswordAuthentication"),
        _item("ssh-port", "SSH: puerto", "info" if ports == ["22"] else "ok",
              "Puerto " + ", ".join(ports),
              "El puerto 22 es el que prueban los bots. Cambiarlo reduce ruido en los registros, pero no sustituye a las llaves ni a fail2ban.",
              fix="ssh", key="Port"),
        _item("ssh-tries", "SSH: intentos por conexión", "ok" if tries <= 4 else "warn",
              f"MaxAuthTries = {tries}", "Recomendado 3 o 4: menos intentos por conexión frena la fuerza bruta.",
              fix="ssh", key="MaxAuthTries"),
        _item("ssh-x11", "SSH: reenvío X11", "ok" if x11 == "no" else "warn",
              f"X11Forwarding = {x11 or '?'}", "Un servidor no necesita reenvío gráfico; desactívalo.",
              fix="ssh", key="X11Forwarding"),
    ]
    return items


def _item(id, title, status, detail, rec, fix=None, key=None, link=None):
    return {"id": id, "title": title, "status": status, "detail": detail, "rec": rec,
            "fix": fix, "key": key, "link": link}


@hardening_bp.route("/api/ssh", methods=["POST"])
@login_required
def ssh_fix():
    """Body: {settings: {Clave: valor, …}, confirm: bool}. Solo se escriben las claves elegidas
    (se conservan las que el panel ya había escrito)."""
    data = request.get_json(silent=True) or {}
    settings = data.get("settings")
    if not isinstance(settings, dict) or not settings:
        raise HardeningError("Elige al menos un ajuste")
    clean = {}
    for k, v in settings.items():
        v = str(v).strip()
        if k in SSH_VALUES and v in SSH_VALUES[k]:
            clean[k] = v
        elif k == "Port" and v.isdigit() and 1 <= int(v) <= 65535:
            clean[k] = str(int(v))
        elif k == "MaxAuthTries" and v.isdigit() and 1 <= int(v) <= 10:
            clean[k] = str(int(v))
        else:
            raise HardeningError(f"Valor no válido para {k}")
    with _lock:
        cfg = _sshd_effective()
        if cfg is None:
            raise HardeningError("sshd no está instalado", 404)
        risky = clean.get("PasswordAuthentication") == "no" or ("Port" in clean and clean["Port"] not in cfg["port"])
        if clean.get("PermitRootLogin") == "no" and not any(a["admin"] and a["uid"] != 0 and a["login"] for a in _accounts()):
            risky = True  # sin otro administrador, root era la única forma de entrar
        if risky and data.get("confirm") is not True:
            raise HardeningError("Este cambio puede dejarte sin acceso SSH: confirma explícitamente")
        if not os.path.isdir(os.path.dirname(SSHD_DROPIN)):
            raise HardeningError("Este sshd no usa /etc/ssh/sshd_config.d; edita sshd_config a mano", 501)
        old = _read(SSHD_DROPIN)
        vals = _parse_dropin(old)
        vals.update(clean)
        if "PasswordAuthentication" in clean:  # con PAM, el método teclado-interactivo también pide contraseña
            if clean["PasswordAuthentication"] == "no":
                vals["KbdInteractiveAuthentication"] = "no"
            else:
                vals.pop("KbdInteractiveAuthentication", None)
        _write(SSHD_DROPIN, HEADER + "".join(f"{k} {vals[k]}\n" for k in SSH_KEYS if k in vals))
        code, out, err = run(["sshd", "-t"], timeout=15)
        if code != 0:
            _restore(SSHD_DROPIN, old)
            raise HardeningError("sshd -t rechazó la configuración; se restauró la anterior:\n"
                                 + _tail(code, out, err, "error"), 422)
        code, out, err = run(["systemctl", "reload", _ssh_unit()], timeout=30)
        if code != 0:
            _restore(SSHD_DROPIN, old)
            run(["systemctl", "reload", _ssh_unit()], timeout=30)
            raise HardeningError("No se pudo recargar SSH; se restauró la configuración anterior:\n"
                                 + _tail(code, out, err, "error"), 500)
        # sshd usa el PRIMER valor que encuentra: otro archivo de sshd_config.d con nombre menor
        # (p. ej. 50-cloud-init.conf) o una línea antes del Include puede ganarle al nuestro.
        now = _sshd_effective() or {}
        ignored = [k for k, v in clean.items()
                   if (v not in now.get("port", []) if k == "Port" else now.get(k.lower(), v) != v)]
    res = {"ok": True}
    if ignored:
        res["warning"] = ("Guardado, pero sshd sigue usando otro valor para: " + ", ".join(ignored)
                          + ". Otro archivo de /etc/ssh/sshd_config.d (que se lee antes) o sshd_config lo define primero.")
    return jsonify(res)


# ── Firewall, actualizaciones, reinicio ─────────────────────────────────────

def _firewall_check():
    detect.cache_clear()
    fw = detect()["fw"]
    active = False
    if fw == "ufw":
        code, out, _ = run(["ufw", "status"], timeout=10)
        active = code == 0 and "status: active" in out.lower()
    elif fw == "firewalld":
        code, out, _ = run(["firewall-cmd", "--state"], timeout=10)
        active = code == 0 and out.strip() == "running"
    elif fw in ("nftables", "iptables"):
        code, out, _ = run(["nft", "list", "ruleset"], timeout=10)
        active = code == 0 and "hook input" in out
        if not active and fw == "iptables":
            code, out, _ = run(["iptables", "-S", "INPUT"], timeout=10)
            active = code == 0 and ("-P INPUT DROP" in out or "-j DROP" in out or "-j REJECT" in out)
    name = {"ufw": "UFW", "firewalld": "firewalld", "nftables": "nftables", "iptables": "iptables"}.get(fw)
    return _item("firewall", "Firewall activo", "ok" if active else "err",
                 f"{name}: {'activo' if active else 'inactivo o sin reglas de entrada'}" if name
                 else "No se detectó ningún firewall (ufw, firewalld, nftables).",
                 "Activa el firewall permitiendo solo SSH, el panel y los servicios que publicas.",
                 link="firewall")


def _updates_checks():
    pm = detect()["pkg"]
    items = []
    if pm == "apt":
        code, out, err = run(["apt", "list", "--upgradable"], timeout=60, env=PM_ENV)
        if code == 0:
            lines = [l for l in out.splitlines() if "/" in l and "[upgradable" in l]
            sec = [l.split("/")[0] for l in lines if "-security" in l.split(" ")[0]]
            items.append(_item("updates", "Actualizaciones de seguridad",
                               "err" if sec else "warn" if lines else "ok",
                               f"{len(lines)} paquete(s) por actualizar, {len(sec)} de seguridad"
                               + (": " + ", ".join(sec[:12]) + ("…" if len(sec) > 12 else "") if sec else "."),
                               "Aplica las actualizaciones desde Paquetes. La lista se basa en el último «apt update».",
                               link="packages"))
        code, out, _ = run(["apt-config", "dump"], timeout=20)
        m = re.search(r'^APT::Periodic::Unattended-Upgrade "(\d+)";', out if code == 0 else _read(AUTO_UPGRADES) or "", re.M)
        installed, enabled = os.path.exists(UU_BIN), bool(m and m.group(1) != "0")
        items.append(_item("unattended", "Actualizaciones automáticas de seguridad",
                           "ok" if installed and enabled else "warn",
                           f"unattended-upgrades {'instalado' if installed else 'no instalado'}, {'activado' if enabled else 'desactivado'}.",
                           "Instala y activa unattended-upgrades para que los parches de seguridad se apliquen solos cada día.",
                           fix=None if installed and enabled else "unattended"))
    elif pm in ("dnf", "yum"):
        code, out, _ = run([pm, "-q", "updateinfo", "list", "--security"], timeout=90)
        if code == 0:
            n = len([l for l in out.splitlines() if l.strip()])
            items.append(_item("updates", "Actualizaciones de seguridad", "err" if n else "ok",
                               f"{n} aviso(s) de seguridad pendientes.", "Aplica las actualizaciones desde Paquetes.",
                               link="packages"))
        code, out, _ = run(["systemctl", "is-enabled", "dnf-automatic-install.timer", "dnf-automatic.timer"], timeout=10)
        items.append(_item("unattended", "Actualizaciones automáticas", "ok" if "enabled" in out else "info",
                           "dnf-automatic " + ("activado" if "enabled" in out else "no activado") + ".",
                           "Instala dnf-automatic y activa dnf-automatic-install.timer."))
    return items


def _reboot_check():
    req = os.path.exists(REBOOT_REQ)
    pkgs = []
    if req:
        try:
            pkgs = sorted({l.strip() for l in (_read(REBOOT_PKGS) or "").splitlines() if l.strip()})
        except HardeningError:
            pass
    else:
        code, _, _ = run(["needs-restarting", "-r"], timeout=30)  # RHEL: 1 = hace falta reiniciar
        req = code == 1
    return _item("reboot", "Reinicio pendiente", "warn" if req else "ok",
                 ("Hace falta reiniciar" + (" por: " + ", ".join(pkgs[:10]) + ("…" if len(pkgs) > 10 else "") if pkgs else "") + ".")
                 if req else "No hace falta reiniciar.",
                 "Las actualizaciones del kernel o de bibliotecas básicas solo protegen después de reiniciar. Hazlo en una ventana de mantenimiento.")


# ── Puertos ────────────────────────────────────────────────────────────────

def _ports():
    code, out, _ = run(["ss", "-tulpnH"], timeout=10)
    rows = []
    for line in out.splitlines() if code == 0 else []:
        p = line.split()
        if len(p) < 5 or p[0] not in ("tcp", "udp"):
            continue
        if p[0] == "tcp" and p[1] != "LISTEN":
            continue
        addr, _, port = p[4].rpartition(":")
        host = addr.strip("[]").split("%")[0]
        if host in ("*", "0.0.0.0", "::"):
            scope = "all"
        else:
            try:
                scope = "local" if ipaddress.ip_address(host).is_loopback else "ip"
            except ValueError:
                scope = "ip"
        procs = sorted(set(re.findall(r'\("([^"]+)",pid=\d+', " ".join(p[5:]))))
        rows.append({"proto": p[0], "addr": addr, "port": int(port) if port.isdigit() else 0,
                     "scope": scope, "process": ", ".join(procs)})
    uniq = {(r["proto"], r["addr"], r["port"]): r for r in rows}
    return sorted(uniq.values(), key=lambda r: (r["port"], r["proto"], r["addr"]))


# ── fail2ban ───────────────────────────────────────────────────────────────

def _f2b_fields(text):
    """'|- Currently banned:\t1' → {'currently banned': '1'}."""
    res = {}
    for line in text.splitlines():
        k, sep, v = line.lstrip(" |`-").partition(":")
        if sep:
            res[k.strip().lower()] = v.strip()
    return res


def _f2b_jails():
    code, out, _ = run(["fail2ban-client", "status"], timeout=15)
    if code != 0:
        return None
    jl = _f2b_fields(out).get("jail list", "")
    return [j.strip() for j in jl.split(",") if JAIL_RE.fullmatch(j.strip())]


def _f2b_status():
    code, out, _ = run(["fail2ban-client", "--version"], timeout=10)
    res = {"installed": code != 127, "running": False, "jails": [], "managed": None,
           "can_install": detect()["pkg"] == "apt"}
    if not res["installed"]:
        return res
    jails = _f2b_jails()
    res["running"] = jails is not None
    for j in jails or []:
        code, out, _ = run(["fail2ban-client", "status", j], timeout=15)
        f = _f2b_fields(out) if code == 0 else {}
        banned = []
        for ip in f.get("banned ip list", "").split():
            try:
                banned.append(str(ipaddress.ip_address(ip)))
            except ValueError:
                pass
        res["jails"].append({"name": j, "failed": f.get("currently failed", "0"), "total_failed": f.get("total failed", "0"),
                             "banned_count": f.get("currently banned", "0"), "total_banned": f.get("total banned", "0"),
                             "banned": banned})
    try:
        txt = _read(F2B_JAIL)
    except HardeningError:
        txt = None
    if txt:
        vals = dict(re.findall(r"^(bantime|findtime|maxretry|ignoreip)\s*=\s*(.*)$", txt, re.M))
        res["managed"] = vals
    return res


def _f2b_check(f2b):
    if not f2b["installed"]:
        return _item("fail2ban", "fail2ban", "warn", "fail2ban no está instalado.",
                     "fail2ban bloquea durante un tiempo las IPs que fallan repetidamente al entrar por SSH.",
                     fix="f2b-install" if f2b["can_install"] else None)
    if not f2b["running"]:
        return _item("fail2ban", "fail2ban", "warn", "fail2ban está instalado pero no está en marcha.",
                     "Activa la protección de SSH para arrancarlo con una configuración válida.", fix="f2b-jail")
    names = [j["name"] for j in f2b["jails"]]
    if "sshd" not in names:
        return _item("fail2ban", "fail2ban", "warn", f"En marcha, pero sin protección de SSH (jails: {', '.join(names) or 'ninguna'}).",
                     "Activa la jail sshd.", fix="f2b-jail")
    banned = sum(len(j["banned"]) for j in f2b["jails"])
    return _item("fail2ban", "fail2ban", "ok", f"En marcha con {len(names)} jail(s); {banned} IP(s) bloqueadas ahora.",
                 "Revisa abajo las IPs bloqueadas.", fix="f2b-jail")


def _need_f2b_running():
    jails = _f2b_jails()
    if jails is None:
        raise HardeningError("fail2ban no está en marcha", 409)
    return jails


def _jail_ip(data):
    jail, ip = str(data.get("jail", "")), str(data.get("ip", "")).strip()
    if not JAIL_RE.fullmatch(jail):
        raise HardeningError("Nombre de jail no válido")
    try:
        ip = str(ipaddress.ip_address(ip))
    except ValueError:
        raise HardeningError("Dirección IP no válida")
    if jail not in _need_f2b_running():
        raise HardeningError("Esa jail no existe", 404)
    return jail, ip


@hardening_bp.route("/api/f2b/install", methods=["POST"])
@login_required
def f2b_install():
    if detect()["pkg"] != "apt":
        raise HardeningError("La instalación automática solo está disponible en Debian/Ubuntu", 501)
    ok, out, msg = apt_get("install", "fail2ban", timeout=900)
    if not ok:
        raise HardeningError(msg or "apt-get falló", 500)
    return jsonify({"ok": True})


@hardening_bp.route("/api/f2b/jail", methods=["POST"])
@login_required
def f2b_jail():
    """Escribe la jail sshd del panel, la valida con `fail2ban-client -t` y recarga."""
    data = request.get_json(silent=True) or {}
    vals = {}
    for k in ("bantime", "findtime"):
        v = str(data.get(k, "")).strip()
        if not F2B_TIME_RE.fullmatch(v) or (k == "findtime" and v == "-1") or v in ("0", "0s", "0m"):
            raise HardeningError(f"Valor no válido para {k} (ej.: 600, 10m, 1h, 1d)")
        vals[k] = v
    mr = str(data.get("maxretry", "")).strip()
    if not mr.isdigit() or not 1 <= int(mr) <= 100:
        raise HardeningError("maxretry debe estar entre 1 y 100")
    vals["maxretry"] = str(int(mr))
    ignore = []
    for tok in str(data.get("ignoreip", "")).replace(",", " ").split()[:50]:
        try:
            ignore.append(str(ipaddress.ip_network(tok, strict=False)) if "/" in tok else str(ipaddress.ip_address(tok)))
        except ValueError:
            raise HardeningError(f"IP o red no válida en «no bloquear nunca»: {tok}")
    code, _, _ = run(["fail2ban-client", "--version"], timeout=10)
    if code == 127:
        raise HardeningError("fail2ban no está instalado", 404)
    with _lock:
        cfg = _sshd_effective() or {}
        ports = ",".join(cfg.get("port") or ["ssh"])
        text = (HEADER + "[sshd]\nenabled = true\n"
                f"port = {ports}\n"
                + "".join(f"{k} = {vals[k]}\n" for k in ("bantime", "findtime", "maxretry"))
                + f"ignoreip = {' '.join(['127.0.0.1/8', '::1', *ignore])}\n"
                + ("" if any(os.path.exists(p) for p in AUTH_LOGS) else "backend = systemd\n"))
        old = _read(F2B_JAIL)
        _write(F2B_JAIL, text)
        code, out, err = run(["fail2ban-client", "-t"], timeout=60)
        if code != 0:
            _restore(F2B_JAIL, old)
            raise HardeningError("fail2ban-client -t rechazó la configuración; se restauró la anterior:\n"
                                 + _tail(code, out, err, "error"), 422)
        running = _f2b_jails() is not None
        cmd = ["fail2ban-client", "reload"] if running else ["systemctl", "enable", "--now", "fail2ban"]
        code, out, err = run(cmd, timeout=60)
        if code != 0:
            _restore(F2B_JAIL, old)
            if running:
                run(["fail2ban-client", "reload"], timeout=60)
            raise HardeningError("fail2ban no pudo aplicar la configuración; se restauró la anterior:\n"
                                 + _tail(code, out, err, "error"), 500)
    return jsonify({"ok": True})


@hardening_bp.route("/api/f2b/unban", methods=["POST"])
@login_required
def f2b_unban():
    jail, ip = _jail_ip(request.get_json(silent=True) or {})
    code, out, err = run(["fail2ban-client", "--", "set", jail, "unbanip", ip], timeout=20)
    if code != 0:
        raise HardeningError(_tail(code, out, err, "fail2ban-client falló"), 500)
    return jsonify({"ok": True})


@hardening_bp.route("/api/f2b/ban", methods=["POST"])
@login_required
def f2b_ban():
    jail, ip = _jail_ip(request.get_json(silent=True) or {})
    me = request.remote_addr or ""
    try:
        if ipaddress.ip_address(ip) == ipaddress.ip_address(me.split("%")[0]):
            raise HardeningError("Esa es tu propia IP: te quedarías fuera del panel")
    except ValueError:
        pass
    if ipaddress.ip_address(ip).is_loopback:
        raise HardeningError("No se puede bloquear la dirección local")
    code, out, err = run(["fail2ban-client", "--", "set", jail, "banip", ip], timeout=20)
    if code != 0:
        raise HardeningError(_tail(code, out, err, "fail2ban-client falló"), 500)
    return jsonify({"ok": True})


# ── Actualizaciones automáticas ─────────────────────────────────────────────

@hardening_bp.route("/api/unattended", methods=["POST"])
@login_required
def unattended_fix():
    if detect()["pkg"] != "apt":
        raise HardeningError("Solo disponible en Debian/Ubuntu", 501)
    if not os.path.exists(UU_BIN):
        ok, out, msg = apt_get("install", "unattended-upgrades", timeout=900)
        if not ok:
            raise HardeningError(msg or "apt-get falló", 500)
    with _lock:
        old = _read(AUTO_UPGRADES)
        _write(AUTO_UPGRADES, 'APT::Periodic::Update-Package-Lists "1";\n'
                              'APT::Periodic::Unattended-Upgrade "1";\n')
        code, out, err = run(["apt-config", "dump"], timeout=20)
        if code != 0:
            _restore(AUTO_UPGRADES, old)
            raise HardeningError("apt-config rechazó la configuración; se restauró la anterior:\n"
                                 + _tail(code, out, err, "error"), 422)
    # ponytail: no se tocan apt-daily*.timer; vienen activos por defecto en Debian/Ubuntu.
    return jsonify({"ok": True})


# ── Vista y estado ─────────────────────────────────────────────────────────

@hardening_bp.route("/")
@login_required
def index():
    return render_template("hardening.html", client_ip=request.remote_addr or "")


@hardening_bp.route("/api/status")
@login_required
def status():
    accounts = _accounts()
    try:
        cfg = _sshd_effective()
        checks = _ssh_checks(cfg, accounts)
    except HardeningError as e:  # p. ej. sshd_config con errores: el resto del análisis sigue
        cfg, checks = None, [_item("ssh", "Servidor SSH", "err", str(e), "Revisa /etc/ssh/sshd_config con «sshd -t» en la terminal.")]
    f2b = _f2b_status()
    ports = _ports()
    checks.append(_firewall_check())
    checks += _updates_checks()
    checks.append(_reboot_check())

    exposed = sorted({f"{p['port']}/{p['proto']}" for p in ports if p["scope"] != "local"},
                     key=lambda s: (int(s.split("/")[0]), s))
    checks.append(_item("ports", "Puertos abiertos a la red", "info",
                        f"{len(exposed)} puerto(s) escuchando fuera de localhost"
                        + (": " + ", ".join(exposed[:20]) + ("…" if len(exposed) > 20 else "") if exposed else "."),
                        "Revisa la tabla de puertos: cada servicio que no necesite la red debería escuchar solo en 127.0.0.1 o estar filtrado por el firewall."))

    uid0 = [a["user"] for a in accounts if a["uid"] == 0 and a["user"] != "root"]
    checks.append(_item("uid0", "Otras cuentas con UID 0", "err" if uid0 else "ok",
                        ("Tienen poderes de root: " + ", ".join(uid0)) if uid0 else "Solo root tiene UID 0.",
                        "Una cuenta con UID 0 distinta de root suele ser una puerta trasera. Elimínala o cámbiale el UID."))
    empty = [a["user"] for a in accounts if a["password"] == "empty"]
    checks.append(_item("emptypw", "Cuentas sin contraseña", "err" if empty else "ok",
                        ("Contraseña vacía: " + ", ".join(empty)) if empty else "Ninguna cuenta tiene la contraseña vacía.",
                        "Asigna una contraseña o bloquea la cuenta (passwd -l) desde Usuarios.",
                        link="users"))
    admins = sorted(a["user"] for a in accounts if a["admin"] and a["uid"] != 0)
    checks.append(_item("sudo", "Administradores (sudo/wheel)", "info",
                        ", ".join(admins) if admins else "Ningún usuario en sudo/wheel.",
                        "Revisa que solo estén las personas que deben administrar el servidor."))
    checks.append(_f2b_check(f2b))

    scored = [c for c in checks if c["status"] != "info"]
    points = sum({"ok": 1, "warn": 0.5}.get(c["status"], 0) for c in scored)
    score = round(100 * points / len(scored)) if scored else 100
    counts = {s: sum(c["status"] == s for c in checks) for s in ("ok", "warn", "err", "info")}
    ssh = None
    if cfg is not None:
        ssh = {"PermitRootLogin": cfg.get("permitrootlogin", ""), "PasswordAuthentication": cfg.get("passwordauthentication", ""),
               "Port": (cfg.get("port") or ["22"])[0], "MaxAuthTries": cfg.get("maxauthtries", ""),
               "X11Forwarding": cfg.get("x11forwarding", ""),
               "admins_with_keys": [a["user"] for a in accounts if a["keys"] and (a["admin"] or a["uid"] == 0)]}
    return jsonify({"ok": True, "score": score, "counts": counts, "checks": checks, "ports": ports,
                    "accounts": accounts, "fail2ban": f2b, "ssh": ssh, "client_ip": request.remote_addr or ""})
