"""Discos: particiones, análisis de espacio (qué ocupa más) y limpieza segura.

El análisis usa `du -x` (no cruza a otras particiones) y `find -xdev`, con prioridad baja (nice/ionice)
porque en discos grandes puede tardar; cada resultado se guarda 10 minutos para navegar sin repetirlo.
La limpieza solo ofrece lo que el sistema regenera o ya no usa, cada cosa con su comando conocido
(apt-get clean, journalctl --vacuum-size, docker builder prune…) o borrando solo archivos normales de
carpetas fijas (logs rotados, volcados de memoria, cachés), sin seguir enlaces simbólicos.
"""
import glob
import json
import os
import posixpath
import re
import shutil
import stat
import threading
import time

import psutil
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required

from ..util import bytes_to_human as _bytes_to_human, run

disk_bp = Blueprint("disk", __name__)

CACHE_TTL = 600
MAX_ENTRIES = 300
BIG_FILE = 50 * 1024 * 1024
JOURNAL_KEEP = "200M"
ROTATED_RE = re.compile(r"(\.(gz|xz|bz2|zst|old)|\.[1-9]\d*(\.log)?|-\d{8}(\.gz)?)$")  # .0 es el actual (Xorg.0.log)
_cache = {}
_lock = threading.Lock()


def _low(cmd):
    """Prioridad baja de CPU y disco: el análisis no debe frenar al servidor."""
    pre = ["nice", "-n", "19"] if shutil.which("nice") else []
    if shutil.which("ionice"):
        pre += ["ionice", "-c3"]
    return pre + cmd


def _cached(key, fn, fresh=False):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and now - hit[0] < CACHE_TTL:
            return {**hit[1], "age": round(now - hit[0])}
    data = fn()
    with _lock:
        if len(_cache) > 60:
            _cache.clear()
        _cache[key] = (now, data)
    return {**data, "age": 0}


def _dir_path(p):
    """Carpeta absoluta y existente (sin resolver enlaces), o None."""
    if not isinstance(p, str) or not p.startswith("/") or "\x00" in p or len(p) > 4096:
        return None
    p = posixpath.normpath(p)  # rutas del servidor (Linux), también al probar en Windows
    p = "/" + p.lstrip("/") if p.startswith("//") else p
    try:
        return p if stat.S_ISDIR(os.lstat(p).st_mode) else None
    except OSError:
        return None


# ── Análisis ─────────────────────────────────────────────────────────────

def parse_du(out, path):
    """Salida de `du -xak --max-depth=1` → (total en bytes, [{name, path, size, dir}] de mayor a menor)."""
    total, entries = 0, []
    for line in out.splitlines():
        size, _, p = line.partition("\t")
        if not size.isdigit() or not p:
            continue
        b = int(size) * 1024
        if posixpath.normpath(p) == posixpath.normpath(path):
            total = b
            continue
        try:
            is_dir = stat.S_ISDIR(os.lstat(p).st_mode)
        except OSError:
            is_dir = False
        entries.append({"name": posixpath.basename(p.rstrip("/")) or p, "path": p, "size": b, "dir": is_dir})
    entries.sort(key=lambda e: -e["size"])
    return total, entries


def analyze(path):
    code, out, err = run(_low(["du", "-xak", "--max-depth=1", "--", path]), timeout=600)
    if code not in (0, 1) or not out.strip():  # 1 = algunos archivos sin permiso o que desaparecieron: el resto vale
        raise OSError((err or out).strip().splitlines()[-1] if (err or out).strip() else f"du terminó con código {code}")
    total, entries = parse_du(out, path)
    rest = entries[MAX_ENTRIES:]
    try:
        usage = psutil.disk_usage(path)
        part = {"total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent}
    except OSError:
        part = None
    return {"path": path, "total": total, "entries": entries[:MAX_ENTRIES],
            "more": {"count": len(rest), "size": sum(e["size"] for e in rest)}, "partition": part,
            "warnings": len([ln for ln in err.splitlines() if ln.strip()])}


def parse_find(out):
    """Salida de `find -printf '%s\\t%T@\\t%p\\0'` → [{path, size, mtime}] de mayor a menor."""
    files = []
    for rec in out.split("\0"):
        size, _, rest = rec.strip("\n").partition("\t")
        mtime, _, p = rest.partition("\t")
        if size.isdigit() and p:
            try:
                files.append({"path": p, "size": int(size), "mtime": float(mtime)})
            except ValueError:
                continue
    return sorted(files, key=lambda f: -f["size"])


def big_files(path, limit=50):
    code, out, err = run(_low(["find", path, "-xdev", "-type", "f", "-size", f"+{BIG_FILE // 1048576}M",
                               "-printf", "%s\\t%T@\\t%p\\0"]), timeout=600)
    if code not in (0, 1):
        raise OSError((err or out).strip()[-300:] or f"find terminó con código {code}")
    return {"path": path, "files": parse_find(out)[:limit], "min_size": BIG_FILE}


# ── Limpieza ─────────────────────────────────────────────────────────────

UNITS = {"": 1, "B": 1, "K": 1024, "KB": 1000, "KIB": 1024, "M": 1024 ** 2, "MB": 1000 ** 2, "MIB": 1024 ** 2,
         "G": 1024 ** 3, "GB": 1000 ** 3, "GIB": 1024 ** 3, "T": 1024 ** 4, "TB": 1000 ** 4, "TIB": 1024 ** 4}


def parse_size(s):
    """«1.2GB», «512.5kB», «1.2G», «0B» → bytes (None si no se entiende)."""
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]*)", str(s or ""))
    if not m:
        return None
    unit = UNITS.get(m[2].upper())
    return int(float(m[1]) * unit) if unit else None


