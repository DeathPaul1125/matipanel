"""Consulta a journalctl, archivos de /var/log y vista en vivo vía WebSocket."""
import os
import stat
import subprocess
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import valid_name

LOG_DIR = "/var/log"
MAX_FILES = 5000              # listado de /var/log
MAX_TAIL_BYTES = 8 * 1024 * 1024  # como mucho se leen los últimos 8 MB de un archivo

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
        out = subprocess.check_output(cmd, text=True, errors="replace", timeout=10)
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
            fd = _open_log(full)
            if fd is None:
                continue
            try:
                if b"\x00" in os.read(fd, 4096):
                    continue
                found.append({"path": full, "size": os.fstat(fd).st_size})
            except OSError:
                pass
            finally:
                os.close(fd)
            if len(found) >= MAX_FILES:
                break
        if len(found) >= MAX_FILES:
            break
    found.sort(key=lambda f: f["path"])
    return jsonify({"files": found})


def _inside_log_dir(path):
    return path.startswith(LOG_DIR + "/")


def _open_log(path):
    """fd de solo lectura de un archivo REGULAR dentro de /var/log, o None.

    - realpath: un symlink que apunte fuera (/etc/shadow) se rechaza.
    - O_NOFOLLOW + comprobar a dónde apunta el fd ya abierto: si alguien con escritura en un
      subdirectorio de /var/log cambia el archivo por un symlink entre la comprobación y la
      apertura (TOCTOU), se detecta.
    - O_NONBLOCK + S_ISREG: un FIFO o dispositivo nunca bloquea el worker (gevent) al abrirlo/leerlo.
    """
    real = os.path.realpath(path)
    if not _inside_log_dir(real):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
    try:
        fd = os.open(real, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("no es un archivo regular")
        if os.path.isdir("/proc/self/fd") and not _inside_log_dir(os.readlink(f"/proc/self/fd/{fd}")):
            raise OSError("fuera de /var/log")
    except OSError:
        os.close(fd)
        return None
    return fd


def _tail(fd, n):
    """Últimas n líneas leyendo como mucho MAX_TAIL_BYTES desde el final (memoria acotada
    aunque el archivo tenga líneas gigantes o pese GB)."""
    size = os.fstat(fd).st_size
    start = max(0, size - MAX_TAIL_BYTES)
    os.lseek(fd, start, os.SEEK_SET)
    chunks, left = [], size - start
    while left > 0:
        chunk = os.read(fd, min(1 << 20, left))
        if not chunk:
            break
        chunks.append(chunk)
        left -= len(chunk)
    lines = b"".join(chunks).decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # la primera queda cortada a la mitad
    return lines[-n:]


@logs_bp.route("/api/file")
@login_required
def file_tail():
    fd = _open_log(request.args.get("path", ""))
    if fd is None:
        return jsonify({"error": "Solo archivos dentro de /var/log"}), 400
    n = max(1, min(request.args.get("n", 200, type=int), 5000))
    try:
        lines = _tail(fd, n)
    except OSError as e:
        return jsonify({"error": f"No se pudo leer el archivo: {e.strerror or e}"}), 500
    finally:
        os.close(fd)
    return jsonify({"lines": lines})
