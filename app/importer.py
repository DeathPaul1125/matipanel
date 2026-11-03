"""Importar a Despliegues un proyecto que ya está en el servidor (carpeta de Apache + base local).

Automatiza los pasos manuales de app/docs/60-migrar-proyectos.md, una vez creada la app:
  scan()          lee la carpeta vieja: .env, base, storage, subidas en public/, git, cron, Supervisor, Apache
  merge_env()     lleva el .env viejo al de la app (APP_KEY incluida) sin pisar lo que maneja el panel
  import_db()     copia la base del MariaDB/MySQL del servidor a la base del contenedor de la app
  copy_storage()  copia storage/app al volumen storage de la app (dueño www-data, uid 33)

No toca la carpeta ni la base viejas: el sitio actual sigue funcionando hasta que se cambie el tráfico.
"""
import glob
import json
import os
import re
import subprocess
from . import deployer as D
from . import jobs

ROOTS = ("/var/www", "/srv", "/home", "/opt")  # donde suelen estar los proyectos; nunca /etc, /root…
ENV_LIMIT = 256 * 1024
WWW_UID = 33  # www-data en las imágenes serversideup/php
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
SRC_DB_RE = re.compile(r"[A-Za-z0-9_$-]{1,64}")
# Lo que el panel decide en el .env de la app (motivo que se muestra al no copiarlo)
KEEP = {"APP_ENV": "se deja production", "APP_DEBUG": "se deja false",
        "LOG_CHANNEL": "stderr: así los errores se ven en la pestaña Logs",
        "APP_URL": "cambia con la dirección nueva (ponla al cambiar el tráfico)",
        "REDIS_HOST": "lo maneja el panel", "REDIS_PORT": "lo maneja el panel"}
REDIS_KEYS = ("SESSION_DRIVER", "CACHE_STORE", "CACHE_DRIVER", "QUEUE_CONNECTION", "REDIS_CLIENT", "REDIS_PASSWORD")
UPLOAD_HINTS = {"uploads", "upload", "files", "archivos", "adjuntos", "documentos", "docs", "fotos", "imagenes",
                "images", "img", "media", "attachments", "storage"}
BUILD_ONLY = {"public/build", "public/hot", "public/storage", "public/mix-manifest.json", "public/js", "public/css",
              "public/fonts", "public/vendor"}


def valid_source(path):
    """→ (ruta real, error). Carpeta de un proyecto dentro de ROOTS (sin escapar por enlaces)."""
    p = str(path or "").strip()
    if not os.path.isabs(p) or "\0" in p or len(p) > 400:
        return None, "Escribe la ruta completa de la carpeta (por ejemplo /var/www/html/miproyecto)"
    real = os.path.realpath(p).replace(os.sep, "/")
    if not any(real == r or real.startswith(r + "/") for r in ROOTS) or real in ROOTS:
        return None, "La carpeta debe estar dentro de " + ", ".join(ROOTS)
    if real.startswith(str(D.BASE_DIR)) or not os.path.isdir(real):
        return None, "No existe esa carpeta"
    return real, ""


def candidates():
    """Proyectos Laravel en /var/www (hasta 2 niveles): carpetas con artisan."""
    found = set()
    for pat in ("/var/www/*/artisan", "/var/www/*/*/artisan", "/srv/*/artisan", "/srv/*/*/artisan"):
        found.update(os.path.dirname(p) for p in glob.glob(pat))
    return sorted(found)[:100]


