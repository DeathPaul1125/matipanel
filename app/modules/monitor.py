"""Pantalla del monitor de servicios (el motor está en app/monitor.py)."""
import os

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from .. import monitor as M

monitor_bp = Blueprint("monitor", __name__)
RANGES = (86400, 604800)


@monitor_bp.record_once
def _start(_state):
    if os.environ.get("MATIPANEL_MONITOR_WORKER", "1") != "0":
        M.start()


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _check(cid):
    return M.get(cid) if M.ID_RE.fullmatch(cid or "") else None


@monitor_bp.route("/")
@login_required
def index():
    return render_template("monitor.html", types=M.TYPES, intervals=M.INTERVALS, defaults=M.DEFAULTS,
                           max_checks=M.MAX_CHECKS)


@monitor_bp.route("/api/checks", methods=["GET", "POST"])
@login_required
def checks():
    if request.method == "GET":
        return jsonify({**M.overview(), "worker": M._started})
    check, err = M.add(request.get_json(silent=True) or {})
    return _err(err) if err else jsonify({"ok": True, "check": check})


@monitor_bp.route("/api/checks/<cid>", methods=["POST"])
@login_required
def edit(cid):
    if not _check(cid):
        return _err("No existe", 404)
    check, err = M.edit(cid, request.get_json(silent=True) or {})
    return _err(err) if err else jsonify({"ok": True, "check": check})


@monitor_bp.route("/api/checks/<cid>/pause", methods=["POST"])
@login_required
def pause(cid):
    d = request.get_json(silent=True) or {}
    if not _check(cid) or not isinstance(d.get("enabled"), bool):
        return _err("Comprobación o valor inválido")
    M.set_enabled(cid, d["enabled"])
    return jsonify({"ok": True})


@monitor_bp.route("/api/checks/<cid>/delete", methods=["POST"])
@login_required
def delete(cid):
    if not _check(cid):
        return _err("No existe", 404)
    M.delete(cid)
    return jsonify({"ok": True})


@monitor_bp.route("/api/checks/<cid>/test", methods=["POST"])
@login_required
def test(cid):
    """Probar ahora: corre la comprobación en el momento. Si está activa, el resultado cuenta como uno más."""
    c = _check(cid)
    if not c:
        return _err("No existe", 404)
    r = M.run_check(c)
    status = M.record(c, r) if c["enabled"] else "paused"
    return jsonify({"ok": True, "result": r, "status": status})


@monitor_bp.route("/api/checks/<cid>/history")
@login_required
def history(cid):
    c = _check(cid)
    rng = request.args.get("range", 86400, type=int)
    if not c or rng not in RANGES:
        return _err("Parámetros inválidos")
    bucket, points = M.history(cid, rng, c["interval"])
    return jsonify({"ok": True, "bucket": bucket, "points": points, "incidents": M.incidents(cid)})
