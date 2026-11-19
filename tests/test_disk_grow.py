"""Ampliar un disco que creció: espacio sin asignar al final, salida de growpart y candidatos (Windows)."""
import sys, types
sys.path.insert(0, sys.argv[1])
for n in ("pwd", "grp", "pty", "termios", "fcntl", "pam"):
    sys.modules.setdefault(n, types.ModuleType(n))
from app.modules import storage as S

GIB = 1024 ** 3 // 512  # sectores por GiB

# GPT con LVM en sda3 (la última): disco de 40 GiB que antes era de 20
t = S.unallocated_tail(40 * GIB, [{"name": "sda1", "number": 1, "start": 2048, "size": 1048576},
                                  {"name": "sda2", "number": 2, "start": 1050624, "size": 2 * GIB},
                                  {"name": "sda3", "number": 3, "start": 1050624 + 2 * GIB,
                                   "size": 20 * GIB - 1050624 - 2 * GIB - 2048}], "gpt")
assert t["last"]["name"] == "sda3" and t["extended"] is None and abs(t["free"] - 20 * 1024 ** 3) < 2 * 1024 ** 2, t
# MBR de Debian con LVM: sda1 /boot, sda2 extendida (1 sector en sysfs), sda5 lógica → ampliar sda2 y sda5
t = S.unallocated_tail(40 * GIB, [{"name": "sda1", "number": 1, "start": 2048, "size": 999424},
                                  {"name": "sda2", "number": 2, "start": 1003518, "size": 2},
                                  {"name": "sda5", "number": 5, "start": 1003520, "size": 20 * GIB - 1003520}], "dos")
assert t["last"]["name"] == "sda5" and t["extended"]["name"] == "sda2" and t["free"] > 19 * 1024 ** 3, t
# Debian sin LVM: raíz en sda1 y swap lógica al final → la última es la swap
t = S.unallocated_tail(40 * GIB, [{"name": "sda1", "number": 1, "start": 2048, "size": 19 * GIB},
                                  {"name": "sda2", "number": 2, "start": 19 * GIB + 2046, "size": 2},
                                  {"name": "sda5", "number": 5, "start": 19 * GIB + 4096, "size": GIB - 8192}], "dos")
assert t["last"]["name"] == "sda5" and t["extended"]["name"] == "sda2"
# Sin espacio nuevo (solo la alineación final)
t = S.unallocated_tail(20 * GIB, [{"name": "sda1", "number": 1, "start": 2048, "size": 20 * GIB - 2048 - 34}], "gpt")
assert t["free"] == 0, t

# growpart
assert S.parse_growpart("CHANGE: partition=3 start=1054720 old: size=40888320 end=41943040 new: size=82831327 end=83886047\n") == ("change", 82831327)
assert S.parse_growpart("CHANGED: partition=5 start=1003520 old: size=40939520 end=41943040 new: size=82882527 end=83886047\n")[0] == "change"
assert S.parse_growpart("NOCHANGE: partition 3 is size 82831327. it cannot be grown\n") == ("nochange", None)
assert S.parse_growpart("FAILED: failed to get start and end for /dev/sda5 in /dev/sda\n") == ("error", None)

# Candidatos, con sysfs simulado
SYS = {("sda", "size"): 40 * GIB,
       ("sda1", "partition"): 1, ("sda1", "start"): 2048, ("sda1", "size"): 999424,
       ("sda2", "partition"): 2, ("sda2", "start"): 1003518, ("sda2", "size"): 2,
       ("sda5", "partition"): 5, ("sda5", "start"): 1003520, ("sda5", "size"): 20 * GIB - 1003520,
       ("sdb", "size"): 30 * GIB,
       ("sdc", "size"): 10 * GIB, ("sdc1", "partition"): 1, ("sdc1", "start"): 2048, ("sdc1", "size"): 5 * GIB}
S._sysfs = lambda name, field: SYS.get((name, field))


def node(path, typ, fstype="", mounts=(), children=(), pttype=""):
    return {"path": path, "name": path[5:], "type": typ, "fstype": fstype, "mounts": list(mounts),
            "children": list(children), "pttype": pttype}


devs = [node("/dev/sda", "disk", children=["/dev/sda1", "/dev/sda2", "/dev/sda5"], pttype="dos"),
        node("/dev/sda1", "part", "ext2", ["/boot"]), node("/dev/sda2", "part"),
        node("/dev/sda5", "part", "LVM2_member"),
        node("/dev/sdb", "disk", "LVM2_member"),
        node("/dev/sdc", "disk", children=["/dev/sdc1"], pttype="gpt"), node("/dev/sdc1", "part", "swap", ["[SWAP]"]),
        node("/dev/loop0", "loop")]
