"""Terminal dentro de contenedores: lista validada, permisos y el comando docker exec que se ejecuta."""
import sys, types, os, subprocess
TMPD = os.path.realpath(os.environ.get("MP_TMP", ".")).replace(os.sep, "/")  # carpeta temporal de esta prueba
sys.path.insert(0, sys.argv[1])
os.environ.update({"MATIPANEL_PREFS": TMPD + "/p.json", "MATIPANEL_2FA_STORE": TMPD + "/2fa.json", "MATIPANEL_METRICS": ":memory:",
                   "MATIPANEL_DEPLOY_WORKER": "0", "MATIPANEL_AUDIT": ":memory:", "MATIPANEL_ROLES": TMPD + "/roles.json"})
U = types.SimpleNamespace(pw_name="admin", pw_uid=1000, pw_gid=1000, pw_dir="/home/admin", pw_shell="/bin/bash", pw_gecos="")
pwd = types.ModuleType("pwd"); pwd.getpwnam = lambda n: U; pwd.getpwall = lambda: [U]; pwd.getpwuid = lambda u: U
G = types.SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=["admin", "pepe", "luis"])
grp = types.ModuleType("grp"); grp.getgrall = lambda: [G]; grp.getgrgid = lambda g: G; grp.getgrnam = lambda n: G
sys.modules.update({"pwd": pwd, "grp": grp})
for n in ("pty", "termios", "fcntl", "pam"): sys.modules[n] = types.ModuleType(n)
from app import create_app, roles
from app.modules import terminal as T
app = create_app()

PS = ("matipanel-erpmayoreo-app_blue-1\tmatipanel-erpmayoreo\tmatipanel/erpmayoreo:ccf627a\n"
      "matipanel-erpmayoreo-db-1\tmatipanel-erpmayoreo\tmariadb:11\n"
      "portainer\t\tportainer/portainer-ce\n"
      "-x;rm\tmal\tx\n")
T.shutil.which = lambda n: "/usr/bin/docker"
T.subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, PS, "")


def client(user):
    c = app.test_client()
    with c.session_transaction() as ss:
        ss["_user_id"] = user; ss["_fresh"] = True
    return c


c = client("admin")
page = c.get("/terminal/").get_data(as_text=True)
assert 'id="tw-target"' in page and "Este servidor (admin)" in page
d = c.get("/terminal/api/containers").get_json()
names = [x["name"] for x in d["containers"]]
assert names == ["matipanel-erpmayoreo-app_blue-1", "matipanel-erpmayoreo-db-1", "portainer"], names  # «-x;rm» descartado
assert d["containers"][0]["project"] == "matipanel-erpmayoreo"
assert "consola" in c.get("/docker/").get_data(as_text=True)

# permisos: solo lectura no tiene Terminal; con Terminal pero sin Docker no ve contenedores
roles.save({"users": {"admin": {"role": "admin"}, "pepe": {"role": "readonly"},
                      "luis": {"role": "custom", "modules": ["terminal"]}}})
assert client("pepe").get("/terminal/api/containers").status_code in (302, 403)
assert not roles.allowed("pepe", "container_ws", "GET", "/terminal/ws/container")[0]
assert roles.allowed("luis", "container_ws", "GET", "/terminal/ws/container")[0]  # la ruta es de Terminal…
assert client("luis").get("/terminal/api/containers").get_json()["containers"] == []  # …pero sin Docker, nada
assert not T._may_use_docker("luis") and T._may_use_docker("admin")

# el comando: docker exec con TTY dentro del contenedor validado; root solo si se pide
calls = []
T._close_inherited_fds = lambda: None
T.os.execvp = lambda prog, argv: calls.append(argv)
T._exec_container("matipanel-erpmayoreo-app_blue-1", False)
T._exec_container("portainer", True)
a, b = calls
assert a[:3] == ["docker", "exec", "-it"] and "-u" not in a and a[-4:-2] == ["matipanel-erpmayoreo-app_blue-1", "sh"]
assert b[b.index("-u") + 1] == "0" and b[-4] == "portainer" and "exec bash" in b[-1] and "exec sh -i" in b[-1]
assert T.CONTAINER_RE.fullmatch("matipanel-erp_1.app") and not T.CONTAINER_RE.fullmatch("-it") and not T.CONTAINER_RE.fullmatch("a b")
print("container_term ok")
