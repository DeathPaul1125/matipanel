"""Umbrales de salud del servidor: deciden qué es un aviso o un problema grave en la barra de estado y en
los correos de «Problemas del servidor» (los dos usan los mismos valores).

Cada métrica tiene: activa, umbral de aviso, umbral grave (0 = sin ese nivel) y, en las que fluctúan,
cuántos minutos debe sostenerse: se promedian las muestras de 5 s de la última hora (0 = valor actual).
Cada partición puede usar el umbral general, uno propio o no vigilarse. Se guardan en notify.json.
"""
import os

import psutil

# clave: (etiqueta, unidad, aviso, grave, minutos (None = no aplica), activa por defecto, máximo)
METRICS = {
    "cpu": ("Uso de CPU", "%", 85, 95, 5, True, 100),
    "mem": ("Uso de memoria (RAM)", "%", 85, 95, 0, True, 100),
    "swap": ("Uso de swap", "%", 50, 80, 5, False, 100),
    "load": ("Carga del sistema", "× núcleos", 1.5, 3, 5, True, 64),
    "disk": ("Uso de disco (cada partición)", "%", 80, 90, None, True, 100),
    "inodes": ("Inodos usados (cada partición)", "%", 85, 95, None, True, 100),
    "temp": ("Temperatura de la CPU", "°C", 80, 90, None, True, 150),
}
SWITCHES = {"services": "Servicios de systemd caídos (grave)", "containers": "Contenedores de Docker con error (grave)"}
MINUTES = (0, 1, 2, 5, 10, 15, 30, 60)
DEFAULTS = {**{k: {"on": v[5], "warn": v[2], "crit": v[3], "minutes": v[4] or 0} for k, v in METRICS.items()},
            **{k: {"on": True} for k in SWITCHES}, "disks": {}, "email_level": "warn"}
TEMP_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "soc_thermal", "acpitz")


