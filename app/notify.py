"""Notificaciones por correo (SMTP), en HTML y texto.

event(tipo, asunto, cuerpo, clave, ...) encola un correo si las notificaciones están activas y ese tipo
de evento está marcado. Un hilo envía la cola (el panel nunca espera al servidor de correo) y otro
revisa la salud del servidor cada minuto: avisa cuando aparece un problema, opcionalmente lo
recuerda, y avisa cuando se resuelve. Las demás revisiones periódicas (reinicio, certificados, SSH,
resumen diario) están en notify_checks.py.

Cada correo lleva una parte de texto y otra HTML con estilos en línea y tablas (lo único que respetan
Outlook y Gmail): franja de color según el nivel, título, detalles en tabla, bloque de registro y botón
para abrir el panel. Todo lo que viene de fuera se escapa.

No depende de Flask: lo usan también el motor de despliegues, los respaldos y el cron de respaldos.
ponytail: estado en memoria, válido con un solo worker; la configuración en un JSON 0600.
"""
import html
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
GROUPS = ("Servidor", "Seguridad", "Apps y respaldos", "Resumen")
EVENTS = {  # clave: (etiqueta, activo por defecto, grupo, nivel del correo)
    "health": ("Problemas del servidor (servicio caído, disco, memoria, CPU, red, contenedores)", True, "Servidor", "err"),
    "resolved": ("Cuando un problema se resuelve", True, "Servidor", "ok"),
    "reboot": ("El servidor se reinició", True, "Servidor", "warn"),
    "cert_expiry": ("Certificados SSL por vencer o vencidos", True, "Servidor", "warn"),
    "updates": ("Actualizaciones de paquetes disponibles (las de seguridad, marcadas)", True, "Servidor", "warn"),
    "panel_update": ("MatiPanel se actualizó a una versión nueva", True, "Servidor", "info"),
    "login_block": ("Bloqueo por intentos fallidos de inicio de sesión", True, "Seguridad", "err"),
    "login_new_ip": ("Inicio de sesión en el panel desde una IP nueva", True, "Seguridad", "warn"),
    "login_ok": ("Cada inicio de sesión en el panel", False, "Seguridad", "info"),
    "ssh_login": ("Inicios de sesión por SSH en el servidor", False, "Seguridad", "warn"),
    "panel_changes": ("Cambios delicados en el panel (usuarios, permisos, firewall, SSH, borrados, restauraciones, apagado)",
                      True, "Seguridad", "warn"),
    "tfa_change": ("Se activa o desactiva el doble factor (2FA) de un usuario", True, "Seguridad", "warn"),
    "deploy_fail": ("Despliegue fallido", True, "Apps y respaldos", "err"),
    "deploy_ok": ("Despliegue exitoso", False, "Apps y respaldos", "ok"),
    "backup_fail": ("Respaldo fallido (apps y carpetas programadas)", True, "Apps y respaldos", "err"),
    "backup_ok": ("Respaldo correcto (apps y carpetas programadas)", False, "Apps y respaldos", "ok"),
    "monitor": ("Monitor: un servicio deja de responder o se recupera", True, "Apps y respaldos", "err"),
    "daily_summary": ("Resumen diario del servidor (estado, recursos, respaldos, despliegues, seguridad)", False, "Resumen", "info"),
}
# nivel: (color fuerte, fondo suave, texto oscuro, icono, etiqueta)
LEVELS = {"err": ("#D92D20", "#FEE4E2", "#B42318", "🔴", "Problema"),
          "warn": ("#DC6803", "#FEF0C7", "#B54708", "🟠", "Aviso"),
          "ok": ("#079455", "#DCFAE6", "#067647", "✅", "Correcto"),
          "info": ("#0E7C6B", "#E2F1EC", "#08594D", "🔔", "Información")}
