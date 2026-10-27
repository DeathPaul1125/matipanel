"""Tráfico de red: ancho de banda por interfaz (en vivo e historial), quién consume (muestreo con
tcpdump), capturas .pcap para Wireshark y alertas de consumo en la barra de salud.

El muestreador del dashboard llama a sample() cada 5 s: el último minuto por interfaz queda en memoria
(alertas, valores actuales) y cada 30 s se guarda una fila por interfaz en metrics.db (7 días).
"""
import ipaddress
import json
import os
import re
import shutil
import threading
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import psutil
from flask import Blueprint, render_template, jsonify, request, send_file
from flask_login import login_required

from .. import metrics
from ..util import run
from .files import _disposition
from .packages import apt_get

traffic_bp = Blueprint("traffic", __name__)

IFACE_RE = re.compile(r"[A-Za-z0-9_.:@-]{1,15}")
CAP_NAME_RE = re.compile(r"\d{8}-\d{6}-[A-Za-z0-9_.@-]{1,20}\.pcap")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
BPF_RE = re.compile(r"[A-Za-z0-9 .:/()!&|<>=\[\]*+-]{0,300}")
PERSIST_EVERY = 30
RANGES = (300, 3600, 21600, 86400, 604800)
CAP_DIR = Path(os.environ.get("MATIPANEL_CAPTURES", "/var/lib/matipanel/captures"))
CAP_KEEP, CAP_MAX_BYTES = 20, 1024 ** 3
CONFIG_PATH = Path(os.environ.get("MATIPANEL_TRAFFIC", "/var/lib/matipanel/traffic.json"))
DEFAULT_ERRORS = 60      # errores de la interfaz por minuto que disparan aviso
LINK_PCT = 0.9           # sin umbral propio: aviso al 90 % de la velocidad del enlace

_recent = defaultdict(lambda: deque(maxlen=12))  # último minuto por interfaz (muestras de 5 s)
_state = {"prev": None, "persist": None}
_lock = threading.Lock()


def tracked(name):
    """Interfaces que se miden: todas menos loopback y los extremos veth de cada contenedor."""
    return name != "lo" and not name.startswith("veth")


def _rates(a, b, dt):
    d = lambda x, y: max(0, y - x) / dt  # noqa: E731 — un contador que vuelve a cero da 0, no negativo
    return {"rx": d(a.bytes_recv, b.bytes_recv), "tx": d(a.bytes_sent, b.bytes_sent),
            "rxp": d(a.packets_recv, b.packets_recv), "txp": d(a.packets_sent, b.packets_sent),
            "errs": d(a.errin + a.errout, b.errin + b.errout), "drops": d(a.dropin + a.dropout, b.dropin + b.dropout)}


def sample(t):
    """Lo llama el muestreador del dashboard cada 5 s."""
    c = psutil.net_io_counters(pernic=True)
    prev = _state["prev"]
    if prev:
        pt, pc = prev
        dt = max(t - pt, 0.001)
        with _lock:
            for name, n in c.items():
                if tracked(name) and name in pc:
                    _recent[name].append({"t": round(t, 1), **_rates(pc[name], n, dt)})
            for name in [k for k in _recent if k not in c]:
                del _recent[name]
    _state["prev"] = (t, c)
    pers = _state["persist"]
    if pers is None:
        _state["persist"] = (t, c)
    elif t - pers[0] >= PERSIST_EVERY:
        dt = t - pers[0]
        metrics.insert_ifaces(round(t, 1), {name: _rates(pers[1][name], n, dt) for name, n in c.items()
                                            if tracked(name) and name in pers[1]})
        _state["persist"] = (t, c)


# ── Alertas ──────────────────────────────────────────────────────────────

