"""Prueba: upload."""
import sys, types, os, io, shutil
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
app = create_app(); c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s: s["_user_id"] = "admin"
d = TMPD + "/uptest"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)

def up(files, **form):
    data = {"dir": d, **form, "files": [(io.BytesIO(b), name) for name, b in files]}
    return c.post("/files/api/upload", data=data, content_type="multipart/form-data")

r = up([("a.txt", b"1"), ("carpeta/sub/b.txt", b"2")])
assert r.status_code == 200 and os.path.exists(d + "/carpeta/sub/b.txt"), r.json
r = up([("a.txt", b"nuevo"), ("c.txt", b"3")])
assert r.status_code == 409 and r.json["conflicts"] == ["a.txt"] and not os.path.exists(d + "/c.txt"), r.json
r = up([("a.txt", b"nuevo"), ("c.txt", b"3")], overwrite="1")
assert r.status_code == 200 and open(d + "/a.txt").read() == "nuevo"
r = up([("../escape.txt", b"x")])
assert r.status_code == 400 and not os.path.exists(TMPD + "/escape.txt"), r.json
print("upload ok")
shutil.rmtree(d)
