"""Prueba: Contabo (API simulada: no sale ninguna petición a Internet)."""
import sys, types, os, json, uuid
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json", "MATIPANEL_NOTIFY": TMPD + "/n.json",
                   "MATIPANEL_CONTABO": TMPD + "/contabo.json", "MATIPANEL_NOTIFY_WORKER": "0", "MATIPANEL_DEPLOY_WORKER": "0",
                   "MATIPANEL_APPBACKUP_WORKER": "0", "MATIPANEL_MONITOR_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app, audit, roles, contabo as api
from app.modules import contabo as mod

SECRET, PASSWORD = "secreto-del-cliente", "clave-de-api"
calls, snaps = [], [{"snapshotId": "snap1", "name": "antes", "description": "", "createdDate": "2026-11-01T10:00:00Z",
                     "autoDeleteDate": "2026-12-01T10:00:00Z"}]


def fake(method, host, path, headers, body):
    """Contabo simulado: dos VPS; el 100 es «este servidor» (misma IP)."""
    calls.append((method, host, path, headers, body))
    if host == api.AUTH_HOST:
        ok = f"password={PASSWORD}" in body and "client_secret=" + SECRET in body
        return (200, b'{"access_token": "TOK", "expires_in": 300}') if ok else (401, b'{"error_description": "Invalid user credentials"}')
    assert host == api.API_HOST and headers["Authorization"] == "Bearer TOK"
    assert uuid.UUID(headers["x-request-id"]).version == 4
    p = path.split("?")[0]
    if p == "/v1/compute/instances":
        return 200, json.dumps({"_pagination": {"totalPages": 1}, "data": [
            {"instanceId": 200, "displayName": "web", "name": "vmi200", "status": "stopped", "ipConfig": {"v4": {"ip": "198.51.100.9"}}},
            {"instanceId": 100, "displayName": "panel", "name": "vmi100", "status": "running", "cpuCores": 4, "ramMb": 8192,
             "ipConfig": {"v4": {"ip": "203.0.113.7"}, "v6": {"ip": "2001:0db8:0000:0000:0000:0000:0000:0001"}}}]}).encode()
    if "/actions/" in p:
        return 201, b'{"data": [{}]}'
    if p.endswith("/snapshots") and method == "GET":
        return 200, json.dumps({"_pagination": {"totalPages": 1}, "data": snaps}).encode()
    if p.endswith("/snapshots") and method == "POST":
        if len(snaps) >= 2:
            return 402, b'{"message": "Snapshot limit reached"}'
        snaps.append({"snapshotId": "snap2", "name": json.loads(body)["name"], "createdDate": "2026-11-24T10:00:00Z"})
        return 201, json.dumps({"data": [snaps[-1]]}).encode()
    if p.endswith("/rollback") and method == "POST":
        return 200, b'{"_links": {}}'
    if method == "DELETE":
        return 204, b""
    return 404, b'{"message": "Not Found"}'


api.transport = fake
mod._local_ips = lambda: {"203.0.113.7", "10.0.0.5"}
app = create_app(); app.config["TESTING"] = True
c = app.test_client(); c.environ_base["HTTP_ORIGIN"] = "http://localhost"
with c.session_transaction() as s:
    s["_user_id"] = "admin"; s["_fresh"] = True

# Sin cuenta: la pantalla abre y no se llama a Contabo
html = c.get("/contabo/").get_data(as_text=True)
assert "Conectar cuenta de Contabo" in html and c.get("/contabo/api/instances").json == {"ok": True, "configured": False, "instances": []}
assert c.post("/contabo/api/instances/100/action", json={"action": "start"}).status_code == 400 and not calls

# Credenciales malas: se prueban contra Contabo y NO se guardan
good = {"client_id": "INT-1", "client_secret": SECRET, "api_user": "ops@ejemplo.gt", "api_password": PASSWORD}
r = c.post("/contabo/api/config", json={**good, "api_password": "mala"})
assert r.status_code == 502 and "credenciales" in r.json["error"] and not os.path.exists(TMPD + "/contabo.json")
assert c.post("/contabo/api/config", json={**good, "client_id": ""}).status_code == 400
r = c.post("/contabo/api/config", json=good)
assert r.json == {"ok": True, "cfg": {"configured": True, "client_id": "INT-1", "api_user": "ops@ejemplo.gt"}}
if os.name == "posix":
    assert os.stat(TMPD + "/contabo.json").st_mode & 0o777 == 0o600
html = c.get("/contabo/").get_data(as_text=True)
assert SECRET not in html and PASSWORD not in html and "ops@ejemplo.gt" in html
# Editar dejando los secretos vacíos conserva los guardados
assert c.post("/contabo/api/config", json={"client_id": "INT-1", "api_user": "ops@ejemplo.gt"}).json["ok"]
assert api.load_creds()["client_secret"] == SECRET

# Lista: este servidor primero y marcado; el IPv6 se compara normalizado
j = c.get("/contabo/api/instances").json
assert [(i["id"], i["self"]) for i in j["instances"]] == [(100, True), (200, False)] and j["instances"][0]["ram_mb"] == 8192
mod._local_ips = lambda: {"2001:db8::1"}
assert c.get("/contabo/api/instances?force=1").json["instances"][0]["self"]
n = len(calls); c.get("/contabo/api/instances")
assert len(calls) == n  # caché corta: refrescos seguidos no vuelven a llamar

