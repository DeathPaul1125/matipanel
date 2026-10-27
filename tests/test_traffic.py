"""Tráfico de red: API, alertas en la barra de salud y validaciones."""
import sys, types, os, time, shutil
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
shutil.rmtree(TMPD + "/trcaps", ignore_errors=True)
for f in (TMPD + "/tr_cfg.json",):
    if os.path.exists(f):
        os.unlink(f)
os.environ.update({"MATIPANEL_PREFS": TMPD + "/trprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/tr2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_CAPTURES": TMPD + "/trcaps", "MATIPANEL_TRAFFIC": TMPD + "/tr_cfg.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
import psutil
from app import create_app, metrics
from app.modules import traffic as T, dashboard as DB
app = create_app()
metrics.open_db(":memory:")
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}

# muestreo: dos llamadas → velocidades en memoria; una tercera a +30 s → fila en la base
t0 = time.time()
T.sample(t0); T.sample(t0 + 5); T.sample(t0 + 35)
name = next(iter(T._recent))
assert len(T._recent[name]) == 2 and set(T._recent[name][0]) == {"t", "rx", "tx", "rxp", "txp", "errs", "drops"}
assert name in metrics.known_ifaces()

r = c.get("/trafico/api/ifaces").get_json()
assert r["ifaces"] and all(i["name"] != "lo" and not i["name"].startswith("veth") for i in r["ifaces"])
r = c.get("/trafico/api/counters").get_json()
assert r["c"] and len(next(iter(r["c"].values()))) == 6
r = c.get(f"/trafico/api/history?iface={name}&range=3600").get_json()
assert r["points"], r
assert c.get("/trafico/api/history?iface=x;rm&range=3600").status_code == 400
assert c.get(f"/trafico/api/history?iface={name}&range=99").status_code == 400

# alertas: uso sostenido sobre el umbral y errores por minuto
T._recent["eth9"].clear()
for i in range(12):
    T._recent["eth9"].append({"t": t0 + i * 5, "rx": 20e6, "tx": 1e6, "rxp": 1, "txp": 1, "errs": 2, "drops": 50})
assert c.post("/trafico/api/alerts", json={"ifaces": {"eth9": {"mbps": "100", "drops": "1000"}}}, headers=H).get_json()["ok"]
issues = [i[1] for i in T.health_issues()]
assert "eth9 al 160 Mbit/s" in issues and "eth9: 120 errores de red por minuto" in issues, issues
assert "eth9: 3000 paquetes descartados por minuto" in issues, issues
DB._health_cache.update(at=0, data=None)
h = c.get("/api/health").get_json()
assert any("eth9" in m for m in h["issues"]), h
assert c.post("/trafico/api/alerts", json={"ifaces": {"eth9": {"mbps": 0, "errors": 0}}}, headers=H).get_json()["ok"]
assert not [i[1] for i in T.health_issues() if i[1].startswith("eth9") and ("Mbit" in i[1] or "errores" in i[1])]
for bad in ({"ifaces": {"a;b": {}}}, {"ifaces": {"eth0": {"mbps": -1}}}, {"ifaces": {"eth0": {"mbps": "x"}}}, {"ifaces": []}):
    assert c.post("/trafico/api/alerts", json=bad, headers=H).status_code == 400, bad
T._recent.pop("eth9", None)

# capturas: validación (en Windows no hay tcpdump: 409 después de validar)
assert c.post("/trafico/api/capture", json={"iface": "noexiste"}, headers=H).status_code == 400
assert c.post("/trafico/api/capture", json={"iface": name, "seconds": 999}, headers=H).status_code == 400
assert c.get("/trafico/api/top?iface=noexiste&seconds=5").status_code == 400
assert c.get(f"/trafico/api/top?iface={name}&seconds=7").status_code == 400
assert c.get("/trafico/api/captures").get_json() == {"captures": []}
for bad in ("../x.pcap", "x.pcap", "20260101-120000-eth0.pcap"):
    assert c.get("/trafico/api/captures/" + bad).status_code == 404
os.makedirs(TMPD + "/trcaps", exist_ok=True)
open(TMPD + "/trcaps/20261026-120000-eth0.pcap", "wb").write(b"\xd4\xc3\xb2\xa1" + b"\0" * 20)
r = c.get("/trafico/api/captures").get_json()["captures"]
assert r[0]["name"] == "20261026-120000-eth0.pcap" and r[0]["size"] == 24
d = c.get("/trafico/api/captures/20261026-120000-eth0.pcap")
assert d.status_code == 200 and "attachment" in d.headers["Content-Disposition"]
d.close()
assert c.post("/trafico/api/captures/20261026-120000-eth0.pcap/delete", headers=H).get_json()["ok"]
assert not os.path.exists(TMPD + "/trcaps/20261026-120000-eth0.pcap")
assert c.get("/trafico/").status_code == 200
print("traffic ok")
