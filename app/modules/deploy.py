"""Despliegues: aplicaciones Laravel desde GitHub con Docker, al estilo Coolify.

El motor (validación, generación de Dockerfile/compose/nginx, cola y pipeline) está en
app/deployer.py; aquí solo están las rutas y el arranque de los hilos de fondo.

POST /despliegues/webhook/<nombre> es la única ruta sin sesión: la protege la firma HMAC de
GitHub (X-Hub-Signature-256), un límite por IP y un tope de 1 MB.
"""
import json
import os
import shutil
from urllib.parse import parse_qs
from flask import Blueprint, render_template, jsonify, request, redirect, url_for, abort
from flask_login import login_required
from .. import deployer as D
from ..distro import detect
from ..util import run, RateLimiter
from .packages import apt_get

deploy_bp = Blueprint("deploy", __name__)

WEBHOOK_MAX = 1024 * 1024
LOG_CHUNK = 256 * 1024
_hook_rate = RateLimiter(max_fails=60, window=60)     # peticiones por IP y minuto
_hook_bad = RateLimiter(max_fails=10, window=600)     # firmas inválidas por IP

PACKAGES = {"docker": ["docker.io"], "compose": ["docker-compose"], "buildx": ["docker-buildx"],
            "git": ["git"], "nginx": ["nginx"]}
COMPOSE_FALLBACK = ["docker-compose-plugin"]  # nombre en el repositorio oficial de Docker


@deploy_bp.record_once
def _start_worker(_state):
    if os.environ.get("MATIPANEL_DEPLOY_WORKER", "1") != "0":
        D.start_background()


def _err(msg, code=400, **extra):
    return jsonify({"ok": False, "error": msg, **extra}), code


def _data():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _cfg(name, allow_draft=False):
    """Configuración de una app existente o abort(404)."""
    if not D.valid_name(name) or name not in D.list_apps():
        abort(404)
    cfg = D.load_config(name)
    if not cfg or (cfg.get("draft") and not allow_draft):
        abort(404)
    return cfg


def _busy(name):
    return any(h.get("status") == "running" for h in D.pending(name))


# ── Páginas ─────────────────────────────────────────────────────────────────

@deploy_bp.route("/")
@login_required
def index():
    return render_template("deploy.html", app_name=None, php_versions=D.PHP_VERSIONS)


@deploy_bp.route("/app/<name>")
@login_required
def detail(name):
    cfg = _cfg(name, allow_draft=True)
    if cfg.get("draft"):
        return redirect(url_for("deploy.index", draft=name))
    return render_template("deploy.html", app_name=name, php_versions=D.PHP_VERSIONS)


# ── Requisitos ──────────────────────────────────────────────────────────────

def _requirements():
    D._compose_cache["at"] = 0.0
    items = []
    docker = shutil.which("docker")
    code, ver, err = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=15) if docker else (127, "", "")
    items.append({"key": "docker", "label": "Docker Engine", "ok": bool(docker) and code == 0,
                  "detail": ver.strip() if code == 0 else ("instalado, pero el servicio no responde: " + err.strip()[:160]
                                                          if docker else "no instalado")})
    cc = D.compose_cmd() if docker else None
    cver = run([*cc, "version", "--short"], timeout=15)[1].strip() if cc else ""
    items.append({"key": "compose", "label": "Docker Compose v2", "ok": bool(cc),
                  "detail": f"{' '.join(cc)} {cver}" if cc else "no instalado"})
    bcode, bout, _ = run(["docker", "buildx", "version"], timeout=15) if docker else (127, "", "")
    items.append({"key": "buildx", "label": "Docker Buildx (recomendado)", "ok": bcode == 0, "optional": True,
                  "detail": bout.strip()[:80] if bcode == 0 else "no instalado: se usará el constructor clásico"})
    gcode, gout, _ = run(["git", "--version"], timeout=10)
    items.append({"key": "git", "label": "Git", "ok": gcode == 0, "detail": gout.strip() if gcode == 0 else "no instalado"})
    nginx = shutil.which("nginx") or os.path.exists("/usr/sbin/nginx")
    active = run(["systemctl", "is-active", "nginx"], timeout=10)[1].strip() if nginx else ""
    items.append({"key": "nginx", "label": "Nginx (en el servidor)", "ok": bool(nginx) and active == "active",
                  "detail": ("activo" if active == "active" else f"instalado, servicio {active or 'desconocido'}")
                  if nginx else "no instalado"})
    ready = all(i["ok"] for i in items if not i.get("optional"))
    return {"items": items, "ready": ready, "apt": detect()["pkg"] == "apt"}