def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    ifaces = data.get("ifaces") if isinstance(data, dict) else None
    out = {}
    for name, c in (ifaces or {}).items():
        if IFACE_RE.fullmatch(str(name)) and isinstance(c, dict):
            out[name] = {k: c.get(k) for k in ("mbps", "errors", "drops")
                         if c.get(k) is None or (isinstance(c.get(k), (int, float)) and c.get(k) >= 0)}
    return {"ifaces": out}


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def thresholds(name, cfg=None, stats=None):
    """Umbrales efectivos de una interfaz: (Mbit/s o 0 = sin aviso, errores/min, descartes/min)."""
    c = (cfg or load_config())["ifaces"].get(name, {})
    mbps = c.get("mbps")
    if mbps is None:
        st = (stats if stats is not None else psutil.net_if_stats()).get(name)
        mbps = round(st.speed * LINK_PCT) if st and st.speed > 0 else 0
    errors = DEFAULT_ERRORS if c.get("errors") is None else c["errors"]
    return mbps, errors, c.get("drops") or 0


def health_issues():
    """Avisos para la barra de salud: uso sostenido (1 min) sobre el umbral y errores por minuto."""
    cfg, stats = load_config(), psutil.net_if_stats()
    with _lock:
        snap = {k: list(v) for k, v in _recent.items()}
    out = []
    for name, pts in sorted(snap.items()):
        if len(pts) < 6:  # al menos 30 s de datos
            continue
        mbps, errors, drops = thresholds(name, cfg, stats)
        window_min = len(pts) * 5 / 60
        if mbps:
            avg = sum(max(p["rx"], p["tx"]) for p in pts) / len(pts) * 8 / 1e6
            if avg >= mbps:
                out.append(("warn", f"{name} al {avg:.0f} Mbit/s"))
        if errors:
            n = sum(p["errs"] * 5 for p in pts) / window_min
            if n >= errors:
                out.append(("warn", f"{name}: {n:.0f} errores de red por minuto"))
        if drops:
            n = sum(p["drops"] * 5 for p in pts) / window_min
            if n >= drops:
                out.append(("warn", f"{name}: {n:.0f} paquetes descartados por minuto"))
    return out


# ── Análisis de paquetes (tcpdump) ───────────────────────────────────────

LINE_RE = re.compile(r"length (\d+): (\S+) > (\S+?):? (.*)$")


def parse_line(line):
    """Línea de `tcpdump -nn -e -q -t` → (bytes, proto, origen, puerto, destino, puerto) o None."""
    m = LINE_RE.search(line)
    if not m:
        return None
    size, src, dst, rest = int(m[1]), m[2], m[3], m[4].lower()
    proto = "tcp" if rest.startswith("tcp") else "udp" if rest.startswith("udp") else \
        "icmp" if "icmp" in rest else "otro"

    def split(addr):
        if proto in ("tcp", "udp"):
            host, _, port = addr.rpartition(".")
            if host and port.isdigit():
                return host, int(port)
        return addr, None
    sh, sp = split(src)
    dh, dp = split(dst)
    return size, proto, sh, sp, dh, dp


def local_addrs(iface=None):
    out = set()
    for name, addrs in psutil.net_if_addrs().items():
        if iface in (None, "any") or name == iface:
            for a in addrs:
                if a.family.name in ("AF_INET", "AF_INET6"):
                    out.add(a.address.split("%")[0])
    return out


def _port_owners():
    """{(proto, puerto local): "proceso (pid)"} de las conexiones abiertas."""
    owners = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        return owners
    names = {}
    for c in conns:
        if not c.laddr or not c.pid:
            continue
        if c.pid not in names:
            try:
                names[c.pid] = f"{psutil.Process(c.pid).name()} ({c.pid})"
            except psutil.Error:
                names[c.pid] = f"pid {c.pid}"
        proto = "tcp" if c.type.name == "SOCK_STREAM" else "udp"
        owners.setdefault((proto, c.laddr.port), names[c.pid])
    return owners


