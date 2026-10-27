"""Respaldos de las apps desplegadas a otro servidor Linux por SSH.

Cada respaldo es una carpeta <destino>/<app>/<AAAAMMDD-HHMMSS>/ en el servidor remoto con:
  db.sql.gz (MariaDB, mariadb-dump --databases --add-drop-database) o db.pgdump (PostgreSQL, pg_dump -Fc)
  vol-<volumen>.tar.gz   una por volumen persistente (storage de Laravel o carpetas persistentes PHP)
  config.tar.gz          app.json, env, db.env y secrets.json (para poder recrear la app y su base)
  manifest.json          tamaños y SHA-256 de cada archivo

Todo viaja por un solo canal SSH por archivo, sin escribir en el disco local. La carpeta se crea
como <stamp>.partial y se renombra al terminar: un respaldo cortado nunca aparece como válido.
La clave SSH es propia del panel (/var/lib/matipanel/backup_key) y la huella del servidor queda
fijada en backup_known_hosts la primera vez.

ponytail: un hilo programador y un respaldo a la vez en todo el servidor; estado en un JSON.
"""
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zlib
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from . import deployer as D

BASE = Path(os.environ.get("MATIPANEL_APPBACKUP_DIR", "/var/lib/matipanel"))
CONFIG_PATH = BASE / "appbackup.json"
KEY_PATH = BASE / "backup_key"
KNOWN_HOSTS = BASE / "backup_known_hosts"
STAMP_RE = re.compile(r"\d{8}-\d{6}")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
USER_RE = re.compile(r"[a-z_][a-z0-9_.-]{0,31}")
PATH_RE = re.compile(r"/[A-Za-z0-9._/-]{1,200}")
FILE_RE = re.compile(r"(db\.sql\.gz|db\.pgdump|config\.tar\.gz|vol-[a-z0-9_.-]{1,120}\.tar\.gz)")
SCHEDULES = {"6h": "Cada 6 horas", "daily": "Diario", "weekly": "Semanal (domingo)"}
DEFAULT_APP = {"enabled": True, "schedule": "daily", "time": "02:00", "keep": 14}
STEP_TIMEOUT = 4 * 3600
CHUNK = 256 * 1024

_lock = threading.RLock()
_run_lock = threading.Lock()          # un respaldo o restauración a la vez
_logs = {}                            # app → deque de líneas del último trabajo
_status = {}                          # app → {"busy": "respaldo"|"restauración", "step": texto}
_started = False


class Fail(Exception):
    pass


# ── Configuración ────────────────────────────────────────────────────────

def load():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    dest = data.get("dest") if isinstance(data.get("dest"), dict) else {}
    return {"dest": {"host": dest.get("host", ""), "port": dest.get("port", 22), "user": dest.get("user", ""),
                     "path": dest.get("path", "")},
            "apps": data.get("apps") if isinstance(data.get("apps"), dict) else {},
            "state": data.get("state") if isinstance(data.get("state"), dict) else {}}


def save(cfg):
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def update(fn):
    with _lock:
        cfg = load()
        fn(cfg)
        save(cfg)
        return cfg


def app_settings(cfg, name):
    s = dict(DEFAULT_APP)
    s.update({k: v for k, v in (cfg["apps"].get(name) or {}).items() if k in DEFAULT_APP})
    return s


def validate_dest(d):
    host, user, path = str(d.get("host", "")).strip(), str(d.get("user", "")).strip(), str(d.get("path", "")).strip().rstrip("/")
    try:
        port = int(d.get("port") if d.get("port") not in (None, "") else 22)
    except (TypeError, ValueError):
        return None, "Puerto inválido"
    if not HOST_RE.fullmatch(host):
        return None, "Servidor inválido (IP o nombre)"
    if not USER_RE.fullmatch(user):
        return None, "Usuario inválido"
    if not 1 <= port <= 65535:
        return None, "Puerto inválido"
    if not PATH_RE.fullmatch(path) or any(p in (".", "..") for p in path.split("/")[1:]) or path.count("/") < 2:
        return None, "Carpeta inválida: ruta absoluta de al menos dos niveles, p. ej. /srv/respaldos/matipanel"
    return {"host": host, "port": port, "user": user, "path": path}, ""


