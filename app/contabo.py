"""Contabo: lógica pura (sin Flask) — credenciales, token y llamadas a la API del proveedor.

MatiPanel administra el servidor por dentro; la API de Contabo lo controla por fuera (encender,
apagar, snapshots). Referencia: https://api.contabo.com/

Archivo (root, 0600, escritura atómica):
  /var/lib/matipanel/contabo.json   las 4 credenciales de la API (secreto)

Seguridad:
- Solo se habla con dos hosts fijos (auth.contabo.com y api.contabo.com), con TLS verificado.
  Ninguna dirección viene del usuario.
- El token de acceso vive solo en memoria y se renueva antes de vencer.
- Los identificadores (instancia, snapshot, acción) se validan antes de formar la ruta.
"""
import hashlib
import http.client
import json
import os
import re
import ssl
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from . import remote_store as store

CONFIG_PATH = Path(os.environ.get("MATIPANEL_CONTABO", "/var/lib/matipanel/contabo.json"))
AUTH_HOST = "auth.contabo.com"
AUTH_PATH = "/auth/realms/contabo/protocol/openid-connect/token"
API_HOST = "api.contabo.com"
TIMEOUT = 20            # s por conexión y por lectura
MAX_BODY = 4 << 20      # 4 MB: una página de 100 instancias ocupa unos cientos de KB
PAGE_SIZE = 100
MAX_PAGES = 10          # hasta 1000 instancias o snapshots

FIELDS = ("client_id", "client_secret", "api_user", "api_password")
SECRET_FIELDS = ("client_secret", "api_password")
# Acción → nombre en la API. «shutdown» avisa al sistema (ACPI); «stop» corta la energía.
ACTIONS = {"start": "start", "restart": "restart", "shutdown": "shutdown", "stop": "stop"}
SNAP_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
SNAP_NAME_RE = re.compile(r"[A-Za-z0-9 -]{1,30}")  # límite de Contabo: letras, números, espacios y guiones

_lock = threading.Lock()
_tokens = {}  # huella de las credenciales → (token, vence)


