"""Respaldos tar.gz en /var/backups/matipanel, restauración y trabajos programados (cron).

Las funciones de la primera sección no usan Flask: las reutiliza app/backup_cli.py.
"""
import json
import os
import re
import shlex
import sys
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, send_file
from flask_login import login_required
from ..util import run

BACKUP_DIR = "/var/backups/matipanel"
JOBS_FILE = "/var/lib/matipanel/backups.json"
CRON_FILE = "/etc/cron.d/matipanel-backups"
CRON_LOG = "/var/log/matipanel-backups.log"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}\Z")  # \Z: "$" aceptaría un salto de línea final
# Manual: <nombre>-YYYYmmdd-HHMMSS.tar.gz · Programado: <trabajo>-auto-YYYYmmdd-HHMMSS.tar.gz.
# Solo los "-auto-" de un trabajo entran en su retención; los manuales nunca se podan.
FILE_RE = re.compile(r"^([A-Za-z0-9_-]{1,40}?)(-auto)?-(\d{8}-\d{6})\.tar\.gz\Z")
AUTO_TAG = "-auto"


def valid_name(name):
    """Nombre de respaldo/trabajo. Prohíbe terminar en -auto (se confundiría con uno programado)."""
    return bool(NAME_RE.match(name or "")) and not name.endswith(AUTO_TAG)
EXCLUDE_RE = re.compile(r"^[A-Za-z0-9_.*?/\[\]-]{1,200}\Z")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)\Z")


def check_paths(paths):
    """Rutas absolutas existentes, sin '..'. Retorna rutas normalizadas o lanza ValueError."""
    if not isinstance(paths, list) or not paths or len(paths) > 50:
        raise ValueError("Indica entre 1 y 50 rutas")
    clean = []
    for p in paths:
        if not isinstance(p, str) or not p.startswith("/") or ".." in p.split("/") \
                or any(ord(c) < 32 for c in p):
            raise ValueError(f"Ruta inválida: {p!r}")
        p = os.path.normpath(p)
        if p == "/":
            raise ValueError("No se puede respaldar / completo; elige directorios concretos")
        if not os.path.lexists(p):
            raise ValueError(f"No existe: {p}")
        clean.append(p)
    return clean


def check_excludes(excludes):
    excludes = excludes or []
    if not isinstance(excludes, list) or len(excludes) > 50:
        raise ValueError("Exclusiones inválidas")
    for e in excludes:
        if not isinstance(e, str) or not EXCLUDE_RE.match(e) or e.startswith("-"):
            raise ValueError(f"Patrón de exclusión inválido: {e!r}")
    return excludes


def create_backup(name, paths, excludes=(), auto=False):
    """Crea BACKUP_DIR/<name>[-auto]-YYYYmmdd-HHMMSS.tar.gz. Retorna (ok, ruta | error)."""
    if not valid_name(name):
        return False, "Nombre inválido"
    os.makedirs(BACKUP_DIR, mode=0o700, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)  # si ya existía con otros permisos, el .tar.gz sería legible un instante
    tag = AUTO_TAG if auto else ""
    dest = os.path.join(BACKUP_DIR, f"{name}{tag}-{datetime.now():%Y%m%d-%H%M%S}.tar.gz")
    if os.path.exists(dest):
        return False, "Ya existe un respaldo con ese nombre en este segundo; reintenta"
    cmd = ["tar", "-czf", dest, "-C", "/"] + [f"--exclude={e}" for e in excludes] \
        + ["--"] + [p.lstrip("/") for p in paths]
    code, out, err = run(cmd, timeout=3600)
    # tar devuelve 1 si algún archivo cambió durante la lectura: el respaldo es válido.
    if code not in (0, 1):
        if os.path.exists(dest):
            os.remove(dest)
        return False, err.strip() or "tar falló"
    os.chmod(dest, 0o600)
    return True, dest


