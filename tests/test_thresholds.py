"""Umbrales del servidor: se guardan con las notificaciones y cambian lo que la barra de estado considera un problema."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json",
                   "MATIPANEL_NOTIFY": TMPD + "/n.json", "MATIPANEL_NOTIFY_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app, thresholds
from app.modules import dashboard
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}
dashboard._failed_units = lambda: []
dashboard.docker_issues = lambda now=None: []

assert thresholds.get() == thresholds.DEFAULTS  # sin configurar: valores por defecto
page = c.get("/notificaciones/").get_data(as_text=True)
assert "Umbrales del servidor" in page and 'data-thr="inodes"' in page and 'data-thr="containers"' in page
now = c.get("/notificaciones/api/config").get_json()["now"]
assert now["partitions"] and "mem" in now and "cores" in now

# umbral de memoria al 1 %: la barra de estado lo marca; discos sin vigilar
mounts = {p["mount"]: {"mode": "off"} for p in now["partitions"]}
thr = {"mem": {"on": True, "warn": 1, "crit": 0, "minutes": 0}, "disk": {"on": True, "warn": 80, "crit": 90},
       "disks": mounts, "email_level": "err"}
r = c.post("/notificaciones/api/config", json={"enabled": False, "to": "", "thresholds": thr}, headers=H).get_json()
assert r["ok"], r
assert r["config"]["thresholds"]["mem"]["warn"] == 1 and r["config"]["thresholds"]["email_level"] == "err"
issues, _ = dashboard.compute_issues()
keys = {k: lv for lv, _, k in issues}
assert keys.get("mem") == "warn" and not any(k.startswith("disk:") for k in keys), issues
assert thresholds.describe(thresholds.get(), "mem") == "aviso 1 %"

# memoria sin vigilar: desaparece; un umbral inválido no se guarda
c.post("/notificaciones/api/config", json={"to": "", "thresholds": {**thr, "mem": {"on": False}}}, headers=H)
assert "mem" not in {k for _, _, k in dashboard.compute_issues()[0]}
r = c.post("/notificaciones/api/config", json={"to": "", "thresholds": {"cpu": {"warn": 95, "crit": 90}}}, headers=H)
assert r.status_code == 400 and "grave debe ser mayor" in r.get_json()["error"]
assert thresholds.get()["mem"]["on"] is False  # lo guardado no cambió
print("thresholds ok")
