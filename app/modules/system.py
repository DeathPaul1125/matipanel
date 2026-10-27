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
        return {"error": err.strip() or "timedatectl no disponible", "epoch": time.time()}
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    _, ts, _ = run(["timedatectl", "show-timesync", "--property=ServerName", "--value"], timeout=10)
    return {
        "timezone": kv.get("Timezone", ""),
        "ntp": kv.get("NTP") == "yes",
        "can_ntp": kv.get("CanNTP") == "yes",
        "synced": kv.get("NTPSynchronized") == "yes",
        "local_time": kv.get("TimeUSec", ""),
        "rtc_local": kv.get("LocalRTC") == "yes",
        "ntp_server": ts.strip(),
        "epoch": time.time(),  # el navegador calcula su desfase y muestra el reloj del servidor en vivo
        "utc_offset": -time.altzone if time.localtime().tm_isdst > 0 else -time.timezone,
    }


def _dmi(name):
    v = (_read(f"/sys/class/dmi/id/{name}", 200) or "").strip()
    return "" if v.startswith("(") or v in ("To be filled by O.E.M.", "Default string", "System Product Name") else v


def _os_release():
    kv = {}
    for line in (_read("/etc/os-release") or "").splitlines():
        k, _, v = line.partition("=")
        kv[k] = v.strip().strip('"')
    return kv


def _scheduled_shutdown():
    """Apagado o reinicio programado con «shutdown» (systemd lo deja en /run/systemd/shutdown/scheduled)."""
    kv = dict(line.split("=", 1) for line in (_read("/run/systemd/shutdown/scheduled", 4000) or "").splitlines()
              if "=" in line)
    if not kv.get("USEC", "").isdigit():
        return None
    return {"at": int(kv["USEC"]) / 1e6, "mode": kv.get("MODE", ""), "message": kv.get("WALL_MESSAGE", "")}


def _locale():
    code, out, _ = run(["localectl", "status"], timeout=10)
    kv = {}
    for line in out.splitlines() if code == 0 else []:
        k, _, v = line.partition(":")
        kv[k.strip()] = v.strip()
    return {"lang": kv.get("System Locale", "").replace("LANG=", ""), "keymap": kv.get("VC Keymap", "") or kv.get("X11 Layout", "")}


def _overview():
    osr = _os_release()
    _, virt, _ = run(["systemd-detect-virt"], timeout=10)
    try:
        load = os.getloadavg()
    except (OSError, AttributeError):
        load = (0.0, 0.0, 0.0)
    reboot = os.path.exists("/var/run/reboot-required")
    pkgs = [p for p in (_read("/var/run/reboot-required.pkgs", 20000) or "").split() if p] if reboot else []
    vendor, product = _dmi("sys_vendor"), _dmi("product_name")
    return {
        "os_name": osr.get("PRETTY_NAME") or detect()["name"], "os_id": osr.get("ID", ""),
        "os_version": osr.get("VERSION", ""), "codename": osr.get("VERSION_CODENAME", ""),
        "virt": virt.strip() if virt.strip() and virt.strip() != "none" else "",
        "machine": " ".join(x for x in (vendor, product) if x),
        "boot": psutil.boot_time(), "load": [round(x, 2) for x in load], "procs": len(psutil.pids()),
        "users": len(psutil.users()), "reboot_required": reboot, "reboot_pkgs": sorted(set(pkgs))[:30],
        "scheduled": _scheduled_shutdown(), "locale": _locale(), "python": platform.python_version(),
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
        "overview": _overview(),
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

    last = out_of(["last", "-n", "60", "-F", "-w", "-i"])
    if "invalid option" in last or "unrecognized" in last or "unknown option" in last:
        last = out_of(["last", "-n", "60", "-F"])
    lastb = out_of(["lastb", "-n", "30", "-F", "-w", "-i"]) if shutil.which("lastb") else ""
    _, who, _ = run(["who", "-u"], timeout=10)
    return jsonify({"who": parse_who(who), "last": parse_last(last), "lastb": parse_last(lastb) if lastb else [],
                    "has_lastb": bool(shutil.which("lastb")), "last_raw": last if not parse_last(last) else "",
                    "ssh_failed": ssh_failures()})


WHO_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\d{4}-\d\d-\d\d \d\d:\d\d|\w{3}\s+\d+\s+\d\d:\d\d)\s+(\S+)?\s*(\d+)?\s*(?:\((.*)\))?")
DATE = r"\w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4}"
LAST_RE = re.compile(rf"^(\S+)\s+(system boot|\S+)\s+(.*?)\s*({DATE})\s*(?:-\s*({DATE}|crash|down)\s*\(([^)]*)\)|(.*))$")


def parse_who(out):
    rows = []
    for line in out.splitlines():
        m = WHO_RE.match(line)
        if m:
            rows.append({"user": m[1], "tty": m[2], "since": m[3], "idle": m[4] or "", "pid": m[5] or "", "from": m[6] or ""})
    return rows


