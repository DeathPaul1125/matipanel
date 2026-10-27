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

import email
from email import policy


def parsed():
    return [email.message_from_string(m["data"], policy=policy.default) for m in MAILS]


def wait(n):
    for _ in range(80):
        if len(MAILS) >= n:
            return
        time.sleep(0.05)


def find(text):
    return next((m for m in parsed() if text in m["Subject"]), None)


def html_of(m):
    return m.get_body(("html",)).get_content()


# guardar la configuración es un «cambio delicado»: también se avisa
wait(1)
chg = find("cambió la configuración de notificaciones")
assert chg and "🟠" in chg["Subject"] and "Auditoría" in html_of(chg), [m["Subject"] for m in parsed()]

# correo de prueba (síncrono): texto + HTML, con enlace al panel
r = c.post("/notificaciones/api/test", headers=H).get_json()
assert r["ok"], r
t = find("Correo de prueba")
assert t and t["To"] == "soporte@empresa.gob.gt" and t.is_multipart()
assert t.get_body(("plain",)).get_content().startswith("Si recibes este correo")
h = html_of(t)
assert "<table" in h and "Correo de prueba" in h and 'href="http://localhost/notificaciones/"' in h and "Matías" in h

# despliegue fallido → correo por la cola, con el error como registro y botón a la app
deployer.find_dep = lambda name, dep_id: {"sha": "af26625834bb", "message": "Arreglo", "trigger": "manual"}
deployer._notify_deploy("quetzal", {"id": "1793", "trigger": "manual"}, "failed", "Las migraciones fallaron <x>")
deployer._notify_deploy("quetzal", {"id": "1794", "trigger": "manual"}, "success", "")  # deploy_ok desactivado
# bloqueo de login: 5 intentos fallidos desde la misma IP
c2 = app.test_client()
for i in range(5):
    c2.post("/login", data={"username": "intruso", "password": "mala"}, headers=H)
wait(4)
d = find("Falló el despliegue")
assert d and "🔴" in d["Subject"] and "Las migraciones fallaron &lt;x&gt;" in html_of(d)
assert 'href="http://localhost/despliegues/app/quetzal"' in html_of(d) and "af26625 Arreglo" in html_of(d)
b = find("Bloqueado el acceso desde 127.0.0.1")
assert b and "intruso" in html_of(b)
assert not find("desplegada")
assert len(MAILS) == 4, [m["Subject"] for m in parsed()]

# entrar desde una IP nueva (la primera fue 127.0.0.1)
c3 = app.test_client()
assert c3.post("/login", data={"username": "admin", "password": "clave"}, headers=H,
               environ_base={"REMOTE_ADDR": "10.9.9.9"}).status_code == 302
wait(5)
n = find("admin entró al panel desde una IP nueva")
assert n and "10.9.9.9" in html_of(n)

# vista previa de cada tipo de aviso (HTML aislado) y ejemplo enviado a pedido
for kind in notify.EVENTS:
    pv = c.get(f"/notificaciones/api/preview/{kind}").get_json()
    assert pv["ok"] and "<table" in pv["html"] and "<script" not in pv["html"], kind
assert c.get("/notificaciones/api/preview/nada").status_code == 404
r = c.post("/notificaciones/api/test", json={"kind": "backup_fail"}, headers=H).get_json()
assert r["ok"] and find("Falló el respaldo de «quetzal» (ejemplo)")
hist = c.get("/notificaciones/api/config").get_json()["history"]
assert len(hist) == 6 and all(x["ok"] for x in hist), hist
assert c.get("/notificaciones/").status_code == 200
print("notify ok")
