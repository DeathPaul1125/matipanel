"""Gestión de servicios systemd vía systemctl."""
import subprocess
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import valid_name

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
            text=True, errors="replace", timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": f"No se pudo listar: {e}"}), 500

    # Estado de arranque (enabled/disabled/static) por unidad
    enabled = {}
    try:
        files = subprocess.check_output(
            ["systemctl", "list-unit-files", "--type=service", "--no-pager", "--no-legend", "--plain"],
            text=True, errors="replace", timeout=10,
        )
        for line in files.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                enabled[parts[0]] = parts[1]
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

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
            "enabled": enabled.get(unit, ""),
            "description": description,
        })
    return jsonify({"services": services})


@services_bp.route("/api/status")
@login_required
def status():
    unit = request.args.get("unit", "")
    if not valid_name(unit):
        return jsonify({"error": "Nombre de unidad inválido"}), 400
    try:
        r = subprocess.run(["systemctl", "status", "--no-pager", "-n", "40", "--", unit],
                           capture_output=True, text=True, errors="replace", timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return jsonify({"error": f"No se pudo consultar el estado: {e}"}), 500
    return jsonify({"output": r.stdout or r.stderr})


@services_bp.route("/api/action", methods=["POST"])
@login_required
def action():
    """Ejecuta systemctl <action> <unit> (el servicio corre como root)."""
    data = request.get_json(silent=True) or {}
    unit = data.get("unit", "")
    act = data.get("action", "")

    if act not in ALLOWED_ACTIONS:
        return jsonify({"ok": False, "error": "Acción no permitida"}), 400
    if not valid_name(unit):
        return jsonify({"ok": False, "error": "Nombre de unidad inválido"}), 400

    try:
        result = subprocess.run(
            ["systemctl", act, "--", unit],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"ok": True, "output": result.stdout})
        return jsonify({"ok": False, "error": result.stderr or "Fallo desconocido"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Tiempo de espera agotado"}), 504

