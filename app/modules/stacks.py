"""Stacks: proyectos arbitrarios de Docker Compose, al estilo de Portainer.

Cada stack vive en /var/lib/matipanel/stacks/<nombre>/ (MATIPANEL_STACKS; carpeta 0700, archivos 0600):
  compose.yaml   el archivo de Compose tal como lo escribió el usuario
  .env           variables para la interpolación ${VAR} (Compose lo lee solo de la carpeta del proyecto)
  stack.json     metadatos: servicios, advertencias de seguridad y fechas (se rehace al guardar)

El proyecto de Compose se llama «stack-<nombre>», así el módulo Docker agrupa sus contenedores.
Antes de guardar o arrancar se valida con `docker compose config -q`; con `config --format json` se
buscan ajustes peligrosos (privileged, red del host, docker.sock, puertos en 0.0.0.0…) y se avisa,
sin bloquear: el administrador decide.

ponytail: el candado de «acción en curso» está en memoria; válido con un solo worker de gunicorn.
"""
import json
import os
import posixpath
import re
import shutil
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from flask import Blueprint, render_template, jsonify, request, abort, url_for
from flask_login import login_required

from .. import deployer as D
from ..util import run

stacks_bp = Blueprint("stacks", __name__)

BASE_DIR = Path(os.environ.get("MATIPANEL_STACKS", "/var/lib/matipanel/stacks"))
NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,30}")
SERVICE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
COMPOSE_MAX = 256 * 1024
ENV_MAX = 256 * 1024
LOG_CAP = 1024 * 1024
TAILS = ("100", "300", "1000")
ACTIONS = {  # acción → (argumentos de compose, tiempo máximo en segundos)
    "start": (["up", "-d", "--remove-orphans"], 900),
    "apply": (["up", "-d", "--remove-orphans"], 900),
    "stop": (["stop"], 300),
    "restart": (["restart"], 300),
    "down": (["down", "--remove-orphans"], 300),
}
DANGER_BINDS = {"/": "la raíz del servidor", "/etc": "la configuración del sistema",
                "/root": "la carpeta de root", "/var/run/docker.sock": "el socket de Docker (control total del host)",
                "/run/docker.sock": "el socket de Docker (control total del host)"}
PUBLIC_IPS = ("", "0.0.0.0", "::", "[::]")

_busy = set()


# ── Puntos de ejecución (las pruebas los reemplazan) ────────────────────────

def compose_base():
    return D.compose_cmd()


def quiet(cmd, timeout=60):
    """Sin transmitir a la terminal en vivo: la salida de `config` trae el .env ya interpolado."""
    return D._run(cmd, timeout=timeout)


def run_capped(cmd, cap=LOG_CAP, timeout=20):
    return D._run_capped(cmd, cap, timeout)


# ── Almacenamiento ──────────────────────────────────────────────────────────

def valid_name(name):
    return isinstance(name, str) and bool(NAME_RE.fullmatch(name)) and not name.endswith("-")


def stack_dir(name):
    if not valid_name(name):
        raise ValueError("nombre inválido")
    return BASE_DIR / name


def project(name):
    return f"stack-{name}"


def list_stacks():
    try:
        names = os.listdir(BASE_DIR)
    except OSError:
        return []
    return sorted(n for n in names if valid_name(n) and D.lstat_kind(BASE_DIR / n) == "dir"
                  and D.lstat_kind(BASE_DIR / n / "compose.yaml") == "file")


def _existing(name):
    """Carpeta de un stack existente o abort(404). Nunca una ruta fuera de BASE_DIR ni un enlace."""
    if not valid_name(name) or name not in list_stacks():
        abort(404)
    d = stack_dir(name)
    if Path(os.path.realpath(d)).parent != Path(os.path.realpath(BASE_DIR)):
        abort(404)
    return d


def load_meta(name):
    return D._read_json(stack_dir(name) / "stack.json", {})


def compose_args(name, compose_file=None, env_file=None):
    base = compose_base()
    if not base:
        raise D.Fail("Docker Compose no está instalado (instálalo desde «Despliegues» → Requisitos)")
    d = stack_dir(name)
    extra = ["--env-file", str(env_file)] if env_file else []
    return [*base, "-p", project(name), "-f", str(compose_file or d / "compose.yaml"), *extra,
            "--project-directory", str(d)]


# ── Validación y advertencias ───────────────────────────────────────────────

