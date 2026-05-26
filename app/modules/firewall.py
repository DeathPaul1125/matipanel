"""Firewall: UFW (Debian/Ubuntu) y firewalld (RHEL/Fedora)."""
import subprocess
from flask import Blueprint, render_template, jsonify
from flask_login import login_required
from ..distro import detect

firewall_bp = Blueprint("firewall", __name__)


@firewall_bp.route("/")
@login_required
def index():
    return render_template("firewall.html", distro=detect())


@firewall_bp.route("/api/status")
@login_required
def status():
    d = detect()
    if d["fw"] == "ufw":
        return jsonify(_ufw_status())
    if d["fw"] == "firewalld":
        return jsonify(_firewalld_status())
    if d["fw"] == "iptables":
        return jsonify(_iptables_status())
    return jsonify({"installed": False, "engine": None,
                    "error": "Ningún firewall detectado (ufw/firewalld/iptables)"})


def _ufw_status():
    try:
        result = subprocess.run(
            ["sudo", "-n", "ufw", "status", "verbose", "numbered"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"installed": False, "engine": "ufw"}

    if result.returncode != 0:
        return {"installed": True, "engine": "ufw", "active": False,
                "error": result.stderr.strip() or "Permiso denegado"}

    return {
        "installed": True, "engine": "ufw",
        "active": "active" in result.stdout.lower(),
        "raw": result.stdout,
    }


def _firewalld_status():
    out = {"installed": True, "engine": "firewalld", "active": False, "zones": []}
    try:
        state = subprocess.run(
            ["systemctl", "is-active", "firewalld"],
            capture_output=True, text=True, timeout=5,
        )
        out["active"] = state.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"installed": False, "engine": "firewalld"}

    if not out["active"]:
        return out

    try:
        default = subprocess.check_output(
            ["sudo", "-n", "firewall-cmd", "--get-default-zone"],
            text=True, timeout=5,
        ).strip()
        out["default_zone"] = default

        zones_out = subprocess.check_output(
            ["sudo", "-n", "firewall-cmd", "--list-all-zones"],
            text=True, timeout=10,
        )
        out["raw"] = zones_out
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        out["error"] = f"Sin permisos: configura sudoers para firewall-cmd ({e})"

    return out


def _iptables_status():
    try:
        out = subprocess.check_output(
            ["sudo", "-n", "iptables", "-L", "-n", "-v"],
            text=True, timeout=5,
        )
        return {"installed": True, "engine": "iptables", "active": True, "raw": out}
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"installed": True, "engine": "iptables", "active": False, "error": str(e)}