def _du_bytes(path):
    if not os.path.isdir(path):
        return 0
    code, out, _ = run(["du", "-sxk", "--", path], timeout=120)
    first = out.split()[0] if out.split() else ""
    return int(first) * 1024 if code in (0, 1) and first.isdigit() else 0


def _walk_files(root, pred):
    """Archivos normales bajo `root` que cumplen `pred` (sin seguir enlaces). → [(ruta, bytes)]"""
    out = []
    for d, dirs, names in os.walk(root, followlinks=False):
        for n in names:
            p = os.path.join(d, n)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and pred(n):
                out.append((p, st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size))
    return out


def _delete_files(root, pred):
    freed = 0
    for p, size in _walk_files(root, pred):
        try:
            os.unlink(p)
            freed += size
        except OSError:
            pass
    return freed


def _empty_dir(root):
    """Vacía una carpeta de caché o papelera sin seguir enlaces (rmtree es seguro ante enlaces)."""
    for e in os.scandir(root):
        try:
            if e.is_dir(follow_symlinks=False):
                shutil.rmtree(e.path)
            else:
                os.unlink(e.path)
        except OSError:
            pass


def _trash_dirs():
    return [d for d in ["/root/.local/share/Trash", *glob.glob("/home/*/.local/share/Trash")] if os.path.isdir(d)]


def docker_df():
    """`docker system df` → {tipo: {size, reclaimable}} en bytes."""
    code, out, _ = run(["docker", "system", "df", "--format", "{{json .}}"], timeout=60)
    res = {}
    for line in out.splitlines() if code == 0 else []:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        res[d.get("Type", "")] = {"size": parse_size(d.get("Size")) or 0,
                                  "reclaimable": parse_size(str(d.get("Reclaimable", "")).split(" ")[0]) or 0}
    return res


