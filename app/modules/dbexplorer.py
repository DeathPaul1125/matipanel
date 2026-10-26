"""Explorador visual de bases de datos (al estilo phpMyAdmin / Adminer).

Navega, edita y administra tablas de MariaDB/MySQL y PostgreSQL con los mismos clientes de línea
de comandos que app.modules.databases (root por unix_socket o debian.cnf / usuario postgres, SQL
por stdin, tiempo límite y muerte del grupo de procesos). Los clientes no admiten parámetros
enlazados, así que todo el SQL se arma aquí con estas reglas:

- VALORES: siempre como literales generados por lit(). MySQL: hex `_utf8mb4 X'…'` / `X'…'` (o un
  número que ya pasó una regex); PostgreSQL: '…' con comillas duplicadas (standard_conforming_strings
  se fuerza en la sesión) o $mp$…$mp$ si el texto lleva barras invertidas; bytea con decode('…','hex').
- IDENTIFICADORES: solo nombres que devuelve el catálogo (information_schema / pg_catalog) para lo
  pedido por el navegador, citados duplicando el delimitador. Los nombres NUEVOS (crear, renombrar)
  pasan por NEW_RE.
- Las lecturas van en sesión de solo lectura. Bases y esquemas del sistema: se ven, no se tocan.
"""
import codecs
import csv
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, Response
from flask_login import login_required

from ..util import run, bytes_to_human
from .databases import (needs_engine, _installed, _my_opts, _my_sandbox_opt, _popen, _kill, _feed, _tail,
                        _run_capped, _dump_stream, _dump_cmd, _str, _ident, SYSTEM_DBS, QUERY_TIMEOUT, CHUNK)
from .files import _disposition

dbexplorer_bp = Blueprint("dbexplorer", __name__)

DB_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_$-]{0,63}$")  # BD existentes (sin '=', ':' ni '/': psql -d)
NEW_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")      # nombres que crea el panel
INT_RE = re.compile(r"^[+-]?\d{1,40}$")
NUM_RE = re.compile(r"^[+-]?(?:\d{1,65}(?:\.\d{0,30})?|\.\d{1,30})(?:[eE][+-]?\d{1,3})?$")
BIT_RE = re.compile(r"^[01]{1,64}$")
HEX_RE = re.compile(r"^(?:[0-9a-fA-F]{2})*$")
MAX_HEX = 128 * 1024 * 1024
PG_SYS_SCHEMAS = ("pg_catalog", "information_schema")
PER_PAGE = (25, 50, 100, 500)
EXACT_MAX = 50_000              # hasta aquí (estimado) se cuenta exacto sin pedirlo
PREVIEW_CHARS, PREVIEW_BYTES = 2000, 64
CELL_MAX = 256 * 1024           # valor completo de una celda (visor / edición)
MAX_FILTERS, MAX_KEYS = 30, 500
CONSOLE_ROWS, CONSOLE_STMTS = 1000, 50
IMPORT_MAX = 100 * 1024 * 1024
IMPORT_ROWS, IMPORT_BYTES = 500, 512 * 1024
IMPORT_TIMEOUT, EXPORT_TIMEOUT = 15 * 60, 60 * 60
MARK = "__matipanel__"

# ── Tipos ────────────────────────────────────────────────────────────
# clase ("kind"): decide cómo se lee, se escribe y se edita cada columna

MY_KIND = {}
for _k, _names in {
    "int": "tinyint smallint mediumint int integer bigint year", "num": "decimal numeric float double real",
    "bit": "bit", "text": "char varchar tinytext text mediumtext longtext",
    "bin": "binary varbinary tinyblob blob mediumblob longblob", "date": "date", "datetime": "datetime timestamp",
    "time": "time", "json": "json", "enum": "enum", "set": "set",
    "geo": "geometry point linestring polygon multipoint multilinestring multipolygon geometrycollection",
}.items():
    MY_KIND.update(dict.fromkeys(_names.split(), _k))
PG_KIND = {}
for _k, _names in {
    "int": "int2 int4 int8 oid", "num": "numeric float4 float8", "bool": "bool", "bin": "bytea", "date": "date",
    "datetime": "timestamp timestamptz", "time": "time timetz interval", "json": "json jsonb",
    "text": "text varchar bpchar name citext uuid xml inet cidr macaddr",
}.items():
    PG_KIND.update(dict.fromkeys(_names.split(), _k))
LONG_KINDS = {"text", "json", "set", "array", "other"}  # se recortan en la rejilla

# Tipos que se pueden elegir al crear/modificar columnas: nombre → (clase, parámetro)
# parámetro: None | "len" (obligatoria) | "len?" (opcional) | "prec" (p[,s]) | "fsp" (0-6) | "values"
MY_TYPES = {
    "TINYINT": ("int", None), "SMALLINT": ("int", None), "MEDIUMINT": ("int", None), "INT": ("int", None),
    "BIGINT": ("int", None), "DECIMAL": ("num", "prec"), "FLOAT": ("num", None), "DOUBLE": ("num", None),
    "BIT": ("bit", "len?"), "BOOLEAN": ("int", None),
    "CHAR": ("text", "len?"), "VARCHAR": ("text", "len"), "TINYTEXT": ("text", None), "TEXT": ("text", None),
    "MEDIUMTEXT": ("text", None), "LONGTEXT": ("text", None),
    "BINARY": ("bin", "len?"), "VARBINARY": ("bin", "len"), "TINYBLOB": ("bin", None), "BLOB": ("bin", None),
    "MEDIUMBLOB": ("bin", None), "LONGBLOB": ("bin", None),
    "DATE": ("date", None), "DATETIME": ("datetime", "fsp"), "TIMESTAMP": ("datetime", "fsp"),
    "TIME": ("time", "fsp"), "YEAR": ("int", None), "JSON": ("json", None),
    "ENUM": ("enum", "values"), "SET": ("set", "values"),
}
PG_TYPES = {
    "smallint": ("int", None), "integer": ("int", None), "bigint": ("int", None), "numeric": ("num", "prec"),
    "real": ("num", None), "double precision": ("num", None), "boolean": ("bool", None),
    "character": ("text", "len?"), "character varying": ("text", "len?"), "text": ("text", None),
    "bytea": ("bin", None), "date": ("date", None),
    "timestamp without time zone": ("datetime", "fsp"), "timestamp with time zone": ("datetime", "fsp"),
    "time without time zone": ("time", "fsp"), "interval": ("time", None), "uuid": ("text", None),
    "json": ("json", None), "jsonb": ("json", None), "inet": ("text", None), "cidr": ("text", None),
    "macaddr": ("text", None),
}
LEN_MAX = {"BIT": 64, "CHAR": 255, "BINARY": 255, "VARCHAR": 65535, "VARBINARY": 65535,
           "character": 10485760, "character varying": 10485760}
DEFAULT_EXPRS = {"mysql": ["CURRENT_TIMESTAMP"],
                 "pg": ["CURRENT_TIMESTAMP", "CURRENT_DATE", "now()", "gen_random_uuid()"]}
TEXT_PREFIX = {"tinytext", "text", "mediumtext", "longtext", "tinyblob", "blob", "mediumblob", "longblob"}

OPS = ("like", "eq", "ne", "gt", "lt", "starts", "null", "notnull")
READ_KW = {"select", "show", "explain", "describe", "desc", "with", "values", "table"}
WRITE_KW = {"insert", "update", "delete", "merge", "into", "replace", "call", "do", "copy"}
# Comandos del cliente mysql que solo se reconocen al principio de una sentencia
MY_CLIENT_CMDS = {"system", "source", "tee", "notee", "pager", "nopager", "edit", "connect", "delimiter",
                  "charset", "prompt", "rehash", "warnings", "nowarning", "exit", "quit", "go", "ego", "print",
                  "status", "clear", "use", "help", "query_attributes", "resetconnection", "ssl_session_data_print"}


class Bad(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


@dbexplorer_bp.errorhandler(Bad)
def _bad_handler(e):
    return jsonify({"ok": False, "error": str(e)}), e.code


# ── Literales e identificadores (funciones puras) ────────────────────

def my_text(s):
    """Texto → literal MySQL en hex con charset: no hay comilla ni barra que escapar."""
    try:
        return "_utf8mb4 X'" + s.encode("utf-8").hex() + "'"
    except UnicodeEncodeError:
        raise Bad("El texto tiene caracteres inválidos")


def pg_text(s):
    """Texto → literal PostgreSQL. Sin barras invertidas basta duplicar comillas (seguro con
    standard_conforming_strings on u off); con barras, comillas dólar con una etiqueta que no
    aparezca en el texto (ni pegada a su final)."""
    if "\x00" in s:
        raise Bad("PostgreSQL no admite el carácter NUL en textos")
    if "\\" not in s:
        return "'" + s.replace("'", "''") + "'"
    tag, n = "$mp$", 0
    while (s + tag).find(tag) != len(s):
        n += 1
        tag = f"$mp{n}$"
    return tag + s + tag


def lit(engine, kind, v):
    """Valor del navegador (None | str | número | bool | {"hex": "…"}) → literal SQL seguro."""
    if v is None:
        return "NULL"
    if isinstance(v, dict):
        h = v.get("hex")
        if not isinstance(h, str) or len(h) > MAX_HEX or not HEX_RE.fullmatch(h):
            raise Bad("Valor hexadecimal inválido")
        return f"X'{h}'" if engine == "mysql" else f"decode('{h}', 'hex')"
    if isinstance(v, bool):
        v = ("1" if v else "0") if engine == "mysql" else ("true" if v else "false")
    elif isinstance(v, (int, float)):
        v = repr(v)
    if not isinstance(v, str):
        raise Bad("Valor no admitido")
    if engine == "mysql":
        if (kind == "int" and INT_RE.fullmatch(v)) or (kind == "num" and NUM_RE.fullmatch(v)):
            return v
        if kind == "bit" and BIT_RE.fullmatch(v):
            return f"b'{v}'"
        if kind == "bin":
            return "X'" + v.encode("utf-8", "surrogatepass").hex() + "'"
        return my_text(v)
    if kind == "bin":
        return f"convert_to({pg_text(v)}, 'UTF8')"
    return pg_text(v)


def qi(engine, name):
    """Identificador citado. El nombre debe venir del catálogo (o de NEW_RE)."""
    if not isinstance(name, str) or not name or len(name) > 128 or any(ord(c) < 32 for c in name):
        raise Bad("Nombre no admitido por el explorador")
    if engine == "mysql" and "\\" in name:
        # el cliente mysql trata "\" dentro de `…` distinto que el servidor: no se arriesga
        raise Bad("Nombre con barra invertida: no admitido por el explorador")
    return _ident(engine, name)


def like_escape(v):
    """Escapa comodines de LIKE con '!' (ESCAPE '!'): no depende de NO_BACKSLASH_ESCAPES."""
    return v.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def csv_line(vals, delim=","):
    """Línea CSV: NULL → campo vacío; '' → "" (así se distinguen al reimportar)."""
    out = []
    for v in vals:
        if v is None:
            out.append("")
        elif v == "" or v != v.strip() or any(ch in v for ch in (delim, '"', "\n", "\r")):
            out.append('"' + v.replace('"', '""') + '"')
        else:
            out.append(v)
    return delim.join(out) + "\r\n"


def detect_delimiter(sample):
    """Separador de un CSV: el que da el mismo número de campos (≥ 2) en casi todas las líneas.
    Con varios candidatos válidos gana tab, luego ';' (CSV con coma decimal), luego ',' y '|'."""
    lines = [ln for ln in sample.splitlines() if ln.strip()][:30]
    fallback, fallback_cols = ",", 1
    for d in ("\t", ";", ",", "|"):
        try:
            counts = [len(r) for r in csv.reader(lines, delimiter=d)]
        except csv.Error:
            continue
        if not counts:
            continue
        mode = max(set(counts), key=counts.count)
        if mode >= 2 and counts.count(mode) >= max(1, int(len(counts) * 0.8)):
            return d
        if mode > fallback_cols:
            fallback, fallback_cols = d, mode
    return fallback


# ── Lexer de SQL (consola) ───────────────────────────────────────────

_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z_0-9]*)?\$")


def split_sql(sql, engine):
    """Divide en sentencias respetando comillas, comentarios y $tag$ (PG).
    Retorna (lista de (texto, esqueleto), error). El esqueleto es la sentencia sin comentarios y con
    el contenido de las cadenas vaciado: sirve para mirar palabras clave sin falsos positivos.
    Una barra invertida fuera de comillas es un comando del cliente (\\! ejecuta en el shell): se rechaza."""
    stmts, skel, start, i, n = [], [], 0, 0, len(sql)
    my = engine == "mysql"
    while i < n:
        c = sql[i]
        if c == "'" or c == '"' or (c == "`" and my):
            j = i + 1
            # MySQL: \ escapa en '…' y "…". PG: solo en E'…'
            esc = (my and c != "`") or (not my and c == "'" and i > 0 and sql[i - 1] in "eE"
                                        and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_")))
            while j < n:
                if esc and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            skel.append(c + c)
            i = j + 1
            continue
        if c == "-" and sql.startswith("--", i) and (not my or i + 2 >= n or sql[i + 2] in " \t\r\n"):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            skel.append(" ")
            continue
        if c == "#" and my:
            j = sql.find("\n", i)
            i = n if j < 0 else j
            skel.append(" ")
            continue
        if c == "/" and sql.startswith("/*", i) and not (my and (sql.startswith("/*!", i) or sql.startswith("/*M!", i))):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                elif not my and sql.startswith("/*", j):  # PG anida comentarios
                    depth += 1
                    j += 2
                else:
                    j += 1
            i = j
            skel.append(" ")
            continue
        if c == "$" and not my and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] in "_$")):
            m = _DOLLAR.match(sql, i)
            if m:
                j = sql.find(m.group(), m.end())
                i = n if j < 0 else j + len(m.group())
                skel.append("''")
                continue
        if c == "\\":
            return None, "Los comandos del cliente (\\…) no están permitidos en la consola"
        if c == ";":
            stmts.append((sql[start:i], "".join(skel)))
            start, skel = i + 1, []
        else:
            skel.append(c)
        i += 1
    stmts.append((sql[start:], "".join(skel)))
    return [(t.strip(), s.strip()) for t, s in stmts if s.strip()], None


