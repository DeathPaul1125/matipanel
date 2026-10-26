"""Sistema: hostname, hora, sesiones, apagado/reinicio, hardware y archivos de configuración."""
import json
import os
import platform
import re
import shutil
import socket
import time
import psutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..distro import detect
from ..util import run, bytes_to_human

system_bp = Blueprint("system", __name__)

HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
HOSTS = "/etc/hosts"
BACKUP_SUFFIX = ".matipanel.bak"

CONFIG_FILES = [
    ("/etc/hosts", "Resolución local de nombres"),
    ("/etc/resolv.conf", "Servidores DNS"),
    ("/etc/ssh/sshd_config", "Servidor SSH"),
    ("/etc/network/interfaces", "Interfaces de red (ifupdown)"),
    ("/etc/fstab", "Montajes al arranque"),
    ("/etc/sysctl.conf", "Parámetros del kernel"),
]


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                key, _, val = line.partition(":")
                if key.strip() in ("model name", "Model", "Hardware") and val.strip():
                    return val.strip()
    except OSError:
        pass
    return platform.processor() or "?"


def _read(path, limit=200_000):
    try:
        with open(path, errors="replace") as f:
            return f.read(limit)
    except OSError as e:
        return None if isinstance(e, FileNotFoundError) else f"(no se pudo leer: {e})"


def _timedate():
    code, out, err = run(["timedatectl", "show"], timeout=10)
    if code != 0:
        return {"error": err.strip() or "timedatectl no disponible"}
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {
        "timezone": kv.get("Timezone", ""),
        "ntp": kv.get("NTP") == "yes",
        "can_ntp": kv.get("CanNTP") == "yes",
        "synced": kv.get("NTPSynchronized") == "yes",
        "local_time": kv.get("TimeUSec", ""),
    }


@system_bp.route("/")
@login_required
def index():
    return render_template("system.html")


