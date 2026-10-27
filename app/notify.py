"""Notificaciones por correo (SMTP).

event(tipo, asunto, cuerpo, clave) encola un correo si las notificaciones están activas y ese tipo
de evento está marcado. Un hilo envía la cola (el panel nunca espera al servidor de correo) y otro
revisa la salud del servidor cada minuto: avisa cuando aparece un problema, opcionalmente lo
recuerda, y avisa cuando se resuelve.

No depende de Flask: lo usan también el motor de despliegues y los respaldos de apps.
ponytail: estado en memoria, válido con un solo worker; la configuración en un JSON 0600.
"""
import json
import os
import queue
import re
import smtplib
import socket
import ssl
import threading
import time
from collections import deque
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("MATIPANEL_NOTIFY", "/var/lib/matipanel/notify.json"))
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
CHECK_EVERY = 60
EVENTS = {  # clave: (etiqueta, activo por defecto)
    "health": ("Problemas del servidor (servicio caído, disco, memoria, CPU, red)", True),
    "resolved": ("Cuando un problema se resuelve", True),
    "deploy_fail": ("Despliegue fallido", True),
    "deploy_ok": ("Despliegue exitoso", False),
    "backup_fail": ("Respaldo de app fallido", True),
    "backup_ok": ("Respaldo de app correcto", False),
    "login_block": ("Bloqueo por intentos fallidos de inicio de sesión", True),
    "monitor": ("Monitor: un servicio deja de responder o se recupera", True),
}
DEFAULTS = {"enabled": False, "panel_url": "",
            "smtp": {"host": "", "port": 587, "security": "starttls", "verify": True, "user": "", "password": "", "from": ""},
            "to": [], "events": {k: v[1] for k, v in EVENTS.items()}, "repeat_minutes": 0}

_q = queue.Queue(maxsize=200)
_sent = deque(maxlen=50)        # historial de envíos para la pantalla
_last = {}                      # clave → último envío (anti-repetición)
_active = {}                    # problemas de salud abiertos: clave → (mensaje, desde, último aviso)
_lock = threading.Lock()
_started = False


def load():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cfg = json.loads(json.dumps(DEFAULTS))
    if isinstance(data, dict):
        for k in ("enabled", "panel_url", "to", "repeat_minutes"):
            if k in data:
                cfg[k] = data[k]
        if isinstance(data.get("smtp"), dict):
            cfg["smtp"].update({k: v for k, v in data["smtp"].items() if k in cfg["smtp"]})
        if isinstance(data.get("events"), dict):
            cfg["events"].update({k: bool(v) for k, v in data["events"].items() if k in EVENTS})
    return cfg


def save(cfg):
    CONFIG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def validate(data, current):
    """Datos del formulario → (config, error). Una contraseña vacía conserva la guardada."""
    cfg = json.loads(json.dumps(current))
    smtp = data.get("smtp") if isinstance(data.get("smtp"), dict) else {}
    host = str(smtp.get("host", "")).strip()
    if host and not HOST_RE.fullmatch(host):
        return None, "Servidor SMTP inválido"
    try:
        port = int(smtp.get("port") if smtp.get("port") not in (None, "") else 587)
    except (TypeError, ValueError):
        return None, "Puerto inválido"
    if not 1 <= port <= 65535:
        return None, "Puerto inválido"
    security = smtp.get("security", "starttls")
    if security not in ("starttls", "ssl", "none"):
        return None, "Seguridad inválida"
    sender = str(smtp.get("from", "")).strip()
    if sender and not EMAIL_RE.fullmatch(sender):
        return None, "Remitente inválido"
    to = data.get("to", [])
    to = re.split(r"[\s,;]+", to) if isinstance(to, str) else to
    to = [t.strip() for t in to if isinstance(t, str) and t.strip()]
    if len(to) > 10 or not all(EMAIL_RE.fullmatch(t) for t in to):
        return None, "Destinatarios inválidos (hasta 10 correos)"
    user = str(smtp.get("user", "")).strip()
    if len(user) > 254 or any(c in user for c in "\r\n"):
        return None, "Usuario SMTP inválido"
    password = smtp.get("password")
    if isinstance(password, str) and password:
        if len(password) > 512 or any(c in password for c in "\r\n"):
            return None, "Contraseña inválida"
        cfg["smtp"]["password"] = password
    if smtp.get("clear_password"):
        cfg["smtp"]["password"] = ""
    try:
        repeat = int(data.get("repeat_minutes") or 0)
    except (TypeError, ValueError):
        return None, "Intervalo de recordatorio inválido"
    if repeat not in (0, 30, 60, 120, 360, 1440):
        return None, "Intervalo de recordatorio inválido"
    events = data.get("events") if isinstance(data.get("events"), dict) else {}
    cfg.update(enabled=bool(data.get("enabled")), to=to, repeat_minutes=repeat,
               events={k: bool(events.get(k, cfg["events"].get(k))) for k in EVENTS})
    cfg["smtp"].update(host=host, port=port, security=security, verify=bool(smtp.get("verify", True)), user=user, **{"from": sender})
    if cfg["enabled"] and (not host or not sender or not to):
        return None, "Para activar las notificaciones completa servidor SMTP, remitente y al menos un destinatario"
    return cfg, ""


