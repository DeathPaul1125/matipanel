"""Firewall: UFW y firewalld (gestión de reglas), nftables (Debian 12+ por defecto) e iptables."""
import ipaddress
import os
import re
import shlex
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..distro import detect
from ..modules.packages import apt_get
from ..util import run

firewall_bp = Blueprint("firewall", __name__)

NFT_CONF = "/etc/nftables.conf"
UFW_ACTIONS = {"allow", "deny", "reject", "limit"}
UFW_POLICIES = {"allow", "deny", "reject"}
COMMENT_RE = re.compile(r"^[A-Za-z0-9 _.,:/()-]{1,60}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _engine_is(name):
    return detect()["fw"] == name


def _port(spec, sep):
    """'80' o '8000<sep>8100' con puertos 1-65535. Retorna el texto o None."""
    m = re.fullmatch(r"(\d{1,5})(?:" + re.escape(sep) + r"(\d{1,5}))?", str(spec).strip())
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if not (1 <= lo <= hi <= 65535):
        return None
    return m.group(0)


@firewall_bp.route("/")
@login_required
def index():
    detect.cache_clear()
    return render_template("firewall.html", distro=detect(), nft_conf=NFT_CONF)


@firewall_bp.route("/api/status")
@login_required
def status():
    detect.cache_clear()
    d = detect()
    fw = d["fw"]
    if fw == "ufw":
        return jsonify(_ufw_status())
    if fw == "firewalld":
        return jsonify(_firewalld_status())
    if fw == "nftables":
        return jsonify(_nft_status())
    if fw == "iptables":
        return jsonify(_iptables_status())
    return jsonify({"installed": False, "engine": None, "can_install_ufw": d["pkg"] == "apt",
                    "error": "Ningún firewall detectado (ufw/firewalld/nftables/iptables)"})


@firewall_bp.route("/api/ufw/install", methods=["POST"])
@login_required
def ufw_install():
    """Instala UFW (APT). Queda inactivo: se activa desde el panel, que antes permite SSH y el panel."""
    if detect()["pkg"] != "apt":
        return _err("La instalación automática de UFW solo está disponible en Debian/Ubuntu")
    ok, out, msg = apt_get("install", "ufw", timeout=600)
    detect.cache_clear()
    if not ok:
        return _err(msg or "apt-get falló", 500)
    return jsonify({"ok": True, "output": out[-4000:]})


# ── UFW ──────────────────────────────────────────────────────────────

def _ufw_status():
    code, out, err = run(["ufw", "status", "verbose"], timeout=10)
    if code == 127:
        return {"installed": False, "engine": "ufw"}
    if code != 0:
        return {"installed": True, "engine": "ufw", "active": False, "error": err.strip() or "ufw falló"}
    res = {"installed": True, "engine": "ufw", "active": "status: active" in out.lower(),
           "raw": out, "defaults": {}, "rules": []}
    m = re.search(r"^Default:\s*(\w+) \(incoming\),\s*(\w+) \(outgoing\)", out, re.M)
    if m:
        res["defaults"] = {"incoming": m.group(1), "outgoing": m.group(2)}
    else:  # inactivo: `ufw status` no muestra políticas, se leen del archivo
        res["defaults"] = _ufw_file_defaults()
    if res["active"]:
        code, out, _ = run(["ufw", "status", "numbered"], timeout=10)
        res["rules"] = _parse_numbered(out) if code == 0 else []
    else:
        res["rules"] = _added_rules()
    return res


def _added_rules():
    """Reglas guardadas (sirve con UFW inactivo). Cada una con su `spec` para poder borrarla."""
    code, out, _ = run(["ufw", "show", "added"], timeout=10)
    rules = []
    for line in out.splitlines() if code == 0 else []:
        line = line.strip()
        if not line.startswith("ufw "):
            continue
        spec = line[4:]
        try:
            tokens = shlex.split(spec)
        except ValueError:
            continue
        action = tokens[0].upper() if tokens else ""
        frm = tokens[tokens.index("from") + 1] if "from" in tokens[:-1] else "Anywhere"
        comment = tokens[tokens.index("comment") + 1] if "comment" in tokens[:-1] else ""
        to = tokens[1] if len(tokens) > 1 else ""
        if "port" in tokens[:-1]:
            to = tokens[tokens.index("port") + 1]
            if "proto" in tokens[:-1]:
                to += "/" + tokens[tokens.index("proto") + 1]
        rules.append({"num": None, "spec": spec, "to": to,
                      "action": action + " IN", "from": frm, "comment": comment, "raw": line})
    return rules


def _ufw_file_defaults():
    names = {"DROP": "deny", "ACCEPT": "allow", "REJECT": "reject"}
    keys = {"DEFAULT_INPUT_POLICY": "incoming", "DEFAULT_OUTPUT_POLICY": "outgoing"}
    res = {}
    try:
        with open("/etc/default/ufw") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k in keys and v.strip('"') in names:
                    res[keys[k]] = names[v.strip('"')]
    except OSError:
        pass
    return res


def _parse_numbered(out):
    rules = []
    for line in out.splitlines():
        m = re.match(r"^\[\s*(\d+)\]\s+(.*)$", line)
        if not m:
            continue
        body, _, comment = m.group(2).partition(" # ")
        parts = re.split(r"\s{2,}", body.strip())
        to, action, frm = (parts + ["", "", ""])[:3]
        rules.append({"num": int(m.group(1)), "to": to, "action": action, "from": frm,
                      "comment": comment.strip(), "raw": line.strip()})
    return rules


def _ssh_ports():
    ports = []
    try:
        with open("/etc/ssh/sshd_config") as f:
            for line in f:
                m = re.match(r"^\s*Port\s+(\d{1,5})\s*$", line, re.I)
                if m and _port(m.group(1), ":"):
                    ports.append(m.group(1))
    except OSError:
        pass
    return ports or ["22"]


def _panel_port():
    host = request.host
    if ":" in host and not host.endswith("]"):
        port = host.rsplit(":", 1)[1]
    else:
        port = os.environ.get("MATIPANEL_PORT", "8443")
    return port if _port(port, ":") else "8443"


@firewall_bp.route("/api/ufw/toggle", methods=["POST"])
@login_required
def ufw_toggle():
    if not _engine_is("ufw"):
        return _err("UFW no es el firewall activo")
    enable = (request.get_json(silent=True) or {}).get("enable")
    if not isinstance(enable, bool):
        return _err("Valor inválido")
    if not enable:
        code, out, err = run(["ufw", "disable"], timeout=30)
        return jsonify({"ok": True, "output": out}) if code == 0 else _err(err.strip() or out.strip(), 500)
    # Antes de activar: permitir SSH y el puerto del panel para no dejar fuera al admin.
    for port in {*_ssh_ports(), _panel_port()}:
        code, out, err = run(["ufw", "allow", f"{port}/tcp"], timeout=30)
        if code != 0:
            return _err(f"No se pudo permitir {port}/tcp, no se activó UFW: {err.strip() or out.strip()}", 500)
    code, out, err = run(["ufw", "--force", "enable"], timeout=30)
    return jsonify({"ok": True, "output": out}) if code == 0 else _err(err.strip() or out.strip(), 500)


@firewall_bp.route("/api/ufw/rule", methods=["POST"])
@login_required
def ufw_add_rule():
    if not _engine_is("ufw"):
        return _err("UFW no es el firewall activo")
    d = request.get_json(silent=True) or {}
    act, proto = d.get("action"), d.get("proto", "any")
    port = _port(d.get("port", ""), ":")
    src = str(d.get("from", "") or "").strip()
    comment = str(d.get("comment", "") or "").strip()
    if act not in UFW_ACTIONS:
        return _err("Acción inválida")
    if proto not in ("tcp", "udp", "any"):
        return _err("Protocolo inválido")
    if not port:
        return _err("Puerto inválido (ej. 80 o 8000:8100)")
    if ":" in port and proto == "any":
        return _err("Un rango de puertos requiere protocolo tcp o udp")
    if src and src != "any":
        try:
            src = str(ipaddress.ip_network(src, strict=False))
        except ValueError:
            return _err("Origen inválido (IP o CIDR)")
    if comment and not COMMENT_RE.match(comment):
        return _err("Comentario inválido (letras, números y . , : / ( ) - _)")

    cmd = ["ufw", act]
    if proto != "any":
        cmd += ["proto", proto]
    cmd += ["from", src or "any", "to", "any", "port", port]
    if comment:
        cmd += ["comment", comment]
    code, out, err = run(cmd, timeout=30)
    if code != 0:
        return _err(err.strip() or out.strip() or "ufw falló", 500)
    return jsonify({"ok": True, "output": out})


@firewall_bp.route("/api/ufw/delete", methods=["POST"])
@login_required
def ufw_delete_rule():
    if not _engine_is("ufw"):
        return _err("UFW no es el firewall activo")
    d = request.get_json(silent=True) or {}
    num, spec = d.get("num"), d.get("spec")
    if spec:
        # Solo se aceptan especificaciones que UFW mismo reporta como guardadas
        if spec not in {r["spec"] for r in _added_rules()}:
            return _err("La regla ya no existe; refresca la página")
        cmd = ["ufw", "--force", "delete", *shlex.split(spec)]
    elif isinstance(num, int) and not isinstance(num, bool) and 1 <= num <= 9999:
        cmd = ["ufw", "--force", "delete", str(num)]
    else:
        return _err("Regla inválida")
    code, out, err = run(cmd, timeout=30)
    if code != 0:
        return _err(err.strip() or out.strip() or "ufw falló", 500)
    return jsonify({"ok": True, "output": out})


@firewall_bp.route("/api/ufw/default", methods=["POST"])
@login_required
def ufw_default():
    if not _engine_is("ufw"):
        return _err("UFW no es el firewall activo")
    d = request.get_json(silent=True) or {}
    direction, policy = d.get("direction"), d.get("policy")
    if direction not in ("incoming", "outgoing") or policy not in UFW_POLICIES:
        return _err("Política inválida")
    code, out, err = run(["ufw", "default", policy, direction], timeout=30)
    if code != 0:
        return _err(err.strip() or out.strip() or "ufw falló", 500)
    return jsonify({"ok": True, "output": out})


# ── firewalld ────────────────────────────────────────────────────────

def _fwcmd(*args, timeout=15):
    return run(["firewall-cmd", *args], timeout=timeout)


def _firewalld_status():
    code, out, _ = run(["systemctl", "is-active", "firewalld"], timeout=5)
    if code == 127:
        return {"installed": False, "engine": "firewalld"}
    res = {"installed": True, "engine": "firewalld", "active": out.strip() == "active"}
    if not res["active"]:
        return res
    code, zone, err = _fwcmd("--get-default-zone")
    if code != 0:
        res["error"] = f"firewall-cmd falló: {err.strip()}"
        return res
    zone = zone.strip()
    res["default_zone"] = zone
    res["services"] = _fwcmd(f"--zone={zone}", "--list-services")[1].split()
    res["ports"] = _fwcmd(f"--zone={zone}", "--list-ports")[1].split()
    res["available_services"] = _fwcmd("--get-services")[1].split()
    res["raw"] = _fwcmd(f"--zone={zone}", "--list-all")[1]
    return res


def _fw_apply(flag):
    """Cambio permanente + reload para que también aplique en runtime."""
    code, out, err = _fwcmd("--permanent", flag, timeout=30)
    if code != 0:
        return _err(err.strip() or out.strip() or "firewall-cmd falló", 500)
    code, out, err = _fwcmd("--reload", timeout=60)
    if code != 0:
        return _err("Guardado, pero falló el reload: " + (err.strip() or out.strip()), 500)
    return jsonify({"ok": True})


@firewall_bp.route("/api/firewalld/port", methods=["POST"])
@login_required
def firewalld_port():
    if not _engine_is("firewalld"):
        return _err("firewalld no es el firewall activo")
    d = request.get_json(silent=True) or {}
    op, proto, port = d.get("op"), d.get("proto"), _port(d.get("port", ""), "-")
    if op not in ("add", "remove") or proto not in ("tcp", "udp"):
        return _err("Operación o protocolo inválido")
    if not port:
        return _err("Puerto inválido (ej. 80 o 8000-8100)")
    return _fw_apply(f"--{op}-port={port}/{proto}")


@firewall_bp.route("/api/firewalld/service", methods=["POST"])
@login_required
def firewalld_service():
    if not _engine_is("firewalld"):
        return _err("firewalld no es el firewall activo")
    d = request.get_json(silent=True) or {}
    op, svc = d.get("op"), str(d.get("service", ""))
    if op not in ("add", "remove") or not SERVICE_RE.match(svc):
        return _err("Operación o servicio inválido")
    code, out, _ = _fwcmd("--get-services")
    if code != 0 or svc not in out.split():
        return _err("Servicio desconocido para firewalld")
    return _fw_apply(f"--{op}-service={svc}")


# ── nftables / iptables (solo lectura, salvo aplicar nftables.conf) ──

def _nft_status():
    code, out, err = run(["nft", "list", "ruleset"], timeout=10)
    if code != 0:
        return {"installed": True, "engine": "nftables", "active": False,
                "error": err.strip() or "nft falló", "conf_exists": os.path.exists(NFT_CONF)}
    return {"installed": True, "engine": "nftables", "active": bool(out.strip()),
            "raw": out or "(ruleset vacío: no hay reglas cargadas)", "conf_exists": os.path.exists(NFT_CONF)}


@firewall_bp.route("/api/nft/apply", methods=["POST"])
@login_required
def nft_apply():
    if not _engine_is("nftables"):
        return _err("nftables no es el firewall activo")
    if not os.path.exists(NFT_CONF):
        return _err(f"{NFT_CONF} no existe")
    code, out, err = run(["nft", "-c", "-f", NFT_CONF], timeout=30)
    if code != 0:
        return _err("La verificación falló, no se aplicó nada:\n" + (err.strip() or out.strip()))
    code, out, err = run(["systemctl", "restart", "nftables"], timeout=30)
    if code != 0:
        return _err(err.strip() or "No se pudo reiniciar nftables", 500)
    return jsonify({"ok": True})


def _iptables_status():
    code, out, err = run(["iptables", "-L", "-n", "-v"], timeout=10)
    if code != 0:
        return {"installed": True, "engine": "iptables", "active": False, "error": err.strip() or "iptables falló"}
    return {"installed": True, "engine": "iptables", "active": True, "raw": out}
