"""Multi-servidor: lógica pura (sin Flask) — tokens, servidores, URLs y huellas TLS.

Archivos (root, 0600, escritura atómica):
  /var/lib/matipanel/remote_tokens.json   tokens que ESTE servidor acepta (solo el hash SHA-256)
  /var/lib/matipanel/remote_servers.json  servidores que ESTE panel vigila (incluye su token: secreto)
"""
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import ssl
import time
from pathlib import Path
from urllib.parse import urlparse

TOKENS_PATH = Path(os.environ.get("MATIPANEL_REMOTE_TOKENS", "/var/lib/matipanel/remote_tokens.json"))
SERVERS_PATH = Path(os.environ.get("MATIPANEL_REMOTE_SERVERS", "/var/lib/matipanel/remote_servers.json"))
CERT_PATH = os.environ.get("MATIPANEL_CERT", "/etc/matipanel/cert.pem")

DEFAULT_PORT = 8443
MAX_SERVERS = 50
MAX_TOKENS = 50
LAST_USED_EVERY = 60  # s: no reescribir el archivo en cada consulta (cada monitor pregunta cada 15 s)

_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")
_FP_RE = re.compile(r"^[0-9a-f]{64}$")
_PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


# ── Almacenamiento ──

def _load(path, key):
    try:
        with open(path) as f:
            data = json.load(f)
        items = data.get(key) if isinstance(data, dict) else None
        # Solo objetos: una entrada que no sea dict haría fallar los .get() (y la API remota con 500).
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except (OSError, ValueError):
        return []


def _save(path, key, items):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    # Creado ya con 0600 (el contenido es secreto desde el primer byte), nuevo (O_EXCL) y sin
    # seguir un symlink plantado en el lugar del temporal.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({key: items}, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _new_id():
    return secrets.token_hex(4)


def _clean_text(s, maxlen):
    s = "".join(c for c in str(s or "") if c.isprintable()).strip()
    return s[:maxlen]


# ── Tokens (lado monitoreado) ──

