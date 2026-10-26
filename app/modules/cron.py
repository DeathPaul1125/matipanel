"""Tareas programadas: crontabs de usuario (crontab -l/-u) y crontabs del sistema (solo lectura)."""
import os
import pwd
import re
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..util import run

cron_bp = Blueprint("cron", __name__)

PRESETS = ("@reboot", "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly")
_TOK = (r"(?:\*|\d+|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
        r"|sun|mon|tue|wed|thu|fri|sat)")
FIELD_RE = re.compile(rf"^{_TOK}(?:[-/,]{_TOK})*$", re.I)
ENV_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=")
USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,31}\Z")
SPOOL_DIRS = ("/var/spool/cron/crontabs", "/var/spool/cron")  # Debian, RHEL
# Marca de las tareas desactivadas por el panel. Un "# 0 5 * * 1 tar …" cualquiera (como la
# plantilla de ejemplo de Debian) es solo un comentario: no se lista ni se puede "activar".
OFF_MARK = "#[matipanel-off]"


# ── Parseo / serialización (funciones puras) ──

def parse_job(line):
    """'*/5 * * * * cmd' o '@daily cmd' -> {schedule, command} o None."""
    s = line.strip()
    if s.startswith("@"):
        parts = s.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in PRESETS:
            return {"schedule": parts[0], "command": parts[1]}
        return None
    parts = s.split(None, 5)
    if len(parts) == 6 and all(FIELD_RE.match(p) for p in parts[:5]):
        return {"schedule": " ".join(parts[:5]), "command": parts[5]}
    return None