# Energía
assert c.post("/contabo/api/instances/200/action", json={"action": "start", "name": "web"}).json == {"ok": True}
assert calls[-1][:3] == ("POST", api.API_HOST, "/v1/compute/instances/200/actions/start")
for bad in ("rescue", "", "start/../stop"):
    assert c.post("/contabo/api/instances/200/action", json={"action": bad}).status_code == 400
assert c.post("/contabo/api/instances/999/action", json={"action": "start"}).status_code == 400  # no es de la cuenta
# Apagar este mismo servidor exige confirmación explícita (también en el servidor, no solo en la pantalla)
n = len([x for x in calls if "/actions/" in x[2]])
for act in ("stop", "shutdown"):
    r = c.post("/contabo/api/instances/100/action", json={"action": act, "confirm_self": "true"})
    assert r.status_code == 409 and r.json["need_confirm_self"]
assert len([x for x in calls if "/actions/" in x[2]]) == n
assert c.post("/contabo/api/instances/100/action", json={"action": "restart"}).json["ok"]  # reiniciar sí vuelve solo
assert c.post("/contabo/api/instances/100/action", json={"action": "shutdown", "confirm_self": True}).json["ok"]
assert calls[-1][2] == "/v1/compute/instances/100/actions/shutdown"

# Snapshots
assert [x["id"] for x in c.get("/contabo/api/instances/200/snapshots").json["snapshots"]] == ["snap1"]
assert c.post("/contabo/api/instances/200/snapshots", json={"name": "año nuevo"}).status_code == 400
r = c.post("/contabo/api/instances/200/snapshots", json={"name": " antes de  PHP-85 ", "description": "prueba"})
assert r.json["snapshot"]["name"] == "antes de PHP-85" and json.loads(calls[-1][4]) == {"name": "antes de PHP-85", "description": "prueba"}
r = c.post("/contabo/api/instances/200/snapshots", json={"name": "otro"})
assert r.status_code == 502 and "pago" in r.json["error"] and "Snapshot limit reached" in r.json["error"]
assert c.post("/contabo/api/instances/200/snapshots/snap1/rollback", json={}).json["ok"]
assert calls[-1][:3] == ("POST", api.API_HOST, "/v1/compute/instances/200/snapshots/snap1/rollback")
assert c.post("/contabo/api/instances/100/snapshots/snap1/rollback", json={}).status_code == 409
assert c.post("/contabo/api/instances/100/snapshots/snap1/rollback", json={"confirm_self": True}).json["ok"]
assert c.post("/contabo/api/instances/200/snapshots/sn%20ap/delete", json={}).status_code == 400
assert c.post("/contabo/api/instances/200/snapshots/snap1/delete", json={}).json["ok"]
assert calls[-1][:3] == ("DELETE", api.API_HOST, "/v1/compute/instances/200/snapshots/snap1")

# Token: uno solo para todo lo anterior; un 401 de la API lo renueva una vez
assert len([x for x in calls if x[1] == api.AUTH_HOST and PASSWORD in x[4]]) == 3  # 2 al guardar + 1 de uso
state = {"n": 0}
def expired(method, host, path, headers, body):
    if host == api.API_HOST and state["n"] == 0:
        state["n"] += 1
        return 401, b'{"message": "Unauthorized"}'
    return fake(method, host, path, headers, body)
api.transport = expired
assert c.get("/contabo/api/instances?force=1").json["ok"] and state["n"] == 1
def down(*a):
    raise OSError("sin red")
api.transport = down
j = c.get("/contabo/api/instances?force=1").json
assert not j["ok"] and j["configured"] and "No se pudo conectar" in j["error"]
api.transport = fake

# Auditoría: queda quién hizo qué, sin secretos
ev = audit.query(module="contabo")
dump = json.dumps(ev, ensure_ascii=False)
assert SECRET not in dump and PASSWORD not in dump and "mala" not in dump
assert any(e["action"] == "action" and e["target"] == "web" and e["ok"] for e in ev)
assert any(e["action"] == "action" and not e["ok"] and e["status"] == 409 for e in ev)
assert audit.MODULES["contabo"] == "Contabo" and "contabo.action" in audit.SENSITIVE

# Permisos: solo lectura ve el estado pero no cambia nada
roles.save({"default": "readonly", "users": {}})
assert c.get("/contabo/api/instances").json["ok"] and c.get("/contabo/").status_code == 200
for url, body in (("/contabo/api/instances/200/action", {"action": "start"}), ("/contabo/api/config/delete", {}),
                  ("/contabo/api/instances/200/snapshots", {"name": "x"}), ("/contabo/api/config", good)):
    assert c.post(url, json=body).status_code == 403
roles.save({"default": "admin", "users": {}})

# Desconectar borra las credenciales y el token en memoria
assert c.post("/contabo/api/config/delete", json={}).json["ok"]
assert not os.path.exists(TMPD + "/contabo.json") and not api._tokens
assert c.get("/contabo/api/instances").json["configured"] is False
print("contabo ok")
