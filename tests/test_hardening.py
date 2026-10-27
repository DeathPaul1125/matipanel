"""Seguridad del servidor: lista de comprobación, correcciones SSH/APT/fail2ban con restauración y validaciones."""
import sys, types, os, shutil
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
ROOT = TMPD + "/hdtest"
shutil.rmtree(ROOT, ignore_errors=True)
for d in ("ssh", "f2b", "apt", "run", "etc", "home/admin/.ssh", "root/.ssh"):
    os.makedirs(f"{ROOT}/{d}", exist_ok=True)
os.environ.update({"MATIPANEL_PREFS": ROOT + "/p.json", "MATIPANEL_2FA_STORE": ROOT + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ROOT + "/audit.db", "MATIPANEL_NOTIFY_WORKER": "0",
                   "MATIPANEL_APPBACKUP_WORKER": "0"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import hardening as HD
from app.modules.hardening import hardening_bp

HASH = "$y$j9T$abcdefghijkl$SECRETHASHVALUE1234567890"
open(f"{ROOT}/etc/passwd", "w").write(
    "root:x:0:0:root:/tmp/hdtest/root:/bin/bash\ntoor:x:0:0::/root:/bin/sh\ndaemon:x:1:1::/usr/sbin:/usr/sbin/nologin\n"
    "admin:x:1000:1000:Admin:/tmp/hdtest/home/admin:/bin/bash\nguest:x:1001:1001::/home/guest:/bin/bash\n"
    "svc:x:998:998::/var/lib/svc:/usr/sbin/nologin\n")
open(f"{ROOT}/etc/shadow", "w").write(f"root:{HASH}:19000::::::\ntoor:!:19000::::::\ndaemon:*:19000::::::\n"
                                      f"admin:{HASH}:19000::::::\nguest::19000::::::\nsvc:!*:19000::::::\n")
open(f"{ROOT}/etc/group", "w").write("root:x:0:\nsudo:x:27:admin\nwheel:x:10:\n")
open(f"{ROOT}/home/admin/.ssh/authorized_keys", "w").write(
    "# comentario\nssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGabcdefghijklmnopqrstuvwxyz admin@pc\n\n")
open(f"{ROOT}/run/reboot-required", "w").write("*** System restart required ***\n")
open(f"{ROOT}/run/reboot-required.pkgs", "w").write("linux-image-amd64\nlibc6\nlinux-image-amd64\n")
HD.SSHD_DROPIN = f"{ROOT}/ssh/00-matipanel.conf"
HD.F2B_JAIL = f"{ROOT}/f2b/matipanel.conf"
HD.AUTO_UPGRADES = f"{ROOT}/apt/20auto-upgrades"
HD.UU_BIN = f"{ROOT}/unattended-upgrade"
HD.REBOOT_REQ, HD.REBOOT_PKGS = f"{ROOT}/run/reboot-required", f"{ROOT}/run/reboot-required.pkgs"
HD.PASSWD, HD.SHADOW, HD.GROUP = f"{ROOT}/etc/passwd", f"{ROOT}/etc/shadow", f"{ROOT}/etc/group"
HD.AUTH_LOGS = (f"{ROOT}/auth.log",)


def fake_detect():
    return {"family": "debian", "id": "debian", "name": "Debian 13", "version": "13", "pkg": "apt", "fw": "ufw"}
fake_detect.cache_clear = lambda: None
HD.detect = fake_detect

BASE = {"permitrootlogin": "yes", "passwordauthentication": "yes", "port": "22", "maxauthtries": "6", "x11forwarding": "yes"}
ST = {"calls": [], "sshd_t": 0, "f2b_t": 0, "aptcfg": 0, "f2b": True, "override": {}, "banned": ["198.51.100.9", "2001:db8::66"]}
SS = """tcp   LISTEN 0      128          0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=530,fd=3))
tcp   LISTEN 0      128             [::]:22            [::]:*    users:(("sshd",pid=530,fd=4))
tcp   LISTEN 0      511        127.0.0.1:5432       0.0.0.0:*    users:(("postgres",pid=700,fd=5))
tcp   LISTEN 0      511                *:8443             *:*    users:(("gunicorn",pid=900,fd=6),("gunicorn",pid=901,fd=6))
tcp   LISTEN 0      511    192.168.1.10:3306       0.0.0.0:*    users:(("mariadbd",pid=800,fd=7))
udp   UNCONN 0      0      127.0.0.53%lo:53         0.0.0.0:*    users:(("systemd-resolve",pid=400,fd=13))
udp   UNCONN 0      0            0.0.0.0:68         0.0.0.0:*
"""
APT = """Listing...
openssl/stable-security 3.5.1-1+deb13u1 amd64 [upgradable from: 3.5.0-1]
libssl3t64/stable-security 3.5.1-1+deb13u1 amd64 [upgradable from: 3.5.0-1]
curl/stable 8.14.1-3 amd64 [upgradable from: 8.14.1-2]
"""


def fake_run(cmd, timeout=30, input=None, env=None, cwd=None):
    cmd = list(cmd)
    ST["calls"].append(cmd)
    if cmd == ["sshd", "-T"]:
        if ST.get("sshd_T_fail"):
            return 255, "", "Missing privilege separation directory: /run/sshd"
        cfg = dict(BASE)
        if os.path.exists(HD.SSHD_DROPIN):
            for line in open(HD.SSHD_DROPIN):
                k, _, v = line.strip().partition(" ")
                if k and not k.startswith("#"):
                    cfg[k.lower()] = v
        cfg.update(ST["override"])
        return 0, "".join(f"{k} {v}\n" for k, v in cfg.items()) + "usepam yes\n", ""
    if cmd == ["sshd", "-t"]:
        return ST["sshd_t"], "", "/etc/ssh/sshd_config.d/00-matipanel.conf line 2: Bad configuration option" if ST["sshd_t"] else ""
    if cmd[:2] == ["systemctl", "reload"] or cmd[:2] == ["systemctl", "enable"]:
        return 0, "", ""
    if cmd == ["ss", "-tulpnH"]:
        return 0, SS, ""
    if cmd == ["ufw", "status"]:
        return 0, "Status: inactive\n", ""
    if cmd == ["apt", "list", "--upgradable"]:
        return 0, APT, ""
    if cmd == ["apt-config", "dump"]:
        txt = open(HD.AUTO_UPGRADES).read() if os.path.exists(HD.AUTO_UPGRADES) else ""
        return ST["aptcfg"], txt, "E: Syntax error" if ST["aptcfg"] else ""
    if cmd[0] == "apt-get":
        open(HD.UU_BIN, "w").close()
        return 0, "Setting up unattended-upgrades\n", ""
    if cmd[0] == "fail2ban-client":
        if not ST["f2b"]:
            return 127, "", "fail2ban-client no está instalado"
        a = [x for x in cmd[1:] if x != "--"]
        if a == ["--version"]:
            return 0, "Fail2Ban v1.1.0\n", ""
        if a == ["status"]:
            return 0, "Status\n|- Number of jail:\t2\n`- Jail list:\tsshd, recidive\n", ""
        if a[0] == "status":
            b = ST["banned"] if a[1] == "sshd" else []
            return 0, (f"Status for the jail: {a[1]}\n|- Filter\n|  |- Currently failed:\t3\n|  |- Total failed:\t40\n"
                       f"|  `- Journal matches:\t_SYSTEMD_UNIT=sshd.service + _COMM=sshd\n`- Actions\n"
                       f"   |- Currently banned:\t{len(b)}\n   |- Total banned:\t9\n   `- Banned IP list:\t{' '.join(b)}\n"), ""
        if a == ["-t"]:
            return ST["f2b_t"], "OK: configuration test is successful\n" if not ST["f2b_t"] else "", "ERROR: bad value" if ST["f2b_t"] else ""
        if a == ["reload"] or a[0] == "set":
            return 0, "1\n", ""
    if cmd[:1] == ["needs-restarting"]:
        return 127, "", ""
    return 127, "", f"{cmd[0]} no está instalado"


HD.run = fake_run
from app.modules import packages as PK
PK.run = fake_run  # apt_get() vive en packages
app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}
P = "/seguridad-servidor/api/"


def status():
    r = c.get(P + "status")
    assert r.status_code == 200, r.data
    assert b"SECRETHASH" not in r.data and b"$y$" not in r.data  # nunca se envían hashes
    return r.get_json()


assert c.get("/seguridad-servidor/").status_code == 200
d = status()
ck = {x["id"]: x for x in d["checks"]}
assert ck["ssh-root"]["status"] == "err" and ck["ssh-password"]["status"] == "warn" and ck["ssh-port"]["status"] == "info"
assert "admin" in ck["ssh-password"]["detail"], ck["ssh-password"]
assert ck["ssh-tries"]["status"] == "warn" and ck["ssh-x11"]["status"] == "warn"
assert ck["firewall"]["status"] == "err" and ck["firewall"]["link"] == "firewall"
assert ck["updates"]["status"] == "err" and "2 de seguridad" in ck["updates"]["detail"] and "openssl" in ck["updates"]["detail"]
assert ck["unattended"]["status"] == "warn" and ck["unattended"]["fix"] == "unattended"
assert ck["reboot"]["status"] == "warn" and "libc6, linux-image-amd64" in ck["reboot"]["detail"], ck["reboot"]
assert ck["uid0"]["status"] == "err" and "toor" in ck["uid0"]["detail"]
assert ck["emptypw"]["status"] == "err" and "guest" in ck["emptypw"]["detail"]
assert ck["sudo"]["detail"] == "admin"
assert ck["fail2ban"]["status"] == "ok"
assert 0 <= d["score"] < 60 and d["counts"]["err"] >= 4
ports = {(p["proto"], p["addr"], p["port"]): p for p in d["ports"]}
assert ports[("tcp", "0.0.0.0", 22)]["scope"] == "all" and ports[("tcp", "[::]", 22)]["scope"] == "all"
assert ports[("tcp", "127.0.0.1", 5432)]["scope"] == "local" and ports[("udp", "127.0.0.53%lo", 53)]["scope"] == "local"
assert ports[("tcp", "*", 8443)]["scope"] == "all" and ports[("tcp", "*", 8443)]["process"] == "gunicorn"
assert ports[("tcp", "192.168.1.10", 3306)]["scope"] == "ip" and ports[("udp", "0.0.0.0", 68)]["process"] == ""
acc = {a["user"]: a for a in d["accounts"]}
assert set(acc) == {"root", "toor", "admin", "guest"}, set(acc)
assert acc["admin"]["admin"] == "sudo" and acc["admin"]["keys"] == 1 and acc["admin"]["password"] == "set"
assert acc["guest"]["password"] == "empty" and acc["toor"]["password"] == "locked" and acc["root"]["keys"] == 0
assert d["ssh"]["admins_with_keys"] == ["admin"]
f = d["fail2ban"]
assert f["running"] and [j["name"] for j in f["jails"]] == ["sshd", "recidive"]
assert f["jails"][0]["banned"] == ["198.51.100.9", "2001:db8::66"] and f["jails"][0]["total_failed"] == "40"

# ── SSH ──
for bad in ({"settings": {"PermitRootLogin": "maybe"}}, {"settings": {"Port": "0"}}, {"settings": {"Port": "22;rm"}},
            {"settings": {"MaxAuthTries": "50"}}, {"settings": {"UsePAM": "no"}}, {"settings": {}}, {"settings": "x"},
            {"settings": {"X11Forwarding": "no\nPermitRootLogin yes"}}):
    r = c.post(P + "ssh", json=bad, headers=H)
    assert r.status_code == 400, (bad, r.data)
assert not os.path.exists(HD.SSHD_DROPIN)
r = c.post(P + "ssh", json={"settings": {"PasswordAuthentication": "no"}}, headers=H)
assert r.status_code == 400 and "confirma" in r.get_json()["error"]
r = c.post(P + "ssh", json={"settings": {"Port": "2222"}}, headers=H)
assert r.status_code == 400
# sin otro administrador, prohibir root exige confirmación
grp_txt = open(HD.GROUP).read()
open(HD.GROUP, "w").write("root:x:0:\nsudo:x:27:\n")
assert c.post(P + "ssh", json={"settings": {"PermitRootLogin": "no"}}, headers=H).status_code == 400
open(HD.GROUP, "w").write(grp_txt)
ST["calls"].clear()
r = c.post(P + "ssh", json={"settings": {"PermitRootLogin": "no", "X11Forwarding": "no", "MaxAuthTries": "4"}}, headers=H)
assert r.get_json() == {"ok": True}, r.data
txt = open(HD.SSHD_DROPIN).read()
assert txt.startswith("# Gestionado por MatiPanel") and "PermitRootLogin no\nMaxAuthTries 4\nX11Forwarding no\n" in txt, txt
assert ["sshd", "-t"] in ST["calls"] and ["systemctl", "reload", "ssh"] in ST["calls"]
assert ST["calls"].index(["sshd", "-t"]) < ST["calls"].index(["systemctl", "reload", "ssh"])
r = c.post(P + "ssh", json={"settings": {"PasswordAuthentication": "no"}, "confirm": True}, headers=H)
assert r.get_json()["ok"]
txt2 = open(HD.SSHD_DROPIN).read()
assert "PermitRootLogin no\nPasswordAuthentication no\nMaxAuthTries 4\nX11Forwarding no\nKbdInteractiveAuthentication no\n" in txt2, txt2
# sshd -t falla → se restaura el contenido anterior y no se recarga
ST["sshd_t"] = 1; ST["calls"].clear()
r = c.post(P + "ssh", json={"settings": {"PasswordAuthentication": "yes", "Port": "2200"}, "confirm": True}, headers=H)
assert r.status_code == 422 and "Bad configuration option" in r.get_json()["error"], r.data
assert open(HD.SSHD_DROPIN).read() == txt2
assert not any(x[:2] == ["systemctl", "reload"] for x in ST["calls"])
os.unlink(HD.SSHD_DROPIN)
r = c.post(P + "ssh", json={"settings": {"X11Forwarding": "no"}}, headers=H)
assert r.status_code == 422 and not os.path.exists(HD.SSHD_DROPIN)  # no existía: se elimina
ST["sshd_t"] = 0
# otro archivo de sshd_config.d gana: se guarda pero se avisa
ST["override"] = {"passwordauthentication": "yes"}
r = c.post(P + "ssh", json={"settings": {"PasswordAuthentication": "no"}, "confirm": True}, headers=H).get_json()
assert r["ok"] and "PasswordAuthentication" in r["warning"], r
ST["override"] = {}
d = status()
assert {x["id"]: x for x in d["checks"]}["ssh-password"]["status"] == "ok"

# sshd -T falla: el análisis sigue y SSH sale en rojo con el motivo
ST["sshd_T_fail"] = True
d = status()
assert d["ssh"] is None and {x["id"]: x for x in d["checks"]}["ssh"]["status"] == "err" and "privilege separation" in d["checks"][0]["detail"]
assert c.post(P + "ssh", json={"settings": {"X11Forwarding": "no"}}, headers=H).status_code == 500
ST["sshd_T_fail"] = False

# ── Actualizaciones automáticas ──
ST["aptcfg"] = 1; ST["calls"].clear()
open(HD.AUTO_UPGRADES, "w").write('APT::Periodic::Unattended-Upgrade "0";\n')
r = c.post(P + "unattended", json={}, headers=H)
assert r.status_code == 422 and open(HD.AUTO_UPGRADES).read() == 'APT::Periodic::Unattended-Upgrade "0";\n', (r.status_code, r.data)
assert any(x[0] == "apt-get" and x[-1] == "unattended-upgrades" for x in ST["calls"])
ST["aptcfg"] = 0
assert c.post(P + "unattended", json={}, headers=H).get_json()["ok"]
assert 'APT::Periodic::Unattended-Upgrade "1";' in open(HD.AUTO_UPGRADES).read()
assert {x["id"]: x for x in status()["checks"]}["unattended"]["status"] == "ok"

# ── fail2ban: jail del panel ──
GOOD = {"bantime": "1h", "findtime": "10m", "maxretry": "5", "ignoreip": "192.168.1.0/24 10.0.0.5"}
for bad in ({**GOOD, "bantime": "abc"}, {**GOOD, "bantime": "1h\nenabled=false"}, {**GOOD, "findtime": "-1"},
            {**GOOD, "maxretry": "0"}, {**GOOD, "maxretry": "x"}, {**GOOD, "ignoreip": "1.2.3.4;rm"}, {**GOOD, "bantime": "0"}):
    assert c.post(P + "f2b/jail", json=bad, headers=H).status_code == 400, bad
assert not os.path.exists(HD.F2B_JAIL)
ST["calls"].clear()
assert c.post(P + "f2b/jail", json=GOOD, headers=H).get_json()["ok"]
jt = open(HD.F2B_JAIL).read()
assert "[sshd]\nenabled = true\nport = 22\nbantime = 1h\nfindtime = 10m\nmaxretry = 5\n" in jt, jt
assert "ignoreip = 127.0.0.1/8 ::1 192.168.1.0/24 10.0.0.5\n" in jt and "backend = systemd" in jt
assert ["fail2ban-client", "-t"] in ST["calls"] and ["fail2ban-client", "reload"] in ST["calls"]
ST["f2b_t"] = 1
r = c.post(P + "f2b/jail", json={**GOOD, "maxretry": "3"}, headers=H)
assert r.status_code == 422 and open(HD.F2B_JAIL).read() == jt
ST["f2b_t"] = 0
assert status()["fail2ban"]["managed"]["maxretry"] == "5"

# ── fail2ban: desbloquear / bloquear ──
for bad in ({"jail": "sshd", "ip": "1.2.3"}, {"jail": "sshd", "ip": "1.2.3.4; rm -rf /"}, {"jail": "ss;h", "ip": "1.2.3.4"},
            {"jail": "-x", "ip": "1.2.3.4"}, {"jail": "sshd", "ip": "--help"}):
    assert c.post(P + "f2b/unban", json=bad, headers=H).status_code == 400, bad
assert c.post(P + "f2b/unban", json={"jail": "nginx", "ip": "1.2.3.4"}, headers=H).status_code == 404
ST["calls"].clear()
assert c.post(P + "f2b/unban", json={"jail": "sshd", "ip": " 2001:DB8::66 "}, headers=H).get_json()["ok"]
assert ["fail2ban-client", "--", "set", "sshd", "unbanip", "2001:db8::66"] in ST["calls"]
assert c.post(P + "f2b/ban", json={"jail": "sshd", "ip": "127.0.0.1"}, headers=H).status_code == 400
r = c.post(P + "f2b/ban", json={"jail": "sshd", "ip": "10.0.0.5"}, headers=H, environ_base={"REMOTE_ADDR": "10.0.0.5"})
assert r.status_code == 400 and "propia IP" in r.get_json()["error"]
assert c.post(P + "f2b/ban", json={"jail": "recidive", "ip": "203.0.113.7"}, headers=H).get_json()["ok"]
assert ["fail2ban-client", "--", "set", "recidive", "banip", "203.0.113.7"] in ST["calls"]
# sin CSRF (Origin) no pasa
assert c.post(P + "f2b/ban", json={"jail": "sshd", "ip": "203.0.113.7"}).status_code in (400, 403)

# ── fail2ban no instalado ──
ST["f2b"] = False
d = status()
assert not d["fail2ban"]["installed"] and {x["id"]: x for x in d["checks"]}["fail2ban"]["fix"] == "f2b-install"
assert c.post(P + "f2b/unban", json={"jail": "sshd", "ip": "1.2.3.4"}, headers=H).status_code == 409
ST["calls"].clear()
assert c.post(P + "f2b/install", json={}, headers=H).get_json()["ok"]
assert any(x[0] == "apt-get" and x[-1] == "fail2ban" for x in ST["calls"])

# ── enlaces simbólicos: nunca se leen ni se siguen ──
try:
    os.unlink(HD.F2B_JAIL)
    os.symlink(HD.SHADOW, HD.F2B_JAIL)
    linked = True
except OSError:
    linked = False  # Windows sin privilegio para crear symlinks
_islink = os.path.islink
if not linked:  # simula el enlace: islink() devuelve True para el archivo de la jail
    open(HD.F2B_JAIL, "w").write("[sshd]\n")
    os.path.islink = lambda p: os.path.normpath(p) == os.path.normpath(HD.F2B_JAIL) or _islink(p)
ST["f2b"] = True
r = c.post(P + "f2b/jail", json=GOOD, headers=H)
assert r.status_code == 409 and "enlace" in r.get_json()["error"], r.data
assert HASH in open(HD.SHADOW).read()
os.path.islink = _islink
print("hardening ok" + ("" if linked else " (enlace simbólico simulado)"))
