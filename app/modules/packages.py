"""Gestión de paquetes con soporte multidistro (APT / DNF)."""
import subprocess
from flask import Blueprint, render_template, jsonify
from flask_login import login_required
from ..distro import detect

packages_bp = Blueprint("packages", __name__)


@packages_bp.route("/")
@login_required
def index():
    return render_template("packages.html", distro=detect())


@packages_bp.route("/api/summary")
@login_required
def summary():
    d = detect()
    if d["pkg"] == "apt":
        return jsonify(_apt_summary())
    if d["pkg"] in ("dnf", "yum"):
        return jsonify(_dnf_summary(d["pkg"]))
    return jsonify({
        "installed": 0, "upgradable": 0, "security": 0,
        "upgradable_list": [],
        "error": f"Gestor de paquetes '{d['pkg']}' no soportado todavía",
    })


def _apt_summary():
    info = {"installed": 0, "upgradable": 0, "security": 0, "upgradable_list": []}
    try:
        out = subprocess.check_output(
            ["dpkg-query", "-f", "${binary:Package}\n", "-W"],
            text=True, timeout=10,
        )
        info["installed"] = len(out.strip().splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        out = subprocess.check_output(
            ["apt", "list", "--upgradable"],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        )
        lines = out.strip().splitlines()[1:]
        info["upgradable"] = len(lines)
        for line in lines[:50]:
            parts = line.split()
            if len(parts) >= 4:
                name = parts[0].split("/")[0]
                origin = parts[0].split("/")[1] if "/" in parts[0] else ""
                new_ver = parts[1]
                is_security = "security" in origin
                if is_security:
                    info["security"] += 1
                info["upgradable_list"].append({
                    "name": name, "new_version": new_ver,
                    "origin": origin, "security": is_security,
                })
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return info


def _dnf_summary(cmd):
    info = {"installed": 0, "upgradable": 0, "security": 0, "upgradable_list": []}
    try:
        out = subprocess.check_output(
            ["rpm", "-qa"], text=True, timeout=15,
        )
        info["installed"] = len(out.strip().splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        out = subprocess.check_output(
            [cmd, "check-update", "--quiet"],
            text=True, stderr=subprocess.DEVNULL, timeout=30,
        )
    except subprocess.CalledProcessError as e:
        out = e.stdout or ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return info

    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3 and "." in parts[0]:
            info["upgradable_list"].append({
                "name": parts[0],
                "new_version": parts[1],
                "origin": parts[2],
                "security": False,
            })
    info["upgradable"] = len(info["upgradable_list"])

    try:
        sec = subprocess.check_output(
            [cmd, "updateinfo", "list", "security", "--quiet"],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        )
        info["security"] = len([l for l in sec.splitlines() if l.strip()])
        sec_names = set()
        for line in sec.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                sec_names.add(parts[2].split("-")[0])
        for p in info["upgradable_list"]:
            if p["name"].split(".")[0] in sec_names:
                p["security"] = True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return info
