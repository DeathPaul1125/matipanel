"""Archivos de contenedores: rutas, listado, montajes y que la ruta nunca entra al texto del script (Windows)."""
import sys, types, os
sys.path.insert(0, sys.argv[1])
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")
os.environ["MATIPANEL_PREFS"] = TMPD + "/mpprefs.json"
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"):
    sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import cfiles as CF

# Rutas
assert CF.norm("/var/www/html/../html/./storage") == "/var/www/html/storage"
assert CF.norm("//etc") == "/etc" and CF.norm("/") == "/"
assert CF.norm("relativa") is None and CF.norm("/a\x00b") is None and CF.norm("/a\nb") is None and CF.norm(None) is None
assert CF.is_protected("/") and CF.is_protected("/etc") and not CF.is_protected("/var/www/html/storage")

# Listado de stat (Debian y busybox escriben «regular file» / «regular empty file»)
out = ("d|directory|4096|1700000000|755|www-data|www-data|storage\n"
       "-|regular file|120|1700000001|644|www-data|www-data|.env\n"
       "-|regular empty file|0|1700000002|644|root|root|con | barra\n"
       "d|symbolic link|11|1700000003|777|root|root|public\n"
       "-|symbolic link|9|1700000004|777|root|root|roto\n"
       "basura sin campos\n")
e = {x["name"]: x for x in CF.parse_ls(out)}
assert list(e) == ["public", "storage", ".env", "con | barra", "roto"], list(e)  # carpetas (y enlaces a carpeta) primero
assert e["storage"]["kind"] == "dir" and e["storage"]["is_dir"] and e[".env"]["kind"] == "file" and e[".env"]["size"] == 120
assert e["public"]["kind"] == "link" and e["public"]["is_dir"] and not e["roto"]["is_dir"] and e["con | barra"]["kind"] == "file"

# Montajes: el más específico gana
mounts = CF.parse_mounts('[{"Type":"volume","Name":"matipanel-rrhh_storage","Destination":"/var/www/html/storage","RW":true},'
                         '{"Type":"bind","Source":"/var/lib/matipanel/apps/rrhh/x","Destination":"/var/www/html/storage/app/x"}]')
assert CF.mount_of("/var/www/html/storage/logs", mounts)["name"] == "matipanel-rrhh_storage"
assert CF.mount_of("/var/www/html/storage/app/x/a.pdf", mounts)["type"] == "bind"
assert CF.mount_of("/var/www/html/app", mounts) is None and CF.mount_of("/var/www/html/storagex", mounts) is None
assert CF.parse_mounts("no json") == []

# Con Docker simulado: contenedor validado y la ruta siempre como argumento ($1)
calls = []
CF.running_containers = lambda: [{"name": "matipanel-rrhh-app_blue-1", "project": "matipanel-rrhh", "image": "x"}]


class R:
    def __init__(self, rc=0, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def fake_run(cmd, **kw):
    calls.append(cmd)
    if cmd[:2] == ["docker", "inspect"]:
        return R(0, "[]", "")
    if "stat -c '%Y-%s'" in cmd[cmd.index("-c") + 1] and "head -c" in cmd[cmd.index("-c") + 1]:
        return R(0, b"1700000000-5\nhola\n")
    return R(0, out.encode())


CF.subprocess.run = fake_run
app = create_app(); c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s:
    s["_user_id"] = "admin"
evil = "/var/www/html/$(rm -rf /);x"
r = c.get("/archivos-contenedores/api/ls", query_string={"c": "matipanel-rrhh-app_blue-1", "path": evil})
assert r.status_code == 200, r.get_json()
ex = calls[-2]  # el docker exec (el último es docker inspect)
assert ex[:5] == ["docker", "exec", "-u", "0", "matipanel-rrhh-app_blue-1"] and ex[-1] == evil and evil not in ex[-3]
assert c.get("/archivos-contenedores/api/ls", query_string={"c": "otro; rm -rf /", "path": "/"}).status_code == 404
r = c.get("/archivos-contenedores/api/read", query_string={"c": "matipanel-rrhh-app_blue-1", "path": "/x.txt"}).get_json()
assert r["ok"] and r["content"] == "hola\n" and r["version"] == "1700000000-5", r
r = c.post("/archivos-contenedores/api/delete", json={"c": "matipanel-rrhh-app_blue-1", "path": "/etc"})
assert r.status_code == 400 and "sistema" in r.get_json()["error"]
r = c.post("/archivos-contenedores/api/create", json={"c": "matipanel-rrhh-app_blue-1", "path": "/tmp", "name": "../x"})
assert r.status_code == 400
assert c.get("/archivos-contenedores/").status_code == 200
print("cfiles ok")