def expand_refs(pairs):
    """${VAR} del .env viejo (Laravel lo expande; el panel lo tomaría literal)."""
    vals = dict(pairs)
    return [(k, re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", lambda m: vals.get(m.group(1), ""), v)) for k, v in pairs]


def plan_env(old_pairs, cfg):
    """[{key, copy, reason}] y {clave: valor} a copiar. Con base propia o externa, las DB_* son del panel;
    sin base en el panel, las DB_* se copian y un host local pasa a host.docker.internal."""
    rows, upd = [], {}
    own_db = cfg.get("database") in ("mariadb", "postgres") or bool((cfg.get("ext_db") or {}).get("host"))
    for k, v in expand_refs(old_pairs):
        reason = KEEP.get(k)
        if not reason and cfg.get("redis") and k in REDIS_KEYS:
            reason = "la app usa el Redis del panel"
        if not reason and (k == "DB_CONNECTION" or D.DB_ALIAS_RE.fullmatch(k)):
            if own_db:
                reason = "la base la maneja el panel"
            elif k.endswith("HOST") and v.strip() in LOCAL_HOSTS:
                v = "host.docker.internal"
        if reason:
            rows.append({"key": k, "copy": False, "reason": reason})
        else:
            rows.append({"key": k, "copy": True, "reason": "APP_KEY: la misma, para leer lo cifrado" if k == "APP_KEY"
                         else ("host local → host.docker.internal" if v == "host.docker.internal" else "")})
            upd[k] = v
    return rows, upd


def read_old_env(src):
    path = os.path.join(src, ".env")
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    with open(path, "rb") as f:
        return f.read(ENV_LIMIT).decode("utf-8", "replace")


def _git(src, *args):
    return D._run(["git", "-c", "safe.directory=*", "-C", src, *args], 30)


def _lines_with(paths, needle):
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if needle in s and not s.startswith("#"):
                        out.append({"file": p, "line": s[:300]})
        except OSError:
            pass
    return out[:20]


def upload_dirs(src, git_ok):
    """Carpetas de public/ que no vienen del repositorio (o con nombre de subidas): se perderían al desplegar."""
    if git_ok:
        code, out, _ = _git(src, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "--", "public")
        items = [l.strip().rstrip("/") for l in out.splitlines() if l.strip()] if code == 0 else []
    else:
        pub = os.path.join(src, "public")
        items = [f"public/{e}" for e in sorted(os.listdir(pub)) if e.lower() in UPLOAD_HINTS
                 and os.path.isdir(os.path.join(pub, e))] if os.path.isdir(pub) else []
    return [i for i in items if i not in BUILD_ONLY and not i.startswith("public/build/")][:30]


def _du(path):
    code, out, _ = D._run(["du", "-sb", path], 120)
    try:
        return int(out.split()[0]) if code == 0 else None
    except (ValueError, IndexError):
        return None


def local_databases():
    from .modules.databases import _my_opts
    code, out, _ = D._run(["mysql", *_my_opts(), "-N", "-B", "-e", "SHOW DATABASES"], 20)
    return set(out.split()) if code == 0 else None


def scan(src, cfg):
    """Lo que hay en la carpeta vieja y lo que el asistente puede hacer con ello."""
    out = {"path": src, "laravel": os.path.isfile(os.path.join(src, "artisan")), "warnings": []}
    text = read_old_env(src)
    pairs = D.parse_env(text)[0] if text is not None else []
    out["env"] = {"found": text is not None, "rows": plan_env(pairs, cfg)[0], "has_key": any(k == "APP_KEY" and v for k, v in pairs)}
    old = dict(pairs)
    git_ok = os.path.isdir(os.path.join(src, ".git"))
    out["git"] = {"repo": git_ok, "remote": "", "pending": 0}
    if git_ok:
        out["git"]["remote"] = _git(src, "remote", "get-url", "origin")[1].strip()
        code, st, _ = _git(src, "status", "--porcelain")
        out["git"]["pending"] = len([l for l in st.splitlines() if l.strip()]) if code == 0 else 0
    # Base de datos
    host, dbn = old.get("DB_HOST", "").strip(), old.get("DB_DATABASE", "").strip()
    db = {"source": dbn, "host": host, "engine": old.get("DB_CONNECTION", "mysql"), "importable": False, "why": ""}
    if cfg.get("database") != "mariadb":
        db["why"] = ("la app usa una base externa o ninguna: no hace falta copiar datos" if cfg.get("database") == "none"
                     else "el asistente copia bases MySQL/MariaDB; para PostgreSQL usa la guía")
    elif db["engine"] not in ("mysql", "mariadb"):
        db["why"] = "el proyecto viejo no usa MySQL/MariaDB"
    elif host not in LOCAL_HOSTS:
        db["why"] = f"la base vieja está en otro servidor ({host}); cópiala con la guía"
    elif not SRC_DB_RE.fullmatch(dbn or "-"):
        db["why"] = "el .env viejo no dice qué base usa (DB_DATABASE)"
    else:
        names = local_databases()
        if names is None:
            db["why"] = "no se pudo entrar al MariaDB/MySQL del servidor como root"
        elif dbn not in names:
            db["why"] = f"la base «{dbn}» no existe en este servidor"
        else:
            db["importable"] = True
    db["target"] = D.db_name(cfg)
    out["db"] = db
    sa = os.path.join(src, "storage", "app")
    out["storage"] = {"exists": os.path.isdir(sa), "bytes": _du(sa) if os.path.isdir(sa) else 0}
    out["uploads"] = upload_dirs(src, git_ok)
    # Lo que habrá que apagar al cambiar el tráfico
    crons = ["/etc/crontab", *glob.glob("/etc/cron.d/*"), *glob.glob("/var/spool/cron/crontabs/*")]
    out["cron"] = _lines_with(crons, src)
    out["supervisor"] = [x["file"] for x in _lines_with(glob.glob("/etc/supervisor/conf.d/*"), src)]
    out["apache"] = sorted({x["file"] for x in _lines_with(glob.glob("/etc/apache2/sites-enabled/*"), src)})
    # Pistas de compilación
    try:
        with open(os.path.join(src, "package.json"), encoding="utf-8") as f:
            scripts = (json.load(f) or {}).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        scripts = None
    out["mix"] = scripts is not None and "build" not in scripts and os.path.isfile(os.path.join(src, "webpack.mix.js"))
    try:
        with open(os.path.join(src, "composer.json"), encoding="utf-8") as f:
            out["php_req"] = str(((json.load(f) or {}).get("require") or {}).get("php", ""))[:40]
    except (OSError, ValueError, AttributeError):
        out["php_req"] = ""
    return out


def merge_env(name, cfg, src):
    """Copia el .env viejo al de la app. → (ok, mensaje)."""
    text = read_old_env(src)
    if text is None:
        return False, "La carpeta no tiene .env"
    pairs, errors = D.parse_env(text)
    _, upd = plan_env(pairs, cfg)
    errs = D.validate_pairs(list(upd.items()))
    if errs:
        return False, "El .env viejo tiene valores que no se pueden copiar: " + "; ".join(errs[:3])
    D.write_env(name, D.env_set(D.read_env(name), upd))
    msg = f"{len(upd)} variables copiadas al .env de la app" + ("" if "APP_KEY" in upd else " (¡el .env viejo no tenía APP_KEY!)")
    return True, msg + ("; líneas ignoradas: " + "; ".join(errors[:3]) if errors else "")


def _say(msg):
    jobs.write(jobs.current_job(), msg + "\n")


def import_db(name, cfg, source):
    """mysqldump de la base del servidor → mariadb del contenedor de la app (reemplaza sus tablas).
    Cambia la collation de MySQL 8 (utf8mb4_0900_ai_ci), que MariaDB no conoce. → (ok, mensaje)."""
    from .modules.databases import _my_opts
    if not SRC_DB_RE.fullmatch(source or "-"):
        return False, "Nombre de la base vieja inválido"
    container = f"{D.project_name(name)}-db-1"
    code, out, _ = D._run(["docker", "inspect", "-f", "{{.State.Running}}", container], 20)
    if code != 0 or out.strip() != "true":
        return False, "La base de la app no está en marcha: despliega la app primero"
    target = D.db_name(cfg)
    dump_cmd = ["mysqldump", *_my_opts(), "--single-transaction", "--routines", "--triggers", "--events",
                "--default-character-set=utf8mb4", "--", source]
    load_cmd = ["docker", "exec", "-i", container, "sh", "-c",
                'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot --default-character-set=utf8mb4 "$@"', "sh",
                "--", target]
    _say(f"$ mysqldump {source} | mariadb (contenedor {container}) {target}")
    dump = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/")
    load = subprocess.Popen(load_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/")
    sent, mark = 0, 0
    try:
        for line in dump.stdout:
            if b"_0900_" in line:
                line = re.sub(rb"utf8mb4_0900_[a-z_]+", b"utf8mb4_unicode_ci", line)
            load.stdin.write(line)
            sent += len(line)
            if sent - mark >= 50 * 1024 * 1024:
                mark = sent
                _say(f"  {sent // (1024 * 1024)} MB copiados…")
        load.stdin.close()
    except OSError:
        pass  # la importación murió: su error se lee abajo
    derr = dump.stderr.read().decode(errors="replace")
    lout, lerr = load.communicate()
    dcode, lcode = dump.wait(), load.returncode
    if dcode != 0:
        return False, "mysqldump falló: " + derr.strip()[-800:]
    if lcode != 0:
        return False, "La importación falló: " + lerr.decode(errors="replace").strip()[-800:]
    return True, f"Base «{source}» copiada a «{target}» ({max(1, sent // (1024 * 1024))} MB)"


def copy_storage(name, src):
    """storage/app viejo → volumen storage de la app (sin storage/framework ni logs). → (ok, mensaje)."""
    sa = os.path.join(src, "storage", "app")
    if not os.path.isdir(sa):
        return False, "La carpeta no tiene storage/app"
    vol = f"{D.project_name(name)}_storage"
    code, mp, err = D._run(["docker", "volume", "inspect", vol, "--format", "{{.Mountpoint}}"], 20)
    mp = mp.strip()
    if code != 0 or not mp.startswith("/"):
        return False, "El volumen storage de la app aún no existe: despliega la app primero"
    dest = os.path.join(mp, "app")
    os.makedirs(dest, exist_ok=True)
    _say(f"$ cp -a {sa}/. {dest}/")
    code, o, e = D._run(["cp", "-a", "--", sa + "/.", dest + "/"], 3600)
    if code != 0:
        return False, "cp falló: " + (o + e).strip()[-800:]
    code, o, e = D._run(["chown", "-R", f"{WWW_UID}:{WWW_UID}", "--", dest], 600)
    if code != 0:
        return False, "chown falló: " + (o + e).strip()[-800:]
    return True, f"storage/app copiado al volumen {vol}"


if __name__ == "__main__":
    cfg_own = {"database": "mariadb", "redis": True}
    pairs = [("APP_NAME", "ERP"), ("APP_KEY", "base64:x"), ("APP_DEBUG", "true"), ("DB_HOST", "127.0.0.1"),
             ("DB_DATABASE", "erp"), ("MAIL_FROM_NAME", "${APP_NAME}"), ("SESSION_DRIVER", "file"), ("API_TOKEN", "t")]
    rows, upd = plan_env(pairs, cfg_own)
    assert upd == {"APP_NAME": "ERP", "APP_KEY": "base64:x", "MAIL_FROM_NAME": "ERP", "API_TOKEN": "t"}, upd
    assert {r["key"] for r in rows if not r["copy"]} == {"APP_DEBUG", "DB_HOST", "DB_DATABASE", "SESSION_DRIVER"}
    _, upd2 = plan_env(pairs, {"database": "none"})  # sin base en el panel: se reusa la del servidor
    assert upd2["DB_HOST"] == "host.docker.internal" and upd2["DB_DATABASE"] == "erp" and upd2["SESSION_DRIVER"] == "file"
    _, upd3 = plan_env(pairs, {"database": "none", "ext_db": {"host": "host.docker.internal"}})
    assert "DB_HOST" not in upd3
    assert valid_source("relativa")[0] is None and valid_source("/etc")[0] is None and valid_source("/var/www")[0] is None
    assert SRC_DB_RE.fullmatch("erp_2024") and not SRC_DB_RE.fullmatch("a;b") and not SRC_DB_RE.fullmatch("")
    print("ok")
