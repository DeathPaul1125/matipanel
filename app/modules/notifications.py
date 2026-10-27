"""Pantalla de notificaciones por correo (el motor está en app/notify.py)."""
import os

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
    return render_template("notify.html", events=notify.EVENTS)


@notify_bp.route("/api/config", methods=["GET", "POST"])
@login_required
def config():
    if request.method == "GET":
        return jsonify({"config": notify.public(notify.load()), "history": notify.history()})
    cfg, err = notify.validate(request.get_json(silent=True) or {}, notify.load())
    if err:
        return jsonify({"ok": False, "error": err}), 400
    cfg["panel_url"] = request.host_url.rstrip("/")  # para el enlace al pie de cada correo
    try:
        notify.save(cfg)
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True, "config": notify.public(cfg)})


@notify_bp.route("/api/test", methods=["POST"])
@login_required
def test():
    """Correo de prueba con la configuración guardada; el error del servidor SMTP se muestra tal cual."""
    cfg = notify.load()
    if not cfg["smtp"]["host"] or not cfg["smtp"]["from"] or not cfg["to"]:
        return jsonify({"ok": False, "error": "Guarda primero el servidor SMTP, el remitente y los destinatarios"}), 400
    try:
        notify.send_now(cfg, "Correo de prueba",
                        "Si recibes este correo, las notificaciones de MatiPanel están bien configuradas.")
    except Exception as e:
        notify._record("Correo de prueba", False, e)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 502
    notify._record("Correo de prueba", True)
    return jsonify({"ok": True, "to": cfg["to"]})
