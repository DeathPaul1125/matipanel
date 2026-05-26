"""Dashboard principal: métricas en vivo del sistema."""
import platform
import socket
import time
import psutil
from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user

dashboard_bp = Blueprint("dashboard", __name__)


def _bytes_to_human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


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
        distro=_distro_name(),
        arch=platform.machine(),
    )


@dashboard_bp.route("/api/stats")
@login_required
def stats():
    """Endpoint JSON consumido por el dashboard cada N segundos."""
    cpu_percent = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load = psutil.getloadavg()

    # Red: delta sobre 0.3s para calcular tasa
    net1 = psutil.net_io_counters()
    time.sleep(0.3)
    net2 = psutil.net_io_counters()
    rx_rate = (net2.bytes_recv - net1.bytes_recv) / 0.3
    tx_rate = (net2.bytes_sent - net1.bytes_sent) / 0.3

    return jsonify({
        "cpu": {
            "percent": round(cpu_percent, 1),
            "count": psutil.cpu_count(logical=True),
            "load": [round(x, 2) for x in load],
        },
        "memory": {
            "percent": mem.percent,
            "used_h": _bytes_to_human(mem.used),
            "total_h": _bytes_to_human(mem.total),
        },
        "disk": {
            "percent": disk.percent,
            "used_h": _bytes_to_human(disk.used),
            "total_h": _bytes_to_human(disk.total),
        },
        "network": {
            "rx_h": _bytes_to_human(rx_rate) + "/s",
            "tx_h": _bytes_to_human(tx_rate) + "/s",
        },
        "uptime": _uptime_str(),
    })


def _distro_name():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except FileNotFoundError:
        pass
    return platform.system()
