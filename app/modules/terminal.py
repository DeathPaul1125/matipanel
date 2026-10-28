"""
Terminal embebida: un shell en PTY como el usuario logueado, o una consola dentro de un contenedor
de Docker (docker exec), con puente bidireccional vía WebSocket con xterm.js.

La consola de contenedor exige permiso de Terminal (por la ruta /terminal/…) y además de Docker; el
contenedor debe estar en marcha y su nombre se valida. Entra con el usuario de la imagen (www-data en
las apps de Despliegues) o, si se pide, como root del contenedor (no del servidor). Queda en la Auditoría.
"""
import os
import re
import shutil
import subprocess
import pty
import select
import termios
import struct
import fcntl
import signal
import threading
import pwd
import json
import time
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required, current_user
from ..util import same_origin
from .. import audit, roles

terminal_bp = Blueprint("terminal", __name__)
MAX_MSG = 1 << 20  # 1 MB por mensaje del navegador (un pegado grande cabe de sobra)
RCFILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "terminal_rc.sh")
CONTAINER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
# Dentro del contenedor: bash si lo tiene (con prompt que dice dónde estás), si no sh
CONTAINER_SHELL = ('export PS1="\\[\\e[1;36m\\]\\u@$MP_CONTAINER\\[\\e[0m\\]:\\[\\e[1;34m\\]\\w\\[\\e[0m\\]\\$ "; '
                   'if command -v bash >/dev/null 2>&1; then exec bash --norc -i; else exec sh -i; fi')


@terminal_bp.route("/")
@login_required
def index():
    return render_template("terminal.html")


