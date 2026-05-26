"""Explorador de archivos (modo lectura)."""
import os
import stat
import pwd
import grp
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, abort
from flask_login import login_required

files_bp = Blueprint("files", __name__)


@files_bp.route("/")
@login_required
def index():
    return render_template("files.html")


@files_bp.route("/api/ls")
@login_required
def ls():
    path = request.args.get("path", "/")
    # Normalizar y resolver para evitar trucos con symlinks engañosos
    path = os.path.abspath(path)

    if not os.path.isdir(path):
        return jsonify({"error": "Ruta no es un directorio"}), 400

    try:
        entries = os.listdir(path)
    except PermissionError:
        return jsonify({"error": "Permiso denegado"}), 403

    items = []
    for name in sorted(entries):
        full = os.path.join(path, name)
        try:
            st = os.lstat(full)
            try:
                owner = pwd.getpwuid(st.st_uid).pw_name
            except KeyError:
                owner = str(st.st_uid)
            try:
                group = grp.getgrgid(st.st_gid).gr_name
            except KeyError:
                group = str(st.st_gid)
            items.append({
                "name": name,
                "is_dir": stat.S_ISDIR(st.st_mode),
                "is_link": stat.S_ISLNK(st.st_mode),
                "size": st.st_size,
                "owner": owner,
                "group": group,
                "perms": stat.filemode(st.st_mode),
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except (PermissionError, FileNotFoundError):
            continue

    parent = os.path.dirname(path) if path != "/" else None
    return jsonify({"path": path, "parent": parent, "items": items})
