"""Versión del panel e historial de actualizaciones.

- La versión está en app/__init__.py (__version__) y sus novedades en CHANGELOG.md (versionado semántico).
- install.sh deja BUILD.json junto a la app: versión, commit de git, rama y fecha de instalación.
- Al arrancar, si la versión o el commit cambiaron respecto de la última vez, se anota en
  /var/lib/matipanel/version_history.json, se registra en la Auditoría y se avisa por correo.
"""
import json
import os
import re
import time
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent          # /opt/matipanel (o la raíz del repositorio)
BUILD_PATH = ROOT / "BUILD.json"
CHANGELOG = ROOT / "CHANGELOG.md"
HISTORY_PATH = Path(os.environ.get("MATIPANEL_VERSION_HISTORY", "/var/lib/matipanel/version_history.json"))
HISTORY_MAX = 50


def build():
    try:
        data = json.loads(BUILD_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def history():
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return [h for h in data if isinstance(h, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items):
    HISTORY_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items[-HISTORY_MAX:], f, indent=2, ensure_ascii=False)
    os.replace(tmp, HISTORY_PATH)


def current():
    b = build()
    return {"version": __version__, "commit": b.get("commit", ""), "branch": b.get("branch", ""),
            "installed_at": b.get("installed_at", "")}


def record_startup(now=None):
    """Anota la versión si cambió desde el último arranque. → la entrada nueva o None."""
    cur, items = current(), history()
    last = items[-1] if items else None
    if last and last.get("version") == cur["version"] and last.get("commit") == cur["commit"]:
        return None
    entry = {**cur, "at": now or time.time(), "previous": (last or {}).get("version", "")}
    _save(items + [entry])
    if last:  # la primera vez solo se anota: no es una actualización
        try:
            from . import audit
            audit.log("panel", f"actualizado a {cur['version']}", cur["commit"], True, 0,
                      {"desde": last.get("version", ""), "commit": cur["commit"]}, user="sistema", ip="")
        except Exception:
            pass
        try:
            from . import notify
            notify.event("panel_update", f"MatiPanel se actualizó a la versión {cur['version']}",
                         "El panel se actualizó y ya está funcionando. Novedades completas en Acerca de.",
                         key=f"panel_update:{cur['version']}:{cur['commit']}", cooldown=0, link="/acerca",
                         sections=[{"rows": [("Versión", f"{last.get('version', '?')} → {cur['version']}", "ok")]
                                    + ([("Commit", cur["commit"])] if cur["commit"] else [])},
                                   *([{"title": "Novedades", "rows": [("", ln.lstrip("-• ").strip())
                                                                      for ln in changes(cur["version"]).splitlines()
                                                                      if ln.strip().startswith(("-", "•"))][:12]}]
                                     if changes(cur["version"]) else [])])
        except Exception:
            pass
    return entry


def changelog_sections():
    """CHANGELOG.md → [(versión, fecha, texto markdown)] en el orden del archivo."""
    try:
        text = CHANGELOG.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for m in re.finditer(r"^## \[([^\]]+)\](?: - (\S+))?\s*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S):
        out.append((m[1], m[2] or "", m[3].strip()))
    return out


def changes(version):
    return next((body for v, _, body in changelog_sections() if v == version), "")


if __name__ == "__main__":
    import tempfile
    HISTORY_PATH = Path(tempfile.mkdtemp()) / "h.json"
    assert record_startup(1000.0)["previous"] == "" and record_startup(2000.0) is None   # mismo: no se repite
    __version__ = "9.9.9"
    e = record_startup(3000.0)
    assert e["version"] == "9.9.9" and e["previous"] and len(history()) == 2
    secs = changelog_sections()
    assert secs and all(len(s) == 3 for s in secs)
    print("ok")
