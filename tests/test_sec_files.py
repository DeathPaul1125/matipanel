"""Pruebas de las correcciones de seguridad del administrador de archivos (Windows, sin symlinks)."""
import sys, types, os, io, shutil, tarfile, zipfile, json, subprocess
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
from app.modules import files as F
app = create_app(); c = app.test_client(); c.environ_base['HTTP_ORIGIN'] = 'http://localhost'
with c.session_transaction() as s: s["_user_id"] = "admin"
d = TMPD + "/fmsec"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)


def mktar(name, entries):
    """entries: (name, type, data/linkname, mode)"""
    with tarfile.open(os.path.join(d, name), "w:gz") as t:
        for n, typ, data, mode in entries:
            ti = tarfile.TarInfo(n); ti.mode = mode
            if typ == "f":
                ti.size = len(data); t.addfile(ti, io.BytesIO(data))
            else:
                ti.type = {"d": tarfile.DIRTYPE, "l": tarfile.SYMTYPE, "h": tarfile.LNKTYPE, "c": tarfile.CHRTYPE,
                           "p": tarfile.FIFOTYPE}[typ]
                if typ in "lh": ti.linkname = data
                t.addfile(ti)
    return f"{d}/{name}"


def extract(p):
    return c.post("/files/api/extract", json={"path": p})


# ── Extracción: comprimidos maliciosos rechazados ──
bad = {
    "abs.tar.gz": [("/etc/evil", "f", b"x", 0o644)],
    "dotdot.tar.gz": [("a/../../evil", "f", b"x", 0o644)],
    "symabs.tar.gz": [("l", "l", "/etc", 0o777), ("l/passwd", "f", b"x", 0o644)],
    "symup.tar.gz": [("l", "l", "../..", 0o777)],
    "chain.tar.gz": [("l1", "l", ".", 0o777), ("l2", "l", "l1/..", 0o777)],
    "hard.tar.gz": [("h", "h", "../../etc/shadow", 0o644)],
    "suid.tar.gz": [("sh", "f", b"\x7fELF", 0o4755)],
    "dev.tar.gz": [("null", "c", b"", 0o666)],
    "fifo.tar.gz": [("p", "p", b"", 0o666)],
}
for name, entries in bad.items():
    r = extract(mktar(name, entries))
    assert r.status_code == 500 and "rechazado" in r.json["error"], (name, r.json)
    assert not os.path.exists(d + "/evil"), name
print("tar maliciosos rechazados")

# Comprimido legítimo (con symlink interno relativo y entrada ./ con modo 777) sí se extrae
good = mktar("good.tar.gz", [(".", "d", b"", 0o777), ("site", "d", b"", 0o755),
                             ("site/index.html", "f", b"<h1>hola</h1>", 0o644)])
code, e = F._extract(good, d, True)  # por ruta con '/': el tar de Git no acepta 'C:\' (en Linux da igual)
assert code == 0 and open(d + "/site/index.html").read() == "<h1>hola</h1>", e
assert F._check_archive(mktar("rel.tar.gz", [("a", "d", b"", 0o755), ("a/b", "l", "../site", 0o777),
                                             ("a/c", "l", "b/index.html", 0o777)]), d) is None
print("tar legítimo extraído")

# Dañado
open(d + "/roto.tar.gz", "wb").write(b"no soy un tar")
r = extract(d + "/roto.tar.gz")
assert r.status_code == 500 and "dañado" in r.json["error"], r.json

# zip
def mkzip(name, entries):
    p = f"{d}/{name}"
    with zipfile.ZipFile(p, "w") as z:
        for n, data, link in entries:
            zi = zipfile.ZipInfo(n)
            if link:
                zi.create_system = 3; zi.external_attr = (0o120777 << 16)
            z.writestr(zi, data)
    return p
assert "'..'" in F._check_archive(mkzip("slip.zip", [("../evil.txt", b"x", False)]), d)
assert "absoluta" in F._check_archive(mkzip("abs.zip", [("/etc/evil", b"x", False)]), d)
assert "'..'" in F._check_archive(mkzip("bs.zip", [("..\\evil.txt", b"x", False)]), d)
assert "fuera" in F._check_archive(mkzip("lnk.zip", [("l", b"/etc", True)]), d)
assert F._check_archive(mkzip("ok.zip", [("z/a.txt", b"x", False), ("z/l", b"a.txt", True)]), d) is None
code, e = F._extract(mkzip("ok2.zip", [("zz/a.txt", b"zip", False)]), d, True)
assert code == 0 and open(d + "/zz/a.txt").read() == "zip", e
print("zip ok")

# Subida con extracción: un comprimido malicioso se guarda pero no se extrae
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as t:
    ti = tarfile.TarInfo("../../fuera.txt"); ti.size = 1; t.addfile(ti, io.BytesIO(b"x"))
r = c.post("/files/api/upload", data={"dir": d, "extract": "keep", "files": [(io.BytesIO(buf.getvalue()), "m.tar.gz")]},
           content_type="multipart/form-data")
assert r.status_code == 500 and "rechazado" in r.json["error"] and r.json["saved"] == ["m.tar.gz"], r.json
print("subida + extracción maliciosa rechazada")

