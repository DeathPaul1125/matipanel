"""Monitor de servicios: comprueba cada cierto tiempo que webs (HTTP/HTTPS), puertos TCP y equipos (ping)
respondan, guarda el historial (latencia, caídas) y avisa por correo cuando algo cae o se recupera.

No depende de Flask. La configuración vive en un JSON 0600; los resultados y las caídas en SQLite
(30 días). Un hilo revisa cada pocos segundos qué comprobaciones tocan y las corre en paralelo con un
pool pequeño.

Estado de cada comprobación: «pending» (aún sin datos), «up» o «down». Pasa a «down» tras N fallos
seguidos y vuelve a «up» con el primer éxito; en cada cambio se abre/cierra una caída y se avisa.
ponytail: estado en memoria, válido con un solo worker (gunicorn --workers 1).
"""
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_PATH = Path(os.environ.get("MATIPANEL_MONITOR", "/var/lib/matipanel/monitor.json"))
DB_PATH = os.environ.get("MATIPANEL_MONITOR_DB", "/var/lib/matipanel/monitor.db")
TYPES = {"http": "HTTP(S)", "tcp": "TCP", "ping": "Ping"}
INTERVALS = {30: "30 s", 60: "1 min", 300: "5 min", 900: "15 min"}
MAX_CHECKS = 200
RETENTION = 30 * 24 * 3600
POOL = 8                 # comprobaciones simultáneas como máximo
TICK = 5                 # cada cuánto mira el planificador qué toca
BODY_MAX = 1024 * 1024   # bytes del cuerpo que se leen para buscar la palabra clave
ID_RE = re.compile(r"[0-9a-f]{8}")
LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?"
HOST_RE = re.compile(rf"{LABEL}(?:\.{LABEL})*\.?")
STATUS_RE = re.compile(r"(\d{3})(?:\s*-\s*(\d{3}))?")
DEFAULTS = {"type": "http", "interval": 60, "failures": 2, "timeout": 10, "codes": "200-399", "keyword": "",
            "insecure": False, "redirects": True, "enabled": True}

_lock = threading.RLock()
_state = {}              # id → {"status", "fails", "last": {t, ok, ms, error}, "since"}
_running = set()         # ids con una comprobación en curso (no se lanzan dos a la vez)
_due = {}                # id → próxima ejecución
_db = None
_restored = False
_started = False
_uptime_cache = {"at": 0.0, "data": None}


# ── Configuración ────────────────────────────────────────────────────────

def load():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    checks = data.get("checks") if isinstance(data, dict) else None
    out = []
    for c in checks if isinstance(checks, list) else []:
        if isinstance(c, dict) and ID_RE.fullmatch(str(c.get("id", ""))):
            v, err = validate(c)
            if not err:
                out.append({**v, "id": c["id"]})
    return {"checks": out}


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


def get(cid):
    return next((c for c in load()["checks"] if c["id"] == cid), None)


def valid_host(h):
    if not isinstance(h, str) or not h or len(h) > 253:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return bool(HOST_RE.fullmatch(h)) and not h.replace(".", "").isdigit()


def split_hostport(target):
    """«host:puerto» o «[ipv6]:puerto» → (host, puerto) o None."""
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:(\d{1,5})|([^:\[\]]+):(\d{1,5})", target)
    if not m:
        return None
    host, port = (m[1], m[2]) if m[1] else (m[3], m[4])
    return (host, int(port)) if valid_host(host) and 1 <= int(port) <= 65535 else None


