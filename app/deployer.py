"""Motor de despliegues de aplicaciones Laravel (al estilo Coolify), sin Flask.

Cada aplicación vive en /var/lib/matipanel/apps/<nombre>/ (carpeta 0700, archivos 0600):
  app.json        configuración y estado (slot activo, imágenes, commit actual)
  secrets.json    secreto del webhook y contraseñas de la base de datos
  deploy_key(.pub), known_hosts   clave SSH de despliegue (solo lectura en GitHub)
  env             el .env de la aplicación (se pasa al contenedor con env_file; nunca se hornea en la imagen)
  db.env          variables del contenedor de base de datos
  src/            checkout de git (contenido NO confiable: solo se usa como contexto de docker build)
  Dockerfile      generado (si el repositorio no trae uno)
  compose.yaml    generado (JSON, que también es YAML válido)
  history.json    últimos 50 despliegues;  deployments/<id>.log  salida de cada uno

Despliegue sin cortes (blue/green): cada app tiene dos puertos internos (slot_base y slot_base+1,
ligados a 127.0.0.1). La versión nueva arranca en el slot libre, se espera a que responda, se cambia
Nginx (nginx -t + reload) y recién entonces se detiene el slot viejo. Si algo falla antes del cambio,
la versión anterior sigue sirviendo.

Un solo hilo trabajador procesa la cola global (FIFO, persistida en .queue.json): un despliegue a
la vez en todo el servidor. Otro hilo consulta GitHub cada 60 s (git ls-remote) para el auto-despliegue.

ponytail: estado en archivos JSON con un lock en memoria; válido con 1 worker de gunicorn.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

BASE_DIR = Path(os.environ.get("MATIPANEL_APPS", "/var/lib/matipanel/apps"))
NGINX_DIR = Path("/etc/nginx")
APACHE_DIR = Path("/etc/apache2")
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_BINARY = getattr(os, "O_BINARY", 0)

PHP_VERSIONS = ("8.2", "8.3", "8.4")
KINDS = ("laravel", "php")         # php: cualquier framework (Apache + .htaccess), sin artisan
MAX_PERSIST = 10
DATABASES = ("none", "mariadb", "postgres")
PORT_RANGE = (8081, 8999)          # puertos públicos asignados automáticamente
PORT_EDIT_RANGE = (1024, 19999)    # rango permitido al editarlo a mano
SLOT_RANGE = (20000, 29998)        # puertos internos blue/green (127.0.0.1)
HISTORY_MAX = 50
KEEP_IMAGES = 5
BUILD_TIMEOUT = 30 * 60
MIGRATE_TIMEOUT = 10 * 60
HEALTH_TIMEOUT = 90
LOG_HEAD_CAP = 4 * 1024 * 1024     # 4 MB del principio + 1 MB del final = tope de 5 MB por log
LOG_TAIL_CAP = 1024 * 1024
ENV_MAX = 256 * 1024
POLL_EVERY = 60
FINAL = ("success", "failed", "cancelled")
TRIGGERS = ("manual", "poll", "webhook", "rollback", "config")

# ── Validación ──────────────────────────────────────────────────────────────

NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,30}")
REPO_HTTPS_RE = re.compile(r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?")
REPO_SSH_RE = re.compile(r"git@github\.com:([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?")
BRANCH_RE = re.compile(r"[A-Za-z0-9._/-]{1,100}")
LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
ENV_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
DEP_ID_RE = re.compile(r"\d{10,17}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
TAG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
PHP_EXT_RE = re.compile(r"[a-z0-9_]{2,30}")
REL_PATH_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")
MIGRATE_CMD_RE = re.compile(r"php [A-Za-z0-9_./-]+\.php(?: [A-Za-z0-9_=:.,/-]+)*")
ARTISAN_RE = re.compile(r"[a-z:-]+( [A-Za-z0-9_=:.,/-]+)*")
ARTISAN_BLOCKED = ("tinker", "serve", "queue:listen", "queue:work", "schedule:work", "pail", "octane:start",
                   "reverb:start", "horizon")
ARTISAN_COMMON = ("migrate:status", "migrate --force", "cache:clear", "config:clear", "route:clear", "view:clear",
                  "optimize", "optimize:clear", "queue:restart", "queue:failed", "queue:retry all",
                  "storage:link", "schedule:list", "route:list", "about", "event:list")
ARTISAN_DANGER = ("db:wipe", "migrate:fresh", "migrate:reset", "migrate:rollback", "migrate:refresh", "down")


def valid_name(s):
    return isinstance(s, str) and bool(NAME_RE.fullmatch(s)) and not s.endswith("-")


def parse_repo(url):
    """→ ("https"|"ssh", owner, repo) o None."""
    if not isinstance(url, str) or len(url) > 200:
        return None
    for kind, rx in (("https", REPO_HTTPS_RE), ("ssh", REPO_SSH_RE)):
        m = rx.fullmatch(url)
        if m and m.group(2) not in (".", "..") and not m.group(2).endswith(".") and not m.group(1).endswith("-"):
            return kind, m.group(1), m.group(2)
    return None


def is_ssh(url):
    p = parse_repo(url)
    return bool(p) and p[0] == "ssh"


def valid_branch(b):
    """Subconjunto seguro de git check-ref-format."""
    if not isinstance(b, str) or not BRANCH_RE.fullmatch(b):
        return False
    if b.startswith(("-", "/")) or b.endswith(("/", ".", ".lock")) or ".." in b or "//" in b or b == "HEAD":
        return False
    return not any(part.startswith(".") or part.endswith(".lock") for part in b.split("/"))


def valid_hostname(h):
    """RFC 1123, en minúsculas y con al menos un punto (miapp.apps.empresa.local)."""
    if not isinstance(h, str) or not 3 <= len(h) <= 253 or "." not in h:
        return False
    return all(LABEL_RE.fullmatch(lbl) for lbl in h.split(".")) and not h.split(".")[-1].isdigit()


def valid_rel_path(p, max_len=120):
    """Ruta relativa dentro del proyecto (app/config/.env, assets/uploads): sin «..», «.» ni «/» inicial."""
    return (isinstance(p, str) and 0 < len(p) <= max_len and bool(REL_PATH_RE.fullmatch(p))
            and not any(part in (".", "..") for part in p.split("/")))


def valid_migrate_cmd(cmd):
    """Comando previo al cambio de versión en proyectos PHP: «php script.php [args]», sin shell."""
    return (isinstance(cmd, str) and len(cmd) <= 200 and bool(MIGRATE_CMD_RE.fullmatch(cmd))
            and ".." not in cmd and valid_rel_path(cmd.split()[1]))


def is_php(cfg):
    return cfg.get("kind") == "php"


def persist_volumes(cfg):
    """{volumen: ruta} de las carpetas persistentes de un proyecto PHP."""
    out = {}
    for path in cfg.get("persist") or []:
        if valid_rel_path(path):
            out["p_" + re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")[:40] + "_" +
                hashlib.sha1(path.encode()).hexdigest()[:6]] = path
    return out


def db_name(cfg):
    """Nombre de la base y de su usuario. Se fija al crear la app y no sigue al tipo de proyecto:
    MariaDB/PostgreSQL solo lo aplican al inicializar el volumen. Apps antiguas: «laravel»."""
    return cfg.get("db_name") or "laravel"


def valid_artisan(cmd):
    """Comando de artisan permitido (sin 'php artisan'). → (ok, motivo)."""
    if not isinstance(cmd, str) or not 1 <= len(cmd) <= 300 or not ARTISAN_RE.fullmatch(cmd):
        return False, "Comando inválido: solo letras, números y _ = : . , / - separados por espacios"
    first = cmd.split()[0]
    if first in ARTISAN_BLOCKED or first.startswith(("horizon", "octane:", "reverb:")):
        return False, f"«{first}» es interactivo o no termina; no se puede ejecutar desde aquí"
    return True, ""


def is_dangerous_artisan(cmd):
    return cmd.split()[0] in ARTISAN_DANGER if cmd else False


# ── .env ────────────────────────────────────────────────────────────────────

SAFE_VAL_RE = re.compile(r"[A-Za-z0-9_./:@,+\-]*")
SECRET_HINT_RE = re.compile(r"KEY|SECRET|PASS|TOKEN|PRIVATE|CREDENTIAL|AUTH|DSN|_URL$|SALT|HASH")


def _close_quote(s):
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i
        i += 1
    return -1


def _unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(n, "\\" + n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_env(text):
    """Lee un .env. → (pares [(clave, valor)], errores [str]).

    Admite comentarios, 'export ', valores sin comillas (con comentario en línea ' #'),
    comillas simples (literal) y dobles (escapes \\n \\" \\\\ y varias líneas)."""
    pairs, errors = [], []
    lines = str(text).replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        no, s = i + 1, lines[i].strip()
        i += 1
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            errors.append(f"Línea {no}: falta «=»")
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            errors.append(f"Línea {no}: nombre inválido «{key[:40]}» (MAYÚSCULAS, números y _)")
            continue
        val = val.lstrip()
        rest = ""
        if val.startswith('"'):
            acc = val[1:]
            end = _close_quote(acc)
            while end < 0 and i < len(lines):
                acc += "\n" + lines[i]
                i += 1
                end = _close_quote(acc)
            if end < 0:
                errors.append(f"Línea {no}: comillas dobles sin cerrar")
                continue
            rest = acc[end + 1:].strip()
            val = _unescape(acc[:end])
        elif val.startswith("'"):
            end = val.find("'", 1)
            if end < 0:
                errors.append(f"Línea {no}: comilla simple sin cerrar (no se permiten varias líneas)")
                continue
            rest = val[end + 1:].strip()
            val = val[1:end]
        else:
            val = re.split(r"\s#", val, maxsplit=1)[0].strip()
        if rest and not rest.startswith("#"):
            errors.append(f"Línea {no}: texto después de las comillas")
            continue
        pairs.append((key, val))
    return pairs, errors


def quote_env(v):
    if SAFE_VAL_RE.fullmatch(v):
        return v
    if "\n" in v or "'" in v or "${" in v:
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return "'" + v + "'"


def validate_pairs(pairs):
    errors, seen = [], set()
    for k, v in pairs:
        if not ENV_KEY_RE.fullmatch(str(k)):
            errors.append(f"Nombre inválido «{str(k)[:40]}» (MAYÚSCULAS, números y _, sin empezar por número)")
        elif k in seen:
            errors.append(f"«{k}» está repetida")
        elif not isinstance(v, str) or "\r" in v or "\0" in v:
            errors.append(f"Valor inválido en «{k}»")
        seen.add(k)
    return errors


def serialize_env(pairs):
    return "".join(f"{k}={quote_env(v)}\n" for k, v in pairs)


def env_set(text, updates):
    """Cambia o agrega claves en un .env conservando el resto (comentarios incluidos)."""
    out, done = [], set()
    for line in str(text).replace("\r\n", "\n").split("\n"):
        m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            if m.group(1) in done:
                continue
            line = f"{m.group(1)}={quote_env(updates[m.group(1)])}"
            done.add(m.group(1))
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    out += [f"{k}={quote_env(v)}" for k, v in updates.items() if k not in done]
    return "\n".join(out) + "\n"


class Redactor:
    """Tapa valores secretos en la salida que se guarda en los logs de despliegue."""

    def __init__(self, values=()):
        self.values = sorted({v for v in values if isinstance(v, str) and len(v) >= 6}, key=len, reverse=True)

    def __call__(self, text):
        for v in self.values:
            if v in text:
                text = text.replace(v, "••••••")
        return text


def secret_values_from_env(pairs):
    return [v for k, v in pairs if SECRET_HINT_RE.search(k)]


# ── Generadores (Dockerfile, compose, nginx) ────────────────────────────────

DOCKERIGNORE = """# Generado por MatiPanel (el repositorio no trae .dockerignore)
.git
.env
.env.*
node_modules
vendor
public/hot
public/storage
storage/logs/*
storage/framework/cache/data/*
storage/framework/sessions/*
storage/framework/views/*
bootstrap/cache/*.php
"""


def render_dockerfile(php, node=False, npm_lock=False, php_ext=()):
    """Dockerfile de producción para Laravel sobre serversideup/php (Nginx + PHP-FPM en :8080, www-data).

    El .env NO entra en la imagen: se pasa al contenedor con env_file. Al arrancar, las
    automatizaciones de la imagen (AUTORUN_ENABLED) hacen storage:link y config/route/view cache;
    las migraciones se desactivan aquí porque el panel las ejecuta antes de cambiar de versión."""
    if php not in PHP_VERSIONS:
        raise ValueError("versión de PHP no soportada")
    exts = [e for e in php_ext if PHP_EXT_RE.fullmatch(e)]
    lines = [
        "# Generado por MatiPanel. Para personalizarlo, agrega un Dockerfile a la raíz del repositorio.",
        f"FROM serversideup/php:{php}-fpm-nginx AS base",
        "ENV AUTORUN_ENABLED=true \\",
        "    AUTORUN_LARAVEL_MIGRATION=false \\",
        "    PHP_OPCACHE_ENABLE=1",
    ]
    if exts:
        lines += ["USER root", "RUN install-php-extensions " + " ".join(exts), "USER www-data"]
    lines += [
        "WORKDIR /var/www/html",
        "COPY --chown=www-data:www-data composer.json composer.lock* ./",
        "RUN composer install --no-dev --no-interaction --no-progress --prefer-dist --no-scripts --no-autoloader",
        "COPY --chown=www-data:www-data . .",
        "RUN mkdir -p storage/framework/cache/data storage/framework/sessions storage/framework/views \\",
        "        storage/logs storage/app/public bootstrap/cache \\",
        " && composer install --no-dev --no-interaction --no-progress --prefer-dist --optimize-autoloader \\",
        " && chmod -R ug+rwX storage bootstrap/cache",
    ]
    if node:
        lines += [
            "",
            "FROM node:22-alpine AS assets",
            "WORKDIR /app",
            "COPY package.json package-lock.json* ./",
            "RUN " + ("npm ci" if npm_lock else "npm install") + " --no-audit --no-fund",
            "COPY . .",
            "COPY --from=base /var/www/html/vendor ./vendor",
            "RUN npm run build",
            "",
            "FROM base",
            "COPY --from=assets --chown=www-data:www-data /app/public /var/www/html/public",
        ]
    return "\n".join(lines) + "\n"


DOCKERIGNORE_PHP = """# Generado por MatiPanel (el repositorio no trae .dockerignore)
.git
.env
**/.env
node_modules
"""


def find_composer_dir(src):
    """Carpeta (relativa) con composer.json: la raíz o una subcarpeta de primer nivel (p. ej. app/)."""
    src = Path(src)
    if lstat_kind(src / "composer.json") == "file":
        return ""
    try:
        subs = sorted(e.name for e in os.scandir(src) if e.is_dir(follow_symlinks=False) and not e.name.startswith("."))
    except OSError:
        return None
    for sub in subs:
        if valid_rel_path(sub) and lstat_kind(src / sub / "composer.json") == "file":
            return sub
    return None


def render_dockerfile_php(php, php_ext=(), composer_dir=None):
    """Dockerfile para PHP genérico sobre serversideup/php con Apache (respeta .htaccess, :8080, www-data).

    La carpeta pública se fija con APACHE_DOCUMENT_ROOT en compose (cambiarla no obliga a recompilar)."""
    if php not in PHP_VERSIONS:
        raise ValueError("versión de PHP no soportada")
    if composer_dir is not None and composer_dir != "" and not valid_rel_path(composer_dir):
        raise ValueError("carpeta de composer inválida")
    exts = [e for e in php_ext if PHP_EXT_RE.fullmatch(e)]
    lines = [
        "# Generado por MatiPanel. Para personalizarlo, agrega un Dockerfile a la raíz del repositorio.",
        f"FROM serversideup/php:{php}-fpm-apache",
        "ENV PHP_OPCACHE_ENABLE=1",
    ]
    if exts:
        lines += ["USER root", "RUN install-php-extensions " + " ".join(exts), "USER www-data"]
    lines += ["WORKDIR /var/www/html", "COPY --chown=www-data:www-data . ."]
    if composer_dir is not None:
        lines.append(("RUN cd " + composer_dir + " && " if composer_dir else "RUN ")
                     + "composer install --no-dev --no-interaction --no-progress --prefer-dist --optimize-autoloader")
    return "\n".join(lines) + "\n"


def slot_port(cfg, slot):
    return int(cfg["slot_base"]) + (0 if slot == "blue" else 1)


def other_slot(slot):
    return "green" if slot == "blue" else "blue"


def project_name(name):
    return f"matipanel-{name}"


def app_services(cfg):
    """Servicios de la app (sin dependencias) que deben correr con el slot activo."""
    svcs = []
    if cfg.get("active_slot"):
        svcs.append("app_" + cfg["active_slot"])
    if cfg.get("worker"):
        svcs.append("worker")
    if cfg.get("scheduler"):
        svcs.append("scheduler")
    return svcs


def dep_services(cfg):
    return (["db"] if cfg.get("database") in ("mariadb", "postgres") else []) + (["redis"] if cfg.get("redis") else [])


def render_compose(cfg, appdir, slots=None):
    """compose.yaml en formato JSON (YAML 1.2 lo acepta tal cual; sin riesgo de inyección)."""
    name, appdir = cfg["name"], str(appdir)
    slots = slots if slots is not None else cfg.get("slots") or {}
    env_file = [f"{appdir}/env"]
    deps = {}
    php_kind = is_php(cfg)
    persist = persist_volumes(cfg) if php_kind else {}
    services, volumes = {}, ({k: {} for k in persist} if php_kind else {"storage": {}})
    dbn = db_name(cfg)
    if cfg.get("database") == "mariadb":
        services["db"] = {
            "image": "mariadb:11", "restart": "unless-stopped", "env_file": [f"{appdir}/db.env"],
            "volumes": ["mariadb_data:/var/lib/mysql"],
            "healthcheck": {"test": ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"],
                            "interval": "5s", "timeout": "5s", "retries": 30, "start_period": "20s"},
            "labels": {"matipanel.app": name, "matipanel.role": "db"},
        }
        volumes["mariadb_data"] = {}
        deps["db"] = {"condition": "service_healthy"}
    elif cfg.get("database") == "postgres":
        services["db"] = {
            "image": "postgres:16", "restart": "unless-stopped", "env_file": [f"{appdir}/db.env"],
            "volumes": ["postgres_data:/var/lib/postgresql/data"],
            "healthcheck": {"test": ["CMD", "pg_isready", "-U", dbn, "-d", dbn],
                            "interval": "5s", "timeout": "5s", "retries": 30, "start_period": "20s"},
            "labels": {"matipanel.app": name, "matipanel.role": "db"},
        }
        volumes["postgres_data"] = {}
        deps["db"] = {"condition": "service_healthy"}
    if cfg.get("redis"):
        services["redis"] = {
            "image": "redis:7-alpine", "restart": "unless-stopped",
            "command": ["redis-server", "--appendonly", "yes"], "volumes": ["redis_data:/data"],
            "healthcheck": {"test": ["CMD", "redis-cli", "ping"], "interval": "5s", "timeout": "3s", "retries": 30},
            "labels": {"matipanel.app": name, "matipanel.role": "redis"},
        }
        volumes["redis_data"] = {}
        deps["redis"] = {"condition": "service_healthy"}

    limits = {}
    if cfg.get("mem_mb"):
        limits["memory"] = f"{int(cfg['mem_mb'])}M"
    if cfg.get("cpus"):
        limits["cpus"] = f"{float(cfg['cpus']):g}"

    if php_kind:
        app_vols = [f"{v}:/var/www/html/{path}" for v, path in persist.items()]
        env_path = cfg.get("env_path") or ".env"
        if valid_rel_path(env_path):  # el .env como archivo, donde el framework lo busca (solo lectura)
            app_vols.append(f"{appdir}/env.mount:/var/www/html/{env_path}:ro")
        docroot = cfg.get("docroot") or ""
        webroot = "/var/www/html" + (f"/{docroot}" if docroot and valid_rel_path(docroot) else "")
    else:
        app_vols = ["storage:/var/www/html/storage"]

    def base(image, role):
        svc = {"image": image, "restart": "unless-stopped", "env_file": env_file,
               "volumes": list(app_vols), "stop_grace_period": "30s",
               "labels": {"matipanel.app": name, "matipanel.role": role}}
        if php_kind:
            svc["environment"] = {"APACHE_DOCUMENT_ROOT": webroot}
        if deps:
            svc["depends_on"] = dict(deps)
        if limits:
            svc["deploy"] = {"resources": {"limits": dict(limits)}}
        return svc

    cport = int(cfg.get("container_port") or 8080)
    for slot in ("blue", "green"):
        if slots.get(slot):
            svc = base(slots[slot], "app")
            svc["ports"] = [f"127.0.0.1:{slot_port(cfg, slot)}:{cport}"]
            services["app_" + slot] = svc
    active = slots.get(cfg.get("active_slot") or "")
    # El HEALTHCHECK de la imagen prueba HTTP; worker y programador no sirven HTTP.
    if active and cfg.get("worker") and not php_kind:
        svc = base(active, "worker")
        svc.update(command=["php", "/var/www/html/artisan", "queue:work", "--tries=3", "--max-time=3600"],
                   stop_signal="SIGTERM", healthcheck={"disable": True})
        services["worker"] = svc
    if active and cfg.get("scheduler") and not php_kind:
        svc = base(active, "scheduler")
        svc.update(command=["php", "/var/www/html/artisan", "schedule:work"], stop_signal="SIGTERM",
                   healthcheck={"disable": True})
        services["scheduler"] = svc
    doc = {"name": project_name(name), "services": services, "volumes": volumes}
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if "$" in text:  # compose interpolaría ${...}; los valores validados nunca lo contienen
        raise ValueError("valor inesperado en compose")
    return f"# Generado por MatiPanel para «{name}». No lo edites: se reescribe en cada despliegue.\n{text}\n"


def render_nginx(cfg, upstream_port):
    name, port = cfg["name"], int(cfg["port"])
    var = "$mp_conn_" + name.replace("-", "_")
    loc = (f"    client_max_body_size 100m;\n\n"
           f"    location / {{\n"
           f"        proxy_pass http://127.0.0.1:{int(upstream_port)};\n"
           f"        proxy_http_version 1.1;\n"
           f"        proxy_set_header Host $http_host;\n"
           f"        proxy_set_header X-Real-IP $remote_addr;\n"
           f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
           f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
           f"        proxy_set_header X-Forwarded-Host $host;\n"
           f"        proxy_set_header X-Forwarded-Port $server_port;\n"
           f"        proxy_set_header Upgrade $http_upgrade;\n"
           f"        proxy_set_header Connection {var};\n"
           f"        proxy_read_timeout 300s;\n"
           f"        proxy_send_timeout 300s;\n"
           f"    }}\n")
    out = (f"# Generado por MatiPanel (Despliegues) para «{name}». No lo edites: se reescribe en cada despliegue.\n"
           f"map $http_upgrade {var} {{\n    default upgrade;\n    ''      close;\n}}\n\n"
           f"server {{\n    listen {port};\n    server_name _;\n{loc}}}\n")
    host = cfg.get("hostname") or ""
    if host:
        if not valid_hostname(host):
            raise ValueError("hostname inválido")
        out += f"\nserver {{\n    listen 80;\n    server_name {host};\n{loc}}}\n"
    return out


def allocate_port(used, lo=PORT_RANGE[0], hi=PORT_RANGE[1]):
    for p in range(lo, hi + 1):
        if p not in used:
            return p
    return None


def allocate_slot_base(used_bases, listening):
    for b in range(SLOT_RANGE[0], SLOT_RANGE[1] + 1, 2):
        if b not in used_bases and b not in listening and b + 1 not in listening:
            return b
    return None


def sign_body(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


SIG_RE = re.compile(r"sha256=[0-9a-f]{64}")


def verify_signature(secret, body, header):
    # El formato se valida antes: compare_digest lanza TypeError con caracteres no ASCII (500)
    if not secret or not isinstance(header, str) or not SIG_RE.fullmatch(header.strip()):
        return False
    return hmac.compare_digest(sign_body(secret, body), header.strip())


def new_app_key():
    return "base64:" + base64.b64encode(secrets.token_bytes(32)).decode()


def new_password(n=32):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


DB_ALIAS_RE = re.compile(r"[A-Z]*DB_(HOST|PORT|NAME|DATABASE|USER|USERNAME|PASS|PASSWORD)")


def service_env(cfg, sec, current=""):
    """Variables que el panel inyecta en el .env según los servicios activos.

    En proyectos PHP cada framework usa sus nombres (Quetzal: DB_NAME/DB_USER/DB_PASS y LDB_* para
    la consola): se rellenan todas las claves *DB_HOST, *DB_NAME… que ya tenga el .env."""
    if is_php(cfg) and cfg.get("database") in ("mariadb", "postgres"):
        vals = {"HOST": "db", "PORT": "3306" if cfg["database"] == "mariadb" else "5432",
                "NAME": db_name(cfg), "DATABASE": db_name(cfg), "USER": db_name(cfg), "USERNAME": db_name(cfg),
                "PASS": sec.get("db_password", ""), "PASSWORD": sec.get("db_password", "")}
        keys = [k for k, _ in parse_env(current)[0] if DB_ALIAS_RE.fullmatch(k)]
        upd = {k: vals[DB_ALIAS_RE.fullmatch(k).group(1)] for k in keys}
        if not upd:
            upd = {f"DB_{k}": vals[k] for k in ("HOST", "PORT", "DATABASE", "USERNAME", "PASSWORD")}
        if cfg.get("redis"):
            upd.update(REDIS_HOST="redis", REDIS_PORT="6379")
        return upd
    upd = {}
    if cfg.get("database") == "mariadb":
        upd.update(DB_CONNECTION="mysql", DB_HOST="db", DB_PORT="3306")
    elif cfg.get("database") == "postgres":
        upd.update(DB_CONNECTION="pgsql", DB_HOST="db", DB_PORT="5432")
    if cfg.get("database") in ("mariadb", "postgres"):
        upd.update(DB_DATABASE=db_name(cfg), DB_USERNAME=db_name(cfg), DB_PASSWORD=sec.get("db_password", ""))
    if cfg.get("redis"):
        upd.update(REDIS_HOST="redis", REDIS_PORT="6379", REDIS_CLIENT="phpredis")
    return upd


def fill_db_placeholders(cfg, sec, text):
    """Proyecto PHP con base del panel: rellena las claves *DB_* vacías o que apuntan a localhost
    (dentro del contenedor localhost nunca es la base). Respeta los valores puestos a mano."""
    if not is_php(cfg) or cfg.get("database") not in ("mariadb", "postgres"):
        return text
    current = dict(parse_env(text)[0])
    hosts = {k for k, v in current.items() if DB_ALIAS_RE.fullmatch(k) and k.endswith("HOST")
             and v in ("localhost", "127.0.0.1")}
    prefixes = {k[:-4] for k in hosts}  # LDB_HOST=localhost → todas las LDB_* van al contenedor
    upd = {k: v for k, v in service_env(cfg, sec, text).items()
           if k in current and (current[k] == "" or any(k.startswith(px) for px in prefixes))}
    return env_set(text, upd) if upd else text


def default_env(cfg, sec):
    if is_php(cfg):  # se completa en el primer despliegue con el .env.example del repositorio, si trae
        return env_set("# .env de la aplicación (se monta en el contenedor como archivo)\n", service_env(cfg, sec))
    url = f"http://{cfg['hostname']}" if cfg.get("hostname") else f"http://localhost:{cfg['port']}"
    redis, db = cfg.get("redis"), cfg.get("database") in ("mariadb", "postgres")
    pairs = [("APP_NAME", cfg["name"]), ("APP_ENV", "production"), ("APP_KEY", new_app_key()),
             ("APP_DEBUG", "false"), ("APP_URL", url), ("LOG_CHANNEL", "stderr"), ("LOG_LEVEL", "info"),
             ("SESSION_DRIVER", "redis" if redis else "file"),
             ("CACHE_STORE", "redis" if redis else "file"), ("CACHE_DRIVER", "redis" if redis else "file"),
             ("QUEUE_CONNECTION", "redis" if redis else "database" if db else "sync")]
    return env_set(serialize_env(pairs), service_env(cfg, sec))


def db_env(cfg, sec):
    if cfg.get("database") == "mariadb":
        pairs = [("MARIADB_DATABASE", db_name(cfg)), ("MARIADB_USER", db_name(cfg)),
                 ("MARIADB_PASSWORD", sec["db_password"]), ("MARIADB_ROOT_PASSWORD", sec["db_root_password"])]
    elif cfg.get("database") == "postgres":
        pairs = [("POSTGRES_DB", db_name(cfg)), ("POSTGRES_USER", db_name(cfg)), ("POSTGRES_PASSWORD", sec["db_password"])]
    else:
        pairs = []
    return serialize_env(pairs)


# ── Archivos ────────────────────────────────────────────────────────────────

_lock = threading.RLock()


def app_dir(name):
    if not valid_name(name):
        raise ValueError("nombre inválido")
    return BASE_DIR / name


def _ensure_dir(path, mode=0o700):
    path = Path(path)
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise OSError(f"{path} no es una carpeta real")
    os.chmod(path, mode)


def write_file(path, data, mode=0o600):
    """Escritura atómica que nunca sigue un enlace simbólico (tmp con O_EXCL|O_NOFOLLOW + rename)."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_text(path, limit=8 * 1024 * 1024):
    """Lee un archivo regular sin seguir enlaces. None si no existe o no es regular."""
    try:
        fd = os.open(path, os.O_RDONLY | O_NOFOLLOW | O_BINARY)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        return f.read(limit).decode("utf-8", "replace")


def _read_json(path, default):
    text = read_text(path)
    if text is None:
        return default
    try:
        data = json.loads(text)
    except ValueError:
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path, data):
    write_file(path, json.dumps(data, indent=2, ensure_ascii=False))


def lstat_kind(path):
    """'file', 'link', 'dir', 'other' o None (no existe). No sigue enlaces."""
    try:
        m = os.lstat(path).st_mode
    except OSError:
        return None
    return "link" if stat.S_ISLNK(m) else "file" if stat.S_ISREG(m) else "dir" if stat.S_ISDIR(m) else "other"


def list_apps():
    try:
        names = os.listdir(BASE_DIR)
    except OSError:
        return []
    return sorted(n for n in names if valid_name(n) and lstat_kind(BASE_DIR / n / "app.json") == "file")


def load_config(name):
    return _read_json(app_dir(name) / "app.json", {}) or None


def save_config(name, cfg):
    _write_json(app_dir(name) / "app.json", cfg)


def update_config(name, fn):
    with _lock:
        cfg = load_config(name)
        if cfg is None:
            raise KeyError(name)
        fn(cfg)
        save_config(name, cfg)
        return cfg


def load_secrets(name):
    return _read_json(app_dir(name) / "secrets.json", {})


def save_secrets(name, sec):
    _write_json(app_dir(name) / "secrets.json", sec)


def load_history(name):
    return [h for h in _read_json(app_dir(name) / "history.json", []) if isinstance(h, dict)]


def save_history(name, hist):
    _write_json(app_dir(name) / "history.json", hist)


def read_env(name):
    return read_text(app_dir(name) / "env", ENV_MAX) or ""


def write_env(name, text):
    write_file(app_dir(name) / "env", text)


def log_path(name, dep_id):
    if not DEP_ID_RE.fullmatch(str(dep_id)):
        raise ValueError("id inválido")
    return app_dir(name) / "deployments" / f"{dep_id}.log"


def public_key(name):
    return (read_text(app_dir(name) / "deploy_key.pub", 4096) or "").strip()


def redactor_for(name):
    sec = load_secrets(name)
    pairs, _ = parse_env(read_env(name))
    return Redactor(secret_values_from_env(pairs) + [v for v in sec.values() if isinstance(v, str)])


# ── Ejecución de comandos ───────────────────────────────────────────────────

_current = {"app": None, "id": None, "proc": None, "cancel": False}


class Fail(Exception):
    pass


class Cancelled(Exception):
    pass


def _run(cmd, timeout=60, env=None, cwd=None, input=None):
    """Comando SIN shell. → (código, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                           input=input, env=env, cwd=cwd, stdin=None if input is not None else subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} no está instalado"
    except subprocess.TimeoutExpired:
        return 124, "", "Tiempo de espera agotado"


def _kill(p):
    try:
        if hasattr(os, "killpg"):
            os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
    except OSError:
        pass


def _stream(cmd, write, timeout, env=None, cwd=None):
    """Ejecuta mostrando la salida línea a línea en write(). Se puede cancelar. → código
    (124 = tiempo agotado, 130 = cancelado)."""
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             env=env, cwd=cwd, start_new_session=True)
    except OSError as e:
        write(f"No se pudo ejecutar {cmd[0]}: {e}\n")
        return 127
    _current["proc"] = p
    expired = []
    timer = threading.Timer(timeout, lambda: (expired.append(1), _kill(p)))
    timer.start()
    try:
        while True:
            line = p.stdout.readline(65536)
            if not line:
                break
            write(line.decode("utf-8", "replace"))
        code = p.wait()
    finally:
        timer.cancel()
        _current["proc"] = None
        p.stdout.close()
    if _current.get("cancel"):
        return 130
    return 124 if expired else code


def _run_capped(cmd, cap=1024 * 1024, timeout=20):
    """Salida combinada con tope de bytes (logs de contenedores pueden ser enormes)."""
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=True)
    except OSError as e:
        return 127, str(e)
    timer = threading.Timer(timeout, _kill, [p])
    timer.start()
    try:
        data = p.stdout.read(cap + 1)
        if len(data) > cap:
            _kill(p)
        p.stdout.close()
        code = p.wait()
    finally:
        timer.cancel()
    text = data[-cap:].decode("utf-8", "replace")
    return code, (f"[… salida recortada a {cap // 1024} KB …]\n" + text) if len(data) > cap else text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _http_status(url, timeout=5):
    """Código HTTP (sin seguir redirecciones ni usar proxies) o None si no responde."""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def listening_ports():
    code, out, _ = _run(["ss", "-ltnH"], timeout=10)
    ports = set()
    if code == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and ":" in parts[3]:
                p = parts[3].rsplit(":", 1)[1]
                if p.isdigit():
                    ports.add(int(p))
    return ports


