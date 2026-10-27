"""Prueba: estilo de la interfaz (Clásico / Moderno) por usuario."""
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
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}

assert 'data-skin="clasico"' in c.get("/apariencia/").get_data(as_text=True)  # predeterminado
r = c.post("/apariencia/api", json={"skin": "moderno", "accent": "#465FFF", "sidebar": "light"}, headers=H).get_json()
assert r["ok"] and r["prefs"]["skin"] == "moderno", r
html = c.get("/").get_data(as_text=True)
assert 'data-skin="moderno"' in html and "--accent:#465FFF" in html
assert json.load(open(TMPD + "/p.json"))["admin"]["skin"] == "moderno"
r = c.post("/apariencia/api", json={"skin": '"><script>'}, headers=H).get_json()  # fuera de la lista blanca
assert r["prefs"]["skin"] == "clasico", r
css = app.test_client().get("/static/css/style.css"); assert b'html[data-skin="moderno"]' in css.data; css.close()
print("skin ok")
