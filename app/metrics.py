"""Historial de métricas persistente (SQLite de la biblioteca estándar).

Una fila cada 5 s durante 7 días (~121 000 filas, unos 10 MB).
ponytail: una sola tabla con muestras crudas; si se quiere más de 7 días, agregar una
tabla de promedios por minuto y consultar esa para rangos largos.
"""
import os
import sqlite3
import threading
import time

FIELDS = ("cpu", "mem", "swap", "rx", "tx", "rd", "wr", "procs", "load")
IF_FIELDS = ("rx", "tx", "rxp", "txp", "errs", "drops")  # por interfaz: bytes/s, paquetes/s, errores y descartes/s
RETENTION = 7 * 24 * 3600
MAX_POINTS = 600  # puntos máximos por consulta: los rangos largos se promedian

_lock = threading.Lock()
_db = None


def open_db(path=None):
    """Abre (o crea) la base. Sin permisos de escritura usa memoria (modo desarrollo)."""
    global _db
    path = path or os.environ.get("MATIPANEL_METRICS", "/var/lib/matipanel/metrics.db")
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            os.chmod(path, 0o600)
    except (OSError, sqlite3.Error):
        conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS samples (t REAL PRIMARY KEY, "
                 + ", ".join(f"{f} REAL" for f in FIELDS) + ")")
    # Tráfico por interfaz: una fila cada 30 s por interfaz (~20 000 por interfaz en 7 días)
    conn.execute("CREATE TABLE IF NOT EXISTS ifaces (t REAL, iface TEXT, "
                 + ", ".join(f"{f} REAL" for f in IF_FIELDS) + ", PRIMARY KEY (t, iface))")
    _db = conn
    return conn


def insert(sample):
    with _lock:
        _db.execute(f"INSERT OR REPLACE INTO samples (t, {', '.join(FIELDS)}) VALUES ({', '.join('?' * (len(FIELDS) + 1))})",
                    [sample["t"], *(sample[f] for f in FIELDS)])


def prune(now=None):
    with _lock:
        _db.execute("DELETE FROM samples WHERE t < ?", [(now or time.time()) - RETENTION])
        _db.execute("DELETE FROM ifaces WHERE t < ?", [(now or time.time()) - RETENTION])


def insert_ifaces(t, rows):
    """rows: {iface: {rx, tx, rxp, txp, errs, drops}}"""
    with _lock:
        _db.executemany(f"INSERT OR REPLACE INTO ifaces (t, iface, {', '.join(IF_FIELDS)}) "
                        f"VALUES ({', '.join('?' * (len(IF_FIELDS) + 2))})",
                        [[t, name, *(r[f] for f in IF_FIELDS)] for name, r in rows.items()])


def query_iface(iface, range_s, now=None, since=None, min_bucket=30):
    """Como query(), para una interfaz. Retorna (bucket, puntos)."""
    now = now or time.time()
    bucket = max(min_bucket, int(range_s / MAX_POINTS))
    start = max(now - range_s, since or 0)
    cols = ", ".join(f"AVG({f})" for f in IF_FIELDS)
    with _lock:
        rows = _db.execute(
            f"SELECT CAST(t / ? AS INTEGER) * ? + ? / 2.0, MAX(t), {cols} FROM ifaces "
            "WHERE iface = ? AND t > ? GROUP BY CAST(t / ? AS INTEGER) ORDER BY 1",
            [bucket, bucket, bucket, iface, start, bucket]).fetchall()
    return bucket, [{"t": round(r[1] if bucket <= min_bucket else r[0], 1),
                     **{f: round(v or 0, 2) for f, v in zip(IF_FIELDS, r[2:])}} for r in rows]


def known_ifaces():
    with _lock:
        return [r[0] for r in _db.execute("SELECT DISTINCT iface FROM ifaces ORDER BY iface")]


def query(range_s, now=None, since=None, min_bucket=5):
    """Puntos de los últimos `range_s` segundos, promediados en cubetas para no pasar de MAX_POINTS.

    Con `since`, solo los posteriores (refresco incremental). Retorna (bucket, puntos).
    """
    now = now or time.time()
    bucket = max(min_bucket, int(range_s / MAX_POINTS))
    start = max(now - range_s, since or 0)
    cols = ", ".join(f"AVG({f}) AS {f}" for f in FIELDS)
    with _lock:
        rows = _db.execute(
            f"SELECT CAST(t / ? AS INTEGER) * ? + ? / 2.0 AS b, MAX(t) AS last, {cols} "
            "FROM samples WHERE t > ? GROUP BY CAST(t / ? AS INTEGER) ORDER BY b",
            [bucket, bucket, bucket, start, bucket]).fetchall()
    points = []
    for row in rows:
        p = {"t": round(row[1] if bucket <= min_bucket else row[0], 1)}
        p.update({f: round(v, 2) if v is not None else 0 for f, v in zip(FIELDS, row[2:])})
        points.append(p)
    return bucket, points


def recent(n):
    """Últimas n muestras crudas, de la más vieja a la más nueva."""
    with _lock:
        rows = _db.execute(f"SELECT t, {', '.join(FIELDS)} FROM samples ORDER BY t DESC LIMIT ?", [n]).fetchall()
    return [dict(zip(("t", *FIELDS), r)) for r in reversed(rows)]


if __name__ == "__main__":
    open_db(":memory:")
    base = 1_000_000.0
    for i in range(2000):  # ~2,8 h de muestras cada 5 s
        insert({"t": base + i * 5, **{f: float(i % 100) for f in FIELDS}})
    now = base + 1999 * 5
    b, pts = query(900, now=now)
    assert b == 5 and len(pts) == 180, (b, len(pts))
    b, pts = query(7 * 86400, now=now)
    assert b >= 1008 and len(pts) <= MAX_POINTS + 1, (b, len(pts))
    b, pts = query(900, now=now, since=now - 20)
    assert len(pts) == 4, pts
    assert recent(3)[-1]["t"] == now
    prune(now=now + RETENTION + 1)
    assert query(900, now=now)[1] == []
    print("ok")
