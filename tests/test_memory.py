"""Procesos: en qué se usa la memoria (reparto, por programa con consejos y contenedores)."""
import sys, types, os, json
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import processes as P
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True

assert "¿En qué se usa la memoria?" in c.get("/processes/").get_data(as_text=True)
d = c.get("/processes/api/memory").get_json()
o = d["overview"]
assert d["ok"] and o["total"] > 0 and 0 <= o["apps"] <= o["total"] and o["apps"] + o["available"] == o["total"], o
assert d["programs"] and all(p["mem"] >= 0 and p["count"] >= 1 for p in d["programs"])
assert d["programs"] == sorted(d["programs"], key=lambda p: -p["mem"])  # de mayor a menor
assert sum(p["count"] for p in d["programs"]) + d["rest"]["count"] >= len(d["programs"])
assert P.hint("php-fpm8.3").startswith("Cada proceso") and P.hint("mariadbd") and P.hint("xyz") == ""
assert any(p["rss"] > 0 for p in c.get("/processes/api/list").get_json()["processes"])

# contenedores: memoria de docker stats con el proyecto de Compose
P.shutil.which = lambda n: "/usr/bin/docker"
P.run = lambda cmd, timeout=30, **kw: (0, "\n".join(json.dumps(x) for x in [
    {"Name": "matipanel-quetzal-db-1", "MemUsage": "412.5MiB / 15.5GiB", "MemPerc": "2.60%"},
    {"Name": "matipanel-quetzal-app_blue-1", "MemUsage": "1.2GiB / 2GiB", "MemPerc": "60.00%"}]), "") if "stats" in cmd else \
    (0, "matipanel-quetzal-db-1\tmatipanel-quetzal\nmatipanel-quetzal-app_blue-1\tmatipanel-quetzal\n", "")
r = c.get("/processes/api/memory/docker").get_json()
assert r["ok"] and [x["name"] for x in r["containers"]] == ["matipanel-quetzal-app_blue-1", "matipanel-quetzal-db-1"], r
assert r["containers"][0]["limit"] == 2 * 1024 ** 3 and r["containers"][0]["project"] == "matipanel-quetzal" and r["containers"][0]["percent"] == 60.0
print("memory ok")
