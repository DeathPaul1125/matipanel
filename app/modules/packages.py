"""Gestión de paquetes APT (Debian/Ubuntu)."""
import subprocess
from flask import Blueprint, render_template, jsonify
from flask_login import login_required

packages_bp = Blueprint("packages", __name__)


@packages_bp.route("/")
@login_required
def index():
    return render_template("packages.html")


@packages_bp.route("/api/summary")
@login_required
def summary():
    """Resumen: total instalados y cuántos pueden actualizarse."""
    info = {"installed": 0, "upgradable": 0, "security": 0, "upgradable_list": []}

    # Total instalados
    try:
        out = subprocess.check_output(
            ["dpkg-query", "-f", "${binary:Package}\n", "-W"],
            text=True, timeout=10,
        )
        info["installed"] = len(out.strip().splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Actualizables
    try:
        out = subprocess.check_output(
            ["apt", "list", "--upgradable"],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        )
        lines = out.strip().splitlines()[1:]  # primera línea es "Listing..."
        info["upgradable"] = len(lines)
        for line in lines[:50]:
            # Formato: paquete/origen versión-nueva arch [upgradable from: versión-vieja]
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0].split("/")[0]
                origin = parts[0].split("/")[1] if "/" in parts[0] else ""
                new_ver = parts[1]
                is_security = "security" in origin
                if is_security:
                    info["security"] += 1
                info["upgradable_list"].append({
                    "name": name,
                    "new_version": new_ver,
                    "origin": origin,
                    "security": is_security,
                })
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return jsonify(info)
