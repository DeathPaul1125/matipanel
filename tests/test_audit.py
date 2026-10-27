"""Registro de auditoría: inicio de sesión, acciones, secretos ocultos, filtros y CSV."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/auprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/au2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_DOCKER_NAMES": TMPD + "/au_names.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl"): sys.modules[n] = types.ModuleType(n)
pam = types.ModuleType("pam"); pam.pam = lambda: types.SimpleNamespace(authenticate=lambda u, p, service: p == "clave")
sys.modules["pam"] = pam
from app import create_app, audit
app = create_app()
c = app.test_client()
H = {"Origin": "http://localhost"}
assert c.post("/login", data={"username": "admin", "password": "mala"}, headers=H).status_code == 401
assert c.post("/login", data={"username": "admin", "password": "clave"}, headers=H).status_code == 302
c.post("/docker/api/name", json={"kind": "groups", "key": "wiki", "label": "Wiki"}, headers=H)
c.post("/trafico/api/alerts", json={"ifaces": {"eth0": {"mbps": 5}}}, headers=H)
c.get("/dashboard") if False else None
ev = audit.query()
acts = [(e["module"], e["action"], e["user"], e["ok"]) for e in ev]
assert ("auth", "login", "admin", False) in acts and ("auth", "login", "admin", True) in acts, acts
assert ("docker", "set name", "admin", True) in acts, acts
login_fail = next(e for e in ev if e["module"] == "auth" and not e["ok"])
assert login_fail["detail"].get("password") == "•••" and "mala" not in str(ev)
dk = next(e for e in ev if e["module"] == "docker")
assert dk["target"] == "wiki" and dk["detail"]["label"] == "Wiki"
assert not any(e["module"] == "jobs" or e["action"].startswith("GET") for e in ev)  # GET normales no se registran
r = c.get("/auditoria/api/events?days=1&module=auth&result=fail").get_json()
assert len(r["events"]) == 1 and r["events"][0]["module_label"] == "Acceso"
r = c.get("/auditoria/api/events?days=1&q=wiki").get_json()
assert [e["module"] for e in r["events"]] == ["docker"]
csv = c.get("/auditoria/api/export?days=1")
assert csv.status_code == 200 and "attachment" in csv.headers["Content-Disposition"]
body = csv.data.decode("utf-8-sig")
assert body.splitlines()[0].startswith("Fecha,Usuario") and "set name" in body
assert c.get("/auditoria/").status_code == 200
print("audit ok")