def merge(raw):
    """Configuración guardada (o nada) → umbrales completos con los valores por defecto."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
    raw = raw if isinstance(raw, dict) else {}
    for k in (*METRICS, *SWITCHES):
        if isinstance(raw.get(k), dict):
            out[k].update({f: raw[k][f] for f in out[k] if f in raw[k]})
    if isinstance(raw.get("disks"), dict):
        out["disks"] = {m: r for m, r in raw["disks"].items() if isinstance(r, dict)}
    if raw.get("email_level") in ("warn", "err"):
        out["email_level"] = raw["email_level"]
    return out


def _num(v, lo, hi):
    try:
        v = float(v if v not in (None, "") else 0)
    except (TypeError, ValueError):
        return None
    return round(v, 2) if lo <= v <= hi else None


def validate(data):
    """Datos del formulario → (umbrales, error)."""
    data = data if isinstance(data, dict) else {}
    out = merge({})
    for k, (label, unit, *_rest) in METRICS.items():
        d = data.get(k) if isinstance(data.get(k), dict) else {}
        hi = METRICS[k][6]
        warn, crit = _num(d.get("warn", out[k]["warn"]), 0, hi), _num(d.get("crit", out[k]["crit"]), 0, hi)
        if warn is None or crit is None:
            return None, f"{label}: los umbrales van de 0 a {hi:g} {unit} (0 = sin ese nivel)"
        if warn and crit and warn >= crit:
            return None, f"{label}: el umbral grave debe ser mayor que el de aviso"
        try:
            minutes = int(d.get("minutes", out[k]["minutes"]) or 0)
        except (TypeError, ValueError):
            minutes = -1
        if minutes not in MINUTES:
            return None, f"{label}: tiempo sostenido inválido"
        out[k] = {"on": bool(d.get("on", out[k]["on"])), "warn": warn, "crit": crit,
                  "minutes": minutes if METRICS[k][4] is not None else 0}
    for k in SWITCHES:
        d = data.get(k) if isinstance(data.get(k), dict) else {}
        out[k] = {"on": bool(d.get("on", True))}
    disks = data.get("disks") if isinstance(data.get("disks"), dict) else {}
    if len(disks) > 50:
        return None, "Demasiadas particiones"
    for mount, r in disks.items():
        if not isinstance(mount, str) or not 0 < len(mount) <= 200 or not mount.isprintable() or not isinstance(r, dict):
            return None, "Partición inválida"
        mode = r.get("mode", "default")
        if mode == "off":
            out["disks"][mount] = {"mode": "off"}
        elif mode == "custom":
            warn, crit = _num(r.get("warn"), 0, 100), _num(r.get("crit"), 0, 100)
            if warn is None or crit is None or (warn and crit and warn >= crit) or not (warn or crit):
                return None, f"Disco {mount}: umbrales inválidos (de 0 a 100 %, el grave mayor que el de aviso)"
            out["disks"][mount] = {"mode": "custom", "warn": warn, "crit": crit}
        elif mode != "default":
            return None, f"Disco {mount}: opción inválida"
    if data.get("email_level", "warn") not in ("warn", "err"):
        return None, "Nivel de correo inválido"
    out["email_level"] = data.get("email_level", "warn")
    return out, ""


def get():
    from . import notify
    return merge(notify.load().get("thresholds"))


# ── Lecturas que no están en el historial ───────────────────────────────

def inode_percent(mount):
    """% de inodos usados (None si el sistema de archivos no los informa, p. ej. btrfs)."""
    try:
        st = os.statvfs(mount)
    except (OSError, AttributeError):
        return None
    return round((st.f_files - st.f_ffree) / st.f_files * 100, 1) if st.f_files else None


def cpu_temperature():
    """Temperatura más alta de la CPU en °C, o None si no hay sensores (lo normal en máquinas virtuales)."""
    try:
        sensors = psutil.sensors_temperatures() or {}
    except (AttributeError, OSError):
        return None
    for name in TEMP_SENSORS:
        vals = [s.current for s in sensors.get(name, []) if s.current and 0 < s.current < 150]
        if vals:
            return max(vals)
    return None


# ── Evaluación ───────────────────────────────────────────────────────────

def _level(v, rule):
    if rule.get("crit") and v >= rule["crit"]:
        return "err"
    if rule.get("warn") and v >= rule["warn"]:
        return "warn"
    return None


def _sustained(history, field, minutes, now):
    """Valor actual (minutos = 0) o promedio de la ventana; None si aún no hay datos suficientes."""
    if not history:
        return None
    if not minutes:
        return history[-1].get(field)
    win = [p[field] for p in history if p["t"] >= now - minutes * 60 and p.get(field) is not None]
    if len(win) < minutes * 60 / 5 * 0.8:  # tras arrancar, esperar a tener casi toda la ventana
        return None
    return sum(win) / len(win)


def evaluate(t, history, now, partitions, cores, temp=None):
    """→ [(nivel, mensaje, clave)] de CPU, memoria, swap, carga, discos, inodos y temperatura.
    partitions: [{mount, percent, inodes (o None)}]; history: muestras {t, cpu, mem, swap, load}."""
    out = []
    for key, fmt in (("cpu", "CPU al {v:.0f} %"), ("mem", "Memoria al {v:.0f} %"), ("swap", "Swap al {v:.0f} %")):
        rule = t[key]
        v = _sustained(history, key, rule["minutes"], now) if rule["on"] else None
        lv = _level(v, rule) if v is not None else None
        if lv:
            out.append((lv, fmt.format(v=v) + (f" ({rule['minutes']} min)" if rule["minutes"] else ""), key))
    rule = t["load"]
    v = _sustained(history, "load", rule["minutes"], now) if rule["on"] else None
    if v is not None and cores:
        lv = _level(v / cores, rule)
        if lv:
            out.append((lv, f"Carga alta ({v:.2f} con {cores} núcleos)", "load"))
    for p in partitions:
        own = t["disks"].get(p["mount"]) or {}
        if own.get("mode") == "off":
            continue
        rule = own if own.get("mode") == "custom" else t["disk"]
        if (own.get("mode") == "custom" or t["disk"]["on"]) and p.get("percent") is not None:
            lv = _level(p["percent"], rule)
            if lv:
                out.append((lv, f"Disco {p['mount']} al {round(p['percent'])} %", f"disk:{p['mount']}"))
        if t["inodes"]["on"] and p.get("inodes") is not None:
            lv = _level(p["inodes"], t["inodes"])
            if lv:
                out.append((lv, f"Inodos de {p['mount']} al {round(p['inodes'])} %", f"inodes:{p['mount']}"))
    rule = t["temp"]
    if rule["on"] and temp is not None:
        lv = _level(temp, rule)
        if lv:
            out.append((lv, f"CPU a {temp:.0f} °C", "temp"))
    return out


def describe(t, key):
    """Umbral aplicado a un problema, para el correo («aviso 85 % · grave 95 % · sostenido 5 min»)."""
    metric, _, mount = key.partition(":")
    if metric not in METRICS:
        return ""
    rule, unit = t[metric], METRICS[metric][1]
    own = t["disks"].get(mount) if metric == "disk" else None
    if own and own.get("mode") == "custom":
        rule = own
    parts = [f"aviso {rule['warn']:g} {unit}" if rule.get("warn") else "", f"grave {rule['crit']:g} {unit}" if rule.get("crit") else "",
             f"sostenido {rule['minutes']} min" if rule.get("minutes") else ""]
    return " · ".join(p for p in parts if p) + (" (propio de esta partición)" if own and own.get("mode") == "custom" else "")


if __name__ == "__main__":
    t = merge({})
    hist = [{"t": 1000 + i * 5, "cpu": 97, "mem": 90, "swap": 10, "load": 2.0} for i in range(61)]  # 5 min
    now = hist[-1]["t"]
    parts = [{"mount": "/", "percent": 93, "inodes": 40}, {"mount": "/respaldos", "percent": 96, "inodes": None},
             {"mount": "/srv", "percent": 85, "inodes": 97}]
    iss = {k: (lv, m) for lv, m, k in evaluate(t, hist, now, parts, cores=4, temp=85)}
    assert iss["cpu"] == ("err", "CPU al 97 % (5 min)") and iss["mem"] == ("warn", "Memoria al 90 %"), iss
    assert "load" not in iss and iss["disk:/"][0] == "err" and iss["disk:/srv"][0] == "warn" and iss["temp"][0] == "warn"
    assert iss["inodes:/srv"] == ("err", "Inodos de /srv al 97 %") and "swap" not in iss  # swap apagada por defecto
    t2, err = validate({"cpu": {"on": True, "warn": 70, "crit": 0, "minutes": 1}, "disks": {"/respaldos": {"mode": "custom", "warn": 97, "crit": 99},
                                                                                         "/srv": {"mode": "off"}},
                        "load": {"on": False}, "email_level": "err"})
    assert not err, err
    iss = {k: lv for lv, _, k in evaluate(t2, hist[-13:], now, parts, cores=4)}
    assert iss["cpu"] == "warn" and "disk:/respaldos" not in iss and "disk:/srv" not in iss and "load" not in iss, iss
    assert evaluate(t, hist[:10], now, [], 4) == [("warn", "Memoria al 90 %", "mem")]  # CPU: menos de 5 min de datos
    assert describe(t2, "disk:/respaldos") == "aviso 97 % · grave 99 % (propio de esta partición)"
    assert describe(t, "cpu") == "aviso 85 % · grave 95 % · sostenido 5 min" and describe(t, "unit:nginx") == ""
    for bad in ({"cpu": {"warn": 90, "crit": 80}}, {"mem": {"warn": 101}}, {"cpu": {"minutes": 7}},
                {"disks": {"/x": {"mode": "custom", "warn": 0, "crit": 0}}}, {"disks": {"/x": {"mode": "otro"}}},
                {"email_level": "x"}, {"load": {"warn": "abc"}}):
        assert validate(bad)[1], bad
    print("ok")
