"""
Autenticación de dos factores (TOTP, compatible con Google Authenticator,
Authy, FreeOTP, etc.).

Los secretos y recovery codes se guardan en /var/lib/matipanel/2fa.json
con permisos 0600 del usuario matipanel.
"""
import os
import json
import secrets
import hashlib
import hmac
import time
from pathlib import Path

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False


STORE_PATH = Path(os.environ.get("MATIPANEL_2FA_STORE", "/var/lib/matipanel/2fa.json"))


class StoreError(Exception):
    """El almacén de 2FA existe pero no se puede leer: hay que fallar cerrado."""


def _load_store():
    # Solo un archivo inexistente significa "sin 2FA". Si existe pero está corrupto
    # o no se puede leer, se lanza StoreError: el login se bloquea y, como todos los
    # guardados cargan primero, el archivo nunca se sobrescribe con un almacén vacío.
    try:
        with open(STORE_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        raise StoreError(f"No se pudo leer la configuración de 2FA; revisa {STORE_PATH}")
    if not isinstance(data, dict):
        raise StoreError(f"No se pudo leer la configuración de 2FA; revisa {STORE_PATH}")
    return data


def _save_store(data):
    STORE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    # Creado ya con 0600 (los secretos nunca quedan legibles ni un instante) y sin seguir
    # un symlink que alguien haya dejado en el lugar del temporal.
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STORE_PATH)


def is_enabled(username):
    """¿El usuario tiene 2FA configurado y activo?"""
    data = _load_store().get(username, {})
    active = bool(data.get("secret") and data.get("active"))
    if active and not HAS_PYOTP:
        # Fallar cerrado: sin pyotp no se puede verificar el código, nunca saltarse el 2FA
        raise StoreError("pyotp no está instalado: no se puede verificar el 2FA "
                         "(reinstala con /opt/matipanel/venv/bin/pip install pyotp)")
    return active


def generate_secret(username):
    """Crea un nuevo secret pendiente de confirmación."""
    if not HAS_PYOTP:
        raise RuntimeError("pyotp no instalado")
    secret = pyotp.random_base32()
    codes = [secrets.token_hex(4) for _ in range(8)]
    store = _load_store()
    store[username] = {
        "secret": secret,
        "active": False,
        "recovery_hashes": [hashlib.sha256(c.encode()).hexdigest() for c in codes],
    }
    _save_store(store)
    return secret, codes


def pending_secret(username):
    """Secret aún no confirmado (para reintentar la activación con el mismo QR)."""
    data = _load_store().get(username, {})
    return None if data.get("active") else data.get("secret")


def valid_recovery_codes(username, codes):
    """Filtra los códigos que siguen vigentes para el usuario (descarta inventados)."""
    hashes = set(_load_store().get(username, {}).get("recovery_hashes", []))
    return [c for c in codes if hashlib.sha256(c.strip().lower().encode()).hexdigest() in hashes]


def recovery_remaining(username):
    """Cuántos códigos de recuperación sin usar le quedan al usuario."""
    return len(_load_store().get(username, {}).get("recovery_hashes", []))


def provisioning_uri(username, secret, issuer="MatiPanel"):
    """URI para QR code (otpauth://...)."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def _is_totp_code(code):
    return isinstance(code, str) and len(code) == 6 and code.isascii() and code.isdigit()


def verify(username, code):
    """Verifica un código TOTP de 6 dígitos (ventana ±1 paso).

    Cada código sirve UNA vez: se guarda el último paso de tiempo aceptado y se rechaza
    cualquier código de ese paso o anteriores (alguien que vio el código, o lo capturó,
    no puede reutilizarlo dentro de su ventana de validez)."""
    if not HAS_PYOTP or not _is_totp_code(code):
        return False
    store = _load_store()
    data = store.get(username)
    if not isinstance(data, dict) or not data.get("secret"):
        return False
    totp = pyotp.TOTP(data["secret"])
    now = int(time.time())
    last = data.get("last_step")
    last = last if isinstance(last, int) else -1
    for offset in (0, -1, 1):
        ts = now + offset * totp.interval
        step = ts // totp.interval
        if step > last and hmac.compare_digest(totp.at(ts), code):
            data["last_step"] = step
            store[username] = data
            _save_store(store)
            return True
    return False


def activate(username, code):
    """Confirma el secret con el primer código válido."""
    if pending_secret(username) and verify(username, code):
        store = _load_store()
        if username in store:
            store[username]["active"] = True
            _save_store(store)
            return True
    return False


def disable(username):
    store = _load_store()
    if username in store:
        del store[username]
        _save_store(store)


def consume_recovery(username, code):
    """Usa un código de recuperación (un solo uso)."""
    code = str(code).strip().lower()
    h = hashlib.sha256(code.encode()).hexdigest()
    store = _load_store()
    data = store.get(username, {})
    hashes = data.get("recovery_hashes", [])
    if h in hashes:
        hashes.remove(h)
        data["recovery_hashes"] = hashes
        store[username] = data
        _save_store(store)
        return True
    return False
