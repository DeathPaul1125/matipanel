"""Contabo: controlar los VPS de la cuenta desde el panel (energía y snapshots).

El panel vive DENTRO de uno de esos VPS. Por eso la instancia propia se reconoce por su IP y
apagarla o revertirla exige una confirmación explícita: una vez apagada, el panel ya no está
para volver a encenderla (habría que hacerlo desde my.contabo.com u otro MatiPanel).

Permisos: el control central de roles deja a «solo lectura» ver el estado (GET) y nada más.
"""
import ipaddress
import socket
import time

import psutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from .. import contabo as api

contabo_bp = Blueprint("contabo", __name__)

CACHE_S = 10  # s: varias pestañas o refrescos seguidos no multiplican las llamadas a Contabo
SELF_GUARDED = {"stop", "shutdown"}
_cache = {"at": 0.0, "items": None}


def _norm_ip(s):
    try:
        return str(ipaddress.ip_address(str(s).split("%")[0].strip()))
    except ValueError:
        return ""


def _local_ips():
    ips = set()
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family in (socket.AF_INET, socket.AF_INET6):
                ip = _norm_ip(a.address)
                if ip and not ipaddress.ip_address(ip).is_loopback:
                    ips.add(ip)
    return ips


def _instances(creds, force=False):
    now = time.time()
    if not force and _cache["items"] is not None and now - _cache["at"] < CACHE_S:
        return _cache["items"]
    local = _local_ips()
    items = api.list_instances(creds)
    for i in items:
        i["self"] = bool({_norm_ip(i["ip"]), _norm_ip(i["ip6"])} & local - {""})
    items.sort(key=lambda i: (not i["self"], i["name"].lower()))
    _cache.update(at=now, items=items)
    return items


def _forget():
    _cache.update(at=0.0, items=None)


def _fail(e):
    """Excepción → respuesta JSON. Los datos mal escritos son 400; los fallos de Contabo, 502."""
    if isinstance(e, ValueError):
        return jsonify({"ok": False, "error": str(e)}), 400
    if isinstance(e, api.ApiError):
        return jsonify({"ok": False, "error": str(e)}), 502
    return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500


def _creds():
    creds = api.load_creds()
    if not creds:
        raise ValueError("Primero conecta la cuenta de Contabo.")
    return creds


def _find(creds, iid):
    """La instancia debe ser de la cuenta: así se sabe también si es este mismo servidor."""
    inst = next((i for i in _instances(creds) if i["id"] == iid), None)
    if inst is None:
        inst = next((i for i in _instances(creds, force=True) if i["id"] == iid), None)
    if inst is None:
        raise ValueError("Esa instancia no está en la cuenta de Contabo.")
    return inst


# ── Pantalla ──

@contabo_bp.route("/")
@login_required
def index():
    return render_template("contabo.html", cfg=api.public_creds(), hostname=socket.gethostname())


@contabo_bp.route("/api/instances")
@login_required
def instances():
    creds = api.load_creds()
    if not creds:
        return jsonify({"ok": True, "configured": False, "instances": []})
    try:
        items = _instances(creds, force=request.args.get("force") == "1")
    except api.ApiError as e:
        return jsonify({"ok": False, "configured": True, "error": str(e), "instances": []})
    return jsonify({"ok": True, "configured": True, "ts": time.time(), "instances": items})


# ── Cuenta ──

@contabo_bp.route("/api/config", methods=["POST"])
@login_required
def config():
    try:
        creds = api.validate_creds(request.get_json(silent=True), api.load_creds())
        api.token(creds, fresh=True)  # se comprueban contra Contabo antes de guardarlas
        api.save_creds(creds)
    except (ValueError, api.ApiError, OSError) as e:
        return _fail(e)
    _forget()
    return jsonify({"ok": True, "cfg": api.public_creds()})


@contabo_bp.route("/api/config/delete", methods=["POST"])
@login_required
def config_delete():
    try:
        api.delete_creds()
    except OSError as e:
        return _fail(e)
    _forget()
    return jsonify({"ok": True})


# ── Energía ──

@contabo_bp.route("/api/instances/<int:iid>/action", methods=["POST"])
@login_required
def action(iid):
    data = request.get_json(silent=True) or {}
    act = str(data.get("action") or "")
    try:
        if act not in api.ACTIONS:
            raise ValueError("Acción inválida.")
        creds = _creds()
        inst = _find(creds, iid)
        if inst["self"] and act in SELF_GUARDED and data.get("confirm_self") is not True:
            return jsonify({"ok": False, "need_confirm_self": True,
                            "error": "Esa instancia es este mismo servidor: al apagarla el panel deja de responder "
                                     "y no podrás encenderla desde aquí."}), 409
        api.instance_action(creds, iid, act)
    except (ValueError, api.ApiError) as e:
        return _fail(e)
    _forget()
    return jsonify({"ok": True})


# ── Snapshots ──

@contabo_bp.route("/api/instances/<int:iid>/snapshots")
@login_required
def snapshots(iid):
    try:
        return jsonify({"ok": True, "snapshots": api.list_snapshots(_creds(), iid)})
    except (ValueError, api.ApiError) as e:
        return _fail(e)


@contabo_bp.route("/api/instances/<int:iid>/snapshots", methods=["POST"])
@login_required
def snapshot_create(iid):
    data = request.get_json(silent=True) or {}
    try:
        creds = _creds()
        _find(creds, iid)
        snap = api.create_snapshot(creds, iid, data.get("name"), data.get("description"))
    except (ValueError, api.ApiError) as e:
        return _fail(e)
    return jsonify({"ok": True, "snapshot": snap})


@contabo_bp.route("/api/instances/<int:iid>/snapshots/<sid>/delete", methods=["POST"])
@login_required
def snapshot_delete(iid, sid):
    try:
        creds = _creds()
        _find(creds, iid)
        api.delete_snapshot(creds, iid, sid)
    except (ValueError, api.ApiError) as e:
        return _fail(e)
    return jsonify({"ok": True})


@contabo_bp.route("/api/instances/<int:iid>/snapshots/<sid>/rollback", methods=["POST"])
@login_required
def snapshot_rollback(iid, sid):
    data = request.get_json(silent=True) or {}
    try:
        creds = _creds()
        inst = _find(creds, iid)
        if inst["self"] and data.get("confirm_self") is not True:
            return jsonify({"ok": False, "need_confirm_self": True,
                            "error": "Esa instancia es este mismo servidor: revertirla devuelve TODO el disco "
                                     "(incluido este panel y sus datos) al momento del snapshot."}), 409
        api.rollback_snapshot(creds, iid, sid)
    except (ValueError, api.ApiError) as e:
        return _fail(e)
    _forget()
    return jsonify({"ok": True})
