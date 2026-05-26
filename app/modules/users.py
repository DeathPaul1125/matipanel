"""Listado de usuarios y grupos del sistema."""
import pwd
import grp
from flask import Blueprint, render_template, jsonify
from flask_login import login_required

users_bp = Blueprint("users", __name__)


@users_bp.route("/")
@login_required
def index():
    return render_template("users.html")


@users_bp.route("/api/users")
@login_required
def list_users():
    users = []
    for u in pwd.getpwall():
        users.append({
            "name": u.pw_name,
            "uid": u.pw_uid,
            "gid": u.pw_gid,
            "home": u.pw_dir,
            "shell": u.pw_shell,
            "gecos": u.pw_gecos,
            "type": "sistema" if u.pw_uid < 1000 else "regular",
        })
    users.sort(key=lambda x: x["uid"])
    return jsonify({"users": users})


@users_bp.route("/api/groups")
@login_required
def list_groups():
    groups = []
    for g in grp.getgrall():
        groups.append({
            "name": g.gr_name,
            "gid": g.gr_gid,
            "members": g.gr_mem,
        })
    groups.sort(key=lambda x: x["gid"])
    return jsonify({"groups": groups})
