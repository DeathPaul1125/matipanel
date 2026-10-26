"""Bases de datos: MySQL/MariaDB y PostgreSQL vía sus clientes de línea de comandos."""
import gzip
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import zlib
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, jsonify, request, Response
from flask_login import login_required
from ..util import run, bytes_to_human

databases_bp = Blueprint("databases", __name__)

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,63}$")      # BD y roles PG (PG trunca a 63)
MY_USER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")  # usuario MySQL (va como literal '...')
HOST_RE = re.compile(r"^[A-Za-z0-9_.%:-]{1,255}$")
SYSTEM_DBS = {
    "mysql": {"information_schema", "performance_schema", "sys", "mysql"},
    "pg": {"template0", "template1", "postgres"},
}
# Cuentas que el panel necesita intactas (root via unix_socket, etc.)
SYSTEM_USERS = {
    "mysql": {"root", "mariadb.sys", "mysql.sys", "mysql.session", "mysql.infoschema", "debian-sys-maint"},
    "pg": {"postgres"},
}
SERVICES = {"mysql": ("mariadb", "mysql"), "pg": ("postgresql",)}
CHARSETS = ("utf8mb4", "latin1")
DEBIAN_CNF = "/etc/mysql/debian.cnf"
MAX_ROWS, MAX_OUT, QUERY_TIMEOUT = 1000, 2 * 1024 * 1024, 60
MAX_UPLOAD = 512 * 1024 * 1024
CHUNK = 64 * 1024


def _installed(engine):
    return shutil.which("mysql" if engine == "mysql" else "psql") is not None


def needs_engine(f):
    @wraps(f)
    def wrapper(engine, *a, **kw):
        if not _installed(engine):
            return jsonify({"ok": False, "error": "Motor no instalado"}), 404
        return f(engine, *a, **kw)
    return wrapper


# ── Conexión ─────────────────────────────────────────────────────────

_my_defaults = None  # None = sin probar; [] = unix_socket/.my.cnf; [--defaults-file=...] = debian.cnf


def _my_opts():
    """Root entra por unix_socket (o /root/.my.cnf); si da Access denied, prueba debian.cnf."""
    global _my_defaults
    if _my_defaults is not None:
        return _my_defaults
    code, _, err = run(["mysql", "--batch"], input="SELECT 1", timeout=10)
    if code == 0:
        _my_defaults = []
    elif "Access denied" in err and os.access(DEBIAN_CNF, os.R_OK):
        opts = [f"--defaults-file={DEBIAN_CNF}"]
        if run(["mysql", *opts, "--batch"], input="SELECT 1", timeout=10)[0] == 0:
            _my_defaults = opts
    return _my_defaults or []  # si el servidor está caído no se cachea


_my_sandbox = None


def _my_sandbox_opt():
    """MariaDB >= 10.5.25 / 11.x: --sandbox desactiva \\! (shell), \\. (source), tee… en el cliente.
    Un volcado restaurado como root no debe poder ejecutar comandos del sistema."""
    global _my_sandbox
    if _my_sandbox is None:
        code, out, _ = run(["mysql", "--help"], timeout=10)
        _my_sandbox = ["--sandbox"] if code == 0 and "--sandbox" in out else []
    return _my_sandbox


def _popen(cmd, **kw):
    """Popen en su propio grupo de procesos: así _kill() alcanza también al psql/pg_dump
    que lanza runuser (matar solo a runuser dejaba al hijo vivo y la lectura colgada)."""
    return subprocess.Popen(cmd, cwd="/", start_new_session=True, **kw)


