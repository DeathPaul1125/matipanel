"""Respaldos de apps de punta a punta: «servidor remoto» = bash local, docker falso."""
import sys, types, os, shutil, json, hashlib, gzip, time, subprocess
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
ROOT = TMPD + "/abtest"
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT + "/panel"); os.makedirs(ROOT + "/apps/quetzal/deployments"); os.makedirs(ROOT + "/vol/sub")
os.environ.update({"MATIPANEL_APPBACKUP_DIR": ROOT + "/panel", "MATIPANEL_APPS": ROOT + "/apps", "MATIPANEL_NOTIFY": ROOT + "/n.json",
                   "FAKE_DOCKER_LOG": ROOT + "/docker.log"})
for n in ("pwd", "grp", "pty", "termios", "fcntl", "pam"):
    sys.modules[n] = types.ModuleType(n)
from app import appbackup as B, deployer as D

D.save_config("quetzal", {"name": "quetzal", "kind": "php", "database": "mariadb", "db_name": "laravel", "persist": ["assets/uploads"],
                          "active_slot": "blue", "slot_base": 20000, "port": 8081, "draft": False, "current": {"sha": "af26625"}})
D.write_env("quetzal", "DB_HOST=db\n")
D.save_secrets("quetzal", {"db_password": "x"})
open(ROOT + "/vol/foto.jpg", "wb").write(os.urandom(50000))
open(ROOT + "/vol/sub/nota.txt", "w").write("hola")
open(B.KEY_PATH, "w").write("fake")  # configured() necesita la clave

# «Servidor remoto»: los comandos remotos los ejecuta bash local; docker y los volúmenes, falsos
B.ssh = lambda dest, cmd: ["bash", "-c", cmd]
real_popen = B._popen
B._popen = lambda cmd, **kw: real_popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_docker.py"), *cmd[1:]] if cmd[0] == "docker" else cmd, **kw)
B.volume_mountpoint = lambda vol, create=False: ROOT + "/vol"
calls = []
real_run = D._run
D._run = lambda cmd, **kw: (calls.append(cmd), (0, "", ""))[1] if cmd[0] == "docker" else real_run(cmd, **kw)
REMOTE = TMPD + "/abtest/remote/matipanel"
B.update(lambda c: c.__setitem__("dest", {"host": "10.0.0.9", "port": 22, "user": "respaldos", "path": REMOTE}))
B.update(lambda c: c["apps"].__setitem__("quetzal", {"enabled": True, "schedule": "daily", "time": "02:00", "keep": 2}))

r = B.backup("quetzal", "manual")
assert r["ok"] and r["size"] > 0, r
loc = ROOT + "/remote/matipanel/quetzal/" + r["name"]
files = sorted(os.listdir(loc))
assert files == ["config.tar.gz", "db.sql.gz", "manifest.json", "vol-assets_uploads_" + files[3].split("_")[-1].split(".")[0] + ".tar.gz"] or \
    set(files) >= {"config.tar.gz", "db.sql.gz", "manifest.json"}, files
m = json.load(open(loc + "/manifest.json"))
for fname, meta in m["files"].items():
    data = open(f"{loc}/{fname}", "rb").read()
    assert len(data) == meta["size"] and hashlib.sha256(data).hexdigest() == meta["sha256"], fname
assert gzip.decompress(open(loc + "/db.sql.gz", "rb").read()).startswith(b"-- dump de prueba")
st = B.load()["state"]["quetzal"]
assert st["last_ok"] and st["last_name"] == r["name"]
assert [b["name"] for b in B.list_remote(B.load()["dests"][0], "quetzal")] == [r["name"]]
assert B.manifest(B.load()["dests"][0], "quetzal", r["name"])["db_name"] == "laravel"