def port_users(port):
    """Programas que escuchan en el puerto TCP (p. ej. {"apache2"}); vacío si nadie."""
    code, out, _ = _run(["ss", "-ltnpH", f"sport = :{int(port)}"], timeout=10)
    return set(re.findall(r'\(\("([^"]+)"', out)) if code == 0 else set()


def _wait_listening(port, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port in listening_ports():
            return True
        time.sleep(0.5)
    return False


_compose_cache = {"cmd": None, "at": 0.0}


def compose_cmd():
    """['docker','compose'] (plugin v2) o ['docker-compose'] (binario v2). None si no hay."""
    if _compose_cache["cmd"] and time.time() - _compose_cache["at"] < 300:
        return _compose_cache["cmd"]
    cmd = None
    if _run(["docker", "compose", "version"], timeout=15)[0] == 0:
        cmd = ["docker", "compose"]
    elif shutil.which("docker-compose") and _run(["docker-compose", "version"], timeout=15)[0] == 0:
        cmd = ["docker-compose"]
    _compose_cache.update(cmd=cmd, at=time.time())
    return cmd


def compose(name, *args):
    base = compose_cmd()
    if not base:
        raise Fail("Docker Compose no está instalado")
    d = app_dir(name)
    return [*base, "-p", project_name(name), "-f", str(d / "compose.yaml"), "--project-directory", str(d), *args]


def git_env(name, cfg):
    d = app_dir(name)
    env = {"PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
           "HOME": str(d), "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull}
    if is_ssh(cfg["repo"]):
        env["GIT_SSH_COMMAND"] = " ".join(shlex.quote(a) for a in [
            "ssh", "-F", "/dev/null", "-i", str(d / "deploy_key"), "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={d / 'known_hosts'}",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"])
    return env


def ls_remote(name, cfg, branch=None):
    """→ (ok, {rama: sha} | mensaje de error)."""
    args = ["git", "ls-remote", "--heads", "--", cfg["repo"]]
    if branch:
        args.append(f"refs/heads/{branch}")
    code, out, err = _run(args, timeout=40, env=git_env(name, cfg))
    if code != 0:
        msg = (err or out).strip().splitlines()
        hint = ""
        if "Permission denied" in (err or "") or "Repository not found" in (err or ""):
            hint = (" — revisa que la clave de despliegue esté agregada en GitHub (Settings → Deploy keys)"
                    if is_ssh(cfg["repo"]) else " — si el repositorio es privado, usa la URL SSH (git@github.com:…)")
        return False, ((msg[-1] if msg else f"git terminó con código {code}") + hint)[:500]
    heads = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and SHA_RE.fullmatch(parts[0]) and parts[1].startswith("refs/heads/"):
            heads[parts[1][11:]] = parts[0]
    return True, heads


def generate_key(name):
    d = app_dir(name)
    for f in ("deploy_key", "deploy_key.pub"):
        try:
            os.unlink(d / f)
        except FileNotFoundError:
            pass
    code, out, err = _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"matipanel-{name}",
                           "-f", str(d / "deploy_key")], timeout=30)
    if code != 0:
        raise OSError((err or out).strip() or "ssh-keygen falló")
    for f in ("deploy_key", "deploy_key.pub"):
        os.chmod(d / f, 0o600)
    write_file(d / "known_hosts", GITHUB_KNOWN_HOSTS)


# Claves de host publicadas por GitHub (https://api.github.com/meta, "ssh_keys"): el primer clone
# no confía a ciegas en quien responda como github.com. Otros hosts siguen con accept-new.
GITHUB_KNOWN_HOSTS = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=\n"
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=\n")


# ── Nginx ───────────────────────────────────────────────────────────────────

def nginx_paths(name):
    fname = f"matipanel-app-{name}.conf"
    return NGINX_DIR / "sites-available" / fname, NGINX_DIR / "sites-enabled" / fname


def apache_paths(name):
    fname = f"matipanel-app-{name}.conf"
    return APACHE_DIR / "sites-available" / fname, APACHE_DIR / "sites-enabled" / fname


def render_apache_proxy(cfg):
    """VirtualHost de Apache para el nombre interno: reenvía al puerto propio de la app (que atiende
    Nginx). Así conviven el Apache del servidor en el 80 y las apps desplegadas."""
    host, port = cfg.get("hostname") or "", int(cfg["port"])
    if not valid_hostname(host):
        raise ValueError("hostname inválido")
    return (f"# Generado por MatiPanel (Despliegues) para «{cfg['name']}»: Apache tiene el puerto 80, así que\n"
            f"# reenvía el nombre interno al puerto {port} de la app. No lo edites: se reescribe en cada despliegue.\n"
            f"<VirtualHost *:80>\n"
            f"    ServerName {host}\n"
            f"    ProxyPreserveHost On\n"
            f"    ProxyRequests Off\n"
            f"    ProxyPass / http://127.0.0.1:{port}/ upgrade=websocket\n"
            f"    ProxyPassReverse / http://127.0.0.1:{port}/\n"
            f"    ProxyTimeout 300\n"
            f"</VirtualHost>\n")


def apply_apache_host(name, cfg):
    """Escribe y activa el sitio de Apache (proxy y proxy_http se activan si hace falta). → (ok, mensaje)."""
    avail, link = apache_paths(name)
    if lstat_kind(avail.parent) != "dir" or lstat_kind(link.parent) != "dir":
        return False, "Apache ocupa el puerto 80 pero no tiene /etc/apache2/sites-available (¿no es Debian/Ubuntu?)"
    if lstat_kind(avail) not in (None, "file") or lstat_kind(link) not in (None, "link"):
        return False, f"{avail} o {link} no son lo que el panel espera; revísalos a mano"
    text = render_apache_proxy(cfg)
    old = read_text(avail) if lstat_kind(avail) == "file" else None
    if old == text and lstat_kind(link) == "link":
        return True, "Apache ya reenvía el nombre interno"
    code, o, e = _run(["a2enmod", "-q", "proxy", "proxy_http"], timeout=60)
    if code != 0:
        return False, "No se pudieron activar los módulos proxy de Apache: " + (o + e).strip()[-500:]
    write_file(avail, text, 0o644)
    made_link = lstat_kind(link) is None
    if made_link:
        os.symlink(f"../sites-available/{avail.name}", str(link))

    def restore():
        if old is not None:
            write_file(avail, old, 0o644)
        else:
            os.unlink(avail)
        if made_link:
            os.unlink(link)

    code, o, e = _run(["apache2ctl", "configtest"], timeout=30)
    if code != 0:
        restore()
        return False, "apache2ctl configtest falló; se dejó todo como estaba:\n" + (o + e).strip()[-1500:]
    code, o, e = _run(["systemctl", "reload", "apache2"], timeout=60)
    if code != 0:
        restore()
        _run(["systemctl", "reload", "apache2"], timeout=60)
        return False, "No se pudo recargar Apache: " + (o + e).strip()[-500:]
    return True, f"Apache reenvía {cfg['hostname']} al puerto {cfg['port']}"


def remove_apache_host(name):
    avail, link = apache_paths(name)
    changed = False
    if lstat_kind(link) == "link":
        os.unlink(link)
        changed = True
    if lstat_kind(avail) == "file":
        os.unlink(avail)
        changed = True
    if changed and _run(["apache2ctl", "configtest"], timeout=30)[0] == 0:
        _run(["systemctl", "reload", "apache2"], timeout=60)


def apply_nginx(name, cfg, upstream_port):
    """Escribe el sitio de la app, valida con nginx -t y recarga. Si falla, deja todo como estaba."""
    avail, link = nginx_paths(name)
    if lstat_kind(avail.parent) != "dir" or lstat_kind(link.parent) != "dir":
        return False, "Nginx no está instalado (falta /etc/nginx/sites-available o sites-enabled)"
    kind = lstat_kind(avail)
    if kind not in (None, "file"):
        return False, f"{avail} no es un archivo normal; revísalo a mano"
    if lstat_kind(link) not in (None, "link"):
        return False, f"{link} es un archivo real y no un enlace; revísalo a mano"
    notes = []
    if cfg.get("hostname"):
        # El nombre interno se atiende en el puerto 80. Si lo tiene Apache, Apache lo reenvía al puerto de
        # la app; si lo tiene otro programa, no hay forma (el reload de Nginx fallaría sin avisar).
        others = port_users(80) - {"nginx"}
        if others and others <= {"apache2", "httpd"}:
            ok, msg = apply_apache_host(name, cfg)  # antes de tocar Nginx: si falla, nada cambió
            if not ok:
                return False, msg
            notes.append(msg)
            cfg = {**cfg, "hostname": ""}  # Nginx solo atiende el puerto propio
        elif others:
            return False, (f"El puerto 80 lo usa {', '.join(sorted(others))}, y el nombre interno necesita que lo "
                           f"atienda Nginx o Apache. Quita el nombre interno en Configuración (la app seguirá en "
                           f"el puerto {cfg['port']}) o libera el puerto 80.")
        else:
            remove_apache_host(name)
    else:
        remove_apache_host(name)
    old = read_text(avail) if kind == "file" else None
    write_file(avail, render_nginx(cfg, upstream_port), 0o644)
    made_link = False
    if lstat_kind(link) is None:
        os.symlink(str(avail), str(link))
        made_link = True
    def restore():
        if old is not None:
            write_file(avail, old, 0o644)
        else:
            os.unlink(avail)
        if made_link:
            os.unlink(link)

    code, out, err = _run(["nginx", "-t"], timeout=30)
    if code != 0:
        restore()
        return False, "nginx -t falló; se dejó la configuración anterior:\n" + (out + err).strip()
    code, o, e = _run(["systemctl", "reload-or-restart", "nginx"], timeout=60)
    if code != 0:
        # Nginx sigue con la configuración vieja en memoria: el archivo también debe volver atrás
        # para que un reinicio posterior no apunte a un slot que se va a detener.
        restore()
        return False, "No se pudo recargar Nginx; se dejó la configuración anterior: " + (o + e).strip()
    if not _wait_listening(int(cfg["port"])):
        # Un reload con un puerto imposible de abrir no da error: Nginx sigue con la configuración vieja.
        restore()
        _run(["systemctl", "reload", "nginx"], timeout=60)
        _, jlog, _ = _run(["journalctl", "-u", "nginx", "-n", "8", "--no-pager"], timeout=15)
        return False, (f"Nginx no abrió el puerto {cfg['port']}; se dejó la configuración anterior. "
                       f"¿Está activo Nginx y libre ese puerto?\n" + jlog.strip())
    return True, "\n".join([*notes, (out + err).strip()]).strip()


def remove_nginx(name):
    avail, link = nginx_paths(name)
    changed = False
    if lstat_kind(link) == "link":
        os.unlink(link)
        changed = True
    if lstat_kind(avail) == "file":
        os.unlink(avail)
        changed = True
    if changed and _run(["nginx", "-t"], timeout=30)[0] == 0:
        _run(["systemctl", "reload", "nginx"], timeout=60)


# ── Historial y cola ────────────────────────────────────────────────────────

_wakeup = threading.Event()
_last_id = [0]


def _queue_path():
    return BASE_DIR / ".queue.json"


def queue_load():
    return [q for q in _read_json(_queue_path(), []) if isinstance(q, dict)
            and valid_name(q.get("app")) and DEP_ID_RE.fullmatch(str(q.get("id", "")))]


def queue_save(q):
    _ensure_dir(BASE_DIR)
    _write_json(_queue_path(), q)


def _new_id():
    i = max(int(time.time() * 1000), _last_id[0] + 1)
    _last_id[0] = i
    return str(i)


def find_dep(name, dep_id):
    return next((h for h in load_history(name) if h.get("id") == dep_id), None)


def update_dep(name, dep_id, **fields):
    with _lock:
        hist = load_history(name)
        for h in hist:
            if h.get("id") == dep_id:
                h.update(fields)
                save_history(name, hist)
                return h
    return None


def pending(name):
    return [h for h in load_history(name) if h.get("status") in ("queued", "running")]


def enqueue(name, trigger, image=None, rollback_of=None, info=None):
    """Agrega un despliegue a la cola. → (dep, mensaje). Evita duplicados de builds en cola."""
    if trigger not in TRIGGERS:
        raise ValueError("disparador inválido")
    with _lock:
        cfg = load_config(name)
        if cfg is None or cfg.get("draft"):
            raise KeyError(name)
        hist = load_history(name)
        if image is None:
            dup = next((h for h in hist if h.get("status") == "queued" and not h.get("reuse")), None)
            if dup:
                return dup, "Ya había un despliegue en cola para esta aplicación"
        dep = {"id": _new_id(), "trigger": trigger, "status": "queued", "created": time.time(),
               "started": None, "finished": None, "sha": "", "message": "", "author": "",
               "image": image or "", "reuse": bool(image), "error": "", "rollback_of": rollback_of or ""}
        if info:
            dep.update({k: info.get(k, "") for k in ("sha", "message", "author")})
        hist.insert(0, dep)
        keep, drop = [], []
        for h in hist:
            (keep if len(keep) < HISTORY_MAX or h.get("status") in ("queued", "running") else drop).append(h)
        save_history(name, keep)
        for h in drop:
            try:
                os.unlink(log_path(name, h["id"]))
            except (OSError, ValueError):
                pass
        q = queue_load()
        q.append({"app": name, "id": dep["id"]})
        queue_save(q)
    _wakeup.set()
    return dep, ""


def cancel(name, dep_id):
    """Cancela un despliegue en cola o en curso. → (ok, mensaje)."""
    with _lock:
        dep = find_dep(name, dep_id)
        if not dep:
            return False, "Despliegue no encontrado"
        if dep["status"] == "queued":
            queue_save([q for q in queue_load() if not (q["app"] == name and q["id"] == dep_id)])
            update_dep(name, dep_id, status="cancelled", finished=time.time(), error="Cancelado antes de empezar")
            return True, "Cancelado"
        if dep["status"] == "running" and _current["app"] == name and _current["id"] == dep_id:
            _current["cancel"] = True
            p = _current.get("proc")
            if p:
                _kill(p)
            return True, "Cancelando…"
    return False, "Ese despliegue ya terminó"


def recover():
    """Al arrancar el panel: lo que quedó 'running' falló (se interrumpió); la cola sigue."""
    with _lock:
        for name in list_apps():
            hist = load_history(name)
            changed = False
            for h in hist:
                if h.get("status") == "running":
                    h.update(status="failed", finished=time.time(), error="Interrumpido: el panel se reinició")
                    changed = True
                    try:
                        with open(log_path(name, h["id"]), "a", encoding="utf-8") as f:
                            f.write("\n==> Interrumpido: el panel se reinició durante el despliegue\n")
                    except (OSError, ValueError):
                        pass
            if changed:
                save_history(name, hist)
        q = queue_load()
        live = [i for i in q if (find_dep(i["app"], i["id"]) or {}).get("status") == "queued"]
        if live != q:
            queue_save(live)


# ── Log de despliegue ───────────────────────────────────────────────────────

class DeployLog:
    """Log append-only con tope: los primeros 4 MB se escriben; después solo se guarda el último
    MB en memoria y se agrega al terminar ("se omite el medio"). Todo pasa por el redactor."""

    def __init__(self, path, redact):
        _ensure_dir(Path(path).parent)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | O_NOFOLLOW | O_BINARY, 0o600)
        self.f = os.fdopen(fd, "ab", buffering=0)
        self.redact, self.size, self.omitted = redact, 0, 0
        self.tail, self.tail_size = deque(), 0

    def write(self, text):
        data = self.redact(text).encode("utf-8", "replace")
        if self.size + len(data) <= LOG_HEAD_CAP:
            self.f.write(data)
            self.size += len(data)
            return
        if not self.omitted and not self.tail:
            self.f.write("\n[… log demasiado grande: se mostrará el final cuando termine …]\n".encode())
        self.tail.append(data)
        self.tail_size += len(data)
        while self.tail_size > LOG_TAIL_CAP and len(self.tail) > 1:
            d = self.tail.popleft()
            self.tail_size -= len(d)
            self.omitted += len(d)

    def step(self, msg):
        self.write(f"\n==> [{time.strftime('%H:%M:%S')}] {msg}\n")

    def close(self):
        if self.tail:
            self.f.write(f"[… {self.omitted} bytes omitidos …]\n".encode())
            self.f.write(b"".join(self.tail))
            self.tail.clear()
        self.f.close()


# ── Pipeline ────────────────────────────────────────────────────────────────

def _check_cancel():
    if _current.get("cancel"):
        raise Cancelled()


def _cmd(log, cmd, timeout, what, env=None, cwd=None):
    log.write("$ " + " ".join(shlex.quote(a) for a in cmd) + "\n")
    code = _stream(cmd, log.write, timeout, env=env, cwd=cwd)
    _check_cancel()
    if code == 124:
        raise Fail(f"{what}: tiempo de espera agotado ({timeout // 60} min)")
    if code != 0:
        raise Fail(f"{what} falló (código {code})")


def _fetch(name, cfg, log):
    d = app_dir(name)
    src = d / "src"
    env = git_env(name, cfg)
    if lstat_kind(src / ".git") != "dir" or lstat_kind(src) != "dir":
        if lstat_kind(src) is not None:
            shutil.rmtree(src) if lstat_kind(src) == "dir" else os.unlink(src)
        _cmd(log, ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--branch", cfg["branch"],
                   "--", cfg["repo"], str(src)], 600, "git clone", env=env, cwd=str(d))
    else:
        g = ["git", "-C", str(src)]
        _cmd(log, [*g, "remote", "set-url", "origin", cfg["repo"]], 30, "git remote", env=env)
        _cmd(log, [*g, "fetch", "--depth", "1", "--no-tags", "origin", cfg["branch"]], 600, "git fetch", env=env)
        _cmd(log, [*g, "reset", "--hard", "FETCH_HEAD"], 120, "git reset", env=env)
        _cmd(log, [*g, "clean", "-fdx"], 300, "git clean", env=env)
    code, out, err = _run(["git", "-C", str(src), "log", "-1", "--format=%H%n%an%n%s"], 30, env=env)
    parts = out.split("\n", 2)
    if code != 0 or not SHA_RE.fullmatch(parts[0].strip()):
        raise Fail("No se pudo leer el último commit: " + (err or out).strip()[:300])
    return {"sha": parts[0].strip(), "author": (parts[1] if len(parts) > 1 else "")[:120],
            "message": (parts[2] if len(parts) > 2 else "").strip()[:300]}


