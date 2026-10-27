"""Prueba: test_2fa."""
import sys, types, os, re, json
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
STORE = TMPD + "/mp2fa_test.json"
if os.path.exists(STORE):
    os.remove(STORE)
os.environ["MATIPANEL_2FA_STORE"] = STORE; os.environ["MATIPANEL_PREFS"] = TMPD + "/mpprefs.json"
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U if n == "admin" else (_ for _ in ()).throw(KeyError(n))
pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
for name, mod in {"pwd": pwd, "grp": grp}.items(): sys.modules[name] = mod
for name in ("pty", "termios", "fcntl", "pam"): sys.modules[name] = types.ModuleType(name)
sys.modules["pam"].pam = lambda: types.SimpleNamespace(authenticate=lambda u, p, service: p == "ok")
import pyotp
from app import create_app
from app import totp
app = create_app(); app.config["TESTING"] = True

def logged():
    c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
    with c.session_transaction() as s:
        s["_user_id"] = "admin"; s["_fresh"] = True
    return c

c = logged()
# 3. setup + código incorrecto -> misma página, mismo secret y códigos
r = c.get("/security/2fa/setup"); html = r.get_data(as_text=True)
secret = re.search(r'word-break:break-all;">([A-Z2-7]+)<', html).group(1)
codes = re.findall(r'name="codes" value="([0-9a-f]{8})"', html)
assert len(codes) == 8, codes
r = c.post("/security/2fa/activate", data={"code": "000000", "codes": codes + ["deadbeef"]})
html = r.get_data(as_text=True)
assert r.status_code == 400 and secret in html and "Código inválido" in html, r.status_code
assert re.findall(r'name="codes" value="([0-9a-f]{8})"', html) == codes  # el inventado se descarta
assert totp.pending_secret("admin") == secret
r = c.post("/security/2fa/activate", data={"code": pyotp.TOTP(secret).now()})
assert r.status_code == 302 and totp.is_enabled("admin")
print("activate retry ok")

# 2. desactivar sin código / con código erróneo -> sigue activo
c.post("/security/2fa/disable", data={})
c.post("/security/2fa/disable", data={"code": "123456"})
assert totp.is_enabled("admin")
print("disable needs code ok")

# 4/5. login con recovery -> mensaje con restantes; sesión permanente
c2 = app.test_client(); c2.environ_base['HTTP_ORIGIN'] = 'http://localhost'; c2.environ_base["REMOTE_ADDR"] = "10.0.0.50"
r = c2.post("/login", data={"username": "admin", "password": "ok"})
assert r.status_code == 302 and "/login/2fa" in r.headers["Location"]
r = c2.post("/login/2fa", data={"code": codes[0]}, follow_redirects=False)
assert r.status_code == 302, r.status_code
with c2.session_transaction() as s:
    assert s.permanent and s.get("_user_id") == "admin"
    flashes = s.get("_flashes")
assert any("te quedan 7" in m and "desactiva y vuelve a activar" in m for _, m in flashes), flashes
assert totp.recovery_remaining("admin") == 7
print("recovery msg + permanent ok")

# 2. desactivar con código de recuperación -> desactivado
r = c.post("/security/2fa/disable", data={"code": codes[1]})
assert not totp.is_enabled("admin")
print("disable with recovery ok")

# 5. login sin 2FA -> permanente
c3 = app.test_client(); c3.environ_base['HTTP_ORIGIN'] = 'http://localhost'; c3.environ_base["REMOTE_ADDR"] = "10.0.0.51"
assert c3.post("/login", data={"username": "admin", "password": "ok"}).status_code == 302
with c3.session_transaction() as s:
    assert s.permanent
print("password-only permanent ok")

# 1. almacén corrupto -> login bloqueado (500) y el archivo no se toca
open(STORE, "w").write("{corrupto")
c4 = app.test_client(); c4.environ_base['HTTP_ORIGIN'] = 'http://localhost'; c4.environ_base["REMOTE_ADDR"] = "10.0.0.52"
r = c4.post("/login", data={"username": "admin", "password": "ok"})
assert r.status_code == 500 and "No se pudo leer la configuración de 2FA" in r.get_data(as_text=True), r.status_code
with c4.session_transaction() as s:
    assert not s.get("_user_id")
r = c.get("/security"); assert r.status_code == 500 and "2fa.json" in r.get_data(as_text=True)
r = c.get("/security/2fa/setup"); assert r.status_code == 500
r = c.post("/security/2fa/disable", data={"code": "x"}); assert r.status_code == 500
assert open(STORE).read() == "{corrupto"
# también si el JSON es válido pero no es un objeto
open(STORE, "w").write("[]")
r = app.test_client().post("/login", data={"username": "admin", "password": "ok"}, environ_base={"REMOTE_ADDR": "10.0.0.53", "HTTP_ORIGIN": "http://localhost"})
assert r.status_code == 500
os.remove(STORE)
print("fail closed ok")