# conservar 2: tras dos respaldos más quedan solo los dos últimos; no hay .partial colgados
time.sleep(1.1); r2 = B.backup("quetzal")
time.sleep(1.1); r3 = B.backup("quetzal")
names = [b["name"] for b in B.list_remote(B.load()["dests"][0], "quetzal")]
assert names == [r3["name"], r2["name"]], names
assert not [d for d in os.listdir(ROOT + "/remote/matipanel/quetzal") if d.endswith(".partial")]

# restauración: base y volumen
shutil.rmtree(ROOT + "/vol"); os.makedirs(ROOT + "/vol"); open(ROOT + "/vol/basura.txt", "w").write("x")
B.restore("quetzal", r3["name"], ["db", "volumes"])
assert sorted(os.listdir(ROOT + "/vol")) == ["foto.jpg", "sub"], os.listdir(ROOT + "/vol")
assert open(ROOT + "/vol/sub/nota.txt").read() == "hola"
log = open(ROOT + "/docker.log", "rb").read()
assert log.startswith(b"RESTORE:") and b"-- dump de prueba" in log
stops = [c for c in calls if "stop" in c]; ups = [c for c in calls if "up" in c]
assert stops and ups, calls  # la app se detuvo y se volvió a levantar

# un fallo deja estado, limpia la carpeta parcial y libera el candado
B.volume_mountpoint = lambda vol, create=False: ROOT + "/no-existe"
try:
    B.backup("quetzal")
    raise AssertionError("debía fallar")
except B.Fail as e:
    assert "tar" in str(e).lower() or "falló" in str(e), e
assert B.load()["state"]["quetzal"]["last_ok"] is False and not B._run_lock.locked()
assert not [d for d in os.listdir(ROOT + "/remote/matipanel/quetzal") if d.endswith(".partial")]
B.delete_remote(B.load()["dests"][0], "quetzal", r2["name"])
assert [b["name"] for b in B.list_remote(B.load()["dests"][0], "quetzal")] == [r3["name"]]
assert B.load()["dests"][0]["type"] == "ssh"  # el formato anterior («dest») se migró solo

# Windows / SMB: el mismo motor sobre el recurso «montado» (aquí, una carpeta local)
mounts = []
B.MOUNT_ROOT = ROOT + "/smb"
B._mount = lambda dest, mp: (mounts.append("mount"), os.makedirs(mp, exist_ok=True))
B._umount = lambda mp: mounts.append("umount")
B._local = lambda cmd: ["bash", "-c", cmd]
B.volume_mountpoint = lambda vol, create=False: ROOT + "/vol"
w, err = B.validate_dest({"type": "smb", "name": "Windows", "host": "srv-win01", "share": "Respaldos", "path": "MatiPanel",
                          "user": r"SENABED\svc-respaldos", "password": "x"})
assert not err, err
B.update(lambda c: c["dests"].append(w))
B.update(lambda c: c["apps"]["quetzal"].__setitem__("dest", w["id"]))
assert B.test(w)["free"] is not None
r4 = B.backup("quetzal")
smb_dir = f"{ROOT}/smb/{w['id']}/MatiPanel/quetzal/{r4['name']}"
assert os.path.isfile(smb_dir + "/manifest.json") and os.path.isfile(smb_dir + "/db.sql.gz"), os.listdir(os.path.dirname(smb_dir))
assert mounts.count("mount") == mounts.count("umount") >= 2 and not B._mounts, mounts  # siempre se desmonta
assert B.load()["state"]["quetzal"]["dest_name"] == "Windows"
assert [b["name"] for b in B.list_remote(w, "quetzal")] == [r4["name"]]
assert [b["name"] for b in B.list_remote(B.load()["dests"][0], "quetzal")] == [r3["name"]]  # el otro servidor sigue intacto
shutil.rmtree(ROOT + "/vol"); os.makedirs(ROOT + "/vol")
B.restore("quetzal", r4["name"], ["volumes"], w["id"])
assert open(ROOT + "/vol/sub/nota.txt").read() == "hola"
assert mounts.count("mount") == mounts.count("umount") and not B._mounts
print("appbackup ok")
