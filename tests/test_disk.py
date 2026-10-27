"""Discos: análisis de espacio (du/find simulados), validación de rutas y limpiezas."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json",
                   "MATIPANEL_NOTIFY": TMPD + "/n.json", "MATIPANEL_NOTIFY_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import disk
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}

CALLS = []


def fake_run(cmd, timeout=30, **kw):
    CALLS.append(cmd)
    if "du" in cmd and "-xak" in cmd:
        return 1, "8\t/etc\n3145728\t/var\n1048576\t/home\n4194400\t/\n", "du: no se puede leer '/proc/x': Permiso denegado\n"
    if "find" in cmd:
        return 0, "209715200\t1730000000\t/var/lib/vm.img\0" "73400320\t1720000000\t/home/ana/iso.iso\0", ""
    return 0, "", ""


disk.run = fake_run
assert c.get("/disk/").status_code == 200
d = c.get("/disk/api/usage?path=/").get_json()
assert d["ok"] and d["total"] == 4194400 * 1024 and [e["name"] for e in d["entries"]] == ["var", "home", "etc"], d
assert d["warnings"] == 1 and d["age"] == 0
assert any(x in CALLS[0] for x in ("nice", "du")) and "-x" in " ".join(CALLS[0])  # sin cruzar a otras particiones
n = len(CALLS)
assert c.get("/disk/api/usage?path=/").get_json()["total"] == d["total"] and len(CALLS) == n  # desde la caché
c.get("/disk/api/usage?path=/&fresh=1")
assert len(CALLS) == n + 1
for bad in ("relativa", "/no/existe/nunca", "/a%00b", ""):
    assert c.get("/disk/api/usage?path=" + bad).status_code == 400, bad
b = c.get("/disk/api/bigfiles?path=/").get_json()
assert b["ok"] and [f["path"] for f in b["files"]] == ["/var/lib/vm.img", "/home/ana/iso.iso"]

# limpieza: lista, una desconocida se rechaza y las de comandos ejecutan el comando estándar
items = c.get("/disk/api/cleanup").get_json()
assert items["ok"] and isinstance(items["items"], list)
assert c.post("/disk/api/cleanup/rm-rf", headers=H).status_code == 404
r = c.post("/disk/api/cleanup/docker_build", headers=H).get_json()
assert r["ok"] and CALLS[-1] == ["docker", "builder", "prune", "-af"], CALLS[-1]
r = c.post("/disk/api/cleanup/journal", headers=H).get_json()
assert r["ok"] and CALLS[-1] == ["journalctl", "--vacuum-size=200M"]

# borrado de logs rotados: solo archivos normales que cumplen el patrón
logs = TMPD + "/log"
os.makedirs(logs + "/apache2", exist_ok=True)
for f in ("syslog", "syslog.1", "syslog.2.gz", "apache2/error.log", "apache2/error.log.3.gz", "Xorg.0.log"):
    open(f"{logs}/{f}", "w").write("x" * 4096)
disk._delete_files(logs, lambda n: bool(disk.ROTATED_RE.search(n)))
left = sorted(os.path.relpath(os.path.join(dp, f), logs).replace(os.sep, "/") for dp, _, fs in os.walk(logs) for f in fs)
assert left == ["Xorg.0.log", "apache2/error.log", "syslog"], left
print("disk ok")
