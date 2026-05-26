#!/usr/bin/env bash
# install.sh — Instala MatiPanel en Ubuntu/Debian
# Uso: sudo ./install.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "Este script debe ejecutarse como root (sudo ./install.sh)" >&2
   exit 1
fi

INSTALL_DIR="/opt/matipanel"
CERT_DIR="/etc/matipanel"
LOG_DIR="/var/log/matipanel"
SERVICE_USER="matipanel"
PORT="${MATIPANEL_PORT:-8443}"

echo "═══════════════════════════════════════════"
echo "  Instalando MatiPanel"
echo "═══════════════════════════════════════════"

# 1. Dependencias del sistema
echo "→ Instalando dependencias del sistema…"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip python3-dev \
    libpam0g-dev build-essential openssl

# 2. Usuario y grupo de servicio
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "→ Creando usuario de servicio: $SERVICE_USER"
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER"
fi
# Grupo para usuarios permitidos en el login
if ! getent group matipanel &>/dev/null; then
    groupadd matipanel
fi

# 3. Copiar archivos
echo "→ Copiando archivos a $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR" "$CERT_DIR" "$LOG_DIR"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cp -r "$SOURCE_DIR"/{app,wsgi.py,requirements.txt} "$INSTALL_DIR/"

# 4. Virtualenv + deps Python
echo "→ Creando entorno virtual e instalando dependencias…"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# 5. Certificado autofirmado (válido 5 años)
if [[ ! -f "$CERT_DIR/cert.pem" ]]; then
    echo "→ Generando certificado TLS autofirmado…"
    openssl req -x509 -newkey rsa:4096 -nodes \
        -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
        -days 1825 -subj "/CN=$(hostname)" 2>/dev/null
    chmod 600 "$CERT_DIR/key.pem"
fi

# 6. Permisos
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$CERT_DIR" "$LOG_DIR"

# 7. Sudoers
echo "→ Instalando regla de sudoers…"
cp "$SOURCE_DIR/scripts/matipanel.sudoers" /etc/sudoers.d/matipanel
chmod 0440 /etc/sudoers.d/matipanel
visudo -c -q || { echo "ERROR: sudoers inválido"; exit 1; }

# 8. Unidad systemd
echo "→ Instalando servicio systemd…"
cp "$SOURCE_DIR/systemd/matipanel.service" /etc/systemd/system/matipanel.service
systemctl daemon-reload
systemctl enable matipanel.service
systemctl restart matipanel.service

# 9. Mostrar info final
sleep 2
IP=$(hostname -I | awk '{print $1}')
echo
echo "═══════════════════════════════════════════"
echo "  ✓ MatiPanel instalado"
echo "═══════════════════════════════════════════"
echo
echo "  Accede desde tu navegador:"
echo "    https://${IP}:${PORT}"
echo
echo "  Usuarios permitidos: los que pertenezcan al grupo 'matipanel' o 'sudo'."
echo "  Para agregar un usuario al panel:"
echo "    sudo usermod -aG matipanel <nombre-usuario>"
echo
echo "  Estado del servicio:"
echo "    sudo systemctl status matipanel"
echo
echo "  Logs:"
echo "    sudo journalctl -u matipanel -f"
echo
