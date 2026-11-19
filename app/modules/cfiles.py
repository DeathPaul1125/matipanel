"""Archivos de contenedores: navegar, ver, editar, subir y bajar archivos DENTRO de un contenedor.

Todo pasa por `docker exec -u 0 <contenedor> sh -c '…' sh <ruta>` y `docker cp`: Docker resuelve las rutas
y los enlaces simbólicos dentro del contenedor. Nunca se usa /proc/<pid>/root, donde un enlace absoluto
creado en el contenedor (p. ej. a /etc/shadow) se resolvería contra el disco del servidor.

Las rutas van siempre como argumento ("$1"), nunca dentro del texto del script. El nombre del contenedor
se valida contra los que están en marcha. Necesita `sh` y `stat` en la imagen (Debian, Alpine/busybox,
serversideup, mariadb, postgres…); en imágenes distroless lo dice y no lista nada.
"""
import json
import os
import posixpath
import subprocess
import tarfile
import tempfile
import time

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import login_required

from .files import PROTECTED, WRITE_ENABLED, _disposition
from .terminal import running_containers

cfiles_bp = Blueprint("cfiles", __name__)

MAX_EDIT = 2 * 1024 * 1024
MAX_LS = 20_000
MAX_UPLOAD = 100 * 1024 * 1024
LS_SCRIPT = ('cd -- "$1" 2>/dev/null || { echo "MPERR:La carpeta no existe" >&2; exit 2; }; '
             'for f in * .[!.]* ..?*; do [ -e "$f" ] || [ -L "$f" ] || continue; '
             'if [ -d "$f" ]; then printf "d|"; else printf "-|"; fi; '
             "stat -c '%F|%s|%Y|%a|%U|%G|%n' -- \"$f\"; done | head -n " + str(MAX_LS + 1))
READ_SCRIPT = ('[ -f "$1" ] || { echo "MPERR:No es un archivo" >&2; exit 2; }; '
               "stat -c '%Y-%s' -- \"$1\"; head -c " + str(MAX_EDIT + 1) + ' -- "$1"')
OWNER = '"$(stat -c %u:%g -- "$(dirname -- "$1")")"'  # dueño de la carpeta: así la app (www-data) puede usarlo
WRITE_SCRIPT = ("v=$(stat -c '%Y-%s' -- \"$1\" 2>/dev/null); "
                'if [ "$v" != "$2" ]; then echo "MPERR:CHANGED" >&2; exit 3; fi; cat > "$1"')
CREATE_SCRIPT = ('[ -e "$1" ] && { echo "MPERR:Ya existe" >&2; exit 4; }; '
                 'if [ "$2" = dir ]; then mkdir -- "$1"; else : > "$1"; fi && chown ' + OWNER + ' -- "$1"')
RENAME_SCRIPT = '[ -e "$2" ] && { echo "MPERR:Ya existe un elemento con ese nombre" >&2; exit 4; }; mv -- "$1" "$2"'


def _bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


# ── Funciones puras (probadas en tests/test_cfiles.py) ──────────────────

def norm(path):
    """Ruta absoluta normalizada del contenedor, o None si es inválida."""
    if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "\x00\n\r"):
        return None
    p = posixpath.normpath(path)
    return "/" + p.lstrip("/") if p.startswith("//") else p


def is_protected(path):
    return path in PROTECTED


def parse_ls(out):
    """Líneas «d|tipo|tamaño|mtime|permisos|usuario|grupo|nombre» → entradas (carpetas primero)."""
    entries = []
    for line in out.splitlines():
        f = line.split("|", 7)
        if len(f) != 8 or not f[7] or f[7] in (".", ".."):
            continue
        isdir, ftype, size, mtime, mode, user, group, name = f
        kind = {"directory": "dir", "symbolic link": "link"}.get(ftype, "file" if ftype.startswith("regular") else "other")
        entries.append({"name": name, "kind": kind, "is_dir": isdir == "d",
                        "size": int(size) if size.isdigit() else 0, "mtime": int(mtime) if mtime.isdigit() else 0,
                        "mode": mode, "user": user, "group": group})
    return sorted(entries, key=lambda e: (not e["is_dir"], e["name"].lower()))


def mount_of(path, mounts):
    """Montaje (volumen o carpeta del servidor) que contiene path, o None. El más específico gana."""
    best = None
    for m in mounts:
        d = m["dest"].rstrip("/") or "/"
        if path == d or path.startswith(d + "/") or d == "/":
            if best is None or len(d) > len(best["dest"]):
                best = m
    return best


def parse_mounts(text):
    try:
        data = json.loads(text or "[]") or []
    except ValueError:
        return []
    return [{"dest": m.get("Destination", ""), "type": m.get("Type", ""), "name": m.get("Name") or m.get("Source", ""),
             "rw": bool(m.get("RW"))} for m in data if isinstance(m, dict) and str(m.get("Destination", "")).startswith("/")]