def _build(name, cfg, image, sha, log):
    d = app_dir(name)
    src = d / "src"
    kind = lstat_kind(src / "Dockerfile")
    if kind == "link":
        raise Fail("El Dockerfile del repositorio es un enlace simbólico; por seguridad no se usa")
    if kind == "file":
        log.step("El repositorio trae Dockerfile: se usa ese")
        dockerfile = src / "Dockerfile"
    elif is_php(cfg):
        cdir = find_composer_dir(src)
        log.step(f"Generando Dockerfile para PHP {cfg['php']} con Apache"
                 + (" (sin composer.json: sin composer install)" if cdir is None
                    else f" (composer install en {cdir or 'la raíz'})"))
        dockerfile = d / "Dockerfile"
        write_file(dockerfile, render_dockerfile_php(cfg["php"], cfg.get("php_ext") or [], cdir))
        if lstat_kind(src / ".dockerignore") is None:
            fd = os.open(src / ".dockerignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY, 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(DOCKERIGNORE_PHP.encode())
    else:
        if lstat_kind(src / "composer.json") != "file":
            raise Fail("El repositorio no tiene composer.json ni Dockerfile: no parece un proyecto Laravel")
        node = lstat_kind(src / "package.json") == "file"
        lock = lstat_kind(src / "package-lock.json") == "file"
        log.step(f"Generando Dockerfile para Laravel (PHP {cfg['php']}"
                 + (", con compilación de assets (npm)" if node else ", sin package.json: sin npm") + ")")
        dockerfile = d / "Dockerfile"
        write_file(dockerfile, render_dockerfile(cfg["php"], node, lock, cfg.get("php_ext") or []))
        if lstat_kind(src / ".dockerignore") is None:
            fd = os.open(src / ".dockerignore", os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY, 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(DOCKERIGNORE.encode())
    log.step(f"Construyendo la imagen {image}")
    env = {**os.environ, "BUILDKIT_PROGRESS": "plain", "DOCKER_CLI_HINTS": "false"}
    _cmd(log, ["docker", "build", "--pull", "-t", image, "--label", f"matipanel.app={name}",
               "--label", f"matipanel.sha={sha}", "-f", str(dockerfile), str(src)],
         BUILD_TIMEOUT, "docker build", env=env)


def exposed_port(ports_json):
    """Del JSON de .Config.ExposedPorts ({"80/tcp": {}}) → el puerto si la imagen expone uno solo."""
    try:
        ports = [k.split("/")[0] for k in (json.loads(ports_json) or {}) if k.endswith("/tcp")]
    except (ValueError, AttributeError):
        return None
    return int(ports[0]) if len(ports) == 1 and ports[0].isdigit() and 1 <= int(ports[0]) <= 65535 else None


def _adopt_exposed_port(name, cfg, image, log):
    """Dockerfile propio con un solo EXPOSE (p. ej. php:*-apache → 80): se usa ese puerto interno,
    así no hay que adivinar el valor en Configuración."""
    code, out, _ = _run(["docker", "image", "inspect", "--format", "{{json .Config.ExposedPorts}}", image], 30)
    port = exposed_port(out.strip()) if code == 0 else None
    if port and port != int(cfg.get("container_port") or 8080):
        log.write(f"La imagen expone el puerto {port}: se usa como puerto interno (antes {cfg.get('container_port') or 8080})\n")
        update_config(name, lambda c: c.update(container_port=port))


def _write_compose(name, cfg, slots=None):
    if is_php(cfg):
        # Copia del .env para montarla como archivo. Dueño www-data (uid 33 en la imagen) y 0400: el
        # contenedor la lee; en el host nadie más entra porque la carpeta de la app es 0700 de root.
        mount = app_dir(name) / "env.mount"
        write_file(mount, read_env(name), 0o400)
        if hasattr(os, "chown"):
            os.chown(mount, 33, 33)
    write_file(app_dir(name) / "compose.yaml", render_compose(cfg, app_dir(name), slots))


def _seed_env(name, cfg, log):
    """Primer despliegue PHP: si el .env aún no tiene nada propio, parte del <env_path>.example del repo."""
    if not is_php(cfg) or cfg.get("env_seeded"):
        return
    update_config(name, lambda c: c.update(env_seeded=True))
    env_path = cfg.get("env_path") or ".env"
    example = app_dir(name) / "src" / (env_path + ".example")
    text = read_text(example, ENV_MAX) if valid_rel_path(env_path) and lstat_kind(example) == "file" else None
    if text is None:
        log.write(f"El repositorio no trae {env_path}.example: revisa el .env en la pestaña Entorno\n")
        return
    pairs, errors = parse_env(text)
    if errors or not pairs:
        log.write(f"Se ignoró {env_path}.example: " + ("; ".join(errors[:3]) or "vacío") + "\n")
        return
    current = dict(parse_env(read_env(name))[0])
    merged = env_set(serialize_env([(k, v) for k, v in pairs if k not in current]), current)
    write_env(name, env_set(merged, service_env(cfg, load_secrets(name), merged)))
    log.write(f"El .env se creó a partir de {env_path}.example (revísalo en la pestaña Entorno)\n")


def _wait_healthy(port, log, timeout=HEALTH_TIMEOUT):
    url = f"http://127.0.0.1:{port}/"
    log.write(f"Esperando respuesta de {url} (hasta {timeout} s)…\n")
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        _check_cancel()
        status = _http_status(url)
        if status is not None and status < 500:
            log.write(f"Respondió HTTP {status}\n")
            return True
        if status != last:
            log.write(f"… {'sin respuesta' if status is None else f'HTTP {status}'}\n")
            last = status
        time.sleep(2)
    return False


def prune_images(name, cfg, log=None):
    """Conserva las imágenes de los slots y las de los últimos KEEP_IMAGES despliegues exitosos."""
    successes = []
    for h in load_history(name):
        if h.get("status") == "success" and h.get("image") and h["image"] not in successes:
            successes.append(h["image"])
    keep = {v for v in (cfg.get("slots") or {}).values() if v} | set(successes[:KEEP_IMAGES])
    code, out, _ = _run(["docker", "image", "ls", f"matipanel/{name}", "--format", "{{.Repository}}:{{.Tag}}"], 30)
    if code != 0:
        return
    for ref in out.split():
        if ref.startswith(f"matipanel/{name}:") and ref not in keep and not ref.endswith(":<none>"):
            c, o, e = _run(["docker", "image", "rm", ref], 60)
            if log:
                log.write(f"Imagen antigua eliminada: {ref}\n" if c == 0 else f"No se pudo borrar {ref}: {(e or o).strip()}\n")


def _pipeline(name, dep, log):
    cfg = load_config(name)
    image = dep.get("image") or ""
    info = {k: dep.get(k, "") for k in ("sha", "message", "author")}
    if not dep.get("reuse"):
        log.step(f"Obteniendo el código de {cfg['repo']} (rama {cfg['branch']})")
        info = _fetch(name, cfg, log)
        image = f"matipanel/{name}:{info['sha'][:7]}"
        update_dep(name, dep["id"], image=image, **info)
        update_config(name, lambda c: c.update(last_build_sha=info["sha"]))
        log.write(f"Commit {info['sha'][:7]} — {info['message']} ({info['author']})\n")
        _check_cancel()
        _seed_env(name, cfg, log)
        _build(name, cfg, image, info["sha"], log)
        _adopt_exposed_port(name, cfg, image, log)
    else:
        log.step(f"Reutilizando la imagen {image} (sin compilar de nuevo)")
        if not TAG_RE.fullmatch(image.split(":", 1)[-1]) or not image.startswith(f"matipanel/{name}:"):
            raise Fail("Imagen inválida")
        code, _, err = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image], 30)
        if code != 0:
            raise Fail(f"La imagen {image} ya no existe (se conservan solo las últimas {KEEP_IMAGES})")
    _check_cancel()

    cfg = load_config(name)
    old = cfg.get("active_slot")
    target = other_slot(old) if old else "blue"
    slots = dict(cfg.get("slots") or {})
    slots[target] = image
    _write_compose(name, cfg, slots)
    tsvc = "app_" + target

    try:
        deps = dep_services(cfg)
        if deps:
            log.step("Levantando servicios: " + ", ".join(deps))
            _cmd(log, compose(name, "up", "-d", "--wait", *deps), 600, "docker compose up (dependencias)")
        if is_php(cfg):
            mcmd = cfg.get("migrate_cmd") or ""
            argv = mcmd.split() if cfg.get("migrate", True) and valid_migrate_cmd(mcmd) else None
        else:
            argv = ["php", "artisan", "migrate", "--force"] if cfg.get("migrate", True) else None
            if argv and lstat_kind(app_dir(name) / "src" / "artisan") != "file":
                log.write("El repositorio no tiene artisan: se omiten las migraciones de Laravel. "
                          "Si no es Laravel, cambia el tipo a «PHP» en Configuración.\n")
                argv = None
        if dep.get("trigger") not in ("rollback", "config") and argv:
            log.step(f"Ejecutando migraciones ({' '.join(argv)})")
            try:
                _cmd(log, compose(name, "run", "--rm", "-T", "-e", "AUTORUN_ENABLED=false", tsvc, *argv),
                     MIGRATE_TIMEOUT, "Las migraciones")
            except Fail as e:
                raise Fail(f"{e}. La versión anterior sigue en línea.")
        log.step(f"Iniciando la versión nueva en el slot {target} (127.0.0.1:{slot_port(cfg, target)})")
        _cmd(log, compose(name, "up", "-d", "--no-deps", "--force-recreate", tsvc), 300, "docker compose up")
        if not _wait_healthy(slot_port(cfg, target), log):
            log.step("La versión nueva no respondió a tiempo. Últimas líneas del contenedor:")
            code, out = _run_capped(compose(name, "logs", "--no-color", "--tail", "100", tsvc), 256 * 1024, 30)
            log.write(out + "\n")
            raise Fail(f"La aplicación no respondió en {HEALTH_TIMEOUT} s (HTTP < 500). La versión anterior sigue en línea.")
        _check_cancel()
        log.step("Cambiando Nginx a la versión nueva")
        ok, out = apply_nginx(name, cfg, slot_port(cfg, target))
        log.write(out + "\n")
        if not ok:
            raise Fail("No se pudo actualizar Nginx. La versión anterior sigue en línea.")
    except Exception:  # Fail, Cancelled o un error inesperado: el slot nuevo se descarta
        _run(compose(name, "rm", "-sf", tsvc), 120)
        _write_compose(name, load_config(name))
        raise

    # Desde aquí la versión nueva ya atiende: lo que falle solo es advertencia.
    def commit(c):
        c["slots"] = {**(c.get("slots") or {}), target: image}
        c["active_slot"] = target
        c["current"] = {**info, "image": image, "deployment": dep["id"], "at": time.time()}
    cfg = update_config(name, commit)
    _write_compose(name, cfg)
    if old:
        log.step(f"Deteniendo la versión anterior (slot {old})")
        code, o, e = _run(compose(name, "rm", "-sf", "app_" + old), 120)
        if code != 0:
            log.write("Advertencia: " + (o + e).strip() + "\n")
    svcs = app_services(cfg)
    log.step("Actualizando servicios: " + ", ".join(svcs))
    code = _stream(compose(name, "up", "-d", "--no-deps", "--remove-orphans", *svcs), log.write, 300)
    if code != 0:
        log.write(f"Advertencia: docker compose up terminó con código {code}\n")
    prune_images(name, cfg, log)
    return image


