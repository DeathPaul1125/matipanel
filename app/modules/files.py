"""Explorador de archivos con editor opt-in (modo escritura)."""
import os
import stat
import pwd
import grp
import shutil
import tempfile
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user

files_bp = Blueprint("files", __name__)

MAX_EDIT_BYTES = 2 * 1024 * 1024  # 2 MB
BACKUP_SUFFIX = ".matipanel.bak"

WRITE_ENABLED = os.environ.get("MATIPANEL_FILE_WRITE", "0") == "1"


@files_bp.route("/")
@login_required
def index():
    return render_template("files.html", write_enabled=WRITE_ENABLED)


@files_bp.route("/api/ls")
@login_required
def ls():
    path = request.args.get("path", "/")
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
                "owner": owner, "group": group,
                "perms": stat.filemode(st.st_mode),
                "mode_oct": oct(st.st_mode & 0o777),
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except (PermissionError, FileNotFoundError):
            continue

    parent = os.path.dirname(path) if path != "/" else None
    return jsonify({"path": path, "parent": parent, "items": items,
                    "write_enabled": WRITE_ENABLED})


@files_bp.route("/api/read")
@login_required
def read():
    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "Falta path"}), 400
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return jsonify({"error": "No es un archivo"}), 400
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    if size > MAX_EDIT_BYTES:
        return jsonify({"error": f"Archivo > {MAX_EDIT_BYTES // 1024} KB (no editable)"}), 413

    try:
        with open(path, "rb") as f:
            raw = f.read()
        if b"\x00" in raw[:8192]:
            return jsonify({"error": "Archivo binario"}), 415
        text = raw.decode("utf-8", errors="replace")
    except PermissionError:
        return jsonify({"error": "Permiso denegado"}), 403

    return jsonify({"path": path, "size": size, "content": text,
                    "language": _detect_language(path)})


@files_bp.route("/api/write", methods=["POST"])
@login_required
def write():
    if not WRITE_ENABLED:
        return jsonify({"error": "Modo escritura deshabilitado. "
                                 "Reinicia el servicio con MATIPANEL_FILE_WRITE=1"}), 403
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    content = data.get("content")
    if not path or content is None:
        return jsonify({"error": "Faltan path o content"}), 400
    if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
        return jsonify({"error": "Contenido demasiado grande"}), 413

    path = os.path.abspath(path)
    if not os.path.exists(path):
        return jsonify({"error": "Archivo no existe (no se crean archivos nuevos)"}), 404

    try:
        if os.path.exists(path):
            shutil.copy2(path, path + BACKUP_SUFFIX)
        directory = os.path.dirname(path) or "/"
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".matipanel.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            st = os.stat(path)
            os.chmod(tmp, st.st_mode & 0o777)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except PermissionError:
                pass
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except PermissionError:
        return jsonify({"error": "Permiso denegado"}), 403
    except OSError as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "user": current_user.username,
                    "backup": path + BACKUP_SUFFIX})


def _detect_language(path):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).lower()
    mapping = {
        ".py": "python", ".sh": "shell", ".bash": "shell",
        ".js": "javascript", ".ts": "javascript", ".json": "json",
        ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
        ".html": "htmlmixed", ".css": "css", ".xml": "xml",
        ".conf": "nginx", ".nginx": "nginx",
        ".md": "markdown", ".sql": "sql",
    }
    if name in ("dockerfile", "containerfile"):
        return "dockerfile"
    if name in ("nginx.conf",) or name.endswith(".conf"):
        return "nginx"
    return mapping.get(ext, "")