@deploy_bp.route("/api/requirements")
@login_required
def requirements():
    return jsonify(_requirements())


def _apt_install(pkgs):
    ok, out, msg = apt_get("install", "--", *pkgs, timeout=1500)
    if not ok and ("Unable to locate package" in out or "has no installation candidate" in out):
        apt_get("update", timeout=600)
        ok, out, msg = apt_get("install", "--", *pkgs, timeout=1500)
    return ok, out, msg


@deploy_bp.route("/api/install", methods=["POST"])
@login_required
def install():
    if detect()["pkg"] != "apt":
        return _err("La instalación automática solo está disponible en Debian/Ubuntu")
    key = _data().get("key", "")
    if key == "all":
        keys = [i["key"] for i in _requirements()["items"] if not i["ok"]]
    elif key in PACKAGES:
        keys = [key]
    else:
        return _err("Componente desconocido")
    log = []
    for k in keys:
        ok, out, msg = _apt_install(PACKAGES[k])
        log.append(out[-3000:])
        if not ok and k == "compose":
            ok, out, msg = _apt_install(COMPOSE_FALLBACK)
            log.append(out[-3000:])
            if not ok:
                msg = ("No se encontró Docker Compose v2 en los repositorios (paquetes docker-compose y "
                       "docker-compose-plugin). Revisa las fuentes de apt.")
        if not ok:
            if k == "buildx" and key == "all":
                continue  # opcional
            return _err(f"No se pudo instalar {k}: {msg}", 500, output="\n".join(log)[-6000:])
        if k == "docker":
            c, o, e = run(["systemctl", "enable", "--now", "docker"], timeout=120)
            log.append((o + e).strip())
        if k == "nginx":
            c, o, e = run(["systemctl", "enable", "--now", "nginx"], timeout=120)
            if c != 0:
                # Diagnóstico visible en la terminal de la acción: casi siempre es el puerto 80 ocupado
                _, t_out, t_err = run(["nginx", "-t"], timeout=30)
                _, ports, _ = run(["ss", "-ltnp", "( sport = :80 or sport = :443 )"], timeout=10)
                _, jlog, _ = run(["journalctl", "-u", "nginx", "-n", "15", "--no-pager"], timeout=15)
                log.append("\n".join(x.strip() for x in (o + e, t_out + t_err, ports, jlog)))
                return _err("Nginx se instaló pero no arrancó. Lo más común: otro servicio (Apache, otro "
                            "proxy o un contenedor) ya usa el puerto 80. Detén ese servicio o quita el sitio "
                            "por defecto de Nginx (/etc/nginx/sites-enabled/default) y vuelve a intentar.",
                            500, output="\n".join(log)[-6000:])
    return jsonify({"ok": True, "output": "\n".join(log)[-6000:], "requirements": _requirements()})


# ── Lista y creación ────────────────────────────────────────────────────────

def _state(cfg, hist, roles):
    """(clave, texto) del estado general de la app para el LED."""
    if cfg.get("draft"):
        return "draft", "Borrador: falta terminar la creación"
    if any(h.get("status") == "running" for h in hist):
        return "deploying", "Desplegando…"
    if not cfg.get("active_slot"):
        if hist and hist[0].get("status") == "failed":
            return "failed", "El primer despliegue falló"
        if any(h.get("status") == "queued" for h in hist):
            return "queued", "En cola"
        return "new", "Aún no se ha desplegado"
    if roles is None:
        return "unknown", "No se pudo consultar Docker"
    states = roles.get("app", [])
    if "running" in states:
        if hist and hist[0].get("status") == "failed":
            return "degraded", "En línea; el último despliegue falló"
        return "running", "En línea"
    return "stopped", "Detenida"