def aggregate(lines, local, seconds, owners=None, top=15):
    """Resume paquetes: equipos, servicios (puertos), procesos y conversaciones que más tráfico mueven."""
    hosts = defaultdict(lambda: [0, 0, 0])       # ip → [bytes enviados, bytes recibidos, paquetes]
    services = defaultdict(lambda: [0, 0])       # proto/puerto → [bytes, paquetes]
    procs = defaultdict(lambda: [0, 0])
    flows = defaultdict(lambda: [0, 0])
    protos = defaultdict(int)
    total = packets = 0
    owners = owners or {}
    for line in lines:
        p = parse_line(line)
        if not p:
            continue
        size, proto, sh, sp, dh, dp = p
        total += size
        packets += 1
        protos[proto] += size
        if sh not in local:
            hosts[sh][0] += size
            hosts[sh][2] += 1
        if dh not in local:
            hosts[dh][1] += size
            hosts[dh][2] += 1
        if sp is not None and dp is not None:
            port = min(sp, dp)
            services[f"{proto}/{port}"][0] += size
            services[f"{proto}/{port}"][1] += 1
            lport = sp if sh in local else dp if dh in local else None
            if lport is not None and (proto, lport) in owners:
                procs[owners[(proto, lport)]][0] += size
                procs[owners[(proto, lport)]][1] += 1
        a, b = (f"{sh}:{sp}" if sp is not None else sh), (f"{dh}:{dp}" if dp is not None else dh)
        flows[(proto, *sorted((a, b)))][0] += size
        flows[(proto, *sorted((a, b)))][1] += 1
    sec = max(seconds, 1)
    rank = lambda d, key=lambda v: v[0]: sorted(d.items(), key=lambda kv: -key(kv[1]))[:top]  # noqa: E731
    return {
        "seconds": seconds, "packets": packets, "bytes": total, "rate": total / sec,
        "hosts": [{"host": h, "sent": v[0] / sec, "recv": v[1] / sec, "packets": v[2]}
                  for h, v in rank(hosts, lambda v: v[0] + v[1])],
        "services": [{"service": k, "rate": v[0] / sec, "packets": v[1]} for k, v in rank(services)],
        "processes": [{"process": k, "rate": v[0] / sec, "packets": v[1]} for k, v in rank(procs)],
        "flows": [{"proto": k[0], "a": k[1], "b": k[2], "rate": v[0] / sec, "packets": v[1]} for k, v in rank(flows)],
        "protocols": {k: v / sec for k, v in sorted(protos.items(), key=lambda kv: -kv[1])},
    }


def _valid_iface(name):
    return isinstance(name, str) and (name == "any" or (IFACE_RE.fullmatch(name) and name in psutil.net_if_addrs()))


def _has_tcpdump():
    return shutil.which("tcpdump") is not None


def build_filter(d):
    """Expresión BPF a partir de campos validados (+ filtro avanzado con caracteres limitados)."""
    parts = []
    proto = d.get("proto") or ""
    if proto not in ("", "tcp", "udp", "icmp", "arp"):
        raise ValueError("Protocolo inválido")
    if proto == "icmp":
        parts.append("(icmp or icmp6)")
    elif proto:
        parts.append(proto)
    host = str(d.get("host") or "").strip()
    if host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not HOST_RE.fullmatch(host):
                raise ValueError("Equipo inválido: una IP o un nombre")
        parts.append(f"host {host}")
    net = str(d.get("net") or "").strip()
    if net:
        try:
            parts.append(f"net {ipaddress.ip_network(net, strict=False)}")
        except ValueError:
            raise ValueError("Red inválida (ej.: 192.168.1.0/24)")
    port = str(d.get("port") or "").strip()
    if port:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("Puerto inválido (1-65535)")
        parts.append(f"port {int(port)}")
    adv = " ".join(str(d.get("expr") or "").split())
    if adv:
        if not BPF_RE.fullmatch(adv):
            raise ValueError("El filtro avanzado tiene caracteres no permitidos")
        parts.append(f"({adv})")
    return " and ".join(parts)


# ── Páginas y API ────────────────────────────────────────────────────────

@traffic_bp.route("/")
@login_required
def index():
    return render_template("traffic.html", has_tcpdump=_has_tcpdump(), ranges=RANGES,
                           apt=shutil.which("apt-get") is not None)