def execute(name, dep):
    """Ejecuta un despliegue (ya marcado running). → (estado, error)."""
    try:
        redact = redactor_for(name)
    except OSError:
        redact = Redactor()
    log = DeployLog(log_path(name, dep["id"]), redact)
    kind = "PHP" if is_php(load_config(name) or {}) else "Laravel"
    log.step(f"Despliegue {dep['id']} de «{name}» ({dep.get('trigger')}, proyecto {kind})")
    t0 = time.time()
    try:
        _pipeline(name, dep, log)
        log.step(f"Listo en {int(time.time() - t0)} s")
        return "success", ""
    except Cancelled:
        log.step("Cancelado por el usuario")
        return "cancelled", "Cancelado por el usuario"
    except Fail as e:
        log.step(f"ERROR: {e}")
        return "failed", str(e)[:500]
    except Exception as e:  # el trabajador nunca debe morir
        log.step(f"ERROR interno: {type(e).__name__}: {e}")
        return "failed", f"Error interno: {e}"[:500]
    finally:
        log.close()


def process_next():
    """Procesa el primer elemento de la cola. → True si había algo."""
    with _lock:
        q = queue_load()
        if not q:
            return False
        item = q[0]
        name, dep_id = item["app"], item["id"]
        dep = find_dep(name, dep_id) if lstat_kind(app_dir(name)) == "dir" else None
        if not dep or dep.get("status") != "queued":
            queue_save(q[1:])
            return True
        dep = update_dep(name, dep_id, status="running", started=time.time())
        _current.update(app=name, id=dep_id, cancel=False, proc=None)
    status, error = "failed", ""
    try:
        status, error = execute(name, dep)
    finally:
        with _lock:
            if lstat_kind(app_dir(name)) == "dir":
                update_dep(name, dep_id, status=status, error=error, finished=time.time())
            queue_save([i for i in queue_load() if not (i["app"] == name and i["id"] == dep_id)])
            _current.update(app=None, id=None, cancel=False, proc=None)
        _notify_deploy(name, dep, status, error)
    return True


