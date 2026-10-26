"""Multi-servidor: vigilar otros MatiPanel desde este y darles acceso de solo lectura a este.

Modelo de confianza:
- Cada servidor emite tokens de SOLO LECTURA (guarda solo su SHA-256). Un token sirve para un
  único endpoint, /servidores/api/remote/summary, que devuelve métricas y nada más.
- El monitor fija ("pinea") la huella SHA-256 del certificado del otro panel (TOFU). La huella se
  comprueba DESPUÉS del handshake TLS y ANTES de enviar el token: si no coincide, el token nunca
  sale de este servidor.
"""
import http.client
import json
import platform
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor

import psutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from .. import remote_store as store
from ..util import RateLimiter, bytes_to_human
from ..distro import detect
from . import dashboard

remote_bp = Blueprint("remote", __name__)

REMOTE_PATH = "/servidores/api/remote/summary"  # mismo prefijo en todos los MatiPanel
TIMEOUT = 5          # s por conexión y por lectura
MAX_BODY = 1 << 20   # 1 MB: un resumen ocupa unos pocos KB
HISTORY_POINTS = 60  # 5 min a una muestra cada 5 s

limiter = RateLimiter(max_fails=10, window=300)
_summary_cache = {"at": 0.0, "data": None}


# ── Datos de este servidor ──

def _summary():
    """Resumen de este servidor. Caché de 5 s: varios monitores pueden preguntar a la vez."""
    now = time.time()
    if _summary_cache["data"] and now - _summary_cache["at"] < 5:
        return _summary_cache["data"]
    from .. import __version__
    mem, swap, disk = psutil.virtual_memory(), psutil.swap_memory(), psutil.disk_usage("/")
    hist = list(dashboard._history)[-HISTORY_POINTS:]
    failed = dashboard._failed_units()
    data = {
        "hostname": socket.gethostname(),
        "distro": detect()["name"],
        "kernel": platform.release(),
        "uptime": dashboard._uptime_str(),
        "uptime_s": int(now - psutil.boot_time()),
        "cpu": hist[-1]["cpu"] if hist else 0.0,
        "mem": mem.percent,
        "mem_h": f"{bytes_to_human(mem.total - mem.available)} de {bytes_to_human(mem.total)}",
        "swap": swap.percent,
        "swap_h": f"{bytes_to_human(swap.used)} de {bytes_to_human(swap.total)}",
        "disk": disk.percent,
        "disk_h": f"{bytes_to_human(disk.used)} de {bytes_to_human(disk.total)}",
        "load": [round(x, 2) for x in psutil.getloadavg()],
        "cores": psutil.cpu_count(logical=True),
        "failed": len(failed),
        "failed_units": failed[:20],
        "updates": dashboard._pending_updates(),
        "version": __version__,
        "ts": round(now, 1),
        "history": [{"t": p["t"], "cpu": p["cpu"], "mem": p["mem"]} for p in hist],
    }
    _summary_cache.update(at=now, data=data)
    return data


# ── API remota (la consultan OTROS paneles; sin sesión, solo token) ──

@remote_bp.route("/api/remote/summary")
def remote_summary():
    ip = request.remote_addr or "?"
    if limiter.blocked(ip):
        return _no_store(jsonify({"ok": False, "error": "Demasiados intentos fallidos."}), 429)
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not store.verify_token(token, ip):
        limiter.fail(ip)
        resp = _no_store(jsonify({"ok": False, "error": "Token inválido o revocado."}), 401)
        resp.headers["WWW-Authenticate"] = 'Bearer realm="matipanel"'
        return resp
    limiter.reset(ip)
    return _no_store(jsonify({"ok": True, **_summary()}))


def _no_store(resp, code=200):
    resp.status_code = code
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── Conexión a otros servidores ──

def _tls_context():
    # El certificado es autofirmado: no hay CA que lo valide. La seguridad la da la huella fijada,
    # que se compara a mano tras el handshake.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _peer_fingerprint(url):
    """Huella del certificado que presenta el servidor (una conexión, sin enviar nada)."""
    host, port = store.split_url(url)
    with socket.create_connection((host, port), timeout=TIMEOUT) as raw:
        with _tls_context().wrap_socket(raw, server_hostname=host) as s:
            return store.fp_of_der(s.getpeercert(binary_form=True) or b"")


def _net_error(e):
    if isinstance(e, socket.timeout):
        return "Sin respuesta (tiempo de espera agotado)."
    if isinstance(e, socket.gaierror):
        return "No se pudo resolver el nombre del servidor."
    if isinstance(e, ConnectionRefusedError):
        return "Conexión rechazada: ¿el panel está encendido y el puerto abierto en el firewall?"
    if isinstance(e, ssl.SSLError):
        return f"Error TLS: {e.reason or e}"
    return f"Sin conexión: {e}"


