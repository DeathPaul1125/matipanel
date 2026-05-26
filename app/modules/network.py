"""Información de red: interfaces, IPs, conexiones activas."""
import psutil
import socket
from flask import Blueprint, render_template, jsonify
from flask_login import login_required

network_bp = Blueprint("network", __name__)


def _bytes_to_human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@network_bp.route("/")
@login_required
def index():
    return render_template("network.html")


@network_bp.route("/api/interfaces")
@login_required
def interfaces():
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    io = psutil.net_io_counters(pernic=True)

    result = []
    for name, addr_list in addrs.items():
        ipv4 = [a.address for a in addr_list if a.family == socket.AF_INET]
        ipv6 = [a.address for a in addr_list if a.family == socket.AF_INET6]
        mac = [a.address for a in addr_list if a.family == psutil.AF_LINK]
        st = stats.get(name)
        i = io.get(name)
        result.append({
            "name": name,
            "ipv4": ipv4[0] if ipv4 else "—",
            "ipv6": ipv6[0] if ipv6 else "—",
            "mac": mac[0] if mac else "—",
            "is_up": st.isup if st else False,
            "speed": st.speed if st else 0,
            "mtu": st.mtu if st else 0,
            "rx_h": _bytes_to_human(i.bytes_recv) if i else "—",
            "tx_h": _bytes_to_human(i.bytes_sent) if i else "—",
        })
    return jsonify({"interfaces": result})


@network_bp.route("/api/connections")
@login_required
def connections():
    conns = []
    try:
        for c in psutil.net_connections(kind="inet"):
            laddr = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "—"
            raddr = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "—"
            proc_name = "—"
            if c.pid:
                try:
                    proc_name = psutil.Process(c.pid).name()
                except psutil.NoSuchProcess:
                    pass
            conns.append({
                "proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                "local": laddr,
                "remote": raddr,
                "status": c.status,
                "pid": c.pid,
                "process": proc_name,
            })
    except psutil.AccessDenied:
        return jsonify({"error": "Necesitas permisos elevados para listar conexiones"}), 403
    return jsonify({"connections": conns})