def _text_error(text, label, limit):
    if not isinstance(text, str):
        return f"{label}: formato inválido"
    if "\0" in text:
        return f"{label} contiene caracteres nulos"
    if len(text.encode()) > limit:
        return f"{label} es demasiado grande (máximo {limit // 1024} KB)"
    return ""


def dangers(cfg):
    """config --format json → (advertencias, puertos publicados en todas las interfaces)."""
    warnings, public = [], []
    services = cfg.get("services") if isinstance(cfg, dict) else None
    for svc, s in sorted((services or {}).items()):
        if not isinstance(s, dict):
            continue
        if s.get("privileged") is True:
            warnings.append(f"{svc}: privileged: true — el contenedor tiene acceso total al servidor")
        if s.get("network_mode") == "host":
            warnings.append(f"{svc}: network_mode: host — comparte la red del servidor y todos sus puertos quedan expuestos")
        if s.get("pid") == "host":
            warnings.append(f"{svc}: pid: host — ve y puede señalar todos los procesos del servidor")
        for cap in s.get("cap_add") or []:
            c = str(cap).upper().removeprefix("CAP_")
            if c in ("ALL", "SYS_ADMIN"):
                warnings.append(f"{svc}: cap_add {c} — capacidades casi equivalentes a privileged")
        for v in s.get("volumes") or []:
            if isinstance(v, dict) and v.get("type") == "bind" and isinstance(v.get("source"), str):
                src = posixpath.normpath(v["source"]) if v["source"] else ""
                src = "/" if src == "//" else src
                if src in DANGER_BINDS:
                    ro = " (solo lectura)" if v.get("read_only") else ""
                    warnings.append(f"{svc}: monta {src}{ro} — {DANGER_BINDS[src]}")
        if s.get("network_mode") == "host":
            continue
        for p in s.get("ports") or []:
            if not isinstance(p, dict):
                continue
            ip = str(p.get("host_ip") or "")
            if ip not in PUBLIC_IPS:
                continue
            pub = str(p.get("published") or "") or "aleatorio"
            public.append(f"{svc}: {ip or '0.0.0.0'}:{pub} → {p.get('target')}/{p.get('protocol') or 'tcp'}")
    return warnings, public


def check(name, compose_text, env_text, commit=False):
    """Valida compose.yaml + .env candidatos en la carpeta del stack; si commit y es válido, los guarda.
    → (ok, error, info) con info = {services, warnings, public_ports}."""
    d = stack_dir(name)
    D._ensure_dir(BASE_DIR)
    kind = D.lstat_kind(d)
    created = kind is None
    if created:
        D._ensure_dir(d)
    elif kind != "dir" or Path(os.path.realpath(d)).parent != Path(os.path.realpath(BASE_DIR)):
        return False, "Ya existe algo con ese nombre que no es una carpeta del panel", {}
    cand_c, cand_e = d / ".compose.yaml.new", d / ".env.new"
    keep = False
    try:
        D.write_file(cand_c, compose_text)
        D.write_file(cand_e, env_text)
        base = compose_args(name, cand_c, cand_e)
        code, out, err = run([*base, "config", "-q"], timeout=60)
        if code != 0:
            msg = (err or out).strip()
            return False, (msg[-3000:] or f"docker compose config terminó con código {code}"), {}
        info = {"services": [], "warnings": [], "public_ports": []}
        code, out, err = quiet([*base, "config", "--format", "json"], timeout=60)
        try:
            cfg = json.loads(out) if code == 0 else None
        except ValueError:
            cfg = None
        if isinstance(cfg, dict):
            info["services"] = sorted(str(k) for k in (cfg.get("services") or {}))
            info["warnings"], info["public_ports"] = dangers(cfg)
        else:
            info["warnings"] = ["No se pudo revisar la configuración en busca de ajustes peligrosos "
                                "(¿Compose antiguo sin «config --format json»?)"]
        if commit:
            os.replace(cand_c, d / "compose.yaml")
            os.replace(cand_e, d / ".env")
            meta = load_meta(name)
            now = time.time()
            D._write_json(d / "stack.json", {**info, "created": meta.get("created") or now, "updated": now})
            keep = True
        return True, "", info
    except D.Fail as e:
        return False, str(e), {}
    finally:
        for f in (cand_c, cand_e):
            try:
                os.unlink(f)
            except OSError:
                pass
        if created and not keep:
            shutil.rmtree(d, ignore_errors=True)