@traffic_bp.route("/api/ifaces")
@login_required
def ifaces():
    stats, addrs, io = psutil.net_if_stats(), psutil.net_if_addrs(), psutil.net_io_counters(pernic=True)
    cfg = load_config()
    with _lock:
        last = {k: v[-1] for k, v in _recent.items() if v}
    out = []
    for name in sorted(io):
        if not tracked(name):
            continue
        st, n = stats.get(name), io[name]
        ips = [a.address.split("%")[0] for a in addrs.get(name, []) if a.family.name in ("AF_INET", "AF_INET6")]
        mac = next((a.address for a in addrs.get(name, []) if a.family.name in ("AF_LINK", "AF_PACKET")), "")
        mbps, errors, drops = thresholds(name, cfg, stats)
        out.append({"name": name, "up": bool(st and st.isup), "speed": st.speed if st else 0, "mtu": st.mtu if st else 0,
                    "ips": ips, "mac": mac,
                    "kind": "docker" if name == "docker0" or name.startswith("br-") else "bridge" if name.startswith(("virbr", "br")) else "",
                    "totals": {"rx": n.bytes_recv, "tx": n.bytes_sent, "errs": n.errin + n.errout, "drops": n.dropin + n.dropout},
                    "now": last.get(name), "alerts": cfg["ifaces"].get(name, {}),
                    "effective": {"mbps": mbps, "errors": errors, "drops": drops}})
    return jsonify({"ifaces": out, "auto_pct": round(LINK_PCT * 100), "default_errors": DEFAULT_ERRORS})


@traffic_bp.route("/api/counters")
@login_required
def counters():
    """Contadores crudos: el navegador calcula la velocidad entre dos lecturas (gráfica en vivo cada 2 s)."""
    import time
    io = psutil.net_io_counters(pernic=True)
    return jsonify({"t": time.time(), "c": {k: [n.bytes_recv, n.bytes_sent, n.packets_recv, n.packets_sent,
                                                 n.errin + n.errout, n.dropin + n.dropout]
                                             for k, n in io.items() if tracked(k)}})


@traffic_bp.route("/api/history")
@login_required
def history():
    iface = request.args.get("iface", "")
    rng = request.args.get("range", 3600, type=int)
    if not IFACE_RE.fullmatch(iface) or rng not in RANGES:
        return jsonify({"error": "Parámetros inválidos"}), 400
    bucket, points = metrics.query_iface(iface, rng)
    return jsonify({"bucket": bucket, "points": points})


@traffic_bp.route("/api/top")
@login_required
def top():
    """Muestra de unos segundos de tráfico de una interfaz: quién consume."""
    iface = request.args.get("iface", "")
    seconds = request.args.get("seconds", 5, type=int)
    if not _valid_iface(iface) or seconds not in (3, 5, 10, 20):
        return jsonify({"ok": False, "error": "Interfaz o duración inválida"}), 400
    if not _has_tcpdump():
        return jsonify({"ok": False, "error": "Falta tcpdump: instálalo desde esta pantalla"}), 409
    # -s 128: solo cabeceras (bastan para el resumen); -c tope de paquetes para no saturar el panel
    code, out, err = run(["timeout", "-s", "INT", str(seconds), "tcpdump", "-i", iface, "-nn", "-e", "-q", "-t",
                          "-l", "-s", "128", "-c", "60000"], timeout=seconds + 10)
    if code not in (0, 124) and not out.strip():
        return jsonify({"ok": False, "error": (err or "tcpdump falló").strip()[-500:]}), 500
    return jsonify({"ok": True, **aggregate(out.splitlines(), local_addrs(iface), seconds, _port_owners())})


def _captures():
    try:
        files = [f for f in CAP_DIR.iterdir() if CAP_NAME_RE.fullmatch(f.name) and f.is_file() and not f.is_symlink()]
    except OSError:
        return []
    return sorted(files, key=lambda f: f.name, reverse=True)


def _rotate():
    files, total = _captures(), 0
    for i, f in enumerate(files):
        total += f.stat().st_size
        if i >= CAP_KEEP or total > CAP_MAX_BYTES:
            f.unlink(missing_ok=True)