def _summary(name, containers):
    cfg = D.load_config(name) or {}
    hist = D.load_history(name)
    roles = None if containers is None else containers.get(name, {})
    key, text = _state(cfg, hist, roles)
    last = hist[0] if hist else None
    return {"name": name, "repo": cfg.get("repo", ""), "branch": cfg.get("branch", ""), "port": cfg.get("port"),
            "hostname": cfg.get("hostname", ""), "draft": bool(cfg.get("draft")), "state": key, "state_text": text,
            "current": cfg.get("current"), "last": last, "auto_deploy": bool(cfg.get("auto_deploy"))}


@deploy_bp.route("/api/apps")
@login_required
def apps():
    names = D.list_apps()
    containers = D.all_containers() if names and shutil.which("docker") else {}
    return jsonify({"apps": [_summary(n, containers) for n in names]})


@deploy_bp.route("/api/draft", methods=["POST"])
@login_required
def draft():
    d = _data()
    name, repo = str(d.get("name", "")).strip(), str(d.get("repo", "")).strip()
    if not D.valid_name(name):
        return _err("Nombre inválido: minúsculas, números y guiones; empieza con letra (2 a 31 caracteres)")
    if not D.parse_repo(repo):
        return _err("URL de GitHub inválida. Usa https://github.com/dueño/repo (público) "
                    "o git@github.com:dueño/repo.git (privado)")
    if name in D.list_apps():
        cfg = D.load_config(name) or {}
        if not cfg.get("draft"):
            return _err("Ya existe una aplicación con ese nombre", 409)
        D.update_config(name, lambda c: c.update(repo=repo))
    else:
        if D.lstat_kind(D.BASE_DIR / name) is not None:
            return _err("Ya existe una carpeta con ese nombre en /var/lib/matipanel/apps", 409)
        try:
            D.create_draft(name, repo)
        except (OSError, FileExistsError) as e:
            return _err(f"No se pudo preparar la aplicación: {e}", 500)
    return jsonify({"ok": True, "name": name, "ssh": D.is_ssh(repo), "pubkey": D.public_key(name)})


@deploy_bp.route("/api/apps/<name>/test-access", methods=["POST"])
@login_required
def test_access(name):
    cfg = _cfg(name, allow_draft=True)
    ok, res = D.ls_remote(name, cfg)
    if not ok:
        return _err(res, 502)
    return jsonify({"ok": True, "branches": sorted(res)[:200]})