# ── Editor: versión ──
open(d + "/conf.txt", "w").write("uno")
r = c.get(f"/files/api/read?path={d}/conf.txt").json
v = r["version"]
r = c.post("/files/api/write", json={"path": d + "/conf.txt", "content": "dos", "version": v})
assert r.status_code == 200 and open(d + "/conf.txt").read() == "dos" and open(d + "/conf.txt.matipanel.bak").read() == "uno", r.json
v2 = r.json["version"]
assert v2 != v
r = c.post("/files/api/write", json={"path": d + "/conf.txt", "content": "tres", "version": v})  # versión vieja
assert r.status_code == 409 and open(d + "/conf.txt").read() == "dos", r.json
r = c.post("/files/api/write", json={"path": d + "/conf.txt", "content": "tres", "version": v2})
assert r.status_code == 200 and open(d + "/conf.txt").read() == "tres"
assert c.post("/files/api/write", json={"path": d + "/conf.txt", "content": ["x"]}).status_code == 400
assert not [f for f in os.listdir(d) if f.startswith(".matipanel.")], os.listdir(d)
print("editor ok")

# Lectura: tope aunque el tamaño mienta
big = d + "/big.txt"; open(big, "wb").write(b"a" * (F.MAX_EDIT_BYTES + 10))
assert c.get(f"/files/api/read?path={big}").status_code == 413

# ── Copiar carpeta dentro de sí misma ──
os.makedirs(d + "/arbol/sub")
r = c.post("/files/api/copy", json={"path": d + "/arbol", "dest": d + "/arbol/sub"})
assert r.status_code == 400 and "sí misma" in r.json["error"], r.json
r = c.post("/files/api/copy", json={"path": d + "/conf.txt", "dest": "conf2.txt"})
assert r.status_code == 200 and open(d + "/conf2.txt").read() == "tres", r.json
assert c.post("/files/api/copy", json={"path": d + "/conf.txt", "dest": "conf2.txt"}).status_code == 400
assert c.post("/files/api/copy", json={"path": d + "/conf.txt", "dest": ["x"]}).status_code == 400
print("copia ok")

# ── Descarga: nombres con comillas / saltos / UTF-8 ──
cd = F._disposition('raro"\r\nñ.tar.gz')
assert "\n" not in cd and '"raro___' in cd and "filename*=UTF-8''raro%22%0D%0A%C3%B1.tar.gz" in cd, cd
os.makedirs(d + "/carpetañ")
r = c.get(f"/files/api/download?path={d}/carpetañ")
assert r.status_code == 200 and "filename*=UTF-8''carpeta%C3%B1.tar.gz" in r.headers["Content-Disposition"], r.headers
r.close()
r = c.get(f"/files/api/download?path={d}/conf.txt")
assert r.status_code == 200 and r.data == b"tres" and "conf.txt" in r.headers["Content-Disposition"]
r.close()
print("descarga ok")

# ── Comprimir: 0600 y sin dejar restos al fallar ──
r = c.post("/files/api/compress", json={"paths": [d + "/conf.txt"], "name": "c1", "format": "tar.gz"})
assert r.status_code == 200 and tarfile.open(d + "/c1.tar.gz").getnames() == ["conf.txt"], r.json
assert c.post("/files/api/compress", json={"paths": "C:/", "name": "x"}).status_code == 400

# ── Rutas: '//etc' cuenta como protegida, tipos raros ──
assert F._path("//etc") in ("/etc", os.path.abspath("//etc")) and F._path(["x"]) is None and F._path(None) is None

# ── Búsqueda: límite dentro de una carpeta enorme ──
os.makedirs(d + "/muchos")
for i in range(700):
    open(f"{d}/muchos/match{i}.txt", "w").close()
r = c.get(f"/files/api/search?path={d}/muchos&q=match").json
assert len(r["results"]) == 300 and r["truncated"], len(r["results"])

# ── Listado: tope de entradas ──
F.MAX_LS = 100
r = c.get(f"/files/api/ls?path={d}/muchos").json
assert len(r["items"]) == 100 and r["total"] == 700, (len(r["items"]), r["total"])
F.MAX_LS = 20_000

# ── chmod sobre symlink: rechazado (simulado) ──
orig = os.path.islink
os.path.islink = lambda p: p.endswith("enlace") or orig(p)
open(d + "/enlace", "w").close()
r = c.post("/files/api/chmod", json={"path": d + "/enlace", "mode": "644"})
os.path.islink = orig
assert r.status_code == 400 and "enlace" in r.json["error"], r.json

# ── Subida: carpeta intermedia que existe como archivo ──
open(d + "/noesdir", "w").close()
r = c.post("/files/api/upload", data={"dir": d, "files": [(io.BytesIO(b"x"), "noesdir/a.txt")]},
           content_type="multipart/form-data")
assert r.status_code == 400 and "no es una carpeta" in r.json["error"], r.json
r = c.post("/files/api/upload", data={"dir": d, "overwrite": "1", "files": [(io.BytesIO(b"nuevo"), "conf.txt")]},
           content_type="multipart/form-data")
assert r.status_code == 200 and open(d + "/conf.txt").read() == "nuevo"
assert not [f for f in os.listdir(d) if f.startswith(".matipanel-up-")]
shutil.rmtree(d, ignore_errors=True)
print("files security ok")
