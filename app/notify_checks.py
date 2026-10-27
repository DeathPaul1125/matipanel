"""Revisiones periódicas que avisan por correo (las llama el hilo de salud de notify.py cada minuto).

- Reinicio del servidor: cambia la hora de arranque.
- Certificados SSL (Let's Encrypt y el del panel) que vencen en 14 días o menos, cada 6 horas.
- Inicios de sesión SSH aceptados (journal de sshd), desde la última revisión.
- Resumen diario a la hora elegida (o al arrancar, si el panel estaba apagado a esa hora).
- Inicios de sesión en el panel y desde IPs nuevas (lo llama auth.py al entrar).

Cada revisión se protege sola: un fallo en una no impide las demás. El estado que debe sobrevivir a un
reinicio (hora de arranque, IPs conocidas por usuario, día del último resumen) va en notify_state.json.
"""
import glob
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import threading
import time
from pathlib import Path

import psutil

from . import notify

STATE_PATH = Path(os.environ.get("MATIPANEL_NOTIFY_STATE", str(notify.CONFIG_PATH.with_name("notify_state.json"))))
CERT_GLOBS = ("/etc/letsencrypt/live/*/cert.pem", "/etc/matipanel/cert.pem")
CERT_WARN_DAYS = 14
ACCEPTED_RE = re.compile(r"Accepted (\S+) for (\S+) from (\S+) port")
METHODS = {"publickey": "clave pública", "password": "contraseña", "keyboard-interactive/pam": "contraseña (PAM)"}

_lock = threading.RLock()
_mem = {"certs": 0.0, "ssh_since": None}


def _load_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_state(st):
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE_PATH)


def _wants(kind):
    cfg = notify.load()
    return cfg["enabled"] and cfg["events"].get(kind)