class ApiError(Exception):
    """Fallo al hablar con Contabo. `status` es el código HTTP (0 = sin conexión)."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


# ── Credenciales ──

def load_creds(path=None):
    items = store._load(path or CONFIG_PATH, "accounts")
    c = items[0] if items else {}
    if not all(isinstance(c.get(k), str) and c.get(k) for k in FIELDS):
        return None
    return {k: c[k] for k in FIELDS}


def public_creds(path=None):
    """Lo que puede ver el navegador: nunca el secreto ni la contraseña."""
    c = load_creds(path)
    if not c:
        return {"configured": False, "client_id": "", "api_user": ""}
    return {"configured": True, "client_id": c["client_id"], "api_user": c["api_user"]}


def validate_creds(data, current=None):
    """Datos del formulario → credenciales. Un secreto vacío conserva el guardado."""
    data = data if isinstance(data, dict) else {}
    out = {}
    for k, label, maxlen in (("client_id", "Client ID", 128), ("client_secret", "Client Secret", 256),
                             ("api_user", "Usuario de la API", 254), ("api_password", "Contraseña de la API", 256)):
        v = data.get(k)
        v = v if isinstance(v, str) else ""
        if k not in SECRET_FIELDS:
            v = v.strip()
        if not v and k in SECRET_FIELDS and current:
            v = current[k]
        if not v:
            raise ValueError(f"Falta «{label}».")
        if len(v) > maxlen or any(not ch.isprintable() for ch in v):
            raise ValueError(f"«{label}» no es válido.")
        if k not in SECRET_FIELDS and any(ch.isspace() for ch in v):
            raise ValueError(f"«{label}» no puede llevar espacios.")
        out[k] = v
    return out


def save_creds(creds, path=None):
    store._save(path or CONFIG_PATH, "accounts", [{k: creds[k] for k in FIELDS}])
    forget_tokens()


def delete_creds(path=None):
    forget_tokens()
    try:
        os.unlink(path or CONFIG_PATH)
        return True
    except FileNotFoundError:
        return False


def forget_tokens():
    with _lock:
        _tokens.clear()


# ── Transporte ──

def _https(method, host, path, headers, body):
    """Una petición HTTPS → (código, cuerpo). No sigue redirecciones."""
    conn = http.client.HTTPSConnection(host, 443, timeout=TIMEOUT, context=ssl.create_default_context())
    try:
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        return r.status, r.read(MAX_BODY + 1)
    finally:
        conn.close()


transport = _https  # las pruebas lo reemplazan por uno simulado


def _send(method, host, path, headers, body=None):
    from . import __version__
    headers = {"Accept": "application/json", "User-Agent": f"MatiPanel/{__version__}", **headers}
    try:
        status, data = transport(method, host, path, headers, body)
    except ssl.SSLError as e:
        raise ApiError(f"Error TLS al conectar con Contabo: {e.reason or e}")
    except (OSError, http.client.HTTPException) as e:
        raise ApiError(f"No se pudo conectar con Contabo ({host}): {e}")
    if len(data) > MAX_BODY:
        raise ApiError("Contabo devolvió una respuesta demasiado grande.", status)
    return status, data


def _json(data):
    try:
        out = json.loads(data) if data else {}
    except (ValueError, RecursionError):
        return {}
    return out if isinstance(out, dict) else {}


def _text(v, maxlen=200):
    s = "".join(ch for ch in str(v if v is not None else "") if ch.isprintable()).strip()
    return s[:maxlen]


def _api_message(status, data):
    """Mensaje para el usuario a partir de un error de la API."""
    known = {
        400: "Contabo rechazó la petición",
        401: "Contabo no aceptó el acceso",
        402: "Esta acción requiere un servicio de pago adicional en Contabo",
        403: "El usuario de la API no tiene permiso para esto en Contabo",
        404: "Contabo no encontró ese recurso",
        409: "Contabo no puede hacerlo ahora: hay otra operación en curso o el estado no lo permite",
        429: "Contabo limitó las peticiones: espera un momento y vuelve a intentar",
    }
    base = known.get(status) or f"Contabo respondió con un error (HTTP {status})"
    d = _json(data)
    detail = d.get("message") or d.get("error_description") or d.get("error") or ""
    if isinstance(detail, list):
        detail = "; ".join(str(x) for x in detail)
    detail = _text(detail, 240)
    return f"{base}. Detalle: {detail}" if detail else base + "."


# ── Token ──

def _creds_key(creds):
    return hashlib.sha256("\0".join(creds[k] for k in FIELDS).encode()).hexdigest()


def token(creds, fresh=False):
    """Token de acceso (OAuth2, grant «password»). Se guarda en memoria hasta poco antes de vencer."""
    key = _creds_key(creds)
    with _lock:
        hit = _tokens.get(key)
        if hit and not fresh and hit[1] > time.time():
            return hit[0]
    body = urlencode({"client_id": creds["client_id"], "client_secret": creds["client_secret"],
                      "username": creds["api_user"], "password": creds["api_password"], "grant_type": "password"})
    status, data = _send("POST", AUTH_HOST, AUTH_PATH, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    d = _json(data)
    tok = d.get("access_token")
    if status in (400, 401, 403):
        detail = _text(d.get("error_description") or d.get("error"), 160)
        raise ApiError("Contabo rechazó las credenciales. Revisa los cuatro datos en my.contabo.com → API."
                       + (f" Detalle: {detail}" if detail else ""), status)
    if status != 200 or not isinstance(tok, str) or not tok:
        raise ApiError(_api_message(status, data) if status != 200
                       else "Contabo no devolvió un token de acceso.", status)
    try:
        ttl = float(d.get("expires_in") or 300)
    except (TypeError, ValueError):
        ttl = 300.0
    with _lock:
        _tokens[key] = (tok, time.time() + max(30.0, min(ttl, 3600.0) - 30.0))
    return tok


# ── Llamadas ──

def request(creds, method, path, query=None, body=None):
    """Llama a la API y devuelve el JSON (dict). Lanza ApiError con un mensaje para el usuario."""
    if query:
        path += "?" + urlencode(query)
    payload = json.dumps(body) if body is not None else None
    for attempt in (0, 1):
        headers = {"Authorization": "Bearer " + token(creds, fresh=attempt == 1),
                   "x-request-id": str(uuid.uuid4())}  # obligatorio en toda la API (UUID v4)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        status, data = _send(method, API_HOST, path, headers, payload)
        if status == 401 and attempt == 0:
            continue  # token vencido o revocado antes de tiempo: se pide otro una vez
        if 200 <= status < 300:
            return _json(data)
        raise ApiError(_api_message(status, data), status)


def _pages(creds, path):
    items = []
    for page in range(1, MAX_PAGES + 1):
        d = request(creds, "GET", path, {"page": page, "size": PAGE_SIZE})
        chunk = d.get("data")
        chunk = [x for x in chunk if isinstance(x, dict)] if isinstance(chunk, list) else []
        items += chunk
        meta = d.get("_pagination") if isinstance(d.get("_pagination"), dict) else {}
        try:
            total = int(meta.get("totalPages") or 1)
        except (TypeError, ValueError):
            total = 1
        if page >= total or not chunk:
            break
    return items


def _iid(instance_id):
    try:
        n = int(instance_id)
    except (TypeError, ValueError):
        raise ValueError("Instancia inválida.")
    if not 0 < n < 10 ** 15:
        raise ValueError("Instancia inválida.")
    return n


def _sid(snapshot_id):
    s = str(snapshot_id or "")
    if not SNAP_ID_RE.fullmatch(s):
        raise ValueError("Snapshot inválido.")
    return s


def list_instances(creds):
    return [clean_instance(x) for x in _pages(creds, "/v1/compute/instances")]


def instance_action(creds, instance_id, action):
    if action not in ACTIONS:
        raise ValueError("Acción inválida.")
    return request(creds, "POST", f"/v1/compute/instances/{_iid(instance_id)}/actions/{ACTIONS[action]}")


def list_snapshots(creds, instance_id):
    items = [clean_snapshot(x) for x in _pages(creds, f"/v1/compute/instances/{_iid(instance_id)}/snapshots")]
    return sorted(items, key=lambda s: s["created"], reverse=True)


def create_snapshot(creds, instance_id, name, description=""):
    name = " ".join(str(name or "").split())
    if not SNAP_NAME_RE.fullmatch(name):
        raise ValueError("El nombre lleva de 1 a 30 caracteres: letras sin acento, números, espacios y guiones.")
    body = {"name": name}
    description = _text(description, 255)
    if description:
        body["description"] = description
    d = request(creds, "POST", f"/v1/compute/instances/{_iid(instance_id)}/snapshots", body=body)
    data = d.get("data")
    return clean_snapshot(data[0]) if isinstance(data, list) and data and isinstance(data[0], dict) else None


def delete_snapshot(creds, instance_id, snapshot_id):
    request(creds, "DELETE", f"/v1/compute/instances/{_iid(instance_id)}/snapshots/{_sid(snapshot_id)}")


def rollback_snapshot(creds, instance_id, snapshot_id):
    request(creds, "POST", f"/v1/compute/instances/{_iid(instance_id)}/snapshots/{_sid(snapshot_id)}/rollback",
            body={})


# ── Datos para la pantalla (solo lo necesario, saneado) ──

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def clean_instance(d):
    ipc = d.get("ipConfig") if isinstance(d.get("ipConfig"), dict) else {}
    v4 = ipc.get("v4") if isinstance(ipc.get("v4"), dict) else {}
    v6 = ipc.get("v6") if isinstance(ipc.get("v6"), dict) else {}
    vmi = _text(d.get("name"), 80)
    return {
        "id": _int(d.get("instanceId")),
        "name": _text(d.get("displayName"), 80) or vmi or str(_int(d.get("instanceId"))),
        "vmi": vmi,
        "status": _text(d.get("status"), 40) or "unknown",
        "region": _text(d.get("regionName"), 60) or _text(d.get("region"), 60),
        "ip": _text(v4.get("ip"), 45),
        "ip6": _text(v6.get("ip"), 45),
        "cpu": _int(d.get("cpuCores")),
        "ram_mb": _int(d.get("ramMb")),
        "disk_mb": _int(d.get("diskMb")),
        "product": _text(d.get("productName"), 60) or _text(d.get("productId"), 40),
        "os": _text(d.get("osType"), 40),
        "user": _text(d.get("defaultUser"), 40),
        "created": _text(d.get("createdDate"), 40),
        "cancel": _text(d.get("cancelDate"), 40),
        "error": _text(d.get("errorMessage"), 200),
    }


def clean_snapshot(d):
    return {
        "id": _text(d.get("snapshotId"), 64),
        "name": _text(d.get("name"), 255),
        "description": _text(d.get("description"), 255),
        "created": _text(d.get("createdDate"), 40),
        "auto_delete": _text(d.get("autoDeleteDate"), 40),
    }


if __name__ == "__main__":
    import tempfile
    calls = []

    def fake(method, host, path, headers, body):
        calls.append((method, host, path, headers, body))
        if host == AUTH_HOST:
            ok = "password=bien" in body
            return (200, b'{"access_token": "T1", "expires_in": 300}') if ok else (401, b'{"error_description": "Invalid user credentials"}')
        if path.startswith("/v1/compute/instances?"):
            return 200, json.dumps({"_pagination": {"totalPages": 1}, "data": [
                {"instanceId": 100, "displayName": "", "name": "vmi100", "status": "running", "regionName": "EU 1",
                 "ipConfig": {"v4": {"ip": "203.0.113.7"}, "v6": {"ip": "2001:db8::1"}}, "cpuCores": 4,
                 "ramMb": 8192, "diskMb": 204800, "productId": "V45"}, "basura"]}).encode()
        if path.endswith("/actions/restart"):
            return 201, b'{"data": [{"instanceId": 100, "action": "restart"}]}'
        return 404, b'{"message": "Not Found"}'

    transport = fake
    p = Path(tempfile.mkdtemp()) / "contabo.json"
    assert load_creds(p) is None and public_creds(p) == {"configured": False, "client_id": "", "api_user": ""}
    good = validate_creds({"client_id": " INT-1 ", "client_secret": "s3", "api_user": "a@b.gt", "api_password": "bien"})
    assert good["client_id"] == "INT-1"
    for bad in ({}, {**good, "client_id": "a b"}, {**good, "api_password": ""}, {**good, "client_secret": "x\ny"}):
        try:
            validate_creds(bad); raise AssertionError(bad)
        except ValueError:
            pass
    assert validate_creds({"client_id": "INT-2", "api_user": "a@b.gt"}, good)["client_secret"] == "s3"  # vacío = conservar
    save_creds(good, p)
    assert load_creds(p) == good and "s3" not in json.dumps(public_creds(p))
    if os.name == "posix":
        assert os.stat(p).st_mode & 0o777 == 0o600
    try:
        token({**good, "api_password": "mal"}); raise AssertionError
    except ApiError as e:
        assert e.status == 401 and "credenciales" in str(e)
    inst = list_instances(good)
    assert len(inst) == 1 and inst[0]["name"] == "vmi100" and inst[0]["ip"] == "203.0.113.7" and inst[0]["ram_mb"] == 8192
    assert sum(1 for c in calls if c[1] == AUTH_HOST and "bien" in c[4]) == 1  # el token se reutiliza
    instance_action(good, "100", "restart")
    m, host, path, headers, _ = calls[-1]
    assert (m, host, path) == ("POST", API_HOST, "/v1/compute/instances/100/actions/restart")
    assert headers["Authorization"] == "Bearer T1" and uuid.UUID(headers["x-request-id"]).version == 4
    n = len(calls)
    for fn in (lambda: instance_action(good, 100, "rescue"), lambda: instance_action(good, "1/../2", "start"),
               lambda: delete_snapshot(good, 100, "../x"), lambda: create_snapshot(good, 100, "ñ" * 3),
               lambda: create_snapshot(good, 100, "a" * 31)):
        try:
            fn(); raise AssertionError
        except ValueError:
            pass
    assert len(calls) == n  # lo inválido nunca sale del servidor
    try:
        list_snapshots(good, 5); raise AssertionError
    except ApiError as e:
        assert e.status == 404 and "Not Found" in str(e)
    assert delete_creds(p) and not delete_creds(p) and not _tokens
    print("ok")
