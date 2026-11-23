"""Volúmenes de Docker: de quién son, tamaño, en uso, y que no se borra uno en uso (Windows, docker simulado)."""
import sys, types, os, json
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
from app import create_app, deployer as D
from app.modules import docker as K, databases as DB

APPS = {"arrendamientos", "recursoshumanos"}
# Dueño
c = K.classify_volume
assert c("matipanel-arrendamientos_postgres_data", {}, APPS) == {"kind": "app", "app": "arrendamientos",
    "owner": "App «arrendamientos» · base de datos (PostgreSQL)"}
assert c("matipanel-viejo_storage", {"com.docker.compose.project": "matipanel-viejo", "com.docker.compose.volume": "storage"},
         APPS)["kind"] == "app_deleted"
assert "eliminada" in c("matipanel-viejo_mariadb_data", {}, APPS)["owner"]
assert c("uptime_data", {"com.docker.compose.project": "uptime"}, APPS)["kind"] == "project"
assert c("a" * 64, {}, APPS)["kind"] == "anonymous" and c("mis-datos", {}, APPS)["kind"] == "other"
assert K.parse_labels("com.docker.compose.project=x,com.docker.compose.volume=storage") == \
    {"com.docker.compose.project": "x", "com.docker.compose.volume": "storage"}

# Informe con docker simulado
DF = json.dumps({"Volumes": [{"Name": "matipanel-arrendamientos_postgres_data", "Links": "1", "Size": "310.5MB"},
                             {"Name": "matipanel-viejo_mariadb_data", "Links": "0", "Size": "4.2GB"},
                             {"Name": "b" * 64, "Links": "0", "Size": "512kB"}]})
LS = "\n".join(json.dumps(v) for v in [
    {"Name": "matipanel-arrendamientos_postgres_data", "Driver": "local", "Mountpoint": "/var/lib/docker/volumes/a/_data",
     "Labels": "com.docker.compose.project=matipanel-arrendamientos,com.docker.compose.volume=postgres_data"},
    {"Name": "matipanel-viejo_mariadb_data", "Driver": "local", "Mountpoint": "/m", "Labels": ""},
    {"Name": "b" * 64, "Driver": "local", "Mountpoint": "/b", "Labels": ""}])
removed, PS_FILTER = [], {"v": ""}


def fake(*args, timeout=15):
    a = list(args)
    if a[:3] == ["system", "df", "-v"]:
        return 0, DF + "\n", ""
    if a[:2] == ["volume", "ls"]:
        return 0, LS + "\n", ""
    if a[:2] == ["ps", "-a"] and "--no-trunc" in a:
        return 0, "matipanel-arrendamientos-db-1\tmatipanel-arrendamientos_postgres_data\n", ""
    if a[:2] == ["ps", "-a"]:
        return 0, PS_FILTER["v"], ""
    if a[0] == "version":
        return 0, "27.3.1\n", ""
    if a[:2] == ["volume", "inspect"]:
        return 0, "null", ""
    if a[:2] == ["volume", "rm"]:
        removed.append(a[-1]); return 0, a[-1], ""
    return 1, "", "no simulado"


K._docker = fake
D.list_apps = lambda: sorted(APPS)
r = K.volume_report()
v = {x["name"]: x for x in r["volumes"]}
assert list(v)[0] == "matipanel-viejo_mariadb_data"  # sin usar y más grande, primero
assert v["matipanel-arrendamientos_postgres_data"]["in_use"] and v["matipanel-arrendamientos_postgres_data"]["used_by"] == ["matipanel-arrendamientos-db-1"]
assert v["matipanel-viejo_mariadb_data"]["size"] == 4_200_000_000 and not v["matipanel-viejo_mariadb_data"]["in_use"]
assert r["unused_count"] == 2 and r["unused_size"] == 4_200_512_000 and r["anon_unused_count"] == 1 and r["can_prune_anon"]

app = create_app(); cl = app.test_client(); cl.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with cl.session_transaction() as s:
    s["_user_id"] = "admin"
URL = "/docker/api/volumes/remove"
assert cl.post(URL, json={"name": "a b; rm"}).status_code == 400
PS_FILTER["v"] = "abc123\n"  # un contenedor lo usa
assert cl.post(URL, json={"name": "matipanel-viejo_mariadb_data"}).status_code == 409 and not removed
PS_FILTER["v"] = ""
r = cl.post(URL, json={"name": "matipanel-arrendamientos_postgres_data"})  # de una app que existe: pide confirmar
assert r.status_code == 409 and r.get_json()["needs_confirm"] and not removed
assert cl.post(URL, json={"name": "matipanel-viejo_mariadb_data"}).get_json()["ok"] and removed == ["matipanel-viejo_mariadb_data"]

# Binlogs del servidor
b = DB.parse_binlogs([["mysql-bin.000001", "1073741824", "No"], ["mysql-bin.000002", "536870912", "No"]],
                     [["binlog_expire_logs_seconds", "864000"], ["expire_logs_days", "10.000000"]])
assert b["count"] == 2 and b["size"] == 1610612736 and b["days"] == 10
assert DB.parse_binlogs([["a", "10"]], [["expire_logs_days", "0"]])["days"] is None

# Bases de las apps: datos y disco por app; si el contenedor no responde, se marca
DB.app_db_target = lambda n: {"app": n, "engine": "pg", "container": f"matipanel-{n}-db-1", "user": n}
DB.shutil.which = lambda x: "/usr/bin/docker"
K.volume_sizes = lambda: {"matipanel-arrendamientos_postgres_data": (310_000_000, 1)}


def fake_exec(engine, sql, db=None, timeout=30):
    t = DB.db_target()
    if t["app"] == "recursoshumanos":
        return 1, [], "Error response from daemon: container is not running"
    return 0, [["16.4"], ["arrendamientos", "UTF8", "", "120000000"], ["postgres", "UTF8", "", "7000000"]], ""


DB._exec = fake_exec
d = cl.get("/databases/api/apps/sizes").get_json()
a = {x["app"]: x for x in d["apps"]}
assert a["arrendamientos"]["data"] == 120_000_000 and a["arrendamientos"]["disk"] == 310_000_000, a
assert a["recursoshumanos"]["data"] is None and a["recursoshumanos"]["error"]
print("docker volumes ok")
