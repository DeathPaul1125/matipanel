"""Gestión de Docker: containers, images, volumes."""
import subprocess
import json
import os
import re
import shutil
import threading
from pathlib import Path
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import valid_name

docker_bp = Blueprint("docker", __name__)
LOGS_MAX = 1024 * 1024
# Nombres que el admin les da a grupos y contenedores. Son solo etiquetas del panel: renombrar el
# contenedor en Docker no sirve con Compose (lo recrea con su nombre en el siguiente despliegue).
NAMES_PATH = Path(os.environ.get("MATIPANEL_DOCKER_NAMES", "/var/lib/matipanel/docker_names.json"))
KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
ROLE_LABEL = {"app": "Aplicación", "db": "Base de datos", "redis": "Redis", "worker": "Worker de colas",
              "scheduler": "Programador"}


def load_names():
    try:
        data = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    out = {"groups": {}, "containers": {}}
    for kind in out:
        src = data.get(kind) if isinstance(data, dict) else None
        if isinstance(src, dict):
            out[kind] = {k: v for k, v in src.items() if KEY_RE.fullmatch(str(k)) and isinstance(v, str)}
    return out


def save_names(data):
    NAMES_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = NAMES_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, NAMES_PATH)


def parse_labels(text):
    """«a=1,b=2» de docker ps → dict. ponytail: un valor con coma se corta; los que usamos no la llevan."""
    out = {}
    for part in (text or "").split(","):
        k, sep, v = part.partition("=")
        if sep:
            out[k.strip()] = v
    return out


def describe(c, labels):
    """Grupo (proyecto de Compose) y rol legible de un contenedor."""
    project = labels.get("com.docker.compose.project", "")
    service = labels.get("com.docker.compose.service", "")
    app, role = labels.get("matipanel.app", ""), labels.get("matipanel.role", "")
    if role:
        label = ROLE_LABEL.get(role, role.capitalize())
        if role == "app" and service.startswith("app_"):
            label += f" ({service[4:]})"  # slot blue/green
    else:
        label = service or c.get("Names", "")
    return {"project": project, "service": service, "app": app, "role": role, "role_label": label}

def _available():
    # Por request (no al importar): instalar Docker no obliga a reiniciar el panel.
    return shutil.which("docker") is not None


