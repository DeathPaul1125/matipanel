"""Permisos del panel por usuario.

Roles:
  admin     todo, incluida esta pantalla de permisos.
  readonly  ve todo pero no cambia nada; sin terminal, archivos, explorador de BD ni descargas de datos
            (equivalen a leer cualquier archivo del servidor como root).
  custom    acceso completo solo a los módulos elegidos.
  none      no puede entrar.
Sin configuración (o para usuarios no listados) se aplica el rol por defecto, «admin» al instalar: así nada
cambia hasta que un administrador decide restringir.

El control es central (before_request): un módulo nuevo queda cubierto sin tocar su código.
"""
import json
import os
import re
from pathlib import Path

from flask import has_request_context, jsonify, redirect, request, url_for, flash

CONFIG_PATH = Path(os.environ.get("MATIPANEL_ROLES", "/var/lib/matipanel/roles.json"))
ROLES = {"admin": "Administrador", "readonly": "Solo lectura", "custom": "Personalizado", "none": "Sin acceso"}
USER_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]{0,63}")
# Siempre permitidos a quien entra: su propia cuenta (2FA), apariencia, ayuda, dashboard y la salida en vivo
ALWAYS = {"auth", "appearance", "help", "dashboard", "jobs"}
ADMIN_ONLY = {"roles"}
READONLY_BLOCKED = {"terminal", "files", "dbexplorer"}
READONLY_BLOCKED_EP = {"databases.dump", "traffic.capture_download", "traffic.capture_summary", "deploy.env",
                       "deploy.webhook_secret"}
# Rutas de WebSocket (no tienen blueprint): primer segmento de la URL → módulo
WS_MODULES = {"terminal": "terminal", "logs": "logs"}


def load():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    users = {}
    for name, r in (data.get("users") or {}).items():
        if USER_RE.fullmatch(str(name)) and isinstance(r, dict) and r.get("role") in ROLES:
            mods = [m for m in r.get("modules") or [] if isinstance(m, str)]
            users[name] = {"role": r["role"], "modules": mods}
    default = data.get("default") if data.get("default") in ROLES else "admin"
    return {"default": default, "users": users}


def save(cfg):
    CONFIG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def role_of(user, cfg=None):
    cfg = cfg or load()
    return cfg["users"].get(user) or {"role": cfg["default"], "modules": []}


def module_of(endpoint, path=""):
    """Módulo (blueprint) de una ruta; las rutas de WebSocket se resuelven por la URL."""
    if endpoint and "." in endpoint:
        return endpoint.split(".", 1)[0]
    first = path.strip("/").split("/", 1)[0]
    return WS_MODULES.get(first, "")


def allowed(user, endpoint, method="GET", path="", cfg=None):
    """¿Puede `user` usar esta ruta? → (sí/no, motivo)."""
    r = role_of(user, cfg)
    role, mod = r["role"], module_of(endpoint, path)
    if role == "admin":
        return True, ""
    if role == "none":
        return False, "Tu usuario no tiene acceso al panel"
    if mod in ADMIN_ONLY:
        return False, "Solo un administrador puede cambiar los permisos"
    if mod in ALWAYS or endpoint == "static":
        return True, ""
    if role == "readonly":
        if method not in ("GET", "HEAD", "OPTIONS"):
            return False, "Tu usuario es de solo lectura"
        if mod in READONLY_BLOCKED or endpoint in READONLY_BLOCKED_EP:
            return False, "Tu usuario es de solo lectura: este módulo da acceso a datos del servidor"
        return True, ""
    if mod in r["modules"]:
        return True, ""
    return False, "Tu usuario no tiene permiso para este módulo"


def can_login(user):
    return role_of(user)["role"] != "none"


def validate(data, modules, current_user_name):
    """Datos del formulario → (config, error). Siempre debe quedar al menos un administrador."""
    default = data.get("default")
    if default not in ROLES:
        return None, "Rol por defecto inválido"
    users = {}
    for name, r in (data.get("users") or {}).items():
        if not USER_RE.fullmatch(str(name)) or not isinstance(r, dict) or r.get("role") not in ROLES:
            return None, f"Datos inválidos para «{str(name)[:30]}»"
        mods = r.get("modules") or []
        if not isinstance(mods, list) or not all(m in modules for m in mods):
            return None, f"Módulos inválidos para «{name}»"
        if r["role"] == "custom" and not mods:
            return None, f"Elige al menos un módulo para «{name}» o cambia su rol"
        users[name] = {"role": r["role"], "modules": sorted(set(mods)) if r["role"] == "custom" else []}
    cfg = {"default": default, "users": users}
    if role_of(current_user_name, cfg)["role"] != "admin":
        return None, "No puedes quitarte el rol de administrador a ti mismo (pídeselo a otro administrador)"
    return cfg, ""


def init_app(app):
    @app.before_request
    def _check_permissions():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return None
        ok, why = allowed(current_user.get_id(), request.endpoint, request.method, request.path)
        if ok:
            return None
        if request.method == "GET" and "/api/" not in request.path and not request.path.endswith("/ws") \
                and "/ws/" not in request.path:
            flash(why, "error")
            return redirect(url_for("dashboard.index"))
        return jsonify({"ok": False, "error": why}), 403

    @app.context_processor
    def _perms():
        def can_see(endpoint):
            from flask_login import current_user
            if not has_request_context() or not current_user.is_authenticated:
                return False
            return allowed(current_user.get_id(), endpoint, "GET")[0]
        return {"can_see": can_see}


if __name__ == "__main__":
    import tempfile
    CONFIG_PATH = Path(tempfile.mkdtemp()) / "r.json"
    cfg = {"default": "readonly", "users": {"ana": {"role": "admin", "modules": []},
                                           "luis": {"role": "custom", "modules": ["deploy", "docker"]},
                                           "eva": {"role": "none", "modules": []}}}
    a = lambda u, ep, m="GET", p="": allowed(u, ep, m, p, cfg)[0]  # noqa: E731
    assert a("ana", "terminal.index") and a("ana", "roles.index", "POST")
    assert a("pepe", "services.index") and not a("pepe", "services.action", "POST")       # por defecto: solo lectura
    assert not a("pepe", "files.index") and not a("pepe", "databases.dump") and not a("pepe", "ws_handler", "GET", "/terminal/ws")
    assert a("pepe", "appearance.save", "POST") and a("pepe", "auth.logout", "POST") and not a("pepe", "roles.index")
    assert a("luis", "deploy.deploy_now", "POST") and a("luis", "docker.index") and not a("luis", "services.index")
    assert a("luis", "dashboard.index") and not a("luis", "ws_handler", "GET", "/terminal/ws")
    assert not a("eva", "dashboard.index")
    mods = {"deploy", "docker", "services"}
    assert validate({"default": "admin", "users": {}}, mods, "ana")[0]
    assert "a ti mismo" in validate({"default": "readonly", "users": {"ana": {"role": "readonly"}}}, mods, "ana")[1]
    assert validate({"default": "none", "users": {"x": {"role": "custom", "modules": ["nope"]}}}, mods, "ana")[1]
    assert validate({"default": "admin", "users": {"x;y": {"role": "admin"}}}, mods, "ana")[1]
    print("ok")
