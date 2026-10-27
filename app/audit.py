"""Registro de auditoría: quién hizo qué, cuándo y desde dónde.

Se registra solo cualquier petición que cambia algo (POST/PUT/PATCH/DELETE), las descargas sensibles
(archivos, volcados, capturas) y los eventos que se anotan a mano (inicio de sesión, terminal).
Los parámetros se guardan resumidos y sin secretos: se descartan contraseñas, claves, tokens y
contenidos (archivos, .env), y cada valor se recorta.

ponytail: SQLite propio con una tabla; se poda a 365 días.
"""
import json
import os
import re
import sqlite3
import threading
import time

from flask import has_request_context, request

RETENTION = 365 * 24 * 3600
SECRET_RE = re.compile(r"pass|secret|token|key|content|text|data|pairs|codes?$|otp|salt|cert|pem|value|sql|script|expr|body",
                       re.I)
MAX_FIELDS, MAX_VALUE = 12, 120
# GET que también se registran: descargan información sensible
AUDITED_GETS = {"files.download", "files.raw", "traffic.capture_download", "dbexplorer.export", "databases.dump",
                "deploy.deploy_log", "deploy.env"}
SKIP = {"static", "jobs.poll", "appearance.save_prefs"}
MODULES = {
    "auth": "Acceso", "dashboard": "Dashboard", "system": "Sistema", "processes": "Procesos", "services": "Servicios",
    "cron": "Tareas", "disk": "Discos", "backups": "Respaldos", "users": "Usuarios", "network": "Red",
    "traffic": "Tráfico de red", "firewall": "Firewall", "webserver": "Servidor web", "ssl": "SSL", "php": "PHP",
    "databases": "Bases de datos", "docker": "Docker", "files": "Archivos", "packages": "Paquetes", "logs": "Logs",
    "terminal": "Terminal", "appearance": "Apariencia", "help": "Ayuda", "remote": "Multi-servidor", "mail": "Correo",
    "dns": "DNS", "shares": "Compartidos", "storage": "Almacenamiento", "dbexplorer": "Explorador BD",
    "deploy": "Despliegues", "audit": "Auditoría", "notify": "Notificaciones", "appbackup": "Respaldos de apps",
}

_lock = threading.Lock()
_db = None


def _conn():
    global _db
    if _db is None:
        path = os.environ.get("MATIPANEL_AUDIT", "/var/lib/matipanel/audit.db")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            if path != ":memory:":
                os.chmod(path, 0o600)
        except (OSError, sqlite3.Error):
            conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, t REAL, user TEXT, ip TEXT, "
                     "module TEXT, action TEXT, target TEXT, ok INTEGER, status INTEGER, detail TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS events_t ON events (t)")
        _db = conn
    return _db


def _clip(v):
    s = " ".join(str(v).split())
    return s if len(s) <= MAX_VALUE else s[:MAX_VALUE - 1] + "…"


def sanitize(params):
    """dict de parámetros → dict corto sin secretos (las claves sensibles quedan como «•••»)."""
    out = {}
    for k, v in list((params or {}).items())[:MAX_FIELDS]:
        k = str(k)[:40]
        if SECRET_RE.search(k):
            out[k] = "•••"
        elif isinstance(v, (dict, list)):
            out[k] = _clip(json.dumps(v, ensure_ascii=False))[:MAX_VALUE] if not any(
                SECRET_RE.search(str(x)) for x in (v.keys() if isinstance(v, dict) else [])) else "•••"
        else:
            out[k] = _clip(v)
    return out


def log(module, action, target="", ok=True, status=0, detail=None, user=None, ip=None):
    """Anota un evento. Nunca lanza: la auditoría no debe romper la acción que registra."""
    try:
        if has_request_context():
            if user is None:
                from flask import session
                from flask_login import current_user
                user = current_user.get_id() if current_user.is_authenticated else (session.get("pending_2fa_user") or "")
            ip = ip or request.remote_addr or ""
        with _lock:
            _conn().execute("INSERT INTO events (t, user, ip, module, action, target, ok, status, detail) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [time.time(), user or "", ip or "", module, _clip(action), _clip(target),
                             1 if ok else 0, int(status or 0),
                             json.dumps(detail, ensure_ascii=False) if detail else ""])
    except Exception:
        pass