def candidates():
    """Qué se puede limpiar, con su tamaño. Cada entrada: id, título, descripción, bytes, riesgo y aviso."""
    items = []

    def add(id_, title, desc, size, risk="seguro", warn="", action="Limpiar", count=None, link=None):
        if (size or 0) >= 1024 * 1024 or count:
            items.append({"id": id_, "title": title, "desc": desc, "size": size, "count": count, "risk": risk,
                          "warn": warn, "action": action, "link": link})

    if shutil.which("apt-get") and os.path.isdir("/var/cache/apt"):
        add("apt_cache", "Paquetes descargados (caché de APT)",
            "Instaladores .deb que ya se instalaron. APT los vuelve a descargar si los necesita.", _du_bytes("/var/cache/apt"))
    elif shutil.which("dnf") and os.path.isdir("/var/cache/dnf"):
        add("dnf_cache", "Caché de DNF", "Paquetes y metadatos descargados; DNF los vuelve a descargar si los necesita.",
            _du_bytes("/var/cache/dnf"))
    if shutil.which("journalctl"):
        code, out, _ = run(["journalctl", "--disk-usage"], timeout=30)
        m = re.search(r"take up ([\d.]+\s*[A-Za-z]*)", out)
        size = parse_size(m[1]) if code == 0 and m else 0
        if size and size > parse_size(JOURNAL_KEEP):
            add("journal", "Registro del sistema (journal)",
                f"Ocupa {_bytes_to_human(size)}. Se conservan los últimos {JOURNAL_KEEP} y se borra lo más viejo.",
                size - parse_size(JOURNAL_KEEP), action=f"Dejar {JOURNAL_KEEP}")
    rotated = _walk_files("/var/log", lambda n: bool(ROTATED_RE.search(n))) if os.path.isdir("/var/log") else []
    add("old_logs", "Logs rotados viejos (/var/log)",
        "Copias antiguas que logrotate ya cerró y comprimió (.gz, .1, .old…). Los logs actuales no se tocan.",
        sum(s for _, s in rotated), count=None)
    if os.path.isdir("/var/lib/systemd/coredump"):
        add("coredumps", "Volcados de memoria (core dumps)",
            "Copias de la memoria de programas que se cerraron con error. Solo sirven para depurar ese fallo.",
            _du_bytes("/var/lib/systemd/coredump"))
    if shutil.which("docker"):
        df = docker_df()
        build = (df.get("Build Cache") or {}).get("reclaimable", 0)
        add("docker_build", "Caché de compilación de Docker",
            "Capas intermedias de compilaciones anteriores. Los despliegues siguientes pueden tardar un poco más.", build)
        code, out, _ = run(["docker", "images", "-f", "dangling=true", "--format", "{{.Size}}"], timeout=60)
        dangling = sum(parse_size(s) or 0 for s in out.split()) if code == 0 else 0
        add("docker_dangling", "Imágenes de Docker huérfanas",
            "Imágenes sin nombre que quedaron de compilaciones viejas; nada las usa.", dangling)
        unused = max(0, (df.get("Images") or {}).get("reclaimable", 0) - dangling)
        add("docker_unused", "Imágenes de Docker sin usar",
            "Imágenes que ningún contenedor usa (incluidas las versiones anteriores de las apps desplegadas).",
            unused, risk="cuidado",
            warn="Se pierde la opción de revertir las apps de Despliegues a versiones anteriores sin compilar de nuevo.")
        vols = (df.get("Local Volumes") or {}).get("reclaimable", 0)
        if vols >= 1024 * 1024:
            items.append({"id": "docker_volumes", "title": "Volúmenes de Docker sin usar", "size": vols, "count": None,
                          "desc": "Volúmenes que ningún contenedor usa. Pueden tener datos (bases, archivos de apps borradas): "
                                  "no se borran desde aquí; revísalos en Docker → Volúmenes.",
                          "risk": "revisar", "warn": "", "action": None, "link": "/docker/"})
    if shutil.which("apt-get"):
        code, out, _ = run(["apt-get", "-s", "autoremove"], timeout=120)
        pkgs = [ln.split()[1] for ln in out.splitlines() if ln.startswith("Remv ")] if code == 0 else []
        kernels = [p for p in pkgs if p.startswith("linux-image")]
        add("autoremove", "Paquetes que ya no se necesitan",
            f"{len(pkgs)} paquete(s) instalados como dependencia que ya nada usa"
            + (f", entre ellos {len(kernels)} kernel(s) viejo(s)" if kernels else "") + ". Se quitan con apt-get autoremove.",
            None, count=len(pkgs), action="Quitar")
    if os.path.isdir("/root/.cache"):
        add("root_cache", "Caché de root (~/.cache)", "Cachés de pip, npm, composer y otros programas; se regeneran solos.",
            _du_bytes("/root/.cache"))
    trash = sum(_du_bytes(d) for d in _trash_dirs())
    add("trash", "Papeleras de los usuarios", "Archivos que los usuarios enviaron a la papelera.", trash)
    backups = _du_bytes("/var/backups/matipanel")
    if backups >= 1024 * 1024:
        items.append({"id": "panel_backups", "title": "Respaldos de carpetas del panel (/var/backups/matipanel)", "size": backups,
                      "count": None, "desc": "Respaldos .tar.gz hechos en este mismo servidor. Borra los que ya no necesites desde Respaldos.",
                      "risk": "revisar", "warn": "", "action": None, "link": "/backups/"})
    items.sort(key=lambda i: -(i["size"] or 0))
    return items


def clean(item_id):
    """Ejecuta una limpieza. → (ok, mensaje). Lo que va por comandos se ve en vivo en la ventana de la acción."""
    if item_id == "apt_cache":
        from .packages import apt_get
        ok, _, msg = apt_get("clean", timeout=300)
        return ok, "Caché de APT vaciada" if ok else msg
    if item_id == "dnf_cache":
        code, out, err = run(["dnf", "clean", "all"], timeout=300)
        return code == 0, "Caché de DNF vaciada" if code == 0 else (err or out)[-300:]
    if item_id == "journal":
        code, out, err = run(["journalctl", f"--vacuum-size={JOURNAL_KEEP}"], timeout=300)
        return code == 0, (err or out).strip().splitlines()[-1] if (err or out).strip() else "Journal reducido"
    if item_id == "old_logs":
        freed = _delete_files("/var/log", lambda n: bool(ROTATED_RE.search(n)))
        return True, f"Se borraron logs rotados: {_bytes_to_human(freed)}"
    if item_id == "coredumps":
        freed = _delete_files("/var/lib/systemd/coredump", lambda n: True)
        return True, f"Se borraron volcados de memoria: {_bytes_to_human(freed)}"
    docker = {"docker_build": ["docker", "builder", "prune", "-af"], "docker_dangling": ["docker", "image", "prune", "-f"],
              "docker_unused": ["docker", "image", "prune", "-af"]}
    if item_id in docker:
        code, out, err = run(docker[item_id], timeout=900)
        last = (out or err).strip().splitlines()[-1] if (out or err).strip() else ""
        return code == 0, last or ("Listo" if code == 0 else f"docker terminó con código {code}")
    if item_id == "autoremove":
        from .packages import apt_get
        ok, _, msg = apt_get("autoremove", "--purge", timeout=1800)
        return ok, "Paquetes innecesarios quitados" if ok else msg
    if item_id == "root_cache":
        _empty_dir("/root/.cache")
        return True, "Caché de root vaciada"
    if item_id == "trash":
        for d in _trash_dirs():
            _empty_dir(d)
        return True, "Papeleras vaciadas"
    return False, "Limpieza desconocida"


