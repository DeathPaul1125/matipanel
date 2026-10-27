"""Prueba: diagrama entidad-relación del Explorador BD (catálogo simulado de MySQL y PostgreSQL)."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import dbexplorer as DX, databases as DB

DB._installed = lambda engine: True
DX._ctx = lambda engine, args: (args.get("db"), (args.get("schema") or "public") if engine == "pg" else None)
SQLS = []


def fake_q(engine, db, sqls):
    SQLS.append((engine, sqls))
    if engine == "mysql":
        return [
            [["clientes", "BASE TABLE", "10"], ["pedidos", "BASE TABLE", "50"], ["detalle", "BASE TABLE", "200"], ["v_ventas", "VIEW", None]],
            [["clientes", "id", "int", "NO", "PRI"], ["clientes", "nombre", "varchar(80)", "YES", ""],
             ["pedidos", "id", "int", "NO", "PRI"], ["pedidos", "cliente_id", "int", "NO", "MUL"],
             ["detalle", "pedido_id", "int", "NO", "PRI"], ["detalle", "linea", "int", "NO", "PRI"],
             ["detalle", "padre", "int", "YES", "MUL"], ["v_ventas", "total", "decimal(10,2)", "YES", ""]],
            [["fk_ped_cli", "pedidos", "cliente_id", "tienda", "clientes", "id", "CASCADE", "RESTRICT"],
             ["fk_det_ped", "detalle", "pedido_id", "tienda", "pedidos", "id", "CASCADE", "CASCADE"],
             ["fk_det_self", "detalle", "padre", "tienda", "detalle", "linea", "SET NULL", "NO ACTION"],
             ["fk_ext", "pedidos", "cliente_id", "otra", "personas", "id", "RESTRICT", "RESTRICT"]],
        ]
    return [
        [["autor", "r", 3], ["libro", "r", -1]],
        [["autor", "id", "integer", False, True], ["libro", "id", "integer", False, True], ["libro", "autor_id", "integer", True, False]],
        [["libro_autor_fk", "libro", "autor_id", "public", "autor", "id", "c", "a"]],
    ]


DX._q = fake_q
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True

d = c.get("/bd/api/mysql/erd?db=tienda").get_json()
assert d["ok"] and [t["name"] for t in d["tables"]] == ["clientes", "pedidos", "detalle", "v_ventas"]
t = {t["name"]: t for t in d["tables"]}
assert t["v_ventas"]["type"] == "view" and t["pedidos"]["rows"] == 50
assert t["pedidos"]["columns"][1] == {"name": "cliente_id", "type": "int", "nullable": False, "pk": False, "fk": True}
assert [col["pk"] for col in t["detalle"]["columns"]] == [True, True, False]  # PK compuesta
fk = {f["name"]: f for f in d["fks"]}
assert fk["fk_ped_cli"]["ref_schema"] is None and fk["fk_ped_cli"]["on_delete"] == "CASCADE"
assert fk["fk_ext"]["ref_schema"] == "otra"  # otra base: se marca para no dibujarla como local
assert fk["fk_det_self"]["table"] == fk["fk_det_self"]["ref_table"] == "detalle"
assert "tienda".encode().hex() in SQLS[-1][1][0].lower()  # filtra por la base pedida (en hexadecimal: sin inyección)

d = c.get("/bd/api/pg/erd?db=biblioteca&schema=public").get_json()
assert d["ok"] and d["tables"][1]["rows"] is None  # reltuples -1 = sin estadísticas
assert d["fks"] == [{"name": "libro_autor_fk", "table": "libro", "columns": ["autor_id"], "ref_schema": None, "ref_table": "autor",
                     "ref_columns": ["id"], "on_delete": "CASCADE", "on_update": "NO ACTION"}]
assert d["tables"][1]["columns"][1]["fk"] and d["tables"][0]["columns"][0]["pk"]

# tope de tablas
DX.ERD_MAX_TABLES = 2
d = c.get("/bd/api/mysql/erd?db=tienda").get_json()
assert d["truncated"] and len(d["tables"]) == 2 and all(f["table"] in ("clientes", "pedidos") for f in d["fks"])
print("erd ok")