DEFAULTS = {"enabled": False, "panel_url": "",
            "smtp": {"host": "", "port": 587, "security": "starttls", "verify": True, "user": "", "password": "", "from": ""},
            "to": [], "events": {k: v[1] for k, v in EVENTS.items()}, "repeat_minutes": 0, "summary_hour": 7}

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
        for k in ("enabled", "panel_url", "to", "repeat_minutes", "summary_hour"):
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
    try:
        hour = int(data.get("summary_hour", cfg.get("summary_hour", 7)))
    except (TypeError, ValueError):
        return None, "Hora del resumen inválida"
    if not 0 <= hour <= 23:
        return None, "Hora del resumen inválida"
    events = data.get("events") if isinstance(data.get("events"), dict) else {}
    cfg.update(enabled=bool(data.get("enabled")), to=to, repeat_minutes=repeat, summary_hour=hour,
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


def fmt_time(t):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(t))


# ── Contenido del correo ─────────────────────────────────────────────────

def _url(cfg, link):
    base = str(cfg.get("panel_url") or "").rstrip("/")
    if not link or not base.startswith(("https://", "http://")):
        return ""
    return base + (link if link.startswith("/") else "/" + link)


def render_text(cfg, m, host, when):
    out = [m["body"].strip()] if m.get("body") else []
    for sec in m.get("sections") or []:
        lines = ([sec["title"].upper()] if sec.get("title") else []) + [f"  {r[0]}: {r[1]}" for r in sec.get("rows") or []]
        out.append("\n".join(lines))
    if m.get("pre"):
        out.append(m["pre"].rstrip())
    url = _url(cfg, m.get("link"))
    if url:
        out.append(f"Abrir en el panel: {url}")
    out.append(f"—\nServidor: {host} · {fmt_time(when)}" + (f"\nPanel: {cfg['panel_url']}" if cfg.get("panel_url") else ""))
    return "\n\n".join(out) + "\n"


SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"


