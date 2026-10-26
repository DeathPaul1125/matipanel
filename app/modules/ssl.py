"""Certificados SSL con Let's Encrypt (certbot)."""
import os
import re
import shutil
from datetime import datetime, timezone
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import run
from .webserver import _installed as _web_installed

ssl_bp = Blueprint("ssl", __name__)

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9-]{1,63}\.)*[a-zA-Z0-9-]{1,63}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
CERT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
TIMERS = ("certbot.timer", "snap.certbot.renew.timer")


def _available():
    return shutil.which("certbot") is not None


def _default_plugin():
    """Método preseleccionado al obtener: el servidor web que esté instalado."""
    if _web_installed("apache"):
        return "apache"
    return "nginx" if _web_installed("nginx") else "webroot"


def _err(msg, code=400, **extra):
    return jsonify({"ok": False, "error": msg, **extra}), code


@ssl_bp.route("/")
@login_required
def index():
    return render_template("ssl.html", available=_available(), default_plugin=_default_plugin())


def parse_certificates(text):
    certs, cur = [], None
    for line in text.splitlines():
        key, sep, val = line.strip().partition(": ")
        if not sep:
            continue
        if key == "Certificate Name":
            cur = {"name": val, "domains": [], "expiry": "", "days_left": None, "valid": "",
                   "cert_path": "", "key_path": ""}
            certs.append(cur)
        elif cur is None:
            continue
        elif key == "Domains":
            cur["domains"] = val.split()
        elif key == "Expiry Date":
            date, _, status = val.partition(" (")
            cur["expiry"] = date.strip()
            cur["valid"] = status.rstrip(")")
            try:
                exp = datetime.fromisoformat(date.strip())
                cur["days_left"] = (exp - datetime.now(timezone.utc)).days
            except ValueError:
                pass
        elif key == "Certificate Path":
            cur["cert_path"] = val
        elif key == "Private Key Path":
            cur["key_path"] = val
    return certs


@ssl_bp.route("/api/certs")
@login_required
def certs():
    if not _available():
        return _err("certbot no está instalado", 404)
    code, out, err = run(["certbot", "certificates"], timeout=60)
    if code != 0:
        return _err((err or out).strip() or "No se pudo listar", 500)
    return jsonify({"certs": parse_certificates(out + "\n" + err)})


@ssl_bp.route("/api/timer")
@login_required
def timer():
    for unit in TIMERS:
        _, active, _ = run(["systemctl", "is-active", unit])
        _, line, _ = run(["systemctl", "list-timers", "--all", "--no-pager", "--no-legend", unit])
        if active.strip() == "active" or line.strip():
            return jsonify({"unit": unit, "active": active.strip(), "line": line.strip()})
    return jsonify({"unit": TIMERS[0], "active": "inactive", "line": ""})


def _valid_webroot(p):
    return (bool(p) and len(p) < 256 and p.startswith("/") and PATH_RE.fullmatch(p)
            and ".." not in p.split("/") and os.path.isdir(p))


@ssl_bp.route("/api/obtain", methods=["POST"])
@login_required
def obtain():
    if not _available():
        return _err("certbot no está instalado", 404)
    data = request.get_json(silent=True) or {}
    domains = [d.strip().lower() for d in str(data.get("domains", "")).replace(" ", ",").split(",") if d.strip()]
    email = str(data.get("email", "")).strip()
    plugin = data.get("plugin", "")
    webroot = str(data.get("webroot", "")).strip().rstrip("/")

    if not domains or len(domains) > 100:
        return _err("Indica entre 1 y 100 dominios")
    bad = [d for d in domains if not HOST_RE.fullmatch(d) or d.startswith("-")]
    if bad:
        return _err("Dominio inválido: " + bad[0])
    if not EMAIL_RE.fullmatch(email) or email.startswith("-"):
        return _err("Email inválido")

    cmd = ["certbot"]
    if plugin in ("apache", "nginx"):
        cmd += [f"--{plugin}", "--redirect" if data.get("redirect") else "--no-redirect"]
    elif plugin == "webroot":
        if not _valid_webroot(webroot):
            return _err("Ruta webroot inválida o inexistente")
        cmd += ["certonly", "--webroot", f"--webroot-path={webroot}"]
    else:
        return _err("Plugin no permitido")
    cmd += ["--non-interactive", "--agree-tos", f"--email={email}", f"--domains={','.join(domains)}"]

    code, out, err = run(cmd, timeout=300)
    output = (out + "\n" + err).strip()
    if code != 0:
        return _err("certbot falló", 500, output=output)
    return jsonify({"ok": True, "output": output})


@ssl_bp.route("/api/renew", methods=["POST"])
@login_required
def renew():
    if not _available():
        return _err("certbot no está instalado", 404)
    data = request.get_json(silent=True) or {}
    cmd = ["certbot", "renew", "--non-interactive"] + (["--dry-run"] if data.get("dry_run") else [])
    code, out, err = run(cmd, timeout=300)
    output = (out + "\n" + err).strip()
    if code != 0:
        return _err("certbot renew falló", 500, output=output)
    return jsonify({"ok": True, "output": output})


@ssl_bp.route("/api/delete", methods=["POST"])
@login_required
def delete():
    if not _available():
        return _err("certbot no está instalado", 404)
    name = (request.get_json(silent=True) or {}).get("name", "")
    if not CERT_RE.fullmatch(name):
        return _err("Nombre de certificado inválido")
    code, out, err = run(["certbot", "delete", f"--cert-name={name}", "--non-interactive"], timeout=60)
    output = (out + "\n" + err).strip()
    if code != 0:
        return _err("certbot delete falló", 500, output=output)
    return jsonify({"ok": True, "output": output})