def _int(v, default, lo, hi):
    if v in (None, ""):
        return default
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def validate(d):
    """Datos del formulario → (comprobación sin id, error). Todo lo que viene del navegador pasa por aquí."""
    if not isinstance(d, dict):
        return None, "Formato inválido"
    name = str(d.get("name") or "").strip()
    if not name or len(name) > 60 or not name.isprintable():
        return None, "Nombre obligatorio, hasta 60 caracteres"
    kind = d.get("type") or DEFAULTS["type"]
    if kind not in TYPES:
        return None, "Tipo inválido"
    target = str(d.get("target") or "").strip()
    if not target or len(target) > 500 or not target.isprintable() or " " in target:
        return None, "Destino obligatorio y sin espacios"
    if kind == "http":
        try:
            u = urlsplit(target)
            u.port  # noqa: B018 — lanza ValueError si el puerto no es válido
        except ValueError:
            return None, "URL inválida"
        if u.scheme not in ("http", "https") or not valid_host(u.hostname or "") or "@" in u.netloc:
            return None, "URL inválida: debe empezar por http:// o https:// y llevar un nombre o IP válido"
    elif kind == "tcp":
        if not split_hostport(target):
            return None, "Destino TCP inválido: equipo:puerto (ej.: 192.168.1.10:3306 o [::1]:443)"
    elif not valid_host(target):
        return None, "Equipo inválido: una IP o un nombre (ej.: 192.168.1.1 o router.local)"
    interval = _int(d.get("interval"), DEFAULTS["interval"], 1, 10 ** 6)
    if interval not in INTERVALS:
        return None, "Intervalo inválido"
    failures = _int(d.get("failures"), DEFAULTS["failures"], 1, 10)
    if failures is None:
        return None, "Fallos antes de dar por caído: de 1 a 10"
    timeout = _int(d.get("timeout"), DEFAULTS["timeout"], 1, 30)
    if timeout is None:
        return None, "Tiempo de espera: de 1 a 30 s"
    codes = str(d.get("codes") or DEFAULTS["codes"]).strip()
    m = STATUS_RE.fullmatch(codes)
    if kind == "http" and (not m or not 100 <= int(m[1]) <= int(m[2] or m[1]) <= 599):
        return None, "Códigos esperados inválidos (ej.: 200-399 o 200)"
    keyword = str(d.get("keyword") or "").strip()
    if len(keyword) > 200 or not keyword.isprintable():
        return None, "Palabra clave: hasta 200 caracteres"
    out = {"name": name, "type": kind, "target": target, "interval": interval, "failures": failures,
           "timeout": timeout, "enabled": bool(d.get("enabled", True))}
    if kind == "http":
        out.update(codes=f"{int(m[1])}-{int(m[2] or m[1])}", keyword=keyword, insecure=bool(d.get("insecure")),
                   redirects=bool(d.get("redirects", True)))
    return out, ""


def add(d):
    v, err = validate(d)
    if err:
        return None, err
    with _lock:
        cfg = load()
        if len(cfg["checks"]) >= MAX_CHECKS:
            return None, f"Máximo {MAX_CHECKS} comprobaciones"
        ids = {c["id"] for c in cfg["checks"]}
        cid = next(i for i in iter(lambda: secrets.token_hex(4), None) if i not in ids)
        check = {"id": cid, **v}
        cfg["checks"].append(check)
        save(cfg)
        _due.pop(cid, None)
    return check, ""


def edit(cid, d):
    v, err = validate(d)
    if err:
        return None, err
    with _lock:
        cfg = load()
        old = next((c for c in cfg["checks"] if c["id"] == cid), None)
        if not old:
            return None, "No existe"
        new = {"id": cid, **v}
        cfg["checks"] = [new if c["id"] == cid else c for c in cfg["checks"]]
        save(cfg)
        if (old["type"], old["target"]) != (new["type"], new["target"]) or not new["enabled"]:
            _reset(cid)  # otro destino u otra cosa: la caída abierta ya no aplica
        _due.pop(cid, None)
    return new, ""


def set_enabled(cid, enabled):
    with _lock:
        cfg = load()
        c = next((c for c in cfg["checks"] if c["id"] == cid), None)
        if not c:
            return False
        c["enabled"] = bool(enabled)
        save(cfg)
        if not enabled:
            _reset(cid)
        _due.pop(cid, None)
    return True


def delete(cid):
    with _lock:
        cfg = load()
        if not any(c["id"] == cid for c in cfg["checks"]):
            return False
        cfg["checks"] = [c for c in cfg["checks"] if c["id"] != cid]
        save(cfg)
        _state.pop(cid, None)
        _due.pop(cid, None)
        db = _conn()
        db.execute("DELETE FROM results WHERE check_id=?", (cid,))
        db.execute("DELETE FROM incidents WHERE check_id=?", (cid,))
    return True


