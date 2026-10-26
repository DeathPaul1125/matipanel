"""Gestión de paquetes con soporte multidistro (APT prioritario / DNF básico)."""
import os
import re
import subprocess
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..distro import detect
from ..util import run, bytes_to_human

packages_bp = Blueprint("packages", __name__)

PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}(:[a-z0-9-]+)?$")
RPM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]{0,127}$")
TERM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]{0,63}$")
LOCK_RE = re.compile(r"Could not get lock|Unable to acquire the dpkg frontend lock|"
                     r"Unable to lock the administration directory|another process using it")
PM_ENV = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
AUTO_UPGRADES = "/etc/apt/apt.conf.d/20auto-upgrades"
OUTPUT_MAX = 200_000
SEARCH_MAX = 200

# acción -> subcomando; las marcadas en NEEDS_PKGS reciben "-- paquetes..."
_APT = {"refresh": ["update"], "upgrade": ["upgrade"], "full-upgrade": ["dist-upgrade"],
        "upgrade-pkgs": ["install", "--only-upgrade"], "install": ["install"],
        "remove": ["remove"], "purge": ["purge"], "autoremove": ["autoremove"]}
_DNF = {"refresh": ["makecache"], "upgrade": ["upgrade"], "upgrade-pkgs": ["upgrade"],
        "install": ["install"], "remove": ["remove"], "autoremove": ["autoremove"]}
NEEDS_PKGS = {"upgrade-pkgs", "install", "remove", "purge"}


def _result(code, out, err):
    """(ok, salida combinada, mensaje de error legible)."""
    output = (out + ("\n" + err if err else "")).strip()[-OUTPUT_MAX:]
    if code == 0:
        return True, output, ""
    if LOCK_RE.search(output):
        msg = ("El gestor de paquetes está bloqueado por otro proceso (apt, dpkg o "
               "unattended-upgrades). Espera a que termine y vuelve a intentarlo.")
    elif "dpkg was interrupted" in output:
        msg = "dpkg quedó interrumpido: ejecuta 'dpkg --configure -a' en la terminal y reintenta."
    else:
        msg = (err.strip().splitlines() or [f"Terminó con código {code}"])[-1]
    return False, output, msg


def apt_get(*args, timeout=1800):
    """apt-get no interactivo (también lo usa el módulo PHP)."""
    code, out, err = run(["apt-get", "-y", "-o", "Dpkg::Options::=--force-confold",
                          "-o", "DPkg::Lock::Timeout=60", *args], timeout=timeout, env=PM_ENV)
    return _result(code, out, err)


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


@packages_bp.route("/api/run", methods=["POST"])
@login_required
def run_action():
    """Operaciones de escritura: refresh, upgrade, install, remove…"""
    data = request.get_json(silent=True) or {}
    act = data.get("action", "")
    pkgs = data.get("packages") or []
    pm = detect()["pkg"]
    table = _APT if pm == "apt" else _DNF if pm in ("dnf", "yum") else {}
    if act not in table:
        return jsonify({"ok": False, "error": "Acción no soportada en este sistema"}), 400

    args = list(table[act])
    if act in NEEDS_PKGS:
        rx = PKG_RE if pm == "apt" else RPM_RE
        if (not isinstance(pkgs, list) or not 0 < len(pkgs) <= 200
                or not all(isinstance(p, str) and rx.match(p) for p in pkgs)):
            return jsonify({"ok": False, "error": "Nombre de paquete inválido"}), 400
        args += ["--", *pkgs]

    if pm == "apt":
        ok, output, error = apt_get(*args)
    else:
        ok, output, error = _result(*run([pm, "-y", *args], timeout=1800, env=PM_ENV))
    if ok:
        return jsonify({"ok": True, "output": output})
    return jsonify({"ok": False, "error": error, "output": output}), 409 if LOCK_RE.search(output) else 500


@packages_bp.route("/api/search")
@login_required
def search():
    if detect()["pkg"] != "apt":
        return jsonify({"error": "La búsqueda solo está disponible con APT"}), 501
    terms = request.args.get("q", "").split()
    if not terms or len(terms) > 5 or not all(TERM_RE.match(t) for t in terms):
        return jsonify({"error": "Búsqueda inválida (letras, números y + . _ -)"}), 400

    code, out, err = run(["apt-cache", "search", "--", *terms], timeout=60, env=PM_ENV)
    if code != 0:
        return jsonify({"error": err.strip() or "apt-cache falló"}), 500

    q = terms[0].lower()
    lines = sorted(out.splitlines(), key=lambda l: (l.split(" ", 1)[0] != q, q not in l.split(" ", 1)[0], l))
    installed = {p["name"]: p["version"] for p in _installed_list()}
    results = []
    for line in lines[:SEARCH_MAX]:
        name, _, desc = line.partition(" - ")
        results.append({"name": name, "description": desc,
                        "installed": name in installed, "version": installed.get(name, "")})
    return jsonify({"results": results, "total": len(lines)})


@packages_bp.route("/api/installed")
@login_required
def installed():
    pkgs = _installed_list()
    if pkgs is None:
        return jsonify({"error": "Gestor de paquetes no soportado"}), 501
    return jsonify({"packages": sorted(pkgs, key=lambda p: p["name"])})


def _installed_list():
    pm = detect()["pkg"]
    if pm == "apt":
        cmd, mult = ["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Installed-Size}\t${Status}\n"], 1024
    elif pm in ("dnf", "yum"):
        cmd, mult = ["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{SIZE}\tinstalled\n"], 1
    else:
        return None
    _, out, _ = run(cmd, timeout=30)
    pkgs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or parts[3].split()[-1:] != ["installed"]:
            continue  # p.ej. "deinstall ok config-files", "half-installed"
        size = int(parts[2]) * mult if parts[2].isdigit() else 0
        pkgs.append({"name": parts[0], "version": parts[1], "bytes": size, "size": bytes_to_human(size)})
    return pkgs


@packages_bp.route("/api/unattended")
@login_required
def unattended():
    enabled = False
    try:
        with open(AUTO_UPGRADES) as f:
            m = re.search(r'APT::Periodic::Unattended-Upgrade\s+"(\d+)"', f.read())
            enabled = bool(m and m.group(1) != "0")
    except OSError:
        pass
    return jsonify({
        "supported": detect()["pkg"] == "apt",
        "installed": os.path.exists("/usr/bin/unattended-upgrade"),
        "enabled": enabled,
    })


@packages_bp.route("/api/unattended", methods=["POST"])
@login_required
def set_unattended():
    if not os.path.isdir(os.path.dirname(AUTO_UPGRADES)):
        return jsonify({"ok": False, "error": "Solo disponible con APT"}), 501
    v = "1" if (request.get_json(silent=True) or {}).get("enabled") else "0"
    try:
        with open(AUTO_UPGRADES, "w") as f:
            f.write(f'APT::Periodic::Update-Package-Lists "{v}";\n'
                    f'APT::Periodic::Unattended-Upgrade "{v}";\n')
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


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
