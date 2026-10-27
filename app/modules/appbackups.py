"""Pantalla de respaldos de las apps desplegadas a otro servidor (el motor está en app/appbackup.py)."""
import os

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from .. import appbackup as B
from .. import deployer as D

appbackup_bp = Blueprint("appbackup", __name__)


@appbackup_bp.record_once
def _start(_state):
    if os.environ.get("MATIPANEL_APPBACKUP_WORKER", "1") != "0":
        B.start()


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _app(name):
    cfg = D.load_config(name) if D.valid_name(name) and name in D.list_apps() else None
    return cfg if cfg and not cfg.get("draft") else None


def _dest(name, dest_id=None):
    """Servidor pedido en la petición o, si no se indica, el de la app."""
    return B.app_dest(B.load(), name, str(dest_id) if dest_id else None)


@appbackup_bp.route("/")
@login_required
def index():
    try:
        B.ensure_key()
    except (B.Fail, OSError):
        pass
    return render_template("appbackups.html", schedules=B.SCHEDULES)


@appbackup_bp.route("/api/overview")
@login_required
def overview():
    cfg = B.load()
    apps = []
    for name in D.list_apps():
        app = D.load_config(name) or {}
        if app.get("draft"):
            continue
        busy, _ = B.status(name)
        dest = B.app_dest(cfg, name)
        apps.append({"name": name, "database": app.get("database", "none"), "deployed": bool(app.get("active_slot")),
                     "volumes": [v.split("_", 1)[1] for v in B.app_volumes(app)], "dest": dest["id"] if dest else "",
                     "settings": B.app_settings(cfg, name), "state": cfg["state"].get(name) or {}, "busy": busy})
    return jsonify({"dests": [B.public_dest(d) for d in cfg["dests"]], "configured": B.configured(cfg),
                    "pubkey": B.public_key(), "fingerprints": B.fingerprints(), "apps": apps})


def _posted_dest():
    """Servidor enviado por el formulario, validado (al editar conserva id y contraseña). → (dest, old, error)"""
    data = request.get_json(silent=True) or {}
    old = None
    if data.get("id"):
        old = B.find_dest(B.load(), str(data["id"]))
        if not old:
            return None, None, "Ese servidor de respaldos ya no existe"
    d, err = B.validate_dest(data, old)
    return d, old, err


@appbackup_bp.route("/api/dests", methods=["POST"])
@login_required
def dest_save():
    d, old, err = _posted_dest()
    if err:
        return _err(err)
    if old and old["type"] == "ssh" and (old["type"], old["host"], old["port"]) != (d["type"], d["host"], d["port"]):
        B.forget_host(old)  # otro servidor: se fija su huella en la próxima conexión

    def put(c):
        i = next((i for i, x in enumerate(c["dests"]) if x["id"] == d["id"]), None)
        if i is None:
            c["dests"].append(d)
        else:
            c["dests"][i] = d
    B.update(put)
    return jsonify({"ok": True, "id": d["id"]})


@appbackup_bp.route("/api/dests/<dest_id>/delete", methods=["POST"])
@login_required
def dest_delete(dest_id):
    old = B.find_dest(B.load(), dest_id)
    if not old:
        return _err("Ese servidor de respaldos ya no existe", 404)
    B.forget_host(old)
    B.update(lambda c: c.__setitem__("dests", [x for x in c["dests"] if x["id"] != dest_id]))
    return jsonify({"ok": True})


@appbackup_bp.route("/api/test", methods=["POST"])
@login_required
def test():
    # Prueba lo que está en el formulario (sin guardarlo) o, con solo {"id"}, el servidor guardado
    data = request.get_json(silent=True) or {}
    if set(data) == {"id"}:
        d, err = B.find_dest(B.load(), str(data["id"])), ""
        if not d:
            return _err("Ese servidor de respaldos ya no existe", 404)
    else:
        d, _, err = _posted_dest()
    if err:
        return _err(err)
    try:
        return jsonify({"ok": True, **B.test(d)})
    except B.Fail as e:
        return _err(str(e), 502)
    except OSError as e:
        return _err(f"No se pudo preparar la conexión: {e}", 500)


