"""Prueba: fw."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ["MATIPANEL_PREFS"] = TMPD + "/mpprefs.json"
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import firewall
from app import distro

state = {"active": False, "calls": []}
ADDED = """Added user rules (see 'ufw status' for running firewall):
ufw allow 22/tcp
ufw allow from 10.0.0.0/8 to any port 80 proto tcp comment 'Web interna'
"""
NUMBERED = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere
[ 2] 80/tcp                     ALLOW IN    10.0.0.0/8                 # Web interna
"""
def fake_run(cmd, timeout=30, input=None, env=None):
    state["calls"].append(cmd)
    if cmd[:3] == ["ufw", "status", "verbose"]:
        return 0, ("Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n" if state["active"] else "Status: inactive\n"), ""
    if cmd[:3] == ["ufw", "status", "numbered"]:
        return 0, NUMBERED, ""
    if cmd[:3] == ["ufw", "show", "added"]:
        return 0, ADDED, ""
    return 0, "ok", ""
firewall.run = fake_run
firewall._ufw_file_defaults = lambda: {"incoming": "deny", "outgoing": "allow"}
fake_detect = lambda: {"fw": "ufw", "pkg": "apt", "family": "debian", "name": "Debian"}
fake_detect.cache_clear = lambda: None
firewall.detect = fake_detect

app = create_app(); c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s: s["_user_id"] = "admin"

r = c.get("/firewall/api/status").json
assert r["engine"] == "ufw" and not r["active"] and len(r["rules"]) == 2, r
assert r["rules"][1]["to"] == "80/tcp" and r["rules"][1]["from"] == "10.0.0.0/8" and r["rules"][1]["comment"] == "Web interna", r["rules"][1]
spec = r["rules"][1]["spec"]
r = c.post("/firewall/api/ufw/delete", json={"spec": spec})
assert r.status_code == 200 and state["calls"][-1] == ["ufw", "--force", "delete", "allow", "from", "10.0.0.0/8", "to", "any", "port", "80", "proto", "tcp", "comment", "Web interna"], state["calls"][-1]
r = c.post("/firewall/api/ufw/delete", json={"spec": "allow 9999/tcp; rm -rf /"})
assert r.status_code == 400, r.json
state["active"] = True
r = c.get("/firewall/api/status").json
assert r["active"] and [x["num"] for x in r["rules"]] == [1, 2] and r["defaults"]["incoming"] == "deny", r
r = c.post("/firewall/api/ufw/rule", json={"action": "allow", "proto": "tcp", "port": "443", "from": "", "comment": "HTTPS (web)"})
assert r.status_code == 200 and state["calls"][-1] == ["ufw", "allow", "proto", "tcp", "from", "any", "to", "any", "port", "443", "comment", "HTTPS (web)"], state["calls"][-1]
firewall.apt_get = lambda *a, **k: (True, "instalado", "")
assert c.post("/firewall/api/ufw/install").status_code == 200
assert c.get("/firewall/").status_code == 200
print("firewall ok")
