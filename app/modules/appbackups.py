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
        apps.append({"name": name, "database": app.get("database", "none"), "deployed": bool(app.get("active_slot")),
                     "volumes": [v.split("_", 1)[1] for v in B.app_volumes(app)],
                     "settings": B.app_settings(cfg, name), "state": cfg["state"].get(name) or {}, "busy": busy})
    return jsonify({"dest": cfg["dest"], "configured": B.configured(cfg), "pubkey": B.public_key(),
                    "fingerprints": B.fingerprints(), "apps": apps})


@appbackup_bp.route("/api/dest", methods=["POST"])
@login_required
def dest():
    d, err = B.validate_dest(request.get_json(silent=True) or {})
    if err:
        return _err(err)
    old = B.load()["dest"]
    if (old.get("host"), old.get("port")) != (d["host"], d["port"]):
        B.forget_host()  # otro servidor: se fija su huella en la próxima conexión
    B.update(lambda c: c.__setitem__("dest", d))
    return jsonify({"ok": True})


@appbackup_bp.route("/api/test", methods=["POST"])
@login_required
def test():
    cfg = B.load()
    if not cfg["dest"]["host"]:
        return _err("Guarda primero el servidor de respaldos")
    try:
        return jsonify({"ok": True, **B.test(cfg["dest"])})
    except B.Fail as e:
        return _err(str(e), 502)


@appbackup_bp.route("/api/forget-host", methods=["POST"])
@login_required
def forget_host():
    B.forget_host()
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/settings", methods=["POST"])
@login_required
def settings(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    s, err = B.validate_app(request.get_json(silent=True) or {})
    if err:
        return _err(err)
    B.update(lambda c: c["apps"].__setitem__(name, s))
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/backup", methods=["POST"])
@login_required
def backup_now(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    if not B.configured():
        return _err("Configura primero el servidor de respaldos")
    if B._run_lock.locked():
        return _err("Ya hay un respaldo o una restauración en curso; espera a que termine", 409)
    B.run_async(B.backup, name, "manual")
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/status")
@login_required
def status(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    busy, log = B.status(name)
    return jsonify({"ok": True, "busy": busy, "log": log, "state": B.load()["state"].get(name) or {}})


@appbackup_bp.route("/api/apps/<name>/list")
@login_required
def remote_list(name):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    cfg = B.load()
    if not B.configured(cfg):
        return _err("Configura primero el servidor de respaldos")
    try:
        return jsonify({"ok": True, "backups": B.list_remote(cfg["dest"], name)})
    except B.Fail as e:
        return _err(str(e), 502)


@appbackup_bp.route("/api/apps/<name>/manifest/<stamp>")
@login_required
def manifest(name, stamp):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    try:
        return jsonify({"ok": True, "manifest": B.manifest(B.load()["dest"], name, stamp)})
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
    B.run_async(B.restore, name, stamp, parts)
    return jsonify({"ok": True})


@appbackup_bp.route("/api/apps/<name>/delete/<stamp>", methods=["POST"])
@login_required
def delete(name, stamp):
    if not _app(name):
        return _err("La aplicación no existe", 404)
    try:
        B.delete_remote(B.load()["dest"], name, stamp)
    except B.Fail as e:
        return _err(str(e), 502)
    return jsonify({"ok": True})
