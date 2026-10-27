"""Prueba: audit_fix."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/afprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/af2fa.json",
                   "MATIPANEL_METRICS": ":memory:", "MATIPANEL_DEPLOY_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl"): sys.modules[n] = types.ModuleType(n)
pam = types.ModuleType("pam"); pam.pam = lambda: types.SimpleNamespace(authenticate=lambda u, p, service: p == "clave")
sys.modules["pam"] = pam
from app import create_app, auth
from app import deployer as D
app = create_app()
c = app.test_client()
H = {"Origin": "http://localhost"}

# chunked sin sesión -> 411; con Content-Length enorme -> 413
r = c.post("/login", data=b"username=a&password=b", headers={**H, "Transfer-Encoding": "chunked",
           "Content-Type": "application/x-www-form-urlencoded"})
assert r.status_code == 411, r.status_code
r = c.post("/login", data="x" * 70000, headers=H, content_type="text/plain")
assert r.status_code == 413, r.status_code

# el formulario se sigue leyendo bien tras get_data(cache=True); el intento cuenta aunque falten campos
auth.limiter.fails.clear()
for i in range(5):
    r = c.post("/login", data={"username": "admin"}, headers=H)
    assert r.status_code == 400, r.status_code
r = c.post("/login", data={"username": "admin", "password": "clave"}, headers=H)
assert r.status_code == 429, r.status_code  # los 400 contaron
auth.limiter.fails.clear()
r = c.post("/login", data={"username": "admin", "password": "clave"}, headers=H)
assert r.status_code == 302, r.status_code
assert not auth.limiter.fails, auth.limiter.fails  # login correcto reinicia el contador

# webhook: firma no ASCII -> False (antes TypeError/500)
assert not D.verify_signature("s", b"{}", "sha256=\xe9" + "0" * 63)
assert D.GITHUB_KNOWN_HOSTS.count("\n") == 3 and D.GITHUB_KNOWN_HOSTS.startswith("github.com ssh-ed25519 ")
print("audit fixes ok")
from app.modules import deploy as DP
cfg0 = {"name": "quetzal", "port": 8081}
o, e = DP._opts({"kind": "php", "docroot": "/", "env_path": "app/config/.env/", "persist": "assets/uploads app/logs",
                 "migrate_cmd": "php  migrate.php"}, cfg0)
assert not e and o == {"kind": "php", "docroot": "", "env_path": "app/config/.env", "persist": ["assets/uploads", "app/logs"],
                       "migrate_cmd": "php migrate.php"}, (o, e)
for bad in ({"kind": "rails"}, {"docroot": "../etc"}, {"env_path": ""}, {"persist": "a a"}, {"persist": "/etc ../x"},
            {"migrate_cmd": "php x.php; rm -rf /"}, {"migrate_cmd": "sh run.sh"}):
    assert DP._opts(bad, cfg0)[1], bad
print("php kind api ok")
