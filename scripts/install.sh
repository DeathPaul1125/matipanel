#!/usr/bin/env bash
# install.sh — Instala MatiPanel en Debian/Ubuntu o RHEL/Rocky/Fedora
# Uso: sudo bash scripts/install.sh [usuario-con-acceso-al-panel]
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Este script debe ejecutarse como root (sudo bash scripts/install.sh)" >&2
   exit 1
fi

ADMIN_USER="${1:-}"
INSTALL_DIR="/opt/matipanel"
CERT_DIR="/etc/matipanel"
DATA_DIR="/var/lib/matipanel"
PORT="${MATIPANEL_PORT:-8443}"

if [[ -n "$ADMIN_USER" ]] && ! id "$ADMIN_USER" &>/dev/null; then
    echo "El usuario '$ADMIN_USER' no existe en este sistema." >&2
    exit 1
fi

# Detectar distro
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"
    DISTRO_LIKE="${ID_LIKE:-}"
else
    DISTRO_ID="unknown"; DISTRO_LIKE=""
fi

if [[ "$DISTRO_ID" =~ ^(ubuntu|debian|raspbian|linuxmint|pop)$ ]] || [[ "$DISTRO_LIKE" =~ debian ]]; then
    FAMILY="debian"
elif [[ "$DISTRO_ID" =~ ^(rhel|rocky|centos|fedora|almalinux|ol)$ ]] || [[ "$DISTRO_LIKE" =~ (rhel|fedora) ]]; then
    FAMILY="rhel"
else
    FAMILY="unknown"
fi

echo "═══════════════════════════════════════════"
echo "  Instalando MatiPanel"
echo "  Distro: ${PRETTY_NAME:-desconocida} ($FAMILY)"
echo "═══════════════════════════════════════════"

echo "→ Instalando dependencias del sistema…"
case "$FAMILY" in
    debian)
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip python3-dev \
            build-essential openssl unzip
        ;;
    rhel)
        if command -v dnf &>/dev/null; then PKG=dnf; else PKG=yum; fi
        $PKG install -y -q python3 python3-pip python3-devel gcc openssl unzip
        ;;
    *)
        echo "⚠ Distro no soportada oficialmente. Instala manualmente:"
        echo "  python3, python3-venv, gcc, openssl"
        read -p "¿Continuar de todas formas? [y/N] " -n 1 -r
        echo
        [[ $REPLY =~ ^[Yy]$ ]] || exit 1
        ;;
esac

# Restos de versiones anteriores (usuario de servicio + sudoers)
rm -f /etc/sudoers.d/matipanel
if id -u matipanel &>/dev/null; then userdel matipanel 2>/dev/null || true; fi

# Grupo de acceso al panel (el servicio corre como root)
getent group matipanel &>/dev/null || groupadd --system matipanel
if [[ -n "$ADMIN_USER" ]]; then
    echo "→ Dando acceso al panel a: $ADMIN_USER"
    usermod -aG matipanel "$ADMIN_USER"
fi

# Archivos
echo "→ Copiando archivos a $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR" "$CERT_DIR" "$DATA_DIR"
chmod 700 "$CERT_DIR" "$DATA_DIR"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$INSTALL_DIR/app"
cp -r "$SOURCE_DIR"/{app,wsgi.py,requirements.txt} "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR" "$CERT_DIR" "$DATA_DIR"

# Virtualenv
echo "→ Creando entorno virtual e instalando dependencias…"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# Certificado
if [[ ! -f "$CERT_DIR/cert.pem" ]]; then
    echo "→ Generando certificado TLS autofirmado…"
    openssl req -x509 -newkey rsa:4096 -nodes \
        -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
        -days 1825 -subj "/CN=$(hostname)" 2>/dev/null
    chmod 600 "$CERT_DIR/key.pem"
fi

# PAM: servicio propio para no heredar retardos/reglas de "login"
echo "→ Configurando PAM…"
if [[ "$FAMILY" == "debian" ]]; then
    printf '@include common-auth\n@include common-account\n' > /etc/pam.d/matipanel
else
    printf 'auth include system-auth\naccount include system-auth\n' > /etc/pam.d/matipanel
fi

# Systemd
echo "→ Instalando servicio systemd (puerto $PORT)…"
sed "s/0\.0\.0\.0:8443/0.0.0.0:${PORT}/" "$SOURCE_DIR/systemd/matipanel.service" \
    > /etc/systemd/system/matipanel.service
systemctl daemon-reload
systemctl enable matipanel.service
systemctl restart matipanel.service

sleep 2
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "tu-ip")
echo
echo "═══════════════════════════════════════════"
echo "  ✓ MatiPanel instalado"
echo "═══════════════════════════════════════════"
echo
echo "  URL:        https://${IP}:${PORT}"
echo "  Servicio:   systemctl status matipanel"
echo "  Logs:       journalctl -u matipanel -f"
echo
if [[ -z "$ADMIN_USER" ]]; then
echo "  Para dar acceso a un usuario:"
echo "    usermod -aG matipanel <usuario>"
echo
fi
echo "  Si tienes firewall activo, abre el puerto:"
echo "    ufw allow ${PORT}/tcp"
echo
echo "  Para habilitar la edición de archivos (opcional):"
echo "    systemctl edit matipanel"
echo "    [Service]"
echo "    Environment=\"MATIPANEL_FILE_WRITE=1\""
echo