def render_html(cfg, m, host, when):
    """Correo HTML compatible con clientes de correo: tablas, estilos en línea y todo escapado."""
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    color, soft, dark, icon, label = LEVELS.get(m.get("level") or "info", LEVELS["info"])
    paras = "".join(f'<p style="margin:0 0 12px;">{e(p).replace(chr(10), "<br>")}</p>'
                    for p in (m.get("body") or "").strip().split("\n\n") if p.strip())
    secs = ""
    for sec in m.get("sections") or []:
        rows = ""
        for r in sec.get("rows") or []:
            tone = LEVELS.get(r[2])[2] if len(r) > 2 and r[2] in LEVELS else "#1D2939"
            rows += (f'<tr><td style="padding:8px 12px 8px 0;border-bottom:1px solid #EEF2F6;color:#667085;font-size:13px;'
                     f'vertical-align:top;width:38%;">{e(r[0])}</td>'
                     f'<td style="padding:8px 0;border-bottom:1px solid #EEF2F6;color:{tone};font-size:14px;font-weight:600;'
                     f'word-break:break-word;">{e(r[1])}</td></tr>')
        title = (f'<div style="font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#98A2B3;font-weight:700;'
                 f'margin:6px 0 4px;">{e(sec["title"])}</div>') if sec.get("title") else ""
        secs += (f'<tr><td style="padding:6px 32px 10px;">{title}'
                 f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
                 f'{rows}</table></td></tr>')
    pre = (f'<tr><td style="padding:6px 32px 12px;"><pre style="margin:0;background:#0F1623;color:#C9E7B0;font:12px/1.55 {MONO};'
           f'padding:14px 16px;border-radius:10px;white-space:pre-wrap;word-break:break-all;">{e(m["pre"].rstrip())}</pre></td></tr>'
           if m.get("pre") else "")
    url = _url(cfg, m.get("link"))
    button = (f'<tr><td style="padding:10px 32px 28px;"><a href="{e(url)}" style="display:inline-block;background:{color};'
              f'color:#ffffff;font:600 14px/1 {SANS};padding:12px 20px;border-radius:10px;text-decoration:none;">'
              f'{e(m.get("link_text") or "Abrir en MatiPanel")} &rarr;</a></td></tr>') if url else \
        '<tr><td style="padding:0 0 18px;font-size:0;line-height:0;">&nbsp;</td></tr>'
    settings = _url(cfg, "/notificaciones/")
    manage = f' · <a href="{e(settings)}" style="color:#667085;">Cambiar qué se avisa</a>' if settings else ""
    preheader = (m.get("body") or m["subject"]).strip().split("\n")[0][:140]
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{e(m["subject"])}</title></head>
<body style="margin:0;padding:0;background:#F1F5F9;-webkit-text-size-adjust:100%;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F1F5F9;">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;font-family:{SANS};">
  <tr><td style="padding:0 4px 14px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="40" style="width:40px;"><div style="width:34px;height:34px;line-height:34px;border-radius:9px;background:#0E7C6B;
        color:#ffffff;font:700 17px/34px {SANS};text-align:center;">M</div></td>
      <td style="font:700 16px/1.2 {SANS};color:#1D2939;">MatiPanel<div style="font:12px/1.4 {MONO};color:#98A2B3;font-weight:400;">{e(host)}</div></td>
      <td align="right" style="font:12px/1.4 {SANS};color:#98A2B3;">{e(fmt_time(when))}</td>
    </tr></table>
  </td></tr>
  <tr><td style="background:#ffffff;border:1px solid #E4E7EC;border-radius:16px;overflow:hidden;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr><td style="height:6px;background:{color};font-size:0;line-height:0;">&nbsp;</td></tr>
      <tr><td style="padding:26px 32px 6px;">
        <span style="display:inline-block;padding:4px 11px;border-radius:999px;background:{soft};color:{dark};font:600 12px/1.4 {SANS};">{icon} {e(m.get("badge") or label)}</span>
        <h1 style="margin:14px 0 0;font:700 21px/1.35 {SANS};color:#1D2939;">{e(m["subject"])}</h1>
      </td></tr>
      <tr><td style="padding:14px 32px 2px;font:15px/1.6 {SANS};color:#344054;">{paras}</td></tr>
      {secs}{pre}{button}
    </table>
  </td></tr>
  <tr><td style="padding:18px 8px 6px;text-align:center;font:12px/1.7 {SANS};color:#98A2B3;">
    Enviado por MatiPanel desde <b style="color:#667085;">{e(host)}</b>{manage}<br>Hecho con 💙 para Matías
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def build(cfg, m, host=None, when=None):
    """Mensaje MIME listo para enviar: texto + HTML."""
    host, when = host or _hostname(), when or time.time()
    s = cfg["smtp"]
    icon = LEVELS.get(m.get("level") or "info", LEVELS["info"])[3]
    msg = EmailMessage()
    msg["Subject"] = f"{icon} [MatiPanel · {host}] {m['subject']}"
    msg["From"] = s["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Date"] = formatdate(when, localtime=True)
    msg["Message-ID"] = make_msgid(domain=s["from"].split("@")[-1] or "matipanel.local")
    msg.set_content(render_text(cfg, m, host, when))
    msg.add_alternative(render_html(cfg, m, host, when), subtype="html")
    return msg


def send_now(cfg, subject, body, **extra):
    """Envía un correo de inmediato (lanza excepción si falla). Lo usan la cola, el cron y el botón de prueba.
    extra: level, sections=[{title, rows: [(etiqueta, valor[, nivel])]}], pre (registro), link (ruta del panel), badge."""
    s = cfg["smtp"]
    msg = build(cfg, {"subject": subject, "body": body, **extra})
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


def _record(subject, ok, error="", kind=""):
    with _lock:
        _sent.appendleft({"t": time.time(), "subject": subject, "ok": ok, "error": str(error)[:300], "kind": kind})


def history():
    with _lock:
        return list(_sent)