def summarize(path):
    """Resumen de un .pcap; la duración sale de la primera y la última marca de tiempo (-tt)."""
    code, out, err = run(["tcpdump", "-nn", "-e", "-q", "-tt", "-r", str(path), "-c", "200000"], timeout=90)
    lines = out.splitlines()
    stamps = [float(ln.split(" ", 1)[0]) for ln in (lines[:1] + lines[-1:]) if re.match(r"\d+\.\d+ ", ln)]
    seconds = max(1.0, stamps[-1] - stamps[0]) if len(stamps) == 2 else 1.0
    data = aggregate(lines, local_addrs(), round(seconds, 1))
    _, prev, _ = run(["tcpdump", "-nn", "-t", "-r", str(path), "-c", "200"], timeout=30)
    data["preview"] = prev.splitlines()[:200]
    return data


@traffic_bp.route("/api/capture", methods=["POST"])
@login_required
def capture():
    d = request.get_json(silent=True) or {}
    iface = d.get("iface", "")
    try:
        seconds, count = int(d.get("seconds", 30)), int(d.get("count", 10000))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Duración o cantidad inválida"}), 400
    if not _valid_iface(iface):
        return jsonify({"ok": False, "error": "Interfaz inválida"}), 400
    if not 1 <= seconds <= 300 or not 1 <= count <= 200000:
        return jsonify({"ok": False, "error": "Duración de 1 a 300 s y de 1 a 200 000 paquetes"}), 400
    if not _has_tcpdump():
        return jsonify({"ok": False, "error": "Falta tcpdump: instálalo desde esta pantalla"}), 409
    try:
        expr = build_filter(d)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    CAP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CAP_DIR, 0o700)
    name = f"{datetime.now():%Y%m%d-%H%M%S}-{iface.replace(':', '_')}.pcap"
    path = CAP_DIR / name
    # -Z root: tcpdump suelta privilegios al usuario «tcpdump» y no podría escribir en la carpeta (0700 root).
    # -U escribe cada paquete al momento; timeout -s INT deja que cierre el archivo bien.
    cmd = ["timeout", "-s", "INT", str(seconds), "tcpdump", "-i", iface, "-nn", "-U", "-Z", "root",
           "-w", str(path), "-c", str(count)] + (["--", expr] if expr else [])
    code, out, err = run(cmd, timeout=seconds + 20)
    if not path.is_file() or path.is_symlink() or (code not in (0, 124) and path.stat().st_size <= 24):
        path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": (err or out or "tcpdump falló").strip()[-800:]}), 500
    os.chmod(path, 0o600)
    _rotate()
    return jsonify({"ok": True, "name": name, "summary": summarize(path)})


@traffic_bp.route("/api/captures")
@login_required
def captures():
    return jsonify({"captures": [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
                                 for f in _captures()]})


def _capture_path(name):
    if not CAP_NAME_RE.fullmatch(name or ""):
        return None
    p = CAP_DIR / name
    return p if p.is_file() and not p.is_symlink() else None


@traffic_bp.route("/api/captures/<name>")
@login_required
def capture_download(name):
    p = _capture_path(name)
    if not p:
        return jsonify({"error": "No existe"}), 404
    resp = send_file(str(p), mimetype="application/vnd.tcpdump.pcap", as_attachment=True, download_name=name)
    resp.headers["Content-Disposition"] = _disposition(name)
    return resp


@traffic_bp.route("/api/captures/<name>/summary")
@login_required
def capture_summary(name):
    p = _capture_path(name)
    if not p:
        return jsonify({"ok": False, "error": "No existe"}), 404
    return jsonify({"ok": True, "name": name, "summary": summarize(p)})


@traffic_bp.route("/api/captures/<name>/delete", methods=["POST"])
@login_required
def capture_delete(name):
    p = _capture_path(name)
    if not p:
        return jsonify({"ok": False, "error": "No existe"}), 404
    p.unlink()
    return jsonify({"ok": True})