def validate_app(d):
    s = dict(DEFAULT_APP)
    s["enabled"] = bool(d.get("enabled", True))
    if d.get("schedule") not in SCHEDULES:
        return None, "Programación inválida"
    s["schedule"] = d["schedule"]
    t = str(d.get("time", "02:00"))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        return None, "Hora inválida (HH:MM)"
    s["time"] = t
    try:
        s["keep"] = int(d.get("keep", 14))
    except (TypeError, ValueError):
        return None, "Cantidad a conservar inválida"
    if not 1 <= s["keep"] <= 365:
        return None, "Conserva entre 1 y 365 respaldos"
    return s, ""


def configured(cfg=None):
    return bool((cfg or load())["dest"]["host"]) and KEY_PATH.exists()


# ── SSH ──────────────────────────────────────────────────────────────────

def ensure_key():
    if KEY_PATH.exists():
        return
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    code, out, err = D._run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"matipanel-respaldos@{os.uname().nodename if hasattr(os, 'uname') else 'panel'}",
                             "-f", str(KEY_PATH)], timeout=30)
    if code != 0:
        raise Fail((err or out).strip() or "ssh-keygen falló")
    os.chmod(KEY_PATH, 0o600)


def public_key():
    try:
        return (KEY_PATH.with_suffix(".pub")).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ssh(dest, remote_cmd):
    return ["ssh", "-F", "/dev/null", "-i", str(KEY_PATH), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4",
            "-p", str(int(dest["port"])), f"{dest['user']}@{dest['host']}", remote_cmd]


def rsh(dest, remote_cmd, timeout=120):
    code, out, err = D._run(ssh(dest, remote_cmd), timeout=timeout)
    if code != 0:
        raise Fail(_ssh_error(err or out, code))
    return out


def _ssh_error(text, code):
    text = (text or "").strip()
    hints = [("Permission denied", "el servidor rechazó la clave: agrega la clave pública en ~/.ssh/authorized_keys del usuario"),
             ("REMOTE HOST IDENTIFICATION HAS CHANGED", "la huella del servidor cambió; si es correcto, bórrala con «Olvidar huella»"),
             ("Connection refused", "el servidor no acepta conexiones SSH en ese puerto"),
             ("timed out", "no hay respuesta del servidor (¿IP, puerto o firewall?)"),
             ("No route to host", "no hay ruta al servidor"), ("Could not resolve", "no se pudo resolver el nombre del servidor")]
    hint = next((h for k, h in hints if k in text), "")
    last = text.splitlines()[-1] if text else f"ssh terminó con código {code}"
    return (last + (f" — {hint}" if hint else ""))[:500]


def q(path):
    return shlex.quote(path)


def fingerprints():
    if not KNOWN_HOSTS.exists():
        return []
    code, out, _ = D._run(["ssh-keygen", "-l", "-f", str(KNOWN_HOSTS)], timeout=10)
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if code == 0 else []


def forget_host():
    try:
        KNOWN_HOSTS.unlink()
    except FileNotFoundError:
        pass


def test(dest):
    ensure_key()
    out = rsh(dest, f"mkdir -p -- {q(dest['path'])} && test -w {q(dest['path'])} && df -Pk -- {q(dest['path'])} | tail -1",
              timeout=40)
    parts = out.split()
    free = int(parts[3]) * 1024 if len(parts) >= 4 and parts[3].isdigit() else None
    return {"free": free, "fingerprints": fingerprints()}


# ── Transferencia ────────────────────────────────────────────────────────

def _popen(cmd, **kw):
    return subprocess.Popen(cmd, start_new_session=True, **kw)


def _killall(*procs):
    for p in procs:
        try:
            if p and p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL) if hasattr(os, "killpg") else p.kill()
        except OSError:
            pass