def _words(skel):
    return re.findall(r"[A-Za-z_]+", skel.lower())


# ── Lectura de resultados ────────────────────────────────────────────

_XML_DECL = re.compile(r"<\?xml[^>]*\?>")
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0d\x0e-\x1f\ufffe\uffff]")
_BAD_REF = re.compile(r"&#(?:x0*([0-8bcef]|1[0-9a-f])|0*([0-8]|1[124-9]|2[0-9]|3[01]));", re.I)
_PUA = re.compile("[\ue000-\ue01f]")


def _xml_safe(text):
    """XML 1.0 no admite caracteres de control: van a la zona de uso privado y se restauran
    después (_unpua). \\r también, para que el parser no lo convierta en \\n."""
    def ref(m):
        code = int(m.group(1), 16) if m.group(1) else int(m.group(2))
        return chr(0xE000 + code)
    text = _BAD_REF.sub(ref, text)
    return _BAD_XML.sub(lambda m: chr(0xE000 + ord(m.group())) if m.group() < " " else "\ufffd", text)


def _unpua(s):
    return _PUA.sub(lambda m: chr(ord(m.group()) - 0xE000), s) if s else s


class MyXml:
    """Lector incremental de la salida de `mysql --xml` (varios <resultset> seguidos, cada uno con su
    declaración <?xml?>). NULL es xsi:nil="true"; el texto vacío es un campo vacío."""
    NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"

    def __init__(self):
        self._p = ET.XMLPullParser(events=("start", "end"))
        self._rest, self._rs, self.broken = "", None, False
        self._push("<mp>")

    def _push(self, text):
        if text and not self.broken:
            try:
                self._p.feed(text)
            except ET.ParseError:
                self.broken = True

    def feed(self, text):
        text = self._rest + text
        cut = text.rfind("<")
        if cut != -1 and text.find(">", cut) == -1:  # etiqueta a medias: espera al siguiente trozo
            text, self._rest = text[:cut], text[cut:]
        else:
            self._rest = ""
        self._push(_XML_DECL.sub("", _xml_safe(text)))
        return self._events()

    def close(self):
        self._push(_XML_DECL.sub("", _xml_safe(self._rest)) + "</mp>")
        self._rest = ""
        if not self.broken:
            try:
                self._p.close()
            except ET.ParseError:
                self.broken = True
        return self._events()

    def _events(self):
        out = []
        try:
            for ev, el in self._p.read_events():
                if el.tag == "resultset" and ev == "start":
                    self._rs = el
                    out.append(("rs", None))
                elif el.tag == "row" and ev == "end":
                    out.append(("row", ([_unpua(f.get("name", "")) for f in el],
                                        [None if f.get(self.NIL) == "true" else _unpua(f.text or "") for f in el])))
                    if self._rs is not None:
                        self._rs.clear()  # no acumular filas ya leídas (exportaciones grandes)
        except ET.ParseError:
            self.broken = True
        return out


def parse_my_xml(text):
    """Salida completa de mysql --xml → ([{"cols": [...], "rows": [[str|None]]}], incompleta)."""
    rd, sets = MyXml(), []
    for ev, data in rd.feed(text) + rd.close():
        if ev == "rs":
            sets.append({"cols": [], "rows": []})
        elif sets:
            if not sets[-1]["rows"]:
                sets[-1]["cols"] = data[0]
            sets[-1]["rows"].append(data[1])
    return sets, rd.broken


class _Obj(list):
    """Objeto JSON como lista de pares: conserva el orden y las columnas repetidas."""


class _Num(str):
    """Número JSON conservado como texto exacto (numeric de PG no cabe en un float)."""


def _jload(line):
    return json.loads(line, object_pairs_hook=_Obj, parse_float=_Num, parse_int=_Num)


def _jdump(v):
    if isinstance(v, _Obj):
        return "{" + ", ".join(json.dumps(k, ensure_ascii=False) + ": " + _jdump(x) for k, x in v) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_jdump(x) for x in v) + "]"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, _Num):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def _jtext(v):
    """Valor de row_to_json → texto para mostrar (None = NULL)."""
    if v is None or isinstance(v, str):
        return str(v) if v is not None else None
    if isinstance(v, bool):
        return "true" if v else "false"
    return _jdump(v)


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


# ── Ejecución ────────────────────────────────────────────────────────

_flavor = None


def _is_mariadb():
    global _flavor
    if _flavor is None:
        code, out, _ = run(["mysql", *_my_opts(), "--batch", "--skip-column-names"], input="SELECT VERSION();", timeout=10)
        if code == 0:
            _flavor = "mariadb" if "mariadb" in out.lower() else "mysql"
    return _flavor == "mariadb"


def _client(engine, db=None, quiet=True):
    if engine == "mysql":
        cmd = ["mysql", *_my_opts(), *_my_sandbox_opt(), "--xml", "--quick", "--default-character-set=utf8mb4"]
        return cmd + ([f"--database={db}"] if db else [])
    cmd = ["runuser", "-u", "postgres", "--", "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-P", "footer=off"]
    return cmd + (["-q"] if quiet else []) + ["-d", db or "postgres"]


def _pg_env(readonly, timeout=QUERY_TIMEOUT):
    opts = ["-c standard_conforming_strings=on", "-c client_min_messages=warning", "-c lock_timeout=15s",
            f"-c statement_timeout={timeout}s" if timeout else "-c statement_timeout=0"]
    if readonly:
        opts.append("-c default_transaction_read_only=on")
    return {**os.environ, "PGOPTIONS": " ".join(opts), "PGCLIENTENCODING": "UTF8"}


def _my_prefix(readonly, limit_time=True):
    s = ""
    if limit_time:  # el servidor corta la consulta (matar al cliente no la detiene)
        s += (f"SET SESSION max_statement_time = {QUERY_TIMEOUT};\n" if _is_mariadb()
              else f"SET SESSION max_execution_time = {QUERY_TIMEOUT * 1000};\n")
    return s + ("SET SESSION TRANSACTION READ ONLY;\n" if readonly else "SET SESSION lock_wait_timeout = 15;\n")


def _clean_err(err):
    err = re.sub(r"^psql:<stdin>:\d+: ", "", err or "", flags=re.M)
    err = re.sub(r"^(ERROR \d+ \(\w+\)) at line \d+", r"\1", err, flags=re.M)
    err = "\n".join(ln for ln in err.splitlines() if "[Warning]" not in ln)
    return err.strip()[:4000]


def _exec(engine, db, sql, readonly=True, quiet=True):
    """→ (code, salida, error, truncada). Lecturas: sesión de solo lectura y tiempo límite."""
    if engine == "mysql":
        sql, env = _my_prefix(readonly, limit_time=readonly) + sql, None
    else:
        env = _pg_env(readonly)
    code, out, err, truncated = _run_capped(_client(engine, db, quiet), sql, env)
    return code, out, _clean_err(err), truncated


def _q(engine, db, sqls):
    """Consultas internas de lectura. MySQL: una lista de filas por sentencia (vía XML).
    PG: cada sentencia debe devolver UNA fila con UN jsonb (ver _pgagg)."""
    sep = ";\n"
    code, out, err, trunc = _exec(engine, db, sep.join(sqls) + sep)
    if code != 0 or trunc:
        raise Bad(err or ("Respuesta demasiado grande" if trunc else "No se pudo consultar el servidor"), 502)
    if engine == "mysql":
        sets, broken = parse_my_xml(out)
        if broken or len(sets) != len(sqls):
            raise Bad("Respuesta inesperada del servidor MySQL", 502)
        return [s["rows"] for s in sets]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) != len(sqls):
        raise Bad("Respuesta inesperada del servidor PostgreSQL", 502)
    return [json.loads(ln) for ln in lines]


def _pgagg(exprs, rest, order=""):
    return (f"SELECT coalesce(jsonb_agg(jsonb_build_array({exprs}){' ORDER BY ' + order if order else ''}),"
            f" '[]'::jsonb) {rest}")


def _mut(engine, db, sql):
    """Ejecuta SQL que modifica. MySQL → resultsets; PG → líneas de salida."""
    code, out, err, _ = _exec(engine, db, sql, readonly=False)
    if code != 0:
        raise Bad(err or "El servidor rechazó la operación")
    if engine == "mysql":
        return parse_my_xml(out)[0]
    return [ln for ln in out.splitlines() if ln.strip()]


# ── Contexto de la petición ──────────────────────────────────────────

def _json():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise Bad("Petición inválida")
    return d


def _ctx(engine, d):
    db = d.get("db")
    if not isinstance(db, str) or not DB_RE.fullmatch(db):
        raise Bad("Base de datos inválida")
    schema = None
    if engine == "pg":
        schema = d.get("schema") or "public"
        if not isinstance(schema, str) or len(schema) > 63 or "\x00" in schema:
            raise Bad("Esquema inválido")
    return db, schema


def _readonly_reason(engine, db, schema):
    if db in SYSTEM_DBS[engine]:
        return "Base de datos del sistema: solo lectura."
    if engine == "pg" and (schema in PG_SYS_SCHEMAS or schema.startswith("pg_")):
        return "Esquema del sistema: solo lectura."
    return None


def _require_writable(engine, db, schema):
    reason = _readonly_reason(engine, db, schema)
    if reason:
        raise Bad(reason + " El explorador no modifica bases ni esquemas del sistema.", 403)


# ── Metadatos de una tabla ───────────────────────────────────────────

def parse_coltype(engine, coltype):
    """Tipo del catálogo → {base (de la lista permitida o None), length, unsigned, values}."""
    out = {"base": None, "length": "", "unsigned": False, "values": []}
    if engine == "mysql":
        m = re.match(r"^(\w+)(?:\((.*)\))?\s*(.*)$", coltype or "", re.S)
        if not m:
            return out
        base, args, rest = m.group(1).upper(), m.group(2) or "", m.group(3).lower()
        if base in ("ENUM", "SET"):
            out["values"] = [v.replace("''", "'") for v in re.findall(r"'((?:[^']|'')*)'", args)]
        elif base in MY_TYPES and MY_TYPES[base][1]:
            out["length"] = args
        out.update(base=base if base in MY_TYPES else None, unsigned="unsigned" in rest)
        return out
    m = re.fullmatch(r"([a-z ]+?)(?:\((\d+(?:,\d+)?)\))?( with(?:out)? time zone)?", coltype or "")
    if m:
        base = m.group(1) + (m.group(3) or "")
        if base in ("timestamp", "time"):
            base += " without time zone"
        if base in PG_TYPES:
            out.update(base=base, length=m.group(2) or "")
    return out


def _my_default_form(c, mariadb):
    """Valor por defecto actual → cómo prellenar el formulario {kind, value}."""
    d, extra = c["default"], c["extra"].lower()
    if d is None:
        return {"kind": "none"}
    if mariadb:  # MariaDB ≥ 10.2.7 lo da como expresión SQL: 'texto', 0, NULL, current_timestamp()
        if d == "NULL":
            return {"kind": "null"}
        m = re.fullmatch(r"'((?:[^'\\]|'')*)'", d, re.S)
        if m:
            return {"kind": "value", "value": m.group(1).replace("''", "'")}
        if NUM_RE.fullmatch(d):
            return {"kind": "value", "value": d}
        if re.fullmatch(r"(?i)current_timestamp(\(\))?", d):
            return {"kind": "expr", "value": "CURRENT_TIMESTAMP"}
        return {"kind": "keep", "value": d}
    if "default_generated" in extra:
        if re.fullmatch(r"(?i)current_timestamp(\(\d?\))?", d):
            return {"kind": "expr", "value": "CURRENT_TIMESTAMP"}
        return {"kind": "keep", "value": d}
    return {"kind": "value", "value": d}


def _pg_default_form(d, identity):
    if d is None or identity:
        return {"kind": "none"}
    if re.fullmatch(r"NULL(::[\w .\"\[\]]+)?", d):
        return {"kind": "null"}
    m = re.fullmatch(r"'((?:[^']|'')*)'(::[\w .\"\[\]()]+)?", d, re.S)
    if m:
        return {"kind": "value", "value": m.group(1).replace("''", "'")}
    m = re.fullmatch(r"\(?(-?\d+(?:\.\d+)?)\)?(::[\w ]+)?", d)
    if m:
        return {"kind": "value", "value": m.group(1)}
    if d in ("true", "false"):
        return {"kind": "value", "value": d}
    if d in DEFAULT_EXPRS["pg"]:
        return {"kind": "expr", "value": d}
    return {"kind": "keep", "value": d}


