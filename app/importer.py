"""Importar a Despliegues un proyecto que ya está en el servidor (carpeta de Apache + base local).

Automatiza los pasos manuales de app/docs/60-migrar-proyectos.md, una vez creada la app:
  scan()          lee la carpeta vieja: .env, base, storage, subidas en public/, git, cron, Supervisor, Apache
  merge_env()     lleva el .env viejo al de la app (APP_KEY incluida) sin pisar lo que maneja el panel
  import_db()     copia la base del MariaDB/MySQL del servidor a la base del contenedor de la app
                  (si la app usa PostgreSQL, import_db_pg la convierte con pgloader)
  copy_storage()  copia storage/app al volumen storage de la app (dueño www-data, uid 33)

No toca la carpeta ni la base viejas: el sitio actual sigue funcionando hasta que se cambie el tráfico.
"""
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
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
MYSQL_ONLY_RE = re.compile(r"\b(FIELD|DATE_FORMAT|GROUP_CONCAT|IFNULL|STR_TO_DATE)\s*\(")  # no existen en PostgreSQL
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
    db = {"source": dbn, "host": host, "engine": old.get("DB_CONNECTION", "mysql"), "importable": False, "why": "",
          "convert": cfg.get("database") == "postgres"}
    if cfg.get("database") not in ("mariadb", "postgres"):
        db["why"] = "la app usa una base externa o ninguna: no hace falta copiar datos"
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
    out["mysql_only"] = mysql_only_sql(src) if db["convert"] else []
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
    if cfg.get("database") == "postgres":
        return import_db_pg(name, cfg, source)
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


def mysql_only_sql(src, limit=20):
    """Funciones de MySQL que PostgreSQL no tiene, en app/ del proyecto viejo. → [{"file", "line"}]"""
    found = []
    for root, dirs, files in os.walk(os.path.join(src, "app")):
        dirs.sort()
        for fn in sorted(files):
            if not fn.endswith(".php"):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for n, line in enumerate(f, 1):
                        if MYSQL_ONLY_RE.search(line):
                            found.append({"file": os.path.relpath(path, src).replace(os.sep, "/") + f":{n}",
                                          "line": line.strip()[:200]})
                            if len(found) >= limit:
                                return found
            except OSError:
                continue
    return found


def schema_diff(my_cols, pg_cols):
    """Columnas (tabla, columna) de la base vieja y de la nueva. → (tablas y columnas que sobran en la vieja,
    columnas que faltan en la vieja); solo cuenta tablas de las dos, y nunca la tabla migrations."""
    my_cols = {c for c in my_cols if c[0] != "migrations"}
    pg_cols = {c for c in pg_cols if c[0] != "migrations"}
    my_t, pg_t = {t for t, _ in my_cols}, {t for t, _ in pg_cols}
    both = my_t & pg_t
    return (sorted(my_t - pg_t), sorted(c for c in my_cols - pg_cols if c[0] in both),
            sorted(c for c in pg_cols - my_cols if c[0] in both))


def pgloader_errors(text):
    """Suma la columna errors del resumen de pgloader (filas «nombre  errores  …»); None si no hay resumen."""
    total, inside = None, False
    for line in text.splitlines():
        if re.match(r"\s*table name\s+errors\b", line):
            inside, total = True, total or 0
            continue
        # nombre (palabras separadas por un espacio), 2+ espacios y la columna errors (número o ✓)
        m = re.match(r"\s*\S+(?: \S+)*\s{2,}(\S+)", line) if inside else None
        if m and m.group(1).isdigit():
            total += int(m.group(1))
    return total


def _q(ident):
    return "`" + ident.replace("`", "``") + "`"


def _lit(text):
    return text.replace("\\", "\\\\").replace("'", "''")


def _mysql(sql, db=None, timeout=600):
    from .modules.databases import _my_opts
    return D._run(["mysql", *_my_opts(), "-N", "-B", *([db] if db else []), "-e", sql], timeout)


