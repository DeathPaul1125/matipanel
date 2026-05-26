"""
journalctl -f en vivo vía WebSocket (Flask-Sock).
Streamea líneas al navegador conforme llegan.
"""
import subprocess
import threading
from flask import Blueprint
from flask_login import current_user

logs_live_bp = Blueprint("logs_live", __name__)

MAX_LINES_PER_SESSION = 10000


def register_websockets(sock):
    """Llamado desde app/__init__.py para registrar los WS."""

    @sock.route("/logs/ws/tail")
    def tail(ws):
        if not current_user.is_authenticated:
            ws.send("__error__: no autenticado")
            ws.close()
            return

        unit = ws.receive(timeout=10)
        if not unit:
            ws.close()
            return
        unit = unit.strip()

        cmd = ["journalctl", "-f", "--no-pager", "--output=short-iso", "-n", "50"]
        if unit and _valid_unit(unit):
            cmd += ["-u", unit]

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except (FileNotFoundError, PermissionError) as e:
            ws.send(f"__error__: {e}")
            ws.close()
            return

        stop_flag = {"stop": False}

        def reader():
            count = 0
            for line in iter(proc.stdout.readline, ""):
                if stop_flag["stop"] or count >= MAX_LINES_PER_SESSION:
                    break
                try:
                    ws.send(line.rstrip())
                except Exception:
                    break
                count += 1

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        try:
            while True:
                msg = ws.receive()
                if msg is None or msg == "stop":
                    break
        except Exception:
            pass
        finally:
            stop_flag["stop"] = True
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, Exception):
                try:
                    proc.kill()
                except Exception:
                    pass


def _valid_unit(name):
    if not name or len(name) > 200:
        return False
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@.-_"
    return all(c in allowed for c in name)
