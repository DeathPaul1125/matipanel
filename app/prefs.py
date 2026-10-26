"""Preferencias de apariencia por usuario (guardadas en el servidor).

Se validan contra una lista blanca y se convierten en atributos data-* del <html>
más un bloque CSS con variables. El CSS solo contiene valores validados (colores #rrggbb).
"""
import copy
import json
import os
import re
from pathlib import Path

STORE_PATH = Path(os.environ.get("MATIPANEL_PREFS", "/var/lib/matipanel/prefs.json"))

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DASH_SECTIONS = ("gauges", "history", "info", "procs", "quick")

DEFAULTS = {
    "accent": "default",       # "default" (verde fósforo) o "#rrggbb"
    "sidebar": "navy",         # navy | light | graphite | accent | "#rrggbb"
    "sbmini": False,           # barra lateral solo con iconos
    "density": "comfortable",  # comfortable | compact
    "zoom": "100",             # 90 | 100 | 110 | 120
    "radius": "soft",          # square | soft | round
    "font": "plex",            # plex | system
    "motion": "on",            # on | off
    "dash": {s: True for s in DASH_SECTIONS},
    "range": "900",            # 300 | 900 | 3600 | 21600 | 86400 | 604800 (segundos)
}
CHOICES = {
    "density": ("comfortable", "compact"),
    "zoom": ("90", "100", "110", "120"),
    "radius": ("square", "soft", "round"),
    "font": ("plex", "system"),
    "motion": ("on", "off"),
    "range": ("300", "900", "3600", "21600", "86400", "604800"),
}
SIDEBAR_PRESETS = {"navy": "#0F1E3A", "graphite": "#1C1E22"}
DEFAULT_ACCENT = "#0E7C6B"


def _load_all():
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def get(username):
    prefs = copy.deepcopy(DEFAULTS)
    saved = _load_all().get(username, {}) if username else {}
    return clean({**prefs, **saved, "dash": {**prefs["dash"], **saved.get("dash", {})}})


def clean(p):
    """Descarta todo valor fuera de la lista blanca (vuelve al predeterminado)."""
    out = copy.deepcopy(DEFAULTS)
    if p.get("accent") == "default" or HEX.match(str(p.get("accent", ""))):
        out["accent"] = p["accent"]
    s = p.get("sidebar")
    if s in ("navy", "light", "graphite", "accent") or HEX.match(str(s or "")):
        out["sidebar"] = s
    for key, allowed in CHOICES.items():
        if str(p.get(key)) in allowed:
            out[key] = str(p[key])
    out["sbmini"] = bool(p.get("sbmini"))
    dash = p.get("dash") if isinstance(p.get("dash"), dict) else {}
    out["dash"] = {s: bool(dash.get(s, True)) for s in DASH_SECTIONS}
    return out


def save(username, prefs):
    data = _load_all()
    data[username] = clean(prefs)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STORE_PATH)
    return data[username]


def _luminance(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def css(p):
    """Variables CSS derivadas de las preferencias (vacío si todo es predeterminado)."""
    rules = []
    accent = p["accent"]
    if accent != "default":
        on = "#111111" if _luminance(accent) > 0.45 else "#FFFFFF"
        rules.append(
            f":root{{--accent:{accent};--accent-hover:color-mix(in srgb,{accent} 82%,#000);"
            f"--accent-bg:color-mix(in srgb,{accent} 13%,#fff);--accent-text:color-mix(in srgb,{accent} 72%,#000);"
            f"--on-accent:{on}}}"
            f":root[data-theme=dark]{{--accent:color-mix(in srgb,{accent} 72%,#fff);"
            f"--accent-hover:color-mix(in srgb,{accent} 55%,#fff);--accent-bg:color-mix(in srgb,{accent} 24%,#1E1E1C);"
            f"--accent-text:color-mix(in srgb,{accent} 50%,#fff);--on-accent:#111111}}")

    sb = p["sidebar"]
    if sb != "light":
        color = SIDEBAR_PRESETS.get(sb) or (
            f"color-mix(in srgb,{accent if accent != 'default' else DEFAULT_ACCENT} 72%,#000)" if sb == "accent" else sb)
        light_bg = HEX.match(sb) and _luminance(sb) > 0.5
        if light_bg:  # color claro elegido a mano: texto oscuro
            rules.append(f":root{{--sb-bg:{color};--sb-text:#16181c;--sb-text-2:rgba(0,0,0,.66);--sb-faint:rgba(0,0,0,.45);"
                         "--sb-hover:rgba(0,0,0,.06);--sb-active-bg:rgba(0,0,0,.1);--sb-active-text:#000;--sb-border:rgba(0,0,0,.08)}")
        else:
            rules.append(f":root{{--sb-bg:{color};--sb-text:#fff;--sb-text-2:rgba(255,255,255,.74);--sb-faint:rgba(255,255,255,.46);"
                         "--sb-hover:rgba(255,255,255,.08);--sb-active-bg:color-mix(in srgb,var(--accent) 32%,transparent);"
                         "--sb-active-text:#fff;--sb-border:rgba(255,255,255,.07)}")
    return "".join(rules)


if __name__ == "__main__":
    assert clean({"accent": "red;}body{display:none", "sidebar": "x", "zoom": "500"}) == DEFAULTS
    assert clean({"accent": "#2563EB"})["accent"] == "#2563EB"
    assert "--sb-bg:#0F1E3A" in css(clean({}))            # azul oscuro por defecto
    assert "--sb-text:#16181c" in css(clean({"sidebar": "#F5F5F0"}))  # color claro → texto oscuro
    assert css(clean({"sidebar": "light"})) == ""
    print("ok")
