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
from pathlib import Path

try:
    import pyotp
    HAS_PYOTP = True
except ImportError:
    HAS_PYOTP = False


STORE_PATH = Path(os.environ.get("MATIPANEL_2FA_STORE", "/var/lib/matipanel/2fa.json"))


def _load_store():
    if not STORE_PATH.exists():
        return {}
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_store(data):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STORE_PATH)


def is_enabled(username):
    """¿El usuario tiene 2FA configurado y activo?"""
    if not HAS_PYOTP:
        return False
    data = _load_store().get(username, {})
    return bool(data.get("secret") and data.get("active"))


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


def provisioning_uri(username, secret, issuer="MatiPanel"):
    """URI para QR code (otpauth://...)."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify(username, code):
    """Verifica un código TOTP de 6 dígitos."""
    if not HAS_PYOTP:
        return False
    data = _load_store().get(username, {})
    secret = data.get("secret")
    if not secret:
        return False
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def activate(username, code):
    """Confirma el secret con el primer código válido."""
    if verify(username, code):
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
    code = code.strip().lower()
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