# ── Estado ──────────────────────────────────────────────────────────────────

def parse_ps(out):
    """Salida de `compose ps --format json`: un array JSON (Compose < 2.21) o una línea JSON por contenedor."""
    out = (out or "").strip()
    try:
        data = json.loads(out) if out.startswith("[") else [json.loads(l) for l in out.splitlines() if l.strip()]
    except ValueError:
        return []
    items = []
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict):
            continue
        ports = []
        for p in c.get("Publishers") or []:
            if isinstance(p, dict) and p.get("PublishedPort"):
                ip = str(p.get("URL") or "")
                s = f"{p['PublishedPort']}→{p.get('TargetPort')}/{p.get('Protocol') or 'tcp'}"
                s = s if ip in PUBLIC_IPS else f"{ip}:{s}"  # 0.0.0.0 y :: son la misma publicación
                if s not in ports:
                    ports.append(s)
        items.append({"service": str(c.get("Service", "")), "name": str(c.get("Name", "")),
                      "state": str(c.get("State", "")), "status": str(c.get("Status", "")),
                      "health": str(c.get("Health", "")), "image": str(c.get("Image", "")), "ports": ports})
    return items


def overall(services, containers):
    """(clave del LED, servicios en marcha, total). clave: running | partial | stopped | empty."""
    svcs = set(services or ()) | {c["service"] for c in containers}
    running = {c["service"] for c in containers if c["state"] == "running"}
    unhealthy = any(c.get("health") == "unhealthy" for c in containers)
    if not containers:
        return "empty", 0, len(svcs)
    if running >= svcs and not unhealthy:
        return "running", len(running), len(svcs)
    return ("partial" if running else "stopped"), len(running), len(svcs)


HOST_PORT_RE = re.compile(r"([0-9a-fA-F.:\[\]]*):(\d+)->(\d+(?:-\d+)?/\w+)")