def _my_meta(db, table):
    s, t = my_text(db), my_text(table)
    w = f"TABLE_SCHEMA = {s} AND TABLE_NAME = {t}"
    info, cols, stats, fks = _q("mysql", None, [
        "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS, DATA_LENGTH + INDEX_LENGTH, TABLE_COLLATION,"
        f" TABLE_COMMENT, AUTO_INCREMENT FROM information_schema.TABLES WHERE {w}",
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA,"
        f" COLUMN_COMMENT, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS WHERE {w} ORDER BY ORDINAL_POSITION",
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, COLUMN_NAME, SUB_PART, INDEX_TYPE"
        f" FROM information_schema.STATISTICS WHERE {w} ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME,"
        " k.REFERENCED_COLUMN_NAME, r.UPDATE_RULE, r.DELETE_RULE FROM information_schema.KEY_COLUMN_USAGE k"
        " JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA"
        " AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME"
        f" WHERE k.TABLE_SCHEMA = {s} AND k.TABLE_NAME = {t} AND k.REFERENCED_TABLE_NAME IS NOT NULL"
        " ORDER BY k.CONSTRAINT_NAME, k.ORDINAL_POSITION",
    ])
    # El catálogo puede comparar sin distinguir mayúsculas: se exige coincidencia exacta
    info = [r for r in info if r[0] == table]
    if not info:
        raise Bad("La tabla no existe", 404)
    mariadb = _is_mariadb()
    r = info[0]
    columns = []
    for c in (c for c in cols if c[0] == table):
        extra = c[7] or ""
        col = {"name": c[1], "type": c[2] or "", "data_type": (c[3] or "").lower(), "nullable": c[4] == "YES",
               "default": c[5], "key": c[6] or "", "extra": extra, "comment": c[8] or "",
               "maxlen": _int(c[9]), "auto": "auto_increment" in extra.lower(),
               "generated": bool(re.search(r"(?i)\b(VIRTUAL|STORED|PERSISTENT) GENERATED\b", extra))}
        col["kind"] = MY_KIND.get(col["data_type"], "text")
        col.update(parse_coltype("mysql", col["type"]))
        col["editable"] = not col["generated"] and col["kind"] != "geo"
        col["sortable"] = True
        col["default_form"] = _my_default_form(col, mariadb)
        columns.append(col)
    indexes = {}
    for s_ in (s_ for s_ in stats if s_[0] == table):
        ix = indexes.setdefault(s_[1], {"name": s_[1], "unique": s_[2] == "0", "primary": s_[1] == "PRIMARY",
                                        "type": s_[5] or "", "columns": [], "cols": [], "expr": False})
        if s_[3] is None:
            ix["expr"] = True
            ix["columns"].append("(expresión)")
        else:
            ix["cols"].append(s_[3])
            ix["columns"].append(s_[3] + (f"({s_[4]})" if s_[4] else ""))
    fkmap = {}
    for f in (f for f in fks if f[0] == table):
        fk = fkmap.setdefault(f[1], {"name": f[1], "columns": [], "ref_db": f[3], "ref_schema": None,
                                     "ref_table": f[4], "ref_columns": [], "on_update": f[6], "on_delete": f[7]})
        fk["columns"].append(f[2])
        fk["ref_columns"].append(f[5])
    ttype = r[1] or ""
    meta = {"engine": "mysql", "db": db, "schema": None, "name": table,
            "type": "view" if "VIEW" in ttype else ("table" if ttype == "BASE TABLE" else "other"),
            "info": {"engine": r[2] or "", "rows_est": _int(r[3]), "size": _int(r[4]), "collation": r[5] or "",
                     "comment": r[6] or "", "auto_increment": r[7], "mariadb": mariadb},
            "columns": columns, "indexes": list(indexes.values()), "fks": list(fkmap.values()),
            "tq": f"{qi('mysql', db)}.{qi('mysql', table)}"}
    return _finish_meta(meta)


def _pg_meta(db, schema, table):
    oid = ("(SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
           f" WHERE n.nspname = {pg_text(schema)} AND c.relname = {pg_text(table)} AND c.relkind IN ('r','p','v','m','f'))")
    attname = ("(SELECT jsonb_agg(a.attname ORDER BY k.n) FROM unnest({arr}) WITH ORDINALITY k(attnum, n)"
               " JOIN pg_attribute a ON a.attrelid = {rel} AND a.attnum = k.attnum)")
    info, cols, idx, fks = _q("pg", db, [
        _pgagg("c.relname, c.relkind::text, c.reltuples::bigint, pg_total_relation_size(c.oid),"
               " obj_description(c.oid, 'pg_class'), n.nspname",
               f"FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = {oid}"),
        _pgagg("a.attname, format_type(a.atttypid, a.atttypmod), t.typname, t.typcategory::text, NOT a.attnotnull,"
               " pg_get_expr(d.adbin, d.adrelid), a.attidentity::text, a.attgenerated::text,"
               " col_description(a.attrelid, a.attnum),"
               " t.typcategory = 'A' OR t.typtype = 'e' OR EXISTS (SELECT 1 FROM pg_opclass oc JOIN pg_am am"
               " ON am.oid = oc.opcmethod WHERE am.amname = 'btree' AND oc.opcdefault"
               " AND oc.opcintype IN (a.atttypid, t.typbasetype)),"
               " (SELECT jsonb_agg(e.enumlabel ORDER BY e.enumsortorder) FROM pg_enum e WHERE e.enumtypid = a.atttypid)",
               "FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid LEFT JOIN pg_attrdef d"
               f" ON d.adrelid = a.attrelid AND d.adnum = a.attnum WHERE a.attrelid = {oid}"
               " AND a.attnum > 0 AND NOT a.attisdropped", "a.attnum"),
        _pgagg("i.relname, ix.indisunique, ix.indisprimary, am.amname, pg_get_indexdef(ix.indexrelid), con.conname,"
               " ix.indpred IS NULL AND 0 <> ALL (ix.indkey::int2[]), "
               + attname.format(arr="ix.indkey::int2[]", rel="ix.indrelid"),
               "FROM pg_index ix JOIN pg_class i ON i.oid = ix.indexrelid JOIN pg_am am ON am.oid = i.relam"
               " LEFT JOIN pg_constraint con ON con.conindid = ix.indexrelid AND con.conrelid = ix.indrelid"
               f" AND con.contype IN ('p','u','x') WHERE ix.indrelid = {oid}", "NOT ix.indisprimary, i.relname"),
        _pgagg("con.conname, " + attname.format(arr="con.conkey", rel="con.conrelid") + ", nf.nspname, cf.relname, "
               + attname.format(arr="con.confkey", rel="con.confrelid") + ", con.confupdtype::text, con.confdeltype::text",
               "FROM pg_constraint con JOIN pg_class cf ON cf.oid = con.confrelid JOIN pg_namespace nf"
               f" ON nf.oid = cf.relnamespace WHERE con.conrelid = {oid} AND con.contype = 'f'", "con.conname"),
    ])
    info = [r for r in info if r[0] == table and r[5] == schema]
    if not info:
        raise Bad("La tabla no existe", 404)
    r = info[0]
    columns = []
    for c in cols:
        typname, cat = c[2] or "", c[3] or ""
        kind = PG_KIND.get(typname) or ("array" if cat == "A" else "enum" if c[10] else "other")
        col = {"name": c[0], "type": c[1] or "", "data_type": typname, "nullable": bool(c[4]), "default": c[5],
               "key": "", "extra": "", "comment": c[8] or "", "maxlen": None, "kind": kind,
               "auto": c[6] in ("a", "d"), "identity": c[6] or "", "generated": c[7] == "s",
               "sortable": bool(c[9]), "values": c[10] or []}
        col.update({k: v for k, v in parse_coltype("pg", col["type"]).items() if k != "values"})
        col["editable"] = not col["generated"] and col["identity"] != "a"
        col["extra"] = ("identity " + ("ALWAYS" if c[6] == "a" else "BY DEFAULT")) if c[6] else ("generated" if col["generated"] else "")
        col["default_form"] = _pg_default_form(col["default"], col["identity"])
        columns.append(col)
    bycol = {c["name"]: c for c in columns}
    indexes = []
    for i in idx:
        cols_ = i[7] or []
        indexes.append({"name": i[0], "unique": bool(i[1]), "primary": bool(i[2]), "type": i[3], "def": i[4],
                        "constraint": i[5], "expr": not i[6], "cols": cols_, "columns": cols_ or ["(expresión)"]})
        for cn in cols_:
            if cn in bycol:
                bycol[cn]["key"] = bycol[cn]["key"] or ("PRI" if i[2] else "UNI" if i[1] else "MUL")
    rules = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
    fklist = [{"name": f[0], "columns": f[1] or [], "ref_db": db, "ref_schema": f[2], "ref_table": f[3],
               "ref_columns": f[4] or [], "on_update": rules.get(f[5], f[5]), "on_delete": rules.get(f[6], f[6])}
              for f in fks]
    kinds = {"r": "table", "p": "table", "v": "view", "m": "matview", "f": "foreign"}
    est = _int(r[2])
    meta = {"engine": "pg", "db": db, "schema": schema, "name": table, "type": kinds.get(r[1], "other"),
            "relkind": r[1],
            "info": {"engine": "", "rows_est": est if est is not None and est >= 0 else None, "size": _int(r[3]),
                     "collation": "", "comment": r[4] or "", "auto_increment": None, "mariadb": False},
            "columns": columns, "indexes": indexes, "fks": fklist,
            "tq": f"{qi('pg', schema)}.{qi('pg', table)}"}
    return _finish_meta(meta)


def _finish_meta(meta):
    """Clave para identificar filas y si los datos son editables (y por qué no)."""
    engine, cols = meta["engine"], {c["name"]: c for c in meta["columns"]}
    key, kind = None, None
    prim = next((i for i in meta["indexes"] if i["primary"]), None)
    if prim and prim["cols"] and not prim["expr"]:
        key, kind = prim["cols"], "primary"
    else:
        uniq = [i for i in meta["indexes"] if i["unique"] and i["cols"] and not i["expr"]
                and all(c in cols and not cols[c]["nullable"] for c in i["cols"])]
        if uniq:
            key, kind = min(uniq, key=lambda i: len(i["cols"]))["cols"], "unique"
    meta["key"], meta["key_kind"] = key, kind
    ro = _readonly_reason(engine, meta["db"], meta["schema"])
    meta["writable"] = ro is None
    meta["readonly_reason"] = ro
    if ro:
        reason = ro
    elif meta["type"] != "table":
        reason = "Es una vista (u otro objeto que no es una tabla): los datos son de solo lectura."
    elif not key:
        reason = ("La tabla no tiene clave primaria ni un índice único sobre columnas NOT NULL: no hay forma"
                  " segura de identificar una fila, así que los datos son de solo lectura.")
    else:
        reason = None
    meta["editable"], meta["edit_reason"] = reason is None, reason
    meta["info"]["size_h"] = bytes_to_human(meta["info"]["size"]) if meta["info"]["size"] is not None else ""
    return meta


def _table_meta(engine, db, schema, table):
    if not isinstance(table, str) or not 0 < len(table) <= 128 or "\x00" in table:
        raise Bad("Tabla inválida")
    return _my_meta(db, table) if engine == "mysql" else _pg_meta(db, schema, table)


def _public(meta):
    return {k: v for k, v in meta.items() if k != "tq"}


def _col(meta, name):
    c = next((c for c in meta["columns"] if c["name"] == name), None)
    if not c:
        raise Bad(f"La columna {name!r} no existe")
    return c


# ── Constructores de SQL (funciones puras sobre metadatos) ──────────

def select_exprs(engine, meta, full=False):
    """Expresiones del SELECT: [valor por columna] + [(índice, expresión de longitud)].
    Columnas de la clave y exportaciones van completas; el resto se recorta para la rejilla."""
    key = set(meta.get("key") or ())
    vals, lens = [], []
    for i, c in enumerate(meta["columns"]):
        q, k, whole = qi(engine, c["name"]), c["kind"], full or c["name"] in key
        if engine == "mysql":
            if k == "bin":
                vals.append(f"HEX({q})" if whole else f"HEX(LEFT({q}, {PREVIEW_BYTES}))")
                if not whole:
                    lens.append((i, f"LENGTH({q})"))
            elif k == "bit":
                vals.append(f"BIN({q})")
            elif k == "geo":
                vals.append(f"ST_AsText({q})")
            elif k in LONG_KINDS and not whole:
                vals.append(f"LEFT({q}, {PREVIEW_CHARS})")
                lens.append((i, f"CHAR_LENGTH({q})"))
            else:
                vals.append(q)
        else:
            if k == "bin":
                vals.append(f"encode({q}, 'hex')" if whole else f"encode(substring({q} from 1 for {PREVIEW_BYTES}), 'hex')")
                if not whole:
                    lens.append((i, f"octet_length({q})"))
            elif k in LONG_KINDS and not whole:
                vals.append(f"left({q}::text, {PREVIEW_CHARS})")
                lens.append((i, f"length({q}::text)"))
            else:
                vals.append(f"{q}::text")
    return vals, lens