def push(dest, producer, remote_path, gzip_it=False, timeout=STEP_TIMEOUT):
    """Ejecuta `producer` y manda su salida a `remote_path` por SSH. → (bytes, sha256). Lanza Fail."""
    perr = tempfile.TemporaryFile()
    serr = tempfile.TemporaryFile()
    p = _popen(producer, stdout=subprocess.PIPE, stderr=perr, stdin=subprocess.DEVNULL)
    s = _popen(ssh(dest, f"cat > {q(remote_path)}"), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=serr)
    timer = threading.Timer(timeout, _killall, (p, s))
    timer.start()
    h, size = hashlib.sha256(), 0
    comp = zlib.compressobj(6, zlib.DEFLATED, 31) if gzip_it else None  # wbits 31 = formato gzip
    try:
        def send(data):
            nonlocal size
            if data:
                h.update(data)
                size += len(data)
                s.stdin.write(data)
        try:
            while chunk := p.stdout.read(CHUNK):
                send(comp.compress(chunk) if comp else chunk)
            if comp:
                send(comp.flush())
            s.stdin.close()
        except BrokenPipeError:
            pass
        pc, sc = p.wait(), s.wait()
    finally:
        timer.cancel()
        _killall(p, s)
    if pc != 0:
        perr.seek(0)
        raise Fail(f"{os.path.basename(producer[0])} falló ({pc}): " + perr.read().decode(errors="replace").strip()[-400:])
    if sc != 0:
        serr.seek(0)
        raise Fail("Envío por SSH falló: " + _ssh_error(serr.read().decode(errors="replace"), sc))
    return size, h.hexdigest()


def pull(dest, remote_path, consumer, gunzip=False, timeout=STEP_TIMEOUT):
    """Trae `remote_path` por SSH y lo entrega a la entrada de `consumer`. Lanza Fail."""
    serr = tempfile.TemporaryFile()
    cerr = tempfile.TemporaryFile()
    s = _popen(ssh(dest, f"cat -- {q(remote_path)}"), stdout=subprocess.PIPE, stderr=serr, stdin=subprocess.DEVNULL)
    c = _popen(consumer, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=cerr)
    timer = threading.Timer(timeout, _killall, (s, c))
    timer.start()
    dec = zlib.decompressobj(47) if gunzip else None  # 47 = detectar gzip
    try:
        try:
            while chunk := s.stdout.read(CHUNK):
                c.stdin.write(dec.decompress(chunk) if dec else chunk)
            if dec:
                c.stdin.write(dec.flush())
            c.stdin.close()
        except BrokenPipeError:
            pass
        except zlib.error as e:
            _killall(s, c)
            raise Fail(f"Archivo dañado: {e}")
        sc, cc = s.wait(), c.wait()
    finally:
        timer.cancel()
        _killall(s, c)
    if sc != 0:
        serr.seek(0)
        raise Fail("Lectura por SSH falló: " + _ssh_error(serr.read().decode(errors="replace"), sc))
    if cc != 0:
        cerr.seek(0)
        raise Fail(f"{os.path.basename(consumer[0])} falló ({cc}): " + cerr.read().decode(errors="replace").strip()[-600:])


# ── Qué se respalda ──────────────────────────────────────────────────────

def db_container(name):
    return f"{D.project_name(name)}-db-1"


def app_volumes(cfg):
    """Volúmenes de datos de la app (no los de la base: esa se respalda con su volcado)."""
    vols = list(D.persist_volumes(cfg)) if D.is_php(cfg) else ["storage"]
    return [f"{D.project_name(cfg['name'])}_{v}" for v in vols]


def volume_mountpoint(vol, create=False):
    code, out, _ = D._run(["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", vol], timeout=20)
    if code != 0 and create:
        project, _, short = vol.partition("_")
        D._run(["docker", "volume", "create", "--label", f"com.docker.compose.project={project}",
                "--label", f"com.docker.compose.volume={short}", vol], timeout=30)
        code, out, _ = D._run(["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", vol], timeout=20)
    mp = out.strip()
    if code != 0 or not mp.startswith("/var/lib/docker/volumes/") or ".." in mp:
        return None
    return mp


