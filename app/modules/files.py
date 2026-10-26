"""Administrador de archivos: navegar, editar, crear, mover, permisos, subir/bajar."""
import os
import re
import stat
import pwd
import grp
import shutil
import tempfile
import subprocess
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, send_file, Response
from flask_login import login_required, current_user
from ..util import run

files_bp = Blueprint("files", __name__)

MAX_EDIT_BYTES = 2 * 1024 * 1024  # 2 MB
BACKUP_SUFFIX = ".matipanel.bak"

# Escritura activa por defecto (panel de admin como Webmin). MATIPANEL_FILE_WRITE=0 la apaga.
WRITE_ENABLED = os.environ.get("MATIPANEL_FILE_WRITE", "1") == "1"

# Rutas que nunca se borran ni se cambian recursivamente.
PROTECTED = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/opt",
             "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var"}


def _path(p):
    """Ruta absoluta normalizada (sin resolver symlinks: se opera sobre el link)."""
    if not p or "\x00" in p:
        return None
    return os.path.abspath(p)


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _write_guard():
    if not WRITE_ENABLED:
        return _bad("Modo escritura deshabilitado (MATIPANEL_FILE_WRITE=0)", 403)
    return None


def _valid_basename(name):
    return bool(name) and name not in (".", "..") and "/" not in name and "\x00" not in name


@files_bp.route("/")
@login_required
def index():
    return render_template("files.html", write_enabled=WRITE_ENABLED)


