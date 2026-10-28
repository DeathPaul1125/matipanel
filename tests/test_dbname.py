"""Despliegues: elegir el nombre de la base al crear y usar una base externa (se escribe en el .env)."""
import sys, types, os, json
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json",
                   "MATIPANEL_APPS": TMPD + "/apps"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app, deployer as D
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}


def draft(name, kind="laravel"):
    D._ensure_dir(D.BASE_DIR); D._ensure_dir(D.app_dir(name))
    D.save_config(name, {"name": name, "repo": "https://github.com/o/r", "branch": "main", "php": "8.3", "hostname": "",
                         "port": 8090, "slot_base": 20100, "container_port": 8080, "auto_deploy": False, "migrate": True,
                         "worker": False, "scheduler": False, "database": "none", "redis": False, "mem_mb": 0, "cpus": 0,
                         "php_ext": [], "draft": True, "kind": kind, "docroot": "", "env_path": ".env", "persist": [],
                         "migrate_cmd": "", "active_slot": None, "slots": {"blue": None, "green": None}})
    D.save_secrets(name, {"webhook_secret": "x"})


def create(name, **body):
    return c.post(f"/despliegues/api/apps/{name}/create", json={"branch": "main", **body}, headers=H)


# Laravel con MariaDB propia y nombre elegido: base y usuario en db.env y .env
draft("erpmayoreo")
r = create("erpmayoreo", database="mariadb", db_name="erp_mayoreo")
assert r.status_code == 200, r.get_json()
cfg = D.load_config("erpmayoreo")
assert cfg["db_name"] == "erp_mayoreo" and D.db_name(cfg) == "erp_mayoreo" and not cfg.get("ext_db")
env = dict(D.parse_env(D.read_env("erpmayoreo"))[0])
dbenv = dict(D.parse_env(D.read_text(D.app_dir("erpmayoreo") / "db.env"))[0])
assert env["DB_DATABASE"] == env["DB_USERNAME"] == "erp_mayoreo" and env["DB_HOST"] == "db"
assert dbenv["MARIADB_DATABASE"] == dbenv["MARIADB_USER"] == "erp_mayoreo"
compose = json.loads(D.read_text(D.app_dir("erpmayoreo") / "compose.yaml").split("\n", 1)[1])
assert all("host.docker.internal:host-gateway" in s.get("extra_hosts", []) for n, s in compose["services"].items() if n != "db") or \
    "extra_hosts" not in json.dumps(compose["services"].get("db", {}))

# nombres inválidos o del sistema: se rechazan
for bad in ("Mayúsculas", "1abc", "mysql", "a-b", "x" * 40, "a;drop"):
    draft("bad1")
    r = create("bad1", database="postgres", db_name=bad)
    assert r.status_code == 400 and "Nombre de la base" in r.get_json()["error"], bad

# PHP (Quetzal) con base externa en este mismo servidor: se rellenan sus propias claves
draft("quetzal2", kind="php")
D.write_env("quetzal2", "DB_HOST=localhost\nDB_NAME=\nDB_USER=\nDB_PASS=\nLDB_HOST=localhost\n")
r = create("quetzal2", database="none", kind="php",
           ext_db={"engine": "mysql", "host": "host.docker.internal", "port": "3306", "name": "quetzal", "user": "qz", "password": "s3c,r=t"})
assert r.status_code == 200, r.get_json()
cfg = D.load_config("quetzal2")
assert cfg["ext_db"] == {"engine": "mysql", "host": "host.docker.internal", "port": 3306, "name": "quetzal", "user": "qz"}
assert "password" not in json.dumps(cfg) and D.load_secrets("quetzal2")["ext_db_password"] == "s3c,r=t"
env = dict(D.parse_env(D.read_env("quetzal2"))[0])
assert env.get("DB_HOST") == "host.docker.internal" and env.get("DB_DATABASE") == "quetzal" and env.get("DB_PASSWORD") == "s3c,r=t", env
# en el primer despliegue, el .env.example con otros nombres también se rellena
merged = D.env_set("DB_HOST=localhost\nDB_NAME=x\nDB_USER=root\nDB_PASS=\n", {})
upd = D.service_env(cfg, D.load_secrets("quetzal2"), merged, ext=True)
assert upd == {"DB_HOST": "host.docker.internal", "DB_NAME": "quetzal", "DB_USER": "qz", "DB_PASS": "s3c,r=t"}, upd
assert D.service_env(cfg, D.load_secrets("quetzal2"), merged) == {}  # después manda lo que se edite en el .env

# Laravel con PostgreSQL externa
draft("rep")
r = create("rep", database="none", ext_db={"engine": "pgsql", "host": "10.0.0.20", "name": "reportes", "user": "rep", "password": "p"})
env = dict(D.parse_env(D.read_env("rep"))[0])
assert r.status_code == 200 and env["DB_CONNECTION"] == "pgsql" and env["DB_PORT"] == "5432" and env["QUEUE_CONNECTION"] == "database"
for bad in ({"host": "a b"}, {"host": "h", "port": 70000, "name": "n", "user": "u"}, {"host": "h", "name": "", "user": "u"},
            {"host": "h", "name": "n", "user": "u", "engine": "oracle"}, {"host": "h", "name": "n", "user": "u", "password": "a\nb=c"}):
    draft("bad2")
    assert create("bad2", database="none", ext_db=bad).status_code == 400, bad
draft("sinbase")
assert create("sinbase", database="none", ext_db={"host": ""}).status_code == 200 and not D.load_config("sinbase").get("ext_db")
print("dbname ok")