def _notify_deploy(name, dep, status, error):
    if status not in ("success", "failed"):
        return
    try:
        from . import notify
        d = find_dep(name, dep["id"]) or dep
        commit = f"{(d.get('sha') or '')[:7]} {d.get('message') or ''}".strip() or "—"
        origin = {"manual": "manual", "poll": "push detectado", "webhook": "webhook", "rollback": "reversión",
                  "config": "cambio de configuración"}.get(dep.get("trigger"), dep.get("trigger") or "")
        if status == "failed":
            notify.event("deploy_fail", f"Falló el despliegue de «{name}»",
                         f"El despliegue {dep['id']} de «{name}» ({origin}) falló:\n\n{error}\n\nCommit: {commit}\n"
                         "La versión anterior sigue en línea. Revisa el log en Despliegues.",
                         key=f"deploy:{dep['id']}", cooldown=0)
        else:
            notify.event("deploy_ok", f"«{name}» desplegada",
                         f"El despliegue {dep['id']} de «{name}» ({origin}) terminó bien.\n\nCommit: {commit}",
                         key=f"deploy:{dep['id']}", cooldown=0)
    except Exception:  # un fallo del correo no debe afectar al despliegue
        pass


def _worker_loop():
    try:
        recover()
    except Exception:
        pass
    while True:
        _wakeup.wait(5)
        _wakeup.clear()
        try:
            while process_next():
                pass
        except Exception:
            time.sleep(5)