@traffic_bp.route("/api/alerts", methods=["POST"])
@login_required
def alerts():
    """{"ifaces": {"eth0": {"mbps": null|0|N, "errors": null|0|N, "drops": 0|N}}}  null = automático."""
    d = request.get_json(silent=True) or {}
    raw = d.get("ifaces")
    if not isinstance(raw, dict) or len(raw) > 64:
        return jsonify({"ok": False, "error": "Formato inválido"}), 400
    cfg = {"ifaces": {}}
    for name, c in raw.items():
        if not IFACE_RE.fullmatch(str(name)) or not isinstance(c, dict):
            return jsonify({"ok": False, "error": f"Interfaz inválida: {str(name)[:20]}"}), 400
        row = {}
        for k, hi in (("mbps", 1_000_000), ("errors", 1_000_000), ("drops", 1_000_000)):
            v = c.get(k)
            if v in (None, ""):
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"Valor inválido en {name}"}), 400
            if not 0 <= v <= hi:
                return jsonify({"ok": False, "error": f"Valor fuera de rango en {name}"}), 400
            row[k] = int(v) if v.is_integer() else v
        if row:
            cfg["ifaces"][name] = row
    try:
        save_config(cfg)
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True})


@traffic_bp.route("/api/install", methods=["POST"])
@login_required
def install():
    if not shutil.which("apt-get"):
        return jsonify({"ok": False, "error": "Instala tcpdump con el gestor de paquetes de tu sistema"}), 400
    ok, out, msg = apt_get("install", "--", "tcpdump", timeout=600)
    return jsonify({"ok": ok, "error": "" if ok else msg, "output": out[-3000:]}), (200 if ok else 500)


if __name__ == "__main__":
    lines = [
        "00:15:5d:01:02:03 > 00:15:5d:04:05:06, IPv4, length 1514: 192.168.1.10.22 > 192.168.1.20.51234: tcp 1448",
        "00:15:5d:04:05:06 > 00:15:5d:01:02:03, IPv4, length 66: 192.168.1.20.51234 > 192.168.1.10.22: tcp 0",
        "00:15:5d:01:02:03 > 00:15:5d:04:05:06, IPv4, length 82: 192.168.1.10.40000 > 8.8.8.8.53: UDP, length 40",
        "00:15:5d:01:02:03 > 00:15:5d:04:05:06, IPv6, length 86: fe80::1.546 > ff02::1:2.547: UDP, length 32",
        "00:15:5d:01:02:03 > 00:15:5d:04:05:06, IPv4, length 98: 192.168.1.10 > 1.1.1.1: ICMP echo request, id 1, seq 1, length 64",
        "00:15:5d:01:02:03 > ff:ff:ff:ff:ff:ff, ARP, length 42: Request who-has 192.168.1.1 tell 192.168.1.10, length 28",
    ]
    assert parse_line(lines[0]) == (1514, "tcp", "192.168.1.10", 22, "192.168.1.20", 51234)
    assert parse_line(lines[3])[1:] == ("udp", "fe80::1", 546, "ff02::1:2", 547)
    assert parse_line(lines[4]) == (98, "icmp", "192.168.1.10", None, "1.1.1.1", None) and parse_line(lines[5]) is None
    r = aggregate(lines, {"192.168.1.10"}, 2, {("tcp", 22): "sshd (812)"})
    assert r["packets"] == 5 and r["bytes"] == 1514 + 66 + 82 + 86 + 98 and r["hosts"][0]["host"] == "192.168.1.20"
    assert r["hosts"][0]["recv"] == 1514 / 2 and r["hosts"][0]["sent"] == 66 / 2
    assert r["services"][0]["service"] == "tcp/22" and r["processes"] == [{"process": "sshd (812)", "rate": (1514 + 66) / 2, "packets": 2}]
    assert r["flows"][0]["a"] == "192.168.1.10:22" and r["flows"][0]["packets"] == 2
    assert build_filter({"proto": "tcp", "host": "10.0.0.5", "port": "443"}) == "tcp and host 10.0.0.5 and port 443"
    assert build_filter({"net": "192.168.1.7/24", "expr": "not port 22"}) == "net 192.168.1.0/24 and (not port 22)"
    for bad in ({"proto": "tcp;id"}, {"host": "a b"}, {"port": "99999"}, {"expr": "port 1; rm"}, {"net": "x"}):
        try:
            build_filter(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    print("ok")