def _log(name, text):
    with _lock:
        _logs.setdefault(name, deque(maxlen=300)).append(f"[{datetime.now():%H:%M:%S}] {text}")


def _step(name, text):
    with _lock:
        if name in _status:
            _status[name]["step"] = text
    _log(name, text)


def backup(name, trigger="manual"):
    """Respalda una app. → dict con el resultado. Lanza Fail si algo falla (y deja estado y aviso)."""
    cfg_all = load()
    dest = cfg_all["dest"]
    if not configured(cfg_all):
        raise Fail("Configura primero el servidor de respaldos")
    cfg = D.load_config(name)
    if not cfg or cfg.get("draft"):
        raise Fail("La aplicación no existe")
    if not _run_lock.acquire(blocking=False):
        raise Fail("Ya hay un respaldo o una restauración en curso; espera a que termine")
    with _lock:
        _logs[name] = deque(maxlen=300)
        _status[name] = {"busy": "respaldo", "step": "Preparando"}
    t0 = time.time()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    app_path = f"{dest['path']}/{name}"
    work = f"{app_path}/{stamp}.partial"
    files = {}
    try:
        _step(name, f"Respaldo de «{name}» ({trigger}) hacia {dest['user']}@{dest['host']}:{work}")
        # restos de respaldos interrumpidos (solo hay uno a la vez), luego la carpeta de trabajo
        rsh(dest, f"mkdir -p -- {q(app_path)} && rm -rf -- {q(app_path)}/*.partial && mkdir -- {q(work)}")
        if cfg.get("database") == "mariadb":
            _step(name, "Volcando la base de datos MariaDB")
            dbn = D.db_name(cfg)
            files["db.sql.gz"] = push(dest, ["docker", "exec", db_container(name), "sh", "-c",
                                             'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb-dump -uroot --single-transaction '
                                             '--routines --triggers --events --add-drop-database --databases "$1"', "sh", dbn],
                                      f"{work}/db.sql.gz", gzip_it=True)
        elif cfg.get("database") == "postgres":
            _step(name, "Volcando la base de datos PostgreSQL")
            files["db.pgdump"] = push(dest, ["docker", "exec", db_container(name), "pg_dump", "-U", D.db_name(cfg), "-Fc",
                                             D.db_name(cfg)], f"{work}/db.pgdump")
        for vol in app_volumes(cfg):
            mp = volume_mountpoint(vol)
            if not mp:
                _log(name, f"Volumen {vol}: no existe todavía, se omite")
                continue
            short = vol.split("_", 1)[1]
            _step(name, f"Copiando el volumen {short}")
            files[f"vol-{short}.tar.gz"] = push(dest, ["tar", "-C", mp, "--numeric-owner", "-czf", "-", "."],
                                                f"{work}/vol-{short}.tar.gz")
        _step(name, "Copiando la configuración (.env y credenciales)")
        d = D.app_dir(name)
        items = [f for f in ("app.json", "env", "db.env", "secrets.json") if D.lstat_kind(d / f) == "file"]
        files["config.tar.gz"] = push(dest, ["tar", "-C", str(d), "-czf", "-", "--", *items], f"{work}/config.tar.gz")
        manifest = {"app": name, "created": stamp, "trigger": trigger, "kind": cfg.get("kind", "laravel"),
                    "database": cfg.get("database"), "db_name": D.db_name(cfg),
                    "commit": (cfg.get("current") or {}).get("sha", ""),
                    "files": {k: {"size": v[0], "sha256": v[1]} for k, v in files.items()}}
        mtmp = tempfile.NamedTemporaryFile(delete=False)
        mtmp.write(json.dumps(manifest, indent=2).encode())
        mtmp.close()
        try:
            push(dest, ["cat", mtmp.name], f"{work}/manifest.json")
        finally:
            os.unlink(mtmp.name)
        rsh(dest, f"mv -- {q(work)} {q(app_path + '/' + stamp)}")
        keep = app_settings(cfg_all, name)["keep"]
        removed = prune_remote(dest, name, keep)
        total = sum(v[0] for v in files.values())
        _step(name, f"Listo: {stamp} ({_human(total)}, {round(time.time() - t0)} s)" +
              (f"; se borraron {len(removed)} respaldos antiguos" if removed else ""))
        result = {"ok": True, "name": stamp, "size": total, "seconds": round(time.time() - t0)}
        update(lambda c: c["state"].__setitem__(name, {"last_t": time.time(), "last_ok": True, "last_error": "",
                                                       "last_name": stamp, "last_size": total, "trigger": trigger}))
        _notify(True, name, f"Respaldo {stamp}: {_human(total)} en {round(time.time() - t0)} s "
                            f"hacia {dest['host']}:{app_path}.")
        return result
    except Exception as e:
        msg = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"
        _log(name, "ERROR: " + msg)
        try:
            rsh(dest, f"rm -rf -- {q(work)}", timeout=60)
        except Fail:
            pass
        update(lambda c: c["state"].__setitem__(name, {**(c["state"].get(name) or {}), "last_t": time.time(),
                                                       "last_ok": False, "last_error": msg[:500], "trigger": trigger}))
        _notify(False, name, msg)
        raise Fail(msg)
    finally:
        with _lock:
            _status.pop(name, None)
        _run_lock.release()


