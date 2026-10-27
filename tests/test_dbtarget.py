"""Explorador BD con la base de una app desplegada: comandos docker exec y validación de ?srv=."""
import sys, types, os, json, shutil
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
APPS = TMPD + "/dbtapps"
shutil.rmtree(APPS, ignore_errors=True)
os.environ.update({"MATIPANEL_PREFS": TMPD + "/dtprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/dt2fa.json",
                   "MATIPANEL_METRICS": ":memory:", "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_APPS": APPS})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from flask import g
from app import create_app
from app import deployer as D
from app.modules import databases as DB, dbexplorer as DX
app = create_app()

base = {"repo": "git@github.com:a/b.git", "branch": "main", "php": "8.4", "hostname": "", "port": 8081, "slot_base": 20000,
        "container_port": 80, "draft": False, "slots": {}, "active_slot": "blue"}
for name, db, extra in (("quetzal", "mariadb", {"kind": "php"}), ("pgapp", "postgres", {"db_name": "app"}),
                        ("sinbd", "none", {}), ("borrador", "mariadb", {"draft": True})):
    os.makedirs(f"{APPS}/{name}")
    D.save_config(name, {**base, "name": name, "database": db, **extra})

assert DB.app_db_target("quetzal") == {"app": "quetzal", "engine": "mysql", "container": "matipanel-quetzal-db-1", "user": "laravel"}
assert DB.app_db_target("pgapp")["engine"] == "pg" and DB.app_db_target("pgapp")["user"] == "app"
assert DB.app_db_target("sinbd") is None and DB.app_db_target("borrador") is None and DB.app_db_target("../x") is None

with app.test_request_context("/bd/api/mysql/dbs?srv=quetzal"):
    g.db_target = DB.app_db_target("quetzal")
    cmd = DX._client("mysql", "laravel")
    assert cmd[:4] == ["docker", "exec", "-i", "matipanel-quetzal-db-1"] and "--sandbox" in cmd and "--database=laravel" in cmd, cmd
    assert 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot "$@"' in cmd and not any("P" * 8 in c for c in cmd)
    assert DB._dump_cmd("mysql", "laravel")[:4] == ["docker", "exec", "-i", "matipanel-quetzal-db-1"]
    assert "mariadb-dump" in DB._dump_cmd("mysql", "laravel")[6] and DX._is_mariadb()
with app.test_request_context("/bd/api/pg/dbs?srv=pgapp"):
    g.db_target = DB.app_db_target("pgapp")
    cmd = DX._client("pg", "app")
    assert cmd[:9] == ["docker", "exec", "-i", "-e", "PGOPTIONS", "-e", "PGCLIENTENCODING", "matipanel-pgapp-db-1", "psql"], cmd
    assert cmd[9:11] == ["-U", "app"] and DB._dump_cmd("pg", "app")[8] == "pg_dump"
with app.test_request_context("/bd/api/mysql/dbs"):
    assert DX._client("mysql", "x")[0] == "mysql" and DB.db_target() is None  # sin srv: el servidor local

c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
r = c.get("/bd/api/mysql/dbs?srv=sinbd")
assert r.status_code == 404 and "no tiene base" in r.get_json()["error"], r.status_code
r = c.get("/bd/api/pg/dbs?srv=quetzal")
assert r.status_code == 400, r.status_code  # motor distinto al de la app
r = c.get("/bd/")
assert r.status_code == 200 and b'id="srv-sel"' in r.data and "App «quetzal»".encode() in r.data and b"pgapp" in r.data
assert b"sinbd" not in r.data and b"borrador" not in r.data
print("db target ok")
