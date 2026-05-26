"""Consulta a journalctl."""
import subprocess
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/")
@login_required
def index():
    return render_template("logs.html")


@logs_bp.route("/api/recent")
@login_required
def recent():
    """Últimas N líneas del journal. Filtrable por unidad y prioridad."""
    n = min(int(request.args.get("n", 200)), 1000)
    unit = request.args.get("unit", "").strip()
    priority = request.args.get("priority", "").strip()

    cmd = ["journalctl", "--no-pager", "-n", str(n), "--output=short-iso"]
    if unit and _valid_unit(unit):
        cmd += ["-u", unit]
    if priority in ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"):
        cmd += ["-p", priority]

    try:
        out = subprocess.check_output(cmd, text=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"journalctl falló: {e}"}), 500
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return jsonify({"error": "journalctl no disponible"}), 500

    return jsonify({"lines": out.splitlines()})


def _valid_unit(name):
    if not name or len(name) > 200:
        return False
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@.-_"
    return all(c in allowed for c in name)
