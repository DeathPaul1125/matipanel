"""Helpers compartidos entre módulos."""
import ipaddress
import subprocess
import time
from urllib.parse import urlparse
from flask import request

_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@.-_")


def run(cmd, timeout=30, input=None, env=None, cwd=None):
    """Ejecuta un comando SIN shell. Retorna (returncode, stdout, stderr)."""
    try:
        # errors="replace": una salida con bytes no UTF-8 (p. ej. un log con basura escrita por
        # un atacante) no debe tumbar la petición con UnicodeDecodeError.
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
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


def limiter_key(addr):
    """Clave de rate-limit para una IP: IPv6 se agrupa por /64 (un atacante suele tener la /64
    entera y podría rotar direcciones); IPv4 mapeada en IPv6 se trata como IPv4."""
    try:
        ip = ipaddress.ip_address(str(addr or "").split("%")[0])
    except ValueError:
        return str(addr or "?")
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class RateLimiter:
    """ponytail: en memoria, válido con --workers 1; usar Redis si se escala a varios workers.

    Las claves que sean IPs se normalizan con limiter_key(); el resto (p. ej. "user:alice")
    se usan tal cual. El diccionario se poda para que un atacante con muchas IPs no lo haga
    crecer sin límite."""

    MAX_KEYS = 20000

    def __init__(self, max_fails=5, window=300):
        self.max_fails, self.window = max_fails, window
        self.fails = {}

    def _recent(self, key, now):
        recent = [t for t in self.fails.get(key, ()) if now - t < self.window]
        if recent:
            self.fails[key] = recent
        else:
            self.fails.pop(key, None)  # no guardar entradas vacías (crecimiento por cada IP nueva)
        return recent

    def blocked(self, key):
        return len(self._recent(limiter_key(key), time.time())) >= self.max_fails

    def fail(self, key):
        now = time.time()
        if len(self.fails) >= self.MAX_KEYS:
            self._prune(now)
        self.fails.setdefault(limiter_key(key), []).append(now)

    def reset(self, key):
        self.fails.pop(limiter_key(key), None)

    def _prune(self, now):
        for k in list(self.fails):
            self._recent(k, now)
        if len(self.fails) >= self.MAX_KEYS:
            # Aún lleno: descartar las claves con el fallo más antiguo (se pierde algo de memoria
            # de rate-limit para esas claves, pero el proceso no crece sin límite).
            for k in sorted(self.fails, key=lambda k: self.fails[k][-1])[:self.MAX_KEYS // 2]:
                del self.fails[k]


if __name__ == "__main__":
    assert valid_name("nginx.service") and valid_name("getty@tty1.service")
    assert not valid_name("--now") and not valid_name("a b") and not valid_name("a;b")
    assert valid_name("org/img", extra="/") and not valid_name("org/img")
    rl = RateLimiter(max_fails=2, window=60)
    assert not rl.blocked("ip")
    rl.fail("ip"); rl.fail("ip")
    assert rl.blocked("ip")
    rl.reset("ip")
    assert not rl.blocked("ip") and "ip" not in rl.fails
    # IPv6: toda la /64 comparte contador; IPv4 mapeada = IPv4
    rl.fail("2001:db8::1"); rl.fail("2001:db8::ffff:2")
    assert rl.blocked("2001:db8::abcd") and not rl.blocked("2001:db8:0:1::1")
    rl.fail("::ffff:10.0.0.1"); rl.fail("10.0.0.1")
    assert rl.blocked("10.0.0.1")
    assert limiter_key("user:alice") == "user:alice" and limiter_key(None) == "?"
    # Poda: no crece sin límite
    rl2 = RateLimiter(max_fails=2, window=60)
    rl2.MAX_KEYS = 100
    for i in range(1000):
        rl2.fail(f"10.1.{i // 256}.{i % 256}")
    assert len(rl2.fails) <= 100
    print("ok")