def running_containers():
    """Contenedores en marcha: [{name, project, image}] (vacío si no hay Docker)."""
    if not shutil.which("docker"):
        return []
    try:
        r = subprocess.run(["docker", "ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Image}}'],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        name, project, image = (line.split("\t") + ["", ""])[:3]
        if CONTAINER_RE.fullmatch(name):
            out.append({"name": name, "project": project, "image": image})
    return sorted(out, key=lambda c: (c["project"] or "~", c["name"]))


def _may_use_docker(user):
    return roles.allowed(user, "docker.index")[0]


@terminal_bp.route("/api/containers")
@login_required
def containers():
    if not _may_use_docker(current_user.username):
        return jsonify({"ok": True, "containers": [], "docker": False})
    return jsonify({"ok": True, "containers": running_containers(), "docker": bool(shutil.which("docker"))})


def register_websockets(sock):
    @sock.route("/terminal/ws")
    def ws_handler(ws):
        if not current_user.is_authenticated or not same_origin():
            ws.close()
            return

        username = current_user.username
        try:
            user_info = pwd.getpwnam(username)
        except KeyError:
            ws.send(json.dumps({"type": "error", "msg": "usuario inexistente"}))
            ws.close()
            return

        audit.log("terminal", "abrir terminal", username)
        pid, fd = pty.fork()
        if pid == 0:
            # Hijo: NUNCA debe volver al código del panel (es una copia del servidor, con root).
            # Cualquier excepción → _exit(1). Nunca entregar un shell root por accidente.
            try:
                _exec_shell(username, user_info)
            except BaseException:
                pass
            os._exit(1)
        _bridge(ws, pid, fd)

    @sock.route("/terminal/ws/container")
    def container_ws(ws):
        if not current_user.is_authenticated or not same_origin():
            ws.close()
            return
        name, as_root = request.args.get("name", ""), request.args.get("user") == "root"
        if not _may_use_docker(current_user.username):
            ws.send(json.dumps({"type": "error", "msg": "Tu usuario no tiene permiso para Docker"}))
            ws.close()
            return
        if not CONTAINER_RE.fullmatch(name) or name not in {c["name"] for c in running_containers()}:
            ws.send(json.dumps({"type": "error", "msg": "Ese contenedor no existe o no está en marcha"}))
            ws.close()
            return
        audit.log("terminal", "consola de contenedor", name, detail={"usuario": "root" if as_root else "el de la imagen"})
        pid, fd = pty.fork()
        if pid == 0:
            try:
                _exec_container(name, as_root)
            except BaseException:
                pass
            os._exit(1)
        _bridge(ws, pid, fd)


def _bridge(ws, pid, fd):
    """Puente navegador ↔ PTY hasta que se cierre el WebSocket o termine el proceso."""
    # Escrituras no bloqueantes en el PTY: si el programa no lee su entrada (p. ej. un pegado
    # enorme), un os.write bloqueante congelaría el único worker gevent y todo el panel.
    try:
        os.set_blocking(fd, False)
    except OSError:
        pass
    stop_flag = {"stop": False}

    def reader():
        while not stop_flag["stop"]:
            try:
                rlist, _, _ = select.select([fd], [], [], 0.2)
                if fd in rlist:
                    try:
                        data = os.read(fd, 4096)
                    except BlockingIOError:
                        continue
                    if not data:
                        break
                    ws.send(json.dumps({
                        "type": "data",
                        "data": data.decode("utf-8", errors="replace"),
                    }))
            except (OSError, ValueError):
                break
            except Exception:
                break
        try:  # el programa terminó (exit): cerrar también el navegador
            ws.close()
        except Exception:
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            if len(msg) > MAX_MSG:
                continue
            try:
                payload = json.loads(msg)
            except (ValueError, RecursionError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "input":
                data = payload.get("data")
                if isinstance(data, str) and data:
                    _write_all(fd, data.encode("utf-8", errors="replace"))
            elif payload.get("type") == "resize":
                rows, cols = _dim(payload.get("rows"), 24), _dim(payload.get("cols"), 80)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass
    finally:
        stop_flag["stop"] = True
        try:
            os.close(fd)
        except OSError:
            pass
        _reap(pid)


def _dim(v, default):
    """Filas/columnas: enteros 1..1000 (struct 'H' revienta con negativos o > 65535)."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, 1000))


def _write_all(fd, data, timeout=10):
    """Escribe en el PTY (no bloqueante) cediendo el control mientras el búfer esté lleno.
    Si el programa no lee en `timeout` s, el resto se descarta."""
    view = memoryview(data)
    deadline = time.monotonic() + timeout
    while view:
        try:
            n = os.write(fd, view)
            view = view[n:]
        except BlockingIOError:
            if time.monotonic() > deadline:
                return False
            select.select([], [fd], [], 0.5)  # cooperativo bajo gevent
    return True


def _close_inherited_fds():
    """El hijo de pty.fork() hereda TODOS los fds del panel (socket de escucha, conexiones de
    otros usuarios, sqlite, logs…). Se cierran todos menos 0-2 (el PTY) antes del exec."""
    try:
        fds = [int(x) for x in os.listdir("/proc/self/fd")]
    except OSError:
        fds = None
    if fds is None:
        try:
            maxfd = os.sysconf("SC_OPEN_MAX")
        except (AttributeError, ValueError, OSError):
            maxfd = 65536
        os.closerange(3, maxfd)
        return
    for x in fds:
        if x > 2:
            try:
                os.close(x)
            except OSError:
                pass


def _exec_shell(username, user_info):
    """Corre en el hijo: baja privilegios (fail-closed), limpia el entorno y hace exec del shell."""
    _close_inherited_fds()
    uid, gid = user_info.pw_uid, user_info.pw_gid
    if os.getuid() == 0:
        os.initgroups(username, gid)
    os.setgid(gid)
    os.setuid(uid)
    # Comprobación explícita: si algo no cambió como se esperaba, abortar.
    if os.getuid() != uid or os.geteuid() != uid or os.getgid() != gid or os.getegid() != gid:
        os._exit(1)
    if uid != 0:
        try:
            os.setuid(0)  # no debe ser posible recuperar root
        except OSError:
            pass
        else:
            os._exit(1)
    # Entorno limpio: no heredar variables del panel (PATH del venv, etc.)
    lang = os.environ.get("LANG", "C.UTF-8")
    os.environ.clear()
    os.environ.update({
        "HOME": user_info.pw_dir, "USER": username, "LOGNAME": username,
        "SHELL": user_info.pw_shell or "/bin/bash", "TERM": "xterm-256color",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": lang,
    })
    try:
        os.chdir(user_info.pw_dir)
    except OSError:
        os.chdir("/tmp")
    shell = user_info.pw_shell or "/bin/bash"
    # bash: banner y prompt de MatiPanel (el rc carga también el entorno del usuario)
    argv = ([shell, "--rcfile", RCFILE, "-i"]
            if os.path.basename(shell) == "bash" and os.path.isfile(RCFILE)
            else [shell, "-l"])
    try:
        os.execvp(shell, argv)
    except OSError:
        os.execvp("/bin/sh", ["/bin/sh"])


def _exec_container(name, as_root):
    """Corre en el hijo: docker exec con terminal dentro del contenedor (ya validado)."""
    _close_inherited_fds()
    lang = os.environ.get("LANG", "C.UTF-8")
    os.environ.clear()
    os.environ.update({"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM": "xterm-256color",
                       "LANG": lang, "HOME": "/root"})
    argv = ["docker", "exec", "-it", "-e", "TERM=xterm-256color", "-e", f"MP_CONTAINER={name}",
            *(["-u", "0"] if as_root else []), name, "sh", "-c", CONTAINER_SHELL]
    os.execvp("docker", argv)


def _reap(pid):
    """SIGHUP al shell; SIGKILL si no termina en 2 s. Evita zombies."""
    try:
        os.kill(pid, signal.SIGHUP)
        for _ in range(20):
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
            time.sleep(0.1)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (OSError, ChildProcessError):
        pass