def import_db_pg(name, cfg, source):
    """Base MariaDB/MySQL del servidor → PostgreSQL del contenedor de la app, con pgloader, solo datos (las
    tablas las creó el primer despliegue con las migraciones). Trabaja sobre una copia temporal de la base
    vieja, de la que quita las tablas y columnas que ya no existen en el código (migraciones borradas después
    de ejecutarse), porque pgloader falla con columnas que PostgreSQL no tiene. → (ok, mensaje)"""
    from .modules.databases import _my_opts
    from .modules.deploy import _apt_install
    container = f"{D.project_name(name)}-db-1"
    code, out, _ = D._run(["docker", "inspect", "-f", "{{.State.Running}}", container], 20)
    if code != 0 or out.strip() != "true":
        return False, "La base de la app no está en marcha: despliega la app primero"
    if not shutil.which("pgloader"):
        _say("$ apt install pgloader")
        ok, aout, amsg = _apt_install(["pgloader"])
        if not ok:
            return False, f"No se pudo instalar pgloader: {amsg}"
    denv = dict(D.parse_env(D.read_text(D.app_dir(name) / "db.env", ENV_LIMIT) or "")[0])
    pg_user, pg_db, pg_pw = denv.get("POSTGRES_USER", ""), denv.get("POSTGRES_DB", ""), denv.get("POSTGRES_PASSWORD", "")
    code, ips, _ = D._run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}",
                           container], 20)
    pg_ip = (ips.split() or [""])[0]
    if not (pg_user and pg_db and pg_ip):
        return False, "No se pudieron leer los datos de la base PostgreSQL de la app (db.env o la IP del contenedor)"
    tmp = source[:56] + "_mpmigra"
    pw = secrets.token_urlsafe(24)
    load_path = None
    try:
        _say(f"$ mysqldump {source} | mysql {tmp}   (copia temporal; la base vieja no se toca)")
        code, _, err = _mysql(f"DROP DATABASE IF EXISTS {_q(tmp)}; CREATE DATABASE {_q(tmp)}")
        if code != 0:
            return False, "No se pudo crear la copia temporal: " + err.strip()[-500:]
        dump = subprocess.Popen(["mysqldump", *_my_opts(), "--single-transaction", "--routines", "--triggers",
                                 "--", source], stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/")
        load = subprocess.Popen(["mysql", *_my_opts(), "--", tmp], stdin=dump.stdout,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/")
        dump.stdout.close()
        _, lerr = load.communicate()
        derr = dump.stderr.read().decode(errors="replace")
        if dump.wait() != 0:
            return False, "mysqldump falló: " + derr.strip()[-800:]
        if load.returncode != 0:
            return False, "La copia temporal falló: " + lerr.decode(errors="replace").strip()[-800:]

        code, my_out, err = _mysql(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c JOIN information_schema.tables t"
            " ON t.table_schema = c.table_schema AND t.table_name = c.table_name"
            f" WHERE c.table_schema = '{tmp}' AND t.table_type = 'BASE TABLE'")
        if code != 0:
            return False, "No se pudieron leer las columnas de la copia: " + err.strip()[-500:]
        code, pg_out, err = D._run(["docker", "exec", container, "sh", "-c",
                                    'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "$1" -c "$2"', "sh", "\t",
                                    "SELECT table_name, column_name FROM information_schema.columns"
                                    " WHERE table_schema = 'public'"], 60)
        if code != 0:
            return False, "No se pudieron leer las columnas de PostgreSQL: " + err.strip()[-500:]
        cols = lambda text: {tuple(l.split("\t", 1)) for l in text.splitlines() if "\t" in l}
        drop_t, drop_c, missing = schema_diff(cols(my_out), cols(pg_out))

        for t, c in drop_c:
            _say(f"  - columna que ya no existe en el código: {t}.{c}")
            code, fks, _ = _mysql("SELECT constraint_name FROM information_schema.key_column_usage"
                                  f" WHERE table_schema = '{tmp}' AND table_name = '{_lit(t)}'"
                                  f" AND column_name = '{_lit(c)}'"
                                  " AND referenced_table_name IS NOT NULL")
            sql = "".join(f"ALTER TABLE {_q(t)} DROP FOREIGN KEY {_q(fk)}; " for fk in fks.split())
            code, _, err = _mysql(sql + f"ALTER TABLE {_q(t)} DROP COLUMN {_q(c)}", tmp)
            if code != 0:
                return False, f"No se pudo quitar {t}.{c} de la copia: " + err.strip()[-500:]
        for t in drop_t:
            _say(f"  - tabla que ya no existe en el código: {t}")
            code, _, err = _mysql(f"SET FOREIGN_KEY_CHECKS = 0; DROP TABLE {_q(t)}", tmp)
            if code != 0:
                return False, f"No se pudo quitar la tabla {t} de la copia: " + err.strip()[-500:]
        for t, c in missing:
            _say(f"  ! PostgreSQL tiene {t}.{c} y la base vieja no: queda vacía o con su valor por defecto")

        code, _, err = _mysql(f"CREATE USER IF NOT EXISTS 'mp_pgloader'@'127.0.0.1' IDENTIFIED BY '{pw}';"
                              f" ALTER USER 'mp_pgloader'@'127.0.0.1' IDENTIFIED BY '{pw}';"
                              f" GRANT SELECT, SHOW VIEW ON {_q(tmp)}.* TO 'mp_pgloader'@'127.0.0.1'")
        if code != 0:
            return False, "No se pudo crear el usuario temporal de MariaDB: " + err.strip()[-500:]
        fd, load_path = tempfile.mkstemp(prefix="matipanel-pgloader-", suffix=".load")  # 0600: lleva una contraseña
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"LOAD DATABASE\n"
                    f"  FROM mysql://mp_pgloader:{pw}@127.0.0.1:3306/{tmp}\n"
                    f"  INTO postgresql://{pg_user}@{pg_ip}:5432/{pg_db}?sslmode=disable\n"
                    f"WITH data only, truncate, reset sequences, workers = 4, concurrency = 1\n"
                    f"SET PostgreSQL PARAMETERS session_replication_role to 'replica'\n"
                    f"EXCLUDING TABLE NAMES MATCHING 'migrations'\n"
                    f"ALTER SCHEMA '{tmp}' RENAME TO 'public';\n")
        _say("$ pgloader (solo datos; vacía las tablas de PostgreSQL antes de copiar)")
        # La contraseña de PostgreSQL va por PGPASSWORD y no en el archivo ni en la línea de comandos
        code, pout, perr = D._run(["pgloader", load_path], 6 * 3600, env={**os.environ, "PGPASSWORD": pg_pw})
        text = pout + perr
        for line in text.splitlines()[-60:]:
            _say(line)
        errors = pgloader_errors(text)
        if code != 0 or errors:
            return False, (f"pgloader terminó con {errors} errores" if errors else f"pgloader falló (código {code})") \
                + ": " + "\n".join(l for l in text.splitlines() if "ERROR" in l)[-800:]
        msg = f"Base «{source}» (MariaDB) copiada a «{D.db_name(cfg)}» (PostgreSQL)"
        if drop_c or drop_t:
            msg += "; se omitió lo que ya no existe en el código: " + ", ".join(
                [f"tabla {t}" for t in drop_t] + [f"{t}.{c}" for t, c in drop_c][:30])
        if missing:
            msg += "; la base vieja no tenía: " + ", ".join(f"{t}.{c}" for t, c in missing[:30])
        return True, msg
    finally:
        _mysql(f"DROP USER IF EXISTS 'mp_pgloader'@'127.0.0.1'; DROP DATABASE IF EXISTS {_q(tmp)}")
        if load_path:
            try:
                os.unlink(load_path)
            except OSError:
                pass


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
    my = {("users", "id"), ("users", "email"), ("contrataciones", "id"), ("contrataciones", "fecha_solicitud"),
          ("viejas", "id"), ("migrations", "id"), ("migrations", "batch")}
    pg = {("users", "id"), ("users", "email"), ("users", "avatar"), ("contrataciones", "id"), ("nuevas", "id"),
          ("migrations", "id")}
    assert schema_diff(my, pg) == (["viejas"], [("contrataciones", "fecha_solicitud")], [("users", "avatar")])
    summary = """             table name     errors       read   imported      bytes      total time
-----------------------  ---------  ---------  ---------  ---------  --------------
        fetch meta data          0         12         12                     0.050s
---------------------------------  ---------  ---------  ---------  ---------  --------------
       "public"."users"          {e}        100        100     10.2 kB          0.030s
   "public"."empleados"          0         50         50      5.1 kB          0.020s
-----------------------  ---------  ---------  ---------  ---------  --------------
COPY Threads Completion          0          4          4                     0.100s
      Total import time          ✓        150        150     15.3 kB          0.150s
"""
    assert pgloader_errors(summary.format(e=0)) == 0 and pgloader_errors(summary.format(e=3)) == 3
    assert pgloader_errors("FATAL: no se pudo conectar") is None
    assert MYSQL_ONLY_RE.search("orderByRaw(\"FIELD(estado, 'a')\")") and not MYSQL_ONLY_RE.search("$this->field('x')")
    assert _q("a`b") == "`a``b`"
    sc = scan(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), {"database": "postgres", "name": "x"})
    assert sc["db"]["convert"] and "guía" not in sc["db"]["why"], sc["db"]
    print("ok")
