"""Apariencia: personalización del panel por usuario."""
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from .. import prefs

appearance_bp = Blueprint("appearance", __name__)


@appearance_bp.route("/")
@login_required
def index():
    return render_template("appearance.html")


@appearance_bp.route("/api", methods=["POST"])
@login_required
def save():
    data = request.get_json(silent=True) or {}
    if data.get("reset"):
        new = prefs.clean({})
    else:
        new = {**prefs.get(current_user.username), **data}
    try:
        saved = prefs.save(current_user.username, new)
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True, "prefs": saved, "css": prefs.css(saved)})