def _reset(cid, now=None):
    """Pausa o cambio de destino: cierra la caída abierta (sin aviso) y vuelve a «sin datos»."""
    _conn().execute("UPDATE incidents SET end=? WHERE check_id=? AND end IS NULL", (now or time.time(), cid))
    _state.pop(cid, None)


# ── Base de datos ────────────────────────────────────────────────────────

def _conn():
    global _db
    with _lock:
        if _db is None:
            try:
                if DB_PATH != ":memory:":
                    os.makedirs(os.path.dirname(DB_PATH) or ".", mode=0o700, exist_ok=True)
                conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
                if DB_PATH != ":memory:":
                    os.chmod(DB_PATH, 0o600)
            except (OSError, sqlite3.Error):
                conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS results (t REAL, check_id TEXT, ok INTEGER, ms REAL, error TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS results_ct ON results (check_id, t)")
            conn.execute("CREATE INDEX IF NOT EXISTS results_t ON results (t)")
            conn.execute("CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY, check_id TEXT, start REAL, "
                         "end REAL, error TEXT)")
            conn.execute("CREATE INDEX IF NOT EXISTS incidents_c ON incidents (check_id, start)")
            _db = conn
        return _db


def prune(now=None):
    cut = (now or time.time()) - RETENTION
    with _lock:
        db = _conn()
        db.execute("DELETE FROM results WHERE t < ?", (cut,))
        db.execute("DELETE FROM incidents WHERE end IS NOT NULL AND end < ?", (cut,))


def _restore():
    """Al arrancar el panel: estado a partir de la base (las caídas abiertas siguen «down», sin volver a avisar)."""
    global _restored
    with _lock:
        if _restored:
            return
        _restored = True
        db = _conn()
        open_inc = {cid: start for cid, start in db.execute("SELECT check_id, start FROM incidents WHERE end IS NULL")}
        for c in load()["checks"]:
            row = db.execute("SELECT t, ok, ms, error FROM results WHERE check_id=? ORDER BY t DESC LIMIT 1",
                             (c["id"],)).fetchone()
            last = {"t": row[0], "ok": bool(row[1]), "ms": row[2], "error": row[3] or ""} if row else None
            if c["id"] in open_inc:
                _state[c["id"]] = {"status": "down", "fails": c["failures"], "last": last, "since": open_inc[c["id"]]}
            elif last and last["ok"]:
                _state[c["id"]] = {"status": "up", "fails": 0, "last": last, "since": last["t"]}
            elif last:  # ponytail: los fallos seguidos previos al reinicio no se cuentan
                _state[c["id"]] = {"status": "pending", "fails": 0, "last": last, "since": last["t"]}


# ── Comprobaciones ───────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None  # la respuesta 3xx se evalúa tal cual


def check_http(c):
    """→ (ok, error). El tiempo lo mide run_check."""
    ctx = ssl.create_default_context()
    if c.get("insecure"):  # certificados propios de la red interna
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if not c.get("redirects", True):
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(c["target"], headers={"User-Agent": "MatiPanel-Monitor/1.0", "Accept": "*/*"})
    deadline = time.monotonic() + c["timeout"]
    try:
        resp = opener.open(req, timeout=c["timeout"])
    except urllib.error.HTTPError as e:
        resp = e  # 4xx/5xx (y 3xx sin seguir): también es una respuesta con código
    except urllib.error.URLError as e:
        r = e.reason
        return False, _net_error(r) if isinstance(r, OSError) else str(r)[:200]
    except (OSError, ValueError, http.client.HTTPException) as e:
        return False, _net_error(e)
    try:
        code = resp.getcode() or resp.code
        lo, hi = (int(x) for x in c.get("codes", "200-399").split("-"))
        if not lo <= code <= hi:
            return False, f"HTTP {code} (se esperaba {lo}-{hi})"
        kw = c.get("keyword")
        if kw:
            body, size = b"", 0
            # ponytail: el tope global se revisa entre bloques; un servidor que gotea bytes respeta el timeout por bloque
            while size < BODY_MAX and time.monotonic() < deadline:
                chunk = resp.read(65536)
                if not chunk:
                    break
                body += chunk
                size += len(chunk)
            charset = resp.headers.get_content_charset() or "utf-8"
            if kw not in body.decode(charset, errors="replace"):
                return False, f"HTTP {code} pero no aparece «{kw}»"
        return True, ""
    except (OSError, ValueError, http.client.HTTPException) as e:
        return False, _net_error(e)
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _net_error(e):
    if isinstance(e, socket.timeout) or isinstance(e, TimeoutError):
        return "Tiempo de espera agotado"
    if isinstance(e, socket.gaierror):
        return "No se pudo resolver el nombre"
    if isinstance(e, ssl.SSLCertVerificationError):
        return f"Certificado no válido: {e.verify_message}"[:200]
    if isinstance(e, ConnectionRefusedError):
        return "Conexión rechazada"
    return (str(e) or type(e).__name__)[:200]


def check_tcp(c):
    host, port = split_hostport(c["target"])
    try:
        socket.create_connection((host, port), timeout=c["timeout"]).close()
        return True, ""
    except OSError as e:
        return False, _net_error(e)


def check_ping(c):
    """ping -c 1 -W s -- equipo → (ok, error, ms del propio ping o None)."""
    from .util import run
    code, out, err = run(["ping", "-c", "1", "-W", str(c["timeout"]), "--", c["target"]], timeout=c["timeout"] + 5)
    if code == 0:
        m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
        return True, "", float(m[1]) if m else None
    if code == 1:
        return False, "Sin respuesta", None
    lines = (err or out).strip().splitlines()
    return False, (lines[-1] if lines else f"ping terminó con código {code}")[:200], None


def run_check(c):
    """Ejecuta una comprobación → {"t", "ok", "ms", "error"}. Nunca lanza excepción.
    ponytail: la resolución DNS no tiene tope propio (lo pone resolv.conf); si se cuelga solo bloquea esa
    comprobación (no se relanza mientras sigue en curso) y un hueco del pool, no el planificador."""
    t0, now = time.monotonic(), time.time()
    ms = None
    try:
        if c["type"] == "http":
            ok, error = check_http(c)
        elif c["type"] == "tcp":
            ok, error = check_tcp(c)
        else:
            ok, error, ms = check_ping(c)
    except Exception as e:  # una comprobación rota no debe tumbar el planificador
        ok, error = False, f"Error interno: {e}"[:200]
    if ms is None:
        ms = (time.monotonic() - t0) * 1000
    return {"t": now, "ok": bool(ok), "ms": round(ms, 1), "error": error or ""}


# ── Estado, caídas y avisos ──────────────────────────────────────────────

def record(c, r, notify_changes=True):
    """Guarda un resultado y aplica la máquina de estados. → estado nuevo."""
    _restore()
    with _lock:
        db = _conn()
        db.execute("INSERT INTO results (t, check_id, ok, ms, error) VALUES (?, ?, ?, ?, ?)",
                   (r["t"], c["id"], int(r["ok"]), r["ms"], r["error"]))
        st = _state.setdefault(c["id"], {"status": "pending", "fails": 0, "last": None, "since": r["t"]})
        st["last"] = r
        change = None
        if r["ok"]:
            st["fails"] = 0
            if st["status"] != "up":
                if st["status"] == "down":
                    db.execute("UPDATE incidents SET end=? WHERE check_id=? AND end IS NULL", (r["t"], c["id"]))
                    change = ("up", r["t"] - st["since"])
                st.update(status="up", since=r["t"])
        else:
            st["fails"] += 1
            if st["fails"] == 1:
                st["first_fail"] = r["t"]
            if st["status"] != "down" and st["fails"] >= c["failures"]:
                # la caída empieza con el primer fallo de la racha, no cuando se confirma
                start = st.get("first_fail") or r["t"]
                db.execute("INSERT INTO incidents (check_id, start, end, error) VALUES (?, ?, NULL, ?)",
                           (c["id"], start, r["error"]))
                st.update(status="down", since=start)
                change = ("down", 0)
        status = st["status"]
    if change and notify_changes:
        _notify(c, r, *change)
    return status


def _duration(sec):
    sec = int(sec)
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return " ".join(p for p in (f"{d} d" if d else "", f"{h} h" if h else "", f"{m} min" if m else "") if p) or f"{sec} s"


def _notify(c, r, status, duration):
    from . import notify
    fmt = lambda t: time.strftime("%d/%m/%Y a las %H:%M:%S", time.localtime(t))  # noqa: E731
    what = f"Comprobación: {TYPES[c['type']]} {c['target']}"
    if status == "down":
        since = (_state.get(c["id"]) or {}).get("since") or r["t"]
        subject = f"Caído: «{c['name']}» no responde"
        body = (f"«{c['name']}» dejó de responder tras {c['failures']} fallo(s) seguido(s).\n\n{what}\n"
                f"Error: {r['error']}\nPrimer fallo: {fmt(since)}")
    else:
        subject = f"Recuperado: «{c['name']}» vuelve a responder"
        body = (f"«{c['name']}» vuelve a responder ({r['ms']:.0f} ms).\n\n{what}\n"
                f"Caído desde: {fmt(r['t'] - duration)}\nRecuperado: {fmt(r['t'])}\nDuración de la caída: {_duration(duration)}")
    try:
        lines = body.split("\n\n", 1)
        rows = [tuple(x.split(": ", 1)) for x in (lines[1] if len(lines) > 1 else "").splitlines() if ": " in x]
        notify.event("monitor", subject, lines[0], key=f"monitor:{c['id']}", cooldown=0,
                     level="err" if status == "down" else "ok", link="/monitor/", sections=[{"rows": rows}])
    except Exception:
        pass


def status_of(cid):
    _restore()
    with _lock:
        st = _state.get(cid)
        return dict(st) if st else {"status": "pending", "fails": 0, "last": None, "since": None}


def health_issues():
    """Comprobaciones activas que están caídas, para la barra de salud. → [(nivel, mensaje, clave estable)]"""
    _restore()
    with _lock:
        down = {k for k, v in _state.items() if v["status"] == "down"}
    return [("err", f"Monitor: «{c['name']}» no responde", f"monitor:{c['id']}")
            for c in load()["checks"] if c["enabled"] and c["id"] in down]


# ── Consultas para la pantalla ───────────────────────────────────────────

def uptimes(now=None, max_age=60):
    """{id: {"d1": %, "d7": %, "d30": %}} (porcentaje de comprobaciones correctas; None sin datos).
    ponytail: cacheado 60 s; con 200 comprobaciones cada 30 s el rango de 30 días son millones de filas."""
    now = now or time.time()
    with _lock:
        if _uptime_cache["data"] is not None and now - _uptime_cache["at"] < max_age:
            return _uptime_cache["data"]
        out = {}
        for key, days in (("d1", 1), ("d7", 7), ("d30", 30)):
            for cid, avg in _conn().execute("SELECT check_id, AVG(ok) FROM results WHERE t >= ? GROUP BY check_id",
                                            (now - days * 86400,)):
                out.setdefault(cid, {"d1": None, "d7": None, "d30": None})[key] = round(avg * 100, 2)
        _uptime_cache.update(at=now, data=out)
        return out


def overview():
    _restore()
    checks, ups = load()["checks"], uptimes()
    rows, summary = [], {"total": len(checks), "up": 0, "down": 0, "paused": 0, "pending": 0}
    for c in checks:
        st = status_of(c["id"])
        status = st["status"] if c["enabled"] else "paused"
        summary[status] += 1
        rows.append({**c, "status": status, "fails": st["fails"], "since": st["since"], "last": st["last"],
                     "uptime": ups.get(c["id"], {"d1": None, "d7": None, "d30": None})})
    return {"checks": rows, "summary": summary}


def history(cid, rng, interval=60):
    """Latencia media por intervalo (solo comprobaciones correctas) → (segundos por punto, [{t, ms}])."""
    bucket = max(rng // 288, interval)
    with _lock:
        rows = _conn().execute(
            "SELECT CAST(t / ? AS INTEGER) * ? AS b, AVG(CASE WHEN ok THEN ms END), COUNT(*), SUM(1 - ok) "
            "FROM results WHERE check_id=? AND t >= ? GROUP BY b ORDER BY b", (bucket, bucket, cid, time.time() - rng)).fetchall()
    return bucket, [{"t": b + bucket / 2, "ms": round(ms, 1), "n": n, "fails": f} for b, ms, n, f in rows if ms is not None]


def incidents(cid, limit=50):
    with _lock:
        rows = _conn().execute("SELECT start, end, error FROM incidents WHERE check_id=? ORDER BY start DESC LIMIT ?",
                               (cid, limit)).fetchall()
    now = time.time()
    return [{"start": s, "end": e, "duration": (e or now) - s, "error": err or ""} for s, e, err in rows]


# ── Planificador ─────────────────────────────────────────────────────────

def _job(c):
    try:
        record(c, run_check(c))
    except Exception:
        pass
    finally:
        with _lock:
            _running.discard(c["id"])


def tick(pool, now=None):
    """Lanza las comprobaciones activas que tocan y que no están ya en curso."""
    now = now or time.time()
    for c in load()["checks"]:
        if not c["enabled"]:
            continue
        with _lock:
            if c["id"] in _running or now < _due.get(c["id"], 0):
                continue
            _running.add(c["id"])
            _due[c["id"]] = now + c["interval"]
        pool.submit(_job, c)


def _loop():
    pool = ThreadPoolExecutor(max_workers=POOL, thread_name_prefix="matipanel-monitor")
    last_prune = 0
    while True:
        try:
            _restore()
            tick(pool)
            if time.time() - last_prune > 3600:
                prune()
                last_prune = time.time()
        except Exception:  # el hilo no debe morir
            pass
        time.sleep(TICK)


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="matipanel-monitor").start()


if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    CONFIG_PATH, DB_PATH = tmp / "m.json", ":memory:"
    for bad in ({"name": "", "target": "http://a"}, {"name": "x" * 61, "target": "http://a"},
                {"name": "a\x07", "target": "http://a"}, {"name": "a", "target": "ftp://a"},
                {"name": "a", "target": "http://a b"}, {"name": "a", "target": "http://-x"},
                {"name": "a", "target": "http://u:p@h"}, {"name": "a", "target": "http://h:99999"},
                {"name": "a", "type": "tcp", "target": "h"}, {"name": "a", "type": "tcp", "target": "h:0"},
                {"name": "a", "type": "ping", "target": "-c9"}, {"name": "a", "type": "ping", "target": "a;b"},
                {"name": "a", "type": "dns", "target": "a"}, {"name": "a", "target": "http://a", "interval": 45},
                {"name": "a", "target": "http://a", "failures": 0}, {"name": "a", "target": "http://a", "codes": "600"},
                {"name": "a", "target": "http://a", "codes": "399-200"}, {"name": "a", "type": "ping", "target": "999"}):
        assert validate(bad)[1], bad
    v, err = validate({"name": " Intranet ", "target": "https://intra.local/salud", "interval": "300"})
    assert not err and v["name"] == "Intranet" and v["interval"] == 300 and v["codes"] == "200-399" and v["redirects"]
    assert split_hostport("[::1]:443") == ("::1", 443) and split_hostport("db.local:3306") == ("db.local", 3306)
    assert validate({"name": "r", "type": "ping", "target": "fe80::1"})[0]["target"] == "fe80::1"
    c, _ = add({"name": "Web", "type": "tcp", "target": "127.0.0.1:1", "failures": 2})
    sent = []
    _notify = lambda c, r, s, d: sent.append(s)  # noqa: E731
    f = {"t": 1000.0, "ok": False, "ms": 1.0, "error": "Conexión rechazada"}
    assert record(c, f) == "pending" and record(c, {**f, "t": 1060.0}) == "down" and record(c, {**f, "t": 1120.0}) == "down"
    assert health_issues() == [("err", "Monitor: «Web» no responde", f"monitor:{c['id']}")]
    assert record(c, {"t": 1300.0, "ok": True, "ms": 5.0, "error": ""}) == "up" and sent == ["down", "up"]
    assert incidents(c["id"])[0]["duration"] == 300 and not health_issues()
    assert uptimes(now=1400, max_age=0)[c["id"]]["d1"] == 25.0
    count = lambda tbl: _conn().execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]  # noqa: E731
    prune(now=1100 + RETENTION)
    assert count("results") == 2 and count("incidents") == 1
    prune(now=1301 + RETENTION)
    assert count("results") == 0 and count("incidents") == 0
    assert run_check(c)["ok"] is False and _duration(3720) == "1 h 2 min"
    print("ok")