def _num(v, cast):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def _opts(d, cfg):
    """Valida los campos editables presentes en d. → (opciones, error)."""
    out = {}
    if "repo" in d:
        repo = str(d["repo"]).strip()
        if not D.parse_repo(repo):
            return None, "URL de GitHub inválida"
        out["repo"] = repo
    if "branch" in d:
        b = str(d["branch"]).strip()
        if not D.valid_branch(b):
            return None, "Rama inválida"
        out["branch"] = b
    if "php" in d:
        if d["php"] not in D.PHP_VERSIONS:
            return None, "Versión de PHP no soportada"
        out["php"] = d["php"]
    if "hostname" in d:
        h = str(d["hostname"] or "").strip().lower().rstrip(".")
        if h and not D.valid_hostname(h):
            return None, "Nombre de host inválido (ej.: miapp.apps.empresa.local)"
        if h and any((D.load_config(n) or {}).get("hostname") == h for n in D.list_apps() if n != cfg["name"]):
            return None, "Otra aplicación ya usa ese nombre de host"
        out["hostname"] = h
    if "port" in d:
        p = _num(d["port"], int)
        lo, hi = D.PORT_EDIT_RANGE
        if p is None or not lo <= p <= hi:
            return None, f"Puerto inválido ({lo}–{hi})"
        if p != cfg.get("port"):
            ports, _ = D.used_ports(exclude=cfg["name"])
            if p in ports:
                return None, "Otra aplicación ya usa ese puerto"
            if p in D.listening_ports():
                return None, "Ese puerto ya está en uso en el servidor"
        out["port"] = p
    if "container_port" in d:
        p = _num(d["container_port"], int)
        if p is None or not 1 <= p <= 65535:
            return None, "Puerto interno del contenedor inválido"
        out["container_port"] = p
    for k in ("auto_deploy", "migrate", "worker", "scheduler", "redis"):
        if k in d:
            out[k] = bool(d[k])
    if "database" in d:
        if d["database"] not in D.DATABASES:
            return None, "Base de datos inválida"
        out["database"] = d["database"]
    if d.get("db_name"):  # solo al crear: MariaDB/PostgreSQL lo aplican al inicializar su volumen
        n = str(d["db_name"]).strip()
        if not D.valid_db_name(n):
            return None, ("Nombre de la base inválido: minúsculas, números y _, empezando por una letra "
                          "(hasta 32), y que no sea un nombre del sistema (mysql, postgres…)")
        out["db_name"] = n
    if "mem_mb" in d:
        m = _num(d["mem_mb"] or 0, int)
        if m is None or not (m == 0 or 64 <= m <= 262144):
            return None, "Memoria inválida (0 = sin límite, o de 64 a 262144 MB)"
        out["mem_mb"] = m
    if "cpus" in d:
        c = _num(d["cpus"] or 0, float)
        if c is None or not (c == 0 or 0.1 <= c <= 256):
            return None, "CPUs inválidas (0 = sin límite, o de 0.1 a 256)"
        out["cpus"] = round(c, 2)
    if "kind" in d:
        if d["kind"] not in D.KINDS:
            return None, "Tipo de proyecto inválido"
        out["kind"] = d["kind"]
    for k, label in (("docroot", "Carpeta pública"), ("env_path", "Ruta del .env")):
        if k in d:
            v = str(d[k] or "").strip().strip("/")
            if (v or k == "env_path") and not D.valid_rel_path(v):
                return None, f"{label} inválida: ruta relativa al proyecto, sin «..» (ej.: public, app/config/.env)"
            out[k] = v
    if "persist" in d:
        raw = d["persist"]
        paths = raw.replace(",", " ").split() if isinstance(raw, str) else raw if isinstance(raw, list) else None
        paths = [str(x).strip().strip("/") for x in paths] if paths is not None else None
        if (paths is None or len(paths) > D.MAX_PERSIST or len(set(paths)) != len(paths)
                or not all(D.valid_rel_path(x) for x in paths)):
            return None, f"Carpetas persistentes inválidas (hasta {D.MAX_PERSIST} rutas relativas, ej.: assets/uploads)"
        out["persist"] = paths
    if "migrate_cmd" in d:
        m = " ".join(str(d["migrate_cmd"] or "").split())
        if m and not D.valid_migrate_cmd(m):
            return None, "Comando de migraciones inválido: «php script.php [argumentos]» (ej.: php migrate.php)"
        out["migrate_cmd"] = m
    if "php_ext" in d:
        raw = d["php_ext"]
        exts = raw.replace(",", " ").split() if isinstance(raw, str) else raw if isinstance(raw, list) else None
        if exts is None or len(exts) > 20 or not all(isinstance(e, str) and D.PHP_EXT_RE.fullmatch(e) for e in exts):
            return None, "Extensiones de PHP inválidas (nombres en minúsculas separados por espacios)"
        out["php_ext"] = exts
    return out, ""


