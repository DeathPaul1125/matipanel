"""Prueba: aviso de actualizaciones de paquetes (correo una sola vez por versión, seguridad primero)."""
import sys, types, os, time
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_NOTIFY": TMPD + "/notify.json",
                   "MATIPANEL_NOTIFY_WORKER": "0", "MATIPANEL_UPDATES": TMPD + "/updates.json", "MATIPANEL_ROLES": TMPD + "/roles.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app, notify, updates
from app.modules import dashboard

cfg, err = notify.validate({"enabled": True, "to": "soporte@empresa.gob.gt", "smtp": {"host": "mail.local", "from": "p@empresa.gob.gt"}},
                           notify.DEFAULTS)
assert not err and cfg["events"]["updates"], err
notify.save(cfg)
SENT = []
notify.send_now = lambda c, subject, body, **kw: SENT.append((subject, body, kw))
refreshes = []
updates.refresh_lists = lambda: refreshes.append(1)
PKGS = [{"name": "nginx", "new_version": "1.26.3-4", "origin": "trixie", "security": False},
        {"name": "openssl", "new_version": "3.5.1-1+deb13u1", "origin": "trixie-security", "security": True}]
updates.summary = lambda: {"upgradable": len(PKGS), "security": sum(p["security"] for p in PKGS), "upgradable_list": list(PKGS)}


def wait_mail(n):
    for _ in range(50):
        if len(SENT) >= n:
            return
        time.sleep(0.05)


t0 = 1_000_000.0
updates.check(t0)
wait_mail(1)
assert len(SENT) == 1 and SENT[0][0] == "2 actualizaciones nuevas (1 de seguridad)", SENT
secs = SENT[0][2]["sections"]  # seguridad primero, en su propia tabla y en rojo
assert secs[0]["title"] == "De seguridad" and secs[0]["rows"][0][:3] == ("openssl", "3.5.1-1+deb13u1", "err")
assert secs[1]["rows"][0][0] == "nginx" and SENT[0][2]["level"] == "err" and refreshes == [1]
updates.check(t0 + 3600)  # lo mismo pendiente: no se repite el correo; la lista no se refresca antes de 12 h
time.sleep(0.3)
assert len(SENT) == 1 and refreshes == [1]
PKGS.append({"name": "curl", "new_version": "8.14-2", "origin": "trixie", "security": False})
updates.check(t0 + 7200)
wait_mail(2)
assert len(SENT) == 2 and SENT[1][0] == "Actualización nueva: curl", SENT
# se instalaron: lo avisado se olvida (una versión futura del mismo paquete volverá a avisarse)
PKGS.clear()
updates.check(t0 + 13 * 3600)
assert updates._load()["notified"] == [] and refreshes == [1, 1]
PKGS.append({"name": "openssl", "new_version": "3.5.2-1", "origin": "trixie-security", "security": True})
updates.check(t0 + 14 * 3600)
wait_mail(3)
assert SENT[2][0] == "Actualización nueva: openssl (seguridad)"

# barra de estado: aviso de seguridad, sin un segundo correo por la revisión de salud
app = create_app()
issues, _ = dashboard.compute_issues()
assert any(k == "updates:security" and "1 actualización de seguridad pendiente" == m for _, m, k in issues), issues
n = len(SENT)
notify.check_health([i for i in issues if i[2] == "updates:security"])
time.sleep(0.3)
assert len(SENT) == n
print("updates ok")