def event(kind, subject, body="", key=None, cooldown=600, level=None, sections=None, pre=None, link=None,
          badge=None, sync=False):
    """Encola un aviso de tipo `kind` si está activo. `key` evita repetir el mismo aviso antes de `cooldown` s.
    sync=True lo envía en el momento (procesos cortos como el cron de respaldos, que no esperan a la cola)."""
    cfg = load()
    if not cfg["enabled"] or not cfg["events"].get(kind):
        return False
    now = time.time()
    k = key or f"{kind}:{subject}"
    with _lock:
        if cooldown and now - _last.get(k, 0) < cooldown:
            return False
        _last[k] = now
    extra = {"level": level or EVENTS.get(kind, ("", 0, "", "info"))[3], "sections": sections, "pre": pre, "link": link,
             "badge": badge}
    extra = {k2: v for k2, v in extra.items() if v}
    if sync:
        try:
            send_now(cfg, subject, body, **extra)
            _record(subject, True, kind=kind)
            return True
        except Exception as e:
            _record(subject, False, e, kind=kind)
            return False
    try:
        _q.put_nowait((kind, subject, body, extra))
    except queue.Full:
        _record(subject, False, "Cola de correos llena: se descartó", kind=kind)
        return False
    _ensure_started()
    return True


def _sender_loop():
    while True:
        kind, subject, body, extra = _q.get()
        try:
            send_now(load(), subject, body, **extra)
            _record(subject, True, kind=kind)
        except Exception as e:  # el hilo no debe morir
            _record(subject, False, e, kind=kind)


# ── Vista previa y ejemplos ──────────────────────────────────────────────

SAMPLES = {
    "health": ("Problema: Disco / al 93 %", "El disco / está al 93 % (440 GB de 473 GB).\n\nSi se llena, las bases de datos y los servicios dejan de escribir.",
               [{"title": "Detalle", "rows": [("Problema", "Disco / al 93 %", "err"), ("Detectado", "27/10/2026 03:12")]}], None, "/"),
    "resolved": ("Resuelto: El servicio nginx falló", "Se resolvió: El servicio nginx falló.",
                 [{"rows": [("Duró", "unos 4 min", "ok"), ("Resuelto", "27/10/2026 03:16")]}], None, "/"),
    "reboot": ("El servidor se reinició", "El servidor arrancó de nuevo y el panel volvió a funcionar.",
               [{"rows": [("Arranque", "27/10/2026 04:01", "warn"), ("Encendido antes", "12 d 6 h"), ("Kernel", "6.12.48-amd64")]}], None, "/system/"),
    "cert_expiry": ("El certificado de app.empresa.gob.gt vence en 9 días", "Let's Encrypt no lo renovó: revisa que el dominio apunte a este servidor y el puerto 80 esté abierto.",
                    [{"rows": [("Certificado", "app.empresa.gob.gt"), ("Vence", "05/11/2026 (9 días)", "warn")]}], None, "/ssl/"),
    "updates": ("3 actualizaciones (1 de seguridad)", "Hay versiones nuevas de paquetes del sistema.",
                [{"title": "Seguridad", "rows": [("openssl", "3.5.1-1 → 3.5.1-2", "err")]},
                 {"title": "Otras", "rows": [("curl", "8.14.1-1 → 8.14.1-2"), ("tzdata", "2025b → 2025c")]}], None, "/packages/"),
    "panel_update": ("MatiPanel se actualizó a la versión 0.9.0", "El panel se actualizó correctamente.",
                     [{"rows": [("Versión", "0.8.0 → 0.9.0", "ok"), ("Commit", "a1b2c3d")]}], None, "/acerca"),
    "login_block": ("Bloqueado el acceso desde 192.100.10.250", "La IP quedó bloqueada 5 minutos por contraseñas incorrectas.\n\nSi no fuiste tú, revisa la Auditoría y considera bloquear esa IP en el Firewall.",
                    [{"rows": [("IP", "192.100.10.250", "err"), ("Último usuario intentado", "admin")]}], None, "/auditoria/"),
    "login_new_ip": ("senatest entró al panel desde una IP nueva", "Es la primera vez que este usuario entra desde esta IP. Si no fuiste tú, cambia la contraseña y activa el doble factor.",
                     [{"rows": [("Usuario", "senatest"), ("IP", "192.100.10.77", "warn"), ("Navegador", "Firefox en Windows")]}], None, "/auditoria/"),
    "login_ok": ("senatest entró al panel", "", [{"rows": [("Usuario", "senatest"), ("IP", "192.100.10.189"), ("Navegador", "Chrome en Windows")]}], None, "/auditoria/"),
    "ssh_login": ("Inicio de sesión SSH: senatest desde 192.100.10.189", "",
                  [{"rows": [("Usuario", "senatest"), ("Desde", "192.100.10.189", "warn"), ("Método", "clave pública"), ("Hora", "27/10/2026 09:41")]}], None, "/system/"),
    "panel_changes": ("senatest eliminó la aplicación «quetzal»", "Se hizo un cambio delicado en el panel.",
                      [{"rows": [("Quién", "senatest"), ("Desde", "192.100.10.189"), ("Módulo", "Despliegues"), ("Acción", "eliminó la aplicación"), ("Objetivo", "quetzal", "warn")]}], None, "/auditoria/"),
    "tfa_change": ("Se desactivó el doble factor de senatest", "La cuenta queda protegida solo con su contraseña.",
                   [{"rows": [("Usuario", "senatest", "warn"), ("Desde", "192.100.10.189")]}], None, "/security"),
    "deploy_fail": ("Falló el despliegue de «quetzal»", "Las migraciones fallaron. La versión anterior sigue en línea.",
                    [{"rows": [("App", "quetzal"), ("Commit", "af26625 — Arreglo de facturas"), ("Disparado por", "webhook")]}],
                    "SQLSTATE[42S01]: Base table or view already exists: 1050 Table 'facturas' already exists", "/despliegues/app/quetzal"),
    "deploy_ok": ("«quetzal» desplegada", "La versión nueva ya está en línea.",
                  [{"rows": [("App", "quetzal"), ("Commit", "af26625 — Arreglo de facturas", "ok"), ("Duración", "2 min 14 s")]}], None, "/despliegues/app/quetzal"),
    "backup_fail": ("Falló el respaldo de «quetzal»", "Se reintentará en una hora.",
                    [{"rows": [("App", "quetzal"), ("Servidor", "Servidor Windows")]}],
                    "No se pudo conectar a la carpeta compartida: mount error(13): Permission denied", "/respaldos-apps/"),
    "backup_ok": ("Respaldo de «quetzal» correcto", "",
                  [{"rows": [("Respaldo", "20261027-020000"), ("Tamaño", "48.2 MB", "ok"), ("Duración", "21 s"), ("Destino", "Servidor Windows")]}], None, "/respaldos-apps/"),
    "monitor": ("Caído: «Intranet» no responde", "«Intranet» dejó de responder tras 3 fallos seguidos.",
                [{"rows": [("Comprobación", "HTTP https://intranet.empresa.gob.gt"), ("Error", "Tiempo de espera agotado", "err"), ("Primer fallo", "27/10/2026 10:02")]}], None, "/monitor/"),
}