def split_lines(text):
    """Líneas como las ve cron: separadas solo por salto de línea (LF).

    str.splitlines() también corta en VT, FF, FS/GS/RS, NEL, U+2028…: una crontab con esos
    caracteres dentro de un comentario mostraría (y al guardar convertiría en real) una tarea oculta.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [l[:-1] if l.endswith("\r") else l for l in lines]


def parse_crontab(text):
    """Texto -> lista de entradas; cada una conserva su línea original en 'raw'."""
    entries = []
    for i, raw in enumerate(split_lines(text)):
        e = {"i": i, "raw": raw, "type": "other"}
        s = raw.strip()
        if s.startswith(OFF_MARK):
            job = parse_job(s[len(OFF_MARK):])
            if job:
                e.update(type="job", enabled=False, **job)
        elif s.startswith("#"):
            pass  # comentario: se conserva tal cual
        elif ENV_RE.match(raw):
            e["type"] = "env"
        elif s:
            job = parse_job(s)
            if job:
                e.update(type="job", enabled=True, **job)
        entries.append(e)
    return entries


def serialize(lines):
    """Lista de líneas -> texto de crontab (cron exige salto de línea final)."""
    return "\n".join(lines) + "\n" if lines else ""


def job_line(schedule, command, enabled=True):
    return ("" if enabled else OFF_MARK + " ") + f"{schedule} {command}"


def toggle_line(raw):
    """Desactiva (con la marca del panel) o reactiva una línea de tarea."""
    s = raw.strip()
    return s[len(OFF_MARK):].lstrip() if s.startswith(OFF_MARK) else f"{OFF_MARK} {raw}"


def is_job_line(raw):
    return parse_crontab(raw)[0]["type"] == "job"


def build_schedule(data):
    """Valida los campos del formulario -> schedule o None."""
    preset = data.get("preset") or ""
    if preset:
        return preset if preset in PRESETS else None
    fields = [str(data.get(k, "")).strip() for k in ("minute", "hour", "day", "month", "weekday")]
    if all(FIELD_RE.match(f) for f in fields):
        return " ".join(fields)
    return None


# ── Acceso al sistema ──

def _valid_user(name):
    if not isinstance(name, str) or not USER_RE.match(name):
        return False
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def _read(user):
    """(texto, error). 'no crontab for X' se trata como vacío."""
    code, out, err = run(["crontab", "-l", "-u", user], timeout=10)
    if code == 0:
        return out, None
    if "no crontab" in err.lower():
        return "", None
    return None, err.strip() or "crontab -l falló"


def _write(user, text):
    code, out, err = run(["crontab", "-u", user, "-"], timeout=10, input=text)
    return None if code == 0 else (err.strip() or "crontab falló")


def _cron_users():
    names = {"root"} | {u.pw_name for u in pwd.getpwall() if 1000 <= u.pw_uid < 65534}
    for d in SPOOL_DIRS:
        try:
            names |= {f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)) and _valid_user(f)}
            break
        except OSError:
            continue
    return sorted(names, key=lambda n: (n != "root", n))


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _load_for_edit(data):
    """Lee la crontab y verifica que la línea indicada no haya cambiado. -> (user, lines, idx, error)."""
    user = data.get("user", "")
    if not _valid_user(user):
        return None, None, None, _err("Usuario inválido")
    text, err = _read(user)
    if text is None:
        return None, None, None, _err(err, 500)
    lines = split_lines(text)
    idx = data.get("index")
    if idx is None:
        return user, lines, None, None
    if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(lines) or lines[idx] != data.get("raw"):
        return None, None, None, _err("La crontab cambió mientras la editabas; recarga", 409)
    return user, lines, idx, None


def _save(user, lines):
    err = _write(user, serialize(lines))
    return _err(err, 500) if err else jsonify({"ok": True})


# ── Rutas ──

@cron_bp.route("/")
@login_required
def index():
    return render_template("cron.html")


@cron_bp.route("/api/users")
@login_required
def users():
    return jsonify({"users": _cron_users()})


@cron_bp.route("/api/crontab/<user>")
@login_required
def crontab(user):
    if not _valid_user(user):
        return jsonify({"error": "Usuario inválido"}), 400
    text, err = _read(user)
    if text is None:
        return jsonify({"error": err}), 500
    return jsonify({"user": user, "entries": parse_crontab(text)})


@cron_bp.route("/api/job/save", methods=["POST"])
@login_required
def save_job():
    """Crea (index=null) o reemplaza (index + raw) una tarea."""
    data = request.get_json(silent=True) or {}
    schedule = build_schedule(data)
    if not schedule:
        return _err("Programación inválida")
    command = str(data.get("command", "")).strip()
    if not command or len(command) > 4096 or "\n" in command or "\r" in command:
        return _err("Comando inválido (vacío o con saltos de línea)")
    user, lines, idx, error = _load_for_edit(data)
    if error:
        return error
    line = job_line(schedule, command, bool(data.get("enabled", True)))
    if idx is None:
        lines.append(line)
    else:
        lines[idx] = line
    return _save(user, lines)


@cron_bp.route("/api/job/delete", methods=["POST"])
@login_required
def delete_job():
    data = request.get_json(silent=True) or {}
    if data.get("index") is None:
        return _err("Falta el índice")
    user, lines, idx, error = _load_for_edit(data)
    if error:
        return error
    del lines[idx]
    return _save(user, lines)


@cron_bp.route("/api/job/toggle", methods=["POST"])
@login_required
def toggle_job():
    data = request.get_json(silent=True) or {}
    if data.get("index") is None:
        return _err("Falta el índice")
    user, lines, idx, error = _load_for_edit(data)
    if error:
        return error
    if not is_job_line(lines[idx]):
        return _err("Esa línea no es una tarea del panel")
    lines[idx] = toggle_line(lines[idx])
    return _save(user, lines)


@cron_bp.route("/api/system")
@login_required
def system():
    """/etc/crontab y /etc/cron.d/* (solo lectura)."""
    paths = ["/etc/crontab"]
    try:
        paths += sorted(os.path.join("/etc/cron.d", f) for f in os.listdir("/etc/cron.d")
                        if not f.startswith("."))
    except OSError:
        pass
    files = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, errors="replace") as f:
                files.append({"path": p, "content": f.read(65536)})
        except OSError as e:
            files.append({"path": p, "content": f"(no se pudo leer: {e})"})
    return jsonify({"files": files})


if __name__ == "__main__":
    src = ("SHELL=/bin/bash\n# m h  dom mon dow   command\n*/5 * * * * /usr/bin/backup.sh --x\n"
           "#[matipanel-off] 0 3 * * mon-fri echo hi\n@reboot /opt/start\n\n# For example, you can run a backup\n"
           "# 0 5 * * 1 tar -zcf /var/backups/home.tgz /home/\n")
    es = parse_crontab(src)
    assert serialize([e["raw"] for e in es]) == src
    assert [e["type"] for e in es] == ["env", "other", "job", "job", "job", "other", "other", "other"]
    assert es[2]["enabled"] and es[2]["schedule"] == "*/5 * * * *" and es[2]["command"] == "/usr/bin/backup.sh --x"
    assert not es[3]["enabled"] and es[3]["command"] == "echo hi"
    assert es[4]["schedule"] == "@reboot"
    # La plantilla de Debian ("# 0 5 * * 1 tar …") es un comentario, no una tarea desactivada
    assert not is_job_line(es[7]["raw"]) and is_job_line(es[3]["raw"]) and is_job_line(es[2]["raw"])
    assert toggle_line(es[3]["raw"]) == "0 3 * * mon-fri echo hi"
    assert toggle_line("@daily x") == "#[matipanel-off] @daily x"
    assert toggle_line(toggle_line("*/5 * * * * a b")) == "*/5 * * * * a b"
    assert job_line("1 2 * * *", "ls", False) == "#[matipanel-off] 1 2 * * * ls"
    assert parse_job(toggle_line(job_line("1 2 * * *", "ls", False))) == {"schedule": "1 2 * * *", "command": "ls"}
    off = parse_crontab(job_line("@daily", "ls", False))[0]
    assert off["type"] == "job" and not off["enabled"] and off["command"] == "ls"
    assert build_schedule({"preset": "@daily"}) == "@daily" and build_schedule({"preset": "@x"}) is None
    assert build_schedule(dict(minute="0", hour="*/2", day="1-15", month="jan,jun", weekday="*")) == "0 */2 1-15 jan,jun *"
    assert build_schedule(dict(minute="0;rm", hour="*", day="*", month="*", weekday="*")) is None
    assert serialize([]) == ""
    # FF / U+2028 no parten la línea (cron solo separa por LF)
    es = parse_crontab("# nota\x0c* * * * * oculto\n#x\u2028@reboot y\n")
    assert [e["type"] for e in es] == ["other", "other"] and len(split_lines("a\nb\n")) == 2
    assert not USER_RE.match("root\n")
    print("ok")
