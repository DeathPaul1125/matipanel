"""Permisos del panel: administrador, solo lectura, personalizado y sin acceso."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
if os.path.exists(TMPD + "/rl_roles.json"):
    os.unlink(TMPD + "/rl_roles.json")
os.environ.update({"MATIPANEL_PREFS": TMPD + "/rlprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/rl2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/rl_roles.json",
                   "MATIPANEL_DOCKER_NAMES": TMPD + "/rl_names.json", "MATIPANEL_NOTIFY_WORKER": "0", "MATIPANEL_APPBACKUP_WORKER": "0"})
NAMES = ["admin", "ana", "luis", "eva"]
USERS = {n: types.SimpleNamespace(pw_name=n, pw_uid=1000 + i, pw_gid=1000 + i, pw_dir=f"/home/{n}", pw_shell="/bin/bash", pw_gecos="")
         for i, n in enumerate(NAMES)}
pwd = types.ModuleType("pwd")
def _getpwnam(n):
    if n not in USERS:
        raise KeyError(n)
    return USERS[n]
pwd.getpwnam = _getpwnam; pwd.getpwall = lambda: list(USERS.values()); pwd.getpwuid = lambda u: next(x for x in USERS.values() if x.pw_uid == u)
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=list(NAMES))
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: types.SimpleNamespace(gr_name="u" + str(g), gr_gid=g, gr_mem=[]); grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl"): sys.modules[n] = types.ModuleType(n)
pam = types.ModuleType("pam"); pam.pam = lambda: types.SimpleNamespace(authenticate=lambda u, p, service: p == "clave")
sys.modules["pam"] = pam
from app import create_app
app = create_app()
H = {"Origin": "http://localhost"}


def client(user):
    c = app.test_client()
    with c.session_transaction() as ss:
        ss["_user_id"] = user; ss["_fresh"] = True
    return c


adm = client("admin")
# sin configuración: todos son administradores
assert client("ana").get("/terminal/").status_code == 200
r = adm.get("/permisos/api/config").get_json()
assert r["ok"] and {u["user"] for u in r["users"]} == set(NAMES) and all(u["role"] == "admin" for u in r["users"])
# no puede quitarse el rol a sí mismo
r = adm.post("/permisos/api/config", json={"default": "readonly", "users": {}}, headers=H)
assert r.status_code == 400 and "ti mismo" in r.get_json()["error"]
cfg = {"default": "readonly", "users": {"admin": {"role": "admin"}, "luis": {"role": "custom", "modules": ["docker"]},
                                        "eva": {"role": "none"}}}
assert adm.post("/permisos/api/config", json=cfg, headers=H).get_json()["ok"]
assert adm.post("/permisos/api/config", json={"default": "admin", "users": {"x": {"role": "custom", "modules": ["noexiste"]}}},
                headers=H).status_code == 400

ana = client("ana")  # rol por defecto: solo lectura
assert ana.get("/services/").status_code == 200
r = ana.post("/docker/api/name", json={"kind": "groups", "key": "w", "label": "x"}, headers=H)
assert r.status_code == 403 and "solo lectura" in r.get_json()["error"]
assert ana.get("/files/").status_code == 302 and ana.get("/terminal/").status_code == 302
assert ana.get("/files/api/ls?path=/").status_code == 403
assert ana.get("/permisos/").status_code == 302 and ana.get("/permisos/api/config").status_code == 403
assert ana.post("/apariencia/api", json={}, headers=H).status_code != 403  # su apariencia sí
html = ana.get("/services/").get_data(as_text=True)
nav = html.split("<nav")[1].split("</nav>")[0]
assert 'href="/services/"' in nav and 'href="/terminal/"' not in nav and 'href="/files/"' not in nav and 'href="/permisos/"' not in nav

luis = client("luis")  # personalizado: solo Docker
assert luis.get("/docker/").status_code == 200 and luis.get("/services/").status_code == 302
assert luis.post("/docker/api/name", json={"kind": "groups", "key": "w", "label": "x"}, headers=H).get_json()["ok"]
html = luis.get("/docker/").get_data(as_text=True)
nav = html.split("<nav")[1].split("</nav>")[0]
assert 'href="/docker/"' in nav and 'href="/services/"' not in nav and "Bases de datos" not in nav

# sin acceso: no entra y su sesión abierta deja de valer
anon = app.test_client()
assert anon.post("/login", data={"username": "eva", "password": "clave"}, headers=H).status_code == 401
assert client("eva").get("/services/").status_code == 302  # → login
assert "/login" in client("eva").get("/services/").headers["Location"]

assert adm.get("/terminal/").status_code == 200 and adm.get("/permisos/").status_code == 200
print("roles ok")