def sample(kind):
    """Correo de ejemplo de un tipo de aviso (vista previa). El resumen diario se arma con datos reales."""
    if kind == "daily_summary":
        from . import notify_checks
        return notify_checks.build_summary()
    subject, body, sections, pre, link = SAMPLES.get(kind, SAMPLES["health"])
    level = "ok" if subject.startswith(("Resuelto", "Respaldo de")) else EVENTS.get(kind, ("", 0, "", "info"))[3]
    return {"subject": subject, "body": body, "sections": sections, "pre": pre, "link": link, "level": level}


def preview(cfg, kind):
    return render_html(cfg, sample(kind), _hostname(), time.time())


# ── Salud del servidor ───────────────────────────────────────────────────

def check_health(issues, now=None):
    """issues: [(nivel, mensaje, clave)] actuales → envía avisos de los nuevos y de los resueltos."""
    now = now or time.time()
    cfg = load()
    # El Monitor (Caído / Recuperado) y las actualizaciones (lista de paquetes) mandan su propio correo
    current = {key: (level, msg) for level, msg, key in issues if not key.startswith(("monitor:", "updates:"))}
    repeat = cfg.get("repeat_minutes", 0) * 60
    others = lambda key: [(m, lv) for k2, (lv, m) in current.items() if k2 != key]  # noqa: E731

    def rows(key, extra):
        r = [*extra]
        if others(key):
            r.append(("Otros problemas activos", " · ".join(m for m, _ in others(key))))
        return [{"rows": r}]
    for key, (level, msg) in current.items():
        prev = _active.get(key)
        if prev is None:
            _active[key] = [msg, now, now]
            event("health", ("Problema: " if level == "err" else "Aviso: ") + msg,
                  f"{msg}.\n\nSe revisa cada minuto: te avisaremos cuando se resuelva.",
                  key=f"health:{key}", cooldown=0, level=level, link="/",
                  sections=rows(key, [("Problema", msg, level), ("Detectado", fmt_time(now))]))
        else:
            prev[0] = msg
            if repeat and now - prev[2] >= repeat:
                prev[2] = now
                event("health", "Sigue: " + msg, f"{msg}: el problema sigue activo.",
                      key=f"health:{key}", cooldown=0, level=level, link="/",
                      sections=rows(key, [("Problema", msg, level), ("Activo desde", fmt_time(prev[1]))]))
    for key in [k for k in _active if k not in current]:
        msg, since, _ = _active.pop(key)
        mins = max(1, round((now - since) / 60))
        event("resolved", "Resuelto: " + msg, f"Se resolvió: {msg}.", key=f"resolved:{key}", cooldown=0, link="/",
              sections=[{"rows": [("Duró", f"unos {mins} min", "ok"), ("Desde", fmt_time(since)), ("Resuelto", fmt_time(now))]}])