CLEAN_IDS = ("apt_cache", "dnf_cache", "journal", "old_logs", "coredumps", "docker_build", "docker_dangling",
             "docker_unused", "autoremove", "root_cache", "trash")


# ── Rutas ────────────────────────────────────────────────────────────────

@disk_bp.route("/")
@login_required
def index():
    return render_template("disk.html")


@disk_bp.route("/api/partitions")
@login_required
def partitions():
    parts = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            parts.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "opts": part.opts,
                "total_h": _bytes_to_human(usage.total),
                "used_h": _bytes_to_human(usage.used),
                "free_h": _bytes_to_human(usage.free),
                "percent": usage.percent,
            })
        except (PermissionError, OSError):
            continue
    return jsonify({"partitions": parts})


@disk_bp.route("/api/usage")
@login_required
def usage():
    path = _dir_path(request.args.get("path", "/"))
    if not path:
        return jsonify({"ok": False, "error": "Carpeta inválida o inexistente"}), 400
    try:
        return jsonify({"ok": True, **_cached(("du", path), lambda: analyze(path), request.args.get("fresh") == "1")})
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo analizar: {e}"}), 500


@disk_bp.route("/api/bigfiles")
@login_required
def bigfiles():
    path = _dir_path(request.args.get("path", "/"))
    if not path:
        return jsonify({"ok": False, "error": "Carpeta inválida o inexistente"}), 400
    try:
        return jsonify({"ok": True, **_cached(("find", path), lambda: big_files(path), request.args.get("fresh") == "1")})
    except OSError as e:
        return jsonify({"ok": False, "error": f"No se pudo buscar: {e}"}), 500


@disk_bp.route("/api/cleanup")
@login_required
def cleanup_list():
    return jsonify({"ok": True, "items": candidates()})


@disk_bp.route("/api/cleanup/<item_id>", methods=["POST"])
@login_required
def cleanup_run(item_id):
    if item_id not in CLEAN_IDS:
        return jsonify({"ok": False, "error": "Limpieza desconocida"}), 404
    before = psutil.disk_usage("/").free
    ok, msg = clean(item_id)
    with _lock:
        _cache.clear()  # los tamaños cambiaron
    freed = max(0, psutil.disk_usage("/").free - before)
    return jsonify({"ok": ok, "message": msg, "freed": freed, **({} if ok else {"error": msg})}), (200 if ok else 500)


if __name__ == "__main__":
    total, entries = parse_du("4\t/var/log/a.log\n2048\t/var/log/journal\n10\t/var/log/x\n2100\t/var/log\n", "/var/log")
    assert total == 2100 * 1024 and [e["name"] for e in entries] == ["journal", "x", "a.log"], entries
    files = parse_find("104857600\t1730000000.5\t/srv/big.iso\0" "52428800\t1730000001\t/var/log/raro\tcon tab\0basura\0")
    assert [f["path"] for f in files] == ["/srv/big.iso", "/var/log/raro\tcon tab"] and files[0]["size"] == 104857600
    assert parse_size("1.5GB") == 1500000000 and parse_size("512kB") == 512000 and parse_size("1.2G") == int(1.2 * 1024 ** 3)
    assert parse_size("0B") == 0 and parse_size("nada") is None
    for n, ok in (("syslog.1", True), ("syslog.2.gz", True), ("auth.log", False), ("syslog", False), ("dpkg.log.1", True),
                  ("apache2/error.log.14.gz", True), ("messages-20261020", True), ("btmp.old", True), ("kern.log", False), ("Xorg.0.log", False)):
        assert bool(ROTATED_RE.search(n)) == ok, n
    assert _dir_path("relativa") is None and _dir_path("/no/existe/x") is None and _dir_path("/a\x00b") is None
    print("ok")