_backoff = {}


def poll_once(now=None):
    """Auto-despliegue: compara la punta de la rama en GitHub con el último commit compilado."""
    now = now or time.time()
    for name in list_apps():
        cfg = load_config(name)
        if not cfg or cfg.get("draft") or not cfg.get("auto_deploy") or not cfg.get("last_build_sha"):
            continue
        nxt, fails = _backoff.get(name, (0, 0))
        if now < nxt or pending(name):
            continue
        ok, heads = ls_remote(name, cfg, cfg["branch"])
        sha = heads.get(cfg["branch"]) if ok else None
        if not sha:
            fails += 1
            _backoff[name] = (now + min(POLL_EVERY * 2 ** fails, 3600), fails)
            continue
        _backoff.pop(name, None)
        if sha != cfg["last_build_sha"]:
            enqueue(name, "poll")


def _poll_loop():
    while True:
        time.sleep(POLL_EVERY)
        try:
            poll_once()
        except Exception:
            pass


_started = [False]


def start_background():
    if _started[0]:
        return
    _started[0] = True
    threading.Thread(target=_worker_loop, daemon=True, name="matipanel-deployer").start()
    threading.Thread(target=_poll_loop, daemon=True, name="matipanel-deploy-poller").start()


# ── Operaciones de la app ───────────────────────────────────────────────────

