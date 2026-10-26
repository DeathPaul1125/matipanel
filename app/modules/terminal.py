"""
Terminal embebida: spawns un shell en PTY como el usuario logueado
y bridge bidireccional vía WebSocket con xterm.js.
"""
import os
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
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from ..util import same_origin

terminal_bp = Blueprint("terminal", __name__)


@terminal_bp.route("/")
@login_required
def index():
    return render_template("terminal.html")


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

        pid, fd = pty.fork()
        if pid == 0:
            # Hijo: bajar a los privilegios del usuario. Si falla, abortar:
            # nunca entregar un shell root por accidente.
            try:
                if os.getuid() == 0:
                    os.initgroups(username, user_info.pw_gid)
                os.setgid(user_info.pw_gid)
                os.setuid(user_info.pw_uid)
            except OSError:
                os._exit(1)
            # Entorno limpio: no heredar variables del panel (PATH del venv, etc.)
            lang = os.environ.get("LANG", "C.UTF-8")
            os.environ.clear()
            os.environ.update({
                "HOME": user_info.pw_dir, "USER": username, "LOGNAME": username,
                "SHELL": user_info.pw_shell or "/bin/bash", "TERM": "xterm-256color",
                "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": lang,
            })
            try:
                os.chdir(user_info.pw_dir)
            except OSError:
                os.chdir("/tmp")
            shell = user_info.pw_shell or "/bin/bash"
            try:
                os.execvp(shell, [shell, "-l"])
            except OSError:
                try:
                    os.execvp("/bin/sh", ["/bin/sh"])
                finally:
                    os._exit(1)

        stop_flag = {"stop": False}

        def reader():
            while not stop_flag["stop"]:
                try:
                    rlist, _, _ = select.select([fd], [], [], 0.2)
                    if fd in rlist:
                        data = os.read(fd, 4096)
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

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        try:
            while True:
                msg = ws.receive()
                if msg is None:
                    break
                try:
                    payload = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "input":
                    os.write(fd, payload.get("data", "").encode("utf-8"))
                elif payload.get("type") == "resize":
                    rows = int(payload.get("rows", 24))
                    cols = int(payload.get("cols", 80))
                    fcntl.ioctl(fd, termios.TIOCSWINSZ,
                                struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass
        finally:
            stop_flag["stop"] = True
            try:
                os.close(fd)
            except OSError:
                pass
            _reap(pid)


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