def build_where(engine, meta, filters):
    if not isinstance(filters, list) or len(filters) > MAX_FILTERS:
        raise Bad("Filtros inválidos")
    parts = []
    for f in filters:
        if not isinstance(f, dict) or f.get("op") not in OPS:
            raise Bad("Filtro inválido")
        c, op, v = _col(meta, f.get("col")), f["op"], f.get("value", "")
        q = qi(engine, c["name"])
        if op in ("null", "notnull"):
            parts.append(f"{q} IS {'NOT ' if op == 'notnull' else ''}NULL")
            continue
        if not isinstance(v, str) or len(v) > 10000:
            raise Bad("Valor de filtro inválido")
        if op in ("like", "starts"):
            pat = ("%" if op == "like" else "") + like_escape(v) + "%"
            target = q if engine == "mysql" else f"{q}::text"
            parts.append(f"{target} {'LIKE' if engine == 'mysql' else 'ILIKE'} {lit(engine, 'text', pat)} ESCAPE '!'")
            continue
        if c["kind"] == "bin" and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", v):
            val = lit(engine, "bin", {"hex": v[2:]})
        else:
            val = lit(engine, c["kind"], v)
        if engine == "pg" and not c.get("sortable", True):
            q = f"{q}::text"  # json, xml, point…: sin operador de igualdad/orden propio
        if op == "ne":
            parts.append(f"NOT ({q} <=> {val})" if engine == "mysql" else f"{q} IS DISTINCT FROM {val}")
        else:
            parts.append(f"{q} {({'eq': '=', 'gt': '>', 'lt': '<'})[op]} {val}")
    return (" WHERE " + " AND ".join(parts)) if parts else ""


def build_order(engine, meta, sort):
    parts, scol = [], None
    if sort:
        if not isinstance(sort, dict):
            raise Bad("Orden inválido")
        c = _col(meta, sort.get("col"))
        if not c.get("sortable", True):
            raise Bad("No se puede ordenar por esa columna")
        scol = c["name"]
        parts.append(f"{qi(engine, scol)} {'DESC' if sort.get('dir') == 'desc' else 'ASC'}")
    parts += [qi(engine, k) for k in (meta.get("key") or []) if k != scol]  # desempate estable
    return (" ORDER BY " + ", ".join(parts)) if parts else ""


def where_key(engine, meta, keyvals):
    """WHERE que identifica UNA fila por su clave primaria/única (todas sus columnas, sin NULL)."""
    key = meta.get("key")
    if not key:
        raise Bad("La tabla no tiene clave primaria ni índice único NOT NULL")
    if not isinstance(keyvals, dict) or set(keyvals) != set(key):
        raise Bad("La identificación de la fila no coincide con la clave de la tabla")
    parts = []
    for k in key:
        v = keyvals[k]
        if v is None:
            raise Bad("La clave de la fila no puede ser NULL")
        parts.append(f"{qi(engine, k)} = {lit(engine, _col(meta, k)['kind'], v)}")
    return " AND ".join(parts)


class DdlError(Bad):
    pass


def _plain(value, what, maxlen=1024):
    if not isinstance(value, str) or len(value) > maxlen:
        raise DdlError(f"{what} inválido")
    if any(ord(ch) < 32 for ch in value):
        raise DdlError(f"{what} no puede tener saltos de línea ni caracteres de control")
    return value


def build_type(engine, spec):
    types = MY_TYPES if engine == "mysql" else PG_TYPES
    base = spec.get("type")
    if base not in types:
        raise DdlError("Tipo de dato no permitido")
    kind, param = types[base]
    arg = str(spec.get("length") or "").strip()
    sql = base
    if param in ("len", "len?"):
        if arg:
            if not arg.isdigit() or not 1 <= int(arg) <= LEN_MAX.get(base, 65535):
                raise DdlError(f"Longitud inválida para {base}")
            sql = f"{base}({int(arg)})"
        elif param == "len":
            raise DdlError(f"{base} requiere una longitud")
    elif param == "prec":
        if arg:
            m = re.fullmatch(r"(\d{1,4})\s*(?:,\s*(\d{1,4}))?", arg)
            maxp, maxs = (65, 30) if engine == "mysql" else (1000, 1000)
            if not m or not 1 <= int(m.group(1)) <= maxp or int(m.group(2) or 0) > min(int(m.group(1)), maxs):
                raise DdlError("Precisión inválida: usa p o p,s (p.ej. 10,2)")
            sql = f"{base}({int(m.group(1))}" + (f",{int(m.group(2))})" if m.group(2) is not None else ")")
    elif param == "fsp":
        if arg:
            if not arg.isdigit() or int(arg) > 6:
                raise DdlError("La precisión de segundos va de 0 a 6")
            sql = f"{base}({int(arg)})" if " " not in base else base.replace(" ", f"({int(arg)}) ", 1)
    elif param == "values":
        vals = spec.get("values")
        if not isinstance(vals, list) or not 1 <= len(vals) <= 255 or len(set(vals)) != len(vals):
            raise DdlError(f"{base} necesita entre 1 y 255 valores distintos")
        sql = f"{base}(" + ",".join(_str("mysql", _plain(v, "Valor de la lista", 255)) for v in vals) + ")"
    elif arg:
        raise DdlError(f"{base} no lleva longitud")
    if engine == "mysql" and spec.get("unsigned") and kind in ("int", "num") and base not in ("BOOLEAN", "YEAR"):
        sql += " UNSIGNED"
    return sql, kind


def ddl_value(engine, kind, value):
    """Literal para DEFAULT (se ve en la vista previa: legible, sin hex)."""
    value = _plain(value, "Valor por defecto", 4000)
    if kind in ("int", "num"):
        if not NUM_RE.fullmatch(value):
            raise DdlError("El valor por defecto debe ser un número")
        return value
    if kind == "bit" and engine == "mysql":
        if not BIT_RE.fullmatch(value):
            raise DdlError("Valor de bits inválido (solo 0 y 1)")
        return f"b'{value}'"
    if kind == "bool" and engine == "pg":
        if value.lower() not in ("true", "false", "t", "f", "1", "0"):
            raise DdlError("Valor booleano inválido (true o false)")
        return "true" if value.lower() in ("true", "t", "1") else "false"
    return _str("mysql", value) if engine == "mysql" else pg_text(value)


def build_default(engine, kind, d):
    d = d if isinstance(d, dict) else {}
    k = d.get("kind") or "none"
    if k == "none":
        return ""
    if k == "null":
        return " DEFAULT NULL"
    if k == "expr":
        if d.get("value") not in DEFAULT_EXPRS[engine]:
            raise DdlError("Expresión por defecto no permitida")
        return f" DEFAULT {d['value']}"
    if k == "value":
        return f" DEFAULT {ddl_value(engine, kind, d.get('value'))}"
    raise DdlError("Tipo de valor por defecto inválido")


def _my_keep_default(c, mariadb):
    """MODIFY reemplaza la definición entera: hay que repetir el DEFAULT actual tal como lo da el catálogo."""
    d, extra = c["default"], c["extra"].lower()
    if d is None:
        return ""
    if mariadb:
        return f" DEFAULT {d}"
    if "default_generated" in extra:
        return f" DEFAULT {d}" if re.match(r"(?i)(current_timestamp|now|localtime)", d) else f" DEFAULT ({d})"
    if c["kind"] in ("int", "num") and NUM_RE.fullmatch(d):
        return f" DEFAULT {d}"
    if c["kind"] == "bit" and re.fullmatch(r"b'[01]+'", d):
        return f" DEFAULT {d}"
    return f" DEFAULT {_str('mysql', d)}"


def build_coldef(engine, spec, current=None, mariadb=False):
    """Definición de columna para CREATE/ALTER. current = metadatos actuales (modificar en MySQL)."""
    if not isinstance(spec, dict):
        raise DdlError("Columna inválida")
    name = spec.get("name")
    if not (isinstance(name, str) and (NEW_RE.fullmatch(name) or (current and name == current["name"]))):
        raise DdlError("Nombre de columna inválido (letras, números y _; empieza con letra o _)")
    if spec.get("type") == "__keep__" and current:
        tsql, kind = current["type"], current["kind"]
    else:
        tsql, kind = build_type(engine, spec)
    auto = bool(spec.get("auto"))
    nullable = bool(spec.get("nullable")) and not auto and not spec.get("primary")
    if auto and kind != "int":
        raise DdlError("Solo una columna entera puede ser auto-incremental / identidad")
    d = spec.get("default") if isinstance(spec.get("default"), dict) else {}
    if d.get("kind") == "keep" and current and engine == "mysql":
        dflt = _my_keep_default(current, mariadb)
    elif d.get("kind") == "keep":
        dflt = ""
    else:
        dflt = build_default(engine, kind, d)
    if auto and dflt:
        raise DdlError("Una columna auto-incremental no lleva valor por defecto")
    if dflt == " DEFAULT NULL" and not nullable:
        raise DdlError("Una columna NOT NULL no puede tener NULL por defecto")
    parts = [qi(engine, name), tsql]
    if engine == "mysql":
        parts.append("NULL" if nullable else "NOT NULL")
        if dflt:
            parts.append(dflt.strip())
        if current and d.get("kind") == "keep":
            m = re.search(r"(?i)on update (current_timestamp(?:\(\d\))?)", current["extra"])
            if m:
                parts.append("ON UPDATE " + m.group(1).upper())
        if auto:
            parts.append("AUTO_INCREMENT")
        comment = spec.get("comment") or ""
        if comment:
            parts.append("COMMENT " + _str("mysql", _plain(comment, "Comentario")))
    else:
        if not nullable:
            parts.append("NOT NULL")
        if dflt:
            parts.append(dflt.strip())
        if auto:
            parts.append("GENERATED BY DEFAULT AS IDENTITY")
    return " ".join(parts)


def _pg_comment(tq, name, comment):
    val = pg_text(_plain(comment, "Comentario")) if comment else "NULL"
    return f"COMMENT ON COLUMN {tq}.{qi('pg', name)} IS {val}"


def build_pg_modify(tq, current, spec):
    """ALTER TABLE de PostgreSQL con solo lo que cambia (tipo, NULL, DEFAULT, nombre, comentario)."""
    if not isinstance(spec, dict):
        raise DdlError("Columna inválida")
    q, acts, stmts = qi("pg", current["name"]), [], []
    if spec.get("type") not in (None, "", "__keep__"):
        tsql, kind = build_type("pg", spec)
        if tsql != current["type"]:
            acts.append(f"ALTER COLUMN {q} TYPE {tsql} USING {q}::{tsql}")
    else:
        kind = current["kind"]
    nullable = bool(spec.get("nullable"))
    if nullable != current["nullable"]:
        acts.append(f"ALTER COLUMN {q} {'DROP' if nullable else 'SET'} NOT NULL")
    d = spec.get("default") if isinstance(spec.get("default"), dict) else {"kind": "keep"}
    if d.get("kind") != "keep" and not current.get("identity") and d != current.get("default_form"):
        dflt = build_default("pg", kind, d)
        acts.append(f"ALTER COLUMN {q} SET{dflt}" if dflt else f"ALTER COLUMN {q} DROP DEFAULT")
    if acts:
        stmts.append(f"ALTER TABLE {tq} " + ",\n  ".join(acts))
    new = spec.get("name") or current["name"]
    if new != current["name"]:
        if not isinstance(new, str) or not NEW_RE.fullmatch(new):
            raise DdlError("Nombre de columna inválido")
        stmts.append(f"ALTER TABLE {tq} RENAME COLUMN {q} TO {qi('pg', new)}")
    comment = spec.get("comment") or ""
    if comment != (current.get("comment") or ""):
        stmts.append(_pg_comment(tq, new, comment))
    if not stmts:
        raise DdlError("No hay cambios que aplicar")
    return stmts


def build_create_table(engine, tq, cols, mariadb=False):
    if not isinstance(cols, list) or not 1 <= len(cols) <= 200:
        raise DdlError("Agrega entre 1 y 200 columnas")
    seen = set()
    for c in cols:
        if not isinstance(c, dict):
            raise DdlError("Columna inválida")
        low = str(c.get("name", "")).lower()
        if low in seen:
            raise DdlError(f"Columna repetida: {c.get('name')}")
        seen.add(low)
        if c.get("auto") and not c.get("primary"):
            raise DdlError("La columna auto-incremental / identidad debe ser parte de la clave primaria")
    defs = [build_coldef(engine, c, mariadb=mariadb) for c in cols]
    pk = [c["name"] for c in cols if c.get("primary")]
    if pk:
        defs.append("PRIMARY KEY (" + ", ".join(qi(engine, n) for n in pk) + ")")
    stmts = [f"CREATE TABLE {tq} (\n  " + ",\n  ".join(defs) + "\n)"]
    if engine == "pg":
        stmts += [_pg_comment(tq, c["name"], c["comment"]) for c in cols if c.get("comment")]
    return stmts


# ── Vistas ───────────────────────────────────────────────────────────

