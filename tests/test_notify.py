"""Notificaciones: guardar config, correo de prueba, bloqueo de login y despliegue fallido, contra un SMTP falso."""
import sys, types, os, socketserver, threading, time
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
for f in (TMPD + "/nt_cfg.json",):
    if os.path.exists(f):
        os.unlink(f)
os.environ.update({"MATIPANEL_PREFS": TMPD + "/ntprefs.json", "MATIPANEL_2FA_STORE": TMPD + "/nt2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_NOTIFY": TMPD + "/nt_cfg.json",
                   "MATIPANEL_NOTIFY_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl"): sys.modules[n] = types.ModuleType(n)
pam = types.ModuleType("pam"); pam.pam = lambda: types.SimpleNamespace(authenticate=lambda u, p, service: p == "clave")
sys.modules["pam"] = pam

MAILS = []


class SMTPHandler(socketserver.StreamRequestHandler):
    """SMTP mínimo: suficiente para smtplib (EHLO, MAIL, RCPT, DATA, QUIT)."""
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())  # noqa: E731
        w("220 fake ESMTP")
        rcpt, data = [], None
        while True:
            line = self.rfile.readline().decode(errors="replace")
            if not line:
                return
            cmd = line.strip().upper()
            if data is not None:
                if line in (".\r\n", ".\n"):
                    MAILS.append({"to": rcpt, "data": "".join(data)})
                    data, rcpt = None, []
                    w("250 OK")
                else:
                    data.append(line)
            elif cmd.startswith("EHLO"):
                w("250-fake"); w("250 SIZE 1000000")
            elif cmd.startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                if cmd.startswith("RCPT"):
                    rcpt.append(line.split(":", 1)[1].strip(" <>\r\n"))
                w("250 OK")
            elif cmd == "DATA":
                data = []
                w("354 go")
            elif cmd == "QUIT":
                w("221 bye")
                return
            else:
                w("502 no")


srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPHandler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

from app import create_app, notify, deployer
app = create_app()
c = app.test_client()
H = {"Origin": "http://localhost"}
assert c.post("/login", data={"username": "admin", "password": "clave"}, headers=H).status_code == 302

# activar sin datos completos → error
r = c.post("/notificaciones/api/config", json={"enabled": True, "smtp": {"host": ""}}, headers=H)
assert r.status_code == 400
cfg = {"enabled": True, "to": "soporte@empresa.gob.gt", "repeat_minutes": 0,
       "events": {"deploy_fail": True, "login_block": True},
       "smtp": {"host": "127.0.0.1", "port": port, "security": "none", "from": "panel@empresa.gob.gt", "user": "", "password": "x"}}
r = c.post("/notificaciones/api/config", json=cfg, headers=H).get_json()
assert r["ok"] and r["config"]["smtp"]["has_password"] and "password" not in r["config"]["smtp"]
g = c.get("/notificaciones/api/config").get_json()
assert "password" not in str(g["config"]) or g["config"]["smtp"].get("has_password")

# correo de prueba (síncrono)
r = c.post("/notificaciones/api/test", headers=H).get_json()
assert r["ok"], r
assert MAILS and MAILS[-1]["to"] == ["soporte@empresa.gob.gt"] and "Correo de prueba" in MAILS[-1]["data"]

# despliegue fallido → correo por la cola
deployer.find_dep = lambda name, dep_id: {"sha": "af26625834bb", "message": "Arreglo", "trigger": "manual"}
deployer._notify_deploy("quetzal", {"id": "1793", "trigger": "manual"}, "failed", "Las migraciones fallaron")
deployer._notify_deploy("quetzal", {"id": "1794", "trigger": "manual"}, "success", "")  # deploy_ok desactivado
# bloqueo de login: 5 intentos fallidos desde la misma IP
c2 = app.test_client()
for i in range(5):
    c2.post("/login", data={"username": "intruso", "password": "mala"}, headers=H)
for _ in range(50):
    if len(MAILS) >= 3:
        break
    time.sleep(0.1)
import email
from email.header import decode_header, make_header
subjects = [str(make_header(decode_header(email.message_from_string(m["data"])["Subject"]))) for m in MAILS]
assert any("Falló el despliegue" in s or "despliegue" in s.lower() for s in subjects), subjects
assert any("Bloqueado el acceso desde 127.0.0.1" in s for s in subjects), subjects
assert not any("desplegada" in s for s in subjects), subjects
assert len(MAILS) == 3, subjects
hist = c.get("/notificaciones/api/config").get_json()["history"]
assert len(hist) == 3 and all(h["ok"] for h in hist)
assert c.get("/notificaciones/").status_code == 200
print("notify ok")
