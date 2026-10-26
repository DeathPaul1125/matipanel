"""Dashboard principal: veredicto de salud y métricas en vivo del sistema."""
import platform
import socket
import threading
import time
from collections import deque
import psutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from ..util import bytes_to_human as _bytes_to_human, run
from ..distro import detect

dashboard_bp = Blueprint("dashboard", __name__)

# ponytail: caché en memoria (1 worker); apt tarda ~1 s, no hace falta consultarlo cada 15 s
_UPDATES_TTL = 1800
_updates_cache = {"at": 0.0, "count": None}

# Historial: una muestra cada 5 s, última hora.
# ponytail: en memoria (se pierde al reiniciar el servicio); persistir en disco si se necesita más historia.
SAMPLE_EVERY = 5
_history = deque(maxlen=720)
_sampler_started = False


def _sample_loop():
    net0, io0, t0 = psutil.net_io_counters(), psutil.disk_io_counters(), time.time()
    psutil.cpu_percent(None)
    while True:
        time.sleep(SAMPLE_EVERY)
        try:
            net, io, t = psutil.net_io_counters(), psutil.disk_io_counters(), time.time()
            dt = max(t - t0, 0.001)
            _history.append({
                "t": round(t, 1),
                "cpu": round(psutil.cpu_percent(None), 1),
                "mem": psutil.virtual_memory().percent,
                "swap": psutil.swap_memory().percent,
                "rx": max(0, (net.bytes_recv - net0.bytes_recv) / dt),
                "tx": max(0, (net.bytes_sent - net0.bytes_sent) / dt),
                "rd": max(0, (io.read_bytes - io0.read_bytes) / dt) if io and io0 else 0,
                "wr": max(0, (io.write_bytes - io0.write_bytes) / dt) if io and io0 else 0,
                "procs": len(psutil.pids()),
                "load": round(psutil.getloadavg()[0], 2),
            })
            net0, io0, t0 = net, io, t
        except Exception:  # el muestreo nunca debe morir por un error puntual
            pass


@dashboard_bp.record_once
def _start_sampler(_state):
    global _sampler_started
    if not _sampler_started:
        _sampler_started = True
        threading.Thread(target=_sample_loop, daemon=True, name="matipanel-sampler").start()


def _cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _uptime_str():
    seconds = int(time.time() - psutil.boot_time())
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@dashboard_bp.route("/")
@login_required
def index():
    return render_template(
        "dashboard.html",
        hostname=socket.gethostname(),
        user=current_user.username,
        uptime=_uptime_str(),
        kernel=platform.release(),
        distro=detect()["name"],
        arch=platform.machine(),
        cores=psutil.cpu_count(logical=True),
        cpu_model=_cpu_model(),
        sample_every=SAMPLE_EVERY,
    )


@dashboard_bp.route("/acerca")
@login_required
def about():
    from .. import __version__
    return render_template("about.html", version=__version__, hostname=socket.gethostname())


@dashboard_bp.route("/api/stats")
@login_required
def stats():
    """Métricas rápidas (cada 3 s). La tasa de red la calcula el navegador con los contadores."""
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    return jsonify({
        "ts": time.time(),
        "cpu": {
            # Última muestra del historial (cpu_percent comparte contador: un solo lector)
            "percent": _history[-1]["cpu"] if _history else 0.0,
            "count": psutil.cpu_count(logical=True),
            "load": [round(x, 2) for x in psutil.getloadavg()],
        },
        "memory": {
            "percent": mem.percent,
            "used_h": _bytes_to_human(mem.total - mem.available),
            "total_h": _bytes_to_human(mem.total),
            "available_h": _bytes_to_human(mem.available),
        },
        "swap": {"percent": swap.percent, "used_h": _bytes_to_human(swap.used),
                 "total_h": _bytes_to_human(swap.total)},
        "disk": {
            "percent": disk.percent,
            "used_h": _bytes_to_human(disk.used),
            "total_h": _bytes_to_human(disk.total),
            "free_h": _bytes_to_human(disk.free),
        },
        "network": {"rx": net.bytes_recv, "tx": net.bytes_sent},
        "procs": len(psutil.pids()),
        "uptime": _uptime_str(),
    })


@dashboard_bp.route("/api/history")
@login_required
def history():
    """Muestras posteriores a `since` (incremental); sin `since`, toda la última hora."""
    since = request.args.get("since", 0, type=float)
    return jsonify({"every": SAMPLE_EVERY, "points": [p for p in _history if p["t"] > since]})


@dashboard_bp.route("/api/overview")
@login_required
def overview():
    """Datos más pesados (cada 15 s): servicios caídos, discos, procesos top, sesiones."""
    return jsonify({
        "failed": _failed_units(),
        "partitions": _partitions(),
        "top": _top_processes(),
        "sessions": len(psutil.users()),
        "updates": _pending_updates(),
    })


def _failed_units():
    code, out, _ = run(["systemctl", "list-units", "--failed", "--type=service",
                        "--plain", "--no-legend", "--no-pager"], timeout=10)
    if code != 0:
        return []
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def _partitions():
    parts = []
    for p in psutil.disk_partitions(all=False):
        if p.fstype in ("squashfs", "tmpfs", "overlay") or p.mountpoint.startswith("/snap"):
            continue
        try:
            u = psutil.disk_usage(p.mountpoint)
        except OSError:
            continue
        parts.append({"mount": p.mountpoint, "percent": u.percent,
                      "used_h": _bytes_to_human(u.used), "total_h": _bytes_to_human(u.total)})
    return parts


def _top_processes(n=6):
    # process_iter reutiliza los objetos Process entre llamadas, así cpu_percent(None)
    # devuelve el promedio desde la consulta anterior (~15 s).
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "memory_percent"]):
        try:
            procs.append({"pid": p.info["pid"], "name": p.info["name"] or "?",
                          "user": p.info["username"] or "?",
                          "cpu": round(p.cpu_percent(None), 1),
                          "mem": round(p.info["memory_percent"] or 0, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: (x["cpu"], x["mem"]), reverse=True)
    return procs[:n]


def _pending_updates():
    """Paquetes actualizables (solo APT). None si no aplica o falló."""
    if detect()["pkg"] != "apt":
        return None
    now = time.time()
    if now - _updates_cache["at"] > _UPDATES_TTL:
        code, out, _ = run(["apt", "list", "--upgradable"], timeout=30)
        _updates_cache["count"] = (len([l for l in out.splitlines() if "/" in l])
                                   if code == 0 else None)
        _updates_cache["at"] = now
    return _updates_cache["count"]
