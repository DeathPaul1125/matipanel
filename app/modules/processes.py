"""Listado y gestión de procesos."""
import psutil
import time
from flask import Blueprint, render_template, jsonify, request, abort
from flask_login import login_required

processes_bp = Blueprint("processes", __name__)


@processes_bp.route("/")
@login_required
def index():
    return render_template("processes.html")


@processes_bp.route("/api/list")
@login_required
def list_processes():
    procs = []
    # Primer llamado a cpu_percent para inicializar contadores
    for p in psutil.process_iter(["pid", "username", "name", "cmdline"]):
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    time.sleep(0.2)

    for p in psutil.process_iter(["pid", "username", "name", "cmdline", "memory_percent", "create_time", "status"]):
        try:
            info = p.info
            cmd = " ".join(info["cmdline"]) if info["cmdline"] else info["name"]
            cpu = p.cpu_percent(None)
            running_secs = int(time.time() - info["create_time"])
            procs.append({
                "pid": info["pid"],
                "user": info["username"] or "?",
                "cmd": cmd[:120],
                "cpu": round(cpu, 1),
                "mem": round(info["memory_percent"] or 0, 1),
                "status": info["status"],
                "running": _fmt_duration(running_secs),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return jsonify({"processes": procs[:200]})


@processes_bp.route("/api/kill/<int:pid>", methods=["POST"])
@login_required
def kill(pid):
    """Manda SIGTERM al proceso. SIGKILL si force=1."""
    force = request.args.get("force") == "1"
    try:
        p = psutil.Process(pid)
        if force:
            p.kill()
        else:
            p.terminate()
        return jsonify({"ok": True})
    except psutil.NoSuchProcess:
        return jsonify({"ok": False, "error": "Proceso no existe"}), 404
    except psutil.AccessDenied:
        return jsonify({"ok": False, "error": "Sin permisos (¿necesitas sudo?)"}), 403


def _fmt_duration(seconds):
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