def _kill(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        try:
            p.kill()
        except OSError:
            pass


def _tail(f, n=4000):
    """Últimos n caracteres de un archivo temporal (stderr) sin leerlo entero."""
    f.seek(0, os.SEEK_END)
    f.seek(max(0, f.tell() - n * 4))
    return f.read().decode(errors="replace").strip()[-n:]


def _cmd(engine, db=None, names=False):
    """Comando cliente que lee SQL por stdin. Salida separada por tabuladores."""
    if engine == "mysql":
        cmd = ["mysql", *_my_opts(), "--batch"]
        if not names:
            cmd += ["--skip-column-names", "--raw"]
        if db:
            cmd.append(f"--database={db}")
        return cmd
    cmd = ["runuser", "-u", "postgres", "--", "psql", "-X", "-A", "-F", "\t",
           "-v", "ON_ERROR_STOP=1", "-P", "footer=off", "-d", db or "postgres"]
    if not names:
        cmd.append("-t")
    return cmd


def _exec(engine, sql, db=None, timeout=30):
    """Ejecuta SQL interno (sin cabeceras). Retorna (code, filas, err)."""
    code, out, err = run(_cmd(engine, db), timeout=timeout, input=sql)
    rows = [line.split("\t") for line in out.splitlines() if line]
    return code, rows, err.strip()


def _do(engine, sql, db=None):
    code, _, err = _exec(engine, sql, db)
    if code != 0:
        return jsonify({"ok": False, "error": err or "Fallo desconocido"}), 500
    return jsonify({"ok": True})


def _bad(msg):
    return jsonify({"ok": False, "error": msg}), 400


# ── Quoting (los nombres ya pasaron por regex) ──────────────────────

def _str(engine, s):
    if engine == "mysql":  # duplicar comillas sirve también con NO_BACKSLASH_ESCAPES
        s = s.replace("\\", "\\\\")
    return "'" + s.replace("'", "''") + "'"


def _ident(engine, name):
    """Identificador citado. Duplica el delimitador, así sirve también para nombres que no pasaron
    por NAME_RE (p. ej. tablas leídas del catálogo en el explorador)."""
    if engine == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def _account(engine, user, host):
    return f"{_str('mysql', user)}@{_str('mysql', host)}" if engine == "mysql" else _ident("pg", user)


def _valid_password(pw):
    return isinstance(pw, str) and 0 < len(pw) <= 256 and all(ord(c) >= 32 for c in pw)


def _user_args(engine, data):
    """Valida user/host del JSON. Retorna (user, host, error)."""
    user, host = data.get("user", ""), data.get("host", "localhost") or "localhost"
    if not isinstance(user, str) or not (MY_USER_RE if engine == "mysql" else NAME_RE).fullmatch(user):
        return None, None, "Nombre de usuario inválido"
    if engine == "mysql" and not (isinstance(host, str) and HOST_RE.fullmatch(host)):
        return None, None, "Host inválido"
    if user in SYSTEM_USERS[engine]:
        return None, None, f"'{user}' es una cuenta del sistema; no se modifica desde el panel"
    return user, host, None


def _valid_db(name):
    return isinstance(name, str) and bool(NAME_RE.fullmatch(name))


def _grant_sql(engine, user, host, db, revoke=False, escape=True):
    if engine == "mysql":
        # En GRANT a nivel de BD, '_' es comodín: se escapa para que sea literal
        target = "`" + (db.replace("_", "\\_") if escape else db) + "`.*"
        acc = _account(engine, user, host)
        return (f"REVOKE ALL PRIVILEGES ON {target} FROM {acc};" if revoke
                else f"GRANT ALL PRIVILEGES ON {target} TO {acc};")
    u, d = _ident("pg", user), _ident("pg", db)
    verb, prep = ("REVOKE", "FROM") if revoke else ("GRANT", "TO")
    # PG 15+ ya no da CREATE en public a todos: se otorga sobre el esquema y objetos
    return "\n".join(f"{verb} ALL ON {obj} {prep} {u};" for obj in (
        f"DATABASE {d}", "SCHEMA public", "ALL TABLES IN SCHEMA public", "ALL SEQUENCES IN SCHEMA public"))


# ── Vistas ───────────────────────────────────────────────────────────

@databases_bp.route("/")
@login_required
def index():
    return render_template("databases.html", has_mysql=_installed("mysql"), has_pg=_installed("pg"))


@databases_bp.route("/api/<any(mysql, pg):engine>/status")
@login_required
@needs_engine
def status(engine):
    service, running = SERVICES[engine][0], False
    for unit in SERVICES[engine]:
        code, out, _ = run(["systemctl", "is-active", "--", unit], timeout=5)
        if out.strip() == "active":
            service, running = unit, True
            break

    if engine == "mysql":
        sql = ("SELECT VERSION();\n"
               "SELECT s.SCHEMA_NAME, s.DEFAULT_CHARACTER_SET_NAME, COUNT(t.TABLE_NAME),"
               " IFNULL(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0)"
               " FROM information_schema.SCHEMATA s"
               " LEFT JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = s.SCHEMA_NAME"
               " GROUP BY s.SCHEMA_NAME, s.DEFAULT_CHARACTER_SET_NAME ORDER BY s.SCHEMA_NAME;")
    else:
        sql = ("SHOW server_version;\n"
               "SELECT datname, pg_encoding_to_char(encoding), '', pg_database_size(oid)"
               " FROM pg_database ORDER BY datname;")
    code, rows, err = _exec(engine, sql)
    if code != 0 or not rows:
        return jsonify({"running": running, "service": service, "version": "",
                        "databases": [], "error": err or "No se pudo conectar"})

    dbs = []
    for r in rows[1:]:
        if len(r) < 4:
            continue
        size = int(r[3]) if r[3].isdigit() else 0
        dbs.append({"name": r[0], "charset": r[1], "tables": r[2], "size": size,
                    "size_h": bytes_to_human(size), "system": r[0] in SYSTEM_DBS[engine]})
    total = sum(d["size"] for d in dbs)
    return jsonify({"running": running, "service": service, "version": rows[0][0],
                    "databases": dbs, "total_h": bytes_to_human(total)})


@databases_bp.route("/api/<any(mysql, pg):engine>/users")
@login_required
@needs_engine
def users(engine):
    if engine == "mysql":
        sql = ("SELECT u.User, u.Host, IFNULL(u.plugin, ''),"
               " IFNULL((SELECT GROUP_CONCAT(d.Db ORDER BY d.Db SEPARATOR ',') FROM mysql.db d"
               " WHERE d.User = u.User AND d.Host = u.Host), '')"
               " FROM mysql.user u ORDER BY u.User, u.Host;")
    else:
        sql = ("SELECT r.rolname, '', CASE WHEN r.rolsuper THEN 'superusuario'"
               " WHEN r.rolcanlogin THEN 'login' ELSE 'rol (sin login)' END,"
               " CASE WHEN r.rolsuper THEN '*' ELSE COALESCE((SELECT string_agg(d.datname, ',' ORDER BY d.datname)"
               " FROM pg_database d WHERE NOT d.datistemplate"
               " AND has_database_privilege(r.oid, d.oid, 'CREATE')), '') END"
               " FROM pg_roles r WHERE r.rolname !~ '^pg_' ORDER BY 1;")
    code, rows, err = _exec(engine, sql)
    if code != 0:
        return jsonify({"error": err or "No se pudo listar"}), 500
    out = []
    for r in rows:
        r += [""] * (4 - len(r))
        dbs = [d.replace("\\_", "_").replace("\\%", "%") for d in r[3].split(",") if d]
        out.append({"user": r[0], "host": r[1], "auth": r[2], "dbs": dbs,
                    "system": r[0] in SYSTEM_USERS[engine] or not r[0]})
    return jsonify({"users": out})


# ── Bases de datos ───────────────────────────────────────────────────

@databases_bp.route("/api/<any(mysql, pg):engine>/db/create", methods=["POST"])
@login_required
@needs_engine
def db_create(engine):
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    if not _valid_db(name):
        return _bad("Nombre inválido (solo letras, números y _; máx. 63)")
    sql = f"CREATE DATABASE {_ident(engine, name)}"
    if engine == "mysql":
        charset = data.get("charset") or "utf8mb4"
        if charset not in CHARSETS:
            return _bad("Charset no permitido")
        sql += f" CHARACTER SET {charset}"
    return _do(engine, sql + ";")


@databases_bp.route("/api/<any(mysql, pg):engine>/db/drop", methods=["POST"])
@login_required
@needs_engine
def db_drop(engine):
    data = request.get_json(silent=True) or {}
    name = data.get("name", "")
    if not _valid_db(name):
        return _bad("Nombre inválido")
    if name in SYSTEM_DBS[engine]:
        return _bad("No se puede eliminar una base de datos del sistema")
    if data.get("confirm") != name:
        return _bad("La confirmación no coincide con el nombre")
    force = " WITH (FORCE)" if engine == "pg" else ""  # PG: cierra conexiones abiertas
    return _do(engine, f"DROP DATABASE {_ident(engine, name)}{force};")


# ── Usuarios ─────────────────────────────────────────────────────────

@databases_bp.route("/api/<any(mysql, pg):engine>/user/create", methods=["POST"])
@login_required
@needs_engine
def user_create(engine):
    data = request.get_json(silent=True) or {}
    user, host, err = _user_args(engine, data)
    if err:
        return _bad(err)
    pw, db = data.get("password", ""), data.get("db", "")
    if not _valid_password(pw):
        return _bad("Contraseña inválida (1-256 caracteres imprimibles)")
    if db and not _valid_db(db):
        return _bad("Base de datos inválida")
    acc = _account(engine, user, host)
    if engine == "mysql":
        sql = f"CREATE USER {acc} IDENTIFIED BY {_str(engine, pw)};"
    else:
        sql = f"CREATE ROLE {acc} LOGIN PASSWORD {_str(engine, pw)};"
    code, _, err = _exec(engine, sql)
    if code != 0:
        return jsonify({"ok": False, "error": err or "Fallo desconocido"}), 500
    if db:
        code, _, err = _exec(engine, _grant_sql(engine, user, host, db), db=db if engine == "pg" else None)
        if code != 0:
            return jsonify({"ok": False, "error": f"Usuario creado, pero falló el GRANT: {err}"}), 500
    return jsonify({"ok": True})


@databases_bp.route("/api/<any(mysql, pg):engine>/user/password", methods=["POST"])
@login_required
@needs_engine
def user_password(engine):
    data = request.get_json(silent=True) or {}
    user, host, err = _user_args(engine, data)
    if err:
        return _bad(err)
    pw = data.get("password", "")
    if not _valid_password(pw):
        return _bad("Contraseña inválida (1-256 caracteres imprimibles)")
    acc = _account(engine, user, host)
    if engine == "mysql":
        return _do(engine, f"ALTER USER {acc} IDENTIFIED BY {_str(engine, pw)};")
    return _do(engine, f"ALTER ROLE {acc} PASSWORD {_str(engine, pw)};")


@databases_bp.route("/api/<any(mysql, pg):engine>/user/drop", methods=["POST"])
@login_required
@needs_engine
def user_drop(engine):
    data = request.get_json(silent=True) or {}
    user, host, err = _user_args(engine, data)
    if err:
        return _bad(err)
    return _do(engine, f"DROP {'USER' if engine == 'mysql' else 'ROLE'} {_account(engine, user, host)};")


@databases_bp.route("/api/<any(mysql, pg):engine>/grant", methods=["POST"])
@login_required
@needs_engine
def grant(engine):
    data = request.get_json(silent=True) or {}
    user, host, err = _user_args(engine, data)
    if err:
        return _bad(err)
    db = data.get("db", "")
    if not _valid_db(db):
        return _bad("Base de datos inválida")
    revoke = bool(data.get("revoke"))
    code, _, err = _exec(engine, _grant_sql(engine, user, host, db, revoke), db=db if engine == "pg" else None)
    if code != 0 and engine == "mysql" and revoke:
        # GRANT hecho fuera del panel sin escapar '_': se revoca con el nombre tal cual
        code, _, err = _exec(engine, _grant_sql(engine, user, host, db, revoke, escape=False))
    if code != 0:
        return jsonify({"ok": False, "error": err or "Fallo desconocido"}), 500
    return jsonify({"ok": True})


# ── Consola SQL ──────────────────────────────────────────────────────

def _feed(stream, data):
    try:
        stream.write(data)
        stream.close()
    except OSError:
        pass


def _run_capped(cmd, sql, env=None):
    """Como run(), pero corta la salida en MAX_OUT y mata el proceso a los QUERY_TIMEOUT s."""
    with tempfile.TemporaryFile() as errf:
        p = _popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf, env=env)
        timer = threading.Timer(QUERY_TIMEOUT, _kill, args=(p,))
        timer.start()
        threading.Thread(target=_feed, args=(p.stdin, sql.encode()), daemon=True).start()
        out = p.stdout.read(MAX_OUT + 1)
        truncated = len(out) > MAX_OUT
        if truncated:
            _kill(p)
        p.stdout.close()
        code = p.wait()
        timer.cancel()
        errf.seek(0)
        err = errf.read(64 * 1024).decode(errors="replace").strip()
    if code == -9 and not truncated:
        err = err or f"Tiempo de espera agotado ({QUERY_TIMEOUT} s)"
    return code, out[:MAX_OUT].decode(errors="replace"), err, truncated