def _health_loop():
    from .modules import dashboard
    from . import notify_checks
    time.sleep(90)  # dar tiempo al muestreador para tener datos de CPU y red
    while True:
        try:
            issues, _ = dashboard.compute_issues()
            check_health(issues)
        except Exception:
            pass
        try:
            from . import updates
            updates.tick()  # cada hora: paquetes con versión nueva → correo
        except Exception:
            pass
        notify_checks.tick()  # reinicio, certificados, SSH, resumen diario (cada una se protege sola)
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
                {"enabled": True, "smtp": {"host": ""}}, {"repeat_minutes": 7}, {"summary_hour": 24}):
        assert validate(bad, DEFAULTS)[1], bad
    # HTML: todo escapado, nivel y botón con la URL del panel
    cfg["panel_url"] = "https://192.100.10.64:8443"
    page = render_html(cfg, {"subject": "<script>x</script>", "body": "a & b", "level": "err", "link": "/monitor/",
                             "sections": [{"title": "T", "rows": [("<b>", "\"v\"", "err")]}], "pre": "<log>"}, "srv", 0)
    assert "<script>x" not in page and "&lt;script&gt;" in page and "&lt;log&gt;" in page and "&quot;v&quot;" in page
    assert 'href="https://192.100.10.64:8443/monitor/"' in page and LEVELS["err"][0] in page
    assert "javascript:" not in render_html({**cfg, "panel_url": "javascript:alert(1)"}, {"subject": "s", "link": "/x"}, "h", 0)
    msg = build(cfg, {"subject": "Prueba", "body": "Hola", "level": "ok"}, "srv", 0)
    assert msg["Subject"].startswith("✅ [MatiPanel · srv] Prueba") and msg.is_multipart()
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]
    assert all(k in SAMPLES or k == "daily_summary" for k in EVENTS)
    save(cfg)
    sent = []
    globals()["send_now"] = lambda c, s, b, **kw: sent.append((s, kw.get("level")))
    _ensure_started()
    check_health([("err", "Disco / al 93 %", "disk:/")], now=1000)
    check_health([("err", "Disco / al 94 %", "disk:/")], now=1060)          # sigue: sin repetir antes de 60 min
    check_health([("err", "Disco / al 95 %", "disk:/")], now=1000 + 3700)  # recordatorio
    check_health([], now=1000 + 4000)                                        # resuelto
    time.sleep(0.5)
    assert sent == [("Problema: Disco / al 93 %", "err"), ("Sigue: Disco / al 95 %", "err"),
                    ("Resuelto: Disco / al 95 %", "ok")], sent
    assert event("deploy_ok", "Despliegue x", "ok", key="d1") and not event("deploy_ok", "Despliegue x", "ok", key="d1")
    print("ok")
