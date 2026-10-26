"""
Detección de distribución y abstracción de comandos.

Permite que MatiPanel funcione en Debian/Ubuntu (apt + ufw) y
RHEL/Rocky/CentOS/Fedora (dnf + firewalld) sin duplicar módulos.
"""
import os
import shutil
from functools import lru_cache


@lru_cache(maxsize=1)
def detect():
    """Detecta la distro leyendo /etc/os-release.

    Retorna un dict con:
      family:  'debian' | 'rhel' | 'arch' | 'suse' | 'unknown'
      id:      'ubuntu', 'debian', 'rocky', 'fedora', ...
      name:    PRETTY_NAME
      pkg:     'apt' | 'dnf' | 'yum' | 'pacman' | 'zypper'
      fw:      'ufw' | 'firewalld' | 'nftables' | 'iptables' | None
    """
    info = {"id": "unknown", "id_like": "", "name": "Linux", "version": ""}

    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    info[k.lower()] = v.strip('"')
    except FileNotFoundError:
        pass

    distro_id = info.get("id", "").lower()
    id_like = info.get("id_like", "").lower()
    name = info.get("pretty_name", info.get("name", "Linux"))

    if distro_id in ("ubuntu", "debian", "raspbian", "linuxmint", "pop") or "debian" in id_like:
        family = "debian"
    elif distro_id in ("rhel", "rocky", "centos", "fedora", "almalinux", "ol") or "rhel" in id_like or "fedora" in id_like:
        family = "rhel"
    elif distro_id in ("arch", "manjaro", "endeavouros") or "arch" in id_like:
        family = "arch"
    elif distro_id in ("opensuse-leap", "opensuse-tumbleweed", "sles") or "suse" in id_like:
        family = "suse"
    else:
        family = "unknown"

    # Gestor de paquetes
    if shutil.which("apt"):
        pkg = "apt"
    elif shutil.which("dnf"):
        pkg = "dnf"
    elif shutil.which("yum"):
        pkg = "yum"
    elif shutil.which("pacman"):
        pkg = "pacman"
    elif shutil.which("zypper"):
        pkg = "zypper"
    else:
        pkg = None

    # Firewall
    if shutil.which("ufw"):
        fw = "ufw"
    elif shutil.which("firewall-cmd"):
        fw = "firewalld"
    elif shutil.which("nft"):
        fw = "nftables"
    elif shutil.which("iptables"):
        fw = "iptables"
    else:
        fw = None

    return {
        "family": family,
        "id": distro_id,
        "name": name,
        "version": info.get("version_id", ""),
        "pkg": pkg,
        "fw": fw,
    }


def is_debian():
    return detect()["family"] == "debian"


def is_rhel():
    return detect()["family"] == "rhel"