def used_ports(exclude=None):
    ports, bases = set(), set()
    for n in list_apps():
        c = load_config(n) or {}
        if n == exclude:
            continue
        if c.get("port"):
            ports.add(int(c["port"]))
        if c.get("slot_base"):
            bases.add(int(c["slot_base"]))
    return ports, bases


def create_draft(name, repo):
    """Paso 1 del asistente: carpeta + clave de despliegue. → cfg."""
    with _lock:
        if lstat_kind(app_dir(name)) is not None:
            raise FileExistsError("Ya existe una aplicación con ese nombre")
        _ensure_dir(BASE_DIR)
        _ensure_dir(app_dir(name))
        try:
            generate_key(name)
            ports, bases = used_ports()
            listening = listening_ports()
            port = allocate_port(ports | listening)
            base = allocate_slot_base(bases, listening)
            if not port or not base:
                raise OSError("No quedan puertos libres")
            cfg = {"name": name, "repo": repo, "branch": "main", "php": "8.3", "hostname": "", "port": port,
                   "slot_base": base, "container_port": 8080, "auto_deploy": True, "migrate": True,
                   "worker": False, "scheduler": False, "database": "none", "redis": False,
                   "mem_mb": 0, "cpus": 0, "php_ext": [], "draft": True, "created": time.time(),
                   "kind": "laravel", "docroot": "", "env_path": ".env", "persist": [], "migrate_cmd": "",
                   "active_slot": None, "slots": {"blue": None, "green": None}, "last_build_sha": None,
                   "current": None}
            save_config(name, cfg)
            save_secrets(name, {"webhook_secret": secrets.token_hex(24)})
            save_history(name, [])
            _ensure_dir(app_dir(name) / "deployments")
        except Exception:
            shutil.rmtree(app_dir(name), ignore_errors=True)
            raise
        return cfg


def finish_create(name, opts):
    """Paso final del asistente: guarda opciones, genera .env, db.env y compose."""
    with _lock:
        cfg = load_config(name)
        if not cfg or not cfg.get("draft"):
            raise KeyError(name)
        cfg.update(opts)
        cfg["draft"] = False
        cfg.setdefault("db_name", "app" if is_php(cfg) else "laravel")
        sec = load_secrets(name)
        if cfg["database"] in ("mariadb", "postgres") and not sec.get("db_password"):
            sec.update(db_password=new_password(), db_root_password=new_password())
            save_secrets(name, sec)
        write_env(name, default_env(cfg, sec))
        write_file(app_dir(name) / "db.env", db_env(cfg, sec))
        save_config(name, cfg)
        _write_compose(name, cfg)
        return cfg


def apply_service_changes(name):
    """Tras cambiar servicios en Configuración: credenciales, .env, db.env y compose."""
    with _lock:
        cfg = load_config(name)
        sec = load_secrets(name)
        if cfg["database"] in ("mariadb", "postgres") and not sec.get("db_password"):
            sec.update(db_password=new_password(), db_root_password=new_password())
            save_secrets(name, sec)
        current = read_env(name)
        upd = service_env(cfg, sec, current)
        if upd:
            write_env(name, env_set(current, upd))
        write_file(app_dir(name) / "db.env", db_env(cfg, sec))
        _write_compose(name, cfg)


def delete_app(name, volumes=False):
    """Elimina contenedores, imágenes, sitio de Nginx y carpeta. → lista de advertencias."""
    warnings = []
    with _lock:
        if any(h.get("status") == "running" for h in pending(name)):
            raise RuntimeError("Hay un despliegue en curso; cancélalo primero")
        queue_save([q for q in queue_load() if q["app"] != name])
    cfg = load_config(name) or {}
    d = app_dir(name)
    if not cfg.get("draft") and lstat_kind(d / "compose.yaml") == "file" and compose_cmd():
        args = ["down", "--remove-orphans", "-t", "20"] + (["-v"] if volumes else [])
        code, o, e = _run(compose(name, *args), 300)
        if code != 0:
            warnings.append("docker compose down: " + (o + e).strip()[-400:])
        if volumes:
            for v in ("storage", "mariadb_data", "postgres_data", "redis_data", *persist_volumes(cfg)):
                _run(["docker", "volume", "rm", f"{project_name(name)}_{v}"], 60)
        code, out, _ = _run(["docker", "image", "ls", f"matipanel/{name}", "--format", "{{.Repository}}:{{.Tag}}"], 30)
        for ref in out.split() if code == 0 else []:
            if ref.startswith(f"matipanel/{name}:"):
                _run(["docker", "image", "rm", "-f", ref], 60)
    try:
        remove_nginx(name)
        remove_apache_host(name)
    except OSError as e:
        warnings.append(f"Nginx: {e}")
    with _lock:
        shutil.rmtree(d)
    return warnings


def container_status(name):
    """Estado de los contenedores vía `docker compose ps --format json` (array o NDJSON)."""
    code, out, err = _run(compose(name, "ps", "-a", "--format", "json"), 30)
    if code != 0:
        return None, (err or out).strip()[:300]
    out = out.strip()
    items = []
    try:
        data = json.loads(out) if out.startswith("[") else [json.loads(l) for l in out.splitlines() if l.strip()]
    except ValueError:
        data = []
    for c in data if isinstance(data, list) else []:
        if isinstance(c, dict):
            items.append({"service": str(c.get("Service", "")), "name": str(c.get("Name", "")),
                          "state": str(c.get("State", "")), "status": str(c.get("Status", "")),
                          "health": str(c.get("Health", "")), "image": str(c.get("Image", ""))})
    return items, ""


def all_containers():
    """{app: {role: state}} de todos los contenedores del panel, con una sola llamada a docker."""
    code, out, _ = _run(["docker", "ps", "-a", "--filter", "label=matipanel.app", "--format", "{{json .}}"], 20)
    res = {}
    if code != 0:
        return None
    for line in out.splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue
        labels = dict(kv.split("=", 1) for kv in str(c.get("Labels", "")).split(",") if "=" in kv)
        app, role = labels.get("matipanel.app"), labels.get("matipanel.role", "")
        if app and "-run-" not in str(c.get("Names", "")):
            res.setdefault(app, {}).setdefault(role, []).append(str(c.get("State", "")))
    return res