@deploy_bp.route("/api/apps/<name>/create", methods=["POST"])
@login_required
def create(name):
    cfg = _cfg(name, allow_draft=True)
    if not cfg.get("draft"):
        return _err("La aplicación ya fue creada", 409)
    d = _data()
    fields = {k: d[k] for k in ("branch", "php", "hostname", "auto_deploy", "worker", "scheduler", "database", "redis",
                                 "kind", "docroot", "env_path", "persist", "migrate_cmd", "db_name") if k in d}
    opts, error = _opts(fields, cfg)
    if error:
        return _err(error)
    ext, ext_pw = None, ""
    if opts.get("database", cfg.get("database")) == "none":
        ext, ext_pw, error = D.validate_ext_db(d.get("ext_db"))
        if error:
            return _err(error)
        if ext:
            opts["ext_db"] = ext
    else:
        opts.pop("ext_db", None)
    try:
        D.finish_create(name, opts, ext_pw)
    except (OSError, KeyError) as e:
        return _err(f"No se pudo crear la aplicación: {e}", 500)
    dep = None
    if d.get("deploy_now"):
        dep, _ = D.enqueue(name, "manual")
    return jsonify({"ok": True, "url": url_for("deploy.detail", name=name), "deployment": dep and dep["id"]})


# ── Detalle ─────────────────────────────────────────────────────────────────

@deploy_bp.route("/api/apps/<name>")
@login_required
def app_info(name):
    cfg = _cfg(name)
    containers = D.all_containers() if shutil.which("docker") else {}
    hist = D.load_history(name)
    q = D.queue_load()
    for h in hist:
        if h.get("status") == "queued":
            h["queue_pos"] = next((i + 1 for i, x in enumerate(q) if x["app"] == name and x["id"] == h["id"]), None)
        if h.get("started") and h.get("finished"):
            h["duration"] = round(h["finished"] - h["started"])
    return jsonify({**_summary(name, containers), "config": cfg, "history": hist, "ssh": D.is_ssh(cfg["repo"]),
                    "pubkey": D.public_key(name), "webhook_path": url_for("deploy.webhook", name=name),
                    "nginx_file": str(D.nginx_paths(name)[0]),
                    "slot_ports": {"blue": D.slot_port(cfg, "blue"), "green": D.slot_port(cfg, "green")},
                    "artisan_common": D.ARTISAN_COMMON, "artisan_danger": D.ARTISAN_DANGER})


@deploy_bp.route("/api/apps/<name>/containers")
@login_required
def containers(name):
    _cfg(name)
    if not D.compose_cmd():
        return _err("Docker Compose no está instalado", 503)
    items, error = D.container_status(name)
    if items is None:
        return _err(error or "docker compose ps falló", 500)
    return jsonify({"ok": True, "containers": items})


@deploy_bp.route("/api/apps/<name>/deploy", methods=["POST"])
@login_required
def deploy_now(name):
    _cfg(name)
    dep, msg = D.enqueue(name, "manual")
    return jsonify({"ok": True, "id": dep["id"], "message": msg})


@deploy_bp.route("/api/apps/<name>/action", methods=["POST"])
@login_required
def action(name):
    cfg = _cfg(name)
    act = _data().get("action", "")
    if act not in ("start", "stop", "restart"):
        return _err("Acción no permitida")
    if _busy(name):
        return _err("Hay un despliegue en curso; espera a que termine", 409)
    if not cfg.get("active_slot"):
        return _err("La aplicación aún no tiene un despliegue exitoso", 409)
    try:
        if act == "start":
            cmd = D.compose(name, "up", "-d", "--remove-orphans", *D.dep_services(cfg), *D.app_services(cfg))
        elif act == "stop":
            cmd = D.compose(name, "stop", "-t", "30")
        else:
            cmd = D.compose(name, "restart", "-t", "30", *D.app_services(cfg))
    except D.Fail as e:
        return _err(str(e), 503)
    code, out, err = run(cmd, timeout=300)
    if code != 0:
        return _err((err or out).strip()[-1500:] or f"docker compose terminó con código {code}", 500)
    return jsonify({"ok": True})