@appbackup_bp.route("/api/forget-host", methods=["POST"])
@login_required
def forget_host():
    dest_id = str((request.get_json(silent=True) or {}).get("id") or "")
    B.forget_host(B.find_dest(B.load(), dest_id) if dest_id else None)
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/settings", methods=["POST"])
@login_required
def settings(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    s, err = B.validate_app(request.get_json(silent=True) or {}, [d["id"] for d in B.load()["dests"]])
    if err:
        return _err(err)
    B.update(lambda c: c["apps"].__setitem__(name, s))
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/backup", methods=["POST"])
@login_required
def backup_now(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    if not B.ready(B.app_dest(B.load(), name)):
        return _err("Configura primero un servidor de respaldos")
    if B._run_lock.locked():
        return _err("Ya hay un respaldo o una restauración en curso; espera a que termine", 409)
    B.run_async(B.backup, name, "manual")
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/status")
@login_required
def status(name):
    if not D.valid_name(name):  # también apps que se están recuperando y aún no existen aquí
        return _err("La aplicación no existe", 404)
    busy, log = B.status(name)
    return jsonify({"ok": True, "busy": busy, "log": log, "state": B.load()["state"].get(name) or {},
                    "exists": bool(_app(name))})


# ── Recuperación ante desastres ─────────────────────────────────────────────

@appbackup_bp.route("/api/dests/<dest_id>/apps")
@login_required
def dr_apps(dest_id):
    dest = B.find_dest(B.load(), dest_id)
    if not B.ready(dest):
        return _err("Ese servidor de respaldos no existe", 404)
    try:
        return jsonify({"ok": True, "apps": B.remote_apps(dest), "docker": bool(D.compose_cmd())})
    except B.Fail as e:
        return _err(str(e), 502)


@appbackup_bp.route("/api/recover", methods=["POST"])
@login_required
def recover():
    d = request.get_json(silent=True) or {}
    name, stamp, dest_id = str(d.get("app", "")), str(d.get("stamp", "")), str(d.get("dest", ""))
    if not D.valid_name(name) or not B.STAMP_RE.fullmatch(stamp):
        return _err("Elige la app y el respaldo")
    if d.get("confirm") != name:
        return _err("Escribe el nombre de la aplicación para confirmar")
    if not B.ready(B.find_dest(B.load(), dest_id)):
        return _err("Ese servidor de respaldos no existe", 404)
    if D.lstat_kind(D.app_dir(name)) is not None:
        return _err(f"Ya existe una app «{name}» en este servidor: usa «Ver respaldos → Restaurar»", 409)
    if not D.compose_cmd():
        return _err("Instala Docker y Docker Compose en este servidor antes de recuperar apps", 409)
    if B._run_lock.locked():
        return _err("Ya hay un respaldo o una restauración en curso; espera a que termine", 409)
    B.run_async(B.recover, name, stamp, dest_id)
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/list")
@login_required
def remote_list(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    dest = _dest(name, request.args.get("dest"))
    if not dest:
        return _err("Configura primero un servidor de respaldos")
    try:
        return jsonify({"ok": True, "backups": B.list_remote(dest, name), "dest": dest["id"]})
    except B.Fail as e:
        return _err(str(e), 502)


@appbackup_bp.route("/api/apps/<name>/manifest/<stamp>")
@login_required
def manifest(name, stamp):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    dest = _dest(name, request.args.get("dest"))
    if not dest:
        return _err("Ese servidor de respaldos no existe", 404)
    try:
        return jsonify({"ok": True, "manifest": B.manifest(dest, name, stamp)})
    except B.Fail as e:
        return _err(str(e), 502)


@appbackup_bp.route("/api/apps/<name>/restore", methods=["POST"])
@login_required
def restore(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    d = request.get_json(silent=True) or {}
    stamp = str(d.get("stamp", ""))
    parts = [p for p in ("db", "volumes") if d.get(p)]
    if not B.STAMP_RE.fullmatch(stamp) or not parts:
        return _err("Elige el respaldo y qué restaurar")
    if d.get("confirm") != name:
        return _err("Escribe el nombre de la aplicación para confirmar")
    if B._run_lock.locked():
        return _err("Ya hay un respaldo o una restauración en curso; espera a que termine", 409)
    if D.pending(name):
        return _err("Hay un despliegue en curso o en cola para esta app; espera a que termine", 409)
    dest = _dest(name, d.get("dest"))
    if not dest:
        return _err("Ese servidor de respaldos no existe", 404)
    B.run_async(B.restore, name, stamp, parts, dest["id"])
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/delete/<stamp>", methods=["POST"])
@login_required
def delete(name, stamp):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    dest = _dest(name, request.args.get("dest"))
    if not dest:
        return _err("Ese servidor de respaldos no existe", 404)
    try:
        B.delete_remote(dest, name, stamp)
    except B.Fail as e:
        return _err(str(e), 502)
    return jsonify({"ok": True})