# ── Autoverificación ────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Nombres
    assert valid_name("miapp") and valid_name("mi-app2") and valid_name("ab")
    for bad in ("a", "Miapp", "1app", "mi_app", "mi app", "-app", "app-", "a" * 32, "../x", "", None):
        assert not valid_name(bad), bad
    # Repos
    assert parse_repo("https://github.com/senabed/portal") == ("https", "senabed", "portal")
    assert parse_repo("https://github.com/senabed/portal.git") == ("https", "senabed", "portal")
    assert parse_repo("git@github.com:senabed/portal.git") == ("ssh", "senabed", "portal")
    assert parse_repo("git@github.com:o/my.repo_x-1.git") == ("ssh", "o", "my.repo_x-1")
    for bad in ("http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com/a/b/c",
                "https://user:tok@github.com/a/b", "git@github.com:a/..", "https://github.com/a/b;rm",
                "git@evil.com:a/b.git", "ext::sh -c id", "--upload-pack=x", "https://github.com/-a/b",
                "https://github.com/a/b\n", "file:///etc"):
        assert parse_repo(bad) is None, bad
    assert is_ssh("git@github.com:a/b.git") and not is_ssh("https://github.com/a/b")
    # Ramas
    for ok in ("main", "develop", "release/1.2", "feature/x_y-z"):
        assert valid_branch(ok), ok
    for bad in ("-x", "a..b", "a/", "/a", "a.lock", ".hidden", "a/.b", "a b", "a~1", "a^", "a:b", "HEAD", "a//b", ""):
        assert not valid_branch(bad), bad
    # Hostnames
    assert valid_hostname("miapp.apps.senabed.local") and valid_hostname("a.b")
    for bad in ("miapp", "-a.b", "a-.b", "a..b", "A.b", "a_b.c", "a.b;", "1.2.3.4", "a.b\n", "x" * 64 + ".a"):
        assert not valid_hostname(bad), bad
    # Artisan
    assert valid_artisan("migrate:status")[0] and valid_artisan("queue:retry all")[0]
    assert valid_artisan("migrate --force")[0] and valid_artisan("cache:forget key=a,b")[0]
    assert not valid_artisan("tinker")[0] and not valid_artisan("migrate; rm -rf /")[0]
    assert not valid_artisan("x $(id)")[0] and not valid_artisan("a  b")[0] and is_dangerous_artisan("db:wipe --force")
    # .env: claves, ida y vuelta
    pairs = [("APP_NAME", "Mi App"), ("APP_KEY", "base64:abc+/="), ("EMPTY", ""), ("Q", "it's"),
             ("ML", "line1\nline2"), ("REF", "${APP_NAME}"), ("BS", 'a\\nb"c'), ("HASH", "a #b"),
             ("URL", "http://x.local:8081/p?q=1&r=2"), ("DOLLAR", "pa$$word")]
    assert validate_pairs(pairs) == []
    text = serialize_env(pairs)
    back, errs = parse_env(text)
    assert errs == [] and back == pairs, (back, errs)
    p, e = parse_env('# c\nexport A=1\nB = "x # y" # comentario\nC=z # inline\nD=\'lit $X\'\n\nE="m\nn"\n')
    assert e == [] and p == [("A", "1"), ("B", "x # y"), ("C", "z"), ("D", "lit $X"), ("E", "m\nn")], p
    _, e = parse_env('lower=1\nNOEQ\nX="abierta\n9A=1')
    assert len(e) == 3, e
    assert validate_pairs([("a", "1")]) and validate_pairs([("A", "1"), ("A", "2")]) and validate_pairs([("A", "x\ry")])
    t2 = env_set("# top\nA=1\nDB_HOST=old\n\n", {"DB_HOST": "db", "NEW": "v w"})
    assert t2 == "# top\nA=1\nDB_HOST=db\nNEW='v w'\n", t2
    # Redacción
    r = Redactor(["supersecreto", "abc", "base64:KEYKEY=="])
    assert r("pw=supersecreto k=base64:KEYKEY== abc") == "pw=•••••• k=•••••• abc"
    assert secret_values_from_env([("DB_PASSWORD", "x1"), ("APP_NAME", "y"), ("AWS_SECRET_ACCESS_KEY", "z"),
                                   ("DATABASE_URL", "u")]) == ["x1", "z", "u"]
    # Dockerfile
    df = render_dockerfile("8.3")
    assert "FROM serversideup/php:8.3-fpm-nginx AS base" in df and "node" not in df and "AUTORUN_LARAVEL_MIGRATION=false" in df
    assert "composer install --no-dev" in df and "--optimize-autoloader" in df and ".env" not in df
    df = render_dockerfile("8.4", node=True, npm_lock=True, php_ext=["intl", "gd", "bad;rm"])
    assert "FROM node:22-alpine AS assets" in df and "RUN npm ci" in df and "npm run build" in df
    assert df.rstrip().endswith("/var/www/html/public") and "install-php-extensions intl gd\n" in df and "bad" not in df
    assert "npm install" in render_dockerfile("8.2", node=True)
    try:
        render_dockerfile("7.4")
        raise AssertionError
    except ValueError:
        pass
    # compose
    cfg = {"name": "miapp", "port": 8081, "slot_base": 20000, "container_port": 8080, "database": "mariadb",
           "redis": True, "worker": True, "scheduler": True, "mem_mb": 512, "cpus": 1.5,
           "active_slot": "blue", "slots": {"blue": "matipanel/miapp:abc1234", "green": None}}
    doc = json.loads(render_compose(cfg, "/var/lib/matipanel/apps/miapp").split("\n", 1)[1])
    s = doc["services"]
    assert doc["name"] == "matipanel-miapp" and set(s) == {"db", "redis", "app_blue", "worker", "scheduler"}
    assert s["app_blue"]["ports"] == ["127.0.0.1:20000:8080"] and s["app_blue"]["env_file"] == ["/var/lib/matipanel/apps/miapp/env"]
    assert s["app_blue"]["deploy"]["resources"]["limits"] == {"memory": "512M", "cpus": "1.5"}
    assert s["worker"]["image"] == "matipanel/miapp:abc1234" and "queue:work" in s["worker"]["command"]
    assert s["worker"]["healthcheck"] == {"disable": True} and s["scheduler"]["healthcheck"] == {"disable": True}
    assert s["app_blue"]["depends_on"]["db"]["condition"] == "service_healthy" and "storage:/var/www/html/storage" in s["app_blue"]["volumes"]
    assert set(doc["volumes"]) == {"storage", "mariadb_data", "redis_data"} and s["db"]["image"] == "mariadb:11"
    doc = json.loads(render_compose({**cfg, "database": "postgres", "redis": False, "worker": False, "scheduler": False,
                                     "mem_mb": 0, "cpus": 0}, "/x", {"blue": "i:1", "green": "i:2"}).split("\n", 1)[1])
    s = doc["services"]
    assert set(s) == {"db", "app_blue", "app_green"} and s["app_green"]["ports"] == ["127.0.0.1:20001:8080"]
    assert "deploy" not in s["app_blue"] and s["db"]["image"] == "postgres:16" and "depends_on" in s["app_green"]
    doc = json.loads(render_compose({**cfg, "database": "none", "redis": False, "active_slot": None,
                                     "slots": {}}, "/x").split("\n", 1)[1])
    assert doc["services"] == {} and doc["volumes"] == {"storage": {}}
    assert app_services(cfg) == ["app_blue", "worker", "scheduler"] and dep_services(cfg) == ["db", "redis"]
    # nginx
    ng = render_nginx({"name": "mi-app", "port": 8082, "hostname": "mi.apps.senabed.local"}, 20003)
    assert "listen 8082;" in ng and "listen 80;" in ng and "server_name mi.apps.senabed.local;" in ng
    assert ng.count("proxy_pass http://127.0.0.1:20003;") == 2 and "client_max_body_size 100m;" in ng
    assert "map $http_upgrade $mp_conn_mi_app" in ng and "proxy_set_header Connection $mp_conn_mi_app;" in ng
    assert "listen 80;" not in render_nginx({"name": "x1", "port": 8083, "hostname": ""}, 20000)
    try:
        render_nginx({"name": "x1", "port": 8083, "hostname": "a.b; include /etc/passwd"}, 20000)
        raise AssertionError
    except ValueError:
        pass
    # puertos
    assert allocate_port({8081, 8082, 8084}) == 8083 and allocate_port(set(range(8081, 9000))) is None
    assert allocate_slot_base({20000}, {20003}) == 20004 and allocate_slot_base(set(), set()) == 20000
    # firma de GitHub
    body = b'{"ref":"refs/heads/main"}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, sig) and sign_body("s3cret", body) == sig
    assert not verify_signature("s3cret", body + b" ", sig) and not verify_signature("otro", body, sig)
    assert not verify_signature("s3cret", body, None) and not verify_signature("", body, sig)
    assert not verify_signature("s3cret", body, sig.replace("sha256=", "sha1="))
    assert not verify_signature("s3cret", body, "sha256=é" + sig[8:])  # no ASCII: False, no TypeError
    # .env por defecto con servicios
    sec = {"db_password": "P" * 32, "db_root_password": "R" * 32}
    env = dict(parse_env(default_env({"name": "miapp", "port": 8081, "hostname": "", "database": "postgres",
                                      "redis": True}, sec))[0])
    assert env["DB_CONNECTION"] == "pgsql" and env["DB_HOST"] == "db" and env["DB_PASSWORD"] == "P" * 32
    assert env["REDIS_HOST"] == "redis" and env["APP_KEY"].startswith("base64:") and env["QUEUE_CONNECTION"] == "redis"
    assert dict(parse_env(db_env({"database": "mariadb"}, sec))[0])["MARIADB_ROOT_PASSWORD"] == "R" * 32
    assert len(new_password()) == 32 and new_password().isalnum()
    # Proyectos PHP genéricos (Quetzal: composer en app/, .env en app/config, DB_* y LDB_*)
    assert valid_rel_path("app/config/.env") and valid_rel_path("assets/uploads") and not valid_rel_path("../etc")
    assert not valid_rel_path("/etc") and not valid_rel_path("a/./b") and not valid_rel_path("a b") and not valid_rel_path("")
    assert valid_migrate_cmd("php migrate.php") and valid_migrate_cmd("php bin/migrate.php up --force")
    assert not valid_migrate_cmd("php ../x.php") and not valid_migrate_cmd("bash x.php") and not valid_migrate_cmd("php m.php; id")
    df = render_dockerfile_php("8.3", ["intl", "bad;rm"], "app")
    assert "FROM serversideup/php:8.3-fpm-apache" in df and "RUN cd app && composer install --no-dev" in df
    assert "install-php-extensions intl\n" in df and "composer" not in render_dockerfile_php("8.4", (), None)
    pc = {"name": "quetzal", "kind": "php", "db_name": "app", "slot_base": 20010, "active_slot": "blue", "database": "mariadb",
          "docroot": "", "env_path": "app/config/.env", "persist": ["assets/uploads", "app/logs"], "worker": True}
    doc = json.loads(render_compose(pc, "/apps/quetzal", {"blue": "matipanel/quetzal:abc1234"}).split("\n", 1)[1])
    app_svc = doc["services"]["app_blue"]
    assert app_svc["environment"] == {"APACHE_DOCUMENT_ROOT": "/var/www/html"} and "worker" not in doc["services"]
    assert "/apps/quetzal/env.mount:/var/www/html/app/config/.env:ro" in app_svc["volumes"]
    assert sorted(v.split(":")[1] for v in app_svc["volumes"][:2]) == ["/var/www/html/app/logs", "/var/www/html/assets/uploads"]
    assert "storage" not in doc["volumes"] and set(persist_volumes(pc)) <= set(doc["volumes"])
    quetzal_env = "DB_ENGINE='mysql'\nDB_HOST='localhost'\nDB_NAME=''\nDB_USER=''\nDB_PASS=''\nLDB_HOST='localhost'\nLDB_PASS=''\n"
    upd = service_env(pc, sec, quetzal_env)
    assert upd == {"DB_HOST": "db", "DB_NAME": "app", "DB_USER": "app", "DB_PASS": "P" * 32,
                   "LDB_HOST": "db", "LDB_PASS": "P" * 32}, upd
    assert set(service_env(pc, sec)) == {"DB_HOST", "DB_PORT", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD"}
    assert dict(parse_env(db_env(pc, sec))[0])["MARIADB_DATABASE"] == "app"
    ap = render_apache_proxy({"name": "quetzal", "hostname": "quetzal.local", "port": 8081})
    assert "ServerName quetzal.local\n" in ap and "ProxyPass / http://127.0.0.1:8081/ upgrade=websocket" in ap
    try:
        render_apache_proxy({"name": "x", "hostname": "a.b\nInclude /etc/shadow", "port": 8081})
        raise AssertionError("hostname con salto de línea aceptado")
    except ValueError:
        pass
    assert exposed_port('{"80/tcp":{}}') == 80 and exposed_port('{"8080/tcp":{},"8443/tcp":{}}') is None
    assert exposed_port("null") is None and exposed_port("") is None and exposed_port('{"53/udp":{}}') is None
    legacy = {k: v for k, v in pc.items() if k != "db_name"}  # creada como Laravel y cambiada a PHP
    assert service_env(legacy, sec, "LDB_USER=x\n") == {"LDB_USER": "laravel"} and db_name(legacy) == "laravel"
    filled = dict(parse_env(fill_db_placeholders(pc, sec, "LDB_HOST='localhost'\nLDB_NAME='db_quetzal'\nLDB_USER='root'\n"
                                                 "LDB_PASS=''\nDB_HOST=ext.local\nDB_NAME=prod\nDB_PASS=\n"))[0])
    assert filled == {"LDB_HOST": "db", "LDB_NAME": "app", "LDB_USER": "app", "LDB_PASS": "P" * 32,
                      "DB_HOST": "ext.local", "DB_NAME": "prod", "DB_PASS": "P" * 32}, filled
    assert fill_db_placeholders({**pc, "kind": "laravel"}, sec, "DB_HOST=localhost\n") == "DB_HOST=localhost\n"
    print("ok")
