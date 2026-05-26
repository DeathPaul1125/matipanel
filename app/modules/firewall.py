"""Lectura de reglas UFW (Uncomplicated Firewall)."""
import subprocess
from flask import Blueprint, render_template, jsonify
from flask_login import login_required

firewall_bp = Blueprint("firewall", __name__)


@firewall_bp.route("/")
@login_required
def index():
    return render_template("firewall.html")


@firewall_bp.route("/api/status")
@login_required
def status():
    try:
        result = subprocess.run(
            ["sudo", "-n", "ufw", "status", "verbose", "numbered"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return jsonify({"installed": False, "error": "UFW no instalado o no accesible"}), 200

    if result.returncode != 0:
        return jsonify({
            "installed": True,
            "active": False,
            "error": result.stderr.strip() or "Permiso denegado (configurar sudoers)",
        })

    return jsonify({
        "installed": True,
        "active": "active" in result.stdout.lower(),
        "raw": result.stdout,
        "rules": _parse_ufw(result.stdout),
    })


def _parse_ufw(output):
    """Extrae filas de la tabla de reglas numeradas."""
    rules = []
    in_rules = False
    for line in output.splitlines():
        if line.strip().startswith("To"):
            in_rules = True
            continue
        if in_rules and line.strip() and not line.startswith("-"):
            # Formato típico: [ 1] 22/tcp     ALLOW IN    Anywhere
            stripped = line.strip()
            if stripped.startswith("["):
                rules.append(stripped)
    return rules
