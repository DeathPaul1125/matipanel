"""Despliegues: asistente «Importar» (proyecto en /var/www) y API de HTTPS por app."""
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
from app import create_app, deployer as D, importer as IM
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}

# App ya creada (sin desplegar) con MariaDB propia y Redis
D._ensure_dir(D.BASE_DIR); D._ensure_dir(D.app_dir("erp"))
D.save_config("erp", {"name": "erp", "repo": "https://github.com/o/r", "branch": "main", "php": "8.3", "hostname": "",
                      "port": 8090, "slot_base": 20100, "container_port": 8080, "auto_deploy": False, "migrate": True,
                      "worker": False, "scheduler": False, "database": "mariadb", "db_name": "erp_app", "redis": True,
                      "mem_mb": 0, "cpus": 0, "php_ext": [], "kind": "laravel", "active_slot": None,
                      "slots": {"blue": None, "green": None}})
D.save_secrets("erp", {"db_password": "P" * 32, "db_root_password": "R" * 32})
D.write_env("erp", "APP_NAME=erp\nAPP_KEY=base64:NUEVA\nAPP_ENV=production\nDB_HOST=db\nDB_PASSWORD=" + "P" * 32 + "\n")

# Proyecto viejo en una carpeta «de Apache»
root = TMPD + "/www"
src = root + "/html/miproyecto"
for d in ("storage/app/public", "public/uploads", "public/build"):
    os.makedirs(f"{src}/{d}", exist_ok=True)
open(f"{src}/artisan", "w").write("<?php")
open(f"{src}/webpack.mix.js", "w").write("")
open(f"{src}/package.json", "w").write(json.dumps({"scripts": {"production": "mix --production"}}))
open(f"{src}/composer.json", "w").write(json.dumps({"require": {"php": "^8.1"}}))
open(f"{src}/.env", "w").write("APP_NAME=MiERP\nAPP_KEY=base64:VIEJA\nAPP_DEBUG=true\nDB_HOST=127.0.0.1\n"
                               "DB_DATABASE=erp_viejo\nDB_PASSWORD=secreta\nMAIL_FROM_NAME=\"${APP_NAME}\"\n"
                               "SESSION_DRIVER=file\nMAIL_PASSWORD='pa$$'\n")
IM.ROOTS = (root,)
IM.local_databases = lambda: {"erp_viejo", "mysql"}

# Rutas fuera de las carpetas permitidas o la raíz misma: rechazadas
assert IM.valid_source(TMPD)[0] is None and IM.valid_source(root)[0] is None
assert IM.valid_source(src + "/../../..")[0] is None

r = c.post("/despliegues/api/apps/erp/import/scan", json={"path": src}, headers=H)
assert r.status_code == 200, r.get_json()
s = r.get_json()["scan"]
assert s["laravel"] and s["env"]["has_key"] and s["mix"] and s["php_req"] == "^8.1" and not s["git"]["repo"]
rows = {x["key"]: x["copy"] for x in s["env"]["rows"]}
assert rows["APP_KEY"] and rows["MAIL_FROM_NAME"] and not rows["DB_HOST"] and not rows["APP_DEBUG"] and not rows["SESSION_DRIVER"]
assert s["db"]["importable"] and s["db"]["source"] == "erp_viejo" and s["db"]["target"] == "erp_app"
assert s["storage"]["exists"] and s["uploads"] == ["public/uploads"], s["uploads"]
assert "secreta" not in r.get_data(as_text=True)  # el análisis no devuelve valores del .env

# Sin desplegar: base y archivos no; el .env sí
r = c.post("/despliegues/api/apps/erp/import", json={"path": src, "steps": ["env", "db"]}, headers=H)
assert r.status_code == 400 and "desplegada" in r.get_json()["error"]
r = c.post("/despliegues/api/apps/erp/import", json={"path": src, "steps": ["env"]}, headers=H)
assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
env = dict(D.parse_env(D.read_env("erp"))[0])
assert env["APP_KEY"] == "base64:VIEJA" and env["APP_NAME"] == "MiERP" and env["MAIL_FROM_NAME"] == "MiERP"
assert env["DB_HOST"] == "db" and env["DB_PASSWORD"] == "P" * 32 and env["APP_ENV"] == "production"
assert env["MAIL_PASSWORD"] == "pa$$" and "SESSION_DRIVER" not in env

# HTTPS: estado; sin nombre ni despliegue no se activa
r = c.get("/despliegues/api/apps/erp/tls")
assert r.status_code == 200 and r.get_json()["mode"] == "" and set(r.get_json()["modes"]) == set(D.TLS_MODES)
r = c.post("/despliegues/api/apps/erp/tls", json={"mode": "self"}, headers=H)
assert r.status_code == 409 and "nombre" in r.get_json()["error"].lower()
r = c.post("/despliegues/api/apps/erp/tls", json={"mode": "raro"}, headers=H)
assert r.status_code == 400
r = c.post("/despliegues/api/apps/erp/tls", json={"mode": "letsencrypt", "email": "no-es-correo"}, headers=H)
assert r.status_code == 400
# Cambiar el nombre de una app con HTTPS lo desactiva (el certificado era del nombre anterior)
D.update_config("erp", lambda cfg: cfg.update(hostname="erp.empresa.com", tls="self"))
r = c.post("/despliegues/api/apps/erp/config", json={"hostname": "otro.empresa.com"}, headers=H)
assert r.status_code == 200 and r.get_json()["tls_off"], r.get_json()
assert D.load_config("erp")["tls"] == "" and D.load_config("erp")["hostname"] == "otro.empresa.com"
print("ok")