def _types_for_ui():
    return {e: [[name, kind, param] for name, (kind, param) in t.items()]
            for e, t in (("mysql", MY_TYPES), ("pg", PG_TYPES))}


@dbexplorer_bp.route("/")
@login_required
def index():
    return render_template("dbexplorer.html", has_mysql=_installed("mysql"), has_pg=_installed("pg"),
                           types=_types_for_ui(), exprs=DEFAULT_EXPRS, per_page=PER_PAGE)


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/dbs")
@login_required
@needs_engine
def dbs(engine):
    if engine == "mysql":
        rows = _q("mysql", None, ["SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME"])[0]
        names = [r[0] for r in rows]
    else:
        names = _q("pg", "postgres", ["SELECT coalesce(jsonb_agg(datname::text ORDER BY datname), '[]'::jsonb)"
                                      " FROM pg_database WHERE datallowconn"])[0]
    return jsonify({"ok": True, "dbs": [{"name": n, "system": n in SYSTEM_DBS[engine], "supported": bool(DB_RE.fullmatch(n))}
                                        for n in names]})


@dbexplorer_bp.route("/api/pg/schemas")
@login_required
def schemas():
    if not _installed("pg"):
        raise Bad("Motor no instalado", 404)
    db, _ = _ctx("pg", request.args)
    names = _q("pg", db, ["SELECT coalesce(jsonb_agg(nspname::text ORDER BY nspname), '[]'::jsonb) FROM pg_namespace"
                          " WHERE nspname !~ '^pg_' OR nspname = 'pg_catalog'"])[0]
    names.sort(key=lambda n: (n in PG_SYS_SCHEMAS, n != "public", n))
    return jsonify({"ok": True, "schemas": [{"name": n, "system": n in PG_SYS_SCHEMAS} for n in names]})


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/tables")
@login_required
@needs_engine
def tables(engine):
    db, schema = _ctx(engine, request.args)
    out = []
    if engine == "mysql":
        rows = _q("mysql", None, [
            "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS, DATA_LENGTH + INDEX_LENGTH, TABLE_COLLATION,"
            f" TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA = {my_text(db)} ORDER BY TABLE_NAME"])[0]
        for r in rows:
            out.append({"name": r[0], "type": "view" if "VIEW" in (r[1] or "") else "table", "engine": r[2] or "",
                        "rows": _int(r[3]), "size": _int(r[4]), "collation": r[5] or "", "comment": r[6] or ""})
    else:
        rows = _q("pg", db, [_pgagg(
            "c.relname, c.relkind::text, c.reltuples::bigint,"
            " CASE WHEN c.relkind IN ('r','p','m') THEN pg_total_relation_size(c.oid) END, obj_description(c.oid, 'pg_class')",
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            f" WHERE n.nspname = {pg_text(schema)} AND c.relkind IN ('r','p','v','m','f')", "c.relname")])[0]
        kinds = {"r": "table", "p": "table", "v": "view", "m": "matview", "f": "foreign"}
        for r in rows:
            est = _int(r[2])
            out.append({"name": r[0], "type": kinds.get(r[1], "other"), "engine": "", "collation": "",
                        "rows": est if est is not None and est >= 0 else None, "size": _int(r[3]), "comment": r[4] or ""})
    for t in out:
        t["size_h"] = bytes_to_human(t["size"]) if t["size"] is not None else ""
    return jsonify({"ok": True, "tables": out, "readonly": _readonly_reason(engine, db, schema)})


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/table")
@login_required
@needs_engine
def table_meta(engine):
    db, schema = _ctx(engine, request.args)
    return jsonify({"ok": True, "meta": _public(_table_meta(engine, db, schema, request.args.get("table")))})


