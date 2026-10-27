"""Prueba: smoke."""
import sys, types, os, re
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ["MATIPANEL_2FA_STORE"] = TMPD + "/mp2fa.json"; os.environ["MATIPANEL_PREFS"] = TMPD + "/mpprefs.json"
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U if n == "admin" else (_ for _ in ()).throw(KeyError(n))
pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
for name, mod in {"pwd": pwd, "grp": grp}.items(): sys.modules[name] = mod
for name in ("pty", "termios", "fcntl", "pam"): sys.modules[name] = types.ModuleType(name)
from app import create_app
app = create_app(); app.config["TESTING"] = True
c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s:
    s["_user_id"] = "admin"; s["_fresh"] = True
nav = [e for e in re.findall(r"\(['\"](\w+\.\w+)['\"], ['\"]", open(os.path.join(sys.argv[1], "app/templates/base.html"), encoding="utf-8").read()) if e in app.view_functions]
nav += ["auth.security", "appearance.index", "help.page", "dashboard.about"]  # menú de usuario
bad = 0
for ep in nav:
    with app.test_request_context():
        from flask import url_for; url = url_for(ep)
    r = c.get(url)
    if r.status_code != 200:
        bad += 1; print("FAIL", ep, url, r.status_code, r.data[:300])
print(f"{len(nav) - bad}/{len(nav)} páginas OK")
r = c.post("/services/api/action", json={"unit": "x", "action": "start"}, headers={"Origin": "https://evil.com"})
print("CSRF cross-origin ->", r.status_code)
r = c.get("/security/2fa/setup"); print("2fa setup ->", r.status_code)
sys.modules["pam"].pam = lambda: types.SimpleNamespace(authenticate=lambda u, p, service: p == "ok")
c2 = app.test_client(); c2.environ_base['HTTP_ORIGIN'] = 'http://localhost'
print("sin sesión ->", c2.get("/").status_code)
codes = [c2.post("/login", data={"username": "admin", "password": "mal"}).status_code for _ in range(6)]
print("intentos fallidos ->", codes)
c3 = app.test_client(); c3.environ_base['HTTP_ORIGIN'] = 'http://localhost'; c3.environ_base["REMOTE_ADDR"] = "10.0.0.9"
print("login correcto ->", c3.post("/login", data={"username": "admin", "password": "ok"}).status_code)
print("usuario no permitido ->", c3.post("/login", data={"username": "otro", "password": "ok"}).status_code)
c4 = app.test_client()
with c4.session_transaction() as s: s["_user_id"] = "admin"; s["_fresh"] = True
print("POST sin Origin ->", c4.post("/services/api/action", json={"unit": "x", "action": "start"}).status_code)
print("POST con Referer propio ->", c4.post("/apariencia/api", json={}, headers={"Referer": "http://localhost/apariencia/"}).status_code)