@databases_bp.route("/api/<any(mysql, pg):engine>/query", methods=["POST"])
@login_required
@needs_engine
def query(engine):
    data = request.get_json(silent=True) or {}
    sql, db = data.get("sql", ""), data.get("db", "")
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 1024 * 1024:
        return _bad("Consulta vacía o demasiado grande (máx. 1 MB)")
    if db and not _valid_db(db):
        return _bad("Base de datos inválida")
    # PG: el servidor también cancela la sentencia (matar al cliente no detiene un pg_sleep)
    env = {**os.environ, "PGOPTIONS": f"-c statement_timeout={QUERY_TIMEOUT}s"} if engine == "pg" else None
    code, out, err, truncated = _run_capped(_cmd(engine, db, names=True), sql, env)

    lines = out.splitlines()
    if truncated and lines:
        lines.pop()  # la última línea puede estar cortada
    columns = lines[0].split("\t") if lines else []
    rows = [line.split("\t") for line in lines[1:MAX_ROWS + 1]]
    truncated = truncated or len(lines) - 1 > MAX_ROWS
    result = {"ok": code == 0 or truncated, "columns": columns, "rows": rows,
              "truncated": truncated, "raw": "\n".join(lines[:MAX_ROWS + 1])}
    if code != 0 and not truncated:
        result["error"] = err or "Fallo desconocido"
        return jsonify(result), 400
    return jsonify(result)


