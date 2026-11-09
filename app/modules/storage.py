"""Almacenamiento avanzado: LVM, RAID por software (mdadm) y cuotas de disco.

Todas las rutas de dispositivos que llegan del navegador se validan contra la salida
actual de lsblk / pvs / lvs / mdstat: nunca se acepta una ruta arbitraria.
"""
import json
import os
import re
import shutil
import stat
import threading
import time
from functools import wraps
from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from ..distro import detect
from ..modules.packages import apt_get
from ..util import run

try:
    import grp
    import pwd
except ImportError:  # Windows: solo para ejecutar las pruebas de los parsers
    grp = pwd = None

storage_bp = Blueprint("storage", __name__)

FSTAB = "/etc/fstab"
MDADM_CONF = "/etc/mdadm/mdadm.conf"
ENV = {**os.environ, "LC_ALL": "C", "LVM_SUPPRESS_FD_WARNINGS": "1"}
LSBLK_COLS = "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL,ROTA,PTTYPE,UUID"
INSTALLABLE = {"lvm2", "mdadm", "quota", "xfsprogs", "cloud-guest-utils"}

# \Z en vez de $: "$" acepta un salto de línea final ("vg\n"), que acabaría en /etc/fstab
LVM_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}\Z")
SIZE_RE = re.compile(r"^\+?\s*(\d{1,7}(?:\.\d{1,3})?)\s*([MGT])(?:i?B)?$", re.I)
PCT_RE = re.compile(r"^\+?\s*(\d{1,3})\s*%(?:\s*(?:FREE|libre))?$", re.I)
QSIZE_RE = re.compile(r"^(\d{1,13}(?:\.\d{1,3})?)\s*([KMGT]?)(?:i?B)?$", re.I)
INODES_RE = re.compile(r"^\d{1,12}$")
MP_RE = re.compile(r"^(/[A-Za-z0-9_.@+-]+)+\Z")
MD_NEW_RE = re.compile(r"^md(\d{1,3})\Z")
UUID_RE = re.compile(r"^[0-9A-Fa-f-]{8,64}\Z")

FREE_TYPES = {"disk", "part", "raid0", "raid1", "raid4", "raid5", "raid6", "raid10", "linear"}
RAID_MEMBER_TYPES = {"disk", "part"}
SKIP_PREFIXES = ("zram", "sr", "fd", "loop", "ram", "nbd")
RESIZABLE_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs"}
QUOTA_FS = {"ext2", "ext3", "ext4", "xfs"}
QUOTA_OPTS = {"usrquota", "grpquota", "quota", "uquota", "gquota", "usrjquota", "grpjquota",
              "uqnoenforce", "gqnoenforce", "prjquota", "pquota", "pqnoenforce"}
RAID_MIN = {"0": 2, "1": 2, "5": 3, "6": 4, "10": 4}
QMULT = {"": 1, "K": 1, "M": 1024, "G": 1024 ** 2, "T": 1024 ** 3}

# Carpetas donde nunca se monta un LV nuevo (exactas) y árboles completos prohibidos
PROTECTED_MP = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/media",
                "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/var"}
PROTECTED_TREES = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc", "/run",
                   "/sbin", "/sys", "/usr")

_busy = threading.Lock()