def _cells(meta, vals, lens):
    """Fila leída → celdas: str | None | {"t": recorte, "len": n} | {"hex": …, "len": n}."""
    lenmap = dict(lens)
    out = []
    for i, c in enumerate(meta["columns"]):
        v = vals[i] if i < len(vals) else None
        n = _int(lenmap.get(i))
        if v is None:
            out.append(None)
        elif c["kind"] == "bin":
            out.append({"hex": v.lower(), "len": n if n is not None else len(v) // 2})
        elif n is not None and n > PREVIEW_CHARS:
            out.append({"t": v, "len": n})
        else:
            out.append(v)
    return out


def _fetch_rows(engine, meta, where, order, limit, offset, count):
    """→ (filas de celdas, total o None, truncado). Una sola llamada al cliente."""
    vals, lens = select_exprs(engine, meta)
    tq, n = meta["tq"], len(vals)
    lexprs = [e for _, e in lens]
    tail = f"{where}{order} LIMIT {int(limit)} OFFSET {int(offset)}"
    if engine == "mysql":
        sel = ", ".join(f"{e} AS `c{i}`" for i, e in enumerate(vals + lexprs))
        stmts = ([f"SELECT COUNT(*) FROM {tq}{where}"] if count else []) + [f"SELECT {sel} FROM {tq}{tail}"]
        code, out, err, trunc = _exec(engine, None, ";\n".join(stmts) + ";\n")
        sets, broken = parse_my_xml(out)
        if code != 0 and not (trunc and sets):
            raise Bad(err or "No se pudieron leer los datos")
        total = _int(sets[0]["rows"][0][0]) if count and sets and sets[0]["rows"] else None
        raw = sets[-1]["rows"] if len(sets) == len(stmts) else []
    else:
        arr = ", ".join(vals + [f"({e})::text" for e in lexprs]) or "NULL"
        stmts = ([f"SELECT count(*) FROM {tq}{where}"] if count else []) + \
                [f"SELECT array_to_json(ARRAY[{arr}]::text[]) FROM {tq}{tail}"]
        code, out, err, trunc = _exec(engine, meta["db"], ";\n".join(stmts) + ";\n")
        lines = out.splitlines()
        if trunc and lines:
            lines.pop()
        if code != 0 and not trunc:
            raise Bad(err or "No se pudieron leer los datos")
        total = _int(lines.pop(0)) if count and lines else None
        raw = [json.loads(ln) for ln in lines if ln.startswith("[")]
    rows = []
    for r in raw:
        lv = [(lens[j][0], r[n + j]) for j in range(len(lens))]
        rows.append(_cells(meta, r[:n], lv))
    return rows, total, bool(trunc)


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/rows", methods=["POST"])
@login_required
@needs_engine
def rows(engine):
    d = _json()
    db, schema = _ctx(engine, d)
    meta = _table_meta(engine, db, schema, d.get("table"))
    per = d.get("per_page") if d.get("per_page") in PER_PAGE else 50
    page = min(max(_int(d.get("page"), 1) or 1, 1), 10_000_000)
    filters = d.get("filters") or []
    where, order = build_where(engine, meta, filters), build_order(engine, meta, d.get("sort"))
    est = meta["info"]["rows_est"]
    count = bool(d.get("count")) or (est is not None and est <= EXACT_MAX)
    data, total, trunc = _fetch_rows(engine, meta, where, order, per + 1, (page - 1) * per, count)
    return jsonify({"ok": True, "columns": [c["name"] for c in meta["columns"]], "rows": data[:per],
                    "has_more": len(data) > per, "page": page, "per_page": per, "count": total,
                    "estimate": est, "filtered": bool(filters), "truncated": trunc})


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/cell", methods=["POST"])
@login_required
@needs_engine
def cell(engine):
    """Valor completo de una celda (hasta CELL_MAX) identificando la fila por su clave."""
    d = _json()
    db, schema = _ctx(engine, d)
    meta = _table_meta(engine, db, schema, d.get("table"))
    c = _col(meta, d.get("col"))
    wk, q = where_key(engine, meta, d.get("key")), qi(engine, c["name"])
    binary = c["kind"] == "bin"
    if engine == "mysql":
        e = f"HEX(LEFT({q}, {CELL_MAX // 2}))" if binary else f"LEFT({q}, {CELL_MAX})"
        ln = f"LENGTH({q})" if binary else f"CHAR_LENGTH({q})"
        rows_ = _q("mysql", None, [f"SELECT {e}, {ln} FROM {meta['tq']} WHERE {wk} LIMIT 1"])[0]
    else:
        e = f"encode(substring({q} from 1 for {CELL_MAX // 2}), 'hex')" if binary else f"left({q}::text, {CELL_MAX})"
        ln = f"octet_length({q})" if binary else f"length({q}::text)"
        rows_ = _q("pg", db, [_pgagg(f"{e}, {ln}", f"FROM {meta['tq']} WHERE {wk} LIMIT 1")])[0]
    if not rows_:
        raise Bad("La fila ya no existe", 404)
    value, n = rows_[0][0], _int(rows_[0][1])
    shown = (len(value) // 2 if binary else len(value)) if value is not None else 0
    return jsonify({"ok": True, "value": value, "len": n, "hex": binary, "complete": n is None or shown >= n})


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/count", methods=["POST"])
@login_required
@needs_engine
def count(engine):
    d = _json()
    db, schema = _ctx(engine, d)
    meta = _table_meta(engine, db, schema, d.get("table"))
    where = build_where(engine, meta, d.get("filters") or [])
    if engine == "mysql":
        r = _q("mysql", None, [f"SELECT COUNT(*) FROM {meta['tq']}{where}"])[0]
    else:
        r = _q("pg", db, [_pgagg("count(*)", f"FROM {meta['tq']}{where}")])[0]
        r = [[r[0][0]]] if r else []
    return jsonify({"ok": True, "count": _int(r[0][0]) if r else 0})


# ── Edición de filas ─────────────────────────────────────────────────

def _editable_meta(engine, d):
    db, schema = _ctx(engine, d)
    _require_writable(engine, db, schema)
    meta = _table_meta(engine, db, schema, d.get("table"))
    if not meta["editable"]:
        raise Bad(meta["edit_reason"], 403)
    return db, meta


def _assignments(engine, meta, values):
    if not isinstance(values, dict) or len(values) > 1000:
        raise Bad("Valores inválidos")
    out = []
    for name, v in values.items():
        c = _col(meta, name)
        if not c["editable"]:
            raise Bad(f"La columna {name} no se puede escribir (generada, identidad ALWAYS o espacial)")
        if v is None and not c["nullable"]:
            raise Bad(f"La columna {name} no admite NULL")
        out.append((qi(engine, name), lit(engine, c["kind"], v)))
    return out


def _guard(alias):
    """PG: devuelve el número de filas afectadas y aborta (error → ROLLBACK) si fuera más de una."""
    return (f"SELECT CASE WHEN count(*) > 1 THEN (count(*)::text || ' filas afectadas: cancelado')::int"
            f" ELSE count(*) END FROM {alias}")


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/row/save", methods=["POST"])
@login_required
@needs_engine
def row_save(engine):
    d = _json()
    db, meta = _editable_meta(engine, d)
    sets, tq = _assignments(engine, meta, d.get("values")), meta["tq"]
    if d.get("key") is None:  # insertar
        if engine == "mysql":
            cols = ", ".join(q for q, _ in sets)
            vals = ", ".join(v for _, v in sets)
            res = _mut(engine, None, f"INSERT INTO {tq} ({cols}) VALUES ({vals});\nSELECT ROW_COUNT(), LAST_INSERT_ID();\n")
            row = res[-1]["rows"][0] if res and res[-1]["rows"] else ["0", "0"]
            return jsonify({"ok": _int(row[0]) == 1, "inserted": _int(row[0]), "insert_id": row[1],
                            **({} if _int(row[0]) == 1 else {"error": "No se insertó la fila"})})
        body = (f"INSERT INTO {tq} (" + ", ".join(q for q, _ in sets) + ") VALUES (" + ", ".join(v for _, v in sets) + ")"
                if sets else f"INSERT INTO {tq} DEFAULT VALUES")
        res = _mut(engine, db, f"WITH i AS ({body} RETURNING 1) SELECT count(*) FROM i;\n")
        n = _int(res[-1]) if res else 0
        return jsonify({"ok": n == 1, "inserted": n, **({} if n == 1 else {"error": "No se insertó la fila"})})

    if not sets:
        raise Bad("No hay cambios que guardar")
    wk = where_key(engine, meta, d.get("key"))
    assign = ", ".join(f"{q} = {v}" for q, v in sets)
    if engine == "mysql":
        res = _mut(engine, None, f"UPDATE {tq} SET {assign} WHERE {wk} LIMIT 1;\nSELECT ROW_COUNT();\n"
                                 f"SELECT COUNT(*) FROM {tq} WHERE {wk};\n")
        n = _int(res[0]["rows"][0][0]) if len(res) >= 1 and res[0]["rows"] else 0
        exists = _int(res[1]["rows"][0][0]) if len(res) >= 2 and res[1]["rows"] else 0
        if n == 0 and exists:
            return jsonify({"ok": True, "updated": 0, "note": "Sin cambios: los valores ya eran esos"})
    else:
        res = _mut(engine, db, f"BEGIN;\nWITH u AS (UPDATE {tq} SET {assign} WHERE {wk} RETURNING 1) {_guard('u')};\nCOMMIT;\n")
        n = _int(res[-1]) if res else 0
    if n != 1:
        return jsonify({"ok": False, "updated": n,
                        "error": "No se actualizó ninguna fila: la fila ya no existe o cambió su clave (recarga la tabla)."}), 409
    return jsonify({"ok": True, "updated": 1})


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/row/delete", methods=["POST"])
@login_required
@needs_engine
def row_delete(engine):
    d = _json()
    db, meta = _editable_meta(engine, d)
    keys = d.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_KEYS:
        raise Bad(f"Selecciona entre 1 y {MAX_KEYS} filas")
    tq = meta["tq"]
    wks = [where_key(engine, meta, k) for k in keys]
    if engine == "mysql":
        body = "".join(f"DELETE FROM {tq} WHERE {wk} LIMIT 1;\nSELECT ROW_COUNT();\n" for wk in wks)
        res = _mut(engine, None, "START TRANSACTION;\n" + body + "COMMIT;\n")
        counts = [_int(s["rows"][0][0], 0) if s["rows"] else 0 for s in res]
    else:
        body = "".join(f"WITH d AS (DELETE FROM {tq} WHERE {wk} RETURNING 1) {_guard('d')};\n" for wk in wks)
        counts = [_int(x, 0) for x in _mut(engine, db, "BEGIN;\n" + body + "COMMIT;\n")]
    deleted = sum(counts)
    missing = len(wks) - deleted
    return jsonify({"ok": True, "deleted": deleted, "missing": missing,
                    **({"note": f"{missing} fila(s) ya no existían"} if missing else {})})


# ── Estructura y operaciones (DDL) ───────────────────────────────────

def _maint_output(res):
    lines = []
    for s in res:
        for r in s["rows"]:
            lines.append(" · ".join(v or "" for v in r))
    return "\n".join(lines)


def _ddl(engine, d, db, schema):
    """→ (sentencias, en_transacción, tabla_resultante). Nunca acepta SQL del navegador: lo arma aquí."""
    op = d.get("op")
    mariadb = engine == "mysql" and _is_mariadb()
    if op == "create_table":
        name = d.get("name")
        if not isinstance(name, str) or not NEW_RE.fullmatch(name):
            raise DdlError("Nombre de tabla inválido (letras, números y _; empieza con letra o _)")
        if engine == "pg":
            have = _q("pg", db, ["SELECT coalesce(jsonb_agg(nspname::text), '[]'::jsonb) FROM pg_namespace"
                                 f" WHERE nspname = {pg_text(schema)}"])[0]
            if schema not in have:
                raise Bad("El esquema no existe", 404)
        tq = f"{qi(engine, db if engine == 'mysql' else schema)}.{qi(engine, name)}"
        return build_create_table(engine, tq, d.get("columns"), mariadb), engine == "pg", name

    meta = _table_meta(engine, db, schema, d.get("table"))
    tq, table, is_table = meta["tq"], meta["name"], meta["type"] == "table"
    names = {c["name"].lower() for c in meta["columns"]}

    def need_table():
        if not is_table:
            raise DdlError("Esta operación solo se aplica a tablas (no a vistas)")

    def new_table_name():
        new = d.get("new_name")
        if not isinstance(new, str) or not NEW_RE.fullmatch(new):
            raise DdlError("Nombre nuevo inválido (letras, números y _; empieza con letra o _)")
        return new

    if op == "add_column":
        need_table()
        spec = d.get("column") or {}
        if str(spec.get("name", "")).lower() in names:
            raise DdlError("Ya existe una columna con ese nombre")
        coldef = build_coldef(engine, spec, mariadb=mariadb)
        if engine == "mysql":
            pos = d.get("position") or ""
            if pos == "first":
                coldef += " FIRST"
            elif isinstance(pos, str) and pos.startswith("after:"):
                coldef += " AFTER " + qi(engine, _col(meta, pos[6:])["name"])
            return [f"ALTER TABLE {tq} ADD COLUMN {coldef}"], False, table
        stmts = [f"ALTER TABLE {tq} ADD COLUMN {coldef}"]
        if spec.get("comment"):
            stmts.append(_pg_comment(tq, spec["name"], spec["comment"]))
        return stmts, True, table
    if op == "modify_column":
        need_table()
        cur, spec = _col(meta, d.get("column")), d.get("spec") or {}
        new = spec.get("name") or cur["name"]
        if new.lower() != cur["name"].lower() and new.lower() in names:
            raise DdlError("Ya existe una columna con ese nombre")
        if engine == "mysql":
            spec = {**spec, "name": new}
            return [f"ALTER TABLE {tq} CHANGE COLUMN {qi(engine, cur['name'])} {build_coldef(engine, spec, cur, mariadb)}"], False, table
        return build_pg_modify(tq, cur, spec), True, table
    if op == "drop_column":
        need_table()
        c = _col(meta, d.get("column"))
        if len(meta["columns"]) < 2:
            raise DdlError("No se puede eliminar la única columna de la tabla")
        return [f"ALTER TABLE {tq} DROP COLUMN {qi(engine, c['name'])}"], False, table
    if op == "add_index":
        need_table()
        kind = d.get("kind")
        cols = d.get("columns")
        if kind not in ("index", "unique", "primary") + (("fulltext",) if engine == "mysql" else ()):
            raise DdlError("Tipo de índice inválido")
        if not isinstance(cols, list) or not 1 <= len(cols) <= 16 or len(set(cols)) != len(cols):
            raise DdlError("Elige entre 1 y 16 columnas distintas")
        cl = [_col(meta, c) for c in cols]
        name = d.get("name") or ""
        if name and not NEW_RE.fullmatch(name):
            raise DdlError("Nombre de índice inválido")
        if kind == "primary":
            if any(i["primary"] for i in meta["indexes"]):
                raise DdlError("La tabla ya tiene clave primaria")
            return [f"ALTER TABLE {tq} ADD PRIMARY KEY (" + ", ".join(qi(engine, c["name"]) for c in cl) + ")"], False, table
        if engine == "mysql":
            parts = [qi(engine, c["name"]) + ("(191)" if c["data_type"] in TEXT_PREFIX and kind != "fulltext" else "")
                     for c in cl]
            label = {"index": "INDEX", "unique": "UNIQUE INDEX", "fulltext": "FULLTEXT INDEX"}[kind]
            return [f"ALTER TABLE {tq} ADD {label}{' ' + qi(engine, name) if name else ''} (" + ", ".join(parts) + ")"], False, table
        return [f"CREATE {'UNIQUE ' if kind == 'unique' else ''}INDEX{' ' + qi(engine, name) if name else ''}"
                f" ON {tq} (" + ", ".join(qi(engine, c["name"]) for c in cl) + ")"], False, table
    if op == "drop_index":
        need_table()
        ix = next((i for i in meta["indexes"] if i["name"] == d.get("name")), None)
        if not ix:
            raise DdlError("El índice no existe")
        if engine == "mysql":
            return [f"ALTER TABLE {tq} DROP PRIMARY KEY" if ix["primary"] else f"ALTER TABLE {tq} DROP INDEX {qi(engine, ix['name'])}"], False, table
        if ix.get("constraint"):
            return [f"ALTER TABLE {tq} DROP CONSTRAINT {qi(engine, ix['constraint'])}"], False, table
        return [f"DROP INDEX {qi(engine, schema)}.{qi(engine, ix['name'])}"], False, table

    pg_obj = {"table": "TABLE", "view": "VIEW", "matview": "MATERIALIZED VIEW", "foreign": "FOREIGN TABLE"}
    if op == "rename_table":
        new = new_table_name()
        if engine == "mysql":
            return [f"RENAME TABLE {tq} TO {qi(engine, db)}.{qi(engine, new)}"], False, new
        return [f"ALTER {pg_obj.get(meta['type'], 'TABLE')} {tq} RENAME TO {qi(engine, new)}"], False, new
    if op == "copy_table":
        need_table()
        new = new_table_name()
        ntq = f"{qi(engine, db if engine == 'mysql' else schema)}.{qi(engine, new)}"
        stmts = [f"CREATE TABLE {ntq} LIKE {tq}" if engine == "mysql" else f"CREATE TABLE {ntq} (LIKE {tq} INCLUDING ALL)"]
        if d.get("data"):
            cols = ", ".join(qi(engine, c["name"]) for c in meta["columns"] if not c["generated"])
            over = " OVERRIDING SYSTEM VALUE" if engine == "pg" and any(c.get("identity") for c in meta["columns"]) else ""
            stmts.append(f"INSERT INTO {ntq} ({cols}){over} SELECT {cols} FROM {tq}")
        return stmts, engine == "pg", new
    if op in ("truncate", "drop_table"):
        if d.get("confirm") != table:
            raise DdlError("La confirmación no coincide con el nombre de la tabla")
        if op == "truncate":
            need_table()
            return [f"TRUNCATE TABLE {tq}"], False, table
        if engine == "mysql":
            return [f"DROP {'VIEW' if meta['type'] == 'view' else 'TABLE'} {tq}"], False, None
        return [f"DROP {pg_obj.get(meta['type'], 'TABLE')} {tq}"], False, None
    if op == "maintenance":
        need_table()
        action = d.get("action")
        allowed = {"mysql": {"optimize": "OPTIMIZE TABLE", "analyze": "ANALYZE TABLE"},
                   "pg": {"vacuum": "VACUUM (ANALYZE)", "analyze": "ANALYZE"}}[engine]
        if action not in allowed:
            raise DdlError("Operación de mantenimiento inválida")
        return [f"{allowed[action]} {tq}"], False, table
    raise DdlError("Operación desconocida")


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/ddl", methods=["POST"])
@login_required
@needs_engine
def ddl(engine):
    d = _json()
    db, schema = _ctx(engine, d)
    _require_writable(engine, db, schema)
    stmts, tx, result_table = _ddl(engine, d, db, schema)
    sql = ";\n".join(stmts) + ";"
    if d.get("dry_run"):
        return jsonify({"ok": True, "sql": sql})
    script = f"BEGIN;\n{sql}\nCOMMIT;\n" if tx else sql + "\n"
    res = _mut(engine, db if engine == "pg" else None, script)
    out = _maint_output(res) if engine == "mysql" and d.get("op") == "maintenance" else ""
    return jsonify({"ok": True, "sql": sql, "output": out, "table": result_table})


# ── Consola SQL ──────────────────────────────────────────────────────

def _pg_wrappable(words):
    return words and words[0] in ("select", "with", "values", "table") and not (set(words) & WRITE_KW)


def console_script(engine, stmts, schema=None):
    """Arma el script: cada sentencia seguida de un marcador para separar resultados."""
    parts = []
    if engine == "mysql":
        for i, (text, _) in enumerate(stmts):
            parts.append(f"{text}\n;\nSELECT {i} AS `{MARK}`, ROW_COUNT() AS `n`;\n")
        return "".join(parts), [False] * len(stmts)
    if schema:
        parts.append(f"SET search_path TO {qi('pg', schema)}, public;\nSELECT '{MARK} -1';\n")
    wrapped = []
    for i, (text, skel) in enumerate(stmts):
        w = _pg_wrappable(_words(skel))
        wrapped.append(w)
        parts.append(f"SELECT row_to_json(t) FROM (\n{text}\n) t LIMIT {CONSOLE_ROWS + 1};\n" if w else f"{text}\n;\n")
        parts.append(f"SELECT '{MARK} {i}';\n")
    return "".join(parts), wrapped


def parse_console(engine, out, stmts, wrapped):
    """Salida del script de consola → [{sql, columns, rows, truncated, affected, output}]."""
    results = [{"sql": t[:300], "columns": [], "rows": [], "truncated": False, "affected": None, "output": "", "done": False}
               for t, _ in stmts]
    if engine == "mysql":
        sets, _ = parse_my_xml(out)
        i = 0
        for s in sets:
            if s["cols"] == [MARK, "n"] and s["rows"]:
                i = _int(s["rows"][0][0], i)
                if i < len(results):
                    n = _int(s["rows"][0][1])
                    results[i]["affected"] = n if n is not None and n >= 0 else None
                    results[i]["done"] = True
                i += 1
            elif i < len(results) and not results[i]["columns"] and not results[i]["rows"]:
                results[i]["columns"] = s["cols"]
                results[i]["rows"] = s["rows"][:CONSOLE_ROWS]
                results[i]["truncated"] = len(s["rows"]) > CONSOLE_ROWS
        return results
    i, text = -1, []
    tag = re.compile(r"^(?:INSERT \d+|UPDATE|DELETE|MERGE|COPY|MOVE|FETCH|SELECT) (\d+)$")
    for ln in out.splitlines():
        m = re.fullmatch(MARK + r" (-?\d+)", ln)
        if m:
            if 0 <= i < len(results):
                results[i]["output"] = "\n".join(text)[:20000]
                results[i]["done"] = True
                for t in reversed(text):
                    mt = tag.match(t)
                    if mt:
                        results[i]["affected"] = int(mt.group(1))
                        break
            i, text = int(m.group(1)) + 1, []
            continue
        if i < 0 or i >= len(results):
            continue
        r = results[i]
        if wrapped[i] and ln.startswith("{"):
            try:
                obj = _jload(ln)
            except ValueError:
                text.append(ln)
                continue
            if len(r["rows"]) >= CONSOLE_ROWS:
                r["truncated"] = True
                continue
            if not r["columns"]:
                r["columns"] = [k for k, _ in obj]
            r["rows"].append([_jtext(v) for _, v in obj])
        else:
            text.append(ln)
    if 0 <= i < len(results) and text:  # sentencia que falló a medias
        results[i]["output"] = "\n".join(text)[:20000]
    return results


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/query", methods=["POST"])
@login_required
@needs_engine
def query(engine):
    d = _json()
    db, schema = _ctx(engine, d)
    sql = d.get("sql")
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 1024 * 1024:
        raise Bad("Consulta vacía o demasiado grande (máx. 1 MB)")
    stmts, err = split_sql(sql, engine)
    if err:
        raise Bad(err)
    if not stmts:
        raise Bad("Consulta vacía")
    if len(stmts) > CONSOLE_STMTS:
        raise Bad(f"Máximo {CONSOLE_STMTS} sentencias por ejecución")
    readonly = _readonly_reason(engine, db, schema)
    for text, skel in stmts:
        words = _words(skel)
        first = words[0] if words else ""
        if engine == "mysql" and first in MY_CLIENT_CMDS:
            raise Bad(f"'{first}' es un comando del cliente mysql: no está permitido aquí")
        if readonly and (first not in READ_KW or set(words) & WRITE_KW):
            raise Bad(readonly + " Aquí solo se permiten consultas de lectura (SELECT, SHOW, EXPLAIN…).")
    script, wrapped = console_script(engine, stmts, schema)
    t0 = time.monotonic()
    code, out, err, trunc = _exec(engine, db, script, readonly=bool(readonly), quiet=False)
    ms = int((time.monotonic() - t0) * 1000)
    results = parse_console(engine, out, stmts, wrapped)
    ran = [r for r in results if r["done"] or r["rows"] or r["columns"] or r["output"]]
    for r in ran:
        r.pop("done", None)
    resp = {"ok": code == 0, "results": ran, "time_ms": ms, "truncated": bool(trunc),
            "total": len(stmts)}
    if code != 0:
        resp["error"] = err or ("Resultado demasiado grande (máx. 2 MB)" if trunc else "Fallo desconocido")
    return jsonify(resp)


# ── Exportar / importar ──────────────────────────────────────────────

def _db_exists(engine, db):
    if engine == "mysql":
        rows = _q("mysql", None, [f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = {my_text(db)}"])[0]
        return any(r[0] == db for r in rows)
    try:
        return bool(_q("pg", db, ["SELECT to_jsonb(1)"]))
    except Bad:
        return False


def _export_stream(engine, meta, fmt):
    """CSV / JSON de una tabla entera, en streaming (sin cargarla en memoria)."""
    names = [c["name"] for c in meta["columns"]]
    vals, _ = select_exprs(engine, meta, full=True)
    tq, order = meta["tq"], build_order(engine, meta, None)
    if engine == "mysql":
        sel = ", ".join(f"{e} AS `c{i}`" for i, e in enumerate(vals))
        sql = _my_prefix(True, limit_time=False) + f"SELECT {sel} FROM {tq}{order};\n"
        env = None
    else:
        sel = ", ".join(f"{e} AS {qi('pg', n)}" for e, n in zip(vals, names))
        inner = f"SELECT {sel} FROM {tq}{order}"
        sql = (f"COPY ({inner}) TO STDOUT WITH (FORMAT csv, HEADER true);\n" if fmt == "csv"
               else f"SELECT row_to_json(t) FROM ({inner}) t;\n")
        env = _pg_env(True, timeout=0)
    cmd = _client(engine, meta["db"] if engine == "pg" else None)

    def gen():
        errf = tempfile.TemporaryFile()
        p = _popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf, env=env)
        timer = threading.Timer(EXPORT_TIMEOUT, _kill, args=(p,))
        timer.start()
        threading.Thread(target=_feed, args=(p.stdin, sql.encode()), daemon=True).start()
        first = True
        try:
            if fmt == "json":
                yield b"[\n"
            if engine == "mysql":
                dec, rd = codecs.getincrementaldecoder("utf-8")("replace"), MyXml()
                if fmt == "csv":
                    yield csv_line(names).encode()
                while True:
                    chunk = p.stdout.read(CHUNK)
                    events = rd.feed(dec.decode(chunk, final=not chunk)) if chunk else rd.close()
                    buf = []
                    for ev, data in events:
                        if ev != "row":
                            continue
                        if fmt == "csv":
                            buf.append(csv_line(data[1]))
                        else:
                            buf.append(("" if first else ",\n") + json.dumps(dict(zip(names, data[1])), ensure_ascii=False))
                            first = False
                    if buf:
                        yield "".join(buf).encode()
                    if not chunk:
                        break
            elif fmt == "csv":
                while chunk := p.stdout.read(CHUNK):
                    yield chunk
            else:
                rest = b""
                while True:
                    chunk = p.stdout.read(CHUNK)
                    data = rest + chunk
                    lines = data.split(b"\n")
                    rest = lines.pop() if chunk else b""
                    out = [ln for ln in lines if ln.strip()]
                    if out:
                        yield (b"" if first else b",\n") + b",\n".join(out)
                        first = False
                    if not chunk:
                        break
            code = p.wait()
            if code != 0:
                errf.seek(0)
                msg = _clean_err(errf.read(8192).decode(errors="replace")) or f"código {code}"
                yield (f"\n# ERROR: la exportación falló: {msg}\n" if fmt == "csv"
                       else ("" if first else ",\n") + json.dumps({"_error": msg}, ensure_ascii=False)).encode()
            if fmt == "json":
                yield b"\n]\n"
        finally:
            timer.cancel()
            if p.poll() is None:  # descarga cancelada
                _kill(p)
            p.stdout.close()
            p.wait()
            errf.close()
    return gen()


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/export")
@login_required
@needs_engine
def export(engine):
    d = request.args
    db, schema = _ctx(engine, d)
    fmt, table = d.get("format"), d.get("table") or None
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "sql":
        if table:
            meta = _table_meta(engine, db, schema, table)
            cmd, base = _dump_cmd(engine, db, meta["name"], schema), f"{db}-{meta['name']}"
        else:
            if not _db_exists(engine, db):
                raise Bad("La base de datos no existe", 404)
            cmd, base = _dump_cmd(engine, db), db
        return Response(_dump_stream(cmd), mimetype="application/gzip",
                        headers={"Content-Disposition": _disposition(f"{base}-{stamp}.sql.gz"), "Cache-Control": "no-store"})
    if fmt not in ("csv", "json") or not table:
        raise Bad("Formato inválido")
    meta = _table_meta(engine, db, schema, table)
    mime = "text/csv; charset=utf-8" if fmt == "csv" else "application/json; charset=utf-8"
    return Response(_export_stream(engine, meta, fmt), mimetype=mime,
                    headers={"Content-Disposition": _disposition(f"{db}-{meta['name']}-{stamp}.{fmt}"),
                             "Cache-Control": "no-store"})


def _encoding(name):
    return {"utf-8": "utf-8-sig", "latin-1": "latin-1"}.get(name, "utf-8-sig")


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/import/preview", methods=["POST"])
@login_required
@needs_engine
def import_preview(engine):
    """Vista previa: el navegador manda solo el principio del archivo (≤ 256 KB)."""
    if (request.content_length or 0) > 300 * 1024:
        raise Bad("Vista previa demasiado grande", 413)
    f = request.files.get("file")
    if not f:
        raise Bad("Falta el archivo")
    raw = f.read(256 * 1024)
    text = raw.decode(_encoding(request.form.get("encoding", "utf-8")), errors="replace")
    if request.form.get("partial") == "1" and "\n" in text:
        text = text[:text.rfind("\n")]  # la última línea puede estar cortada
    delim = request.form.get("delimiter") or ""
    if delim not in (",", ";", "\t", "|"):
        delim = detect_delimiter(text)
    rows_ = []
    try:
        for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim):
            if r:
                rows_.append([v[:500] for v in r[:200]])
            if len(rows_) >= 21:
                break
    except csv.Error as e:
        raise Bad(f"CSV inválido: {e}")
    return jsonify({"ok": True, "delimiter": delim, "rows": rows_})


def _import_batches(engine, meta, reader, mapping, opts, errors):
    """Genera (sql, filas) por lote. Cada valor pasa por lit(); filas inválidas → errors."""
    cols = [_col(meta, m) if m else None for m in mapping]
    used = [(i, c) for i, c in enumerate(cols) if c]
    need = max(i for i, _ in used) + 1
    tq = meta["tq"]
    collist = ", ".join(qi(engine, c["name"]) for _, c in used)
    ignore, empty_null = bool(opts.get("ignore_dup")), bool(opts.get("empty_null"))
    head = f"INSERT {'IGNORE ' if ignore and engine == 'mysql' else ''}INTO {tq} ({collist}) VALUES\n"
    batch, size, lineno = [], 0, 0

    def flush():
        body = head + ",\n".join(batch)
        if engine == "mysql":
            return body + ";\nSELECT ROW_COUNT();\n"
        return (f"WITH i AS ({body}{' ON CONFLICT DO NOTHING' if ignore else ''} RETURNING 1)"
                " SELECT count(*) FROM i;\n")

    for row in reader:
        lineno += 1
        if lineno == 1 and opts.get("header"):
            continue
        if not row or row == [""]:
            continue
        if len(row) < need:
            errors.append(f"Fila {lineno}: tiene {len(row)} campos, se esperaban al menos {need}")
            continue
        try:
            vals = []
            for i, c in used:
                v = row[i]
                if v == "\\N" or (v == "" and empty_null and c["nullable"]):
                    v = None
                vals.append(lit(engine, c["kind"], v))
        except Bad as e:
            errors.append(f"Fila {lineno}: {e}")
            continue
        tup = "(" + ", ".join(vals) + ")"
        batch.append(tup)
        size += len(tup)
        if len(batch) >= IMPORT_ROWS or size >= IMPORT_BYTES:
            yield flush(), len(batch)
            batch, size = [], 0
    if batch:
        yield flush(), len(batch)


@dbexplorer_bp.route("/api/<any(mysql, pg):engine>/import", methods=["POST"])
@login_required
@needs_engine
def import_csv(engine):
    if not request.content_length:
        raise Bad("Falta Content-Length", 411)
    if request.content_length > IMPORT_MAX + 1024 * 1024:
        raise Bad("Archivo demasiado grande (máx. 100 MB)", 413)
    try:
        opts = json.loads(request.form.get("opts") or "{}")
    except ValueError:
        raise Bad("Opciones inválidas")
    if not isinstance(opts, dict):
        raise Bad("Opciones inválidas")
    db, schema = _ctx(engine, opts)
    _require_writable(engine, db, schema)
    meta = _table_meta(engine, db, schema, opts.get("table"))
    if meta["type"] != "table":
        raise Bad("Solo se puede importar en tablas")
    mapping = opts.get("mapping")
    if not isinstance(mapping, list) or len(mapping) > 1000 or not any(mapping):
        raise Bad("Asigna al menos una columna del CSV a una columna de la tabla")
    mapping = [m or None for m in mapping]
    named = [m for m in mapping if m]
    if len(set(named)) != len(named):
        raise Bad("Cada columna de la tabla solo puede recibir una columna del CSV")
    for m in named:
        if not _col(meta, m)["editable"]:
            raise Bad(f"La columna {m} no se puede escribir")
    delim = opts.get("delimiter")
    if delim not in (",", ";", "\t", "|"):
        raise Bad("Separador inválido")
    f = request.files.get("file")
    if not f:
        raise Bad("Falta el archivo")
    stream = io.TextIOWrapper(f.stream, encoding=_encoding(opts.get("encoding", "utf-8")), errors="strict", newline="")
    reader = csv.reader(stream, delimiter=delim)
    errors, sent, bad = [], 0, None
    cmd = _client(engine, db if engine == "pg" else None)
    env = None if engine == "mysql" else _pg_env(False)
    with tempfile.TemporaryFile() as outf, tempfile.TemporaryFile() as errf:
        p = _popen(cmd, stdin=subprocess.PIPE, stdout=outf, stderr=errf, env=env)
        timer = threading.Timer(IMPORT_TIMEOUT, _kill, args=(p,))
        timer.start()
        try:
            p.stdin.write(((_my_prefix(False, limit_time=False) + "START TRANSACTION;\n") if engine == "mysql" else "BEGIN;\n").encode())
            for sql, n in _import_batches(engine, meta, reader, mapping, opts, errors):
                p.stdin.write(sql.encode())
                sent += n
            p.stdin.write(b"COMMIT;\n")
        except BrokenPipeError:
            pass  # el cliente terminó (error SQL): el motivo está en stderr
        except UnicodeDecodeError:
            bad = "El archivo no está en la codificación elegida (prueba con Latin-1)"
            _kill(p)
        except (csv.Error, OSError, Bad) as e:
            bad = f"No se pudo leer el CSV: {e}"
            _kill(p)
        try:
            p.stdin.close()
        except OSError:
            pass
        code = p.wait()
        timer.cancel()
        err = _clean_err(_tail(errf))
        outf.seek(0)
        out = outf.read(8 * 1024 * 1024).decode(errors="replace")
    if bad:
        return jsonify({"ok": False, "error": bad + ". No se insertó ninguna fila."}), 400
    if code != 0:
        return jsonify({"ok": False, "error": (err or f"Falló con código {code}") +
                        "\nSe revirtió la importación: no se insertó ninguna fila.", "row_errors": errors[:20]}), 400
    if engine == "mysql":
        inserted = sum(_int(s["rows"][0][0], 0) for s in parse_my_xml(out)[0] if s["rows"])
    else:
        inserted = sum(_int(ln, 0) for ln in out.splitlines() if ln.strip())
    return jsonify({"ok": True, "inserted": inserted, "sent": sent, "skipped": max(sent - inserted, 0),
                    "errors": len(errors), "row_errors": errors[:20]})


if __name__ == "__main__":
    # Autocomprobación de las funciones puras: python -m app.modules.dbexplorer
    evil = ["'); DROP TABLE x; --", "a'b", "a\\'b", "\\", "x\x00y", "línea\nnueva\r\t", "😀 ñ", "$mp$", "x$mp", "`", '"']
    for v in evil:
        s = my_text(v)
        assert re.fullmatch(r"_utf8mb4 X'[0-9a-f]*'", s), s
        assert bytes.fromhex(s[11:-1]).decode() == v
        if "\x00" not in v:
            p = pg_text(v)
            if p.startswith("'"):
                assert "\\" not in v and p[1:-1].replace("''", "'") == v and "'" not in p[1:-1].replace("''", "")
            else:
                tag = p[:p.index("$", 1) + 1]
                assert p.endswith(tag) and p[len(tag):-len(tag)] == v and (v + tag).find(tag) == len(v), p
    try:
        pg_text("a\x00")
        raise AssertionError("NUL aceptado")
    except Bad:
        pass
    assert pg_text("\\x$mp") == "$mp1$\\x$mp$mp1$" and pg_text("a\\b") == "$mp$a\\b$mp$" and pg_text("x$mp") == "'x$mp'"
    assert lit("mysql", "int", "42") == "42" and lit("mysql", "int", "4 OR 1=1").startswith("_utf8mb4 X'")
    assert lit("mysql", "num", "-1.5e3") == "-1.5e3" and lit("mysql", "num", "1;DROP").startswith("_utf8mb4")
    assert lit("mysql", "bit", "101") == "b'101'" and lit("mysql", "text", None) == "NULL"
    assert lit("mysql", "bin", {"hex": "00ff"}) == "X'00ff'" and lit("pg", "bin", {"hex": "00ff"}) == "decode('00ff', 'hex')"
    assert lit("pg", "int", "1') OR ('1'='1") == "'1'') OR (''1''=''1'"
    for badhex in ("0g", "abc", "'x"):
        try:
            lit("mysql", "bin", {"hex": badhex})
            raise AssertionError(badhex)
        except Bad:
            pass
    assert qi("mysql", "a`b") == "`a``b`" and qi("pg", 'a"b') == '"a""b"'
    for badid in ("", "a\nb", "x\x00", "a" * 200):
        try:
            qi("pg", badid)
            raise AssertionError(repr(badid))
        except Bad:
            pass
    try:
        qi("mysql", "a\\")
        raise AssertionError("barra en identificador MySQL")
    except Bad:
        pass
    assert like_escape("50%_!") == "50!%!_!!"
    assert csv_line([None, "", "a,b", 'x"y', "ok", " s"]) == ',"","a,b","x""y",ok," s"\r\n'
    assert detect_delimiter("a,b,c\n1,2,3\n4,5,6") == ","
    assert detect_delimiter("a;b;c\n1,5;2;3\n4;5,5;6") == ";"
    assert detect_delimiter("a\tb\n1\t2") == "\t"
    assert detect_delimiter('"x;y",b\n"1;2",3') == ","
    assert detect_delimiter("solo") == ","
    # Lexer
    st, e = split_sql("SELECT 'a;b'; SELECT \"c;\" -- x;y\n; /* ; */ SELECT 1", "mysql")
    assert e is None and [t for t, _ in st] == ["SELECT 'a;b'", 'SELECT "c;" -- x;y', "/* ; */ SELECT 1"], st
    assert split_sql("SELECT 1; \\! id", "mysql")[1] and split_sql("SELECT 1 \\g", "pg")[1]
    assert split_sql("SELECT 'a\\'; \\! id'", "mysql")[1] is None  # dentro de la cadena (MySQL escapa con \)
    assert split_sql("SELECT 'a\\'; \\! id; --'", "pg")[1]          # PG: \ no escapa en '…' → comando fuera
    assert split_sql("SELECT E'a\\'; \\! x'", "pg")[1] is None
    assert split_sql("/*! \\! id */", "mysql")[1]                   # /*! … */ lo ejecuta el servidor
    assert split_sql("SELECT $f$ ; \\! $f$", "pg")[1] is None and len(split_sql("SELECT $f$;$f$; SELECT 2", "pg")[0]) == 2
    assert split_sql("SELECT 1 /* a /* b */ ; */; SELECT 2", "pg")[0][0][0] == "SELECT 1 /* a /* b */ ; */"
    assert _words(split_sql("with x as (delete from t returning *) select 1", "pg")[0][0][1])[:1] == ["with"]
    assert not _pg_wrappable(_words(split_sql("with x as (delete from t returning *) select * from x", "pg")[0][0][1]))
    assert _pg_wrappable(_words(split_sql("SELECT 'insert' AS x", "pg")[0][0][1]))
    # XML de mysql: NULL vs 'NULL', tabuladores, saltos, control, varios resultsets
    xml = ('<?xml version="1.0"?>\n\n<resultset statement="SELECT" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
           '  <row>\n\t<field name="a">NULL</field>\n\t<field name="b" xsi:nil="true" />\n'
           '\t<field name="c">x\ty\nz\r\x01 &lt;&amp;&gt;</field>\n\t<field name="d"></field>\n  </row>\n</resultset>\n'
           '<?xml version="1.0"?>\n<resultset statement="x" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"></resultset>\n')
    sets, broken = parse_my_xml(xml)
    assert not broken and len(sets) == 2 and sets[1]["rows"] == []
    assert sets[0]["cols"] == ["a", "b", "c", "d"] and sets[0]["rows"] == [["NULL", None, "x\ty\nz\r\x01 <&>", ""]], sets
    rd, got = MyXml(), []
    for k in range(0, len(xml), 7):  # en trozos (exportación en streaming)
        got += rd.feed(xml[k:k + 7])
    got += rd.close()
    assert [g[1][1] for g in got if g[0] == "row"] == [["NULL", None, "x\ty\nz\r\x01 <&>", ""]]
    sets, broken = parse_my_xml(xml[:150])
    assert broken
    # JSON de PostgreSQL
    o = _jload('{"a":null,"b":"NULL","a":1.10,"j":{"k":[1,true]}}')
    assert [k for k, _ in o] == ["a", "b", "a", "j"] and [_jtext(v) for _, v in o] == [None, "NULL", "1.10", '{"k": [1, true]}']
    # Metadatos falsos → WHERE / ORDER / clave
    meta = {"columns": [{"name": "id", "kind": "int", "sortable": True}, {"name": "n`m", "kind": "text", "sortable": True},
                        {"name": "b", "kind": "bin", "sortable": True}, {"name": "j", "kind": "json", "sortable": False}],
            "key": ["id"]}
    w = build_where("mysql", meta, [{"col": "n`m", "op": "like", "value": "50%'"}, {"col": "id", "op": "ne", "value": "1"},
                                    {"col": "b", "op": "eq", "value": "0xff"}, {"col": "j", "op": "null"}])
    assert w == (" WHERE `n``m` LIKE " + my_text("%50!%'%") + " ESCAPE '!' AND NOT (`id` <=> 1) AND `b` = X'ff' AND `j` IS NULL"), w
    w = build_where("pg", meta, [{"col": "j", "op": "eq", "value": "{}"}, {"col": "id", "op": "gt", "value": "1; DROP"}])
    assert w == " WHERE \"j\"::text = '{}' AND \"id\" > '1; DROP'", w
    for badf in ([{"col": "nope", "op": "eq", "value": "1"}], [{"col": "id", "op": "OR 1=1", "value": "1"}], "x"):
        try:
            build_where("mysql", meta, badf)
            raise AssertionError(badf)
        except Bad:
            pass
    assert build_order("mysql", meta, {"col": "n`m", "dir": "desc"}) == " ORDER BY `n``m` DESC, `id`"
    assert build_order("pg", meta, None) == ' ORDER BY "id"'
    assert where_key("mysql", meta, {"id": "7"}) == "`id` = 7"
    assert where_key("pg", meta, {"id": "7' OR '1'='1"}) == "\"id\" = '7'' OR ''1''=''1'"
    for badk in ({}, {"id": None}, {"id": "1", "x": "2"}):
        try:
            where_key("mysql", meta, badk)
            raise AssertionError(badk)
        except Bad:
            pass
    v, ln = select_exprs("mysql", {"columns": meta["columns"], "key": ["id"]})
    assert v == ["`id`", "LEFT(`n``m`, 2000)", "HEX(LEFT(`b`, 64))", "LEFT(`j`, 2000)"] and [i for i, _ in ln] == [1, 2, 3]
    # DDL
    assert build_type("mysql", {"type": "VARCHAR", "length": "255"}) == ("VARCHAR(255)", "text")
    assert build_type("mysql", {"type": "DECIMAL", "length": "10, 2", "unsigned": True})[0] == "DECIMAL(10,2) UNSIGNED"
    assert build_type("pg", {"type": "timestamp with time zone", "length": "3"})[0] == "timestamp(3) with time zone"
    assert build_type("mysql", {"type": "ENUM", "values": ["a'b", "c\\d"]})[0] == "ENUM('a''b','c\\\\d')"
    for bt in ({"type": "VARCHAR"}, {"type": "INT); DROP TABLE x; --"}, {"type": "VARCHAR", "length": "1)--"},
               {"type": "DECIMAL", "length": "99"}, {"type": "TEXT", "length": "5"}, {"type": "ENUM", "values": ["a\nb"]}):
        try:
            build_type("mysql", bt)
            raise AssertionError(bt)
        except Bad:
            pass
    assert build_coldef("mysql", {"name": "precio", "type": "DECIMAL", "length": "10,2", "nullable": False,
                                  "default": {"kind": "value", "value": "0.00"}, "comment": "it's"}) == \
        "`precio` DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT 'it''s'"
    assert build_coldef("pg", {"name": "id", "type": "bigint", "auto": True, "primary": True}) == \
        '"id" bigint NOT NULL GENERATED BY DEFAULT AS IDENTITY'
    assert build_coldef("pg", {"name": "t", "type": "text", "nullable": True, "default": {"kind": "value", "value": "a\\'b"}}) == \
        "\"t\" text DEFAULT $mp$a\\'b$mp$"
    assert build_coldef("mysql", {"name": "t", "type": "TEXT", "nullable": True, "default": {"kind": "value", "value": "x\\'; DROP"}}) == \
        "`t` TEXT NULL DEFAULT 'x\\\\''; DROP'"
    for bc in ({"name": "a b", "type": "INT"}, {"name": "x", "type": "INT", "default": {"kind": "value", "value": "1 OR 1"}},
               {"name": "x", "type": "INT", "default": {"kind": "expr", "value": "sleep(10)"}},
               {"name": "x", "type": "TEXT", "default": {"kind": "value", "value": "a\nb"}},
               {"name": "x", "type": "TEXT", "auto": True}):
        try:
            build_coldef("mysql", bc)
            raise AssertionError(bc)
        except Bad:
            pass
    ct = build_create_table("mysql", "`d`.`t`", [{"name": "id", "type": "INT", "auto": True, "primary": True},
                                                 {"name": "nombre", "type": "VARCHAR", "length": "80", "nullable": True}])
    assert ct == ["CREATE TABLE `d`.`t` (\n  `id` INT NOT NULL AUTO_INCREMENT,\n  `nombre` VARCHAR(80) NULL,\n  PRIMARY KEY (`id`)\n)"], ct
    cur = {"name": "c", "type": "integer", "kind": "int", "nullable": True, "comment": "", "identity": "",
           "default_form": {"kind": "none"}}
    assert build_pg_modify('"s"."t"', cur, {"name": "c2", "type": "bigint", "nullable": False,
                                            "default": {"kind": "value", "value": "5"}}) == [
        'ALTER TABLE "s"."t" ALTER COLUMN "c" TYPE bigint USING "c"::bigint,\n  ALTER COLUMN "c" SET NOT NULL,\n'
        '  ALTER COLUMN "c" SET DEFAULT 5', 'ALTER TABLE "s"."t" RENAME COLUMN "c" TO "c2"']
    assert parse_coltype("mysql", "int(10) unsigned")["base"] == "INT" and parse_coltype("mysql", "int(10) unsigned")["unsigned"]
    assert parse_coltype("mysql", "enum('a','b''c')")["values"] == ["a", "b'c"]
    assert parse_coltype("pg", "character varying(255)") == {"base": "character varying", "length": "255", "unsigned": False, "values": []}
    assert parse_coltype("pg", "timestamp(3) with time zone")["base"] == "timestamp with time zone"
    assert _pg_default_form("'it''s'::text", "") == {"kind": "value", "value": "it's"}
    assert _pg_default_form("nextval('s'::regclass)", "")["kind"] == "keep"
    print("dbexplorer: autocomprobación OK")
