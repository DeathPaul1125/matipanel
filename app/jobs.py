"""Salida en vivo de las acciones del panel ("terminal" de cada botón).

El navegador manda la cabecera X-Job-Id en cada POST; util.run() escribe aquí, línea a
línea, lo que van imprimiendo los comandos de esa petición. La ventana de terminal del
navegador consulta /jobs/<id> mientras la acción termina.

ponytail: en memoria, válido con un solo worker de gunicorn; cada tarea guarda como máximo
1 MB y se olvida 15 minutos después de terminar.
"""
import re
import threading
import time
from flask import Blueprint, jsonify, request, has_request_context
from flask_login import login_required, current_user

JOB_RE = re.compile(r"[A-Za-z0-9-]{16,64}")
MAX_BYTES = 1024 * 1024
TTL = 900

_jobs = {}
_lock = threading.Lock()

jobs_bp = Blueprint("jobs", __name__)


def current_job():
    """Id de la tarea de la petición actual (o None fuera de una petición con X-Job-Id)."""
    if not has_request_context():
        return None
    jid = request.headers.get("X-Job-Id", "")
    if not JOB_RE.fullmatch(jid) or not current_user.is_authenticated:
        return None
    with _lock:
        _cleanup()
        job = _jobs.get(jid)
        if job is None:
            job = _jobs[jid] = {"user": current_user.get_id(), "text": "", "size": 0,
                                "done": False, "ok": None, "at": time.time(), "truncated": False}
        elif job["user"] != current_user.get_id():
            return None
    return jid


def write(jid, text):
    if not jid or not text:
        return
    with _lock:
        job = _jobs.get(jid)
        if not job:
            return
        if job["size"] >= MAX_BYTES:
            if not job["truncated"]:
                job["text"] += "\n[… salida recortada: límite de 1 MB …]\n"
                job["truncated"] = True
            return
        job["text"] += text
        job["size"] += len(text)


def finish(jid, ok):
    with _lock:
        job = _jobs.get(jid)
        if job:
            job["done"], job["ok"], job["at"] = True, ok, time.time()


def _cleanup():
    now = time.time()
    for k in [k for k, j in _jobs.items() if now - j["at"] > TTL and (j["done"] or now - j["at"] > 6 * 3600)]:
        del _jobs[k]


@jobs_bp.route("/<jid>")
@login_required
def poll(jid):
    """Salida desde `offset` (caracteres). Solo la ve el usuario que lanzó la acción."""
    if not JOB_RE.fullmatch(jid):
        return jsonify({"error": "Tarea inválida"}), 400
    offset = max(0, request.args.get("offset", 0, type=int))
    with _lock:
        job = _jobs.get(jid)
        if not job or job["user"] != current_user.get_id():
            return jsonify({"text": "", "offset": offset, "done": False, "known": False})
        return jsonify({"text": job["text"][offset:offset + 256 * 1024], "offset": min(len(job["text"]), offset + 256 * 1024),
                        "done": job["done"], "ok": job["ok"], "known": True})


def init_app(app):
    app.register_blueprint(jobs_bp, url_prefix="/jobs")

    @app.after_request
    def _finish_job(resp):
        jid = request.headers.get("X-Job-Id", "")
        if JOB_RE.fullmatch(jid) and jid in _jobs:
            ok = resp.status_code < 400
            if resp.is_json:
                body = resp.get_json(silent=True)
                if isinstance(body, dict) and body.get("ok") is False:
                    ok = False
            finish(jid, ok)
        return resp
