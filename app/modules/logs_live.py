"""
journalctl -f en vivo vía WebSocket (Flask-Sock).
Streamea líneas al navegador conforme llegan.
"""
import subprocess
import threading
from flask_login import current_user
from ..util import valid_name, same_origin

MAX_LINES_PER_SESSION = 10000


def register_websockets(sock):
    """Llamado desde app/__init__.py para registrar los WS."""

    @sock.route("/logs/ws/tail")
    def tail(ws):
        if not current_user.is_authenticated or not same_origin():
            ws.send("__error__: no autenticado")
            ws.close()
            return

        unit = ws.receive(timeout=10)
        if not isinstance(unit, str):  # None = timeout; "" = todo el journal (unidad opcional)
            ws.close()
            return
        unit = unit.strip()

        cmd = ["journalctl", "-f", "--no-pager", "--output=short-iso", "-n", "50"]
        if unit:
            if not valid_name(unit):
                ws.send(f"__error__: nombre de unidad inválido: {unit[:200]}")
                ws.close()
                return
            cmd += ["-u", unit]

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                # errors="replace": un mensaje con bytes no UTF-8 (cualquier usuario local puede
                # escribir en el journal con `logger`) mataba el hilo lector en silencio.
                text=True, errors="replace", bufsize=1,
            )
        except (FileNotFoundError, PermissionError) as e:
            ws.send(f"__error__: {e}")
            ws.close()
            return

        stop_flag = {"stop": False}

        def reader():
            count = 0
            for line in iter(proc.stdout.readline, ""):
                if stop_flag["stop"]:
                    break
                if count >= MAX_LINES_PER_SESSION:
                    # Avisar y cerrar: el receive() del bucle principal termina y limpia.
                    try:
                        ws.send(f"__error__: límite de {MAX_LINES_PER_SESSION} líneas "
                                "alcanzado; reconecta para seguir")
                        ws.close()
                    except Exception:
                        pass
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

