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
from flask import Blueprint, render_template
from flask_login import login_required, current_user

terminal_bp = Blueprint("terminal", __name__)


@terminal_bp.route("/")
@login_required
def index():
    return render_template("terminal.html")


def register_websockets(sock):
    @sock.route("/terminal/ws")
    def ws_handler(ws):
        if not current_user.is_authenticated:
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
            try:
                os.setgid(user_info.pw_gid)
                os.setuid(user_info.pw_uid)
            except PermissionError:
                pass
            os.environ["HOME"] = user_info.pw_dir
            os.environ["USER"] = username
            os.environ["LOGNAME"] = username
            os.environ["TERM"] = "xterm-256color"
            try:
                os.chdir(user_info.pw_dir)
            except OSError:
                os.chdir("/tmp")
            shell = user_info.pw_shell or "/bin/bash"
            try:
                os.execvp(shell, [shell, "-l"])
            except OSError:
                os.execvp("/bin/sh", ["/bin/sh"])
            return

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
            try:
                os.kill(pid, signal.SIGHUP)
            except (OSError, ProcessLookupError):
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