def _notify(ok, name, text):
    try:
        from . import notify
        if ok:
            notify.event("backup_ok", f"Respaldo de «{name}» correcto", text, key=f"backup_ok:{name}", cooldown=0)
        else:
            notify.event("backup_fail", f"Falló el respaldo de «{name}»",
                         f"El respaldo de «{name}» falló:\n\n{text}\n\nSe reintentará en una hora.",
                         key=f"backup_fail:{name}", cooldown=6 * 3600)
    except Exception:
        pass


def _human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def list_remote(dest, name):
    """Respaldos completos de una app en el servidor remoto, del más nuevo al más viejo."""
    app_path = f"{dest['path']}/{name}"
    out = rsh(dest, f"cd -- {q(app_path)} 2>/dev/null || exit 0; for d in */; do d=${{d%/}}; "
                    f"case $d in *.partial) continue;; esac; printf '%s\\t' \"$d\"; du -sk -- \"$d\" | cut -f1; done",
              timeout=60)
    items = []
    for line in out.splitlines():
        stamp, _, kb = line.partition("\t")
        if STAMP_RE.fullmatch(stamp) and kb.strip().isdigit():
            items.append({"name": stamp, "size": int(kb) * 1024,
                          "t": datetime.strptime(stamp, "%Y%m%d-%H%M%S").timestamp()})
    return sorted(items, key=lambda x: x["name"], reverse=True)


def prune_remote(dest, name, keep):
    old = [b["name"] for b in list_remote(dest, name)][keep:]
    if old:
        app_path = f"{dest['path']}/{name}"
        rsh(dest, "rm -rf -- " + " ".join(q(f"{app_path}/{s}") for s in old), timeout=300)
    return old


def delete_remote(dest, name, stamp):
    if not STAMP_RE.fullmatch(stamp):
        raise Fail("Respaldo inválido")
    rsh(dest, f"rm -rf -- {q(dest['path'] + '/' + name + '/' + stamp)}", timeout=300)


def manifest(dest, name, stamp):
    if not STAMP_RE.fullmatch(stamp):
        raise Fail("Respaldo inválido")
    out = rsh(dest, f"cat -- {q(dest['path'] + '/' + name + '/' + stamp + '/manifest.json')}", timeout=30)
    try:
        data = json.loads(out)
    except ValueError:
        raise Fail("El respaldo no tiene un manifest.json válido")
    data["files"] = {k: v for k, v in (data.get("files") or {}).items() if FILE_RE.fullmatch(k)}
    return data


# ── Restauración ─────────────────────────────────────────────────────────

