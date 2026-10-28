"""PHP del contenedor: extensiones pedidas por composer, php.ini, paquetes del sistema y estado del contenedor."""
import sys, types, os, json
from pathlib import Path
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

# composer: el caso de «impresoras» (phpspreadsheet pide ext-gd) más otras que la imagen ya trae
src = Path(TMPD) / "src"
src.mkdir(exist_ok=True)
(src / "composer.json").write_text(json.dumps({"require": {"php": "^8.2", "ext-intl": "*", "laravel/framework": "^11"}}))
(src / "composer.lock").write_text(json.dumps({"packages": [
    {"name": "phpoffice/phpspreadsheet", "require": {"ext-gd": "*", "ext-zip": "*", "ext-xml": "*", "ext-ctype": "*", "ext-zlib": "*"}},
    {"name": "maatwebsite/excel", "require": {"phpoffice/phpspreadsheet": "^1.30"}},
    {"name": "x/y", "require": {"ext-bcmath": "*", "ext-Zend-OPcache": "*", "ext-bad;rm": "*"}}],
    "packages-dev": [{"name": "dev/only", "require": {"ext-xdebug": "*"}}]}))
assert D.composer_extensions(src) == ["bcmath", "gd", "intl"], D.composer_extensions(src)  # sin las incluidas ni las de desarrollo


class Log:
    lines = []
    def write(self, t): self.lines.append(t)


exts = D._log_exts(Log(), {"php_ext": ["exif"], "apt_pkgs": ["snmp"]}, D.composer_extensions(src))
assert exts == ["bcmath", "exif", "gd", "intl"] and any("pedidas por composer: bcmath, gd, intl" in ln for ln in Log.lines)
df = D.render_dockerfile("8.4", node=False, php_ext=exts, apt=["snmp"])
assert "RUN install-php-extensions bcmath exif gd intl" in df and "install -y --no-install-recommends snmp" in df
assert df.index("install-php-extensions") < df.index("composer install")  # gd está antes de composer install

# configuración: php.ini, paquetes del sistema y validación
D._ensure_dir(D.BASE_DIR); D._ensure_dir(D.app_dir("impresoras"))
D.save_config("impresoras", {"name": "impresoras", "repo": "https://github.com/o/r", "branch": "main", "php": "8.4", "hostname": "",
                             "port": 8091, "slot_base": 20200, "container_port": 8080, "auto_deploy": False, "migrate": True,
                             "worker": False, "scheduler": False, "database": "none", "redis": False, "mem_mb": 0, "cpus": 0,
                             "php_ext": [], "draft": False, "kind": "laravel", "active_slot": "blue",
                             "slots": {"blue": "matipanel/impresoras:abc1234", "green": None}, "current": {"image": "matipanel/impresoras:abc1234"}})
D.write_env("impresoras", "APP_KEY=x\n")
r = c.post("/despliegues/api/apps/impresoras/config", json={"php_ini": {"memory_limit": "512M", "upload": "64M",
           "max_execution_time": "300", "timezone": "America/Guatemala"}, "apt_pkgs": "snmp iputils-ping"}, headers=H).get_json()
assert r["ok"] and r["needs_apply"] and r["needs_deploy"], r  # php.ini: aplicar; paquetes: desplegar
cfg = D.load_config("impresoras")
assert cfg["apt_pkgs"] == ["snmp", "iputils-ping"] and cfg["php_ini"]["timezone"] == "America/Guatemala"
compose = json.loads(D.read_text(D.app_dir("impresoras") / "compose.yaml").split("\n", 1)[1])
env = compose["services"]["app_blue"]["environment"]
assert env == {"PHP_MEMORY_LIMIT": "512M", "PHP_UPLOAD_MAX_FILE_SIZE": "64M", "PHP_POST_MAX_SIZE": "64M",
               "PHP_MAX_EXECUTION_TIME": "300", "PHP_DATE_TIMEZONE": "America/Guatemala"}, env
for bad in ({"php_ini": {"memory_limit": "512MB; rm"}}, {"php_ini": {"timezone": "Mars/Base;x"}}, {"php_ini": {"upload": "-5"}},
            {"apt_pkgs": "snmp; rm -rf /"}, {"apt_pkgs": "-y"}, {"apt_pkgs": " ".join(f"p{i}x" for i in range(21))}):
    r = c.post("/despliegues/api/apps/impresoras/config", json=bad, headers=H)
    assert r.status_code == 400, (bad, r.get_json())

# el PHP del contenedor en marcha
real_run = D._run
D._run = lambda cmd, timeout=60, **kw: (0, "matipanel-impresoras-app_blue-1\n", "") if cmd[:2] == ["docker", "ps"] else \
    (0, "8.4.14\nCore,date,gd,intl,PDO,Zend OPcache\nmemory_limit=512M\nupload_max_filesize=64M\npost_max_size=64M\n"
        "max_execution_time=300\ndate.timezone=America/Guatemala\nopcache.enable=1\n", "") if cmd[:2] == ["docker", "exec"] else real_run(cmd, timeout)
d = c.get("/despliegues/api/apps/impresoras/php").get_json()
assert d["ok"] and d["version"] == "8.4.14" and "gd" in d["extensions"] and d["ini"]["memory_limit"] == "512M" and d["container"].endswith("app_blue-1"), d
assert "PHP del contenedor" in c.get("/despliegues/app/impresoras").get_data(as_text=True)
print("php_container ok")