# ── Backup / restauración ───────────────────────────────────────────

def _dump_stream(cmd):
    """Genera el volcado comprimido en gzip sin cargarlo en memoria."""
    errf = tempfile.TemporaryFile()
    p = _popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf)
    z = zlib.compressobj(6, zlib.DEFLATED, 31)  # wbits=31 -> formato gzip
    try:
        while chunk := p.stdout.read(CHUNK):
            if data := z.compress(chunk):
                yield data
        if p.wait() != 0:  # el error queda como comentario al final del .sql
            errf.seek(0)
            msg = errf.read(8192).decode(errors="replace")
            note = "\n-- ERROR: el volcado falló\n" + "".join(f"-- {l}\n" for l in msg.splitlines())
            yield z.compress(note.encode())
        yield z.flush()
    finally:
        if p.poll() is None:  # descarga cancelada por el navegador: muere también pg_dump
            _kill(p)
        p.stdout.close()
        p.wait()
        errf.close()


def _dump_cmd(engine, db, table=None, schema="public"):
    """mysqldump / pg_dump de una BD entera o de una sola tabla (el explorador también lo usa).
    db y table ya deben estar validados (existen); el nombre de tabla va tras "--" o citado."""
    if engine == "mysql":
        if table:
            return ["mysqldump", *_my_opts(), "--single-transaction", "--triggers", "--", db, table]
        return ["mysqldump", *_my_opts(), "--single-transaction", "--routines", "--triggers", "--", db]
    cmd = ["runuser", "-u", "postgres", "--", "pg_dump"]
    if table:
        # Patrón de pg_dump: entre comillas dobles todo es literal (*, ?, . no son comodines)
        cmd.append("--table=" + _ident("pg", schema) + "." + _ident("pg", table))
    return cmd + ["--", db]


