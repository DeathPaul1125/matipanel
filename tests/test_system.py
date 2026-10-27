"""Prueba: Sistema (sesiones, intentos fallidos de SSH, apagado programado)."""
import sys, types, os
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app
from app.modules import system as S

last = """senatest pts/0        192.100.10.189   Mon Oct 26 14:01:02 2026 - Mon Oct 26 15:00:01 2026  (00:58)
senatest pts/1        192.100.10.189   Mon Oct 26 19:55:10 2026   still logged in
root     tty1                          Sun Oct 25 09:00:00 2026 - crash                      (1+02:00)
reboot   system boot  6.12.57+deb13-amd64 Thu Oct 23 11:40:10 2026   still running

wtmp begins Thu Oct  1 10:00:00 2026"""
rows = S.parse_last(last)
assert [r["user"] for r in rows] == ["senatest", "senatest", "root", "reboot"]
assert rows[0]["duration"] == "00:58" and rows[1]["status"] == "still logged in" and rows[2]["end"] == "crash"
assert rows[3]["boot"] and rows[3]["kernel"] == "6.12.57+deb13-amd64" and rows[2]["from"] == ""
w = S.parse_who("senatest pts/0        2026-10-26 19:55   .          1234 (192.100.10.189)")
assert w == [{"user": "senatest", "tty": "pts/0", "since": "2026-10-26 19:55", "idle": ".", "pid": "1234", "from": "192.100.10.189"}]

calls = []
JOURNAL = "\n".join(["2026-10-26T10:00:01-06:00 srv sshd-session[1]: Failed password for invalid user admin from 45.1.2.3 port 1 ssh2"] * 3
                    + ["2026-10-26T11:00:00-06:00 srv sshd[2]: Invalid user test from 45.1.2.3 port 2",
                       "2026-10-26T12:00:00-06:00 srv sshd[3]: Failed publickey for root from 10.0.0.9 port 3 ssh2"])


def fake_run(cmd, timeout=30, **kw):
    calls.append(cmd)
    if cmd[0] == "journalctl":
        return 0, JOURNAL, ""
    if cmd[0] == "shutdown":
        return 0, "", ""
    return 1, "", "no simulado"


S.run = fake_run
f = S.ssh_failures()
assert f["total"] == 5 and f["ips"][0] == {"ip": "45.1.2.3", "count": 4, "users": ["admin", "test"], "last": "2026-10-26T11:00:00-06:00"}

app = create_app()
c = app.test_client()
with c.session_transaction() as ss:
    ss["_user_id"] = "admin"; ss["_fresh"] = True
H = {"Origin": "http://localhost"}
host = S.socket.gethostname()
ok = lambda body: c.post("/system/api/power/schedule", json=body, headers=H)  # noqa: E731
assert ok({"action": "reboot", "when": "30", "confirm": host, "message": "Mantenimiento"}).get_json()["ok"]
assert calls[-1] == ["shutdown", "-r", "+30", "Mantenimiento"]
assert ok({"action": "poweroff", "when": "23:15", "confirm": host}).get_json()["ok"] and calls[-1] == ["shutdown", "-P", "23:15"]
for bad in ({"action": "halt", "when": "5", "confirm": host}, {"action": "reboot", "when": "5", "confirm": "otro"},
            {"action": "reboot", "when": "now; id", "confirm": host}, {"action": "reboot", "when": "25:00", "confirm": host},
            {"action": "reboot", "when": "0", "confirm": host}, {"action": "reboot", "when": "5", "confirm": host, "message": "x" * 201}):
    assert ok(bad).status_code == 400, bad
assert c.post("/system/api/power/cancel", headers=H).get_json()["ok"] and calls[-1] == ["shutdown", "-c"]
assert c.get("/system/").status_code == 200
print("system ok")