pvs = [{"path": "/dev/sda5", "vg": "debian-vg", "size": 20 * 1024 ** 3}, {"path": "/dev/sdb", "vg": "datos", "size": 20 * 1024 ** 3}]
c = {x["disk"]: x for x in S.grow_candidates(devs, pvs)}
assert c["/dev/sda"]["action"] == "grow" and c["/dev/sda"]["part"] == "/dev/sda5" and c["/dev/sda"]["extended"] == "/dev/sda2"
assert c["/dev/sda"]["vg"] == "debian-vg" and c["/dev/sda"]["content"] == "LVM2_member"
assert c["/dev/sdb"]["action"] == "grow" and c["/dev/sdb"]["part"] == "" and c["/dev/sdb"]["free"] == 10 * 1024 ** 3
assert c["/dev/sdc"]["action"] == "" and "swap" in c["/dev/sdc"]["why"]
assert S._part_number("/dev/nvme0n1p3") == "3" and S._part_number("/dev/sda5") == "5"
assert "cloud-guest-utils" in S.INSTALLABLE
print("disk grow ok")

# ── Captura de LinuxTemp: sda1 ext4 /, sda2 extendida (1 KB), sda5 swap al final; disco de 64 GiB ──
ext_node = {"type": "part", "name": "sda2", "size": 1024, "fstype": "", "mounts": [], "children": [], "pttype": "",
            "parttype": "0x5", "has_root": False}
assert not S.is_free(ext_node) and S.is_extended(ext_node)
assert S.is_extended({**ext_node, "parttype": ""})  # sin PARTTYPE: por tamaño
assert not S.is_extended({**ext_node, "size": 30 * 1024 ** 3, "parttype": "0x83"})
SYS.update({("sdd", "size"): 64 * GIB,
            ("sdd1", "partition"): 1, ("sdd1", "start"): 2048, ("sdd1", "size"): 30 * GIB,
            ("sdd2", "partition"): 2, ("sdd2", "start"): 30 * GIB + 4094, ("sdd2", "size"): 2,
            ("sdd5", "partition"): 5, ("sdd5", "start"): 30 * GIB + 4096, ("sdd5", "size"): 2 * GIB - 8192})
devs2 = [node("/dev/sdd", "disk", children=["/dev/sdd1", "/dev/sdd2", "/dev/sdd5"], pttype="dos"),
         node("/dev/sdd1", "part", "ext4", ["/"]), node("/dev/sdd2", "part"), node("/dev/sdd5", "part", "swap", ["[SWAP]"])]
MEM_OK = "MemTotal: 4000000 kB\nMemAvailable: 3000000 kB\nSwapTotal: 1800000 kB\nSwapFree: 1700000 kB\n"
MEM_BAD = "MemAvailable: 300000 kB\nSwapTotal: 1800000 kB\nSwapFree: 100000 kB\n"
assert S.swap_fits(MEM_OK) and not S.swap_fits(MEM_BAD)
S._read_meminfo = lambda: MEM_OK
c = S.grow_candidates(devs2, [])[0]
assert c["action"] == "swap" and c["part"] == "/dev/sdd1" and c["swap"] == ["/dev/sdd5"] and c["removes_ext"], c
assert c["plan"]["delete_ext"]["number"] == 2 and c["plan"]["fs"] == "ext4" and c["plan"]["mount"] == "/"
assert c["free"] > 33 * 1024 ** 3  # espacio nuevo + lo que ocupaba la swap
S._read_meminfo = lambda: MEM_BAD
c = S.grow_candidates(devs2, [])[0]
assert c["action"] == "" and "RAM" in c["why"], c
# Otra partición de datos detrás de la swap: no se toca
plan, why = S.swap_plan([{"name": "a1", "path": "/a1", "number": 1, "start": 2048, "size": 100},
                         {"name": "a2", "path": "/a2", "number": 2, "start": 3000, "size": 100},
                         {"name": "a3", "path": "/a3", "number": 3, "start": 4000, "size": 100}],
                        {"/a1": {"name": "a1", "fstype": "ext4", "mounts": ["/"]}, "/a2": {"name": "a2", "fstype": "swap", "mounts": []},
                         "/a3": {"name": "a3", "fstype": "ext4", "mounts": ["/srv"]}}, "gpt")
assert plan is None  # la swap no es la última: no hay nada que mover
plan, why = S.swap_plan([{"name": "a1", "path": "/a1", "number": 1, "start": 2048, "size": 100},
                         {"name": "a2", "path": "/a2", "number": 2, "start": 3000, "size": 100}],
                        {"/a1": {"name": "a1", "fstype": "ntfs", "mounts": []}, "/a2": {"name": "a2", "fstype": "swap", "mounts": []}}, "gpt")
assert plan is None and "ext4" in why
fst = "UUID=root / ext4 errors=remount-ro 0 1\n# swap was on /dev/sda5 during installation\nUUID=abc none swap sw 0 0\n"
new = S.fstab_swap_replace(fst, {"UUID=abc", "/dev/sda5"})
assert "# MatiPanel (swap movida a /swapfile): UUID=abc none swap sw 0 0" in new and new.endswith("/swapfile none swap sw 0 0\n")
assert S.fstab_swap_replace(new, {"UUID=abc"}).count("/swapfile none swap") == 1
print("swap move ok")
