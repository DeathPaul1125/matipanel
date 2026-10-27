"""Prueba: favicon (SVG, PNG, ICO) en todas las páginas, también sin sesión."""
import sys, types, os, struct
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
anon = app.test_client()

r = anon.get("/favicon.ico")  # sin sesión
assert r.status_code == 200 and r.mimetype == "image/x-icon", (r.status_code, r.mimetype)
data = r.data
reserved, kind, count = struct.unpack("<HHH", data[:6])
assert (reserved, kind) == (0, 1) and count == 3  # cabecera ICO con 16, 32 y 48 px
sizes = sorted(data[6 + 16 * i] for i in range(count))
assert sizes == [16, 32, 48], sizes
first_off = struct.unpack("<I", data[6 + 12:6 + 16])[0]
assert data[first_off:first_off + 8] == b"\x89PNG\r\n\x1a\n"  # imágenes PNG embebidas
r.close()
for f in ("favicon.svg", "favicon-32.png", "apple-touch-icon.png", "icon-192.png"):
    r = anon.get("/static/" + f)
    assert r.status_code == 200, f
    r.close()
assert b"<svg" in anon.get("/static/favicon.svg").data

assert "favicon.svg" in anon.get("/login").get_data(as_text=True)
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
html = c.get("/acerca").get_data(as_text=True)
assert "favicon.svg" in html and "apple-touch-icon.png" in html and "🐧" not in html
print("favicon ok")
