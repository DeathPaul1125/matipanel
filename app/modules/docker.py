"""Gestión de Docker: containers, images, volumes."""
import subprocess
import json
import shutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import valid_name

docker_bp = Blueprint("docker", __name__)

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
            })
        except json.JSONDecodeError:
            continue
    return jsonify({"containers": items})


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
    code, out, err = _docker("logs", "--tail", "200", "--", container, timeout=10)
    if code != 0:
        return jsonify({"error": err.strip()}), 500
    return jsonify({"logs": out + err})


def _safe_id(s):
    return valid_name(s, extra="/")