def _err(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def _data():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _msg(out, err, fallback="El comando falló"):
    text = (err or "").strip() or (out or "").strip() or fallback
    return text[-3000:]


def _cmd(cmd, timeout=60, input=""):
    """Ejecuta sin shell, stdin vacío (las preguntas [y/n] se responden 'no') y LC_ALL=C."""
    return run(cmd, timeout=timeout, input=input, env=ENV)


def exclusive(fn):
    """Una sola operación de almacenamiento a la vez (gevent, 1 worker)."""
    @wraps(fn)
    def wrapper(*a, **kw):
        if not _busy.acquire(blocking=False):
            return _err("Otra operación de almacenamiento está en curso; espera a que termine.", 409)
        try:
            return fn(*a, **kw)
        finally:
            _busy.release()
    return wrapper


def _can_install():
    return detect()["pkg"] == "apt"


# ── Parsers puros (probados al final del archivo) ─────────────────────

def _int(v):
    try:
        return int(float(str(v or "0").strip().rstrip("Bb") or 0))
    except ValueError:
        return 0


def _bool(v):
    return v is True or str(v).lower() in ("1", "true")


def parse_lsblk(data):
    """Aplana el árbol JSON de lsblk. Retorna lista ordenada (recorrido en profundidad) de nodos."""
    nodes, order = {}, []

    def walk(node, parent, depth):
        path = node.get("path") or "/dev/" + str(node.get("name", ""))
        mounts = [m for m in (node.get("mountpoints") or []) if m]
        if not mounts and node.get("mountpoint"):
            mounts = [node["mountpoint"]]
        n = nodes.get(path)
        if n is None:
            n = nodes[path] = {
                "path": path, "name": str(node.get("name", "")), "size": _int(node.get("size")),
                "type": node.get("type") or "", "fstype": node.get("fstype") or "",
                "mounts": mounts, "model": (node.get("model") or "").strip(),
                "serial": (node.get("serial") or "").strip(), "rota": _bool(node.get("rota")),
                "pttype": node.get("pttype") or "", "uuid": node.get("uuid") or "",
                "children": [], "parents": [], "depth": depth,
            }
            order.append(path)
        if parent and parent not in n["parents"]:
            n["parents"].append(parent)
        for k in node.get("children") or []:
            kp = k.get("path") or "/dev/" + str(k.get("name", ""))
            if kp not in n["children"]:
                n["children"].append(kp)
            walk(k, path, depth + 1)

    for top in (data or {}).get("blockdevices") or []:
        walk(top, None, 0)

    def has_root(path, seen=()):
        n = nodes[path]
        return "/" in n["mounts"] or any(has_root(c, seen + (path,)) for c in n["children"] if c not in seen)

    result = []
    for path in order:
        n = nodes[path]
        n["has_root"] = has_root(path)
        n["free"] = is_free(n)
        n["status"] = _dev_status(n)
        result.append(n)
    return result


def is_free(n):
    """Sin particiones/hijos, sin sistema de archivos ni firma, sin montar y no es del sistema."""
    if n["type"] not in FREE_TYPES or n["name"].startswith(SKIP_PREFIXES) or n["size"] <= 0:
        return False
    if n["children"] or n["fstype"] or n["mounts"] or n.get("has_root"):
        return False
    return not (n["type"] == "disk" and n["pttype"])


def _dev_status(n):
    if n["free"]:
        return "libre"
    if n.get("has_root") and n["type"] in ("disk", "part"):
        return "sistema"
    if "[SWAP]" in n["mounts"]:
        return "swap"
    if n["mounts"]:
        return "montado"
    fs = n["fstype"]
    if fs == "LVM2_member":
        return "PV de LVM"
    if fs == "linux_raid_member":
        return "miembro RAID"
    if n["children"]:
        return "en uso"
    if fs:
        return "con datos"
    if n["type"] == "disk" and n["pttype"]:
        return "tabla de particiones vacía"
    return "no disponible"


def lvm_rows(data, key):
    rows = []
    for rep in (data or {}).get("report") or []:
        rows.extend(rep.get(key) or [])
    return rows


def parse_lv_size(spec, grow=False):
    """'10G' → ['-L','10g'] · '50%' → ['-l','50%FREE']; con grow agrega '+'. None si es inválido."""
    s = str(spec or "").strip()
    plus = "+" if grow else ""
    m = SIZE_RE.match(s)
    if m:
        if float(m.group(1)) <= 0:
            return None
        num = m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else m.group(1)
        return ["-L", plus + num + m.group(2).lower()]
    m = PCT_RE.match(s)
    if m and 1 <= int(m.group(1)) <= 100:
        return ["-l", f"{plus}{int(m.group(1))}%FREE"]
    return None


def parse_quota_size(s):
    """'500M' / '2G' / '1024' (K) → bloques de 1 KiB. '' = 0 (sin límite). None si es inválido."""
    s = str(s if s is not None else "").strip()
    if s == "":
        return 0
    m = QSIZE_RE.match(s)
    if not m:
        return None
    return int(round(float(m.group(1)) * QMULT[m.group(2).upper()]))


def parse_inodes(s):
    s = str(s if s is not None else "").strip()
    if s == "":
        return 0
    return int(s) if INODES_RE.match(s) else None


def fstab_entries(text):
    """[(índice de línea, campos)] de las líneas activas de fstab."""
    res = []
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if s and not s.startswith("#"):
            f = s.split()
            if len(f) >= 2:
                res.append((i, f))
    return res


def fstab_add_options(text, target, opts):
    """Agrega opciones a la línea de fstab cuyo punto de montaje es target. None si no existe."""
    lines = text.splitlines()
    for i, f in fstab_entries(text):
        if f[1] != target:
            continue
        while len(f) < 6:
            f.append({2: "auto", 3: "defaults", 4: "0", 5: "0"}[len(f)])
        cur = f[3].split(",")
        f[3] = ",".join(cur + [o for o in opts if o not in cur])
        lines[i] = "\t".join(f)
        return "\n".join(lines) + "\n"
    return None


def has_quota_opts(options):
    return any(o.split("=", 1)[0] in QUOTA_OPTS for o in str(options or "").split(","))


def parse_mdstat(text):
    arrays, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^(md\w+)\s*:\s*(\S+)\s*(.*)$", line)
        if m:
            name, state, rest = m.groups()
            cur = {"name": name, "active": state == "active", "state": state, "readonly": "",
                   "level": "", "members": [], "blocks": 0, "total": 0, "working": 0,
                   "map": "", "degraded": False, "sync": None}
            for t in rest.split():
                mm = re.match(r"^([A-Za-z0-9_.!-]+)\[(\d+)\]((?:\([A-Z]\))*)$", t)
                if mm:
                    cur["members"].append({"name": mm.group(1).replace("!", "/"), "slot": int(mm.group(2)),
                                           "flags": re.sub(r"[()]", "", mm.group(3))})
                elif t.startswith("(") and t.endswith(")"):
                    cur["readonly"] = t.strip("()")
                elif re.match(r"^(raid\d+|linear|multipath|faulty)$", t):
                    cur["level"] = t
            arrays.append(cur)
            continue
        if cur is None:
            continue
        if not line.strip() or line.startswith("unused devices"):
            cur = None
            continue
        m = re.search(r"(\d+) blocks", line)
        if m and not cur["blocks"]:
            cur["blocks"] = int(m.group(1))
        m = re.search(r"\[(\d+)/(\d+)\]\s+\[([U_]+)\]", line)
        if m:
            cur["total"], cur["working"], cur["map"] = int(m.group(1)), int(m.group(2)), m.group(3)
            cur["degraded"] = cur["working"] < cur["total"] or "_" in cur["map"]
        m = re.search(r"(resync|recovery|reshape|check|repair)\s*=\s*([\d.]+)%", line)
        if m:
            fin = re.search(r"finish=(\S+)", line)
            spd = re.search(r"speed=(\S+)", line)
            cur["sync"] = {"action": m.group(1), "percent": float(m.group(2)),
                           "finish": fin.group(1) if fin else "", "speed": spd.group(1) if spd else ""}
        else:
            m = re.search(r"(resync|recovery|reshape|check|repair)\s*=\s*(DELAYED|PENDING)", line)
            if m:
                cur["sync"] = {"action": m.group(1), "percent": 0.0, "finish": m.group(2).lower(), "speed": ""}
    for a in arrays:
        if not a["active"]:
            a["degraded"] = True
    return arrays


def parse_md_export(text):
    """mdadm --detail --export → (info dict, [{'dev','role'}])."""
    info, members = {}, {}
    for line in text.splitlines():
        k, sep, v = line.strip().partition("=")
        if not sep:
            continue
        v = v.strip().strip("'\"")
        m = re.match(r"^MD_DEVICE_(.+)_(ROLE|DEV)$", k)
        if m:
            members.setdefault(m.group(1), {})[m.group(2).lower()] = v
        else:
            info[k] = v
    return info, [{"dev": x["dev"], "role": x.get("role", "")} for x in members.values() if x.get("dev")]


def merge_mdadm_conf(existing, scan):
    """Agrega a mdadm.conf las líneas ARRAY de `mdadm --detail --scan` cuyo UUID no esté ya."""
    have = {u.lower() for u in re.findall(r"UUID=([0-9A-Fa-f:]+)", existing)}
    added = []
    for line in scan.splitlines():
        line = line.strip()
        m = re.search(r"UUID=([0-9A-Fa-f:]+)", line)
        if line.startswith("ARRAY ") and m and m.group(1).lower() not in have:
            added.append(line)
            have.add(m.group(1).lower())
    if not added:
        return existing, []
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"# Agregado por MatiPanel el {time.strftime('%Y-%m-%d %H:%M')}\n" + "\n".join(added) + "\n"
    return text, added


def parse_quotaon(text):
    """quotaon -pa → {(tipo, punto de montaje): bool}."""
    res = {}
    for line in text.splitlines():
        m = re.match(r"^(user|group|project) quota on (\S+) \((.+?)\) is (on|off)", line.strip())
        if m:
            res[(m.group(1), m.group(2))] = m.group(4) == "on"
    return res


def _grace(tok, now):
    if tok is None:
        return ""
    if tok.isdigit():
        secs = int(tok) - now if int(tok) else 0
        if not int(tok):
            return ""
        if secs <= 0:
            return "vencido"
        d, h = divmod(secs // 3600, 24)
        return f"{d}d {h}h" if d else f"{h}h {secs % 3600 // 60}min"
    return "" if tok == "none" else tok


def parse_repquota(text, now=None):
    """repquota -a -u -g -v -p (texto) → [{kind, device, block_grace, inode_grace, entries}]."""
    now = int(now if now is not None else time.time())
    reports, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^\*\*\* Report for (user|group|project) quotas on device (\S+)", line)
        if m:
            cur = {"kind": m.group(1), "device": m.group(2), "block_grace": "", "inode_grace": "", "entries": []}
            reports.append(cur)
            continue
        if cur is None:
            continue
        m = re.match(r"^Block grace time:\s*([^;]+);\s*Inode grace time:\s*(.+)$", line.strip())
        if m:
            cur["block_grace"], cur["inode_grace"] = m.group(1).strip(), m.group(2).strip()
            continue
        t = line.split()
        if len(t) < 8 or not re.fullmatch(r"[-+]{2}", t[1]):
            continue
        rest = t[2:]
        if len(rest) == 8:
            bu, bs, bh, bg, iu, is_, ih, ig = rest
        elif len(rest) == 6:
            (bu, bs, bh, iu, is_, ih), bg, ig = rest, None, None
        elif len(rest) == 7 and t[1][0] == "+":
            (bu, bs, bh, bg, iu, is_, ih), ig = rest, None
        elif len(rest) == 7:
            (bu, bs, bh, iu, is_, ih, ig), bg = rest, None
        else:
            continue
        if not all(x.isdigit() for x in (bu, bs, bh, iu, is_, ih)):
            continue
        cur["entries"].append({
            "name": t[0], "block_over": t[1][0] == "+", "inode_over": t[1][1] == "+",
            "used": int(bu), "soft": int(bs), "hard": int(bh), "grace": _grace(bg, now),
            "files": int(iu), "isoft": int(is_), "ihard": int(ih), "igrace": _grace(ig, now),
        })
    return reports


# ── Lectura del estado del sistema ───────────────────────────────────

def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _write_atomic(path, text):
    tmp = path + ".matipanel-tmp"
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _backup(path):
    dst = f"{path}.matipanel-{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(path, dst)
    return dst


def _devices():
    code, out, err = _cmd(["lsblk", "-J", "-b", "-o", LSBLK_COLS], timeout=20)
    if code != 0:
        return None, _msg(out, err, "lsblk falló")
    try:
        return parse_lsblk(json.loads(out)), None
    except ValueError:
        return None, "No se pudo interpretar la salida de lsblk"


def _device(path, devs):
    """Nodo de lsblk cuya ruta coincide exactamente (allow-list)."""
    if not isinstance(path, str) or not path.startswith("/dev/"):
        return None
    return next((d for d in devs if d["path"] == path), None)


def _lvm_json(cmd, cols, key):
    code, out, err = _cmd([cmd, "--reportformat", "json", "--units", "b", "--nosuffix", "-o", cols], timeout=30)
    if code == 127:
        return None, "missing"
    if code != 0:
        return None, _msg(out, err, f"{cmd} falló")
    try:
        return lvm_rows(json.loads(out), key), None
    except ValueError:
        return None, f"No se pudo interpretar la salida de {cmd}"


def _lvm_state(devs=None):
    """pvs/vgs/lvs normalizados + datos de lsblk (montaje, fs, uso)."""
    if devs is None:
        devs, _ = _devices()
    by_path = {d["path"]: d for d in devs or []}
    pvs, e1 = _lvm_json("pvs", "pv_name,vg_name,pv_size,pv_free,pv_attr,pv_fmt", "pv")
    if e1 == "missing":
        return {"installed": False, "pvs": [], "vgs": [], "lvs": []}
    vgs, e2 = _lvm_json("vgs", "vg_name,vg_size,vg_free,pv_count,lv_count,snap_count,vg_attr", "vg")
    lvs, e3 = _lvm_json("lvs", "lv_name,vg_name,lv_size,lv_attr,origin,pool_lv,data_percent,lv_path,"
                               "lv_dm_path,segtype", "lv")
    st = {"installed": True, "error": e1 or e2 or e3, "pvs": [], "vgs": [], "lvs": []}
    for p in pvs or []:
        size, free = _int(p.get("pv_size")), _int(p.get("pv_free"))
        st["pvs"].append({"path": p.get("pv_name", ""), "vg": p.get("vg_name", ""), "size": size,
                          "free": free, "attr": p.get("pv_attr", ""), "fmt": p.get("pv_fmt", ""),
                          "missing": "m" in (p.get("pv_attr") or "")[2:3]})
    for v in vgs or []:
        st["vgs"].append({"name": v.get("vg_name", ""), "size": _int(v.get("vg_size")),
                          "free": _int(v.get("vg_free")), "pv_count": _int(v.get("pv_count")),
                          "lv_count": _int(v.get("lv_count")), "snap_count": _int(v.get("snap_count")),
                          "attr": v.get("vg_attr", "")})
    raw = lvs or []
    origins = {(l.get("vg_name"), l.get("origin")) for l in raw if l.get("origin")}
    pools = {(l.get("vg_name"), l.get("pool_lv")) for l in raw if l.get("pool_lv")}
    for l in raw:
        vg, name, attr = l.get("vg_name", ""), l.get("lv_name", ""), l.get("lv_attr") or ""
        dm = l.get("lv_dm_path") or ""
        node = by_path.get(dm) or by_path.get(l.get("lv_path") or "") or {}
        mounts = node.get("mounts") or []
        usage = None
        mp = next((m for m in mounts if m.startswith("/")), None)
        if mp:
            try:
                u = shutil.disk_usage(mp)
                usage = {"total": u.total, "used": u.used, "percent": round(u.used * 100 / u.total, 1) if u.total else 0}
            except OSError:
                pass
        st["lvs"].append({
            "vg": vg, "name": name, "size": _int(l.get("lv_size")), "attr": attr,
            "origin": l.get("origin") or "", "pool": l.get("pool_lv") or "", "segtype": l.get("segtype") or "",
            "data_percent": l.get("data_percent") or "", "path": l.get("lv_path") or f"/dev/{vg}/{name}",
            "dm_path": dm, "snapshot": attr[:1] in ("s", "S") or bool(l.get("origin")),
            "invalid": attr[:1] == "S", "thin_pool": attr[:1] == "t", "active": attr[4:5] == "a",
            "has_snapshots": (vg, name) in origins, "has_thin": (vg, name) in pools,
            "fstype": node.get("fstype", ""), "uuid": node.get("uuid", ""), "mounts": mounts,
            "holders": [c for c in node.get("children", []) if not c.endswith(("-real", "-cow"))],
            "usage": usage,
        })
    return st


def _find_lv(st, vg, lv):
    return next((l for l in st["lvs"] if l["vg"] == vg and l["name"] == lv), None)


def _mounted_targets():
    code, out, _ = _cmd(["findmnt", "-J", "-l", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"], timeout=15)
    if code != 0:
        return []
    try:
        return json.loads(out).get("filesystems") or []
    except ValueError:
        return []


def _fstab_verify():
    code, out, err = _cmd(["findmnt", "--verify"], timeout=30)
    return code == 0, (out + err).strip()


def _fstab_transaction(new_text, steps):
    """Respalda fstab, escribe new_text, verifica y ejecuta steps(); ante un error restaura el respaldo.

    steps() retorna None si todo salió bien o un mensaje de error (y deshace sus propios efectos).
    """
    base_ok, _ = _fstab_verify()
    backup = _backup(FSTAB)
    _write_atomic(FSTAB, new_text)
    _cmd(["systemctl", "daemon-reload"], timeout=60)
    ok, out = _fstab_verify()
    if base_ok and not ok:
        error = "findmnt --verify encontró errores en el nuevo /etc/fstab:\n" + out
    else:
        error = steps()
    if error:
        shutil.copy2(backup, FSTAB)
        _cmd(["systemctl", "daemon-reload"], timeout=60)
        return f"{error}\nSe restauró /etc/fstab desde {backup}."
    return None


def _realpath(p):
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def _check_mountpoint(mp, fstab_text, mounted):
    if len(mp) > 200 or not MP_RE.match(mp) or any(p in (".", "..") for p in mp.split("/")[1:]):
        return "Punto de montaje inválido: usa una ruta absoluta como /srv/datos (letras, números, . _ - @ +)."
    for p in {mp, _realpath(mp)}:
        if p in PROTECTED_MP or any(p == t or p.startswith(t + "/") for t in PROTECTED_TREES):
            return f"{mp} es una carpeta del sistema. Usa una subcarpeta, por ejemplo /srv/datos o /mnt/respaldos."
    if mp in mounted:
        return f"Ya hay algo montado en {mp}."
    if any(f[1] == mp for _, f in fstab_entries(fstab_text)):
        return f"{mp} ya figura en /etc/fstab."
    if os.path.lexists(mp):
        if os.path.islink(mp) or not os.path.isdir(mp):
            return f"{mp} existe y no es una carpeta."
        if os.listdir(mp):
            return f"La carpeta {mp} no está vacía: su contenido quedaría oculto bajo el volumen."
    return _mountpoint_owner_problem(mp)


def _mountpoint_owner_problem(mp):
    """El punto de montaje y sus carpetas superiores que ya existan deben ser de root, sin symlinks
    y no escribibles por otros usuarios.

    mount (y el arranque, vía fstab) siguen symlinks: si un usuario controla /home/ana/vol o su
    carpeta superior, puede cambiarla por un symlink a /etc justo antes del montaje (o después,
    para el próximo arranque) y el volumen nuevo taparía /etc.
    """
    cur = mp
    while True:
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            st = None
        except OSError as e:
            return f"No se pudo revisar {cur}: {e}"
        if st is not None:
            if stat.S_ISLNK(st.st_mode):
                return f"{cur} es un enlace simbólico: usa la ruta real."
            if st.st_uid != 0:
                return f"{cur} pertenece a otro usuario: usa una carpeta de root, por ejemplo /srv/datos."
            if st.st_mode & 0o002 or (st.st_mode & 0o020 and st.st_gid != 0):
                return f"Otros usuarios pueden escribir en {cur}: usa una carpeta de root, por ejemplo /srv/datos."
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


# ── Páginas y lectura ────────────────────────────────────────────────

@storage_bp.route("/")
@login_required
def index():
    return render_template("storage.html")


@storage_bp.route("/api/lvm")
@login_required
def lvm_status():
    devs, error = _devices()
    st = _lvm_state(devs or [])
    public = [{k: d[k] for k in ("path", "name", "type", "size", "fstype", "mounts", "model", "serial",
                                 "rota", "free", "status", "depth", "has_root")} for d in devs or []]
    return jsonify({"devices": public, "lvm": st, "error": error,
                    "xfs": bool(shutil.which("mkfs.xfs")), "can_install": _can_install()})


@storage_bp.route("/api/raid")
@login_required
def raid_status():
    installed = bool(shutil.which("mdadm"))
    try:
        mdstat = _read("/proc/mdstat")
    except OSError:
        mdstat = ""
    arrays = parse_mdstat(mdstat)
    for a in arrays:
        info, members = {}, []
        if installed:
            code, out, _ = _cmd(["mdadm", "--detail", "--export", "/dev/" + a["name"]], timeout=15)
            if code == 0:
                info, members = parse_md_export(out)
        flags = {"/dev/" + m["name"]: m["flags"] for m in a["members"]}
        if not members:
            members = [{"dev": "/dev/" + m["name"], "role": "faulty" if "F" in m["flags"] else
                        "spare" if "S" in m["flags"] else str(m["slot"])} for m in a["members"]]
        for m in members:
            f = flags.get(m["dev"], "")
            m["state"] = ("faulty" if m["role"] == "faulty" or "F" in f else
                          "spare" if m["role"] == "spare" or "S" in f else "active")
        a["members_detail"] = sorted(members, key=lambda m: m["dev"])
        a["level"] = a["level"] or info.get("MD_LEVEL", "")
        a["uuid"] = info.get("MD_UUID", "")
        a["metadata"] = info.get("MD_METADATA", "")
        a["md_name"] = info.get("MD_NAME", "")
        a["size"] = a["blocks"] * 1024
        try:
            a["array_state"] = _read(f"/sys/block/{a['name']}/md/array_state").strip()
        except OSError:
            a["array_state"] = ""
    conf = ""
    if os.path.exists(MDADM_CONF):
        try:
            conf = _read(MDADM_CONF)
        except OSError:
            pass
    known = {u.lower() for u in re.findall(r"UUID=([0-9A-Fa-f:]+)", conf)}
    for a in arrays:
        a["in_conf"] = bool(a["uuid"]) and a["uuid"].lower() in known
    return jsonify({"installed": installed, "arrays": arrays, "can_install": _can_install(),
                    "conf_path": MDADM_CONF})


@storage_bp.route("/api/quota")
@login_required
def quota_status():
    tools = all(shutil.which(t) for t in ("quotacheck", "quotaon", "repquota", "setquota"))
    fss = _quota_filesystems()
    state = {}
    reports = []
    if tools:
        code, out, _ = _cmd(["quotaon", "-pa"], timeout=20)
        state = parse_quotaon(out)
        if any(f["quota"] for f in fss):
            code, out, err = _cmd(["repquota", "-a", "-u", "-g", "-v", "-p"], timeout=120)
            reports = parse_repquota(out)
    by_source = {f["source"]: f["target"] for f in fss}
    for f in fss:
        f["user_on"] = state.get(("user", f["target"]))
        f["group_on"] = state.get(("group", f["target"]))
    for r in reports:
        r["target"] = by_source.get(r["device"], "")
    return jsonify({"installed": tools, "filesystems": fss, "reports": reports, "can_install": _can_install()})


def _quota_filesystems():
    seen, res = set(), []
    for f in _mounted_targets():
        src, tgt, fs = f.get("source") or "", f.get("target") or "", f.get("fstype") or ""
        if fs not in QUOTA_FS or "[" in src or tgt in seen:
            continue
        seen.add(tgt)
        res.append({"target": tgt, "source": src, "fstype": fs, "options": f.get("options") or "",
                    "quota": has_quota_opts(f.get("options"))})
    return res


@storage_bp.route("/api/install", methods=["POST"])
@login_required
@exclusive
def install():
    pkg = _data().get("pkg")
    if pkg not in INSTALLABLE:
        return _err("Paquete no permitido")
    if not _can_install():
        return _err("La instalación automática solo está disponible en Debian/Ubuntu (APT)")
    ok, out, msg = apt_get("install", pkg, timeout=900)
    if not ok:
        return _err(msg or "apt-get falló", 500)
    return jsonify({"ok": True, "output": out[-4000:]})


# ── LVM: acciones ────────────────────────────────────────────────────

def _lvm_or_err():
    devs, error = _devices()
    if error:
        return None, None, _err(error, 500)
    st = _lvm_state(devs)
    if not st["installed"]:
        return None, None, _err("LVM no está instalado (paquete lvm2)")
    if st.get("error"):
        return None, None, _err(st["error"], 500)
    return devs, st, None


def _free_device(path, devs, allowed_types=FREE_TYPES):
    """Valida que path sea un dispositivo libre según lsblk. Retorna (nodo, error)."""
    n = _device(path, devs)
    if not n:
        return None, "Dispositivo desconocido; refresca la página."
    if n["has_root"] or n["mounts"]:
        return None, f"{path} está montado o contiene el sistema raíz."
    if n["type"] not in allowed_types or not n["free"]:
        return None, f"{path} no está libre (tiene particiones, datos o está en uso)."
    return n, None


@storage_bp.route("/api/pv/create", methods=["POST"])
@login_required
@exclusive
def pv_create():
    devs, st, e = _lvm_or_err()
    if e:
        return e
    n, error = _free_device(_data().get("device"), devs)
    if error:
        return _err(error)
    code, out, err = _cmd(["pvcreate", n["path"]], timeout=120)
    if code != 0:
        return _err(_msg(out, err, "pvcreate falló"), 500)
    return jsonify({"ok": True, "message": f"{n['path']} ya es un volumen físico (PV)."})


@storage_bp.route("/api/vg/create", methods=["POST"])
@login_required
@exclusive
def vg_create():
    d = _data()
    devs, st, e = _lvm_or_err()
    if e:
        return e
    name, pvs = d.get("name"), d.get("pvs")
    if not isinstance(name, str) or not LVM_NAME_RE.match(name) or name in (".", ".."):
        return _err("Nombre inválido: letras, números y _ . + - (máx. 64, sin empezar por punto ni guion).")
    if any(v["name"] == name for v in st["vgs"]) or os.path.exists("/dev/" + name):
        return _err(f"El nombre {name} ya está en uso.")
    free_pvs = {p["path"] for p in st["pvs"] if not p["vg"]}
    if not isinstance(pvs, list) or not pvs or len(pvs) > 32 or len(set(pvs)) != len(pvs):
        return _err("Elige al menos un PV.")
    if any(p not in free_pvs for p in pvs):
        return _err("Algún PV elegido no existe o ya pertenece a otro grupo; refresca la página.")
    code, out, err = _cmd(["vgcreate", name, *pvs], timeout=120)
    if code != 0:
        return _err(_msg(out, err, "vgcreate falló"), 500)
    return jsonify({"ok": True, "message": f"Grupo {name} creado."})


@storage_bp.route("/api/vg/extend", methods=["POST"])
@login_required
@exclusive
def vg_extend():
    d = _data()
    devs, st, e = _lvm_or_err()
    if e:
        return e
    vg, pv = d.get("vg"), d.get("pv")
    if not any(v["name"] == vg for v in st["vgs"]):
        return _err("Grupo de volúmenes desconocido")
    if pv not in {p["path"] for p in st["pvs"] if not p["vg"]}:
        return _err("El PV no existe o ya pertenece a un grupo; refresca la página.")
    code, out, err = _cmd(["vgextend", vg, pv], timeout=120)
    if code != 0:
        return _err(_msg(out, err, "vgextend falló"), 500)
    return jsonify({"ok": True, "message": f"{pv} agregado a {vg}."})


@storage_bp.route("/api/lv/create", methods=["POST"])
@login_required
@exclusive
def lv_create():
    d = _data()
    devs, st, e = _lvm_or_err()
    if e:
        return e
    vg, name = d.get("vg"), d.get("name")
    fs, mp = d.get("fs") or "", str(d.get("mountpoint") or "").strip()
    vgo = next((v for v in st["vgs"] if v["name"] == vg), None)
    if not vgo:
        return _err("Grupo de volúmenes desconocido")
    if not isinstance(name, str) or not LVM_NAME_RE.match(name) or name in (".", "..") \
            or name.startswith(("snapshot", "pvmove")):
        return _err("Nombre inválido: letras, números y _ . + - (máx. 64).")
    if _find_lv(st, vg, name):
        return _err(f"Ya existe {vg}/{name}.")
    size = parse_lv_size(d.get("size"))
    if not size:
        return _err("Tamaño inválido. Ejemplos: 10G, 500M o 100% (del espacio libre).")
    if vgo["free"] <= 0:
        return _err(f"El grupo {vg} no tiene espacio libre.")
    if fs not in ("", "ext4", "xfs"):
        return _err("Sistema de archivos no soportado")
    if fs == "xfs" and not shutil.which("mkfs.xfs"):
        return _err("Para XFS instala antes el paquete xfsprogs.")
    if mp and not fs:
        return _err("Para montar el volumen primero hay que formatearlo (elige ext4 o XFS).")
    if mp:
        try:
            fstab_text = _read(FSTAB)
        except OSError as ex:
            return _err(f"No se pudo leer {FSTAB}: {ex}", 500)
        error = _check_mountpoint(mp, fstab_text, {f.get("target") for f in _mounted_targets()})
        if error:
            return _err(error)

    code, out, err = _cmd(["lvcreate", "-y", "-n", name, *size, vg], timeout=180)
    if code != 0:
        return _err(_msg(out, err, "lvcreate falló"), 500)
    dev = f"/dev/{vg}/{name}"
    done = f"Volumen {vg}/{name} creado"
    if not fs:
        return jsonify({"ok": True, "message": done + "."})

    mkfs = ["mkfs.ext4", "-q", dev] if fs == "ext4" else ["mkfs.xfs", "-q", dev]
    code, out, err = _cmd(mkfs, timeout=1800)
    if code != 0:
        return _err(f"{done}, pero falló el formateo:\n{_msg(out, err)}", 500)
    done += f" y formateado en {fs}"
    if not mp:
        return jsonify({"ok": True, "message": done + "."})

    code, uuid, _ = _cmd(["blkid", "-s", "UUID", "-o", "value", dev], timeout=20)
    uuid = uuid.strip()
    if code != 0 or not UUID_RE.match(uuid):
        return _err(f"{done}, pero no se pudo leer su UUID; no se tocó /etc/fstab.", 500)
    created = not os.path.exists(mp)
    try:
        os.makedirs(mp, mode=0o755, exist_ok=True)
    except OSError as ex:
        return _err(f"{done}, pero no se pudo crear {mp}: {ex}", 500)
    text = fstab_text if fstab_text.endswith("\n") or not fstab_text else fstab_text + "\n"
    text += f"# {vg}/{name} (MatiPanel)\nUUID={uuid}\t{mp}\t{fs}\tdefaults,nofail\t0\t{2 if fs == 'ext4' else 0}\n"

    def steps():
        c, o, er = _cmd(["mount", mp], timeout=60)
        return None if c == 0 else "mount falló: " + _msg(o, er)

    error = _fstab_transaction(text, steps)
    if error:
        if created:
            try:
                os.rmdir(mp)
            except OSError:
                pass
        return _err(f"{done}, pero no se pudo montar:\n{error}", 500)
    return jsonify({"ok": True, "message": f"{done} y montado en {mp} (agregado a /etc/fstab por UUID)."})


@storage_bp.route("/api/lv/extend", methods=["POST"])
@login_required
@exclusive
def lv_extend():
    d = _data()
    devs, st, e = _lvm_or_err()
    if e:
        return e
    lv = _find_lv(st, d.get("vg"), d.get("lv"))
    if not lv:
        return _err("Volumen lógico desconocido; refresca la página.")
    if lv["thin_pool"]:
        return _err("Los thin pools se amplían desde la terminal (lvextend).")
    size = parse_lv_size(d.get("size"), grow=True)
    if not size:
        return _err("Tamaño inválido. Ejemplos: 5G, 500M o 100% (del espacio libre).")
    vgo = next((v for v in st["vgs"] if v["name"] == lv["vg"]), None)
    if not vgo or vgo["free"] <= 0:
        return _err(f"El grupo {lv['vg']} no tiene espacio libre. Agrégale un PV primero.")
    resize = ["-r"] if lv["fstype"] in RESIZABLE_FS and not lv["snapshot"] else []
    code, out, err = _cmd(["lvextend", *resize, *size, f"{lv['vg']}/{lv['name']}"], timeout=3600)
    if code != 0:
        return _err(_msg(out, err, "lvextend falló"), 500)
    extra = " y su sistema de archivos" if resize else ""
    return jsonify({"ok": True, "message": f"{lv['vg']}/{lv['name']}{extra} ampliado."})


@storage_bp.route("/api/lv/remove", methods=["POST"])
@login_required
@exclusive
def lv_remove():
    d = _data()
    devs, st, e = _lvm_or_err()
    if e:
        return e
    lv = _find_lv(st, d.get("vg"), d.get("lv"))
    if not lv:
        return _err("Volumen lógico desconocido; refresca la página.")
    full = f"{lv['vg']}/{lv['name']}"
    if lv["mounts"]:
        return _err(f"{full} está en uso ({', '.join(lv['mounts'])}). Desmóntalo antes.")
    if lv["has_snapshots"]:
        return _err(f"{full} tiene snapshots. Elimínalos primero.")
    if lv["has_thin"]:
        return _err(f"{full} es un thin pool con volúmenes. Elimínalos primero.")
    if lv["holders"]:
        return _err(f"{full} está siendo usado por otro dispositivo ({', '.join(lv['holders'])}).")
    try:
        fstab_text = _read(FSTAB)
    except OSError:
        fstab_text = ""
    refs = {lv["path"], lv["dm_path"], f"UUID={lv['uuid']}" if lv["uuid"] else None}
    if any(f[0] in refs for _, f in fstab_entries(fstab_text)):
        return _err(f"{full} figura en /etc/fstab. Quita esa línea antes de eliminarlo.")
    code, out, err = _cmd(["lvremove", "-y", full], timeout=120)
    if code != 0:
        return _err(_msg(out, err, "lvremove falló"), 500)
    return jsonify({"ok": True, "message": f"{full} eliminado."})


@storage_bp.route("/api/snap/create", methods=["POST"])
@login_required
@exclusive
def snap_create():
    d = _data()
    devs, st, e = _lvm_or_err()
    if e:
        return e
    lv = _find_lv(st, d.get("vg"), d.get("lv"))
    name = d.get("name")
    if not lv or lv["snapshot"] or lv["thin_pool"]:
        return _err("Elige un volumen lógico normal (no un snapshot ni un pool).")
    if not isinstance(name, str) or not LVM_NAME_RE.match(name) or name.startswith(("snapshot", "pvmove")):
        return _err("Nombre de snapshot inválido.")
    if _find_lv(st, lv["vg"], name):
        return _err(f"Ya existe {lv['vg']}/{name}.")
    cmd = ["lvcreate", "-s", "-n", name]
    if lv["segtype"] != "thin":
        size = parse_lv_size(d.get("size"))
        if not size or size[0] != "-L":
            return _err("Tamaño inválido para el snapshot. Ejemplos: 1G, 500M.")
        vgo = next((v for v in st["vgs"] if v["name"] == lv["vg"]), None)
        if not vgo or vgo["free"] <= 0:
            return _err(f"El grupo {lv['vg']} no tiene espacio libre para el snapshot.")
        cmd += size
    code, out, err = _cmd([*cmd, f"{lv['vg']}/{lv['name']}"], timeout=120)
    if code != 0:
        return _err(_msg(out, err, "lvcreate -s falló"), 500)
    return jsonify({"ok": True, "message": f"Snapshot {lv['vg']}/{name} creado."})


# ── RAID: acciones ───────────────────────────────────────────────────

def _arrays():
    try:
        return parse_mdstat(_read("/proc/mdstat"))
    except OSError:
        return []


def _array_members(name):
    code, out, _ = _cmd(["mdadm", "--detail", "--export", "/dev/" + name], timeout=15)
    return parse_md_export(out)[1] if code == 0 else []


def _raid_ready():
    if not shutil.which("mdadm"):
        return _err("mdadm no está instalado")
    return None


@storage_bp.route("/api/raid/fail-remove", methods=["POST"])
@login_required
@exclusive
def raid_fail_remove():
    if (e := _raid_ready()):
        return e
    d = _data()
    arr = next((a for a in _arrays() if a["name"] == d.get("array")), None)
    if not arr:
        return _err("Array desconocido; refresca la página.")
    dev = d.get("device")
    members = _array_members(arr["name"]) or [
        {"dev": "/dev/" + m["name"], "role": "faulty" if "F" in m["flags"] else
         "spare" if "S" in m["flags"] else str(m["slot"])} for m in arr["members"]]
    member = next((m for m in members if m["dev"] == dev), None)
    if not member:
        return _err(f"{dev} no es miembro de {arr['name']}.")
    flags = next((m["flags"] for m in arr["members"] if "/dev/" + m["name"] == dev), "")
    passive = member["role"] in ("faulty", "spare") or "F" in flags or "S" in flags
    if not passive:
        if arr["level"] in ("raid0", "linear", ""):
            return _err(f"{arr['name']} ({arr['level'] or 'inactivo'}) no tiene redundancia: quitar un disco lo destruye.")
        if arr["degraded"]:
            return _err(f"{arr['name']} ya está degradado: quitar otro disco activo podría destruir el array. "
                        "Reemplaza primero el disco que falta.")
    path = "/dev/" + arr["name"]
    if not passive or "F" not in flags:
        code, out, err = _cmd(["mdadm", "--manage", path, "--fail", dev], timeout=60)
        if code != 0 and not passive:
            return _err(_msg(out, err, "mdadm --fail falló"), 500)
    for attempt in range(3):
        code, out, err = _cmd(["mdadm", "--manage", path, "--remove", dev], timeout=60)
        if code == 0:
            break
        time.sleep(1)
    if code != 0:
        return _err("Se marcó como fallido, pero no se pudo quitar:\n" + _msg(out, err), 500)
    return jsonify({"ok": True, "message": f"{dev} quitado de {arr['name']}. Ya puedes reemplazar el disco."})


@storage_bp.route("/api/raid/add", methods=["POST"])
@login_required
@exclusive
def raid_add():
    if (e := _raid_ready()):
        return e
    d = _data()
    arr = next((a for a in _arrays() if a["name"] == d.get("array")), None)
    if not arr or not arr["active"]:
        return _err("Array desconocido o inactivo; refresca la página.")
    if arr["level"] in ("raid0", "linear"):
        return _err(f"{arr['level']} no admite discos de repuesto.")
    devs, error = _devices()
    if error:
        return _err(error, 500)
    n, error = _free_device(d.get("device"), devs, RAID_MEMBER_TYPES)
    if error:
        return _err(error)
    sizes = [x["size"] for x in devs if any(x["path"] == "/dev/" + m["name"] for m in arr["members"])]
    if sizes and n["size"] < min(sizes):
        return _err(f"{n['path']} es más pequeño que los miembros actuales del array.")
    code, out, err = _cmd(["mdadm", "--manage", "/dev/" + arr["name"], "--add", n["path"]], timeout=120)
    if code != 0:
        return _err(_msg(out, err, "mdadm --add falló"), 500)
    msg = "Se está reconstruyendo el array." if arr["degraded"] else "Quedó como disco de repuesto (spare)."
    return jsonify({"ok": True, "message": f"{n['path']} agregado a {arr['name']}. {msg}"})


@storage_bp.route("/api/raid/create", methods=["POST"])
@login_required
@exclusive
def raid_create():
    if (e := _raid_ready()):
        return e
    d = _data()
    name, level, devices = d.get("name"), str(d.get("level", "")), d.get("devices")
    m = MD_NEW_RE.match(name) if isinstance(name, str) else None
    if not m or int(m.group(1)) > 127:
        return _err("Nombre inválido: usa md0 a md127.")
    if any(a["name"] == name for a in _arrays()) or os.path.exists("/dev/" + name):
        return _err(f"/dev/{name} ya existe.")
    if level not in RAID_MIN:
        return _err("Nivel RAID no soportado (0, 1, 5, 6 o 10).")
    if not isinstance(devices, list) or len(set(devices)) != len(devices) or len(devices) > 32:
        return _err("Lista de discos inválida.")
    if len(devices) < RAID_MIN[level]:
        return _err(f"RAID {level} necesita al menos {RAID_MIN[level]} discos.")
    devs, error = _devices()
    if error:
        return _err(error, 500)
    for p in devices:
        _, error = _free_device(p, devs, RAID_MEMBER_TYPES)
        if error:
            return _err(error)
    cmd = ["mdadm", "--create", "/dev/" + name, f"--level={level}", f"--raid-devices={len(devices)}",
           "--metadata=1.2", "--run", *devices]
    # Los discos ya se validaron como libres: se responde "y" a la nota de arranque de RAID 1.
    code, out, err = _cmd(cmd, timeout=180, input="y\n")
    if code != 0:
        return _err(_msg(out, err, "mdadm --create falló"), 500)
    return jsonify({"ok": True, "message": f"/dev/{name} creado (RAID {level}). La sincronización inicial "
                                           "corre en segundo plano. Guárdalo en mdadm.conf para que se "
                                           "ensamble al arrancar."})


@storage_bp.route("/api/raid/save-conf", methods=["POST"])
@login_required
@exclusive
def raid_save_conf():
    if (e := _raid_ready()):
        return e
    code, out, err = _cmd(["mdadm", "--detail", "--scan"], timeout=30)
    if code != 0:
        return _err(_msg(out, err, "mdadm --detail --scan falló"), 500)
    try:
        existing = _read(MDADM_CONF) if os.path.exists(MDADM_CONF) else ""
    except OSError as ex:
        return _err(f"No se pudo leer {MDADM_CONF}: {ex}", 500)
    text, added = merge_mdadm_conf(existing, out)
    if not added:
        return jsonify({"ok": True, "added": 0, "message": f"Todos los arrays ya estaban en {MDADM_CONF}."})
    try:
        if existing:
            _backup(MDADM_CONF)
        os.makedirs(os.path.dirname(MDADM_CONF), exist_ok=True)
        _write_atomic(MDADM_CONF, text)
    except OSError as ex:
        return _err(f"No se pudo escribir {MDADM_CONF}: {ex}", 500)
    return jsonify({"ok": True, "added": len(added),
                    "message": f"{len(added)} array(s) agregados a {MDADM_CONF}. "
                               "Ejecuta ahora «Actualizar initramfs» para que se ensamblen al arrancar."})


@storage_bp.route("/api/raid/initramfs", methods=["POST"])
@login_required
@exclusive
def raid_initramfs():
    code, out, err = _cmd(["update-initramfs", "-u"], timeout=900)
    if code != 0:
        return _err(_msg(out, err, "update-initramfs falló"), 500)
    return jsonify({"ok": True, "message": "initramfs actualizado."})


# ── Cuotas: acciones ─────────────────────────────────────────────────

@storage_bp.route("/api/quota/enable", methods=["POST"])
@login_required
@exclusive
def quota_enable():
    if not all(shutil.which(t) for t in ("quotacheck", "quotaon")):
        return _err("Instala antes el paquete quota.")
    target = _data().get("target")
    fs = next((f for f in _quota_filesystems() if f["target"] == target), None)
    if not fs:
        return _err("Sistema de archivos desconocido; refresca la página.")
    if fs["fstype"] not in ("ext4", "ext3", "ext2"):
        return _err("La activación automática solo es para ext4. En XFS sigue las instrucciones.")
    if fs["quota"]:
        return _err("Ese sistema de archivos ya tiene cuotas en sus opciones de montaje.")
    try:
        text = _read(FSTAB)
    except OSError as ex:
        return _err(f"No se pudo leer {FSTAB}: {ex}", 500)
    new = fstab_add_options(text, target, ["usrquota", "grpquota"])
    if new is None:
        return _err(f"{target} no figura en /etc/fstab; agrégalo antes para que las cuotas sean permanentes.")

    def steps():
        c, o, er = _cmd(["mount", "-o", "remount,usrquota,grpquota", target], timeout=60)
        if c != 0:
            return "No se pudo remontar con cuotas: " + _msg(o, er)
        c, o, er = _cmd(["quotacheck", "-cugm", target], timeout=3600)
        if c == 0:
            c, o, er = _cmd(["quotaon", "-ug", target], timeout=60)
            if c == 0:
                return None
            _cmd(["quotaoff", "-ug", target], timeout=60)
        _cmd(["mount", "-o", "remount,noquota", target], timeout=60)
        return "Falló la activación de cuotas: " + _msg(o, er)

    error = _fstab_transaction(new, steps)
    if error:
        return _err(error, 500)
    return jsonify({"ok": True, "message": f"Cuotas de usuario y grupo activadas en {target}."})


@storage_bp.route("/api/quota/set", methods=["POST"])
@login_required
@exclusive
def quota_set():
    if not shutil.which("setquota"):
        return _err("Instala antes el paquete quota.")
    d = _data()
    kind, name, target = d.get("kind"), str(d.get("name") or "").strip(), d.get("target")
    if kind not in ("user", "group"):
        return _err("Tipo inválido")
    if not name or name.startswith("-") or len(name) > 64:
        return _err("Nombre inválido")
    try:
        (pwd.getpwnam if kind == "user" else grp.getgrnam)(name)
    except (KeyError, AttributeError):
        return _err(f"El {'usuario' if kind == 'user' else 'grupo'} «{name}» no existe.")
    fs = next((f for f in _quota_filesystems() if f["target"] == target and f["quota"]), None)
    if not fs:
        return _err("Ese sistema de archivos no tiene cuotas activas.")
    bs, bh = parse_quota_size(d.get("bsoft")), parse_quota_size(d.get("bhard"))
    is_, ih = parse_inodes(d.get("isoft")), parse_inodes(d.get("ihard"))
    if None in (bs, bh):
        return _err("Tamaño inválido. Ejemplos: 500M, 2G, 1.5T (0 o vacío = sin límite).")
    if None in (is_, ih):
        return _err("Límite de archivos inválido (número entero, 0 = sin límite).")
    if bh and bs > bh or ih and is_ > ih:
        return _err("El límite blando no puede ser mayor que el duro.")
    code, out, err = _cmd(["setquota", "-u" if kind == "user" else "-g", name,
                           str(bs), str(bh), str(is_), str(ih), target], timeout=30)
    if code != 0:
        return _err(_msg(out, err, "setquota falló"), 500)
    return jsonify({"ok": True, "message": f"Límites de {name} en {target} actualizados."})


# ── Ampliar un disco que creció (VM agrandada en el hipervisor) ─────────
# 1) el kernel vuelve a leer el tamaño (device/rescan), 2) growpart amplía la última partición,
# 3) pvresize o el redimensionado del sistema de archivos. Antes de tocar la tabla se respalda con sfdisk.

PART_BACKUP_DIR = "/var/lib/matipanel/partition-backups"
GROW_MIN = 16 * 1024 ** 2  # menos que esto es alineación o la copia de la tabla GPT, no espacio agregado
SECTOR = 512  # /sys/class/block/*/{size,start} siempre en sectores de 512 B
DISK_NAME_RE = re.compile(r"^[a-z][a-z0-9]{0,31}\Z")
GROW_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs"}


def unallocated_tail(disk_sectors, parts, pttype):
    """Espacio sin asignar al final del disco y la partición que termina más tarde.
    parts: [{"name", "number", "start", "size"}] en sectores (sysfs). En MBR la extendida mide 1-2 sectores
    en sysfs, así que no cuenta para el final; si la última es lógica (5+), hay que ampliar antes la extendida.
    → {"free": bytes, "last": part o None, "extended": part o None}"""
    real = [p for p in parts if not (pttype == "dos" and p["number"] <= 4 and p["size"] <= 2)]
    if not real:
        return {"free": 0, "last": None, "extended": None}
    last = max(real, key=lambda p: p["start"] + p["size"])
    margin = 2048  # 1 MiB: copia de la tabla GPT y alineación
    free = max(0, disk_sectors - (last["start"] + last["size"]) - margin) * SECTOR
    ext = None
    if pttype == "dos" and last["number"] >= 5:
        ext = next((p for p in parts if p["number"] <= 4 and p["size"] <= 2), None)
    return {"free": free, "last": last, "extended": ext}


def parse_growpart(out):
    """growpart: «CHANGE:» (--dry-run) o «CHANGED:» → ("change", sectores nuevos); «NOCHANGE:» → ("nochange", None)."""
    m = re.search(r"^CHANGED?:.*\bnew: size=(\d+)", out or "", re.M)
    if m:
        return "change", int(m.group(1))
    if re.search(r"^NOCHANGE:", out or "", re.M):
        return "nochange", None
    return "error", None


def _sysfs(name, field):
    try:
        with open(f"/sys/class/block/{name}/{field}") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def grow_candidates(devs, pvs=()):
    """Discos con espacio sin asignar al final y qué se puede hacer con él (action "grow" o why)."""
    by_path = {d["path"]: d for d in devs}
    pv_vg = {p["path"]: (p["vg"], p["size"]) for p in pvs}
    out = []
    for d in devs:
        if d["type"] != "disk" or d["name"].startswith(SKIP_PREFIXES) or not DISK_NAME_RE.match(d["name"]):
            continue
        disk_sectors = _sysfs(d["name"], "size")
        if not disk_sectors:
            continue
        kids = [by_path[c] for c in d["children"] if c in by_path and by_path[c]["type"] == "part"]
        if not kids:
            # Disco entero usado como PV de LVM (sin particiones): basta pvresize
            if d["fstype"] == "LVM2_member" and d["path"] in pv_vg:
                vg, pv_size = pv_vg[d["path"]]
                free = disk_sectors * SECTOR - pv_size
                if free >= GROW_MIN:
                    out.append({"disk": d["path"], "free": free, "part": "", "extended": "", "content": "LVM2_member",
                                "mount": "", "vg": vg, "action": "grow", "why": ""})
            continue
        if d["pttype"] not in ("gpt", "dos"):
            continue
        parts = []
        for k in kids:
            num, start, size = (_sysfs(k["name"], f) for f in ("partition", "start", "size"))
            if None not in (num, start, size):
                parts.append({"name": k["name"], "path": k["path"], "number": num, "start": start, "size": size})
        tail = unallocated_tail(disk_sectors, parts, d["pttype"])
        if tail["free"] < GROW_MIN or not tail["last"]:
            continue
        node = by_path[tail["last"]["path"]]
        fs, mount = node["fstype"], next((m for m in node["mounts"] if m.startswith("/")), "")
        why = ""
        if fs == "swap" or "[SWAP]" in node["mounts"]:
            why = (f"la última partición ({node['name']}) es swap: la que está antes no toca el espacio nuevo. "
                   "Ver la ayuda: «Usar el espacio que agregaste a un disco».")
        elif fs in ("ext2", "ext3", "ext4", "xfs", "btrfs") and not mount:
            why = f"{node['name']} ({fs}) no está montada: móntala primero para ampliarla en línea."
        elif fs != "LVM2_member" and fs not in GROW_FS:
            why = f"{node['name']} contiene {fs or 'algo que no se reconoce'}: el panel solo amplía LVM, ext4, XFS y Btrfs."
        out.append({"disk": d["path"], "free": tail["free"], "part": node["path"],
                    "extended": f"{d['path']}{'p' if d['name'][-1].isdigit() else ''}{tail['extended']['number']}"
                    if tail["extended"] else "",
                    "content": fs, "mount": mount, "vg": pv_vg.get(node["path"], ("", 0))[0],
                    "action": "" if why else "grow", "why": why})
    return out


def _part_number(path):
    m = re.search(r"(\d+)\Z", path)
    return m.group(1) if m else ""


@storage_bp.route("/api/grow")
@login_required
def grow_status():
    devs, error = _devices()
    st = _lvm_state(devs or [])
    rescan = any(os.path.exists(f"/sys/class/block/{d['name']}/device/rescan") for d in devs or []
                 if d["type"] == "disk" and DISK_NAME_RE.match(d["name"]))
    return jsonify({"candidates": grow_candidates(devs or [], st.get("pvs") or []), "error": error,
                    "growpart": bool(shutil.which("growpart")), "rescan": rescan, "can_install": _can_install()})


@storage_bp.route("/api/grow/rescan", methods=["POST"])
@login_required
@exclusive
def grow_rescan():
    """Pide al kernel que vuelva a leer el tamaño de cada disco (no escribe nada en el disco)."""
    devs, error = _devices()
    if error:
        return _err(error, 500)
    done = []
    for d in devs:
        if d["type"] != "disk" or d["name"].startswith(SKIP_PREFIXES) or not DISK_NAME_RE.match(d["name"]):
            continue
        path = f"/sys/class/block/{d['name']}/device/rescan"
        if os.path.exists(path):
            try:
                with open(path, "w") as f:
                    f.write("1")
                done.append(d["name"])
            except OSError:
                pass
    msg = (f"Tamaño releído en {', '.join(done)}." if done
           else "Los discos de este servidor actualizan su tamaño solos (virtio/NVMe): no hizo falta releerlo.")
    return jsonify({"ok": True, "message": msg})


@storage_bp.route("/api/grow", methods=["POST"])
@login_required
@exclusive
def grow_run():
    d = _data()
    devs, error = _devices()
    if error:
        return _err(error, 500)
    st = _lvm_state(devs)
    c = next((x for x in grow_candidates(devs, st.get("pvs") or [])
              if x["disk"] == d.get("disk") and x["part"] == (d.get("part") or "")), None)
    if not c:
        return _err("Ese disco ya no tiene espacio sin asignar; refresca la página.")
    if c["action"] != "grow":
        return _err(c["why"])
    steps = []

    def step(cmd, timeout=300):
        code, out, err = _cmd(cmd, timeout=timeout)
        steps.append({"cmd": " ".join(cmd), "ok": code == 0, "output": ((out or "") + (err or "")).strip()[-2000:]})
        return code, (out or "") + (err or "")

    if c["part"]:
        if not shutil.which("growpart"):
            return _err("Falta growpart: instala el paquete cloud-guest-utils.")
        # 1) Respaldo de la tabla de particiones (se restaura con: sfdisk /dev/sdX < respaldo)
        code, dump, err = _cmd(["sfdisk", "--dump", c["disk"]], timeout=30)
        if code != 0 or not dump.strip():
            return _err("No se pudo respaldar la tabla de particiones: " + _msg(dump, err))
        os.makedirs(PART_BACKUP_DIR, mode=0o700, exist_ok=True)
        backup = os.path.join(PART_BACKUP_DIR, f"{os.path.basename(c['disk'])}-{time.strftime('%Y%m%d-%H%M%S')}.sfdisk")
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(dump)
        steps.append({"cmd": f"sfdisk --dump {c['disk']} > {backup}", "ok": True, "output": ""})
        # 2) MBR con la última partición lógica: primero la extendida
        if c["extended"]:
            code, out = step(["growpart", c["disk"], _part_number(c["extended"])])
            if parse_growpart(out)[0] == "error":
                return jsonify({"ok": False, "error": "No se pudo ampliar la partición extendida: "
                                + out.strip()[-500:], "steps": steps, "backup": backup}), 500
        # 3) Prueba sin escribir y 4) ampliación real
        num = _part_number(c["part"])
        code, out = step(["growpart", "--dry-run", c["disk"], num])
        if parse_growpart(out)[0] != "change":
            return jsonify({"ok": False, "error": "growpart no puede ampliar la partición: " + out.strip()[-500:],
                            "steps": steps, "backup": backup}), 400
        code, out = step(["growpart", c["disk"], num])
        if parse_growpart(out)[0] != "change":
            return jsonify({"ok": False, "error": "growpart falló; la tabla está respaldada en " + backup,
                            "steps": steps, "backup": backup}), 500
    else:
        backup = ""
    # 5) Lo que hay adentro
    target, fs = c["part"] or c["disk"], c["content"]
    if fs == "LVM2_member":
        cmd = ["pvresize", target]
    elif fs in ("ext2", "ext3", "ext4"):
        cmd = ["resize2fs", target]
    elif fs == "xfs":
        cmd = ["xfs_growfs", c["mount"]]
    else:
        cmd = ["btrfs", "filesystem", "resize", "max", c["mount"]]
    code, out = step(cmd, timeout=3600)
    if code != 0:
        return jsonify({"ok": False, "error": f"La partición creció, pero {cmd[0]} falló. Revisa la salida.",
                        "steps": steps, "backup": backup}), 500
    res = {"ok": True, "steps": steps, "backup": backup, "vg": c["vg"],
           "message": f"{target} ampliado" + (f": el grupo {c['vg']} tiene el espacio nuevo libre." if c["vg"] else ".")}
    if c["vg"]:
        vgo = next((v for v in _lvm_state().get("vgs") or [] if v["name"] == c["vg"]), None)
        res["vg_free"] = vgo["free"] if vgo else 0
    return jsonify(res)
