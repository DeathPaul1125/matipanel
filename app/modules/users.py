"""Usuarios y grupos del sistema: listado y gestión (useradd, usermod, gpasswd…)."""
import os
import posixpath
import pwd
import grp
import re
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from ..auth import ALLOWED_GROUPS
from ..util import run
from .. import sshkeys

users_bp = Blueprint("users", __name__)

# Nombres nuevos: estrictos. Existentes: algo más laxos (p. ej. Debian-exim, cuentas samba$).
NEW_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\Z")  # \Z: "$" aceptaría un salto de línea final
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}\$?\Z")
EXTRA_SHELLS = ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false")
# "userdel -r" nunca sobre estas carpetas (cuentas de sistema con home en /, /var/www, /bin…)
SYSTEM_HOMES = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/opt", "/proc",
                "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var", "/var/www",
                "/var/lib", "/var/spool", "/var/mail", "/var/backups", "/var/cache", "/var/log",
                "/usr/sbin", "/usr/bin"}


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _cmd(cmd, **kw):
    code, out, err = run(cmd, timeout=60, **kw)
    if code != 0:
        return _err((err or out).strip() or f"{cmd[0]} falló (código {code})", 500)
    return jsonify({"ok": True})


def _user(name):
    """pwd entry de un usuario existente o None."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def _group(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    try:
        return grp.getgrnam(name)
    except KeyError:
        return None


def _shells():
    shells = []
    try:
        with open("/etc/shells") as f:
            shells = [l.strip() for l in f if l.strip().startswith("/")]
    except OSError:
        pass
    shells += [s for s in EXTRA_SHELLS if os.path.exists(s)]
    return list(dict.fromkeys(shells))


def _valid_password(pw):
    return isinstance(pw, str) and 0 < len(pw) <= 512 and "\n" not in pw and "\r" not in pw


def _set_password(user, pw):
    return run(["chpasswd"], timeout=30, input=f"{user}:{pw}\n")


def _user_groups(name):
    """Grupos suplementarios de un usuario."""
    return sorted(g.gr_name for g in grp.getgrall() if name in g.gr_mem)


def _keeps_panel_access(user, groups_after):
    """Evita que el usuario logueado se quite a sí mismo el acceso al panel."""
    if user.pw_name != current_user.username:
        return True
    try:
        primary = grp.getgrgid(user.pw_gid).gr_name
    except KeyError:
        primary = ""
    return any(g in ALLOWED_GROUPS for g in [primary, *groups_after])


def _parse_groups(value):
    """'a, b,c' o lista -> lista de grupos existentes, o None si alguno es inválido."""
    items = value if isinstance(value, list) else str(value or "").split(",")
    names = [str(g).strip() for g in items if str(g).strip()]
    if any(_group(g) is None for g in names):
        return None
    return list(dict.fromkeys(names))


def _shadow():
    """{usuario: campo de contraseña} desde /etc/shadow (requiere root)."""
    out = {}
    try:
        with open("/etc/shadow") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) > 1:
                    out[parts[0]] = parts[1]
    except OSError:
        pass
    return out


@users_bp.route("/")
@login_required
def index():
    return render_template("users.html")


@users_bp.route("/api/users")
@login_required
def list_users():
    shadow = _shadow()
    members = {}
    for g in grp.getgrall():
        for m in g.gr_mem:
            members.setdefault(m, []).append(g.gr_name)
    users = []
    for u in pwd.getpwall():
        pw = shadow.get(u.pw_name)
        try:
            primary = grp.getgrgid(u.pw_gid).gr_name
        except KeyError:
            primary = str(u.pw_gid)
        users.append({
            "name": u.pw_name,
            "uid": u.pw_uid,
            "gid": u.pw_gid,
            "home": u.pw_dir,
            "shell": u.pw_shell,
            "gecos": u.pw_gecos,
            "type": "sistema" if u.pw_uid < 1000 else "regular",
            "primary_group": primary,
            "groups": sorted(members.get(u.pw_name, [])),
            # None = desconocido (sin acceso a /etc/shadow)
            "locked": None if pw is None else pw.startswith("!"),
            "has_password": None if pw is None else pw.lstrip("!") not in ("", "*", "!"),
        })
    users.sort(key=lambda x: x["uid"])
    return jsonify({"users": users, "me": current_user.username})


@users_bp.route("/api/groups")
@login_required
def list_groups():
    groups = []
    for g in grp.getgrall():
        groups.append({
            "name": g.gr_name,
            "gid": g.gr_gid,
            "members": g.gr_mem,
        })
    groups.sort(key=lambda x: x["gid"])
    return jsonify({"groups": groups})


@users_bp.route("/api/shells")
@login_required
def list_shells():
    return jsonify({"shells": _shells()})


# ── Usuarios ──

@users_bp.route("/api/user/create", methods=["POST"])
@login_required
def create_user():
    data = request.get_json(silent=True) or {}
    name = data.get("username", "")
    if not isinstance(name, str) or not NEW_NAME_RE.match(name):
        return _err("Nombre de usuario inválido (minúsculas, dígitos, _ y -; máx. 32)")
    if _user(name):
        return _err("El usuario ya existe")
    gecos = re.sub(r"[:\x00-\x1f\x7f]", "", str(data.get("gecos", "")))[:200]
    shell = data.get("shell", "")
    if shell not in _shells():
        return _err("Shell no válida")
    groups = _parse_groups(data.get("groups", ""))
    if groups is None:
        return _err("Algún grupo no existe o es inválido")
    if data.get("sudo"):
        admin = next((g for g in ("sudo", "wheel") if _group(g)), None)
        if not admin:
            return _err("No existe el grupo sudo/wheel")
        groups.append(admin)
    pw = data.get("password") or ""
    if pw and not _valid_password(pw):
        return _err("Contraseña inválida (sin saltos de línea, máx. 512)")

    cmd = ["useradd", "-s", shell, "-c", gecos, "-m" if data.get("create_home", True) else "-M"]
    if groups:
        cmd += ["-G", ",".join(dict.fromkeys(groups))]
    code, out, err = run(cmd + ["--", name], timeout=60)
    if code != 0:
        return _err(err.strip() or "useradd falló", 500)
    if pw:
        code, out, err = _set_password(name, pw)
        if code != 0:
            return _err("Usuario creado, pero falló la contraseña: " + err.strip(), 500)
    return jsonify({"ok": True})


@users_bp.route("/api/user/delete", methods=["POST"])
@login_required
def delete_user():
    data = request.get_json(silent=True) or {}
    u = _user(data.get("username"))
    if not u:
        return _err("Usuario no encontrado", 404)
    if u.pw_uid == 0 or u.pw_name == current_user.username:
        return _err("No se puede eliminar root ni el usuario con sesión activa", 403)
    remove_home = bool(data.get("remove_home"))
    if remove_home:
        home = os.path.realpath(u.pw_dir or "/")
        if home in SYSTEM_HOMES or posixpath.normpath(u.pw_dir or "/") in SYSTEM_HOMES:
            return _err(f"El home de {u.pw_name} es {u.pw_dir}, una carpeta del sistema: "
                        "elimina el usuario sin borrar su home.", 403)
    cmd = ["userdel"] + (["-r"] if remove_home else [])
    return _cmd(cmd + ["--", u.pw_name])


@users_bp.route("/api/user/password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    u = _user(data.get("username"))
    if not u:
        return _err("Usuario no encontrado", 404)
    pw = data.get("password")
    if not _valid_password(pw):
        return _err("Contraseña inválida (vacía, con saltos de línea o demasiado larga)")
    code, out, err = _set_password(u.pw_name, pw)
    if code != 0:
        return _err(err.strip() or "chpasswd falló", 500)
    return jsonify({"ok": True})


@users_bp.route("/api/user/lock", methods=["POST"])
@login_required
def lock_user():
    data = request.get_json(silent=True) or {}
    u = _user(data.get("username"))
    if not u:
        return _err("Usuario no encontrado", 404)
    lock = bool(data.get("lock"))
    if lock and (u.pw_uid == 0 or u.pw_name == current_user.username):
        return _err("No se puede bloquear root ni el usuario con sesión activa", 403)
    return _cmd(["usermod", "-L" if lock else "-U", "--", u.pw_name])


@users_bp.route("/api/user/shell", methods=["POST"])
@login_required
def change_shell():
    data = request.get_json(silent=True) or {}
    u = _user(data.get("username"))
    if not u:
        return _err("Usuario no encontrado", 404)
    shell = data.get("shell", "")
    if shell not in _shells():
        return _err("Shell no válida")
    return _cmd(["usermod", "-s", shell, "--", u.pw_name])


@users_bp.route("/api/user/groups", methods=["POST"])
@login_required
def set_groups():
    """Reemplaza los grupos suplementarios (usermod -G)."""
    data = request.get_json(silent=True) or {}
    u = _user(data.get("username"))
    if not u:
        return _err("Usuario no encontrado", 404)
    groups = _parse_groups(data.get("groups", ""))
    if groups is None:
        return _err("Algún grupo no existe o es inválido")
    if not _keeps_panel_access(u, groups):
        return _err("Perderías el acceso al panel (" + ", ".join(ALLOWED_GROUPS) + ")", 403)
    return _cmd(["usermod", "-G", ",".join(groups), "--", u.pw_name])


# ── Grupos ──

@users_bp.route("/api/group/create", methods=["POST"])
@login_required
def create_group():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    if not isinstance(name, str) or not NEW_NAME_RE.match(name):
        return _err("Nombre de grupo inválido")
    if _group(name):
        return _err("El grupo ya existe")
    return _cmd(["groupadd", "--", name])


@users_bp.route("/api/group/delete", methods=["POST"])
@login_required
def delete_group():
    data = request.get_json(silent=True) or {}
    g = _group(data.get("name"))
    if not g:
        return _err("Grupo no encontrado", 404)
    if g.gr_gid < 1000 or g.gr_name in ALLOWED_GROUPS:
        return _err("No se pueden eliminar grupos del sistema ni de administración", 403)
    return _cmd(["groupdel", "--", g.gr_name])


@users_bp.route("/api/group/member", methods=["POST"])
@login_required
def group_member():
    data = request.get_json(silent=True) or {}
    g = _group(data.get("group"))
    u = _user(data.get("username"))
    if not g or not u:
        return _err("Grupo o usuario no encontrado", 404)
    act = data.get("action")
    if act not in ("add", "remove"):
        return _err("Acción no permitida")
    if act == "remove":
        after = [x for x in _user_groups(u.pw_name) if x != g.gr_name]
        if not _keeps_panel_access(u, after):
            return _err("Perderías el acceso al panel (" + ", ".join(ALLOWED_GROUPS) + ")", 403)
    return _cmd(["gpasswd", "-a" if act == "add" else "-d", u.pw_name, "--", g.gr_name])


# ── Claves SSH autorizadas ───────────────────────────────────────────────

@users_bp.route("/api/user/keys")
@login_required
def user_keys():
    pw = _user(request.args.get("user", ""))
    if not pw:
        return _err("Usuario inexistente", 404)
    try:
        _, keys = sshkeys.read(pw)
    except (sshkeys.KeyError_, OSError) as e:
        return _err(str(e), 409)
    return jsonify({"ok": True, "keys": keys, "home": pw.pw_dir})


@users_bp.route("/api/user/keys/add", methods=["POST"])
@login_required
def user_keys_add():
    data = request.get_json(silent=True) or {}
    pw = _user(data.get("user", ""))
    if not pw:
        return _err("Usuario inexistente", 404)
    try:
        k = sshkeys.add(pw, data.get("key", ""))
    except sshkeys.KeyError_ as e:
        return _err(str(e))
    except OSError as e:
        return _err(f"No se pudo escribir: {e.strerror or e}", 500)
    return jsonify({"ok": True, "key": k})


@users_bp.route("/api/user/keys/delete", methods=["POST"])
@login_required
def user_keys_delete():
    data = request.get_json(silent=True) or {}
    pw = _user(data.get("user", ""))
    line = data.get("line")
    if not pw or not isinstance(line, int) or line < 0:
        return _err("Datos inválidos")
    try:
        sshkeys.remove(pw, line, str(data.get("fingerprint", "")))
    except sshkeys.KeyError_ as e:
        return _err(str(e), 409)
    except OSError as e:
        return _err(f"No se pudo escribir: {e.strerror or e}", 500)
    return jsonify({"ok": True})