@files_bp.route("/api/ls")
@login_required
def ls():
    path = _path(request.args.get("path", "/"))
    if not path or not os.path.isdir(path):
        return jsonify({"error": "Ruta no es un directorio"}), 400
    try:
        entries = os.listdir(path)
    except PermissionError:
        return jsonify({"error": "Permiso denegado"}), 403

    items = []
    for name in entries:
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
            is_link = stat.S_ISLNK(st.st_mode)
            items.append({
                "name": name,
                "is_dir": stat.S_ISDIR(st.st_mode) or (is_link and os.path.isdir(full)),
                "is_link": is_link,
                "target": os.readlink(full) if is_link else "",
                "size": st.st_size,
                "owner": owner, "group": group,
                "perms": stat.filemode(st.st_mode),
                "mode_oct": oct(st.st_mode & 0o7777)[2:],
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except (PermissionError, FileNotFoundError):
            continue
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    parent = os.path.dirname(path) if path != "/" else None
    return jsonify({"path": path, "parent": parent, "items": items,
                    "write_enabled": WRITE_ENABLED})


@files_bp.route("/api/read")
@login_required
def read():
    path = _path(request.args.get("path", ""))
    if not path or not os.path.isfile(path):
        return jsonify({"error": "No es un archivo"}), 400
    size = os.path.getsize(path)
    if size > MAX_EDIT_BYTES:
        return jsonify({"error": f"Archivo > {MAX_EDIT_BYTES // 1024} KB (descárgalo)"}), 413
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except PermissionError:
        return jsonify({"error": "Permiso denegado"}), 403
    if b"\x00" in raw[:8192]:
        return jsonify({"error": "Archivo binario (descárgalo)"}), 415
    # No-UTF-8 (p. ej. Latin-1): se muestra aproximado y el editor lo abre en solo
    # lectura, porque guardarlo reemplazaría para siempre los bytes no válidos.
    try:
        content, lossy = raw.decode("utf-8"), False
    except UnicodeDecodeError:
        content, lossy = raw.decode("utf-8", errors="replace"), True
    return jsonify({"path": path, "size": size, "content": content, "lossy": lossy,
                    "language": _detect_language(path)})


@files_bp.route("/api/write", methods=["POST"])
@login_required
def write():
    if (g := _write_guard()):
        return g
    data = request.get_json(silent=True) or {}
    path = _path(data.get("path", ""))
    content = data.get("content")
    if not path or content is None:
        return _bad("Faltan path o content")
    if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
        return _bad("Contenido demasiado grande", 413)
    if not os.path.isfile(path):
        return _bad("Archivo no existe (usa 'Nuevo archivo')", 404)

    real = os.path.realpath(path)  # si es symlink, se escribe el destino real
    try:
        shutil.copy2(real, real + BACKUP_SUFFIX)
        # Escritura atómica: temporal en el mismo dir + rename, conservando modo y dueño.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".matipanel.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            st = os.stat(real)
            os.chmod(tmp, st.st_mode & 0o7777)
            os.chown(tmp, st.st_uid, st.st_gid)
            os.replace(tmp, real)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except OSError as e:
        return _bad(str(e), 500)
    return jsonify({"ok": True, "user": current_user.username,
                    "backup": real + BACKUP_SUFFIX})


@files_bp.route("/api/create", methods=["POST"])
@login_required
def create():
    """Crea un directorio o un archivo vacío dentro de `dir`."""
    if (g := _write_guard()):
        return g
    data = request.get_json(silent=True) or {}
    parent, name = _path(data.get("dir", "")), data.get("name", "")
    if not parent or not os.path.isdir(parent) or not _valid_basename(name):
        return _bad("Nombre o directorio inválido")
    target = os.path.join(parent, name)
    if os.path.lexists(target):
        return _bad("Ya existe")
    try:
        if data.get("type") == "dir":
            os.mkdir(target, 0o755)
        else:
            with open(target, "x"):
                pass
    except OSError as e:
        return _bad(str(e), 500)
    return jsonify({"ok": True, "path": target})


@files_bp.route("/api/rename", methods=["POST"])
@login_required
def rename():
    """Renombra o mueve (dest puede ser ruta absoluta o un nombre en el mismo dir)."""
    if (g := _write_guard()):
        return g
    data = request.get_json(silent=True) or {}
    src = _path(data.get("path", ""))
    dest = data.get("dest", "")
    if not src or not os.path.lexists(src) or src in PROTECTED or not dest:
        return _bad("Origen inválido o protegido")
    dest = _path(dest if dest.startswith("/") else os.path.join(os.path.dirname(src), dest))
    if os.path.isdir(dest) and not os.path.islink(dest):
        dest = os.path.join(dest, os.path.basename(src))
    if os.path.lexists(dest):
        return _bad("El destino ya existe")
    try:
        shutil.move(src, dest)
    except OSError as e:
        return _bad(str(e), 500)
    return jsonify({"ok": True, "path": dest})


@files_bp.route("/api/copy", methods=["POST"])
@login_required
def copy():
    if (g := _write_guard()):
        return g
    data = request.get_json(silent=True) or {}
    src, dest = _path(data.get("path", "")), data.get("dest", "")
    if not src or not dest or not os.path.lexists(src):
        return _bad("Ruta inválida")
    # Igual que rename(): un destino relativo se toma respecto al directorio del origen.
    dest = _path(dest if dest.startswith("/") else os.path.join(os.path.dirname(src), dest))
    if not dest:
        return _bad("Ruta inválida")
    if os.path.isdir(dest) and not os.path.islink(dest):
        dest = os.path.join(dest, os.path.basename(src))
    if os.path.lexists(dest):
        return _bad("El destino ya existe")
    try:
        if os.path.isdir(src) and not os.path.islink(src):
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)
    except (OSError, shutil.Error) as e:
        return _bad(str(e), 500)
    return jsonify({"ok": True, "path": dest})


@files_bp.route("/api/delete", methods=["POST"])
@login_required
def delete():
    if (g := _write_guard()):
        return g
    path = _path((request.get_json(silent=True) or {}).get("path", ""))
    if not path or path in PROTECTED or not os.path.lexists(path):
        return _bad("Ruta inválida o protegida")
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError as e:
        return _bad(str(e), 500)
    return jsonify({"ok": True})


@files_bp.route("/api/chmod", methods=["POST"])
@login_required
def chmod():
    if (g := _write_guard()):
        return g
    data = request.get_json(silent=True) or {}
    path, mode = _path(data.get("path", "")), str(data.get("mode", ""))
    recursive = bool(data.get("recursive"))
    if not path or not os.path.lexists(path) or not re.fullmatch(r"[0-7]{3,4}", mode):
        return _bad("Ruta o modo inválido (ej. 644, 0755)")
    if recursive and path in PROTECTED:
        return _bad("No se permite chmod recursivo en una ruta del sistema")
    code, _, err = run(["chmod", *(["-R"] if recursive else []), mode, "--", path])
    return jsonify({"ok": code == 0, "error": err.strip()}), (200 if code == 0 else 500)


@files_bp.route("/api/chown", methods=["POST"])
@login_required
def chown():
    if (g := _write_guard()):
        return g
    data = request.get_json(silent=True) or {}
    path = _path(data.get("path", ""))
    owner, group = data.get("owner", ""), data.get("group", "")
    recursive = bool(data.get("recursive"))
    if not path or not os.path.lexists(path):
        return _bad("Ruta inválida")
    try:
        pwd.getpwnam(owner)
        grp.getgrnam(group)
    except KeyError:
        return _bad("Usuario o grupo inexistente")
    if recursive and path in PROTECTED:
        return _bad("No se permite chown recursivo en una ruta del sistema")
    code, _, err = run(["chown", "-h", *(["-R"] if recursive else []),
                        f"{owner}:{group}", "--", path])
    return jsonify({"ok": code == 0, "error": err.strip()}), (200 if code == 0 else 500)


@files_bp.route("/api/upload", methods=["POST"])
@login_required
def upload():
    """Sube archivos (o una carpeta completa con rutas relativas) al directorio `dir`.

    Campos: files (con filename = ruta relativa), overwrite=1, extract=none|keep|delete.
    Sin overwrite y con conflictos responde 409 con la lista, sin guardar nada.
    """
    if (g := _write_guard()):
        return g
    parent = _path(request.form.get("dir", ""))
    if not parent or not os.path.isdir(parent):
        return _bad("Directorio inválido")
    overwrite = request.form.get("overwrite") == "1"
    extract_mode = request.form.get("extract", "none")

    # Validar todo antes de escribir nada
    plan = []
    for f in request.files.getlist("files"):
        rel = _safe_relpath(f.filename or "")
        if not rel:
            return _bad(f"Nombre inválido: {f.filename!r}")
        plan.append((f, rel, os.path.join(parent, rel)))
    if not plan:
        return _bad("No se recibieron archivos")
    conflicts = [rel for _, rel, target in plan if os.path.lexists(target)]
    if conflicts and not overwrite:
        return jsonify({"ok": False, "conflicts": conflicts,
                        "error": f"{len(conflicts)} archivo(s) ya existen"}), 409

    saved, extracted, errors = [], [], []
    for f, rel, target in plan:
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.islink(target):
                os.unlink(target)  # sobrescribir el link, no lo que apunta
            f.save(target)
            saved.append(rel)
        except OSError as e:
            errors.append(f"{rel}: {e}")
            continue
        if extract_mode in ("keep", "delete") and ARCHIVE_RE.search(rel.lower()):
            code, err = _extract(target, os.path.dirname(target), overwrite)
            if code == 0:
                extracted.append(rel)
                if extract_mode == "delete":
                    os.unlink(target)
            else:
                errors.append(f"{rel}: no se pudo extraer ({err})")
    return jsonify({"ok": not errors, "saved": saved, "extracted": extracted,
                    "error": "; ".join(errors)}), (200 if not errors else 500)


def _safe_relpath(name):
    """Ruta relativa segura ('a/b.txt'); None si intenta salir del directorio."""
    parts = [p for p in name.replace("\\", "/").split("/") if p]
    if not parts or not all(_valid_basename(p) for p in parts):
        return None
    return os.path.join(*parts)


@files_bp.route("/api/download")
@login_required
def download():
    """Archivo: descarga directa. Directorio: tar.gz generado al vuelo."""
    path = _path(request.args.get("path", ""))
    if not path or not os.path.exists(path):
        return jsonify({"error": "No existe"}), 404
    name = os.path.basename(path)
    if not name:  # "/": empaquetaría todo el sistema (incluidos /proc y /sys)
        return jsonify({"error": "No se puede descargar la raíz del sistema"}), 400
    if os.path.isfile(path):
        return send_file(path, as_attachment=True)
    proc = subprocess.Popen(["tar", "-czf", "-", "-C", os.path.dirname(path), "--", name],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def stream():
        try:
            while chunk := proc.stdout.read(64 * 1024):
                yield chunk
        finally:
            proc.kill()
            proc.wait()

    return Response(stream(), mimetype="application/gzip", headers={
        "Content-Disposition": f'attachment; filename="{name}.tar.gz"'})


@files_bp.route("/api/compress", methods=["POST"])
@login_required
def compress():
    """Comprime un archivo/directorio a <nombre>.tar.gz en el mismo directorio."""
    if (g := _write_guard()):
        return g
    path = _path((request.get_json(silent=True) or {}).get("path", ""))
    if not path or not os.path.lexists(path) or path == "/":
        return _bad("Ruta inválida")
    dest = path + ".tar.gz"
    if os.path.lexists(dest):
        return _bad(f"Ya existe {dest}")
    code, _, err = run(["tar", "-czf", dest, "-C", os.path.dirname(path), "--",
                        os.path.basename(path)], timeout=3600)
    return jsonify({"ok": code == 0, "error": err.strip(), "path": dest}), (200 if code == 0 else 500)


@files_bp.route("/api/extract", methods=["POST"])
@login_required
def extract():
    """Extrae .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz o .zip en su mismo directorio."""
    if (g := _write_guard()):
        return g
    path = _path((request.get_json(silent=True) or {}).get("path", ""))
    if not path or not os.path.isfile(path):
        return _bad("Archivo inválido")
    if not ARCHIVE_RE.search(path.lower()):
        return _bad("Formato no soportado")
    code, err = _extract(path, os.path.dirname(path), overwrite=True)
    return jsonify({"ok": code == 0, "error": err}), (200 if code == 0 else 500)


ARCHIVE_RE = re.compile(r"\.(zip|tar|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz)$")


def _extract(path, dest, overwrite):
    """tar/unzip ya rechazan rutas absolutas y '..'. Sin overwrite no pisa lo existente."""
    if path.lower().endswith(".zip"):
        cmd = ["unzip", "-o" if overwrite else "-n", "-q", path, "-d", dest]
    else:
        cmd = ["tar", "-xf", path, "-C", dest] + ([] if overwrite else ["--skip-old-files"])
    code, _, err = run(cmd, timeout=3600)
    return code, err.strip()


def _detect_language(path):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).lower()
    mapping = {
        ".py": "python", ".sh": "shell", ".bash": "shell",
        ".js": "javascript", ".ts": "javascript", ".json": "json",
        ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".ini": "ini",
        ".html": "html", ".css": "css", ".xml": "xml", ".php": "php",
        ".conf": "conf", ".md": "markdown", ".sql": "sql",
    }
    if name in ("dockerfile", "containerfile"):
        return "dockerfile"
    return mapping.get(ext, "")
