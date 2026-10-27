"""Prueba: fm."""
import sys, types, os, io, shutil, tarfile
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ["MATIPANEL_PREFS"] = TMPD + "/mpprefs.json"
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
app = create_app(); c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s: s["_user_id"] = "admin"
d = TMPD + "/fmtest"; shutil.rmtree(d, ignore_errors=True)
os.makedirs(d + "/sub/deep"); open(d + "/a.txt", "w").write("hola"); open(d + "/sub/deep/Reporte.TXT", "w").write("x")
open(d + "/foto.png", "wb").write(b"\x89PNG\r\n\x1a\n"); open(d + "/x.svg", "w").write("<svg onload=alert(1)>")

r = c.get(f"/files/api/ls?path={d}").json
assert r["disk"] and r["disk"]["total"] > 0, r
r = c.get(f"/files/api/search?path={d}&q=reporte").json
assert [x["name"] for x in r["results"]] == ["Reporte.TXT"], r
assert c.get(f"/files/api/search?path={d}&q=a").status_code == 400
r = c.post("/files/api/compress", json={"paths": [d + "/a.txt", d + "/sub"], "name": "paquete", "format": "tar.gz"})
assert r.status_code == 200 and os.path.exists(d + "/paquete.tar.gz"), r.json
names = tarfile.open(d + "/paquete.tar.gz").getnames()
assert "a.txt" in names and "sub/deep/Reporte.TXT" in names, names
assert c.post("/files/api/compress", json={"paths": [d + "/a.txt", TMPD + "/otro"], "name": "x"}).status_code == 400
assert c.post("/files/api/compress", json={"paths": [d + "/a.txt"], "name": "../malo"}).status_code == 400
r = c.get(f"/files/api/download?path={d}/a.txt&path={d}/sub")
assert r.status_code == 200 and r.mimetype == "application/gzip", r.status_code
assert c.get(f"/files/api/download?path={d}/a.txt&path={os.path.abspath(__file__)}").status_code == 400
r = c.get(f"/files/api/raw?path={d}/foto.png")
assert r.status_code == 200 and r.mimetype == "image/png" and r.headers["X-Content-Type-Options"] == "nosniff"
assert c.get(f"/files/api/raw?path={d}/x.svg").status_code == 400     # SVG nunca en línea (XSS)
assert c.get(f"/files/api/raw?path={d}/a.txt").status_code == 400
assert c.get("/files/").status_code == 200
shutil.rmtree(d, ignore_errors=True)
print("files ok")