def parse_last(out):
    """Salida de `last -F` (util-linux o wtmpdb) → filas; las líneas que no encajan se ignoran."""
    rows = []
    for line in out.splitlines():
        m = LAST_RE.match(line.rstrip())
        if not m or m[1] in ("wtmp", "btmp", "wtmpdb"):
            continue
        boot = m[2] == "system boot"
        rows.append({"user": m[1], "tty": m[2], "from": "" if boot else m[3].strip(), "kernel": m[3].strip() if boot else "",
                     "start": m[4], "end": m[5] or "", "duration": m[6] or "", "status": (m[7] or "").strip(),
                     "boot": boot})
    return rows


SSH_FAIL_RE = re.compile(r"(?:Failed \S+ for (?:invalid user )?(\S+)|Invalid user (\S*)) from (\S+) port")


def ssh_failures(days=7):
    """Intentos fallidos de SSH de los últimos días, por IP (del journal: Debian 13 ya no trae lastb)."""
    code, out, _ = run(["journalctl", "--no-pager", "-o", "short-iso", "--since", f"-{days}d", "-g",
                        "Failed password|Failed publickey|Invalid user", "_COMM=sshd", "_COMM=sshd-session"], timeout=30)
    if code not in (0, 1):
        return {"error": "No se pudo leer el journal", "ips": []}
    ips = {}
    for line in out.splitlines()[-20000:]:
        m = SSH_FAIL_RE.search(line)
        if not m:
            continue
        ip, user = m[3], m[1] or m[2] or "?"
        e = ips.setdefault(ip, {"ip": ip, "count": 0, "users": set(), "last": ""})
        e["count"] += 1
        e["users"].add(user)
        e["last"] = line.split(" ", 1)[0]
    top = sorted(ips.values(), key=lambda e: -e["count"])[:50]
    return {"days": days, "total": sum(e["count"] for e in ips.values()), "ips": [
        {**e, "users": sorted(e["users"])[:8]} for e in top]}


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


@system_bp.route("/api/power/schedule", methods=["POST"])
@login_required
def power_schedule():
    """Reinicio o apagado programado con «shutdown» (avisa a los usuarios conectados y se puede cancelar)."""
    data = request.get_json(silent=True) or {}
    act, when, msg = data.get("action"), str(data.get("when", "")).strip(), " ".join(str(data.get("message", "")).split())
    if act not in ("reboot", "poweroff"):
        return _err("Acción no permitida")
    if data.get("confirm") != socket.gethostname():
        return _err("La confirmación no coincide con el hostname")
    if re.fullmatch(r"\d{1,4}", when) and 1 <= int(when) <= 10080:
        spec = f"+{int(when)}"
    elif re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", when):
        spec = when
    else:
        return _err("Indica minutos (1 a 10080) o una hora HH:MM")
    if len(msg) > 200 or not all(c.isprintable() for c in msg):
        return _err("Mensaje inválido (hasta 200 caracteres)")
    code, out, err = run(["shutdown", "-r" if act == "reboot" else "-P", spec, *([msg] if msg else [])], timeout=15)
    if code != 0:
        return _err(err.strip() or out.strip() or "shutdown falló", 500)
    return jsonify({"ok": True, "scheduled": _scheduled_shutdown()})


@system_bp.route("/api/power/cancel", methods=["POST"])
@login_required
def power_cancel():
    code, out, err = run(["shutdown", "-c"], timeout=15)
    if code != 0:
        return _err(err.strip() or "No se pudo cancelar", 500)
    return jsonify({"ok": True})


@system_bp.route("/api/hardware")
@login_required
def hardware():
    """Detalle del procesador (lscpu) y dispositivos PCI/USB si las herramientas existen."""
    code, out, _ = run(["lscpu", "-J"], timeout=10)
    cpu = {}
    try:
        for item in json.loads(out).get("lscpu", []) if code == 0 else []:
            cpu[item.get("field", "").rstrip(":")] = item.get("data", "")
            for ch in item.get("children", []) or []:
                cpu[ch.get("field", "").rstrip(":")] = ch.get("data", "")
    except (ValueError, AttributeError):
        cpu = {}
    keys = ["Model name", "Vendor ID", "Architecture", "CPU(s)", "Socket(s)", "Core(s) per socket", "Core(s) per cluster",
            "Thread(s) per core", "CPU max MHz", "CPU min MHz", "BogoMIPS", "Virtualization", "Hypervisor vendor",
            "Virtualization type", "L1d", "L1i", "L2", "L3", "NUMA node(s)"]
    def lines(cmd):
        if not shutil.which(cmd[0]):
            return None
        c, o, _ = run(cmd, timeout=15)
        return o.splitlines()[:200] if c == 0 else []
    return jsonify({"cpu": [[k, cpu[k]] for k in keys if cpu.get(k)], "pci": lines(["lspci"]), "usb": lines(["lsusb"])})


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
