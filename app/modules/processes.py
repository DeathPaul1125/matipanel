"""Listado y gestión de procesos, y en qué se usa la memoria."""
import json
import os
import shutil
import psutil
import time
from flask import Blueprint, render_template, jsonify, request, abort
from flask_login import login_required

from ..util import run

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

    for p in psutil.process_iter(["pid", "username", "name", "cmdline", "memory_percent", "memory_info", "create_time", "status"]):
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
                "rss": info["memory_info"].rss if info["memory_info"] else 0,
                "status": info["status"],
                "running": _fmt_duration(running_secs),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return jsonify({"processes": procs[:1000]})  # todos: los que más memoria usan pueden no estar entre los de más CPU


@processes_bp.route("/api/kill/<int:pid>", methods=["POST"])
@login_required
def kill(pid):
    """Manda SIGTERM al proceso. SIGKILL si force=1."""
    force = request.args.get("force") == "1"
    # PID 0 = "todo el grupo de procesos" para kill(); getppid() = el maestro de gunicorn
    if pid <= 1 or pid in (os.getpid(), os.getppid()):
        return jsonify({"ok": False, "error": "No se puede matar init ni el propio panel"}), 400
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
        return jsonify({"ok": False, "error": "Sin permisos"}), 403
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ── ¿En qué se usa la memoria? ───────────────────────────────────────────

# Programas conocidos: por qué usan memoria y qué se puede hacer (se busca por el inicio del nombre)
HINTS = (
    (("mariadbd", "mysqld"), "Reserva memoria para su caché de datos (innodb_buffer_pool_size): es normal que sea de los que más usan. "
                             "Se ajusta en su configuración; menos caché = consultas más lentas."),
    (("postgres",), "Cada conexión es un proceso; shared_buffers es su caché. Muchas conexiones abiertas suman memoria."),
    (("php-fpm",), "Cada proceso atiende una petición web. pm.max_children limita cuántos hay a la vez (PHP → PHP-FPM)."),
    (("apache2", "httpd"), "Cada proceso o hilo atiende visitas. Muchos procesos = mucho tráfico o MaxRequestWorkers alto."),
    (("nginx",), "Servidor web; suele usar poca memoria."),
    (("dockerd", "containerd"), "Motor de Docker. Lo que usan los contenedores se ve en su propia tabla."),
    (("java",), "Usa lo que su opción -Xmx le permita (máximo de memoria de Java)."),
    (("node",), "Aplicación de Node.js."),
    (("gunicorn",), "Es MatiPanel."),
    (("clamd",), "Antivirus: mantiene sus firmas en memoria (alrededor de 1 GB)."),
    (("redis-server",), "Base en memoria: usa tanto como datos guarde (maxmemory la limita)."),
    (("systemd-journal",), "Registro del sistema."),
    (("qemu", "kvm"), "Máquina virtual: usa la memoria que se le asignó."),
)
_mem_cache = {"at": 0.0, "data": None}


def hint(name):
    return next((h for prefixes, h in HINTS if name.startswith(prefixes)), "")


def memory_overview():
    """Reparto de la RAM: programas, caché (se libera sola), libre y swap. Bytes."""
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    cache = getattr(vm, "buffers", 0) + getattr(vm, "cached", 0)
    apps = max(0, vm.total - vm.available)
    return {"total": vm.total, "available": vm.available, "apps": apps, "cache": min(cache, max(0, vm.total - apps)),
            "free": vm.free, "shared": getattr(vm, "shared", 0), "percent": vm.percent,
            "swap_total": sw.total, "swap_used": sw.used, "swap_percent": sw.percent}


def _pss(pid):
    """PSS en bytes desde /proc/<pid>/smaps_rollup (rápido; kernel 4.14+). None si no se puede leer.
    ponytail: se lee a mano porque psutil.memory_full_info puede recorrer smaps completo (lento con muchos procesos)."""
    try:
        with open(f"/proc/{pid}/smaps_rollup", "rb") as f:
            for line in f:
                if line.startswith(b"Pss:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def by_program(limit=25):
    """Procesos agrupados por programa, con su memoria sumada. Usa PSS (reparte la memoria compartida entre
    quienes la comparten, así 12 procesos de php-fpm no cuentan 12 veces la misma); si no se puede leer, RSS."""
    groups, total_pss = {}, True
    for p in psutil.process_iter(["name", "username", "memory_info"]):
        info = p.info
        if not info.get("memory_info"):
            continue
        mem = _pss(p.pid)
        if mem is None:
            mem, total_pss = info["memory_info"].rss, False
        name = info["name"] or "?"
        g = groups.setdefault(name, {"name": name, "count": 0, "mem": 0, "users": set()})
        g["count"] += 1
        g["mem"] += mem
        g["users"].add(info["username"] or "?")
    rows = sorted(groups.values(), key=lambda g: -g["mem"])
    total = psutil.virtual_memory().total
    out = [{**g, "users": sorted(g["users"])[:4], "percent": round(g["mem"] / total * 100, 1), "hint": hint(g["name"])}
           for g in rows[:limit]]
    rest = rows[limit:]
    return out, {"count": sum(g["count"] for g in rest), "mem": sum(g["mem"] for g in rest)}, total_pss


def docker_memory():
    """Memoria de cada contenedor en marcha, con su proyecto de Compose (app o stack)."""
    if not shutil.which("docker"):
        return None
    from .disk import parse_size
    code, out, _ = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=30)
    if code != 0:
        return None
    code2, ps, _ = run(["docker", "ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}'], timeout=20)
    projects = dict(line.split("\t", 1) for line in ps.splitlines() if "\t" in line) if code2 == 0 else {}
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        used, _, limit = str(d.get("MemUsage", "")).partition(" / ")
        name = d.get("Name", "")
        rows.append({"name": name, "project": projects.get(name, ""), "mem": parse_size(used) or 0,
                     "limit": parse_size(limit) or 0, "percent": float(str(d.get("MemPerc", "0")).rstrip("%") or 0)})
    return sorted(rows, key=lambda r: -r["mem"])


@processes_bp.route("/api/memory")
@login_required
def memory():
    now = time.time()
    if _mem_cache["data"] is None or now - _mem_cache["at"] > 10:  # leer la memoria de cada proceso tiene costo
        programs, rest, pss = by_program()
        _mem_cache.update(at=now, data={"overview": memory_overview(), "programs": programs, "rest": rest, "pss": pss})
    return jsonify({"ok": True, **_mem_cache["data"]})


@processes_bp.route("/api/memory/docker")
@login_required
def memory_docker():
    rows = docker_memory()
    return jsonify({"ok": rows is not None, "containers": rows or []})


def _fmt_duration(seconds):
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
