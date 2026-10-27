"""Pantalla de notificaciones por correo (el motor está en app/notify.py)."""
import os
import time

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from .. import notify

notify_bp = Blueprint("notify", __name__)


@notify_bp.record_once
def _start(_state):
    if os.environ.get("MATIPANEL_NOTIFY_WORKER", "1") != "0":
        notify.start()


@notify_bp.route("/")
@login_required
def index():
    groups = [(g, [(k, v[0]) for k, v in notify.EVENTS.items() if v[2] == g]) for g in notify.GROUPS]
    return render_template("notify.html", groups=groups)


@notify_bp.route("/api/config", methods=["GET", "POST"])
@login_required
def config():
    if request.method == "GET":
        return jsonify({"config": notify.public(notify.load()), "history": notify.history()})
    cfg, err = notify.validate(request.get_json(silent=True) or {}, notify.load())
    if err:
        return jsonify({"ok": False, "error": err}), 400
    cfg["panel_url"] = request.host_url.rstrip("/")  # para los botones y enlaces de cada correo
    try:
        notify.save(cfg)
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True, "config": notify.public(cfg)})


def _test_mail(cfg):
    return {"subject": "Correo de prueba", "level": "ok", "badge": "Prueba", "link": "/notificaciones/",
            "body": "Si recibes este correo, las notificaciones de MatiPanel están bien configuradas.",
            "sections": [{"rows": [("Servidor SMTP", f"{cfg['smtp']['host']}:{cfg['smtp']['port']}"),
                                   ("Seguridad", {"starttls": "STARTTLS", "ssl": "SSL/TLS", "none": "Sin cifrar"}[cfg["smtp"]["security"]]),
                                   ("Remitente", cfg["smtp"]["from"]), ("Destinatarios", ", ".join(cfg["to"])),
                                   ("Avisos activos", str(sum(1 for v in cfg["events"].values() if v)), "ok")]}]}


@notify_bp.route("/api/preview/<kind>")
@login_required
def preview(kind):
    """HTML de un correo de ejemplo (el navegador lo muestra en un iframe aislado, sin scripts)."""
    if kind not in notify.EVENTS and kind != "test":
        return jsonify({"ok": False, "error": "Tipo de aviso desconocido"}), 404
    cfg = {**notify.load(), "panel_url": request.host_url.rstrip("/")}
    m = _test_mail(cfg) if kind == "test" else notify.sample(kind)
    return jsonify({"ok": True, "subject": m["subject"], "html": notify.render_html(cfg, m, notify._hostname(), time.time())})


@notify_bp.route("/api/test", methods=["POST"])
@login_required
def test():
    """Correo de prueba (o el ejemplo de un tipo de aviso) con la configuración guardada; el error SMTP se muestra tal cual."""
    cfg = notify.load()
    if not cfg["smtp"]["host"] or not cfg["smtp"]["from"] or not cfg["to"]:
        return jsonify({"ok": False, "error": "Guarda primero el servidor SMTP, el remitente y los destinatarios"}), 400
    kind = str((request.get_json(silent=True) or {}).get("kind") or "")
    if kind and kind not in notify.EVENTS:
        return jsonify({"ok": False, "error": "Tipo de aviso desconocido"}), 404
    m = notify.sample(kind) if kind else _test_mail(cfg)
    subject = m.pop("subject") + (" (ejemplo)" if kind and kind != "daily_summary" else "")
    try:
        notify.send_now(cfg, subject, m.pop("body", ""), **{k: v for k, v in m.items() if v})
    except Exception as e:
        notify._record(subject, False, e, kind=kind)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 502
    notify._record(subject, True, kind=kind)
    return jsonify({"ok": True, "to": cfg["to"]})
