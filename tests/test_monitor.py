"""Monitor de servicios: motor (estados, uptime, poda, validación) y API (CRUD, probar ahora, avisos)."""
import sys, types, os, time, socket, threading
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, sys.argv[1])
for f in (TMPD + "/mon_cfg.json", TMPD + "/mon.db", TMPD + "/mon.db-wal", TMPD + "/mon.db-shm"):
    if os.path.exists(f):
        os.unlink(f)
os.environ.update({"MATIPANEL_PREFS": TMPD + "/monprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/mon2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_NOTIFY_WORKER": "0", "MATIPANEL_APPBACKUP_WORKER": "0",
                   "MATIPANEL_NOTIFY": TMPD + "/mon_notify.json", "MATIPANEL_AUDIT": TMPD + "/mon_audit.db",
                   "MATIPANEL_ROLES": TMPD + "/mon_roles.json", "MATIPANEL_MONITOR": TMPD + "/mon_cfg.json",
                   "MATIPANEL_MONITOR_DB": TMPD + "/mon.db", "MATIPANEL_MONITOR_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app, notify, util
from app import monitor as M
from app.modules.monitor import monitor_bp
app = create_app()
assert not M._started  # MATIPANEL_MONITOR_WORKER=0: sin hilo
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}

# ── servidor HTTP local y un puerto cerrado ──
FLAG = {"fail": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302); self.send_header("Location", "/"); self.end_headers(); return
        code = 500 if self.path == "/toggle" and FLAG["fail"] else 200
        body = "<h1>Hola MatiPanel</h1>".encode()
        self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
s = socket.socket(); s.bind(("127.0.0.1", 0)); CLOSED = s.getsockname()[1]; s.close()
BASE = f"http://127.0.0.1:{PORT}"

SENT = []
notify.event = lambda kind, subject, body, key=None, cooldown=600, **kw: SENT.append((kind, subject, key, cooldown)) or True
assert notify.EVENTS["monitor"][:2] == ("Monitor: un servicio deja de responder o se recupera", True)


def add(**kw):
    r = c.post("/monitor/api/checks", json=kw, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["check"]


def test(cid):
    r = c.post(f"/monitor/api/checks/{cid}/test", headers=H).get_json()
    assert r["ok"], r
    return r


def row(cid):
    return next(x for x in c.get("/monitor/api/checks").get_json()["checks"] if x["id"] == cid)


# ── validación en la frontera ──
for bad in ({}, {"name": "x", "target": "ftp://a"}, {"name": "x" * 61, "target": BASE}, {"name": "a\nb", "target": BASE},
            {"name": "x", "target": "http://a b/"}, {"name": "x", "target": "http://-a/"}, {"name": "x", "target": "https://h:70000/"},
            {"name": "x", "type": "tcp", "target": "h:0"}, {"name": "x", "type": "tcp", "target": "h"},
            {"name": "x", "type": "ping", "target": "-f"}, {"name": "x", "type": "ping", "target": "a;rm -rf"},
            {"name": "x", "type": "ping", "target": "$(id)"}, {"name": "x", "type": "otro", "target": "a"},
            {"name": "x", "target": BASE, "interval": 10}, {"name": "x", "target": BASE, "failures": 99},
            {"name": "x", "target": BASE, "timeout": 0}, {"name": "x", "target": BASE, "codes": "abc"}, [1, 2]):
    r = c.post("/monitor/api/checks", json=bad, headers=H)
    assert r.status_code == 400 and r.get_json()["error"], bad
assert c.get("/monitor/api/checks").get_json()["checks"] == []

# ── HTTP con palabra clave: correcto ──
web = add(name="Intranet", target=BASE + "/", keyword="Hola MatiPanel", interval="30", failures="2", timeout="5")
assert web["interval"] == 30 and web["codes"] == "200-399" and row(web["id"])["status"] == "pending"
r = test(web["id"])
assert r["result"]["ok"] and r["status"] == "up" and r["result"]["ms"] >= 0, r
web_bad = add(name="Sin palabra", target=BASE + "/", keyword="No está")
r = test(web_bad["id"])
assert not r["result"]["ok"] and "no aparece" in r["result"]["error"], r
redir = add(name="Redirección", target=BASE + "/redir", redirects=False, codes="200")
r = test(redir["id"])
assert not r["result"]["ok"] and "HTTP 302" in r["result"]["error"], r
redir2 = add(name="Redirección seguida", target=BASE + "/redir", codes="200")
assert test(redir2["id"])["result"]["ok"]

# ── TCP a un puerto cerrado: N fallos → DOWN y aviso; health_issues ──
SENT.clear()
tcp = add(name="Base de datos", type="tcp", target=f"127.0.0.1:{CLOSED}", failures=2, timeout=2)
r1, r2 = test(tcp["id"]), test(tcp["id"])
assert not r1["result"]["ok"] and r1["status"] == "pending" and r2["status"] == "down", (r1, r2)
assert SENT == [("monitor", "Caído: «Base de datos» no responde", f"monitor:{tcp['id']}", 0)], SENT
assert ("err", "Monitor: «Base de datos» no responde", f"monitor:{tcp['id']}") in M.health_issues()
assert test(tcp["id"])["status"] == "down" and len(SENT) == 1  # sigue caído: sin repetir aviso
tcp_ok = add(name="Puerto web", type="tcp", target=f"127.0.0.1:{PORT}")
assert test(tcp_ok["id"])["result"]["ok"]

# ── HTTP que falla y se recupera: aviso de caída y de recuperación ──
SENT.clear()
tog = add(name="Toggle", target=BASE + "/toggle", failures=1)
r = test(tog["id"])
assert r["status"] == "down" and "HTTP 500" in r["result"]["error"], r
FLAG["fail"] = False
assert test(tog["id"])["status"] == "up"
assert [s[1] for s in SENT] == ["Caído: «Toggle» no responde", "Recuperado: «Toggle» vuelve a responder"], SENT
h = c.get(f"/monitor/api/checks/{tog['id']}/history?range=86400").get_json()
assert h["ok"] and len(h["incidents"]) == 1 and h["incidents"][0]["end"] and h["points"], h
assert c.get(f"/monitor/api/checks/{tog['id']}/history?range=123").status_code == 400
assert c.get("/monitor/api/checks/zzzz/history?range=86400").status_code == 400
assert c.post("/monitor/api/checks/../test", headers=H).status_code in (400, 404)
assert c.post("/monitor/api/checks/deadbeef/test", headers=H).status_code == 404

# ── ping (util.run simulado: en Windows no hay ping de Linux) ──
calls = []
real_run = util.run
util.run = lambda cmd, timeout=30, **kw: calls.append((cmd, timeout)) or (0, "64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.42 ms\n", "")
png = add(name="Router", type="ping", target="10.0.0.1", timeout=3)
r = test(png["id"])
assert r["result"] == {**r["result"], "ok": True, "ms": 0.4} and calls == [(["ping", "-c", "1", "-W", "3", "--", "10.0.0.1"], 8)], (r, calls)
util.run = lambda cmd, timeout=30, **kw: (1, "1 packets transmitted, 0 received, 100% packet loss", "")
r = test(png["id"])
assert not r["result"]["ok"] and r["result"]["error"] == "Sin respuesta", r
util.run = real_run

# ── editar, pausar, listar ──
r = c.post(f"/monitor/api/checks/{tcp['id']}", json={**tcp, "target": f"127.0.0.1:{PORT}"}, headers=H).get_json()
assert r["ok"] and row(tcp["id"])["status"] == "pending" and tcp["id"] not in str(M.health_issues())  # destino nuevo: estado reiniciado
assert c.post(f"/monitor/api/checks/{tcp['id']}", json={"name": ""}, headers=H).status_code == 400
assert c.post(f"/monitor/api/checks/{web['id']}/pause", json={"enabled": False}, headers=H).get_json()["ok"]
assert c.post(f"/monitor/api/checks/{web['id']}/pause", json={"enabled": "no"}, headers=H).status_code == 400
assert test(web["id"])["status"] == "paused"
d = c.get("/monitor/api/checks").get_json()
assert d["summary"]["total"] == 8 and d["summary"]["paused"] == 1 and d["worker"] is False, d["summary"]
M._uptime_cache["data"] = None
d = c.get("/monitor/api/checks").get_json()
assert row(web["id"])["uptime"]["d1"] == 100.0 and row(tog["id"])["uptime"]["d30"] == 50.0
assert row(web_bad["id"])["uptime"]["d7"] == 0.0 and row(web["id"])["status"] == "paused"

# ── planificador: tick lanza las activas que tocan, sin duplicar ──
before = M._conn().execute("SELECT COUNT(*) FROM results").fetchone()[0]
M._due.clear()
with ThreadPoolExecutor(4) as pool:
    M.tick(pool)
    M.tick(pool)  # las que siguen en curso o ya programadas no se repiten
assert not M._running
after = M._conn().execute("SELECT COUNT(*) FROM results").fetchone()[0]
assert after - before == 7, (before, after)  # 8 comprobaciones, 1 en pausa (el ping falla sin util.run simulado: da igual)

# ── uptime, retención y estado tras reinicio (motor) ──
now = time.time()
M._conn().execute("INSERT INTO results (t, check_id, ok, ms, error) VALUES (?, ?, 1, 5, '')", (now - 31 * 86400, tog["id"]))
M.prune()
assert M._conn().execute("SELECT COUNT(*) FROM results WHERE t < ?", (now - M.RETENTION,)).fetchone()[0] == 0
SENT.clear()
FLAG["fail"] = True
for _ in range(1):
    test(tog["id"])
M._state.clear(); M._restored = False  # simula reinicio del panel: la caída abierta sigue «down» y no vuelve a avisar
assert row(tog["id"])["status"] == "down" and test(tog["id"])["status"] == "down" and len(SENT) == 1

# ── límite de comprobaciones y borrado ──
M.MAX_CHECKS, old_max = 8, M.MAX_CHECKS
r = c.post("/monitor/api/checks", json={"name": "Una más", "target": BASE}, headers=H)
assert r.status_code == 400 and "Máximo" in r.get_json()["error"]
M.MAX_CHECKS = old_max
assert c.post(f"/monitor/api/checks/{tog['id']}/delete", headers=H).get_json()["ok"]
assert M._conn().execute("SELECT COUNT(*) FROM results WHERE check_id=?", (tog["id"],)).fetchone()[0] == 0
assert c.post(f"/monitor/api/checks/{tog['id']}/delete", headers=H).status_code == 404
assert oct(os.stat(TMPD + "/mon_cfg.json").st_mode)[-3:] in ("600", "666")  # 0600 en Linux; Windows no tiene permisos POSIX
assert c.get("/monitor/").status_code == 200
srv.shutdown()
print("monitor ok")
