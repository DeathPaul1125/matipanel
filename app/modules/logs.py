"""Consulta a journalctl, archivos de /var/log y vista en vivo vía WebSocket."""
import os
import subprocess
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import valid_name, run

LOG_DIR = "/var/log"

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/")
@login_required
def index():
    return render_template("logs.html")


@logs_bp.route("/live")
@login_required
def live():
    return render_template("logs_live.html")


@logs_bp.route("/api/recent")
@login_required
def recent():
    n = max(1, min(request.args.get("n", 200, type=int), 1000))
    unit = request.args.get("unit", "").strip()
    priority = request.args.get("priority", "").strip()

    cmd = ["journalctl", "--no-pager", "-n", str(n), "--output=short-iso"]
    if unit:
        # Antes una unidad inválida se ignoraba y se mostraba TODO el journal sin avisar.
        if not valid_name(unit):
            return jsonify({"error": f"Nombre de unidad inválido: {unit}"}), 400
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


@logs_bp.route("/api/files")
@login_required
def files():
    """Archivos de texto en /var/log (sin rotados comprimidos ni binarios)."""
    found = []
    for root, dirs, names in os.walk(LOG_DIR):
        if root == LOG_DIR and "journal" in dirs:
            dirs.remove("journal")  # journal de systemd: binario, se consulta con journalctl
        for name in names:
            if name.endswith((".gz", ".xz", ".bz2", ".zst")) or name in ("wtmp", "btmp", "lastlog"):
                continue
            full = os.path.join(root, name)
            try:
                if _is_binary(full):
                    continue
                found.append({"path": full, "size": os.path.getsize(full)})
            except OSError:
                pass
    found.sort(key=lambda f: f["path"])
    return jsonify({"files": found})


def _is_binary(path):
    with open(path, "rb") as f:
        return b"\x00" in f.read(4096)


@logs_bp.route("/api/file")
@login_required
def file_tail():
    path = os.path.realpath(request.args.get("path", ""))
    if not path.startswith(LOG_DIR + "/") or not os.path.isfile(path):
        return jsonify({"error": "Solo archivos dentro de /var/log"}), 400
    n = max(1, min(request.args.get("n", 200, type=int), 5000))
    code, out, err = run(["tail", "-n", str(n), "--", path], timeout=10)
    if code != 0:
        return jsonify({"error": err.strip()}), 500
    return jsonify({"lines": out.splitlines()})