def public(cfg):
    """Configuración para el navegador: sin la contraseña."""
    out = json.loads(json.dumps(cfg))
    out["smtp"]["has_password"] = bool(out["smtp"].pop("password", ""))
    return out


def _hostname():
    return socket.gethostname()


def send_now(cfg, subject, body):
    """Envía un correo de inmediato (lanza excepción si falla). Lo usan la cola y el botón de prueba."""
    s = cfg["smtp"]
    msg = EmailMessage()
    msg["Subject"] = f"[MatiPanel · {_hostname()}] {subject}"
    msg["From"] = s["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=s["from"].split("@")[-1])
    footer = f"\n\n—\nServidor: {_hostname()}" + (f"\nPanel: {cfg['panel_url']}" if cfg.get("panel_url") else "")
    msg.set_content(body + footer)
    ctx = ssl.create_default_context()
    if not s.get("verify", True):  # servidores internos con certificado propio
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if s["security"] == "ssl":
        server = smtplib.SMTP_SSL(s["host"], s["port"], timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(s["host"], s["port"], timeout=20)
    try:
        server.ehlo()
        if s["security"] == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if s["user"]:
            server.login(s["user"], s["password"])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass


def _record(subject, ok, error=""):
    with _lock:
        _sent.appendleft({"t": time.time(), "subject": subject, "ok": ok, "error": str(error)[:300]})


def history():
    with _lock:
        return list(_sent)


def event(kind, subject, body, key=None, cooldown=600):
    """Encola un aviso de tipo `kind` si está activo. `key` evita repetir el mismo aviso antes de `cooldown` s."""
    cfg = load()
    if not cfg["enabled"] or not cfg["events"].get(kind):
        return False
    now = time.time()
    k = key or f"{kind}:{subject}"
    with _lock:
        if cooldown and now - _last.get(k, 0) < cooldown:
            return False
        _last[k] = now
    try:
        _q.put_nowait((subject, body))
    except queue.Full:
        _record(subject, False, "Cola de correos llena: se descartó")
        return False
    _ensure_started()
    return True


def _sender_loop():
    while True:
        subject, body = _q.get()
        try:
            send_now(load(), subject, body)
            _record(subject, True)
        except Exception as e:  # el hilo no debe morir
            _record(subject, False, e)


# ── Salud del servidor ───────────────────────────────────────────────────

def check_health(issues, now=None):
    """issues: [(nivel, mensaje, clave)] actuales → envía avisos de los nuevos y de los resueltos."""
    now = now or time.time()
    cfg = load()
    # Los servicios del Monitor mandan su propio correo (Caído / Recuperado): aquí no se repiten
    current = {key: (level, msg) for level, msg, key in issues if not key.startswith("monitor:")}
    repeat = cfg.get("repeat_minutes", 0) * 60
    for key, (level, msg) in current.items():
        prev = _active.get(key)
        if prev is None:
            _active[key] = [msg, now, now]
            event("health", ("Problema: " if level == "err" else "Aviso: ") + msg,
                  f"{msg}\n\nDetectado el {time.strftime('%d/%m/%Y a las %H:%M', time.localtime(now))}.",
                  key=f"health:{key}", cooldown=0)
        else:
            prev[0] = msg
            if repeat and now - prev[2] >= repeat:
                prev[2] = now
                event("health", "Sigue: " + msg,
                      f"{msg}\n\nActivo desde el {time.strftime('%d/%m/%Y a las %H:%M', time.localtime(prev[1]))}.",
                      key=f"health:{key}", cooldown=0)
    for key in [k for k in _active if k not in current]:
        msg, since, _ = _active.pop(key)
        mins = max(1, round((now - since) / 60))
        event("resolved", "Resuelto: " + msg, f"Se resolvió: {msg}\n\nDuró unos {mins} min.", key=f"resolved:{key}", cooldown=0)


def _health_loop():
    from .modules import dashboard
    time.sleep(90)  # dar tiempo al muestreador para tener datos de CPU y red
    while True:
        try:
            issues, _ = dashboard.compute_issues()
            check_health(issues)
        except Exception:
            pass
        time.sleep(CHECK_EVERY)


def _ensure_started():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_sender_loop, daemon=True, name="matipanel-mail").start()


def start(with_health=True):
    _ensure_started()
    if with_health:
        threading.Thread(target=_health_loop, daemon=True, name="matipanel-notify").start()


if __name__ == "__main__":
    import tempfile
    CONFIG_PATH = Path(tempfile.mkdtemp()) / "n.json"
    cfg, err = validate({"enabled": True, "smtp": {"host": "mail.empresa.local", "port": "465", "security": "ssl",
                                                    "from": "panel@empresa.local", "password": "s3cr"},
                         "to": "a@empresa.local, b@empresa.local", "repeat_minutes": 60, "events": {"deploy_ok": True}},
                        DEFAULTS)
    assert not err and cfg["to"] == ["a@empresa.local", "b@empresa.local"] and cfg["smtp"]["password"] == "s3cr"
    assert cfg["events"]["deploy_ok"] and cfg["events"]["health"] and public(cfg)["smtp"] == {**{k: v for k, v in cfg["smtp"].items() if k != "password"}, "has_password": True}
    assert validate({"smtp": {"password": ""}}, cfg)[0]["smtp"]["password"] == "s3cr"  # vacío conserva
    for bad in ({"to": "no-es-correo"}, {"smtp": {"host": "a b"}}, {"smtp": {"port": 0}}, {"smtp": {"user": "a\r\nRCPT"}},
                {"enabled": True, "smtp": {"host": ""}}, {"repeat_minutes": 7}):
        assert validate(bad, DEFAULTS)[1], bad
    save(cfg)
    sent = []
    send_now = lambda c, s, b: sent.append(s)  # noqa: E731
    globals()["send_now"] = send_now
    _ensure_started()
    check_health([("err", "Disco / al 93 %", "disk:/")], now=1000)
    check_health([("err", "Disco / al 94 %", "disk:/")], now=1060)          # sigue: sin repetir antes de 60 min
    check_health([("err", "Disco / al 95 %", "disk:/")], now=1000 + 3700)  # recordatorio
    check_health([], now=1000 + 4000)                                        # resuelto
    time.sleep(0.5)
    assert sent == ["Problema: Disco / al 93 %", "Sigue: Disco / al 95 %", "Resuelto: Disco / al 95 %"], sent
    assert event("deploy_ok", "Despliegue x", "ok", key="d1") and not event("deploy_ok", "Despliegue x", "ok", key="d1")
    print("ok")
