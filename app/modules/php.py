"""Gestión de PHP (layout Debian: /etc/php/<versión>/<sapi>/)."""
import os
import re
import shutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import run
from .packages import apt_get

php_bp = Blueprint("php", __name__)

PHP_ROOT = "/etc/php"
SAPIS = ("cli", "fpm", "apache2")
DIRECTIVES = ("memory_limit", "upload_max_filesize", "post_max_size",
              "max_execution_time", "display_errors", "date.timezone")
POOL_KEYS = ("listen", "pm", "pm.max_children")
VER_RE = re.compile(r"^\d+\.\d+$")
MOD_RE = re.compile(r"^[a-z0-9_]+$")
EXT_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,63}$")
VALUE_RE = re.compile(r"^[A-Za-z0-9_./:+-]{1,64}$")


def _versions():
    try:
        names = os.listdir(PHP_ROOT)
    except OSError:
        return []
    vers = [n for n in names if VER_RE.match(n) and os.path.isdir(os.path.join(PHP_ROOT, n))]
    return sorted(vers, key=lambda v: tuple(map(int, v.split("."))), reverse=True)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _ini_values(text, keys):
    """Valores activos (no comentados) de las claves pedidas; la última aparición gana."""
    vals = dict.fromkeys(keys)
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() in vals:
            vals[k.strip()] = v.split(";")[0].strip().strip('"')
    return vals


def _is_active(unit):
    return run(["systemctl", "is-active", "--quiet", "--", unit], timeout=10)[0] == 0


def _apply(ver, sapi):
    """Reinicia FPM / recarga Apache si el SAPI modificado está en uso. Devuelve un mensaje."""
    unit, act = {"fpm": (f"php{ver}-fpm", "restart"), "apache2": ("apache2", "reload")}.get(sapi, (None, None))
    if not unit or not _is_active(unit):
        return ""
    code, _, err = run(["systemctl", act, "--", unit], timeout=60)
    return f"{unit}: {act} " + ("OK" if code == 0 else f"falló ({err.strip()})")


def _pools(ver):
    d = os.path.join(PHP_ROOT, ver, "fpm", "pool.d")
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".conf"))
    except OSError:
        return []
    pools = []
    for f in files:
        path = os.path.join(d, f)
        text = _read(path)
        m = re.search(r"^\s*\[([^\]]+)\]", text, re.M)
        pools.append({"name": m.group(1) if m else f, "path": path, **_ini_values(text, POOL_KEYS)})
    return pools


def _enabled_mods(ver, sapi):
    try:
        names = os.listdir(os.path.join(PHP_ROOT, ver, sapi, "conf.d"))
    except OSError:
        return set()
    return {m.group(1) for n in names if (m := re.match(r"^\d+-(.+)\.ini$", n))}


@php_bp.route("/")
@login_required
def index():
    return render_template("php.html", installed=bool(_versions()) or shutil.which("php") is not None)


@php_bp.route("/api/overview")
@login_required
def overview():
    code, out, _ = run(["php", "-v"], timeout=10)
    default = out.splitlines()[0] if code == 0 and out else ""

    alt = {"current": "", "mode": "", "alternatives": []}
    code, out, _ = run(["update-alternatives", "--query", "php"], timeout=10)
    for line in out.splitlines() if code == 0 else []:
        key, _, val = line.partition(": ")
        if key == "Value":
            alt["current"] = val.strip()
        elif key == "Status":
            alt["mode"] = val.strip()
        elif key == "Alternative":
            alt["alternatives"].append(val.strip())

    versions = []
    for v in _versions():
        sapis = [s for s in SAPIS if os.path.isdir(os.path.join(PHP_ROOT, v, s))]
        inis = []
        for s in sapis:
            path = os.path.join(PHP_ROOT, v, s, "php.ini")
            if os.path.isfile(path):
                inis.append({"sapi": s, "path": path, "values": _ini_values(_read(path), DIRECTIVES)})
        has_fpm = "fpm" in sapis
        versions.append({
            "version": v,
            "binary": shutil.which(f"php{v}") is not None,
            "sapis": sapis,
            "inis": inis,
            "fpm": {"installed": has_fpm,
                    "active": has_fpm and _is_active(f"php{v}-fpm"),
                    "pools": _pools(v) if has_fpm else []},
        })
    return jsonify({"default": default, "alt": alt, "versions": versions})


@php_bp.route("/api/alternative", methods=["POST"])
@login_required
def set_alternative():
    ver = (request.get_json(silent=True) or {}).get("version", "")
    target = f"/usr/bin/php{ver}"
    if ver not in _versions() or not os.path.exists(target):
        return jsonify({"ok": False, "error": "Versión inválida o sin binario CLI"}), 400
    code, out, err = run(["update-alternatives", "--set", "php", target], timeout=20)
    if code != 0:
        return jsonify({"ok": False, "error": err.strip() or "update-alternatives falló"}), 500
    return jsonify({"ok": True, "output": out.strip()})


