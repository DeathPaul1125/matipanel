"""Helpers compartidos entre módulos."""
import subprocess
import time
from urllib.parse import urlparse
from flask import request

_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@.-_")


def run(cmd, timeout=30, input=None, env=None, cwd=None):
    """Ejecuta un comando SIN shell. Retorna (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           input=input, env=env, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} no está instalado"
    except subprocess.TimeoutExpired:
        return 124, "", "Tiempo de espera agotado"


def bytes_to_human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def valid_name(name, extra=""):
    """Nombre de unidad systemd / contenedor: sin espacios, sin opciones (-x)."""
    if not name or len(name) > 200 or name.startswith("-"):
        return False
    return all(c in _NAME_CHARS or c in extra for c in name)


def same_origin():
    """Protege WebSockets contra cross-site WebSocket hijacking."""
    origin = request.headers.get("Origin", "")
    return bool(origin) and urlparse(origin).netloc == request.host


class RateLimiter:
    """ponytail: en memoria, válido con --workers 1; usar Redis si se escala a varios workers."""

    def __init__(self, max_fails=5, window=300):
        self.max_fails, self.window = max_fails, window
        self.fails = {}

    def blocked(self, key):
        now = time.time()
        recent = [t for t in self.fails.get(key, []) if now - t < self.window]
        self.fails[key] = recent
        return len(recent) >= self.max_fails

    def fail(self, key):
        self.fails.setdefault(key, []).append(time.time())

    def reset(self, key):
        self.fails.pop(key, None)


if __name__ == "__main__":
    assert valid_name("nginx.service") and valid_name("getty@tty1.service")
    assert not valid_name("--now") and not valid_name("a b") and not valid_name("a;b")
    assert valid_name("org/img", extra="/") and not valid_name("org/img")
    rl = RateLimiter(max_fails=2, window=60)
    assert not rl.blocked("ip")
    rl.fail("ip"); rl.fail("ip")
    assert rl.blocked("ip")
    rl.reset("ip")
    assert not rl.blocked("ip")
    print("ok")