@system_bp.route("/api/info")
@login_required
def info():
    mem, swap = psutil.virtual_memory(), psutil.swap_memory()
    code, out, err = run(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL"], timeout=10)
    try:
        disks = json.loads(out).get("blockdevices", []) if code == 0 else []
    except json.JSONDecodeError:
        disks = []
    return jsonify({
        "hostname": socket.gethostname(),
        "os": detect()["name"],
        "kernel": platform.release(),
        "arch": platform.machine(),
        "uptime": int(time.time() - psutil.boot_time()),
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "mem_total": bytes_to_human(mem.total), "mem_used": bytes_to_human(mem.used), "mem_pct": mem.percent,
        "swap_total": bytes_to_human(swap.total), "swap_used": bytes_to_human(swap.used), "swap_pct": swap.percent,
        "disks": disks,
        "lsblk_error": "" if code == 0 else err.strip(),
        "fstab": _read("/etc/fstab") or "",
        "time": _timedate(),
        "configs": [{"path": p, "desc": d, "exists": os.path.exists(p)} for p, d in CONFIG_FILES],
    })


@system_bp.route("/api/timezones")
@login_required
def timezones():
    code, out, err = run(["timedatectl", "list-timezones"], timeout=10)
    if code != 0:
        return jsonify({"error": err.strip() or "No se pudo listar"}), 500
    return jsonify({"timezones": out.split()})


@system_bp.route("/api/sessions")
@login_required
def sessions():
    def out_of(cmd):
        code, out, err = run(cmd, timeout=10)
        return out if code == 0 else (err.strip() or f"{cmd[0]} falló")

    last = out_of(["last", "-n", "50", "-F"])
    if "invalid option" in last or "unrecognized" in last:
        last = out_of(["last", "-n", "50"])
    lastb = out_of(["lastb", "-n", "30", "-F"]) if shutil.which("lastb") else "lastb no disponible"
    return jsonify({"who": out_of(["who"]) or "(nadie conectado)", "last": last, "lastb": lastb})


def _update_hosts(new):
    """Reescribe la línea 127.0.1.1 (convención Debian), guardando copia en /etc/hosts.matipanel.bak."""
    short = new.split(".")[0]
    entry = f"127.0.1.1\t{new} {short}" if short != new else f"127.0.1.1\t{new}"
    with open(HOSTS) as f:
        lines = f.read().splitlines()
    out, done = [], False
    for line in lines:
        if line.split()[:1] == ["127.0.1.1"]:
            if not done:
                out.append(entry)
                done = True
            continue  # elimina duplicados de 127.0.1.1
        out.append(line)
    if not done:
        idx = next((i + 1 for i, l in enumerate(out) if l.split()[:1] == ["127.0.0.1"]), len(out))
        out.insert(idx, entry)
    shutil.copy2(HOSTS, HOSTS + BACKUP_SUFFIX)
    with open(HOSTS, "w") as f:  # en el sitio: /etc/hosts puede ser un bind-mount
        f.write("\n".join(out) + "\n")


@system_bp.route("/api/hostname", methods=["POST"])
@login_required
def set_hostname():
    name = str((request.get_json(silent=True) or {}).get("hostname", "")).strip()
    if not HOSTNAME_RE.match(name):
        return _err("Hostname inválido (letras, números, guiones y puntos; RFC 1123)")
    code, out, err = run(["hostnamectl", "set-hostname", "--", name], timeout=15)
    if code != 0:
        return _err(err.strip() or "hostnamectl falló", 500)
    try:
        _update_hosts(name)
    except OSError as e:
        return jsonify({"ok": True, "warning": f"Hostname cambiado, pero no se pudo actualizar /etc/hosts: {e}"})
    return jsonify({"ok": True})


@system_bp.route("/api/timezone", methods=["POST"])
@login_required
def set_timezone():
    tz = str((request.get_json(silent=True) or {}).get("timezone", ""))
    if not TZ_RE.match(tz) or tz.startswith("-"):
        return _err("Zona horaria inválida")
    code, out, err = run(["timedatectl", "list-timezones"], timeout=10)
    if code != 0 or tz not in out.split():
        return _err("Zona horaria desconocida")
    code, out, err = run(["timedatectl", "set-timezone", "--", tz], timeout=15)
    if code != 0:
        return _err(err.strip() or "timedatectl falló", 500)
    return jsonify({"ok": True})


@system_bp.route("/api/ntp", methods=["POST"])
@login_required
def set_ntp():
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return _err("Valor inválido")
    code, out, err = run(["timedatectl", "set-ntp", "true" if enabled else "false"], timeout=15)
    if code != 0:
        return _err(err.strip() or "timedatectl falló (¿systemd-timesyncd instalado?)", 500)
    return jsonify({"ok": True})


@system_bp.route("/api/power", methods=["POST"])
@login_required
def power():
    data = request.get_json(silent=True) or {}
    act = data.get("action")
    if act not in ("reboot", "poweroff"):
        return _err("Acción no permitida")
    if data.get("confirm") != socket.gethostname():
        return _err("La confirmación no coincide con el hostname")
    # Se programa a 3 s para que la respuesta HTTP llegue antes de apagar.
    code, out, err = run(["systemd-run", "--on-active=3", "systemctl", act], timeout=15)
    if code != 0:
        return _err(err.strip() or "systemd-run falló", 500)
    return jsonify({"ok": True})


@system_bp.route("/api/ssh-restart", methods=["POST"])
@login_required
def ssh_restart():
    code, out, err = run(["sshd", "-t"], timeout=15)
    if code != 0:
        return _err("sshd -t falló, NO se reinició SSH:\n" + (err.strip() or out.strip()))
    unit = "ssh" if detect()["family"] == "debian" else "sshd"
    code, out, err = run(["systemctl", "restart", "--", unit], timeout=30)
    if code != 0:
        return _err(err.strip() or f"No se pudo reiniciar {unit}", 500)
    return jsonify({"ok": True})
