"""Guarda la configuración de una app Laravel existente cambiándola a PHP, como lo hace la interfaz."""
import sys, types, os, json, shutil
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
APPS = TMPD + "/cfgapps"
shutil.rmtree(APPS, ignore_errors=True)
os.environ.update({"MATIPANEL_PREFS": TMPD + "/csprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/cs2fa.json",
                   "MATIPANEL_METRICS": ":memory:", "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_APPS": APPS})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from flask import session
from app import create_app
from app import deployer as D
app = create_app()


@app.before_request
def _auto():
    session["_user_id"] = "admin"; session["_fresh"] = True


# App creada con la versión anterior del panel: sin kind/docroot/env_path/persist/migrate_cmd
os.makedirs(f"{APPS}/quetzal/deployments")
old = {"name": "quetzal", "repo": "git@github.com:DeathPaul1125/quetzal.git", "branch": "main", "php": "8.3", "hostname": "",
       "port": 8081, "slot_base": 20000, "container_port": 8080, "auto_deploy": True, "migrate": True, "worker": False,
       "scheduler": False, "database": "mariadb", "redis": False, "mem_mb": 0, "cpus": 0, "php_ext": [], "draft": False,
       "created": 1, "active_slot": None, "slots": {"blue": None, "green": None}, "last_build_sha": None, "current": None}
D.save_config("quetzal", old)
D.save_secrets("quetzal", {"webhook_secret": "x" * 48, "db_password": "P" * 32, "db_root_password": "R" * 32})
D.save_history("quetzal", [])
D.write_env("quetzal", "DB_HOST=db\n")
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
body = {"repo": old["repo"], "branch": "main", "php": "8.4", "port": "8081", "container_port": "80", "hostname": "",
        "php_ext": "", "auto_deploy": True, "migrate": True, "worker": False, "scheduler": False, "redis": False,
        "database": "mariadb", "mem_mb": 0, "cpus": 0, "kind": "php", "docroot": "", "env_path": "app/config/.env",
        "persist": "assets/uploads app/logs", "migrate_cmd": "php migrate.php"}
r = c.post("/despliegues/api/apps/quetzal/config", json=body, headers={"Origin": "http://localhost"})
print(r.status_code, r.get_json())
cfg = D.load_config("quetzal")
print({k: cfg.get(k) for k in ("kind", "container_port", "env_path", "persist", "migrate_cmd")})

# ── .env de proyectos PHP: claves *DB_* vacías o en localhost ──
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
assert D.load_config("quetzal")["kind"] == "php"
text = "APP_DEBUG=false\nLDB_ENGINE='mysql'\nLDB_HOST='localhost'\nLDB_NAME='db_quetzal'\nLDB_USER='root'\nLDB_PASS=''\nDB_HOST=db\n"
r = c.post("/despliegues/api/apps/quetzal/env", json={"text": text}, headers={"Origin": "http://localhost"})
assert r.status_code == 200, (r.status_code, r.get_json())
env = dict(D.parse_env(D.read_env("quetzal"))[0])
assert env["LDB_HOST"] == "db" and env["LDB_NAME"] == "laravel" and env["LDB_USER"] == "laravel" and env["LDB_PASS"] == "P" * 32, env
assert env["LDB_ENGINE"] == "mysql" and env["APP_DEBUG"] == "false"
print("env fill ok")