@databases_bp.route("/api/<any(mysql, pg):engine>/backup/<db>")
@login_required
@needs_engine
def backup(engine, db):
    if not _valid_db(db):
        return jsonify({"error": "Base de datos inválida"}), 400
    code, _, err = _exec(engine, "SELECT 1;", db=db, timeout=10)
    if code != 0:
        return jsonify({"error": err or "No se pudo conectar a la base de datos"}), 404
    cmd = _dump_cmd(engine, db)
    fname = f"{db}-{datetime.now():%Y%m%d-%H%M}.sql.gz"
    return Response(_dump_stream(cmd), mimetype="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@databases_bp.route("/api/<any(mysql, pg):engine>/restore/<db>", methods=["POST"])
@login_required
@needs_engine
def restore(engine, db):
    if not _valid_db(db):
        return _bad("Base de datos inválida")
    if not request.content_length:
        return jsonify({"ok": False, "error": "Falta Content-Length"}), 411
    if request.content_length > MAX_UPLOAD:
        return jsonify({"ok": False, "error": "Archivo demasiado grande (máx. 512 MB)"}), 413
    f = request.files.get("file")
    if not f or not f.filename or not f.filename.lower().endswith((".sql", ".sql.gz", ".gz")):
        return _bad("Sube un archivo .sql o .sql.gz")
    if _exec(engine, "SELECT 1;", db=db, timeout=10)[0] != 0:
        return _bad("La base de datos no existe o el servidor no responde")

    src = gzip.GzipFile(fileobj=f.stream) if f.filename.lower().endswith(".gz") else f.stream
    if engine == "mysql":
        # se detiene en el primer error; --sandbox (si el cliente lo soporta) bloquea \! y similares
        cmd = ["mysql", *_my_opts(), *_my_sandbox_opt(), f"--database={db}"]
    else:
        # ponytail: psql no tiene modo sandbox; un \! del volcado corre como el usuario postgres (no root)
        cmd = ["runuser", "-u", "postgres", "--", "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", db]
    bad = None
    with tempfile.TemporaryFile() as errf:
        p = _popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errf)
        try:
            while chunk := src.read(CHUNK):
                p.stdin.write(chunk)
        except BrokenPipeError:
            pass  # el cliente terminó antes (error SQL): el motivo está en stderr
        except (OSError, EOFError, zlib.error) as e:
            bad = f"Archivo inválido: {e}"
            _kill(p)
        try:
            p.stdin.close()
        except OSError:
            pass
        code = p.wait()
        err = _tail(errf)  # psql puede emitir miles de NOTICE: no cargar todo en memoria
    if bad:
        return _bad(bad)
    if code != 0:
        return jsonify({"ok": False, "error": err or f"Falló con código {code}"}), 500
    return jsonify({"ok": True, "output": err})