def duration(sec):
    sec = int(max(0, sec))
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return " ".join(p for p in (f"{d} d" if d else "", f"{h} h" if h else "", f"{m} min" if m and not d else "") if p) or "menos de 1 min"


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def browser(agent):
    """User-Agent → «Firefox en Windows» (aproximado, para el correo)."""
    a = agent or ""
    name = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                ("Safari/", "Safari"), ("curl/", "curl")) if k in a), "Navegador desconocido")
    osn = next((n for k, n in (("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                               ("Mac OS", "macOS"), ("Linux", "Linux")) if k in a), "")
    return f"{name} en {osn}" if osn else name


# ── Inicio de sesión en el panel ─────────────────────────────────────────

def login(username, ip, agent=""):
    """Al entrar al panel: aviso de IP nueva para ese usuario o, si no, del inicio de sesión (si están marcados)."""
    try:
        with _lock:
            st = _load_state()
            ips = st.setdefault("ips", {}).setdefault(username, [])
            known, first = ip in ips, not ips
            if not known:
                ips.append(ip)
                del ips[:-30]
                _save_state(st)
        rows = [("Usuario", username), ("IP", ip, "warn" if not known and not first else None), ("Navegador", browser(agent)),
                ("Hora", notify.fmt_time(time.time()))]
        if not known and not first and notify.event(
                "login_new_ip", f"{username} entró al panel desde una IP nueva",
                "Es la primera vez que este usuario entra desde esta IP. Si no fuiste tú, cambia la contraseña "
                "y activa el doble factor (2FA).", key=f"newip:{username}:{ip}", cooldown=0, link="/auditoria/",
                sections=[{"rows": rows}]):
            return
        notify.event("login_ok", f"{username} entró al panel", "", key=f"login:{username}:{ip}", cooldown=60,
                     link="/auditoria/", sections=[{"rows": rows}])
    except Exception:
        pass


# ── Reinicio ─────────────────────────────────────────────────────────────

def check_reboot(now=None):
    now = now or time.time()
    boot = psutil.boot_time()
    with _lock:
        st = _load_state()
        prev, seen = st.get("boot"), st.get("seen")
        changed = prev is None or abs(prev - boot) > 120
        if changed or now - (seen or 0) >= 600:
            st.update(boot=boot, seen=now)
            _save_state(st)
    if prev is None or not changed:
        return False
    rows = [("Arranque", notify.fmt_time(boot), "warn")]
    if seen:
        rows.append(("Último dato antes del reinicio", notify.fmt_time(seen)))
        rows.append(("Tiempo fuera de servicio", f"unos {duration(boot - seen)}" if boot > seen else "—"))
        rows.append(("Encendido antes", duration(seen - prev)))
    rows.append(("Kernel", platform.release()))
    return notify.event("reboot", "El servidor se reinició",
                        "El servidor arrancó de nuevo y el panel volvió a funcionar. Si no fue un reinicio planeado, "
                        "revisa el registro del arranque anterior: journalctl -b -1 -e", key=f"reboot:{int(boot)}",
                        cooldown=0, link="/system/", sections=[{"rows": rows}])


# ── Certificados ─────────────────────────────────────────────────────────

def cert_expiries():
    """[{name, path, expires}] de los certificados de Let's Encrypt y del panel."""
    out = []
    for path in sorted({p for g in CERT_GLOBS for p in glob.glob(g)}):
        try:
            r = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", path], capture_output=True, text=True, timeout=10)
            m = re.search(r"notAfter=(.+)", r.stdout)
            expires = ssl.cert_time_to_seconds(m.group(1).strip()) if m else None
        except (OSError, subprocess.SubprocessError, ValueError):
            expires = None
        if expires:
            panel = path.startswith("/etc/matipanel/")
            out.append({"name": "Panel MatiPanel (autofirmado)" if panel else Path(path).parent.name,
                        "path": path, "expires": expires, "panel": panel})
    return out


def check_certs(now=None, force=False):
    now = now or time.time()
    if not force and now - _mem["certs"] < 6 * 3600:
        return 0
    _mem["certs"] = now
    if not _wants("cert_expiry"):
        return 0
    sent = 0
    for c in cert_expiries():
        days = (c["expires"] - now) / 86400
        if days > CERT_WARN_DAYS:
            continue
        when = notify.fmt_time(c["expires"])
        subject = (f"El certificado de {c['name']} venció" if days < 0
                   else f"El certificado de {c['name']} vence en {max(0, int(days))} día{'s' if int(days) != 1 else ''}")
        body = ("Es el certificado del propio panel. Para generar otro, bórralo de /etc/matipanel/ y vuelve a ejecutar "
                "el instalador." if c["panel"] else
                "Let's Encrypt lo renueva solo 30 días antes de vencer, así que la renovación está fallando: revisa que el "
                "dominio apunte a este servidor y que el puerto 80 esté abierto, y pulsa «Renovar» en SSL.")
        sent += bool(notify.event("cert_expiry", subject, body, key=f"cert:{c['path']}", cooldown=24 * 3600,
                                  level="err" if days <= 3 else "warn", link="/ssl/",
                                  sections=[{"rows": [("Certificado", c["name"]), ("Archivo", c["path"]),
                                                      ("Vence", when, "err" if days <= 3 else "warn")]}]))
    return sent


# ── SSH ──────────────────────────────────────────────────────────────────

def ssh_logins(since):
    """Inicios de sesión SSH aceptados desde `since` (journal de sshd). → [{t, method, user, ip}]"""
    try:
        r = subprocess.run(["journalctl", "--no-pager", "-q", "-o", "short-unix", "--since", f"@{int(since)}",
                            "-g", "Accepted ", "_COMM=sshd", "_COMM=sshd-session"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in r.stdout.splitlines():
        m = ACCEPTED_RE.search(line)
        try:
            t = float(line.split(" ", 1)[0])
        except ValueError:
            continue
        if m:
            out.append({"t": t, "method": m[1], "user": m[2], "ip": m[3]})
    return out


def check_ssh(now=None):
    now = now or time.time()
    since = _mem["ssh_since"]
    if since is None or not _wants("ssh_login"):  # al arrancar no se avisan los anteriores
        _mem["ssh_since"] = now
        return 0
    items = [x for x in ssh_logins(since) if x["t"] > since]
    _mem["ssh_since"] = max([x["t"] for x in items] + [now - 2])
    if not items:
        return 0
    rows = [(notify.fmt_time(x["t"]), f"{x['user']} desde {x['ip']} ({METHODS.get(x['method'], x['method'])})", "warn")
            for x in items[:30]]
    first = items[0]
    subject = (f"Inicio de sesión SSH: {first['user']} desde {first['ip']}" if len(items) == 1
               else f"{len(items)} inicios de sesión SSH en el servidor")
    notify.event("ssh_login", subject, "Si no reconoces alguno, cambia la contraseña de ese usuario y revisa sus claves SSH "
                 "en Usuarios y grupos.", key=f"ssh:{items[-1]['t']}", cooldown=0, link="/system/",
                 sections=[{"rows": rows}])
    return len(items)


# ── Resumen diario ───────────────────────────────────────────────────────

def build_summary(now=None):
    """Resumen de las últimas 24 h con datos reales. → mensaje para notify (subject, body, level, sections, link)."""
    now = now or time.time()
    since = now - 86400
    sections, errs, warns = [], 0, 0

    def part(fn):  # cada sección por su cuenta: si una falla, el resumen sale igual
        try:
            fn()
        except Exception:
            pass

    def status():
        nonlocal errs, warns
        from .modules import dashboard
        issues, _ = dashboard.compute_issues()
        errs = sum(1 for i in issues if i[0] == "err")
        warns = len(issues) - errs
        rows = [("Problema" if lv == "err" else "Aviso", msg, lv) for lv, msg, _ in issues[:12]]
        sections.append({"title": "Estado ahora", "rows": rows or [("Estado", "Todo en orden", "ok")]})

    def resources():
        from .modules import dashboard
        boot, mem = psutil.boot_time(), psutil.virtual_memory()
        load1, cores = psutil.getloadavg()[0], psutil.cpu_count() or 1
        hist = list(dashboard._history)[-15:]
        cpu = round(sum(p["cpu"] for p in hist) / len(hist)) if hist else round(psutil.cpu_percent(interval=0.3))
        rows = [("Encendido", f"desde {notify.fmt_time(boot)} ({duration(now - boot)})"),
                ("CPU (últimos minutos)", f"{cpu} %", "warn" if cpu >= 80 else None),
                ("Memoria", f"{round(mem.percent)} % de {human(mem.total)}", "warn" if mem.percent >= 85 else None),
                ("Carga", f"{load1:.2f} con {cores} núcleos", "warn" if load1 / cores >= 1.5 else None)]
        for p in dashboard._partitions():
            rows.append((f"Disco {p['mount']}", f"{round(p['percent'])} % ({p['used_h']} de {p['total_h']})",
                         "err" if p["percent"] >= 90 else "warn" if p["percent"] >= 80 else None))
        sections.append({"title": "Recursos", "rows": rows})

    def backups():
        from . import appbackup, deployer
        cfg = appbackup.load()
        if not cfg["dests"]:
            return
        rows = []
        for name in deployer.list_apps():
            app = deployer.load_config(name) or {}
            if app.get("draft"):
                continue
            st, s = cfg["state"].get(name) or {}, appbackup.app_settings(cfg, name)
            if (st.get("last_t") or 0) >= since:
                rows.append((name, f"Correcto · {human(st.get('last_size') or 0)} · {notify.fmt_time(st['last_t'])}", "ok")
                            if st.get("last_ok") else (name, f"Falló: {st.get('last_error', '')[:120]}", "err"))
            elif s["enabled"] and app.get("active_slot"):
                rows.append((name, "Sin respaldo en las últimas 24 h", "warn"))
        if rows:
            sections.append({"title": "Respaldos de apps (24 h)", "rows": rows})

    def deploys():
        from . import deployer
        labels = {"success": ("Correcto", "ok"), "failed": ("Falló", "err"), "cancelled": ("Cancelado", None)}
        rows = []
        for name in deployer.list_apps():
            for h in deployer.load_history(name):
                if (h.get("finished") or 0) >= since and h.get("status") in labels:
                    lab, tone = labels[h["status"]]
                    rows.append((name, f"{lab} · {(h.get('sha') or '')[:7]} {h.get('message') or ''}".strip()[:120]
                                 + f" · {notify.fmt_time(h['finished'])}", tone))
        if rows:
            sections.append({"title": "Despliegues (24 h)", "rows": rows[:20]})

    def updates_part():
        from . import updates
        info = updates._last.get("info") or updates.summary()
        if info:
            n, s = info.get("upgradable", 0), info.get("security", 0)
            sections.append({"title": "Actualizaciones", "rows": [
                ("Pendientes", str(n), "warn" if n else "ok"), ("De seguridad", str(s), "err" if s else "ok")]})

    def monitor_part():
        from . import monitor
        ov = monitor.overview()
        if ov["summary"]["total"]:
            down = [c["name"] for c in ov["checks"] if c["status"] == "down"]
            rows = [("Comprobaciones", f"{ov['summary']['up']} en línea · {ov['summary']['down']} caídas · "
                                       f"{ov['summary']['paused']} en pausa", "err" if down else "ok")]
            rows += [("Caída", n, "err") for n in down[:10]]
            sections.append({"title": "Monitor", "rows": rows})

    def security():
        from . import audit
        ev = audit.query(since=since, module="auth", limit=5000)
        ok = [e for e in ev if e["action"] in ("login", "login 2fa") and e["ok"]]
        bad = [e for e in ev if e["action"] in ("login", "login 2fa") and not e["ok"]]
        changes = len([e for e in audit.query(since=since, limit=5000) if e["module"] not in ("auth",)])
        rows = [("Inicios de sesión en el panel", f"{len(ok)} ({', '.join(sorted({e['user'] for e in ok})) or '—'})"),
                ("Intentos fallidos en el panel", str(len(bad)), "warn" if bad else None),
                ("Acciones registradas en la Auditoría", str(changes))]
        try:
            from .modules import system
            f = system.ssh_failures(days=1)
            if not f.get("error"):
                top = f["ips"][0] if f["ips"] else None
                rows.append(("Intentos fallidos de SSH", f"{f['total']}" + (f" (más: {top['ip']}, {top['count']})" if top else ""),
                             "warn" if f["total"] else None))
        except Exception:
            pass
        sections.append({"title": "Seguridad (24 h)", "rows": rows})

    for fn in (status, resources, backups, deploys, updates_part, monitor_part, security):
        part(fn)
    level = "err" if errs else "warn" if warns else "ok"
    verdict = ("todo en orden" if not errs and not warns else
               f"{errs} problema{'s' if errs != 1 else ''}" + (f" y {warns} aviso{'s' if warns != 1 else ''}" if warns else "")
               if errs else f"{warns} aviso{'s' if warns != 1 else ''}")
    return {"subject": f"Resumen diario: {verdict}", "level": level, "link": "/", "badge": "Resumen diario",
            "body": f"Así está el servidor {notify._hostname()} el {time.strftime('%d/%m/%Y a las %H:%M', time.localtime(now))}, "
                    "con lo ocurrido en las últimas 24 horas.",
            "sections": sections}


def check_summary(now=None):
    now = now or time.time()
    cfg = notify.load()
    if not (cfg["enabled"] and cfg["events"].get("daily_summary")):
        return False
    lt = time.localtime(now)
    if lt.tm_hour < int(cfg.get("summary_hour", 7)):
        return False
    today = time.strftime("%Y-%m-%d", lt)
    with _lock:
        st = _load_state()
        if st.get("summary_day") == today:
            return False
        st["summary_day"] = today
        _save_state(st)
    m = build_summary(now)
    return notify.event("daily_summary", m["subject"], m["body"], key=f"summary:{today}", cooldown=0, level=m["level"],
                        sections=m["sections"], link=m["link"], badge=m["badge"])


def tick(now=None):
    now = now or time.time()
    for fn in (check_reboot, check_certs, check_ssh, check_summary):
        try:
            fn(now)
        except Exception:
            pass


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    STATE_PATH = Path(d) / "st.json"
    notify.CONFIG_PATH = Path(d) / "n.json"
    cfg, _ = notify.validate({"enabled": True, "to": "a@b.gt", "smtp": {"host": "h", "from": "p@b.gt"},
                              "events": {"login_ok": True, "daily_summary": True}, "summary_hour": 7}, notify.DEFAULTS)
    notify.save(cfg)
    sent = []
    notify.event = lambda kind, subject, body="", **kw: sent.append((kind, subject)) or True
    assert browser("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/131.0") == "Firefox en Windows"
    assert duration(90061) == "1 d 1 h" and duration(30) == "menos de 1 min"
    login("ana", "10.0.0.1")                      # primera vez: no es «IP nueva»
    login("ana", "10.0.0.1")
    login("ana", "10.0.0.9")                      # IP nueva
    assert [k for k, _ in sent] == ["login_ok", "login_ok", "login_new_ip"], sent
    sent.clear()
    st = _load_state()
    st.update(boot=psutil.boot_time() - 86400 * 3, seen=psutil.boot_time() - 600)
    _save_state(st)
    assert check_reboot() and sent[-1] == ("reboot", "El servidor se reinició") and not check_reboot()
    noon = time.mktime(time.strptime("2026-10-27 12:00", "%Y-%m-%d %H:%M"))
    early = time.mktime(time.strptime("2026-10-28 06:00", "%Y-%m-%d %H:%M"))
    globals()["build_summary"] = lambda now=None: {"subject": "Resumen diario: todo en orden", "body": "", "level": "ok",
                                                   "sections": [], "link": "/", "badge": "Resumen diario"}
    assert check_summary(noon) and not check_summary(noon + 60) and not check_summary(early)  # una vez al día, desde las 7
    print("ok")