def _docker(*args, timeout=15):
    """Wrapper para subprocess de docker. Retorna (returncode, stdout, stderr)."""
    if not _available():
        return 127, "", "docker no instalado"
    try:
        r = subprocess.run(
            ["docker", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "docker no instalado"


@docker_bp.route("/")
@login_required
def index():
    return render_template("docker.html", available=_available())


@docker_bp.route("/api/info")
@login_required
def info():
    code, out, err = _docker("info", "--format", "{{json .}}")
    if code != 0:
        return jsonify({"available": False, "error": err.strip()})
    try:
        data = json.loads(out)
        return jsonify({
            "available": True,
            "version": data.get("ServerVersion", "?"),
            "containers": data.get("Containers", 0),
            "running": data.get("ContainersRunning", 0),
            "stopped": data.get("ContainersStopped", 0),
            "images": data.get("Images", 0),
            "driver": data.get("Driver", ""),
            "kernel": data.get("KernelVersion", ""),
            "os": data.get("OperatingSystem", ""),
        })
    except (json.JSONDecodeError, KeyError) as e:
        return jsonify({"available": False, "error": str(e)})


@docker_bp.route("/api/containers")
@login_required
def containers():
    code, out, err = _docker("ps", "-a", "--no-trunc",
                              "--format", "{{json .}}")
    if code != 0:
        return jsonify({"error": err.strip() or "No se pudo listar"}), 500

    items = []
    for line in out.strip().splitlines():
        if not line:
            continue
        try:
            c = json.loads(line)
            items.append({
                "id": c.get("ID", "")[:12],
                "name": c.get("Names", ""),
                "image": c.get("Image", ""),
                "status": c.get("Status", ""),
                "state": c.get("State", ""),
                "ports": c.get("Ports", ""),
                "created": c.get("CreatedAt", ""),
                **describe(c, parse_labels(c.get("Labels", ""))),
            })
        except json.JSONDecodeError:
            continue
    names = load_names()
    for it in items:
        it["alias"] = names["containers"].get(it["name"], "")
    groups = {}
    for it in items:
        key = it["project"]
        if key and key not in groups:
            groups[key] = {"project": key, "app": it["app"], "alias": names["groups"].get(key, ""),
                           "default": it["app"] or key}
    return jsonify({"containers": items, "groups": list(groups.values())})


@docker_bp.route("/api/name", methods=["POST"])
@login_required
def set_name():
    """Nombre propio para un grupo (proyecto de Compose) o un contenedor. Vacío = quitarlo."""
    data = request.get_json(silent=True) or {}
    kind, key, label = data.get("kind"), str(data.get("key", "")), " ".join(str(data.get("label", "")).split())
    if kind not in ("groups", "containers") or not KEY_RE.fullmatch(key):
        return jsonify({"ok": False, "error": "Datos inválidos"}), 400
    if len(label) > 60 or not all(ch.isprintable() for ch in label):
        return jsonify({"ok": False, "error": "El nombre admite hasta 60 caracteres imprimibles"}), 400
    names = load_names()
    if label:
        names[kind][key] = label
    else:
        names[kind].pop(key, None)
    try:
        save_names(names)
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    return jsonify({"ok": True})


@docker_bp.route("/api/images")
@login_required
def images():
    code, out, err = _docker("images", "--format", "{{json .}}")
    if code != 0:
        return jsonify({"error": err.strip()}), 500
    items = []
    for line in out.strip().splitlines():
        if not line:
            continue
        try:
            im = json.loads(line)
            items.append({
                "id": im.get("ID", "")[:12],
                "repo": im.get("Repository", ""),
                "tag": im.get("Tag", ""),
                "size": im.get("Size", ""),
                "created": im.get("CreatedSince", ""),
            })
        except json.JSONDecodeError:
            continue
    return jsonify({"images": items})


@docker_bp.route("/api/volumes")
@login_required
def volumes():
    code, out, err = _docker("volume", "ls", "--format", "{{json .}}")
    if code != 0:
        return jsonify({"error": err.strip()}), 500
    items = []
    for line in out.strip().splitlines():
        if not line:
            continue
        try:
            v = json.loads(line)
            items.append({
                "name": v.get("Name", ""),
                "driver": v.get("Driver", ""),
                "mount": v.get("Mountpoint", ""),
            })
        except json.JSONDecodeError:
            continue
    return jsonify({"volumes": items})


ALLOWED_ACTIONS = {"start", "stop", "restart", "rm", "pause", "unpause"}


@docker_bp.route("/api/action", methods=["POST"])
@login_required
def action():
    data = request.get_json(silent=True) or {}
    container = data.get("container", "")
    act = data.get("action", "")
    if act not in ALLOWED_ACTIONS:
        return jsonify({"ok": False, "error": "Acción no permitida"}), 400
    if not _safe_id(container):
        return jsonify({"ok": False, "error": "ID/nombre inválido"}), 400

    code, out, err = _docker(act, "--", container, timeout=20)
    if code != 0:
        return jsonify({"ok": False, "error": err.strip()}), 500
    return jsonify({"ok": True, "output": out.strip()})


@docker_bp.route("/api/logs/<container>")
@login_required
def logs(container):
    if not _safe_id(container):
        return jsonify({"error": "ID inválido"}), 400
    if not _available():
        return jsonify({"error": "docker no instalado"}), 500
    # 200 líneas pueden ser cientos de MB si el contenedor escribe líneas gigantes o binario:
    # se lee como bytes, con tope y tiempo límite, para no tumbar el único worker.
    try:
        p = subprocess.Popen(["docker", "logs", "--tail", "200", "--", container],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    timer = threading.Timer(10, p.kill)
    timer.start()
    try:
        data = p.stdout.read(LOGS_MAX + 1)
        if len(data) > LOGS_MAX:
            p.kill()
        p.stdout.close()
        code = p.wait()
    finally:
        timer.cancel()
    text = data[-LOGS_MAX:].decode("utf-8", "replace")
    if len(data) > LOGS_MAX:
        text = f"[… salida recortada a {LOGS_MAX // 1024} KB …]\n" + text
    elif code != 0:
        return jsonify({"error": text.strip() or "docker logs falló"}), 500
    return jsonify({"logs": text})


def _safe_id(s):
    return valid_name(s, extra="/")