# ── Docker ──────────────────────────────────────────────────────────────

def _container(name):
    return name if isinstance(name, str) and name in {c["name"] for c in running_containers()} else None


def _exec(c, script, *args, input=None, timeout=30):
    """docker exec como root con sh; los argumentos llegan como $1, $2… → (código, stdout bytes, stderr str)."""
    cmd = ["docker", "exec", *(["-i"] if input is not None else []), "-u", "0", c, "sh", "-c", script, "sh", *args]
    try:
        r = subprocess.run(cmd, input=input, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, b"", "Tiempo de espera agotado"
    except OSError as e:
        return 127, b"", str(e)
    return r.returncode, r.stdout, r.stderr.decode(errors="replace")


def _err_text(code, err):
    if "MPERR:" in err:
        return err.split("MPERR:", 1)[1].strip()
    if code == 126 or code == 127 or "executable file not found" in err:
        return "La imagen de este contenedor no tiene sh: no se puede explorar (imagen mínima o distroless)"
    return (err.strip() or f"El comando falló (código {code})")[-500:]


def _mounts(c):
    try:
        r = subprocess.run(["docker", "inspect", "--format", "{{json .Mounts}}", c], capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_mounts(r.stdout) if r.returncode == 0 else []


def _args():
    d = request.get_json(silent=True) if request.method == "POST" else request.args
    d = d if hasattr(d, "get") else {}
    return _container(d.get("c")), norm(d.get("path")), d


def _write_guard():
    return None if WRITE_ENABLED else _bad("Modo escritura deshabilitado (MATIPANEL_FILE_WRITE=0)", 403)


# ── Vistas ──────────────────────────────────────────────────────────────

@cfiles_bp.route("/")
@login_required
def index():
    return render_template("cfiles.html", write_enabled=WRITE_ENABLED)


@cfiles_bp.route("/api/containers")
@login_required
def containers():
    return jsonify({"ok": True, "containers": running_containers()})


@cfiles_bp.route("/api/ls")
@login_required
def ls():
    c, path, _ = _args()
    if not c:
        return _bad("Elige un contenedor en marcha", 404)
    if not path:
        return _bad("Ruta inválida")
    code, out, err = _exec(c, LS_SCRIPT, path)
    if code != 0:
        return _bad(_err_text(code, err), 404 if code == 2 else 500)
    entries = parse_ls(out.decode(errors="replace"))
    mounts = _mounts(c)
    for e in entries:
        m = mount_of(posixpath.join(path, e["name"]), mounts)
        e["volume"] = m["name"] if m else ""
    here = mount_of(path, mounts)
    return jsonify({"ok": True, "path": path, "entries": entries[:MAX_LS], "truncated": len(entries) > MAX_LS,
                    "mounts": mounts, "here": here, "write": WRITE_ENABLED})


@cfiles_bp.route("/api/read")
@login_required
def read():
    c, path, _ = _args()
    if not c or not path:
        return _bad("Contenedor o ruta inválidos")
    code, out, err = _exec(c, READ_SCRIPT, path)
    if code != 0:
        return _bad(_err_text(code, err), 404 if code == 2 else 500)
    version, _, data = out.partition(b"\n")
    if len(data) > MAX_EDIT:
        return _bad("El archivo pesa más de 2 MB: descárgalo para verlo", 413)
    if b"\x00" in data[:8192]:
        return _bad("Es un archivo binario: descárgalo para verlo", 415)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _bad("El archivo no es texto UTF-8: descárgalo para verlo", 415)
    return jsonify({"ok": True, "content": text, "version": version.decode().strip(), "size": len(data)})


@cfiles_bp.route("/api/write", methods=["POST"])
@login_required
def write():
    if (g := _write_guard()):
        return g
    c, path, d = _args()
    content = d.get("content")
    if not c or not path or not isinstance(content, str):
        return _bad("Datos inválidos")
    data = content.encode("utf-8")
    if len(data) > MAX_EDIT:
        return _bad("El contenido supera 2 MB")
    code, _, err = _exec(c, WRITE_SCRIPT, path, str(d.get("version") or ""), input=data)
    if code == 3:
        return _bad("El archivo cambió desde que lo abriste. Vuelve a abrirlo para no pisar esos cambios.", 409)
    if code != 0:
        return _bad(_err_text(code, err), 500)
    return jsonify({"ok": True, "message": "Guardado"})


@cfiles_bp.route("/api/create", methods=["POST"])
@login_required
def create():
    if (g := _write_guard()):
        return g
    c, path, d = _args()
    name = str(d.get("name") or "")
    kind = "dir" if d.get("kind") == "dir" else "file"
    if not c or not path or not name or "/" in name or name in (".", "..") or any(x in name for x in "\x00\n\r"):
        return _bad("Nombre inválido")
    code, _, err = _exec(c, CREATE_SCRIPT, posixpath.join(path, name), kind)
    if code != 0:
        return _bad(_err_text(code, err), 409 if code == 4 else 500)
    return jsonify({"ok": True, "message": ("Carpeta" if kind == "dir" else "Archivo") + " creado"})


@cfiles_bp.route("/api/rename", methods=["POST"])
@login_required
def rename():
    if (g := _write_guard()):
        return g
    c, path, d = _args()
    name = str(d.get("name") or "")
    if not c or not path or is_protected(path) or not name or "/" in name or name in (".", "..") \
            or any(x in name for x in "\x00\n\r"):
        return _bad("Datos inválidos")
    code, _, err = _exec(c, RENAME_SCRIPT, path, posixpath.join(posixpath.dirname(path), name))
    if code != 0:
        return _bad(_err_text(code, err), 409 if code == 4 else 500)
    return jsonify({"ok": True, "message": "Renombrado"})


@cfiles_bp.route("/api/delete", methods=["POST"])
@login_required
def delete():
    if (g := _write_guard()):
        return g
    c, path, _ = _args()
    if not c or not path:
        return _bad("Datos inválidos")
    if is_protected(path):
        return _bad(f"{path} es una carpeta del sistema: no se borra")
    code, _, err = _exec(c, 'rm -rf -- "$1"', path, timeout=300)
    if code != 0:
        return _bad(_err_text(code, err), 500)
    return jsonify({"ok": True, "message": "Eliminado"})


@cfiles_bp.route("/api/download")
@login_required
def download():
    """Archivo: el único archivo del tar de `docker cp`. Carpeta: el .tar tal cual."""
    c, path, d = _args()
    if not c or not path:
        return _bad("Contenedor o ruta inválidos")
    is_dir = d.get("dir") == "1"
    try:
        p = subprocess.Popen(["docker", "cp", *([] if is_dir else ["-L"]), f"{c}:{path}", "-"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return _bad(str(e), 500)
    base = posixpath.basename(path) or "raiz"

    if is_dir:
        def gen_tar():
            try:
                while chunk := p.stdout.read(256 * 1024):
                    yield chunk
            finally:
                p.kill() if p.poll() is None else None
                p.wait()
        return Response(gen_tar(), mimetype="application/x-tar",
                        headers={"Content-Disposition": _disposition(base + ".tar")})

    try:
        tf = tarfile.open(fileobj=p.stdout, mode="r|")
        member = next((m for m in tf if m.isfile()), None)
    except tarfile.TarError:
        member = None
    if member is None:
        p.kill()
        err = p.stderr.read().decode(errors="replace")
        p.wait()
        return _bad(_err_text(1, err) if err else "No es un archivo", 404)
    src = tf.extractfile(member)

    def gen_file():
        try:
            while chunk := src.read(256 * 1024):
                yield chunk
        finally:
            p.kill() if p.poll() is None else None
            p.wait()
    return Response(gen_file(), mimetype="application/octet-stream",
                    headers={"Content-Disposition": _disposition(base), "Content-Length": str(member.size)})


@cfiles_bp.route("/api/upload", methods=["POST"])
@login_required
def upload():
    """Archivos del navegador → carpeta del contenedor, con el dueño de esa carpeta (docker cp - desde un tar)."""
    if (g := _write_guard()):
        return g
    if (request.content_length or 0) > MAX_UPLOAD:
        return _bad("La subida supera 100 MB", 413)
    c, path = _container(request.form.get("c")), norm(request.form.get("path"))
    files = request.files.getlist("files")
    if not c or not path or not files:
        return _bad("Datos inválidos")
    code, out, err = _exec(c, '[ -d "$1" ] || { echo "MPERR:La carpeta no existe" >&2; exit 2; }; stat -c %u:%g -- "$1"', path)
    if code != 0:
        return _bad(_err_text(code, err), 404)
    try:
        uid, gid = (int(x) for x in out.decode().strip().split(":"))
    except ValueError:
        uid = gid = 0
    names = []
    with tempfile.TemporaryFile() as tmp:
        with tarfile.open(fileobj=tmp, mode="w") as tar:
            for f in files:
                name = posixpath.basename((f.filename or "").replace("\\", "/"))
                if not name or name in (".", "..") or any(x in name for x in "\x00\n\r"):
                    return _bad(f"Nombre de archivo inválido: {f.filename!r}")
                f.stream.seek(0, os.SEEK_END)
                size = f.stream.tell()
                f.stream.seek(0)
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.uid, info.gid, info.mtime = size, 0o644, uid, gid, int(time.time())
                tar.addfile(info, f.stream)
                names.append(name)
        tmp.seek(0)
        try:
            r = subprocess.run(["docker", "cp", "-", f"{c}:{path}"], stdin=tmp, capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as e:
            return _bad(str(e), 500)
    if r.returncode != 0:
        return _bad(r.stderr.decode(errors="replace").strip()[-500:] or "docker cp falló", 500)
    return jsonify({"ok": True, "message": f"{len(names)} archivo(s) subido(s)", "names": names})
