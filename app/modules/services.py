"""Gestión de servicios systemd vía systemctl."""
import subprocess
import shlex
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

services_bp = Blueprint("services", __name__)

ALLOWED_ACTIONS = {"start", "stop", "restart", "reload", "enable", "disable"}


@services_bp.route("/")
@login_required
def index():
    return render_template("services.html")


@services_bp.route("/api/list")
@login_required
def list_services():
    """Lista servicios con systemctl list-units."""
    try:
        out = subprocess.check_output(
            ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend", "--plain"],
            text=True, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": f"No se pudo listar: {e}"}), 500

    services = []
    for line in out.strip().splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        unit, load, active, sub, description = parts
        services.append({
            "unit": unit,
            "load": load,
            "active": active,
            "sub": sub,
            "description": description,
        })
    return jsonify({"services": services})


@services_bp.route("/api/action", methods=["POST"])
@login_required
def action():
    """Ejecuta systemctl <action> <unit>. Requiere sudo sin password configurado."""
    unit = request.json.get("unit", "")
    act = request.json.get("action", "")

    if act not in ALLOWED_ACTIONS:
        return jsonify({"ok": False, "error": "Acción no permitida"}), 400
    if not _valid_unit_name(unit):
        return jsonify({"ok": False, "error": "Nombre de unidad inválido"}), 400

    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", act, unit],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True, "output": result.stdout})
        return jsonify({"ok": False, "error": result.stderr or "Fallo desconocido"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Tiempo de espera agotado"}), 504


def _valid_unit_name(name):
    """Sanitización básica para evitar inyección de comandos."""
    if not name or len(name) > 200:
        return False
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@.-_"
    return all(c in allowed for c in name)