def _fetch(server):
    """Consulta un servidor remoto. Nunca lanza: devuelve el estado para la tarjeta."""
    out = {"id": server["id"], "state": "offline", "error": "", "data": None, "ms": None}
    host, port = store.split_url(server["url"])
    t0 = time.time()
    conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=_tls_context())
    try:
        conn.connect()
        got = store.fp_of_der(conn.sock.getpeercert(binary_form=True) or b"")
        if not store.fp_equal(got, server.get("fp")):
            # El token NO se envía: podría ser un intermediario.
            out.update(state="mismatch", got_fp=store.format_fp(got),
                       error="La huella del certificado no coincide con la guardada.")
            return out
        from .. import __version__
        conn.request("GET", REMOTE_PATH, headers={
            "Authorization": "Bearer " + server.get("token", ""),
            "Accept": "application/json",
            "User-Agent": f"MatiPanel/{__version__} (multi-servidor)",
        })
        r = conn.getresponse()
        body = r.read(MAX_BODY + 1)
        out["ms"] = int((time.time() - t0) * 1000)
        if r.status == 401:
            out.update(state="auth", error="El otro servidor rechazó el token (inválido o revocado).")
        elif r.status == 429:
            out.update(state="auth", error="El otro servidor bloqueó esta IP por intentos fallidos. Espera 5 minutos.")
        elif r.status == 404:
            out.update(state="error", error="El otro panel no tiene Multi-servidor: actualiza MatiPanel allí.")
        elif r.status != 200:
            out.update(state="error", error=f"Respuesta inesperada del otro panel (HTTP {r.status}).")
        elif len(body) > MAX_BODY:
            out.update(state="error", error="Respuesta demasiado grande.")
        else:
            data = store.clean_summary(json.loads(body))
            if data is None:
                out.update(state="error", error="Respuesta no válida del otro panel.")
            else:
                out.update(state="online", data=data)
    except (OSError, http.client.HTTPException) as e:
        out["error"] = _net_error(e)
    except ValueError:
        out.update(state="error", error="Respuesta no válida del otro panel.")
    finally:
        conn.close()
    return out


def _parallel(fn, items):
    """Consultas en paralelo: greenlets bajo gevent (como auth.py), hilos si no."""
    try:
        from gevent import monkey
        if monkey.is_module_patched("socket"):
            from gevent.pool import Pool
            return Pool(8).map(fn, items)
    except ImportError:
        pass
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        return list(pool.map(fn, items))


# ── Pantalla ──

@remote_bp.route("/")
@login_required
def index():
    fp = store.local_fingerprint()
    return render_template(
        "remote.html",
        hostname=socket.gethostname(),
        local_fp=store.format_fp(fp) if fp else "",
        cert_path=store.CERT_PATH,
        tokens=store.list_tokens(),
        servers=[store.public_server(s) for s in store.list_servers()],
    )


@remote_bp.route("/api/status")
@login_required
def status():
    servers = store.list_servers()
    results = {r["id"]: r for r in _parallel(_fetch, servers)} if servers else {}
    remote = []
    for s in servers:
        r = results.get(s["id"]) or {"state": "offline", "error": "Sin datos."}
        remote.append({**store.public_server(s), **r})
    local = {"id": "local", "local": True, "name": socket.gethostname(), "url": "",
             "state": "online", "error": "", "data": _summary(), "ms": 0}
    return jsonify({"ok": True, "ts": time.time(), "servers": [local] + remote})


@remote_bp.route("/api/fingerprint", methods=["POST"])
@login_required
def fingerprint():
    data = request.get_json(silent=True) or {}
    try:
        url = store.normalize_url(data.get("url"))
        fp = _peer_fingerprint(url)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except OSError as e:
        return jsonify({"ok": False, "error": _net_error(e)}), 502
    return jsonify({"ok": True, "url": url, "fp": store.format_fp(fp)})


@remote_bp.route("/api/servers/save", methods=["POST"])
@login_required
def server_save():
    data = request.get_json(silent=True) or {}
    try:
        entry = store.save_server(data)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True, "server": store.public_server(entry)})


@remote_bp.route("/api/servers/delete", methods=["POST"])
@login_required
def server_delete():
    sid = str((request.get_json(silent=True) or {}).get("id") or "")
    try:
        ok = store.delete_server(sid)
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": ok, "error": "" if ok else "El servidor ya no existe."})


@remote_bp.route("/api/tokens/create", methods=["POST"])
@login_required
def token_create():
    label = (request.get_json(silent=True) or {}).get("label")
    try:
        token, entry = store.create_token(label)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    resp = jsonify({"ok": True, "token": token, "entry": entry})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@remote_bp.route("/api/tokens/revoke", methods=["POST"])
@login_required
def token_revoke():
    tid = str((request.get_json(silent=True) or {}).get("id") or "")
    try:
        ok = store.revoke_token(tid)
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": ok, "error": "" if ok else "El token ya no existe."})