@deploy_bp.route("/api/apps/<name>/rollback", methods=["POST"])
@login_required
def rollback(name):
    cfg = _cfg(name)
    dep_id = str(_data().get("id", ""))
    target = D.find_dep(name, dep_id) if D.DEP_ID_RE.fullmatch(dep_id) else None
    if not target or target.get("status") != "success" or not target.get("image"):
        return _err("Solo se puede volver a un despliegue exitoso con imagen", 404)
    if target["image"] == (cfg.get("current") or {}).get("image"):
        return _err("Esa versión es la que está en línea", 409)
    dep, _ = D.enqueue(name, "rollback", image=target["image"], rollback_of=dep_id, info=target)
    return jsonify({"ok": True, "id": dep["id"]})


@deploy_bp.route("/api/apps/<name>/cancel", methods=["POST"])
@login_required
def cancel(name):
    _cfg(name)
    dep_id = str(_data().get("id", ""))
    if not D.DEP_ID_RE.fullmatch(dep_id):
        return _err("Id inválido")
    ok, msg = D.cancel(name, dep_id)
    return jsonify({"ok": ok, "message": msg, "error": "" if ok else msg}), 200 if ok else 409


@deploy_bp.route("/api/apps/<name>/log")
@login_required
def deploy_log(name):
    _cfg(name)
    dep_id = request.args.get("id", "")
    dep = D.find_dep(name, dep_id) if D.DEP_ID_RE.fullmatch(dep_id) else None
    if not dep:
        return _err("Despliegue no encontrado", 404)
    offset = _num(request.args.get("offset", "0"), int)
    offset = 0 if offset is None else offset
    data, size = b"", 0
    try:
        fd = os.open(D.log_path(name, dep_id), os.O_RDONLY | D.O_NOFOLLOW | D.O_BINARY)
    except OSError:
        fd = None
    if fd is not None:
        with os.fdopen(fd, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if offset < 0:  # pedir solo el final
                offset = max(0, size - LOG_CHUNK)
            offset = min(offset, size)
            f.seek(offset)
            data = f.read(LOG_CHUNK)
        if len(data) == LOG_CHUNK and b"\n" in data:  # no cortar una línea (ni un carácter UTF-8) a la mitad
            data = data[:data.rindex(b"\n") + 1]
    new_offset = offset + len(data)
    return jsonify({"text": data.decode("utf-8", "replace"), "offset": new_offset, "size": size,
                    "status": dep["status"], "done": dep["status"] in D.FINAL and new_offset >= size})


@deploy_bp.route("/api/apps/<name>/env", methods=["GET", "POST"])
@login_required
def env(name):
    cfg = _cfg(name)
    if request.method == "GET":
        text = D.read_env(name)
        pairs, errors = D.parse_env(text)
        return jsonify({"text": text, "pairs": pairs, "errors": errors})
    d = _data()
    if d.get("mode") == "pairs":
        raw = d.get("pairs")
        if not isinstance(raw, list) or len(raw) > 1000 or not all(isinstance(p, list) and len(p) == 2 for p in raw):
            return _err("Formato inválido")
        pairs = [(str(k).strip(), v) for k, v in raw]
        errors = D.validate_pairs(pairs)
        text = D.serialize_env(pairs) if not errors else ""
    else:
        text = d.get("text")
        if not isinstance(text, str):
            return _err("Formato inválido")
        text = text.replace("\r\n", "\n")
        errors = D.parse_env(text)[1]
        if "\0" in text:
            errors.append("El texto contiene caracteres nulos")
    if errors:
        return _err("Corrige el .env: " + "; ".join(errors[:5]), errors=errors)
    if len(text.encode()) > D.ENV_MAX:
        return _err("El .env es demasiado grande (máximo 256 KB)")
    text = D.fill_db_placeholders(cfg, D.load_secrets(name), text)
    D.write_env(name, text)
    dep = None
    if d.get("apply") and cfg.get("active_slot") and (cfg.get("current") or {}).get("image"):
        dep, _ = D.enqueue(name, "config", image=cfg["current"]["image"], info=cfg["current"])
    return jsonify({"ok": True, "deployment": dep and dep["id"]})


@deploy_bp.route("/api/apps/<name>/logs")
@login_required
def container_logs(name):
    cfg = _cfg(name)
    svc = request.args.get("service", "app")
    tail = request.args.get("tail", "300")
    if tail not in ("100", "300", "1000"):
        return _err("Cantidad de líneas inválida")
    allowed = {"app": "app_" + cfg["active_slot"] if cfg.get("active_slot") else None,
               "worker": "worker" if cfg.get("worker") else None,
               "scheduler": "scheduler" if cfg.get("scheduler") else None,
               "db": "db" if cfg.get("database") in ("mariadb", "postgres") else None,
               "redis": "redis" if cfg.get("redis") else None}
    target = allowed.get(svc)
    if not target:
        return _err("Ese servicio no existe en esta aplicación", 404)
    try:
        cmd = D.compose(name, "logs", "--no-color", "--tail", tail, target)
    except D.Fail as e:
        return _err(str(e), 503)
    code, out = D._run_capped(cmd, 1024 * 1024, 20)
    out = D.redactor_for(name)(out)  # igual que artisan: los secretos del .env no llegan al navegador
    return jsonify({"ok": code == 0 or bool(out), "logs": out, "error": "" if code == 0 else out.strip()[-500:]})


@deploy_bp.route("/api/apps/<name>/artisan", methods=["POST"])
@login_required
def artisan(name):
    cfg = _cfg(name)
    d = _data()
    cmd = str(d.get("command", "")).strip()
    if cmd.startswith("php artisan "):
        cmd = cmd[12:].strip()
    if D.is_php(cfg):
        return _err("Artisan solo existe en proyectos Laravel")
    ok, why = D.valid_artisan(cmd)
    if not ok:
        return _err(why)
    if D.is_dangerous_artisan(cmd) and not d.get("confirm"):
        return _err("Comando destructivo: confírmalo", 409, needs_confirm=True)
    if not cfg.get("active_slot"):
        return _err("La aplicación aún no está desplegada", 409)
    try:
        argv = D.compose(name, "exec", "-T", "app_" + cfg["active_slot"], "php", "artisan", *cmd.split())
    except D.Fail as e:
        return _err(str(e), 503)
    code, out = D._run_capped(argv, 256 * 1024, 300)
    return jsonify({"ok": code == 0, "code": code, "output": D.redactor_for(name)(out),
                    "error": "" if code == 0 else f"Terminó con código {code}"})


@deploy_bp.route("/api/apps/<name>/config", methods=["POST"])
@login_required
def config(name):
    cfg = _cfg(name)
    if _busy(name):
        return _err("Hay un despliegue en curso; espera a que termine para cambiar la configuración", 409)
    opts, error = _opts(_data(), cfg)
    if error:
        return _err(error)
    new = {**cfg, **opts}
    if new.get("active_slot") and (new["port"] != cfg["port"] or new["hostname"] != cfg.get("hostname")):
        ok, out = D.apply_nginx(name, new, D.slot_port(new, new["active_slot"]))
        if not ok:
            return _err("No se guardó: " + out[-1500:], 409)
    D.update_config(name, lambda c: c.update(opts))
    svc_keys = ("database", "redis", "worker", "scheduler", "mem_mb", "cpus", "container_port",
                "docroot", "env_path", "persist")
    changed = any(new.get(k) != cfg.get(k) for k in svc_keys)
    if changed:
        try:
            D.apply_service_changes(name)
        except (OSError, ValueError) as e:
            return _err(f"Guardado, pero no se pudo regenerar compose: {e}", 500)
    rebuild = any(new.get(k) != cfg.get(k) for k in ("repo", "branch", "php", "php_ext", "kind"))
    return jsonify({"ok": True, "needs_apply": changed and bool(new.get("active_slot")), "needs_deploy": rebuild})


@deploy_bp.route("/api/apps/<name>/apply", methods=["POST"])
@login_required
def apply_now(name):
    """Recrea los contenedores con la imagen actual (sin compilar), con el mismo cambio sin cortes."""
    cfg = _cfg(name)
    cur = cfg.get("current") or {}
    if not cfg.get("active_slot") or not cur.get("image"):
        return _err("La aplicación aún no tiene un despliegue exitoso", 409)
    dep, _ = D.enqueue(name, "config", image=cur["image"], info=cur)
    return jsonify({"ok": True, "id": dep["id"]})


@deploy_bp.route("/api/apps/<name>/regen-key", methods=["POST"])
@login_required
def regen_key(name):
    _cfg(name, allow_draft=True)
    try:
        D.generate_key(name)
    except OSError as e:
        return _err(str(e), 500)
    return jsonify({"ok": True, "pubkey": D.public_key(name)})


@deploy_bp.route("/api/apps/<name>/secret", methods=["GET", "POST"])
@login_required
def webhook_secret(name):
    _cfg(name)
    sec = D.load_secrets(name)
    if request.method == "POST":
        import secrets as _s
        sec["webhook_secret"] = _s.token_hex(24)
        D.save_secrets(name, sec)
    return jsonify({"ok": True, "secret": sec.get("webhook_secret", "")})


@deploy_bp.route("/api/apps/<name>/delete", methods=["POST"])
@login_required
def delete(name):
    _cfg(name, allow_draft=True)
    d = _data()
    if d.get("confirm") != name:
        return _err("Escribe el nombre de la aplicación para confirmar")
    try:
        warnings = D.delete_app(name, volumes=bool(d.get("volumes")))
    except RuntimeError as e:
        return _err(str(e), 409)
    except OSError as e:
        return _err(f"No se pudo eliminar: {e}", 500)
    return jsonify({"ok": True, "warnings": warnings})


# ── Webhook de GitHub (sin sesión) ──────────────────────────────────────────

@deploy_bp.route("/webhook/<name>", methods=["POST"])
def webhook(name):
    ip = request.remote_addr
    if _hook_rate.blocked(ip) or _hook_bad.blocked(ip):
        return _err("Demasiadas peticiones", 429)
    _hook_rate.fail(ip)
    if (request.content_length or 0) > WEBHOOK_MAX:
        return _err("Cuerpo demasiado grande", 413)
    body = request.stream.read(WEBHOOK_MAX + 1)
    if len(body) > WEBHOOK_MAX:
        return _err("Cuerpo demasiado grande", 413)
    cfg = D.load_config(name) if D.valid_name(name) and name in D.list_apps() else None
    secret = D.load_secrets(name).get("webhook_secret") if cfg and not cfg.get("draft") else None
    # Misma respuesta para app inexistente y firma inválida: no revela qué apps existen.
    if not secret or not D.verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
        _hook_bad.fail(ip)
        return _err("Firma inválida", 403)
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return jsonify({"ok": True, "message": "pong"})
    if event != "push":
        return jsonify({"ok": True, "ignored": f"evento {event[:40]} ignorado"}), 202
    try:
        if body.startswith(b"payload="):  # webhook configurado como application/x-www-form-urlencoded
            body = parse_qs(body.decode("utf-8", "replace")).get("payload", [""])[0].encode()
        payload = json.loads(body)
    except ValueError:
        return _err("JSON inválido")
    if not isinstance(payload, dict) or payload.get("ref") != f"refs/heads/{cfg['branch']}" or payload.get("deleted"):
        return jsonify({"ok": True, "ignored": "otra rama"}), 202
    if not cfg.get("auto_deploy"):
        return jsonify({"ok": True, "ignored": "auto-despliegue desactivado"}), 202
    dep, msg = D.enqueue(name, "webhook")
    return jsonify({"ok": True, "id": dep["id"], "message": msg}), 202
