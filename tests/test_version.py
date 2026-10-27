"""Prueba: versión del panel, historial de actualizaciones y «Acerca de»."""
import sys, types, os, json
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json",
                   "MATIPANEL_NOTIFY": TMPD + "/n.json", "MATIPANEL_VERSION_HISTORY": TMPD + "/version_history.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app, __version__, version, audit

# Arranque anterior con otra versión: el nuevo arranque cuenta como actualización
json.dump([{"version": "0.4.0", "commit": "abc1234", "at": 1.0, "previous": ""}], open(TMPD + "/version_history.json", "w"))
version.BUILD_PATH = version.Path(TMPD + "/BUILD.json")
json.dump({"version": __version__, "commit": "d20a4a7", "branch": "main", "installed_at": "2026-10-26T21:00:00-06:00"},
          open(TMPD + "/BUILD.json", "w"))
app = create_app()
h = version.history()
assert [x["version"] for x in h] == ["0.4.0", __version__] and h[-1]["previous"] == "0.4.0" and h[-1]["commit"] == "d20a4a7"
assert any(e["module"] == "panel" and e["action"] == f"actualizado a {__version__}" for e in audit.query())
create_app()  # mismo arranque: no se repite
assert len(version.history()) == 2
assert version.changes(__version__), "el CHANGELOG debe tener la sección de la versión actual"
assert version.changelog_sections()[0][0] == __version__ and version.changelog_sections()[0][1]  # con fecha

c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
html = c.get("/acerca").get_data(as_text=True)
assert f"v{__version__}" in html and "d20a4a7" in html and "Historial de actualizaciones" in html and "Novedades" in html
assert "2026-10-26T21:00:00-06:00" in html  # fecha de instalación (el navegador la formatea)
print("version ok")
