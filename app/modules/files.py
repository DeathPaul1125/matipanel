"""Administrador de archivos: navegar, editar, crear, mover, permisos, subir/bajar."""
import os
import re
import stat
import pwd
import grp
import shutil
import secrets
import sys
import tempfile
import time
import subprocess
from datetime import datetime
from urllib.parse import quote
from flask import Blueprint, render_template, jsonify, request, send_file, Response
from flask_login import login_required, current_user
from ..util import run

files_bp = Blueprint("files", __name__)

MAX_EDIT_BYTES = 2 * 1024 * 1024  # 2 MB
BACKUP_SUFFIX = ".matipanel.bak"

# Escritura activa por defecto (panel de admin como Webmin). MATIPANEL_FILE_WRITE=0 la apaga.
WRITE_ENABLED = os.environ.get("MATIPANEL_FILE_WRITE", "1") == "1"

# Rutas que nunca se borran ni se cambian recursivamente.
PROTECTED = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/libx32",
             "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp",
             "/usr", "/usr/bin", "/usr/lib", "/usr/local", "/usr/sbin", "/var", "/var/lib", "/var/log"}
MAX_LS = 20_000  # entradas por carpeta (una carpeta con millones bloquearía el único worker)

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
O_BINARY = getattr(os, "O_BINARY", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
# Linux: la subida escribe relativa a descriptores de carpeta abiertos con O_NOFOLLOW, inmune a
# que un usuario cambie una carpeta por un symlink a mitad de la operación. Windows (pruebas): por ruta.
DIRFD = bool(O_NOFOLLOW and O_DIRECTORY) and os.open in os.supports_dir_fd


def _path(p):
    """Ruta absoluta normalizada (sin resolver symlinks: se opera sobre el link)."""
    if not isinstance(p, str) or not p or "\x00" in p:
        return None
    p = os.path.abspath(p)
    if p.startswith("//"):  # POSIX conserva un '//' inicial: '//etc' es /etc y debe contar como protegida
        p = "/" + p.lstrip("/")
    return p


def _open_regular(path):
    """Abre para leer solo si es un archivo regular (sigue symlinks).

    O_NONBLOCK: si entre la comprobación y la apertura alguien pone un FIFO en su lugar,
    open() no se queda bloqueado para siempre (con un solo worker colgaría todo el panel).
    """
    fd = os.open(path, os.O_RDONLY | O_NONBLOCK | O_BINARY)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(f"{os.path.basename(path)} no es un archivo regular")
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def _version(st):
    """Identifica lo que se abrió en el editor: otro inodo, mtime o tamaño = el archivo cambió."""
    return f"{st.st_ino}-{st.st_mtime_ns}-{st.st_size}"


def _disposition(name):
    """Content-Disposition seguro aunque el nombre tenga comillas, saltos de línea o UTF-8."""
    plain = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip() or "descarga"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


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
    total = len(entries)
    if total > MAX_LS:
        entries = sorted(entries)[:MAX_LS]

    owners, groups = {}, {}

    def owner_of(uid):
        if uid not in owners:
            try:
                owners[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                owners[uid] = str(uid)
        return owners[uid]

    def group_of(gid):
        if gid not in groups:
            try:
                groups[gid] = grp.getgrgid(gid).gr_name
            except KeyError:
                groups[gid] = str(gid)
        return groups[gid]

    items = []
    for name in entries:
        full = os.path.join(path, name)
        try:
            st = os.lstat(full)
            owner, group = owner_of(st.st_uid), group_of(st.st_gid)
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
    try:
        du = shutil.disk_usage(path)
        disk = {"total": du.total, "free": du.free}
    except OSError:
        disk = None
    return jsonify({"path": path, "parent": parent, "items": items, "disk": disk,
                    "write_enabled": WRITE_ENABLED, "total": total})


@files_bp.route("/api/read")
@login_required
def read():
    path = _path(request.args.get("path", ""))
    if not path or not os.path.isfile(path):
        return jsonify({"error": "No es un archivo"}), 400
    too_big = jsonify({"error": f"Archivo > {MAX_EDIT_BYTES // 1024} KB (descárgalo)"}), 413
    try:
        with _open_regular(path) as f:
            st = os.fstat(f.fileno())
            if st.st_size > MAX_EDIT_BYTES:
                return too_big
            raw = f.read(MAX_EDIT_BYTES + 1)  # el archivo puede crecer tras el fstat
    except PermissionError:
        return jsonify({"error": "Permiso denegado"}), 403
    except OSError:
        return jsonify({"error": "No es un archivo"}), 400
    if len(raw) > MAX_EDIT_BYTES:
        return too_big
    size = len(raw)
    if b"\x00" in raw[:8192]:
        return jsonify({"error": "Archivo binario (descárgalo)"}), 415
    # No-UTF-8 (p. ej. Latin-1): se muestra aproximado y el editor lo abre en solo
    # lectura, porque guardarlo reemplazaría para siempre los bytes no válidos.
    try:
        content, lossy = raw.decode("utf-8"), False
    except UnicodeDecodeError:
        content, lossy = raw.decode("utf-8", errors="replace"), True
    return jsonify({"path": path, "size": size, "content": content, "lossy": lossy,
                    "language": _detect_language(path), "version": _version(st)})


@files_bp.route("/api/write", methods=["POST"])
@login_required
def write():
    if (g := _write_guard()):
        return g
    data = request.get_json(silent=True) or {}
    path = _path(data.get("path", ""))
    content = data.get("content")
    if not path or not isinstance(content, str):
        return _bad("Faltan path o content")
    if len(content.encode("utf-8")) > MAX_EDIT_BYTES:
        return _bad("Contenido demasiado grande", 413)
    if not os.path.isfile(path):
        return _bad("Archivo no existe (usa 'Nuevo archivo')", 404)

    real = os.path.realpath(path)  # si es symlink, se escribe el destino real
    try:
        st = os.stat(real)
        # El editor manda la versión que leyó: si el archivo cambió (o alguien cambió el archivo
        # por un symlink a otro sitio) no se escribe a ciegas.
        version = data.get("version")
        if version and version != _version(st):
            return _bad("El archivo cambió en el disco desde que lo abriste (o ahora es otro archivo). "
                        "Ciérralo y vuelve a abrirlo; copia antes tus cambios si los necesitas.", 409)
        _write_backup(real, st)
        # Escritura atómica: temporal en el mismo dir + rename, conservando modo y dueño.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".matipanel.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            os.chmod(tmp, st.st_mode & 0o7777)
            if hasattr(os, "chown"):
                os.chown(tmp, st.st_uid, st.st_gid)
            os.replace(tmp, real)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        new_version = _version(os.stat(real))
    except OSError as e:
        return _bad(str(e), 500)
    return jsonify({"ok": True, "user": current_user.username,
                    "backup": real + BACKUP_SUFFIX, "version": new_version})


def _write_backup(real, st):
    """Copia real → real.matipanel.bak.

    Se escribe en un temporal propio (O_EXCL) y se renombra: rename reemplaza un symlink que un
    usuario haya dejado con el nombre .matipanel.bak en vez de seguirlo (shutil.copy2 lo seguía y
    root terminaba escribiendo, p. ej., en /etc/passwd).
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".matipanel.")
    try:
        with os.fdopen(fd, "wb") as out, _open_regular(real) as src:
            shutil.copyfileobj(src, out, 1 << 20)
        os.chmod(tmp, st.st_mode & 0o777)  # sin setuid/setgid en la copia
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, real + BACKUP_SUFFIX)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


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
    if not src or not os.path.lexists(src) or src in PROTECTED or not dest or not isinstance(dest, str):
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
    if not src or not dest or not isinstance(dest, str) or not os.path.lexists(src):
        return _bad("Ruta inválida")
    # Igual que rename(): un destino relativo se toma respecto al directorio del origen.
    dest = _path(dest if dest.startswith("/") else os.path.join(os.path.dirname(src), dest))
    if not dest:
        return _bad("Ruta inválida")
    if os.path.isdir(dest) and not os.path.islink(dest):
        dest = os.path.join(dest, os.path.basename(src))
    if os.path.lexists(dest):
        return _bad("El destino ya existe")
    is_dir = os.path.isdir(src) and not os.path.islink(src)
    if is_dir:
        rsrc, rdest = os.path.realpath(src), os.path.realpath(dest)
        if rdest == rsrc or rdest.startswith(rsrc.rstrip(os.sep) + os.sep):
            # copytree volvería a encontrar la copia dentro del origen: recursión que llena el disco
            return _bad("No se puede copiar una carpeta dentro de sí misma")
    try:
        if is_dir:
            shutil.copytree(src, dest, symlinks=True)
        elif os.path.isfile(src) and not os.path.islink(src):
            _copy_file_excl(src, dest)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)  # symlink: se copia el enlace
    except (OSError, shutil.Error) as e:
        return _bad(str(e), 500)
    return jsonify({"ok": True, "path": dest})


def _copy_file_excl(src, dest):
    """Copia un archivo regular creando el destino con O_EXCL|O_NOFOLLOW: si alguien deja un
    symlink con ese nombre justo después de la comprobación, falla en vez de escribir a través
    de él. Modo y fechas se aplican sobre el descriptor, no sobre la ruta."""
    with _open_regular(src) as fsrc:
        st = os.fstat(fsrc.fileno())
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as fdst:
                shutil.copyfileobj(fsrc, fdst, 1 << 20)
            if hasattr(os, "fchmod"):
                os.fchmod(fd, st.st_mode & 0o7777)
            if os.utime in os.supports_fd:
                os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
        except BaseException:
            os.close(fd)
            os.unlink(dest)
            raise
        os.close(fd)
        if not hasattr(os, "fchmod"):  # Windows (pruebas)
            os.chmod(dest, st.st_mode & 0o777)


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
    if os.path.islink(path):
        # chmod sigue el enlace: un usuario podría dejar un symlink a /etc/shadow entre sus
        # archivos y que un "chmod 644" masivo lo hiciera legible. Los enlaces no tienen modo propio.
        return _bad("Es un enlace simbólico: cambia los permisos del archivo al que apunta, no del enlace")
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
        error = _check_rel_dirs(parent, rel)
        if error:
            return _bad(error)
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
            _save_upload(f, parent, rel)
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


def _check_rel_dirs(parent, rel):
    """Las carpetas intermedias de una subida que ya existan no pueden ser symlinks: un usuario
    podría dejar 'web -> /etc' en su carpeta y una subida de 'web/cron.d/x' escribiría en /etc."""
    cur = parent
    for comp in rel.split(os.sep)[:-1]:
        cur = os.path.join(cur, comp)
        if os.path.islink(cur):
            return f"{os.path.relpath(cur, parent)} es un enlace simbólico; no se sube a través de enlaces"
        if os.path.lexists(cur) and not os.path.isdir(cur):
            return f"{os.path.relpath(cur, parent)} existe y no es una carpeta"
    return None


def _save_upload(f, parent, rel):
    """Guarda un archivo subido en parent/rel sin seguir symlinks en ningún componente.

    En Linux se recorre con descriptores (openat + O_NOFOLLOW), así que cambiar una carpeta por un
    symlink a mitad de la subida no redirige la escritura. El contenido va a un temporal O_EXCL y
    luego rename(): si el destino es un symlink se reemplaza el enlace, no lo que apunta.
    """
    parts = rel.split(os.sep)
    name, tmp = parts[-1], f".matipanel-up-{secrets.token_hex(8)}"
    if not DIRFD:  # Windows (pruebas): mismas reglas, por ruta
        d = parent
        for comp in parts[:-1]:
            d = os.path.join(d, comp)
            if os.path.islink(d):
                raise OSError(f"{comp} es un enlace simbólico")
            if not os.path.isdir(d):
                os.mkdir(d, 0o755)
        tmp_path, target = os.path.join(d, tmp), os.path.join(d, name)
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_BINARY, 0o644)
        try:
            with os.fdopen(fd, "wb") as out:
                f.save(out)
            if os.path.isdir(target) and not os.path.islink(target):
                raise IsADirectoryError(f"{name} es una carpeta")
            os.replace(tmp_path, target)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        return

    dfd = os.open(parent, os.O_RDONLY | O_DIRECTORY)
    try:
        for comp in parts[:-1]:
            try:
                os.mkdir(comp, 0o755, dir_fd=dfd)
            except FileExistsError:
                pass
            nfd = os.open(comp, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=dfd)  # ELOOP si es symlink
            os.close(dfd)
            dfd = nfd
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o666, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as out:
                f.save(out)
            try:
                old = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except FileNotFoundError:
                old = None
            if old and stat.S_ISDIR(old.st_mode):
                raise IsADirectoryError(f"{name} es una carpeta")
            if old and stat.S_ISREG(old.st_mode):  # sobrescritura: conservar modo y dueño
                os.chmod(tmp, old.st_mode & 0o777, dir_fd=dfd)
                os.chown(tmp, old.st_uid, old.st_gid, dir_fd=dfd, follow_symlinks=False)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


@files_bp.route("/api/download")
@login_required
def download():
    """Un archivo: descarga directa. Una carpeta o varios elementos: tar.gz generado al vuelo."""
    paths = [_path(p) for p in request.args.getlist("path")]
    if not paths or not all(p and os.path.lexists(p) for p in paths):
        return jsonify({"error": "No existe"}), 404
    names = [os.path.basename(p) for p in paths]
    if not all(names):  # "/": empaquetaría todo el sistema (incluidos /proc y /sys)
        return jsonify({"error": "No se puede descargar la raíz del sistema"}), 400
    parent = os.path.dirname(paths[0])
    if any(os.path.dirname(p) != parent for p in paths):
        return jsonify({"error": "Los elementos deben estar en la misma carpeta"}), 400
    if len(paths) == 1 and os.path.isfile(paths[0]):
        try:
            fobj = _open_regular(paths[0])
        except OSError as e:
            return jsonify({"error": str(e)}), 400
        return send_file(fobj, as_attachment=True, download_name=names[0],
                         mimetype="application/octet-stream")
    name = names[0] if len(names) == 1 else (os.path.basename(parent) or "seleccion")
    proc = subprocess.Popen(["tar", "-czf", "-", "-C", parent, "--", *names],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def stream():
        try:
            while chunk := proc.stdout.read(64 * 1024):
                yield chunk
        finally:
            proc.kill()
            proc.wait()

    return Response(stream(), mimetype="application/gzip", headers={
        "Content-Disposition": _disposition(f"{name}.tar.gz")})


@files_bp.route("/api/compress", methods=["POST"])
@login_required
def compress():
    """Comprime uno o varios elementos de una carpeta en .tar.gz o .zip dentro de esa carpeta."""
    if (g := _write_guard()):
        return g
    d = request.get_json(silent=True) or {}
    raw_paths = d.get("paths") or [d.get("path", "")]
    paths = [_path(p) for p in raw_paths] if isinstance(raw_paths, list) else []
    if not paths or not all(p and p != "/" and os.path.lexists(p) for p in paths):
        return _bad("Ruta inválida")
    parent = os.path.dirname(paths[0])
    if any(os.path.dirname(p) != parent for p in paths):
        return _bad("Los elementos deben estar en la misma carpeta")
    fmt = d.get("format", "tar.gz")
    if fmt not in ("tar.gz", "zip"):
        return _bad("Formato inválido")
    base = d.get("name") or (os.path.basename(paths[0]) if len(paths) == 1 else "archivos")
    if not isinstance(base, str) or not _valid_basename(base):
        return _bad("Nombre inválido")
    dest = os.path.join(parent, base if base.endswith("." + fmt) else f"{base}.{fmt}")
    if os.path.lexists(dest):
        return _bad(f"Ya existe {os.path.basename(dest)}")
    names = [os.path.basename(p) for p in paths]
    # El comprimido queda 0600 (solo root): puede contener archivos que otros usuarios no pueden
    # leer (claves, /etc/shadow…) y se crea en una carpeta que quizá sí pueden leer.
    if fmt == "zip":
        # "./": un nombre como "-TT=cmd" nunca se interpreta como opción; -nw: sin comodines propios de zip
        code, _, err = run(["zip", "-r", "-q", "-y", "-nw", dest, "--", *["./" + n for n in names]],
                           timeout=3600, cwd=parent)
        if code == 0:
            os.chmod(dest, 0o600)
    else:
        try:  # se crea antes con O_EXCL|O_NOFOLLOW: tar no puede terminar escribiendo a través de un symlink
            os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY, 0o600))
        except OSError as e:
            return _bad(str(e), 500)
        # --force-local: sin esto GNU tar toma "host:archivo" como un servidor remoto
        code, _, err = run(["tar", "--force-local", "-czf", dest, "-C", parent, "--", *names], timeout=3600)
        if code != 0 and os.path.isfile(dest) and not os.path.islink(dest):
            os.unlink(dest)
    return jsonify({"ok": code == 0, "error": err.strip(), "path": dest}), (200 if code == 0 else 500)


@files_bp.route("/api/search")
@login_required
def search():
    """Busca por nombre (sin distinguir mayúsculas) bajo `path`.

    ponytail: recorrido en Python con límites (300 resultados, 200 000 entradas, 15 s);
    para búsquedas por contenido usar la terminal (grep -r).
    """
    path = _path(request.args.get("path", "/"))
    q = request.args.get("q", "").strip().lower()
    if not path or not os.path.isdir(path) or len(q) < 2:
        return _bad("Escribe al menos 2 caracteres")
    results, seen, deadline = [], 0, time.time() + 15

    def over():
        return len(results) >= 300 or seen >= 200_000 or time.time() > deadline

    truncated = False
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        dirs[:] = [x for x in dirs if os.path.join(root, x) not in SKIP_SEARCH]
        # Límites también dentro de una carpeta: una sola con millones de entradas (creada por
        # cualquier usuario en /tmp) no debe colgar el único worker.
        for is_dir, names in ((True, dirs), (False, files)):
            for name in names:
                seen += 1
                if q in name.lower():
                    results.append({"path": os.path.join(root, name), "name": name, "dir": root, "is_dir": is_dir})
                if seen % 2000 == 0:
                    time.sleep(0)  # con gevent cede el turno a las demás peticiones
                if (len(results) >= 300 or seen % 2000 == 0) and over():
                    truncated = True
                    break
            if truncated:
                break
        if truncated or over():
            truncated = True
            break
    return jsonify({"results": results[:300], "truncated": truncated})


SKIP_SEARCH = {"/proc", "/sys", "/dev", "/run"}


@files_bp.route("/api/du")
@login_required
def du():
    """Tamaño real de una carpeta (du)."""
    path = _path(request.args.get("path", ""))
    if not path or not os.path.isdir(path):
        return _bad("No es una carpeta")
    code, out, err = run(["du", "-sb", "-x", "--", path], timeout=120)
    if code not in (0, 1) or not out.strip():  # 1 = algunos subdirectorios sin permiso
        return _bad(err.strip() or "du falló", 500)
    return jsonify({"ok": True, "bytes": int(out.split()[0])})


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon"}


@files_bp.route("/api/raw")
@login_required
def raw():
    """Vista previa de imágenes. Solo formatos raster: un SVG o HTML servido en línea
    ejecutaría scripts con la sesión del panel."""
    path = _path(request.args.get("path", ""))
    mime = IMAGE_TYPES.get(os.path.splitext(path or "")[1].lower())
    if not path or not mime or not os.path.isfile(path):
        return _bad("Solo se pueden previsualizar imágenes PNG, JPG, GIF, WebP, BMP o ICO")
    try:
        fobj = _open_regular(path)
    except OSError as e:
        return _bad(str(e))
    if os.fstat(fobj.fileno()).st_size > 20 * 1024 * 1024:
        fobj.close()
        return _bad("Imagen demasiado grande para previsualizar (máx. 20 MB)", 413)
    resp = send_file(fobj, mimetype=mime)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    return resp


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


# Revisión previa de un comprimido (se ejecuta en un proceso aparte con límite de tiempo: listar un
# .tar.gz obliga a descomprimirlo entero y no debe bloquear el worker). Imprime el motivo y sale con
# 1 si el comprimido intenta escribir fuera del destino:
#  - rutas absolutas o con '..';
#  - symlinks o hardlinks cuyo destino (resolviendo también los otros symlinks del comprimido) sale;
#  - rutas que en el disco pasan por un symlink ya existente que apunta fuera (p. ej. un usuario dejó
#    'www -> /etc' en su carpeta y el comprimido trae 'www/cron.d/x' sin la entrada de carpeta);
#  - dispositivos, FIFOs y archivos setuid/setgid (root los crearía con el dueño que diga el tar).
ARCHIVE_CHECK = r'''
import os, posixpath, stat, sys, tarfile, zipfile
path, dest, kind = sys.argv[1:4]
MAX = 100000
def fail(msg):
    print(msg[:500]); sys.exit(1)
def parts_of(name):
    n = name.replace("\\", "/") if kind == "zip" else name
    if n.startswith("/") or (kind == "zip" and len(n) > 1 and n[1] == ":"):
        fail(f"{name}: ruta absoluta")
    p = [x for x in n.split("/") if x not in ("", ".")]
    if ".." in p:
        fail(f"{name}: contiene '..'")
    return p
members = []
if kind == "zip":
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > MAX:
            fail(f"más de {MAX} elementos")
        for i in infos:
            mode = i.external_attr >> 16
            if i.create_system == 3 and stat.S_ISLNK(mode):
                if i.file_size > 4096:
                    fail(f"{i.filename}: enlace simbólico inválido")
                members.append((i.filename, "l", z.read(i).decode("utf-8", "replace")))
            else:
                members.append((i.filename, "d" if i.is_dir() else "f", ""))
else:
    with tarfile.open(path, "r:*") as t:
        for n, m in enumerate(t):
            if n >= MAX:
                fail(f"más de {MAX} elementos")
            if m.issym(): typ = "l"
            elif m.islnk(): typ = "h"
            elif m.isdir(): typ = "d"
            elif m.isreg(): typ = "f"
            else: fail(f"{m.name}: dispositivo, FIFO u otro tipo especial")
            if typ in "fd" and m.mode & 0o6000:
                fail(f"{m.name}: tiene el bit setuid/setgid")
            members.append((m.name, typ, m.linkname))
links = {}
for name, typ, target in members:
    if typ == "l":
        links["/".join(parts_of(name))] = target
def resolve(parts, follow_last):
    out, todo, hops = [], list(parts), 0
    while todo:
        c = todo.pop(0)
        if c in ("", "."):
            continue
        if c == "..":
            if not out:
                return None
            out.pop()
            continue
        key = "/".join(out + [c])
        if key in links and (todo or follow_last):
            hops += 1
            t = links[key]
            if hops > 64 or t.startswith("/"):
                return None
            todo = t.split("/") + todo
            continue
        out.append(c)
    return out
root = os.path.realpath(dest)
def inside(p):
    r = os.path.realpath(p)
    return r == root or r.startswith(root.rstrip(os.sep) + os.sep)
for name, typ, target in members:
    p = parts_of(name)
    if not p:
        continue
    if not inside(os.path.join(dest, *p)):
        fail(f"{name}: en el destino pasa por un enlace simbólico que apunta fuera de la carpeta")
    if typ == "l":
        if target.startswith("/") or resolve(p[:-1] + target.split("/"), True) is None:
            fail(f"{name}: enlace simbólico que apunta fuera de la carpeta ({target})")
        if not inside(os.path.join(dest, *p[:-1], *[x for x in target.split("/") if x])):
            fail(f"{name}: enlace simbólico que apunta fuera de la carpeta ({target})")
    elif typ == "h":
        tp = parts_of(target)
        if resolve(tp, False) is None or not inside(os.path.join(dest, *tp)):
            fail(f"{name}: enlace duro a un archivo fuera de la carpeta ({target})")
'''


def _check_archive(path, dest):
    kind = "zip" if path.lower().endswith(".zip") else "tar"
    try:
        r = subprocess.run([sys.executable, "-I", "-c", ARCHIVE_CHECK, path, dest, kind],
                           capture_output=True, text=True, errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return "la revisión del comprimido tardó demasiado"
    if r.returncode == 0:
        return None
    msg = r.stdout.strip() if r.returncode == 1 else ""
    if not msg:  # excepción de tarfile/zipfile: comprimido dañado o formato no reconocido
        lines = r.stderr.strip().splitlines()
        msg = "comprimido dañado o ilegible" + (f" ({lines[-1][:200]})" if lines else "")
    return "comprimido rechazado: " + msg


def _extract(path, dest, overwrite):
    """Revisa el contenido y extrae. Sin overwrite no pisa lo existente.

    tar: --no-same-owner (no se adoptan los dueños del comprimido) y --no-overwrite-dir (una
    entrada './' con modo 777 no cambia los permisos de la carpeta destino). unzip no restaura
    dueños ni bits setuid por defecto.
    """
    error = _check_archive(path, dest)
    if error:
        return 1, error
    if path.lower().endswith(".zip"):
        cmd = ["unzip", "-o" if overwrite else "-n", "-q", path, "-d", dest]
    else:
        cmd = ["tar", "--force-local", "--no-same-owner", "--no-overwrite-dir", "-xf", path, "-C", dest] \
            + ([] if overwrite else ["--skip-old-files"])
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
