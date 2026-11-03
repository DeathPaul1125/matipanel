"""Respaldo de la configuración del propio panel: «servidor remoto» = bash local."""
import sys, types, os, shutil, json, io, tarfile, time
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")
sys.path.insert(0, sys.argv[1])
ROOT = TMPD + "/pbtest"
shutil.rmtree(ROOT, ignore_errors=True)
DATA, ETC = ROOT + "/data", ROOT + "/etc"
for d in (DATA + "/apps/erp", DATA + "/stacks/web/html", DATA + "/captures", ETC):
    os.makedirs(d)
os.environ.update({"MATIPANEL_APPBACKUP_DIR": DATA, "MATIPANEL_DATA_DIR": DATA, "MATIPANEL_ETC_DIR": ETC,
                   "MATIPANEL_APPS": DATA + "/apps", "MATIPANEL_NOTIFY": DATA + "/notify.json", "MATIPANEL_NO_RESTART": "1"})
for n in ("pwd", "grp", "pty", "termios", "fcntl", "pam"):
    sys.modules[n] = types.ModuleType(n)
from app import appbackup as B


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


put(DATA + "/2fa.json", '{"admin": "SECRETO"}')
put(DATA + "/notify.json", '{"smtp": {"password": "x"}}')
put(DATA + "/roles.json", '{"roles": {}}')
put(DATA + "/.matipanel_secret", "clave-sesion")
put(DATA + "/metrics.db", "historial")            # historial: no va
put(DATA + "/notify_state.json", "{}")            # estado: no va
put(DATA + "/apps/erp/env", "APP_KEY=x")          # apps: tienen sus propios respaldos
put(DATA + "/captures/a.pcap", "pcap")
put(DATA + "/stacks/web/compose.yaml", "services: {}")
put(DATA + "/stacks/web/.env", "A=1")
put(DATA + "/stacks/web/html/index.html", "<h1>")  # datos del stack: no va
put(DATA + "/.smb-abcd.cred", "password=x")
put(ETC + "/cert.pem", "CERT")
put(ETC + "/key.pem", "KEY")
put(B.KEY_PATH, "fake")  # ready() necesita la clave

B.ssh = lambda dest, cmd: ["bash", "-c", cmd]
REMOTE = ROOT + "/remote/respaldos"
here = {"id": "aaaa1111", "name": "Respaldos", "type": "ssh", "host": "10.0.0.9", "port": 22, "user": "r", "path": REMOTE}
B.update(lambda c: (c.__setitem__("dests", [here]), c.__setitem__("panel", {**B.DEFAULT_PANEL, "keep": 2})))

# 1. Qué entra: solo la lista blanca
names = sorted(a for a, _ in B.panel_sources())
assert names == sorted(["data/2fa.json", "data/notify.json", "data/roles.json", "data/.matipanel_secret", "data/appbackup.json",
                        "data/stacks/web/compose.yaml", "data/stacks/web/.env", "etc/cert.pem", "etc/key.pem"]), names

# 2. Respaldo, lista y retención (conservar 2)
r1 = B.panel_backup("manual")
assert r1["ok"] and r1["files"] == 9, r1
host = B.panel_host()
loc = f"{REMOTE}/_panel/{host}/{r1['name']}"
assert sorted(os.listdir(loc)) == ["manifest.json", "panel.tar.gz"]
m = json.load(open(loc + "/manifest.json"))
assert m["panel"] == host and "data/2fa.json" in m["items"] and m["files"]["panel.tar.gz"]["size"] > 0
assert B.load()["state"][B.PANEL]["last_ok"] and not B._run_lock.locked()
time.sleep(1.1); B.panel_backup()
time.sleep(1.1); r3 = B.panel_backup()
lst = B.list_panel(B.load()["dests"][0])
assert len(lst) == 2 and lst[0]["name"] == r3["name"] and lst[0]["host"] == host, lst
os.makedirs(f"{REMOTE}/_panel/otro-servidor/20260101-010101")  # respaldos de otro panel también aparecen
assert {b["host"] for b in B.list_panel(B.load()["dests"][0])} == {host, "otro-servidor"}
assert B.remote_apps(B.load()["dests"][0]) == []  # _panel no es una app

# 3. Restaurar: vuelve lo respaldado, no toca lo que no es configuración, conserva los servidores de aquí
put(DATA + "/2fa.json", '{"admin": "CAMBIADO"}')
os.remove(ETC + "/key.pem")
put(DATA + "/metrics.db", "historial nuevo")
other = {"id": "bbbb2222", "name": "Nuevo", "type": "ssh", "host": "10.0.0.50", "port": 22, "user": "r", "path": "/srv/r"}
B.update(lambda c: c["dests"].append(other))
res = B.panel_restore("aaaa1111", host, r3["name"])
assert res["ok"] and res["files"] == 9, res
assert open(DATA + "/2fa.json").read() == '{"admin": "SECRETO"}' and open(ETC + "/key.pem").read() == "KEY"
assert open(DATA + "/metrics.db").read() == "historial nuevo"
assert [d["id"] for d in B.load()["dests"]] == ["aaaa1111", "bbbb2222"]  # el servidor agregado aquí no se pierde
assert not B._run_lock.locked()

# 4. Respaldo alterado: la huella del manifest no coincide → no se escribe nada
with open(f"{REMOTE}/_panel/{host}/{r3['name']}/panel.tar.gz", "ab") as f:
    f.write(b"x")
put(DATA + "/2fa.json", "{}")
try:
    B.panel_restore("aaaa1111", host, r3["name"])
    raise AssertionError("debía rechazar el archivo alterado")
except B.Fail as e:
    assert "huella" in str(e), e
assert open(DATA + "/2fa.json").read() == "{}" and not B._run_lock.locked()

# 5. Tar manipulado: rutas, enlaces y nombres desconocidos no salen del archivo
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    for nm, kind in (("data/2fa.json", tarfile.REGTYPE), ("data/../../etc/cron.d/x", tarfile.REGTYPE), ("/etc/passwd", tarfile.REGTYPE),
                     ("data/roles.json", tarfile.SYMTYPE), ("data/stacks/web/html/x.php", tarfile.REGTYPE), ("data/metrics.db", tarfile.REGTYPE)):
        ti = tarfile.TarInfo(nm)
        ti.type = kind
        if kind == tarfile.SYMTYPE:
            ti.linkname = "/etc/shadow"
            tf.addfile(ti)
        else:
            ti.size = 2
            tf.addfile(ti, io.BytesIO(b"{}"))
assert B.read_panel_archive(buf.getvalue()) == {"data/2fa.json": b"{}"}
for bad in ("../x", "a/../../b", "data/stacks/.."):
    assert B.panel_member(bad) is None, bad
try:
    B.panel_restore("aaaa1111", "../etc", r3["name"])
    raise AssertionError("debía rechazar el host")
except B.Fail:
    pass

# 6. No escribe a través de un enlace simbólico (si el sistema permite crearlos)
try:
    os.makedirs(ROOT + "/fuera")
    shutil.rmtree(DATA + "/stacks/web")
    os.symlink(ROOT + "/fuera", DATA + "/stacks/web", target_is_directory=True)
except (OSError, NotImplementedError):
    pass
else:
    try:
        B._safe_write(B.PANEL_DIR, "stacks/web/.env", b"A=2")
        raise AssertionError("debía negarse a seguir el enlace")
    except B.Fail:
        pass
    assert os.listdir(ROOT + "/fuera") == []
print("panel_backup ok")