@php_bp.route("/api/ini", methods=["POST"])
@login_required
def set_ini():
    data = request.get_json(silent=True) or {}
    ver, sapi, values = data.get("version", ""), data.get("sapi", ""), data.get("values")
    if ver not in _versions() or sapi not in SAPIS:
        return jsonify({"ok": False, "error": "Versión o SAPI inválido"}), 400
    if not isinstance(values, dict) or not values:
        return jsonify({"ok": False, "error": "Sin cambios"}), 400
    for k, v in values.items():
        if k not in DIRECTIVES or not isinstance(v, str) or not VALUE_RE.match(v):
            return jsonify({"ok": False, "error": f"Valor inválido para {k}"}), 400

    path = os.path.join(PHP_ROOT, ver, sapi, "php.ini")
    if not os.path.isfile(path):
        return jsonify({"ok": False, "error": "php.ini no encontrado"}), 404
    try:
        _write_directives(path, values)
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "message": _apply(ver, sapi)})


def _write_directives(path, values):
    """Reemplaza `clave = valor` en su sitio (descomenta si hace falta); respalda antes."""
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    shutil.copy2(path, path + ".matipanel.bak")
    for key, val in values.items():
        line = f"{key} = {val}"
        k = re.escape(key)
        active = re.compile(rf"^[ \t]*{k}[ \t]*=.*$", re.M)
        commented = re.compile(rf"^[ \t]*;[ \t]*{k}[ \t]*=.*$", re.M)
        if active.search(text):
            text = active.sub(lambda _m: line, text)
        elif commented.search(text):
            text = commented.sub(lambda _m: line, text, count=1)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)


@php_bp.route("/api/modules")
@login_required
def modules():
    ver = request.args.get("version", "")
    if ver not in _versions():
        return jsonify({"error": "Versión inválida"}), 400
    code, out, _ = run([f"php{ver}", "-m"], timeout=15)
    loaded = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("[")] if code == 0 else []
    try:
        avail = sorted(f[:-4] for f in os.listdir(os.path.join(PHP_ROOT, ver, "mods-available")) if f.endswith(".ini"))
    except OSError:
        avail = []
    sapis = [s for s in SAPIS if os.path.isdir(os.path.join(PHP_ROOT, ver, s, "conf.d"))]
    enabled = {s: _enabled_mods(ver, s) for s in sapis}
    return jsonify({
        "loaded": loaded,
        "sapis": sapis,
        "modules": [{"name": m, "enabled": {s: m in enabled[s] for s in sapis}} for m in avail],
    })


@php_bp.route("/api/module", methods=["POST"])
@login_required
def toggle_module():
    data = request.get_json(silent=True) or {}
    ver, sapi, mod = data.get("version", ""), data.get("sapi", ""), data.get("module", "")
    if ver not in _versions() or sapi not in SAPIS or not MOD_RE.match(mod):
        return jsonify({"ok": False, "error": "Parámetros inválidos"}), 400
    if not os.path.isfile(os.path.join(PHP_ROOT, ver, "mods-available", f"{mod}.ini")):
        return jsonify({"ok": False, "error": "Módulo no disponible"}), 404
    tool = "phpenmod" if data.get("enable") else "phpdismod"
    code, out, err = run([tool, "-v", ver, "-s", sapi, "--", mod], timeout=30)
    if code != 0:
        return jsonify({"ok": False, "error": (err or out).strip() or f"{tool} falló"}), 500
    return jsonify({"ok": True, "message": _apply(ver, sapi)})


@php_bp.route("/api/install", methods=["POST"])
@login_required
def install_ext():
    data = request.get_json(silent=True) or {}
    ver, name = data.get("version", ""), data.get("name", "")
    if ver not in _versions() or not EXT_RE.match(name):
        return jsonify({"ok": False, "error": "Versión o nombre de extensión inválido"}), 400
    ok, output, error = apt_get("install", "--", f"php{ver}-{name}", timeout=600)
    if not ok:
        return jsonify({"ok": False, "error": error, "output": output}), 500
    return jsonify({"ok": True, "output": output})


@php_bp.route("/api/fpm", methods=["POST"])
@login_required
def restart_fpm():
    ver = (request.get_json(silent=True) or {}).get("version", "")
    if ver not in _versions() or not os.path.isdir(os.path.join(PHP_ROOT, ver, "fpm")):
        return jsonify({"ok": False, "error": "Versión sin FPM"}), 400
    code, _, err = run(["systemctl", "restart", "--", f"php{ver}-fpm"], timeout=60)
    if code != 0:
        return jsonify({"ok": False, "error": err.strip() or "No se pudo reiniciar"}), 500
    return jsonify({"ok": True})