def prune(now=None):
    with _lock:
        _conn().execute("DELETE FROM events WHERE t < ?", [(now or time.time()) - RETENTION])


def query(since=0.0, user="", module="", result="", q="", limit=2000):
    sql, args = "SELECT t, user, ip, module, action, target, ok, status, detail FROM events WHERE t >= ?", [since]
    if user:
        sql += " AND user = ?"; args.append(user)
    if module:
        sql += " AND module = ?"; args.append(module)
    if result in ("ok", "fail"):
        sql += " AND ok = ?"; args.append(1 if result == "ok" else 0)
    if q:
        sql += " AND (action LIKE ? OR target LIKE ? OR detail LIKE ? OR ip LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY t DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = _conn().execute(sql, args).fetchall()
    return [{"t": r[0], "user": r[1], "ip": r[2], "module": r[3], "module_label": MODULES.get(r[3], r[3]),
             "action": r[4], "target": r[5], "ok": bool(r[6]), "status": r[7],
             "detail": json.loads(r[8]) if r[8] else {}} for r in rows]


def users_and_modules():
    with _lock:
        c = _conn()
        users = [r[0] for r in c.execute("SELECT DISTINCT user FROM events WHERE user != '' ORDER BY user")]
        mods = [r[0] for r in c.execute("SELECT DISTINCT module FROM events ORDER BY module")]
    return users, [{"key": m, "label": MODULES.get(m, m)} for m in mods]


def _request_params():
    params = dict(request.view_args or {})
    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            params.update(body)
    elif request.form:
        params.update({k: v for k, v in request.form.items()})
    if request.files:
        params["archivos"] = ", ".join(f.filename or "?" for f in request.files.getlist(next(iter(request.files))))[:200]
    if request.method == "GET":
        params.update({k: v for k, v in request.args.items()})
    return params


def init_app(app):
    @app.after_request
    def _audit(resp):
        ep = request.endpoint or ""
        if ep in SKIP or ep.startswith("static"):
            return resp
        audited = request.method in ("POST", "PUT", "PATCH", "DELETE") or ep in AUDITED_GETS
        if not audited:
            return resp
        module, _, func = ep.partition(".")
        ok = resp.status_code < 400
        if ok and resp.is_json:
            body = resp.get_json(silent=True)
            if isinstance(body, dict) and body.get("ok") is False:
                ok = False
        user = None
        if ep in ("auth.login", "auth.login_2fa"):
            # 302 = entró (o pasó al 2.º factor). En /login el usuario aún no está en la sesión.
            if ep == "auth.login":
                user = (request.form.get("username") or "").strip()[:64] or None
            ok = resp.status_code == 302
        action = request.method + " " + (func or ep) if request.method != "POST" else (func or ep)
        params = _request_params()
        target = next((str(params[k]) for k in ("name", "unit", "path", "container", "iface", "db", "user", "zone",
                                                 "app", "key", "service", "pkg", "package") if params.get(k)), "")
        log(module or "panel", action.replace("_", " "), target, ok, resp.status_code, sanitize(params), user=user)
        return resp


def selfcheck():
    s = sanitize({"password": "x", "unit": "nginx", "text": "APP_KEY=1", "ifaces": {"eth0": {"mbps": 5}},
                  "long": "a" * 500, "secret_key": "k"})
    assert s["password"] == "•••" and s["text"] == "•••" and s["secret_key"] == "•••" and s["unit"] == "nginx"
    assert len(s["long"]) == MAX_VALUE and s["ifaces"].startswith('{"eth0"')
    assert sanitize({"pairs": [["A", "B"]]})["pairs"] == "•••"
    return True


if __name__ == "__main__":
    os.environ["MATIPANEL_AUDIT"] = ":memory:"
    selfcheck()
    log("services", "action", "nginx", True, 200, {"unit": "nginx"}, user="ana", ip="10.0.0.1")
    log("auth", "login", "", False, 401, {}, user="pedro", ip="10.0.0.2")
    assert [e["user"] for e in query()] == ["pedro", "ana"] and query(result="fail")[0]["module_label"] == "Acceso"
    assert query(q="nginx")[0]["detail"] == {"unit": "nginx"} and users_and_modules()[0] == ["ana", "pedro"]
    print("ok")
