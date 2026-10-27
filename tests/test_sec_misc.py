"""Pruebas de las correcciones en respaldos, usuarios, almacenamiento, procesos y servicios (Windows)."""
import sys, types, os, tempfile, stat
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ["MATIPANEL_PREFS"] = TMPD + "/mpprefs.json"
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
SYS = types.SimpleNamespace(pw_name="www-data", pw_uid=33, pw_gid=33, pw_dir="/var/www", pw_shell="/usr/sbin/nologin", pw_gecos="")
pwd = types.ModuleType("pwd")
pwd.getpwnam = lambda n: {"admin": U, "www-data": SYS}[n]
pwd.getpwall = lambda: [U, SYS]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import backups as B, users as Us, storage as S, cron as C
app = create_app(); c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s: s["_user_id"] = "admin"

# ── Respaldos: "$" ya no acepta salto de línea final ──
assert B.valid_name("web") and not B.valid_name("web\n") and not B.NAME_RE.match("web\n")
assert not B.FILE_RE.match("web-20240101-000000.tar.gz\n")
assert B.TIME_RE.match("02:30") and not B.TIME_RE.match("02:30\n")
r = c.post("/backups/api/jobs/save", json={"name": "web\n", "paths": [TMPD], "keep": 3,
                                          "schedule": {"type": "daily", "time": "02:30"}})
assert r.status_code == 400, r.json
# Destino de restauración: _target_problem (en Windows st_uid=0; se simula el lstat)
real_lstat = os.lstat
def fake(owner_uid, mode, gid=0):
    return lambda p: types.SimpleNamespace(st_uid=owner_uid, st_gid=gid, st_mode=stat.S_IFDIR | mode)
os.lstat = fake(0, 0o755)
assert B._target_problem(TMPD) is None
os.lstat = fake(1000, 0o755)
assert "otro usuario" in B._target_problem(TMPD)
os.lstat = fake(0, 0o1777)
assert "escribir" in B._target_problem(TMPD)        # el propio destino no puede ser /tmp-like
os.lstat = fake(0, 0o775, gid=33)
assert "escribir" in B._target_problem(TMPD)
os.lstat = real_lstat
print("backups ok")

# ── Usuarios ──
assert not Us.NEW_NAME_RE.match("bob\n") and not Us.NAME_RE.match("bob\n") and Us.NAME_RE.match("samba$")
r = c.post("/users/api/user/delete", json={"username": "www-data", "remove_home": True})
assert r.status_code == 403 and "sistema" in r.json["error"], r.json
print("users ok")

# ── Almacenamiento ──
assert not S.LVM_NAME_RE.match("vg0\n") and S.LVM_NAME_RE.match("vg0")
assert not S.MD_NEW_RE.match("md0\n") and S.MD_NEW_RE.match("md0")
os.lstat = fake(1000, 0o755)
assert "otro usuario" in S._mountpoint_owner_problem(TMPD + "/vol")
os.lstat = fake(0, 0o1777)
assert "escribir" in S._mountpoint_owner_problem(TMPD + "/vol")
os.lstat = lambda p: types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFLNK | 0o777)
assert "enlace" in S._mountpoint_owner_problem(TMPD + "/vol")
os.lstat = fake(0, 0o755)
assert S._mountpoint_owner_problem(TMPD + "/vol") is None
os.lstat = real_lstat
print("storage ok")

# ── Procesos: PID 0 y el maestro de gunicorn ──
r = c.post("/processes/api/kill/0")
assert r.status_code == 400, r.status_code
r = c.post(f"/processes/api/kill/{os.getppid()}")
assert r.status_code == 400
print("processes ok")

# ── Cron ──
assert [e["type"] for e in C.parse_crontab("# a\x0b* * * * * x\n")] == ["other"]
print("misc security ok")
