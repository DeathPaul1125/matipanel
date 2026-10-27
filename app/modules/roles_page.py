"""Pantalla «Permisos del panel» (solo administradores; la regla está en app/roles.py)."""
import grp
import pwd

from flask import Blueprint, current_app, render_template, jsonify, request
from flask_login import login_required, current_user

from .. import roles
from ..audit import MODULES
from ..auth import ALLOWED_GROUPS

roles_bp = Blueprint("roles", __name__)


def _modules():
    """Módulos asignables: los registrados, menos los que todos tienen y la propia pantalla de permisos."""
    skip = roles.ALWAYS | roles.ADMIN_ONLY | {"panel"}
    return [{"key": k, "label": v} for k, v in sorted(MODULES.items(), key=lambda kv: kv[1])
            if k not in skip and k in current_app.blueprints]


def _panel_users():
    """Usuarios que pueden entrar por grupo (matipanel, sudo, wheel, admin), con sus grupos de acceso."""
    out = {}
    for g in grp.getgrall():
        if g.gr_name in ALLOWED_GROUPS:
            for m in g.gr_mem:
                out.setdefault(m, set()).add(g.gr_name)
            for p in pwd.getpwall():
                if p.pw_gid == g.gr_gid:
                    out.setdefault(p.pw_name, set()).add(g.gr_name)
    return {u: sorted(gs) for u, gs in out.items()}


@roles_bp.route("/")
@login_required
def index():
    return render_template("roles.html", role_names=roles.ROLES, modules=_modules())


@roles_bp.route("/api/config", methods=["GET", "POST"])
@login_required
def config():
    if request.method == "POST":
        cfg, err = roles.validate(request.get_json(silent=True) or {}, {m["key"] for m in _modules()}, current_user.get_id())
        if err:
            return jsonify({"ok": False, "error": err}), 400
        try:
            roles.save(cfg)
        except OSError as e:
            return jsonify({"ok": False, "error": f"No se pudo guardar: {e}"}), 500
    cfg = roles.load()
    users = _panel_users()
    rows = [{"user": u, "groups": users.get(u, []), "configured": u in cfg["users"], "allowed_by_group": u in users,
             **roles.role_of(u, cfg)} for u in sorted(set(users) | set(cfg["users"]))]
    return jsonify({"ok": True, "default": cfg["default"], "users": rows, "me": current_user.get_id()})