def _empty_dir(mp):
    """Vacía el contenido del volumen sin seguir enlaces simbólicos."""
    for e in os.scandir(mp):
        if e.is_dir(follow_symlinks=False):
            shutil.rmtree(e.path)
        else:
            os.unlink(e.path)


def restore(name, stamp, parts):
    """parts ⊆ {"db", "volumes"}. Detiene la app, restaura y la vuelve a levantar."""
    cfg_all = load()
    dest = cfg_all["dest"]
    cfg = D.load_config(name)
    if not cfg or cfg.get("draft"):
        raise Fail("La aplicación no existe")
    if D.pending(name):
        raise Fail("Hay un despliegue en curso o en cola para esta app; espera a que termine")
    m = manifest(dest, name, stamp)
    if not _run_lock.acquire(blocking=False):
        raise Fail("Ya hay un respaldo o una restauración en curso; espera a que termine")
    with _lock:
        _logs[name] = deque(maxlen=300)
        _status[name] = {"busy": "restauración", "step": "Preparando"}
    rdir = f"{dest['path']}/{name}/{stamp}"
    svcs = D.app_services(cfg)
    stopped = False
    try:
        _step(name, f"Restaurando «{name}» desde el respaldo {stamp}")
        if svcs:
            _step(name, "Deteniendo la aplicación: " + ", ".join(svcs))
            code, o, e = D._run(D.compose(name, "stop", "-t", "20", *svcs), timeout=180)
            stopped = code == 0
        if "db" in parts:
            if "db.sql.gz" in m["files"] and cfg.get("database") == "mariadb":
                _step(name, "Restaurando la base de datos MariaDB")
                pull(dest, f"{rdir}/db.sql.gz", ["docker", "exec", "-i", db_container(name), "sh", "-c",
                                                 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot'], gunzip=True)
            elif "db.pgdump" in m["files"] and cfg.get("database") == "postgres":
                _step(name, "Restaurando la base de datos PostgreSQL")
                pull(dest, f"{rdir}/db.pgdump", ["docker", "exec", "-i", db_container(name), "pg_restore", "-U",
                                                 D.db_name(cfg), "-d", D.db_name(cfg), "--clean", "--if-exists", "--no-owner"])
            else:
                _log(name, "El respaldo no tiene una base de datos compatible con la configuración actual; se omite")
        if "volumes" in parts:
            for fname in [f for f in m["files"] if f.startswith("vol-")]:
                short = fname[4:-7]
                vol = f"{D.project_name(name)}_{short}"
                mp = volume_mountpoint(vol, create=True)
                if not mp:
                    raise Fail(f"No se pudo preparar el volumen {vol}")
                _step(name, f"Restaurando el volumen {short}")
                _empty_dir(mp)
                pull(dest, f"{rdir}/{fname}", ["tar", "-C", mp, "--numeric-owner", "-xzf", "-"])
        _step(name, "Restauración terminada")
        return {"ok": True}
    except Exception as e:
        msg = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"
        _log(name, "ERROR: " + msg)
        raise Fail(msg)
    finally:
        if svcs and stopped:
            _step(name, "Levantando la aplicación")
            D._run(D.compose(name, "up", "-d", "--no-deps", *svcs), timeout=300)
        with _lock:
            _status.pop(name, None)
        _run_lock.release()


# ── Programador ──────────────────────────────────────────────────────────

def due(settings, state, now):
    """¿Toca respaldar? Tras un fallo se reintenta a la hora."""
    if not settings["enabled"]:
        return False
    last = state.get("last_t", 0)
    if state.get("last_ok") is False and now - last >= 3600:
        return True
    dt = datetime.fromtimestamp(now)
    if settings["schedule"] == "6h":
        return now - last >= 6 * 3600 - 60
    hh, mm = map(int, settings["time"].split(":"))
    slot = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if settings["schedule"] == "weekly":
        slot -= timedelta(days=(slot.weekday() + 1) % 7)  # domingo de esta semana
    if slot > dt:
        slot -= timedelta(days=7 if settings["schedule"] == "weekly" else 1)
    return last < slot.timestamp()


def _scheduler():
    time.sleep(120)
    while True:
        try:
            cfg = load()
            if configured(cfg):
                now = time.time()
                for name in D.list_apps():
                    app = D.load_config(name) or {}
                    if app.get("draft") or not app.get("active_slot"):
                        continue
                    if due(app_settings(cfg, name), cfg["state"].get(name) or {}, now):
                        try:
                            backup(name, "programado")
                        except Fail:
                            pass
                        cfg = load()
        except Exception:
            pass
        time.sleep(60)


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_scheduler, daemon=True, name="matipanel-appbackup").start()


def run_async(fn, *args):
    """Lanza un respaldo o restauración en segundo plano (la pantalla sigue el avance con status())."""
    threading.Thread(target=lambda: _swallow(fn, *args), daemon=True).start()


def _swallow(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


def status(name):
    with _lock:
        return dict(_status.get(name) or {}), list(_logs.get(name) or [])


if __name__ == "__main__":
    def ts(s):
        return datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()
    daily = {"enabled": True, "schedule": "daily", "time": "02:00", "keep": 14}
    assert due(daily, {}, ts("2026-10-26 01:00"))                                       # nunca respaldada
    assert not due(daily, {"last_t": ts("2026-10-26 02:01"), "last_ok": True}, ts("2026-10-26 23:00"))
    assert due(daily, {"last_t": ts("2026-10-25 02:01"), "last_ok": True}, ts("2026-10-26 02:05"))
    assert not due(daily, {"last_t": ts("2026-10-25 02:01"), "last_ok": True}, ts("2026-10-26 01:59"))
    assert due(daily, {"last_t": ts("2026-10-26 02:01"), "last_ok": False}, ts("2026-10-26 03:02"))  # reintento
    assert not due({**daily, "enabled": False}, {}, ts("2026-10-26 03:00"))
    weekly = {**daily, "schedule": "weekly"}                                              # 2026-10-25 es domingo
    assert due(weekly, {"last_t": ts("2026-10-18 02:01"), "last_ok": True}, ts("2026-10-25 02:30"))
    assert not due(weekly, {"last_t": ts("2026-10-25 02:01"), "last_ok": True}, ts("2026-10-31 23:00"))
    six = {**daily, "schedule": "6h"}
    assert due(six, {"last_t": ts("2026-10-26 00:00"), "last_ok": True}, ts("2026-10-26 06:00"))
    assert not due(six, {"last_t": ts("2026-10-26 00:00"), "last_ok": True}, ts("2026-10-26 05:00"))
    assert validate_dest({"host": "10.0.0.9", "user": "respaldos", "path": "/srv/respaldos/"})[0]["path"] == "/srv/respaldos"
    for bad in ({"host": "a;b", "user": "x", "path": "/srv/r"}, {"host": "h", "user": "Root!", "path": "/srv/r"},
                {"host": "h", "user": "u", "path": "/"}, {"host": "h", "user": "u", "path": "/srv/../etc"},
                {"host": "h", "user": "u", "path": "relativa/x"}, {"host": "h", "user": "u", "path": "/srv"},
                {"host": "h", "user": "u", "path": "/srv/r", "port": 0}):
        assert validate_dest(bad)[1], bad
    assert validate_app({"schedule": "daily", "time": "25:00"})[1] and validate_app({"schedule": "x"})[1]
    assert validate_app({"schedule": "weekly", "time": "03:30", "keep": "8"})[0]["keep"] == 8
    assert FILE_RE.fullmatch("vol-storage.tar.gz") and not FILE_RE.fullmatch("../x") and not FILE_RE.fullmatch("vol-a/b.tar.gz")
    cmd = ssh({"host": "10.0.0.9", "port": 22, "user": "respaldos", "path": "/srv/r"}, "true")
    assert cmd[-2] == "respaldos@10.0.0.9" and "BatchMode=yes" in cmd and f"UserKnownHostsFile={KNOWN_HOSTS}" in cmd
    print("ok")
