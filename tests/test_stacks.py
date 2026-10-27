"""Stacks (Docker Compose arbitrario): API, validación, advertencias, archivos y borrado."""
import sys, types, os, json, shutil
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
ROOT = TMPD + "/sttest"
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)
os.environ.update({"MATIPANEL_PREFS": ROOT + "/p.json", "MATIPANEL_2FA_STORE": ROOT + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_STACKS": ROOT + "/stacks", "MATIPANEL_AUDIT": ROOT + "/audit.db",
                   "MATIPANEL_NOTIFY_WORKER": "0", "MATIPANEL_APPBACKUP_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from pathlib import Path
from app import create_app
from app import deployer as D
from app.modules import stacks as S
from app.modules.stacks import stacks_bp
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}
BASE = Path(ROOT + "/stacks")

# ── Comandos falsos ──
calls, writes, dirs = [], [], []
PS_MODE = {"lines": False}
SAFE_JSON = {"name": "x", "services": {"web": {"image": "nginx", "ports": [
    {"mode": "ingress", "target": 80, "published": "8080", "protocol": "tcp"},
    {"mode": "ingress", "host_ip": "127.0.0.1", "target": 81, "published": "8081", "protocol": "tcp"}]}}}
DANGER_JSON = {"services": {
    "bad": {"privileged": True, "network_mode": "host", "pid": "host", "cap_add": ["ALL", "CAP_SYS_ADMIN", "NET_ADMIN"],
            "volumes": [{"type": "bind", "source": "/", "target": "/host"},
                        {"type": "bind", "source": "/etc/", "target": "/e", "read_only": True},
                        {"type": "bind", "source": "/root", "target": "/r"},
                        {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"},
                        {"type": "bind", "source": "/srv/data", "target": "/d"},
                        {"type": "volume", "source": "data", "target": "/x"}],
            "ports": [{"target": 1, "published": "1"}]},
    "db": {"image": "postgres", "ports": [{"host_ip": "0.0.0.0", "target": 5432, "published": "5432", "protocol": "tcp"},
                                          {"host_ip": "::", "target": 53, "protocol": "udp"}]}}}
PS = [{"Service": "web", "Name": "stack-web-web-1", "State": "running", "Status": "Up 2 minutes", "Health": "healthy",
       "Image": "nginx:alpine", "Publishers": [{"URL": "0.0.0.0", "TargetPort": 80, "PublishedPort": 8080, "Protocol": "tcp"},
                                               {"URL": "::", "TargetPort": 80, "PublishedPort": 8080, "Protocol": "tcp"},
                                               {"URL": "", "TargetPort": 443, "PublishedPort": 0, "Protocol": "tcp"}]},
      {"Service": "worker", "Name": "stack-web-worker-1", "State": "exited", "Status": "Exited (1)", "Health": "",
       "Image": "busybox", "Publishers": None}]


def compose_file(cmd):
    return Path(cmd[cmd.index("-f") + 1]).read_text(encoding="utf-8")


def fake(cmd, timeout=60, **kw):
    calls.append(list(cmd))
    if cmd[:2] == ["docker", "ps"]:
        return 0, ("stack-web\tweb\trunning\t0.0.0.0:8080->80/tcp, :::8080->80/tcp, 443/tcp\n"
                   "stack-web\tworker\texited\t\n"
                   "otro\tx\trunning\t\n"
                   "stack-db\tweb\trunning\t127.0.0.1:5432->5432/tcp\n"), ""
    if cmd[:2] != ["docker", "compose"]:
        return 127, "", "?"
    assert cmd[2] == "-p" and cmd[3].startswith("stack-") and "--project-directory" in cmd, cmd
    rest = cmd[cmd.index("--project-directory") + 2:]
    if rest[:2] == ["config", "-q"]:
        text = compose_file(cmd)
        if "--env-file" in cmd:
            assert Path(cmd[cmd.index("--env-file") + 1]).exists()
        return (1, "", "yaml: line 2: mapping values are not allowed in this context") if "INVALIDO" in text else (0, "", "")
    if rest[:3] == ["config", "--format", "json"]:
        return 0, json.dumps(DANGER_JSON if "privileged" in compose_file(cmd) else SAFE_JSON), ""
    if rest[:4] == ["ps", "-a", "--format", "json"]:
        return 0, ("\n".join(json.dumps(x) for x in PS) if PS_MODE["lines"] else json.dumps(PS)), ""
    return 0, "", ""


def fake_capped(cmd, cap=0, timeout=0):
    calls.append(list(cmd))
    return 0, "web-1  | hola\n"


S.compose_base = lambda: ["docker", "compose"]
S.run = S.quiet = fake
S.run_capped = fake_capped
orig_write, orig_dir = D.write_file, D._ensure_dir
D.write_file = lambda p, data, mode=0o600: (writes.append((Path(p), mode)), orig_write(p, data, mode))[1]
D._ensure_dir = lambda p, mode=0o700: (dirs.append((Path(p), mode)), orig_dir(p, mode))[1]
post = lambda url, body=None: c.post("/stacks" + url, json=body or {}, headers=H)
GOOD = 'services:\n  web:\n    image: nginx:alpine\n    ports:\n      - "8080:80"\n'

# ── Páginas ──
assert c.get("/stacks/").status_code == 200
assert b"Uptime Kuma" in c.get("/stacks/").data
assert c.get("/stacks/s/nope").status_code == 404
assert c.get("/stacks/api/stacks").get_json() == {"ok": True, "compose": True, "dir": str(BASE), "stacks": []}

# ── Nombres y tamaños ──
for bad in ("", "A", "a", "1ab", "ab-", "a_b", "../x", "a" * 32, "web/x", "-ab"):
    r = post("/api/stacks", {"name": bad, "compose_text": GOOD})
    assert r.status_code == 400 and "Nombre" in r.get_json()["error"], (bad, r.get_json())
assert post("/api/stacks", {"name": "big", "compose_text": "#" * (256 * 1024 + 1)}).status_code == 400
assert post("/api/stacks", {"name": "big", "compose_text": GOOD, "env_text": "X" * (256 * 1024 + 1)}).status_code == 400
assert post("/api/stacks", {"name": "big", "compose_text": "a\0b"}).status_code == 400
assert post("/api/stacks", {"name": "big", "compose_text": "  \n"}).status_code == 400
assert post("/api/stacks", {"name": "big", "compose_text": ["x"]}).status_code == 400

# ── Inválido: muestra el error y no deja carpeta ──
r = post("/api/stacks", {"name": "roto", "compose_text": "INVALIDO: : :"})
assert r.status_code == 422 and "mapping values" in r.get_json()["error"], r.get_json()
assert not (BASE / "roto").exists()
r = post("/api/validate", {"name": "roto", "compose_text": "INVALIDO"})
assert r.status_code == 422 and not (BASE / "roto").exists()

# ── Validar: advertencias ──
r = post("/api/validate", {"name": "peligro", "compose_text": "privileged", "env_text": "A=1"}).get_json()
assert r["ok"] and r["services"] == ["bad", "db"] and not (BASE / "peligro").exists(), r
w = "\n".join(r["warnings"])
for frag in ("bad: privileged: true", "network_mode: host", "pid: host", "cap_add ALL", "cap_add SYS_ADMIN",
             "monta / —", "monta /etc (solo lectura)", "monta /root", "monta /var/run/docker.sock"):
    assert frag in w, (frag, w)
assert "NET_ADMIN" not in w and "/srv/data" not in w, w
assert r["public_ports"] == ["db: 0.0.0.0:5432 → 5432/tcp", "db: :::aleatorio → 53/udp"], r["public_ports"]
r = post("/api/validate", {"name": "seguro", "compose_text": GOOD}).get_json()
assert r["ok"] and r["warnings"] == [] and r["public_ports"] == ["web: 0.0.0.0:8080 → 80/tcp"], r

# ── Crear ──
writes.clear(); dirs.clear(); calls.clear()
r = post("/api/stacks", {"name": "web", "compose_text": GOOD.replace("\n", "\r\n"), "env_text": "PUERTO=8080\n"})
assert r.status_code == 200 and r.get_json()["url"] == "/stacks/s/web" and not r.get_json()["started"], r.get_json()
d = BASE / "web"
assert (d / "compose.yaml").read_text() == GOOD and (d / ".env").read_text() == "PUERTO=8080\n"
assert sorted(os.listdir(d)) == [".env", "compose.yaml", "stack.json"], os.listdir(d)
meta = json.loads((d / "stack.json").read_text())
assert meta["services"] == ["web"] and meta["public_ports"] and meta["created"] == meta["updated"]
assert writes and all(m == 0o600 and p.parent == d for p, m in writes), writes
assert (BASE, 0o700) in dirs and (d, 0o700) in dirs, dirs
assert not any(x[:2] == ["docker", "compose"] and "up" in x for x in calls)
assert post("/api/stacks", {"name": "web", "compose_text": GOOD}).status_code == 409
calls.clear()
r = post("/api/stacks", {"name": "db", "compose_text": GOOD, "start": True}).get_json()
assert r["ok"] and r["started"] and not r["start_error"], r
assert calls[-1][-3:] == ["up", "-d", "--remove-orphans"] and calls[-1][3] == "stack-db", calls[-1]
assert calls[-1][calls[-1].index("-f") + 1] == str(BASE / "db" / "compose.yaml")

# ── Lista ──
r = c.get("/stacks/api/stacks").get_json()
st = {s["name"]: s for s in r["stacks"]}
assert set(st) == {"db", "web"}
assert st["web"]["state"] == "partial" and st["web"]["running"] == 1 and st["web"]["total"] == 2, st["web"]
assert st["web"]["ports"] == ["8080→80/tcp"], st["web"]
assert st["db"]["state"] == "running" and st["db"]["ports"] == ["127.0.0.1:5432→5432/tcp"], st["db"]

# ── Detalle: ps como array y como líneas JSON ──
for mode in (False, True):
    PS_MODE["lines"] = mode
    r = c.get("/stacks/api/stacks/web").get_json()
    assert r["ok"] and r["compose_text"] == GOOD and r["env_text"] == "PUERTO=8080\n" and r["project"] == "stack-web"
    assert [x["service"] for x in r["containers"]] == ["web", "worker"], r["containers"]
    assert r["containers"][0]["ports"] == ["8080→80/tcp"] and r["containers"][0]["health"] == "healthy"
    assert r["containers"][1]["ports"] == [] and r["state"] == "partial" and (r["running"], r["total"]) == (1, 2), r
assert S.overall(["a"], []) == ("empty", 0, 1)
assert S.overall(["a"], [{"service": "a", "state": "running", "health": "unhealthy"}])[0] == "partial"
assert S.overall(["a"], [{"service": "a", "state": "exited"}])[0] == "stopped"
assert S.parse_ps("basura") == [] and S.parse_ps("") == []
assert c.get("/stacks/s/web").status_code == 200

# ── Editar ──
NEW = GOOD.replace("8080:80", "9090:80")
r = post("/api/stacks/web/save", {"compose_text": NEW, "env_text": "PUERTO=9090\n"}).get_json()
assert r["ok"] and (d / "compose.yaml").read_text() == NEW and (d / ".env").read_text() == "PUERTO=9090\n"
r = post("/api/stacks/web/save", {"compose_text": "INVALIDO"})
assert r.status_code == 422 and (d / "compose.yaml").read_text() == NEW and not (d / ".compose.yaml.new").exists()
r = post("/api/stacks/web/save", {"env_text": "PUERTO=1\n"}).get_json()  # solo el .env: conserva el compose
assert r["ok"] and (d / "compose.yaml").read_text() == NEW and (d / ".env").read_text() == "PUERTO=1\n"
r = post("/api/validate", {"name": "web", "compose_text": "privileged"}).get_json()
assert r["ok"] and r["warnings"] and (d / "compose.yaml").read_text() == NEW  # validar no guarda
assert post("/api/stacks/nope/save", {"compose_text": GOOD}).status_code == 404

# ── Acciones ──
for act, tail in (("start", ["up", "-d", "--remove-orphans"]), ("apply", ["up", "-d", "--remove-orphans"]), ("stop", ["stop"]),
                  ("restart", ["restart"]), ("down", ["down", "--remove-orphans"])):
    calls.clear()
    assert post("/api/stacks/web/action", {"action": act}).get_json()["ok"], act
    assert calls[0][-2:] == ["config", "-q"] and calls[-1][-len(tail):] == tail and len(calls) == 2, calls
calls.clear()
assert post("/api/stacks/web/action", {"action": "pull"}).get_json()["ok"]
assert [x[-1] for x in calls] == ["-q", "pull", "--remove-orphans"], calls
for bad in ("rm", "exec", "", None):
    assert post("/api/stacks/web/action", {"action": bad}).status_code == 400
S._busy.add("web")
assert post("/api/stacks/web/action", {"action": "start"}).status_code == 409
S._busy.discard("web")
(d / "compose.yaml").write_text("INVALIDO")  # no se arranca con un compose inválido
calls.clear()
r = post("/api/stacks/web/action", {"action": "start"})
assert r.status_code == 500 and "no es válido" in r.get_json()["error"] and len(calls) == 1
(d / "compose.yaml").write_text(NEW)

# ── Logs ──
calls.clear()
r = c.get("/stacks/api/stacks/web/logs?service=web&tail=100").get_json()
assert r["ok"] and "hola" in r["logs"] and calls[-1][-6:] == ["logs", "--no-color", "--tail", "100", "--", "web"], calls
c.get("/stacks/api/stacks/web/logs?tail=300")
assert calls[-1][-4:] == ["logs", "--no-color", "--tail", "300"], calls[-1]
for q in ("service=-f&tail=100", "service=a;b&tail=100", "service=web&tail=5", "service=../x&tail=100"):
    assert c.get("/stacks/api/stacks/web/logs?" + q).status_code == 400, q

# ── Eliminar ──
assert post("/api/stacks/web/delete", {"confirm": "otro"}).status_code == 400
for evil in ("..%2Fstacks", "..", "%2E%2E", "web%2F..", "..%5C..%5Cx"):
    assert post(f"/api/stacks/{evil}/delete", {"confirm": evil}).status_code in (404, 405), evil
(BASE / "falso").write_text("x")  # un archivo, no una carpeta
os.makedirs(BASE / "sincompose")    # carpeta sin compose.yaml
for n in ("falso", "sincompose"):
    assert post(f"/api/stacks/{n}/delete", {"confirm": n}).status_code == 404
assert (BASE / "falso").exists() and (BASE / "sincompose").exists()
outside = Path(ROOT + "/fuera"); os.makedirs(outside); (outside / "compose.yaml").write_text(GOOD)
try:
    os.symlink(outside, BASE / "enlace", target_is_directory=True)
    assert post("/api/stacks/enlace/delete", {"confirm": "enlace"}).status_code == 404
    assert (outside / "compose.yaml").exists()
    print("  (enlace simbólico probado)")
except OSError:
    # Sin permiso para enlaces simbólicos: una unión de Windows (lstat la ve como carpeta) prueba el realpath
    import subprocess
    subprocess.run(["cmd", "/c", "mklink", "/J", str(BASE / "enlace").replace("/", "\\"), str(outside).replace("/", "\\")],
                   check=True, capture_output=True)
    assert "enlace" in S.list_stacks()
    assert post("/api/stacks/enlace/delete", {"confirm": "enlace"}).status_code == 404
    assert c.get("/stacks/api/stacks/enlace").status_code == 404
    assert post("/api/validate", {"name": "enlace", "compose_text": GOOD}).status_code == 422
    assert sorted(os.listdir(outside)) == ["compose.yaml"]
    assert (outside / "compose.yaml").exists()
    os.rmdir(BASE / "enlace")
    print("  (unión de Windows probada en lugar de enlace simbólico)")
calls.clear()
r = post("/api/stacks/web/delete", {"confirm": "web", "volumes": True}).get_json()
assert r["ok"] and r["warnings"] == [] and not d.exists(), r
assert calls[-1][-3:] == ["down", "--remove-orphans", "-v"], calls
assert not any(n.startswith(".borrado") for n in os.listdir(BASE))
calls.clear()
assert post("/api/stacks/db/delete", {"confirm": "db"}).get_json()["ok"]
assert calls[-1][-2:] == ["down", "--remove-orphans"]
assert [s["name"] for s in c.get("/stacks/api/stacks").get_json()["stacks"]] == []

# ── Sin Compose ──
S.compose_base = lambda: None
assert c.get("/stacks/api/stacks").get_json()["compose"] is False
r = post("/api/validate", {"name": "nuevo", "compose_text": GOOD})
assert r.status_code == 422 and "no está instalado" in r.get_json()["error"] and not (BASE / "nuevo").exists()
print("stacks ok")
