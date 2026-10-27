"""Aviso de actualizaciones de paquetes del sistema.

Cada hora mira qué paquetes se pueden actualizar (APT o DNF); cada 12 h refresca antes la lista de
paquetes (apt-get update) por si el temporizador apt-daily del sistema está apagado. Si aparecen
versiones que todavía no se avisaron, manda un correo (Notificaciones → «Actualizaciones disponibles»)
con las de seguridad primero. Lo ya avisado se recuerda en un JSON para no repetir el correo cada hora.
"""
import json
import os
import threading
import time
from pathlib import Path

STATE_PATH = Path(os.environ.get("MATIPANEL_UPDATES", "/var/lib/matipanel/updates.json"))
CHECK_EVERY = 3600
REFRESH_EVERY = 12 * 3600
_lock = threading.Lock()
_last = {"check": 0.0, "refresh": 0.0, "info": None}


def _load():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, STATE_PATH)


def summary():
    """{upgradable, security, upgradable_list[...]} del gestor de paquetes (None si no hay APT/DNF)."""
    from .distro import detect
    from .modules import packages
    pkg = detect()["pkg"]
    if pkg == "apt":
        return packages._apt_summary()
    if pkg in ("dnf", "yum"):
        return packages._dnf_summary(pkg)
    return None


def refresh_lists():
    from .distro import detect
    from .util import run
    if detect()["pkg"] == "apt":
        run(["apt-get", "-qq", "-o", "DPkg::Lock::Timeout=120", "update"], timeout=600)


def new_since_notified(info, notified):
    """Paquetes (nombre=versión) que no se habían avisado; seguridad primero."""
    pkgs = [p for p in info.get("upgradable_list", []) if f"{p['name']}={p['new_version']}" not in notified]
    return sorted(pkgs, key=lambda p: (not p["security"], p["name"]))


def message(info, new):
    sec = [p for p in new if p["security"]]
    subject = (f"{len(new)} actualizaciones nuevas" + (f" ({len(sec)} de seguridad)" if sec else "")
               if len(new) != 1 else f"Actualización nueva: {new[0]['name']}" + (" (seguridad)" if sec else ""))
    lines = [f"Hay {info['upgradable']} paquetes que se pueden actualizar"
             + (f", {info['security']} de seguridad" if info.get("security") else "") + ".", ""]
    if sec:
        lines += ["De seguridad (conviene instalarlas cuanto antes):"] + [f"  • {p['name']} → {p['new_version']}" for p in sec] + [""]
    rest = [p for p in new if not p["security"]]
    if rest:
        lines += ["Otras:"] + [f"  • {p['name']} → {p['new_version']}" for p in rest[:60]]
        if len(rest) > 60:
            lines.append(f"  … y {len(rest) - 60} más")
        lines.append("")
    lines.append("Puedes instalarlas en el panel: Sistema → Paquetes → Actualizar todo"
                 " (o activa las actualizaciones automáticas de seguridad).")
    return subject, "\n".join(lines)


def check(now=None, force_refresh=False):
    """Revisa y avisa si hay versiones nuevas. → info (o None)."""
    now = now or time.time()
    with _lock:
        if force_refresh or now - _last["refresh"] >= REFRESH_EVERY:
            refresh_lists()
            _last["refresh"] = now
        info = summary()
        _last.update(check=now, info=info)
        if not info:
            return info
        state = _load()
        notified = set(state.get("notified", []))
        new = new_since_notified(info, notified)
        # Lo que ya no está pendiente (se instaló) se olvida: así una versión futura vuelve a avisarse
        pending = {f"{p['name']}={p['new_version']}" for p in info.get("upgradable_list", [])}
        if new:
            from . import notify
            subject, body = message(info, new)
            sent = notify.event("updates", subject, body, key="updates:" + ",".join(sorted(f"{p['name']}={p['new_version']}" for p in new))[:200],
                                cooldown=0)
            if sent or not notify.load()["enabled"]:
                notified |= {f"{p['name']}={p['new_version']}" for p in new}
        _save({"notified": sorted(notified & pending), "checked": now})
        return info


def security_issue():
    """Aviso para la barra de estado si hay actualizaciones de seguridad pendientes (del último chequeo)."""
    info = _last["info"]
    if info and info.get("security"):
        n = info["security"]
        return [("warn", f"{n} actualización{'es' if n != 1 else ''} de seguridad pendiente{'s' if n != 1 else ''}", "updates:security")]
    return []


def tick(now=None):
    """Lo llama el hilo de notificaciones cada minuto; solo trabaja cada CHECK_EVERY."""
    now = now or time.time()
    if now - _last["check"] >= CHECK_EVERY:
        try:
            check(now)
        except Exception:  # nunca debe tumbar el hilo
            _last["check"] = now


if __name__ == "__main__":
    import tempfile
    STATE_PATH = Path(tempfile.mkdtemp()) / "u.json"
    info = {"upgradable": 3, "security": 1, "upgradable_list": [
        {"name": "nginx", "new_version": "1.26.3-4", "origin": "trixie", "security": False},
        {"name": "openssl", "new_version": "3.5.1-1+deb13u1", "origin": "trixie-security", "security": True},
        {"name": "curl", "new_version": "8.14-2", "origin": "trixie", "security": False}]}
    new = new_since_notified(info, {"nginx=1.26.3-4"})
    assert [p["name"] for p in new] == ["openssl", "curl"]
    subject, body = message(info, new)
    assert subject == "2 actualizaciones nuevas (1 de seguridad)" and "openssl → 3.5.1-1+deb13u1" in body
    assert body.index("openssl") < body.index("curl")
    assert message(info, new[:1])[0] == "Actualización nueva: openssl (seguridad)"
    _last["info"] = info
    assert security_issue()[0][2] == "updates:security"
    print("ok")
