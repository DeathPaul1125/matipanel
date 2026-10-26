#!/usr/bin/env bash
# install.sh — Instala MatiPanel en Debian/Ubuntu o RHEL/Rocky/Fedora
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Este script debe ejecutarse como root (sudo ./install.sh)" >&2
   exit 1
fi

INSTALL_DIR="/opt/matipanel"
CERT_DIR="/etc/matipanel"
DATA_DIR="/var/lib/matipanel"
LOG_DIR="/var/log/matipanel"
SERVICE_USER="matipanel"
PORT="${MATIPANEL_PORT:-8443}"

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
            libpam0g-dev build-essential openssl
        ;;
    rhel)
        if command -v dnf &>/dev/null; then PKG=dnf; else PKG=yum; fi
        $PKG install -y -q python3 python3-pip python3-devel \
            pam-devel gcc openssl
        ;;
    *)
        echo "⚠ Distro no soportada oficialmente. Instala manualmente:"
        echo "  python3, python3-venv, libpam-dev, gcc, openssl"
        read -p "¿Continuar de todas formas? [y/N] " -n 1 -r
        echo
        [[ $REPLY =~ ^[Yy]$ ]] || exit 1
        ;;
esac

# Usuario y grupo
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "→ Creando usuario de servicio: $SERVICE_USER"
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER" 2>/dev/null || \
    useradd --system --shell /sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER"
fi
if ! getent group matipanel &>/dev/null; then
    groupadd matipanel
fi

# Archivos
echo "→ Copiando archivos a $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR" "$CERT_DIR" "$DATA_DIR" "$LOG_DIR"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cp -r "$SOURCE_DIR"/{app,wsgi.py,requirements.txt} "$INSTALL_DIR/"

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

# Permisos
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$CERT_DIR" "$DATA_DIR" "$LOG_DIR"

# Sudoers
echo "→ Instalando regla de sudoers…"
cp "$SOURCE_DIR/scripts/matipanel.sudoers" /etc/sudoers.d/matipanel
chmod 0440 /etc/sudoers.d/matipanel
visudo -c -q || { echo "ERROR: sudoers inválido"; exit 1; }

# Systemd
echo "→ Instalando servicio systemd…"
cp "$SOURCE_DIR/systemd/matipanel.service" /etc/systemd/system/matipanel.service
systemctl daemon-reload
systemctl enable matipanel.service
systemctl restart matipanel.service

# Docker hook (opcional)
if command -v docker &>/dev/null; then
    echo "→ Docker detectado, agregando $SERVICE_USER al grupo docker…"
    usermod -aG docker "$SERVICE_USER" || true
fi

sleep 2
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "tu-ip")
echo
echo "═══════════════════════════════════════════"
echo "  ✓ MatiPanel instalado"
echo "═══════════════════════════════════════════"
echo
echo "  URL:        https://${IP}:${PORT}"
echo "  Servicio:   sudo systemctl status matipanel"
echo "  Logs:       sudo journalctl -u matipanel -f"
echo
echo "  Para dar acceso a un usuario:"
echo "    sudo usermod -aG matipanel <usuario>"
echo
echo "  Para habilitar la edición de archivos (opcional):"
echo "    sudo systemctl edit matipanel"
echo "    [Service]"
echo "    Environment=\"MATIPANEL_FILE_WRITE=1\""
echo
