"""Prueba: docker_names."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/dnprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/dn2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_DOCKER_NAMES": TMPD + "/dn_names.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import docker as DK
app = create_app(); c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}
post = lambda body: c.post("/docker/api/name", json=body, headers=H)
assert post({"kind": "groups", "key": "matipanel-quetzal", "label": "  Quetzal   prod "}).get_json()["ok"]
assert DK.load_names()["groups"]["matipanel-quetzal"] == "Quetzal prod"
for bad in ({"kind": "x", "key": "a", "label": "b"}, {"kind": "groups", "key": "../etc", "label": "b"},
            {"kind": "groups", "key": "a", "label": "x" * 61}, {"kind": "containers", "key": "a", "label": "a\u0000b"}):
    assert post(bad).status_code == 400, bad
assert post({"kind": "groups", "key": "matipanel-quetzal", "label": ""}).get_json()["ok"]
assert "matipanel-quetzal" not in DK.load_names()["groups"]
assert DK.parse_labels("a=1,b=x=y,c") == {"a": "1", "b": "x=y"}
d = DK.describe({"Names": "n"}, {"com.docker.compose.service": "app_green", "matipanel.role": "app"})
assert d["role_label"] == "Aplicación (green)"
print("docker names ok")
