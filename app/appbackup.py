"""Respaldos de las apps desplegadas a otros servidores: Linux/Unix por SSH o Windows/NAS por SMB.

Puede haber varios servidores de destino; cada app elige el suyo. Para SMB (carpeta compartida de
Windows, con una cuenta del Active Directory) el recurso se monta con mount.cifs solo mientras dura
la operación, en /run/matipanel-smb/<id>, y las MISMAS órdenes de shell que van por SSH se ejecutan
ahí como «nobody» (setpriv): si el servidor presentara un enlace simbólico hacia este equipo, esas
órdenes no pueden leer ni escribir nada de root. La contraseña se escribe a un archivo de
credenciales 0600 solo durante el montaje (nunca en la línea de órdenes) y no vuelve al navegador.

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
import io
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import zlib
from collections import deque
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from . import deployer as D

BASE = Path(os.environ.get("MATIPANEL_APPBACKUP_DIR", "/var/lib/matipanel"))
CONFIG_PATH = BASE / "appbackup.json"
KEY_PATH = BASE / "backup_key"
KNOWN_HOSTS = BASE / "backup_known_hosts"
MOUNT_ROOT = os.environ.get("MATIPANEL_SMB_MOUNTS", "/run/matipanel-smb")
NOBODY = 65534
TYPES = {"ssh": "Linux / Unix (SSH)", "smb": "Windows / NAS (SMB)"}
STAMP_RE = re.compile(r"\d{8}-\d{6}")
ID_RE = re.compile(r"[a-z0-9]{4,16}")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
USER_RE = re.compile(r"[a-z_][a-z0-9_.-]{0,31}")
PATH_RE = re.compile(r"/[A-Za-z0-9._/-]{1,200}")
SHARE_RE = re.compile(r"\w[\w .$-]{0,79}")
SMB_PART_RE = re.compile(r"\w[\w .-]{0,79}")
SMB_USER_RE = re.compile(r"\w[\w.@$ -]{0,99}")
DOMAIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,99}")
FILE_RE = re.compile(r"(db\.sql\.gz|db\.pgdump|config\.tar\.gz|vol-[a-z0-9_.-]{1,120}\.tar\.gz)")
SCHEDULES = {"6h": "Cada 6 horas", "daily": "Diario", "weekly": "Semanal (domingo)"}
# config.tar.gz: con esto se recrea la app en otro servidor (recuperación ante desastres). Incluye la clave
# de despliegue: sin ella un repositorio privado no se podría volver a clonar.
CONFIG_FILES = ("app.json", "env", "db.env", "secrets.json", "deploy_key", "deploy_key.pub", "known_hosts")
CONFIG_MAX = 8 * 1024 * 1024
DB_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
DEFAULT_APP = {"enabled": True, "schedule": "daily", "time": "02:00", "keep": 14, "dest": ""}
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
    dests = [d for d in data.get("dests") or [] if isinstance(d, dict) and d.get("type") in TYPES
             and ID_RE.fullmatch(str(d.get("id", "")))] if isinstance(data.get("dests"), list) else []
    old = data.get("dest")
    if not dests and isinstance(old, dict) and old.get("host"):  # formato anterior: un solo servidor Linux
        dests = [{"id": "principal", "name": old["host"], "type": "ssh", "host": old["host"], "port": old.get("port", 22),
                  "user": old.get("user", ""), "path": old.get("path", "")}]
    return {"dests": dests,
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


def validate_dest(d, old=None):
    """Normaliza un servidor de destino. `old`: el guardado (al editar), conserva id y contraseña."""
    kind = d.get("type", "ssh")
    if kind not in TYPES:
        return None, "Tipo de servidor inválido"
    host = str(d.get("host", "")).strip()
    try:
        port = int(d.get("port") if d.get("port") not in (None, "") else (22 if kind == "ssh" else 445))
    except (TypeError, ValueError):
        return None, "Puerto inválido"
    if not HOST_RE.fullmatch(host):
        return None, "Servidor inválido (IP o nombre)"
    if not 1 <= port <= 65535:
        return None, "Puerto inválido"
    name = " ".join("".join(c for c in str(d.get("name") or "") if c.isprintable()).split())[:60] or host
    out = {"id": old["id"] if old else secrets.token_hex(4), "name": name, "type": kind, "host": host, "port": port}
    if kind == "ssh":
        user, path = str(d.get("user", "")).strip(), str(d.get("path", "")).strip().rstrip("/")
        if not USER_RE.fullmatch(user):
            return None, "Usuario inválido"
        if not PATH_RE.fullmatch(path) or any(p in (".", "..") for p in path.split("/")[1:]) or path.count("/") < 2:
            return None, "Carpeta inválida: ruta absoluta de al menos dos niveles, p. ej. /srv/respaldos/matipanel"
        out.update(user=user, path=path)
        return out, ""
    share = str(d.get("share", "")).strip().strip("\\/")
    if not SHARE_RE.fullmatch(share) or share.endswith((" ", ".")):
        return None, "Recurso compartido inválido: el nombre de la carpeta compartida, p. ej. Respaldos"
    parts = [p for p in str(d.get("path", "")).replace("\\", "/").strip().split("/") if p]
    if len(parts) > 8 or any(not SMB_PART_RE.fullmatch(p) or p.endswith((" ", ".")) for p in parts):
        return None, "Carpeta inválida: subcarpetas dentro del recurso, p. ej. MatiPanel o Apps\\Produccion"
    user, domain = str(d.get("user", "")).strip(), str(d.get("domain", "")).strip()
    if "\\" in user:  # DOMINIO\usuario
        domain, user = user.split("\\", 1)
    if not SMB_USER_RE.fullmatch(user):
        return None, "Usuario inválido (usuario, DOMINIO\\usuario o usuario@dominio)"
    if domain and not DOMAIN_RE.fullmatch(domain):
        return None, "Dominio inválido (p. ej. SENABED o senabed.local)"
    pw = str(d.get("password") or "")
    if not pw and old and old.get("type") == "smb":
        pw = old.get("password", "")
    if not pw:
        return None, "Escribe la contraseña de la cuenta"
    if len(pw) > 256 or any(c in pw for c in "\r\n\0"):
        return None, "Contraseña inválida"
    out.update(share=share, path="".join("/" + p for p in parts), domain=domain, user=user, password=pw,
               encrypt=bool(d.get("encrypt", True)))
    return out, ""


def public_dest(d):
    """Lo que ve el navegador: nunca la contraseña."""
    p = {k: v for k, v in d.items() if k != "password"}
    if d["type"] == "smb":
        p["has_password"] = bool(d.get("password"))
    return p


def describe(d):
    if d["type"] == "smb":
        who = (d["domain"] + "\\" if d.get("domain") else "") + d["user"]
        return f"\\\\{d['host']}\\{d['share']}{d['path'].replace('/', chr(92))} ({who})"
    return f"{d['user']}@{d['host']}:{d['path']}"


def find_dest(cfg, dest_id):
    return next((d for d in cfg["dests"] if d["id"] == dest_id), None)


def app_dest(cfg, name, dest_id=None):
    """Servidor de una app: el pedido (`dest_id`), el elegido en su programación o el primero."""
    if dest_id:
        return find_dest(cfg, dest_id)
    return find_dest(cfg, app_settings(cfg, name)["dest"]) or (cfg["dests"][0] if cfg["dests"] else None)


def validate_app(d, dest_ids=()):
    s = dict(DEFAULT_APP)
    s["enabled"] = bool(d.get("enabled", True))
    s["dest"] = str(d.get("dest") or "")
    if s["dest"] and s["dest"] not in dest_ids:
        return None, "Servidor de respaldo inválido"
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


def ready(dest):
    return bool(dest) and (dest["type"] == "smb" or KEY_PATH.exists())


def configured(cfg=None):
    return any(ready(d) for d in (cfg or load())["dests"])


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


def _local(cmd):
    """Orden sobre el recurso SMB montado, sin privilegios (ver el docstring del módulo)."""
    return ["setpriv", f"--reuid={NOBODY}", f"--regid={NOBODY}", "--clear-groups", "--no-new-privs", "sh", "-c", cmd]


def remote(dest, cmd):
    return _local(cmd) if dest["type"] == "smb" else ssh(dest, cmd)


def base(dest):
    """Carpeta raíz de los respaldos tal como la ven las órdenes de `remote`."""
    return f"{MOUNT_ROOT}/{dest['id']}{dest['path']}" if dest["type"] == "smb" else dest["path"]


def rsh(dest, remote_cmd, timeout=120):
    code, out, err = D._run(remote(dest, remote_cmd), timeout=timeout)
    if code != 0:
        raise Fail(_ssh_error(err or out, code, dest))
    return out


def _ssh_error(text, code, dest=None):
    text = (text or "").strip()
    if dest and dest["type"] == "smb":
        hints = [("Permission denied", "la cuenta no tiene permiso de escritura en la carpeta compartida (permisos del recurso y NTFS)"),
                 ("No space left", "la carpeta compartida no tiene espacio libre"),
                 ("Host is down", "se perdió la conexión con el servidor Windows")]
        hint = next((h for k, h in hints if k in text), "")
        last = text.splitlines()[-1] if text else f"terminó con código {code}"
        return (last + (f" — {hint}" if hint else ""))[:500]
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


def forget_host(dest=None):
    """Olvida la huella de un servidor SSH (o todas, sin `dest`)."""
    if dest is None:
        try:
            KNOWN_HOSTS.unlink()
        except FileNotFoundError:
            pass
    elif dest["type"] == "ssh" and KNOWN_HOSTS.exists():
        host = dest["host"] if int(dest["port"]) == 22 else f"[{dest['host']}]:{int(dest['port'])}"
        D._run(["ssh-keygen", "-q", "-R", host, "-f", str(KNOWN_HOSTS)], timeout=10)


def test(dest):
    if dest["type"] == "ssh":
        ensure_key()
    b = q(base(dest))
    probe = q(base(dest) + "/.matipanel-prueba")
    with connected(dest):  # escribe y borra un archivo: comprueba el permiso real, no solo el que se ve
        out = rsh(dest, f"mkdir -p -- {b} && touch -- {probe} && rm -f -- {probe} && df -Pk -- {b} | tail -1", timeout=40)
    parts = out.split()
    free = int(parts[3]) * 1024 if len(parts) >= 4 and parts[3].isdigit() else None
    return {"free": free, "fingerprints": fingerprints() if dest["type"] == "ssh" else []}


# ── SMB (Windows / NAS) ──────────────────────────────────────────────────

_mounts = {}                          # punto de montaje → operaciones que lo usan
_mount_lock = threading.RLock()


@contextmanager
def connected(dest):
    """Monta el recurso SMB mientras dura el bloque (anidable). Con SSH no hace nada."""
    if dest["type"] != "smb":
        yield
        return
    mp = f"{MOUNT_ROOT}/{dest['id']}"
    with _mount_lock:
        if not _mounts.get(mp):
            _mount(dest, mp)
        _mounts[mp] = _mounts.get(mp, 0) + 1
    try:
        yield
    finally:
        with _mount_lock:
            _mounts[mp] -= 1
            if not _mounts[mp]:
                del _mounts[mp]
                _umount(mp)


def _mount(dest, mp):
    if not shutil.which("mount.cifs"):
        raise Fail("Falta cifs-utils para conectar con carpetas compartidas de Windows: instálalo en Paquetes "
                   "o vuelve a ejecutar el instalador del panel")
    os.makedirs(MOUNT_ROOT, exist_ok=True)
    os.chmod(MOUNT_ROOT, 0o711)  # «nobody» puede atravesarla para llegar a su montaje, no listarla
    if os.path.ismount(mp):      # resto de un reinicio del panel a mitad de un respaldo
        _umount(mp)
    os.makedirs(mp, mode=0o700, exist_ok=True)
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    cred = BASE / f".smb-{dest['id']}.cred"
    try:
        os.unlink(cred)
    except FileNotFoundError:
        pass
    fd = os.open(cred, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"username={dest['user']}\npassword={dest['password']}\n" + (f"domain={dest['domain']}\n" if dest.get("domain") else ""))
    opts = [f"credentials={cred}", f"port={int(dest['port'])}", f"uid={NOBODY}", f"gid={NOBODY}", "forceuid", "forcegid",
            "file_mode=0600", "dir_mode=0700", "nosuid", "nodev", "noexec", "soft", "echo_interval=15"]
    if dest.get("encrypt", True):
        opts.append("seal")  # cifrado SMB3 de punta a punta
    try:
        code, out, err = D._run(["mount", "-t", "cifs", "-o", ",".join(opts), f"//{dest['host']}/{dest['share']}", mp], timeout=45)
    finally:
        os.unlink(cred)
    if code != 0:
        try:
            os.rmdir(mp)
        except OSError:
            pass
        raise Fail(_smb_error(err or out, code))


def _umount(mp):
    if D._run(["umount", "--", mp], timeout=60)[0] != 0:
        D._run(["umount", "-l", "--", mp], timeout=30)
    try:
        os.rmdir(mp)
    except OSError:
        pass


def _smb_error(text, code):
    text = (text or "").strip()
    hints = {"13": "usuario, contraseña o dominio incorrectos, o la cuenta no tiene acceso al recurso compartido",
             "2": "no existe ese recurso compartido en el servidor (revisa el nombre)",
             "6": "no existe ese recurso compartido en el servidor (revisa el nombre)",
             "95": "el servidor no admite SMB3 cifrado: desmarca «Cifrar la conexión» (servidores antiguos o NAS)",
             "111": "el servidor rechazó la conexión en ese puerto (¿compartir archivos activado? ¿firewall?)",
             "112": "el servidor no responde (¿apagado o firewall en el puerto 445?)",
             "113": "no hay ruta al servidor", "115": "no hay respuesta del servidor (¿IP, puerto o firewall?)",
             "22": "datos rechazados: revisa el dominio y el usuario"}
    m = re.search(r"mount error\((\d+)\)", text)
    hint = hints.get(m.group(1), "") if m else ""
    if "unknown filesystem type" in text or "wrong fs type" in text:
        hint = "falta el soporte CIFS: instala cifs-utils"
    last = next((ln for ln in text.splitlines() if "mount error" in ln), text.splitlines()[-1] if text else f"mount terminó con código {code}")
    return ("No se pudo conectar a la carpeta compartida: " + last + (f" — {hint}" if hint else ""))[:500]


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
    """Ejecuta `producer` y manda su salida a `remote_path` en el destino. → (bytes, sha256). Lanza Fail."""
    perr = tempfile.TemporaryFile()
    serr = tempfile.TemporaryFile()
    p = _popen(producer, stdout=subprocess.PIPE, stderr=perr, stdin=subprocess.DEVNULL)
    s = _popen(remote(dest, f"cat > {q(remote_path)}"), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=serr)
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
        raise Fail("Envío al servidor de respaldos falló: " + _ssh_error(serr.read().decode(errors="replace"), sc, dest))
    return size, h.hexdigest()


def pull(dest, remote_path, consumer, gunzip=False, timeout=STEP_TIMEOUT):
    """Trae `remote_path` del destino y lo entrega a la entrada de `consumer`. Lanza Fail."""
    serr = tempfile.TemporaryFile()
    cerr = tempfile.TemporaryFile()
    s = _popen(remote(dest, f"cat -- {q(remote_path)}"), stdout=subprocess.PIPE, stderr=serr, stdin=subprocess.DEVNULL)
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
        raise Fail("Lectura del servidor de respaldos falló: " + _ssh_error(serr.read().decode(errors="replace"), sc, dest))
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
    dest = app_dest(cfg_all, name)
    if not ready(dest):
        raise Fail("Configura primero un servidor de respaldos")
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
    app_path = f"{base(dest)}/{name}"
    work = f"{app_path}/{stamp}.partial"
    files = {}
    stack, linked = ExitStack(), False
    try:
        _step(name, f"Respaldo de «{name}» ({trigger}) hacia «{dest['name']}»: {describe(dest)}")
        stack.enter_context(connected(dest))
        linked = True
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
        items = [f for f in CONFIG_FILES if D.lstat_kind(d / f) == "file"]
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
        stack.close()
        total = sum(v[0] for v in files.values())
        _step(name, f"Listo: {stamp} ({_human(total)}, {round(time.time() - t0)} s)" +
              (f"; se borraron {len(removed)} respaldos antiguos" if removed else ""))
        result = {"ok": True, "name": stamp, "size": total, "seconds": round(time.time() - t0)}
        update(lambda c: c["state"].__setitem__(name, {"last_t": time.time(), "last_ok": True, "last_error": "",
                                                       "last_name": stamp, "last_size": total, "trigger": trigger,
                                                       "dest_name": dest["name"]}))
        _notify(True, name, f"Respaldo {stamp}: {_human(total)} en {round(time.time() - t0)} s "
                            f"hacia «{dest['name']}» ({describe(dest)}).")
        return result
    except Exception as e:
        msg = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"
        _log(name, "ERROR: " + msg)
        if linked:
            try:
                rsh(dest, f"rm -rf -- {q(work)}", timeout=60)
            except Fail:
                pass
        update(lambda c: c["state"].__setitem__(name, {**(c["state"].get(name) or {}), "last_t": time.time(),
                                                       "last_ok": False, "last_error": msg[:500], "trigger": trigger,
                                                       "dest_name": dest["name"]}))
        _notify(False, name, msg)
        raise Fail(msg)
    finally:
        try:
            stack.close()
        except Exception:
            pass
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
    app_path = f"{base(dest)}/{name}"
    with connected(dest):
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
        app_path = f"{base(dest)}/{name}"
        with connected(dest):
            rsh(dest, "rm -rf -- " + " ".join(q(f"{app_path}/{s}") for s in old), timeout=300)
    return old


def delete_remote(dest, name, stamp):
    if not STAMP_RE.fullmatch(stamp):
        raise Fail("Respaldo inválido")
    with connected(dest):
        rsh(dest, f"rm -rf -- {q(base(dest) + '/' + name + '/' + stamp)}", timeout=300)


def manifest(dest, name, stamp):
    if not STAMP_RE.fullmatch(stamp):
        raise Fail("Respaldo inválido")
    with connected(dest):
        try:
            out = rsh(dest, f"cat -- {q(base(dest) + '/' + name + '/' + stamp + '/manifest.json')}", timeout=30)
        except Fail as e:
            if "No such file" in str(e):
                raise Fail(f"El respaldo {stamp} de «{name}» está incompleto o ya no existe (falta manifest.json)")
            raise
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


def restore(name, stamp, parts, dest_id=None):
    """parts ⊆ {"db", "volumes"}. Detiene la app, restaura y la vuelve a levantar."""
    dest = app_dest(load(), name, dest_id)
    if not ready(dest):
        raise Fail("Ese servidor de respaldos no existe")
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
    rdir = f"{base(dest)}/{name}/{stamp}"
    svcs = D.app_services(cfg)
    stopped = False
    stack = ExitStack()
    try:
        _step(name, f"Restaurando «{name}» desde el respaldo {stamp} de «{dest['name']}»")
        stack.enter_context(connected(dest))
        if svcs:
            _step(name, "Deteniendo la aplicación: " + ", ".join(svcs))
            code, o, e = D._run(D.compose(name, "stop", "-t", "20", *svcs), timeout=180)
            stopped = code == 0
        _restore_data(dest, name, cfg, rdir, m, parts)
        _step(name, "Restauración terminada")
        return {"ok": True}
    except Exception as e:
        msg = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"
        _log(name, "ERROR: " + msg)
        raise Fail(msg)
    finally:
        try:
            stack.close()
        except Exception:
            pass
        if svcs and stopped:
            _step(name, "Levantando la aplicación")
            D._run(D.compose(name, "up", "-d", "--no-deps", *svcs), timeout=300)
        with _lock:
            _status.pop(name, None)
        _run_lock.release()


def _restore_data(dest, name, cfg, rdir, m, parts):
    """Base de datos y/o volúmenes de un respaldo, con el contenedor de la base ya en marcha."""
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


# ── Recuperación ante desastres ──────────────────────────────────────────

def remote_apps(dest):
    """Apps con respaldos completos en un servidor. → [{name, backups: [stamps, del más nuevo], local}]"""
    with connected(dest):
        out = rsh(dest, f"cd -- {q(base(dest))} 2>/dev/null || exit 0; "
                        "for d in */*/; do printf '%s\\n' \"${d%/}\"; done", timeout=60)
    found = {}
    for line in out.splitlines():
        app, _, stamp = line.partition("/")
        if D.valid_name(app) and STAMP_RE.fullmatch(stamp):  # sin .partial: solo respaldos terminados
            found.setdefault(app, []).append(stamp)
    local = set(D.list_apps())
    return [{"name": a, "backups": sorted(v, reverse=True), "local": a in local} for a, v in sorted(found.items())]


def read_config_archive(data):
    """Archivos de config.tar.gz leídos en memoria: solo archivos normales con nombres conocidos
    (ni rutas ni enlaces del archivo llegan al disco). → {nombre: bytes}"""
    out = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            for mem in tf:
                if mem.isreg() and mem.name in CONFIG_FILES and mem.size <= CONFIG_MAX and mem.name not in out:
                    out[mem.name] = tf.extractfile(mem).read()
    except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
        raise Fail(f"config.tar.gz dañado: {e}")
    return out


def check_app_config(cfg, name):
    """El app.json del respaldo viene de otro servidor: se valida como si lo escribiera un usuario. → error o ''."""
    if not isinstance(cfg, dict):
        return "app.json ilegible"
    num = lambda v, lo, hi, kinds=(int,): type(v) in kinds and lo <= v <= hi  # noqa: E731
    persist = cfg.get("persist") or []
    exts = cfg.get("php_ext") or []
    checks = (
        ("name", cfg.get("name") == name),
        ("repo", D.parse_repo(cfg.get("repo"))),
        ("branch", D.valid_branch(cfg.get("branch"))),
        ("kind", cfg.get("kind", "laravel") in D.KINDS),
        ("database", cfg.get("database", "none") in D.DATABASES),
        ("php", str(cfg.get("php")) in D.PHP_VERSIONS),
        ("hostname", not cfg.get("hostname") or D.valid_hostname(cfg["hostname"])),
        ("db_name", isinstance(D.db_name(cfg), str) and DB_NAME_RE.fullmatch(D.db_name(cfg))),
        ("persist", isinstance(persist, list) and len(persist) <= D.MAX_PERSIST and all(D.valid_rel_path(x) for x in persist)),
        ("docroot", not cfg.get("docroot") or D.valid_rel_path(cfg["docroot"])),
        ("env_path", not cfg.get("env_path") or D.valid_rel_path(cfg["env_path"])),
        ("migrate_cmd", not cfg.get("migrate_cmd") or D.valid_migrate_cmd(cfg["migrate_cmd"])),
        ("php_ext", isinstance(exts, list) and all(isinstance(e, str) and D.PHP_EXT_RE.fullmatch(e) for e in exts)),
        ("port", num(cfg.get("port"), *D.PORT_EDIT_RANGE)),
        ("slot_base", num(cfg.get("slot_base"), *D.SLOT_RANGE)),
        ("container_port", num(cfg.get("container_port", 8080), 1, 65535)),
        ("mem_mb", num(cfg.get("mem_mb", 0), 0, 262144, (int, float))),
        ("cpus", num(cfg.get("cpus", 0), 0, 256, (int, float))),
    )
    return next((f"campo «{k}» inválido" for k, ok in checks if not ok), "")


def _read_remote(dest, path, cap=CONFIG_MAX + 1024 * 1024):
    """Archivo pequeño del destino, en bytes (config.tar.gz)."""
    try:
        r = subprocess.run(remote(dest, f"head -c {int(cap)} -- {q(path)}"), capture_output=True, timeout=120,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise Fail("El servidor de respaldos no respondió a tiempo")
    if r.returncode != 0:
        raise Fail("Lectura del servidor de respaldos falló: " +
                   _ssh_error(r.stderr.decode(errors="replace"), r.returncode, dest))
    return r.stdout


def recover(name, stamp, dest_id):
    """Recuperación ante desastres: recrea en este servidor una app que solo existe en los respaldos.

    Orden: configuración → base de datos y volúmenes → despliegue. Así las migraciones del primer despliegue
    corren sobre los datos recuperados (solo aplican las pendientes) y no sobre una base vacía.
    Si algo falla se deshace todo lo creado, para poder reintentar con otro respaldo."""
    dest = find_dest(load(), dest_id)
    if not ready(dest):
        raise Fail("Ese servidor de respaldos no existe")
    if not D.valid_name(name) or not STAMP_RE.fullmatch(stamp):
        raise Fail("Respaldo inválido")
    if D.lstat_kind(D.app_dir(name)) is not None:
        raise Fail(f"Ya existe una app «{name}» en este servidor: usa «Ver respaldos → Restaurar» o elimínala primero")
    if not D.compose_cmd():
        raise Fail("Instala Docker y Docker Compose en este servidor antes de recuperar apps")
    if not _run_lock.acquire(blocking=False):
        raise Fail("Ya hay un respaldo o una restauración en curso; espera a que termine")
    with _lock:
        _logs[name] = deque(maxlen=300)
        _status[name] = {"busy": "recuperación", "step": "Preparando"}
    rdir = f"{base(dest)}/{name}/{stamp}"
    d = D.app_dir(name)
    stack, created = ExitStack(), False
    try:
        _step(name, f"Recuperando «{name}» desde el respaldo {stamp} de «{dest['name']}»")
        stack.enter_context(connected(dest))
        m = manifest(dest, name, stamp)
        if "config.tar.gz" not in m["files"]:
            raise Fail("El respaldo no tiene la configuración de la app (config.tar.gz)")
        _step(name, "Leyendo la configuración del respaldo")
        files = read_config_archive(_read_remote(dest, f"{rdir}/config.tar.gz"))
        try:
            cfg = json.loads(files.get("app.json", b"").decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            cfg = None
        err = check_app_config(cfg, name)
        if err:
            raise Fail(f"La configuración del respaldo no es válida ({err})")
        try:
            sec = json.loads(files["secrets.json"].decode("utf-8")) if "secrets.json" in files else {}
        except (ValueError, UnicodeDecodeError):
            sec = {}
        sec = sec if isinstance(sec, dict) and all(isinstance(v, str) for v in sec.values()) else {}
        # El estado de ejecución era del servidor anterior: aquí todavía no hay ninguna versión desplegada
        cfg.update(draft=False, active_slot=None, slots={"blue": None, "green": None}, current=None, last_build_sha=None)
        ports, bases = D.used_ports()
        listening = D.listening_ports()
        if cfg["port"] in ports | listening:
            new = D.allocate_port(ports | listening)
            if not new:
                raise Fail("No quedan puertos libres")
            _log(name, f"El puerto {cfg['port']} está ocupado en este servidor: la app usará el {new}")
            cfg["port"] = new
        if cfg["slot_base"] in bases or {cfg["slot_base"], cfg["slot_base"] + 1} & listening:
            cfg["slot_base"] = D.allocate_slot_base(bases, listening)
            if not cfg["slot_base"]:
                raise Fail("No quedan puertos internos libres")

        _step(name, "Creando la aplicación con su configuración original")
        D._ensure_dir(D.BASE_DIR)
        D._ensure_dir(d)
        created = True
        if cfg.get("database") in ("mariadb", "postgres") and not sec.get("db_password"):
            sec.update(db_password=D.new_password(), db_root_password=D.new_password())
            files.pop("db.env", None)  # credenciales nuevas: el db.env viejo ya no sirve
        if cfg.get("database") in ("mariadb", "postgres") and "db.env" not in files:
            sec.setdefault("db_root_password", D.new_password())  # la base parte de cero: root nuevo sirve
        for f in ("env", "db.env", "deploy_key", "deploy_key.pub", "known_hosts"):
            if f in files:
                D.write_file(d / f, files[f])
        sec.setdefault("webhook_secret", secrets.token_hex(24))
        D.save_secrets(name, sec)
        if "env" not in files:
            D.write_env(name, D.default_env(cfg, sec))
        if "db.env" not in files:
            D.write_file(d / "db.env", D.db_env(cfg, sec))
        if "deploy_key" not in files:
            D.generate_key(name)
            if D.is_ssh(cfg["repo"]):
                _log(name, "AVISO: el respaldo no traía la clave de despliegue (es de una versión anterior del panel). "
                           "Se generó una nueva: agrégala en GitHub (Settings → Deploy keys) y vuelve a desplegar. "
                           "Clave pública: " + D.public_key(name))
        elif "known_hosts" not in files:
            D.write_file(d / "known_hosts", D.GITHUB_KNOWN_HOSTS)
        D.save_config(name, cfg)
        D.save_history(name, [])
        D._ensure_dir(d / "deployments")
        D._write_compose(name, cfg)

        deps = D.dep_services(cfg)
        if deps:
            _step(name, "Levantando la base de datos: " + ", ".join(deps))
            code, o, e = D._run(D.compose(name, "up", "-d", "--wait", *deps), timeout=600)
            if code != 0:
                raise Fail("docker compose up falló: " + (o + e).strip()[-400:])
        _restore_data(dest, name, cfg, rdir, m, ("db", "volumes"))
        dep, _ = D.enqueue(name, "manual")
        update(lambda c: c["apps"].__setitem__(name, {**app_settings(c, name), "dest": dest["id"]}))
        _step(name, f"Datos recuperados. Desplegando la app desde GitHub (rama {cfg['branch']}): "
                    f"sigue el avance en Despliegues → {name}")
        return {"ok": True, "deployment": dep["id"]}
    except Exception as e:
        msg = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {e}"
        _log(name, "ERROR: " + msg)
        if created:
            _log(name, "Se deshace la recuperación parcial (contenedores, volúmenes y carpeta) para poder reintentar")
            try:
                D.delete_app(name, volumes=True)
            except Exception:
                shutil.rmtree(d, ignore_errors=True)
        raise Fail(msg)
    finally:
        try:
            stack.close()
        except Exception:
            pass
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
    # Windows / SMB con cuenta del AD
    w, err = validate_dest({"type": "smb", "name": "Windows", "host": "srv-win01", "share": "Respaldos", "path": "\\MatiPanel\\Apps Prod\\",
                            "user": "SENABED\\svc-respaldos", "password": "p,a=s s"})
    assert not err and w["domain"] == "SENABED" and w["user"] == "svc-respaldos" and w["path"] == "/MatiPanel/Apps Prod", (w, err)
    assert w["port"] == 445 and w["encrypt"] and ID_RE.fullmatch(w["id"]) and "password" not in public_dest(w)
    assert base(w) == f"{MOUNT_ROOT}/{w['id']}/MatiPanel/Apps Prod" and describe(w).startswith("\\\\srv-win01\\Respaldos\\MatiPanel")
    assert remote(w, "true")[:2] == ["setpriv", "--reuid=65534"]  # nunca como root sobre el recurso montado
    edited, err = validate_dest({**public_dest(w), "password": ""}, w)  # al editar sin contraseña se conserva
    assert not err and edited["password"] == "p,a=s s" and edited["id"] == w["id"]
    for bad in ({"share": "a/../b"}, {"share": "x;y"}, {"path": "a/../b"}, {"path": "a/b."}, {"user": "u\nx"},
                {"domain": "dom;rm"}, {"password": "a\nusername=root"}, {"password": ""}, {"type": "ftp"}):
        assert validate_dest({"type": "smb", "host": "h", "share": "R", "user": "u", "password": "p", **bad})[1], bad
    assert "contraseña" in _smb_error("mount error(13): Permission denied", 32)
    cfg = {"dests": [w, {"id": "abcd1234", "type": "ssh"}], "apps": {"a": {"dest": "abcd1234"}}, "state": {}}
    assert app_dest(cfg, "a")["id"] == "abcd1234" and app_dest(cfg, "b")["id"] == w["id"] and app_dest(cfg, "a", "zzzz") is None
    assert validate_app({"schedule": "daily", "dest": "nope"}, ["abcd1234"])[1]
    # config.tar.gz manipulado: rutas, enlaces y nombres desconocidos no salen del archivo
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for nm, kind in (("app.json", tarfile.REGTYPE), ("../../etc/cron.d/x", tarfile.REGTYPE),
                         ("env", tarfile.SYMTYPE), ("otro.txt", tarfile.REGTYPE)):
            ti = tarfile.TarInfo(nm)
            ti.type = kind
            if kind == tarfile.SYMTYPE:
                ti.linkname = "/etc/shadow"
                tf.addfile(ti)
            else:
                ti.size = 2
                tf.addfile(ti, io.BytesIO(b"{}"))
    assert read_config_archive(buf.getvalue()) == {"app.json": b"{}"}
    good = {"name": "app1", "repo": "https://github.com/o/r", "branch": "main", "php": "8.3", "port": 8081,
            "slot_base": 20000, "kind": "php", "database": "mariadb", "persist": ["assets/uploads"]}
    assert check_app_config(good, "app1") == ""
    for k, v in (("name", "otra"), ("branch", "x;y"), ("repo", "file:///etc"), ("db_name", "a'b"), ("persist", ["../x"]),
                 ("php", "5.6"), ("port", "8081"), ("hostname", "-x"), ("migrate_cmd", "sh -c x"), ("php_ext", ["a b"])):
        assert check_app_config({**good, k: v}, "app1"), k
    print("ok")