def hash_token(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def list_tokens(path=None):
    """Sin el hash: es lo que se muestra en pantalla."""
    return [{k: t.get(k) for k in ("id", "label", "created", "last_used", "last_ip")}
            for t in _load(path or TOKENS_PATH, "tokens")]


def create_token(label, path=None):
    """Retorna (token_en_claro, entrada). El token en claro solo existe en esta respuesta."""
    path = path or TOKENS_PATH
    label = _clean_text(label, 60)
    if not label:
        raise ValueError("Escribe una etiqueta (por ejemplo, el nombre del servidor que lo usará).")
    tokens = _load(path, "tokens")
    if len(tokens) >= MAX_TOKENS:
        raise ValueError(f"Máximo {MAX_TOKENS} tokens. Revoca los que no uses.")
    token = secrets.token_urlsafe(32)
    entry = {"id": _new_id(), "label": label, "hash": hash_token(token),
             "created": int(time.time()), "last_used": None, "last_ip": None}
    tokens.append(entry)
    _save(path, "tokens", tokens)
    return token, {k: v for k, v in entry.items() if k != "hash"}


def revoke_token(token_id, path=None):
    path = path or TOKENS_PATH
    tokens = _load(path, "tokens")
    kept = [t for t in tokens if t.get("id") != token_id]
    if len(kept) == len(tokens):
        return False
    _save(path, "tokens", kept)
    return True


def verify_token(token, ip=None, path=None):
    """id del token válido o None. Compara todos los hashes en tiempo constante."""
    path = path or TOKENS_PATH
    if not token or len(token) > 200:
        return None
    h = hash_token(token)
    tokens = _load(path, "tokens")
    found = None
    for t in tokens:
        if hmac.compare_digest(str(t.get("hash", "")).encode(), h.encode()):
            found = t
    if not found:
        return None
    now = int(time.time())
    last = found.get("last_used")
    if not isinstance(last, (int, float)) or now - last >= LAST_USED_EVERY or found.get("last_ip") != ip:
        found["last_used"], found["last_ip"] = now, ip
        try:
            _save(path, "tokens", tokens)
        except OSError:
            pass
    return found["id"]


# ── Huellas de certificado ──

def fp_of_der(der):
    return hashlib.sha256(der).hexdigest()


def normalize_fp(s):
    """'AB:CD:…' / 'ab cd …' → 64 hex en minúsculas, o None si no es una huella SHA-256."""
    s = re.sub(r"[\s:]", "", str(s or "")).lower()
    return s if _FP_RE.match(s) else None


def format_fp(s):
    n = normalize_fp(s)
    return ":".join(n[i:i + 2] for i in range(0, 64, 2)).upper() if n else ""


def fp_equal(a, b):
    a, b = normalize_fp(a), normalize_fp(b)
    return bool(a and b) and hmac.compare_digest(a, b)


def local_fingerprint(cert_path=None):
    """Huella SHA-256 del certificado de ESTE panel (primer certificado del PEM), o None."""
    try:
        with open(cert_path or CERT_PATH) as f:
            m = _PEM_RE.search(f.read())
        return fp_of_der(ssl.PEM_cert_to_DER_cert(m.group(0))) if m else None
    except (OSError, ValueError):
        return None


# ── URLs ──

def normalize_url(url):
    """'https://host:puerto' canónico. Sin puerto → 8443. Lanza ValueError con el motivo."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("Escribe la dirección del servidor.")
    if "://" not in url:
        url = "https://" + url
    if len(url) > 300 or any(c.isspace() or not c.isprintable() for c in url):
        raise ValueError("La dirección tiene caracteres no válidos.")
    try:
        u = urlparse(url)
        port = u.port
    except ValueError:
        raise ValueError("El puerto no es válido.")
    if u.scheme.lower() != "https":
        raise ValueError("La dirección debe empezar con https://")
    if u.username is not None or u.password is not None or "@" in u.netloc:
        raise ValueError("La dirección no puede llevar usuario ni contraseña.")
    if u.path not in ("", "/") or u.query or u.fragment or u.params:
        raise ValueError("Escribe solo https://servidor:puerto, sin rutas.")
    host = u.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
        host_out = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    except ValueError:
        if not _HOST_RE.match(host):
            raise ValueError("El nombre del servidor no es válido.")
        host_out = host.lower().rstrip(".")
    port = port or DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise ValueError("El puerto no es válido.")
    return f"https://{host_out}:{port}"


def split_url(url):
    """(host, puerto) de una URL ya normalizada (sin corchetes en IPv6)."""
    u = urlparse(url)
    return u.hostname, u.port


# ── Servidores (lado monitor) ──

def list_servers(path=None):
    return _load(path or SERVERS_PATH, "servers")


def public_server(s):
    """Lo que puede ver el navegador: nunca el token, solo sus últimos 4 caracteres."""
    tok = str(s.get("token") or "")
    return {"id": s.get("id"), "name": s.get("name"), "url": s.get("url"),
            "fp": format_fp(s.get("fp")), "token_hint": ("…" + tok[-4:]) if tok else "",
            "created": s.get("created")}


def save_server(data, path=None):
    """Alta (sin id) o edición. Token vacío al editar = conservar el actual. Lanza ValueError."""
    path = path or SERVERS_PATH
    servers = _load(path, "servers")
    sid = data.get("id") or None
    current = next((s for s in servers if s.get("id") == sid), None) if sid else None
    if sid and not current:
        raise ValueError("El servidor ya no existe.")

    name = _clean_text(data.get("name"), 60)
    if not name:
        raise ValueError("Escribe un nombre para el servidor.")
    url = normalize_url(data.get("url"))
    fp = normalize_fp(data.get("fp"))
    if not fp:
        raise ValueError("La huella SHA-256 no es válida (64 caracteres hexadecimales). Usa «Obtener huella».")
    token = str(data.get("token") or "").strip()
    if token:
        if len(token) > 200 or not re.match(r"^[A-Za-z0-9_\-]+$", token):
            raise ValueError("El token no tiene un formato válido.")
    elif current:
        token = current.get("token", "")
    else:
        raise ValueError("Pega el token generado en el otro servidor.")
    if any(s.get("url") == url and s.get("id") != sid for s in servers):
        raise ValueError("Ya hay un servidor con esa dirección.")

    if current:
        current.update(name=name, url=url, fp=fp, token=token)
        entry = current
    else:
        if len(servers) >= MAX_SERVERS:
            raise ValueError(f"Máximo {MAX_SERVERS} servidores.")
        entry = {"id": _new_id(), "name": name, "url": url, "fp": fp, "token": token,
                 "created": int(time.time())}
        servers.append(entry)
    _save(path, "servers", servers)
    return entry


def delete_server(sid, path=None):
    path = path or SERVERS_PATH
    servers = _load(path, "servers")
    kept = [s for s in servers if s.get("id") != sid]
    if len(kept) == len(servers):
        return False
    _save(path, "servers", kept)
    return True


# ── Respuesta remota ──

def _num(v, lo=None, hi=None):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return round(v, 2)


def clean_summary(d):
    """Lista blanca de lo que devuelve otro servidor: tipos fijos, textos cortos, números finitos.
    Un servidor remoto comprometido no debe poder colar nada raro en este panel."""
    if not isinstance(d, dict):
        return None
    out = {k: _clean_text(d.get(k), n) for k, n in
           (("hostname", 64), ("distro", 80), ("kernel", 80), ("uptime", 32), ("version", 20),
            ("mem_h", 40), ("disk_h", 40), ("swap_h", 40))}
    for k in ("cpu", "mem", "swap", "disk"):
        out[k] = _num(d.get(k), 0, 100)
    out["cores"] = _num(d.get("cores"), 0, 100000)
    out["uptime_s"] = _num(d.get("uptime_s"), 0)
    out["ts"] = _num(d.get("ts"), 0)
    load = d.get("load") if isinstance(d.get("load"), list) else []
    out["load"] = [x for x in (_num(v, 0) for v in load[:3]) if x is not None]
    failed = _num(d.get("failed"), 0, 100000)
    out["failed"] = int(failed) if failed is not None else None
    units = d.get("failed_units") if isinstance(d.get("failed_units"), list) else []
    out["failed_units"] = [_clean_text(u, 100) for u in units[:20]]
    upd = _num(d.get("updates"), 0, 1000000)
    out["updates"] = int(upd) if upd is not None else None
    hist = d.get("history") if isinstance(d.get("history"), list) else []
    out["history"] = [{"t": _num(p.get("t"), 0), "cpu": _num(p.get("cpu"), 0, 100), "mem": _num(p.get("mem"), 0, 100)}
                      for p in hist[-120:] if isinstance(p, dict)]
    return out


if __name__ == "__main__":
    import tempfile

    # URLs
    assert normalize_url("https://srv1.example.com:8443") == "https://srv1.example.com:8443"
    assert normalize_url("https://SRV1.example.com:8443/") == "https://srv1.example.com:8443"
    assert normalize_url("10.0.0.5") == "https://10.0.0.5:8443"
    assert normalize_url("https://[2001:db8::1]:9000") == "https://[2001:db8::1]:9000"
    assert split_url("https://[2001:db8::1]:9000") == ("2001:db8::1", 9000)
    for bad in ("http://a:8443", "https://u:p@a:8443", "https://a:8443/x", "https://a:99999",
                "https://a b:8443", "https://a:8443?x=1", "https://-bad-:8443", "", "https://a:8443#f",
                "https://a_b:8443", "javascript:alert(1)"):
        try:
            normalize_url(bad)
            raise AssertionError("aceptó " + bad)
        except ValueError:
            pass

    # Huellas
    der = b"certificado de prueba"
    h = fp_of_der(der)
    assert normalize_fp(format_fp(h)) == h and len(format_fp(h)) == 95
    assert fp_equal(format_fp(h), h.upper()) and fp_equal(" " + h + " ", format_fp(h))
    assert not fp_equal(h, "0" * 64) and not fp_equal("", "") and normalize_fp("xyz") is None
    assert format_fp("nope") == ""

    with tempfile.TemporaryDirectory() as d:
        tp, sp = Path(d) / "t.json", Path(d) / "s.json"
        # Tokens
        tok, entry = create_token("monitor central", tp)
        assert "hash" not in entry and len(tok) >= 40
        raw = json.loads(tp.read_text())
        assert raw["tokens"][0]["hash"] == hash_token(tok) and tok not in tp.read_text()
        if os.name == "posix":
            assert (tp.stat().st_mode & 0o777) == 0o600
        assert verify_token(tok, "1.2.3.4", tp) == entry["id"]
        assert list_tokens(tp)[0]["last_ip"] == "1.2.3.4" and "hash" not in list_tokens(tp)[0]
        assert verify_token(tok + "x", None, tp) is None and verify_token("", None, tp) is None
        assert verify_token("a" * 500, None, tp) is None
        try:
            create_token("   ", tp)
            raise AssertionError
        except ValueError:
            pass
        assert revoke_token(entry["id"], tp) and not revoke_token(entry["id"], tp)
        assert verify_token(tok, None, tp) is None

        # Servidores
        s = save_server({"name": "Web 1", "url": "web1.local", "fp": format_fp(h), "token": "abc_DEF-123"}, sp)
        assert s["url"] == "https://web1.local:8443" and s["fp"] == h
        p = public_server(s)
        assert "token" not in p and p["token_hint"] == "…-123"
        assert "abc_DEF-123" not in json.dumps(p)
        s2 = save_server({"id": s["id"], "name": "Web uno", "url": "https://web1.local:8443", "fp": h, "token": ""}, sp)
        assert s2["token"] == "abc_DEF-123" and s2["name"] == "Web uno" and len(list_servers(sp)) == 1
        for bad in ({"name": "", "url": "x.local", "fp": h, "token": "t"},
                    {"name": "x", "url": "x.local", "fp": "123", "token": "t"},
                    {"name": "x", "url": "x.local", "fp": h, "token": ""},
                    {"name": "x", "url": "x.local", "fp": h, "token": "a b"},
                    {"name": "x", "url": "web1.local", "fp": h, "token": "t"},  # duplicado
                    {"id": "nope", "name": "x", "url": "y.local", "fp": h, "token": "t"}):
            try:
                save_server(bad, sp)
                raise AssertionError(bad)
            except ValueError:
                pass
        assert delete_server(s["id"], sp) and list_servers(sp) == []

        # Huella local: primer certificado del PEM
        import base64
        pem = Path(d) / "cert.pem"
        pem.write_text("-----BEGIN CERTIFICATE-----\n" + base64.b64encode(der).decode()
                       + "\n-----END CERTIFICATE-----\n-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
        assert local_fingerprint(pem) == h and local_fingerprint(Path(d) / "no.pem") is None

        # Archivo corrupto → lista vacía, sin excepción
        tp.write_text("{no es json")
        assert list_tokens(tp) == [] and verify_token(tok, None, tp) is None
        # Entradas que no son objetos → se ignoran (antes: AttributeError → 500 en la API remota)
        tp.write_text(json.dumps({"tokens": ["x", 1, None, {"id": "a", "hash": "é"}]}))
        assert [t["id"] for t in list_tokens(tp)] == ["a"] and verify_token(tok, None, tp) is None
        # Un temporal huérfano (o un symlink en su lugar) no impide guardar
        (Path(d) / "t.json.tmp").write_text("basura")
        create_token("otro", tp)
        assert len(list_tokens(tp)) == 2

    # Limpieza de respuestas remotas
    c = clean_summary({"hostname": "<b>x</b>\x00", "cpu": float("nan"), "mem": 150, "disk": "42.5",
                       "load": [1, "x", 2, 3, 4], "failed": 2, "updates": None, "evil": "<script>",
                       "history": [{"t": 1, "cpu": 5, "mem": 6, "x": 1}, "basura"], "failed_units": ["a.service"]})
    assert c["hostname"] == "<b>x</b>" and c["cpu"] is None and c["mem"] == 100 and c["disk"] == 42.5
    assert c["load"] == [1.0, 2.0] and c["failed"] == 2 and c["updates"] is None and "evil" not in c
    assert c["history"] == [{"t": 1.0, "cpu": 5.0, "mem": 6.0}] and clean_summary([1]) is None
    json.dumps(c, allow_nan=False)
    print("ok")