def all_containers():
    """{proyecto: [contenedor]} de todos los stacks con una sola llamada a docker. None si falla."""
    fmt = '{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}\t{{.State}}\t{{.Ports}}'
    code, out, _ = quiet(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project", "--format", fmt], 20)
    if code != 0:
        return None
    res = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or not parts[0].startswith("stack-"):
            continue
        ports = []
        for ip, host, target in HOST_PORT_RE.findall(parts[3]):
            s = f"{host}→{target}" if ip in ("0.0.0.0", "[::]", "::", "") else f"{ip}:{host}→{target}"
            if s not in ports:
                ports.append(s)
        res.setdefault(parts[0], []).append({"service": parts[1], "state": parts[2], "ports": ports})
    return res


def _summary(name, containers):
    meta = load_meta(name)
    cs = None if containers is None else containers.get(project(name), [])
    state, running, total = overall(meta.get("services"), cs or [])
    ports = []
    for c in cs or []:
        ports += [p for p in c["ports"] if p not in ports]
    return {"name": name, "state": "unknown" if cs is None else state, "running": running, "total": total,
            "ports": ports, "warnings": len(meta.get("warnings") or []), "updated": meta.get("updated"),
            "busy": name in _busy}


@contextmanager
def _lock(name):
    if name in _busy:
        raise RuntimeError("Ya hay una acción en curso en este stack; espera a que termine")
    _busy.add(name)
    try:
        yield
    finally:
        _busy.discard(name)


def _err(msg, code=400, **extra):
    return jsonify({"ok": False, "error": msg, **extra}), code


def _data():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _texts(d, name=None):
    """compose_text/env_text de la petición (o los guardados si faltan). → (compose, env, error)."""
    cur = stack_dir(name) if name else None
    compose_text = d.get("compose_text")
    if compose_text is None and cur:
        compose_text = D.read_text(cur / "compose.yaml", COMPOSE_MAX + 1) or ""
    env_text = d.get("env_text")
    if env_text is None:
        env_text = (D.read_text(cur / ".env", ENV_MAX + 1) or "") if cur else ""
    err = _text_error(compose_text, "compose.yaml", COMPOSE_MAX) or _text_error(env_text, "El .env", ENV_MAX)
    if err:
        return None, None, err
    compose_text, env_text = compose_text.replace("\r\n", "\n"), env_text.replace("\r\n", "\n")
    if not compose_text.strip():
        return None, None, "El compose.yaml está vacío"
    return compose_text, env_text, ""


def _compose_run(name, args, timeout):
    """Valida el compose.yaml guardado y ejecuta la acción (salida en vivo en la terminal)."""
    base = compose_args(name)
    code, out, err = run([*base, "config", "-q"], timeout=60)
    if code != 0:
        return False, "El compose.yaml no es válido: " + ((err or out).strip()[-1500:] or f"código {code}")
    for a in args:
        code, out, err = run([*base, *a], timeout=timeout)
        if code != 0:
            return False, (err or out).strip()[-1500:] or f"docker compose {a[0]} terminó con código {code}"
    return True, ""


# ── Páginas ─────────────────────────────────────────────────────────────────

TEMPLATES = {
    "uptime-kuma": {
        "label": "Uptime Kuma (monitor de servicios)",
        "compose": """services:
  uptime-kuma:
    image: louislam/uptime-kuma:1
    restart: unless-stopped
    ports:
      - "3001:3001"
    volumes:
      - data:/app/data

volumes:
  data:
""",
    },
    "portainer": {
        "label": "Portainer CE (gestor de Docker)",
        "compose": """# Portainer necesita el socket de Docker: quien entre a Portainer controla el servidor.
services:
  portainer:
    image: portainer/portainer-ce:latest
    restart: unless-stopped
    ports:
      - "9443:9443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - data:/data

volumes:
  data:
""",
    },
    "nginx": {
        "label": "Sitio estático con Nginx",
        "compose": """# Sube tus archivos a la carpeta html/ del stack (Archivos → /var/lib/matipanel/stacks/<nombre>/html)
# y quita el bloque «configs» para que Nginx sirva tu sitio en lugar de la página de ejemplo.
services:
  web:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "${PUERTO:-8088}:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
    configs:
      - source: index
        target: /usr/share/nginx/html/index.html

configs:
  index:
    content: |
      <!doctype html>
      <meta charset="utf-8">
      <title>Funciona</title>
      <h1>Nginx en marcha</h1>
      <p>Stack creado con MatiPanel.</p>
""",
        "env": "PUERTO=8088\n",
    },
    "adminer": {
        "label": "Adminer (administrador de bases de datos)",
        "compose": """services:
  adminer:
    image: adminer:latest
    restart: unless-stopped
    ports:
      - "8089:8080"
    environment:
      ADMINER_DESIGN: pepa-linha
""",
    },
}


@stacks_bp.route("/")
@login_required
def index():
    return render_template("stacks.html", stack_name=None, templates=TEMPLATES)


@stacks_bp.route("/s/<name>")
@login_required
def detail(name):
    _existing(name)
    return render_template("stacks.html", stack_name=name, templates={})


# ── API ─────────────────────────────────────────────────────────────────────

@stacks_bp.route("/api/stacks")
@login_required
def stacks():
    names = list_stacks()
    have = bool(compose_base())
    containers = all_containers() if names and have else ({} if have else None)
    return jsonify({"ok": True, "compose": have, "dir": str(BASE_DIR),
                    "stacks": [_summary(n, containers) for n in names]})


@stacks_bp.route("/api/validate", methods=["POST"])
@login_required
def validate():
    d = _data()
    name = str(d.get("name", "")).strip()
    if not valid_name(name):
        return _err("Nombre inválido: minúsculas, números y guiones; empieza con letra (2 a 31 caracteres)")
    compose_text, env_text, error = _texts(d, name if name in list_stacks() else None)
    if error:
        return _err(error)
    ok, error, info = check(name, compose_text, env_text)
    if not ok:
        return _err(error, 422)
    return jsonify({"ok": True, **info})


@stacks_bp.route("/api/stacks", methods=["POST"])
@login_required
def create():
    d = _data()
    name = str(d.get("name", "")).strip()
    if not valid_name(name):
        return _err("Nombre inválido: minúsculas, números y guiones; empieza con letra (2 a 31 caracteres)")
    if D.lstat_kind(BASE_DIR / name) is not None:
        return _err("Ya existe un stack (o una carpeta) con ese nombre", 409)
    compose_text, env_text, error = _texts(d)
    if error:
        return _err(error)
    try:
        ok, error, info = check(name, compose_text, env_text, commit=True)
    except OSError as e:
        return _err(f"No se pudo guardar: {e}", 500)
    if not ok:
        return _err(error, 422)
    started, start_error = False, ""
    if d.get("start"):
        try:
            with _lock(name):
                started, start_error = _compose_run(name, [ACTIONS["start"][0]], ACTIONS["start"][1])
        except (RuntimeError, D.Fail) as e:
            start_error = str(e)
    return jsonify({"ok": True, "url": url_for("stacks.detail", name=name), "started": started,
                    "start_error": start_error, **info})


@stacks_bp.route("/api/stacks/<name>")
@login_required
def info(name):
    d = _existing(name)
    meta = load_meta(name)
    res = {"ok": True, "name": name, "project": project(name), "dir": str(d), "meta": meta,
           "compose_text": D.read_text(d / "compose.yaml", COMPOSE_MAX) or "",
           "env_text": D.read_text(d / ".env", ENV_MAX) or "", "busy": name in _busy,
           "containers": None, "error": ""}
    try:
        code, out, err = quiet([*compose_args(name), "ps", "-a", "--format", "json"], 30)
    except D.Fail as e:
        res["error"] = str(e)
    else:
        if code == 0:
            res["containers"] = parse_ps(out)
        else:
            res["error"] = (err or out).strip()[-500:] or f"docker compose ps terminó con código {code}"
    state, running, total = overall(meta.get("services"), res["containers"] or [])
    res.update(state="unknown" if res["containers"] is None else state, running=running, total=total)
    return jsonify(res)


@stacks_bp.route("/api/stacks/<name>/save", methods=["POST"])
@login_required
def save(name):
    _existing(name)
    compose_text, env_text, error = _texts(_data(), name)
    if error:
        return _err(error)
    if name in _busy:
        return _err("Hay una acción en curso en este stack; espera a que termine", 409)
    try:
        ok, error, info = check(name, compose_text, env_text, commit=True)
    except OSError as e:
        return _err(f"No se pudo guardar: {e}", 500)
    if not ok:
        return _err(error, 422)
    return jsonify({"ok": True, **info})


@stacks_bp.route("/api/stacks/<name>/action", methods=["POST"])
@login_required
def action(name):
    _existing(name)
    act = _data().get("action", "")
    if act == "pull":
        steps, timeout = [["pull"], ACTIONS["start"][0]], 1800
    elif act in ACTIONS:
        steps, timeout = [ACTIONS[act][0]], ACTIONS[act][1]
    else:
        return _err("Acción no permitida")
    try:
        with _lock(name):
            ok, error = _compose_run(name, steps, timeout)
    except RuntimeError as e:
        return _err(str(e), 409)
    except D.Fail as e:
        return _err(str(e), 503)
    return jsonify({"ok": True}) if ok else _err(error, 500)


@stacks_bp.route("/api/stacks/<name>/logs")
@login_required
def logs(name):
    _existing(name)
    svc = request.args.get("service", "")
    tail = request.args.get("tail", "300")
    if tail not in TAILS:
        return _err("Cantidad de líneas inválida")
    if svc and not SERVICE_RE.fullmatch(svc):
        return _err("Servicio inválido")
    try:
        cmd = [*compose_args(name), "logs", "--no-color", "--tail", tail] + (["--", svc] if svc else [])
    except D.Fail as e:
        return _err(str(e), 503)
    code, out = run_capped(cmd, LOG_CAP, 20)
    return jsonify({"ok": code == 0 or bool(out), "logs": out, "error": "" if code == 0 else out.strip()[-500:]})


@stacks_bp.route("/api/stacks/<name>/delete", methods=["POST"])
@login_required
def delete(name):
    d = _existing(name)
    data = _data()
    if data.get("confirm") != name:
        return _err("Escribe el nombre del stack para confirmar")
    warnings = []
    try:
        with _lock(name):
            base = compose_args(name) if compose_base() else None
            if base:
                code, out, err = run([*base, "down", "--remove-orphans", *(["-v"] if data.get("volumes") else [])],
                                     timeout=300)
                if code != 0:
                    warnings.append("docker compose down: " + (err or out).strip()[-400:])
            else:
                warnings.append("Docker Compose no está instalado: los contenedores (si los hay) no se bajaron")
            # Renombrar primero: si rmtree falla a medias, el stack ya no aparece como válido a medias.
            trash = BASE_DIR / f".borrado-{name}-{secrets.token_hex(4)}"
            os.replace(d, trash)
            shutil.rmtree(trash)
    except RuntimeError as e:
        return _err(str(e), 409)
    except OSError as e:
        return _err(f"No se pudo eliminar la carpeta: {e}", 500, warnings=warnings)
    return jsonify({"ok": True, "warnings": warnings})
