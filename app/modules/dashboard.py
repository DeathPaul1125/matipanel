"""Dashboard principal: veredicto de salud y métricas en vivo del sistema."""
import platform
import re
import shutil
import socket
import threading
import time
from collections import deque
import psutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from ..util import bytes_to_human as _bytes_to_human, run
from ..distro import detect
from .. import thresholds
from .. import metrics
from . import traffic
from .. import audit

dashboard_bp = Blueprint("dashboard", __name__)

# ponytail: caché en memoria (1 worker); apt tarda ~1 s, no hace falta consultarlo cada 15 s
_UPDATES_TTL = 1800
_updates_cache = {"at": 0.0, "count": None}

# Historial: una muestra cada 5 s, guardada en SQLite (app/metrics.py, 7 días).
# _history es la última hora en memoria, para respuestas rápidas y para el módulo multi-servidor.
SAMPLE_EVERY = 5
RANGES = (300, 900, 3600, 21600, 86400, 604800)
_history = deque(maxlen=720)
_sampler_started = False


def _sample_loop():
    metrics.open_db()
    _history.extend(metrics.recent(720))  # retomar la última hora tras un reinicio
    last_prune = 0.0
    net0, io0, t0 = psutil.net_io_counters(), psutil.disk_io_counters(), time.time()
    psutil.cpu_percent(None)
    while True:
        time.sleep(SAMPLE_EVERY)
        try:
            net, io, t = psutil.net_io_counters(), psutil.disk_io_counters(), time.time()
            dt = max(t - t0, 0.001)
            sample = {
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
            }
            _history.append(sample)
            metrics.insert(sample)
            traffic.sample(t)  # velocidad por interfaz (Tráfico de red y alertas)
            if t - last_prune > 3600:
                metrics.prune(t)
                audit.prune(t)
                last_prune = t
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
    import markdown
    from .. import version
    cur, hist = version.current(), list(reversed(version.history()))
    notes = [{"version": v, "date": d, "html": markdown.markdown(body)} for v, d, body in version.changelog_sections()[:6]]
    return render_template("about.html", version=cur["version"], hostname=socket.gethostname(), build=cur,
                           history=hist[:20], notes=notes, updated_at=hist[0]["at"] if hist else None)


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
    """Historial del rango pedido (segundos), promediado si es largo; `since` = solo lo nuevo."""
    rng = request.args.get("range", 900, type=int)
    if rng not in RANGES:
        rng = 900
    since = request.args.get("since", 0, type=float) or None
    try:
        bucket, points = metrics.query(rng, since=since, min_bucket=SAMPLE_EVERY)
    except Exception:  # base aún no abierta (primeros segundos) → memoria
        bucket, points = SAMPLE_EVERY, [p for p in _history if p["t"] > (since or 0)]
    return jsonify({"every": bucket, "range": rng, "points": points})


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


# Veredicto de salud para la barra superior (todas las pantallas). Caché corta: varias
# pestañas abiertas no multiplican las llamadas a systemctl.
_health_cache = {"at": 0.0, "data": None}
_docker_cache = {"at": 0.0, "data": []}


def docker_issues(now=None):
    """Contenedores de proyectos Compose (apps, stacks) que se reinician sin parar, terminaron con error o están
    «unhealthy». ponytail: cada 60 s como mucho; un `docker stop` normal sale con 0, 137 o 143 y no se avisa."""
    now = now or time.time()
    if now - _docker_cache["at"] < 60:
        return _docker_cache["data"]
    _docker_cache["at"] = now
    out = []
    code, text, _ = run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project",
                         "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"], timeout=10)
    for line in text.splitlines() if code == 0 else []:
        name, _, rest = line.partition("\t")
        state, _, status = rest.partition("\t")
        exit_code = re.match(r"Exited \((\d+)\)", status)
        if state == "restarting":
            out.append(("err", f"El contenedor {name} se reinicia sin parar", f"docker:{name}"))
        elif state == "exited" and exit_code and int(exit_code[1]) not in (0, 137, 143):
            out.append(("err", f"El contenedor {name} se detuvo con error (código {exit_code[1]})", f"docker:{name}"))
        elif "(unhealthy)" in status:
            out.append(("warn", f"El contenedor {name} no responde (unhealthy)", f"docker:{name}"))
    _docker_cache["data"] = out
    return out


def compute_issues():
    """Problemas actuales: ([(nivel, mensaje, clave estable)], servicios caídos).

    La clave identifica el problema aunque cambie el número (las notificaciones por correo la usan
    para avisar una vez al aparecer y otra al resolverse)."""
    issues = []
    t = thresholds.get()  # los umbrales de Notificaciones → Umbrales del servidor
    failed = _failed_units()
    if t["services"]["on"]:
        for u in failed:
            issues.append(("err", f"El servicio {u} falló", f"unit:{u}"))
    now = time.time()
    hist = list(_history) or [{"t": now, "cpu": psutil.cpu_percent(None), "mem": psutil.virtual_memory().percent,
                               "swap": psutil.swap_memory().percent, "load": psutil.getloadavg()[0]}]
    parts = [{**p, "inodes": thresholds.inode_percent(p["mount"]) if t["inodes"]["on"] else None} for p in _partitions()]
    issues += thresholds.evaluate(t, hist, now, parts, psutil.cpu_count() or 1,
                                  thresholds.cpu_temperature() if t["temp"]["on"] else None)
    try:
        issues += traffic.health_issues()
    except Exception:  # la barra de salud no debe caerse por las alertas de red
        pass
    try:
        if t["containers"]["on"] and shutil.which("docker"):
            issues += docker_issues()
    except Exception:
        pass
    try:
        from .. import monitor
        issues += monitor.health_issues()
    except Exception:
        pass
    try:
        from .. import updates
        issues += updates.security_issue()  # del último chequeo horario (no consulta APT aquí)
    except Exception:
        pass
    issues.sort(key=lambda i: i[0] != "err")
    return issues, failed


@dashboard_bp.route("/api/health")
@login_required
def health():
    now = time.time()
    if _health_cache["data"] and now - _health_cache["at"] < 10:
        return jsonify(_health_cache["data"])
    issues, failed = compute_issues()
    # Varios servicios caídos se resumen en una sola línea en la barra
    units = [i for i in issues if i[2].startswith("unit:")]
    shown = [i for i in issues if not i[2].startswith("unit:")]
    if units:
        shown.insert(0, units[0] if len(units) == 1 else ("err", f"{len(units)} servicios fallaron", "units"))
    level = "err" if any(i[0] == "err" for i in shown) else "warn" if shown else "ok"
    data = {"level": level, "issues": [i[1] for i in shown], "failed": failed,
            "updates": _pending_updates(), "sessions": len(psutil.users())}
    _health_cache.update(at=now, data=data)
    return jsonify(data)


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