def list_backups():
    items = []
    try:
        names = os.listdir(BACKUP_DIR)
    except OSError:
        return items
    for f in names:
        m = FILE_RE.match(f)
        if not m:
            continue
        st = os.stat(os.path.join(BACKUP_DIR, f))
        items.append({"file": f, "job": m.group(1), "auto": bool(m.group(2)), "stamp": m.group(3),
                      "size": st.st_size, "mtime": int(st.st_mtime)})
    return sorted(items, key=lambda x: x["stamp"], reverse=True)


def backup_path(fname):
    """Ruta completa de un respaldo existente, o None si el nombre no es válido."""
    if not isinstance(fname, str) or not FILE_RE.match(fname):
        return None
    p = os.path.join(BACKUP_DIR, fname)
    return p if os.path.isfile(p) else None


def prune(name, keep):
    """Borra las copias PROGRAMADAS (-auto-) de <name> más antiguas que las últimas <keep>."""
    removed = []
    for b in [b for b in list_backups() if b["job"] == name and b["auto"]][keep:]:
        os.remove(os.path.join(BACKUP_DIR, b["file"]))
        removed.append(b["file"])
    return removed


def load_jobs():
    try:
        with open(JOBS_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_jobs(jobs):
    os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
    tmp = JOBS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(jobs, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, JOBS_FILE)
    write_cron(jobs)


def check_schedule(s):
    """{'type': 'daily'|'weekly', 'time': 'HH:MM', 'dow': 0-6} -> campos cron 'M H * * D'."""
    if not isinstance(s, dict) or s.get("type") not in ("daily", "weekly"):
        raise ValueError("Frecuencia inválida")
    m = TIME_RE.match(str(s.get("time", "")))
    if not m:
        raise ValueError("Hora inválida (HH:MM)")
    dow = "*"
    if s["type"] == "weekly":
        if s.get("dow") not in range(7):
            raise ValueError("Día de la semana inválido")
        dow = str(s["dow"])
    return f"{int(m.group(2))} {int(m.group(1))} * * {dow}"


def write_cron(jobs):
    if not jobs:
        if os.path.exists(CRON_FILE):
            os.remove(CRON_FILE)
        return
    py = os.path.join(APP_ROOT, "venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    lines = ["# Generado por MatiPanel (Respaldos). No editar a mano.",
             "SHELL=/bin/sh", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", ""]
    for name, job in sorted(jobs.items()):
        if not valid_name(name):
            continue
        lines.append(f"{check_schedule(job['schedule'])} root cd {shlex.quote(APP_ROOT)} && "
                     f"{shlex.quote(py)} -m app.backup_cli {name} >> {CRON_LOG} 2>&1")
    tmp = CRON_FILE + ".tmp"  # cron ignora archivos con '.' en /etc/cron.d
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, CRON_FILE)


def run_job(name, scheduled=False):
    """Ejecuta un trabajo guardado. Retorna (ok, mensaje).

    scheduled=True (cron): copia <job>-auto-… y poda las programadas que sobren.
    scheduled=False ("Ejecutar ahora"): copia manual, que la retención nunca borra.
    """
    job = load_jobs().get(name)
    if not job:
        return False, f"Trabajo desconocido: {name}"
    try:
        paths = check_paths(job["paths"])
        excludes = check_excludes(job.get("excludes"))
    except ValueError as e:
        return False, str(e)
    ok, msg = create_backup(name, paths, excludes, auto=scheduled)
    if not ok:
        return False, msg
    removed = prune(name, int(job.get("keep", 7))) if scheduled else []
    return True, msg + (f" (eliminados: {', '.join(removed)})" if removed else "")


# ── Rutas web ────────────────────────────────────────────────────────

backups_bp = Blueprint("backups", __name__)


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _check_dir(p):
    if not isinstance(p, str) or not p.startswith("/") or ".." in p.split("/") or any(ord(c) < 32 for c in p):
        return None
    p = os.path.normpath(p)
    return p if os.path.isdir(p) else None


@backups_bp.route("/")
@login_required
def index():
    return render_template("backups.html", backup_dir=BACKUP_DIR)


@backups_bp.route("/api/list")
@login_required
def list_all():
    return jsonify({"backups": list_backups(), "jobs": load_jobs()})


@backups_bp.route("/api/create", methods=["POST"])
@login_required
def create():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", ""))
    if not valid_name(name):
        return _err("Nombre inválido (letras, números, _ y -, máx. 40; sin terminar en -auto)")
    try:
        paths, excludes = check_paths(d.get("paths")), check_excludes(d.get("excludes"))
    except ValueError as e:
        return _err(str(e))
    ok, msg = create_backup(name, paths, excludes)
    return jsonify({"ok": True, "file": os.path.basename(msg)}) if ok else _err(msg, 500)


@backups_bp.route("/api/download/<fname>")
@login_required
def download(fname):
    p = backup_path(fname)
    if not p:
        return _err("Respaldo no encontrado", 404)
    return send_file(p, as_attachment=True, download_name=fname)


@backups_bp.route("/api/delete", methods=["POST"])
@login_required
def delete():
    p = backup_path((request.get_json(silent=True) or {}).get("file"))
    if not p:
        return _err("Respaldo no encontrado", 404)
    os.remove(p)
    return jsonify({"ok": True})


@backups_bp.route("/api/restore", methods=["POST"])
@login_required
def restore():
    d = request.get_json(silent=True) or {}
    p = backup_path(d.get("file"))
    target = _check_dir(d.get("target", "/"))
    if not p:
        return _err("Respaldo no encontrado", 404)
    if not target:
        return _err("Destino inválido: debe ser un directorio absoluto existente")
    if target == "/" and d.get("confirm") != "RESTAURAR":
        return _err("Restaurar sobre / requiere escribir RESTAURAR")
    code, out, err = run(["tar", "-xzf", p, "-C", target], timeout=3600)
    if code != 0:
        return _err(err.strip() or "tar falló", 500)
    return jsonify({"ok": True})


@backups_bp.route("/api/jobs/save", methods=["POST"])
@login_required
def job_save():
    d = request.get_json(silent=True) or {}
    name, keep = str(d.get("name", "")), d.get("keep")
    if not valid_name(name):
        return _err("Nombre inválido (letras, números, _ y -, máx. 40; sin terminar en -auto)")
    if not isinstance(keep, int) or isinstance(keep, bool) or not 1 <= keep <= 365:
        return _err("Copias a conservar: entre 1 y 365")
    try:
        job = {"paths": check_paths(d.get("paths")), "excludes": check_excludes(d.get("excludes")),
               "keep": keep, "schedule": d.get("schedule")}
        check_schedule(job["schedule"])
        jobs = load_jobs()
        if name in jobs and d.get("overwrite") is not True:
            return jsonify({"ok": False, "exists": True,
                            "error": f"Ya existe un trabajo llamado {name}"}), 409
        jobs[name] = job
        save_jobs(jobs)
    except ValueError as e:
        return _err(str(e))
    except OSError as e:
        return _err(f"No se pudo guardar: {e}", 500)
    return jsonify({"ok": True})


@backups_bp.route("/api/jobs/delete", methods=["POST"])
@login_required
def job_delete():
    name = (request.get_json(silent=True) or {}).get("name")
    jobs = load_jobs()
    if name not in jobs:
        return _err("Trabajo no encontrado", 404)
    del jobs[name]
    try:
        save_jobs(jobs)
    except OSError as e:
        return _err(f"No se pudo guardar: {e}", 500)
    return jsonify({"ok": True})


@backups_bp.route("/api/jobs/run", methods=["POST"])
@login_required
def job_run():
    name = str((request.get_json(silent=True) or {}).get("name", ""))
    if not NAME_RE.match(name):
        return _err("Nombre inválido")
    ok, msg = run_job(name)  # manual: no poda
    return jsonify({"ok": True, "output": msg}) if ok else _err(msg, 500)
