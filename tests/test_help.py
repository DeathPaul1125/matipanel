"""Prueba: help."""
import sys, types, os, re
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
from app.modules import help as h
app = create_app(); c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s: s["_user_id"] = "admin"
slugs = h._slugs(); bad = []
for slug in slugs:
    r = c.get("/ayuda/" + slug)
    if r.status_code != 200: bad.append((slug, r.status_code)); continue
    html = r.data.decode()
    if ".md\"" in html or ".md#" in html: bad.append((slug, "enlace .md sin convertir"))
    for link in set(re.findall(r'href="/ayuda/(\d{2}-[a-z0-9-]+)', html)):
        if link not in slugs: bad.append((slug, "enlace roto -> " + link))
idx = c.get("/ayuda/buscar.json").json
print(len(slugs), "páginas,", len(idx), "secciones indexadas")
print("pendientes/rotos:", sorted(set(b[1] for b in bad if isinstance(b[1], str))))
print("errores HTTP:", [b for b in bad if not isinstance(b[1], str)])
assert not any("&amp;" in i["text"] or "<code>" in i["text"] for i in idx)
assert c.get("/ayuda/../etc").status_code == 404 and c.get("/ayuda/99-nada").status_code == 404
print("ayuda ok")
