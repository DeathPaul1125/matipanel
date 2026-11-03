#!/usr/bin/env bash
# install.sh — Instala MatiPanel en Debian/Ubuntu o RHEL/Rocky/Fedora
# Uso: sudo bash scripts/install.sh [usuario-con-acceso-al-panel]
set -euo pipefail
# "su" sin guion no carga /usr/sbin (groupadd, usermod…); lo agregamos.
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"

DEFAULTS_FILE="/etc/default/matipanel"   # aquí se recuerda el puerto entre actualizaciones
UNIT_FILE="/etc/systemd/system/matipanel.service"

# --- funciones ---
valid_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); }

# Puerto ya configurado: /etc/default/matipanel o, en instalaciones anteriores, el --bind de la unidad
saved_port() {
    local p=""
    [[ -f "$DEFAULTS_FILE" ]] && p="$(sed -n 's/^MATIPANEL_PORT=\([0-9]\{1,5\}\)[[:space:]]*$/\1/p' "$DEFAULTS_FILE" | tail -1)"
    if [[ -n "$p" ]] && valid_port "$p"; then echo "$p $DEFAULTS_FILE"; return; fi
    [[ -f "$UNIT_FILE" ]] && p="$(sed -n 's/.*--bind 0\.0\.0\.0:\([0-9]\{1,5\}\).*/\1/p' "$UNIT_FILE" | head -1)"
    if [[ -n "$p" ]] && valid_port "$p"; then echo "$p $UNIT_FILE"; fi
}

# PIDs que escuchan en TCP $1 (vacío si nadie o si no hay ss)
port_pids() {
    command -v ss &>/dev/null || return 0
    ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true
}

# Nombres de los programas que escuchan en TCP $1
port_programs() {
    command -v ss &>/dev/null || return 0
    ss -ltnpH "sport = :$1" 2>/dev/null | grep -o '(("[^"]*"' | cut -d'"' -f2 | sort -u | paste -sd, - || true
}

# ¿El PID $1 es MatiPanel? (una actualización encuentra su propio puerto ocupado por él)
is_matipanel_pid() {
    grep -qs 'matipanel\.service' "/proc/$1/cgroup" || tr '\0' ' ' 2>/dev/null < "/proc/$1/cmdline" | grep -q '/opt/matipanel/'
}

# Libre = nadie escucha, o solo MatiPanel. → 0 libre, 1 ocupado por otro programa
port_free_for_us() {
    local pid listening
    listening="$(command -v ss &>/dev/null && ss -ltnH "sport = :$1" 2>/dev/null || true)"
    [[ -z "$listening" ]] && return 0
    for pid in $(port_pids "$1"); do
        is_matipanel_pid "$pid" || return 1
    done
    # Escucha alguien pero ss no mostró PIDs (sin permisos): no podemos saber quién es
    [[ -n "$(port_pids "$1")" ]] || return 1
    return 0
}

# Primer puerto libre para sugerir
suggest_port() {
    local p
    for p in 9443 $(seq 8444 8499) 10443; do
        [[ "$p" == "$1" ]] && continue
        if [[ -z "$(command -v ss &>/dev/null && ss -ltnH "sport = :$p" 2>/dev/null || true)" ]]; then echo "$p"; return; fi
    done
    echo 9443
}

# Estado de la contraseña de un usuario local: ok | none | locked | unknown (usuarios de LDAP/AD, etc.)
password_state() {
    local out
    # "x" delante para distinguir "sin línea" (usuario de LDAP/AD) de "hash vacío"
    out="$(awk -F: -v u="$1" '$1 == u {print "x" $2; exit}' "${SHADOW_FILE:-/etc/shadow}" 2>/dev/null || true)"
    [[ -z "$out" ]] && { echo unknown; return; }
    case "${out#x}" in
        ''|'!'|'*'|'!!'|'!*'|'*LK*') echo none ;;
        '!'*) echo locked ;;
        *) echo ok ;;
    esac
}
# --- fin funciones ---

if [[ $EUID -ne 0 ]]; then
   echo "Este script debe ejecutarse como root (sudo bash scripts/install.sh)" >&2
   exit 1
fi

ADMIN_USER="${1:-}"
INSTALL_DIR="/opt/matipanel"
CERT_DIR="/etc/matipanel"
DATA_DIR="/var/lib/matipanel"
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WARNINGS=""

if [[ -n "$ADMIN_USER" ]] && ! id "$ADMIN_USER" &>/dev/null; then
    echo "El usuario '$ADMIN_USER' no existe en este sistema." >&2
    exit 1
fi

# Puerto: MATIPANEL_PORT > el de la instalación anterior > 8443
SAVED="$(saved_port || true)"
SAVED_PORT="${SAVED%% *}"
if [[ -n "${MATIPANEL_PORT:-}" ]]; then
    PORT="$MATIPANEL_PORT"
    PORT_FROM="variable MATIPANEL_PORT"
    [[ -n "$SAVED_PORT" && "$SAVED_PORT" != "$PORT" ]] && PORT_FROM="$PORT_FROM; antes era $SAVED_PORT"
elif [[ -n "$SAVED_PORT" ]]; then
    PORT="$SAVED_PORT"
    PORT_FROM="instalación anterior (${SAVED#* })"
else
    PORT=8443
    PORT_FROM="predeterminado"
fi
if ! valid_port "$PORT"; then
    echo "MATIPANEL_PORT='$PORT' no es un puerto válido (1-65535)." >&2
    exit 1
fi

if ! port_free_for_us "$PORT"; then
    PROGS="$(port_programs "$PORT")"
    FREE="$(suggest_port "$PORT")"
    echo "✗ El puerto $PORT ya lo usa otro programa${PROGS:+: $PROGS}." >&2
    echo "  MatiPanel no podría arrancar ahí (y entrarías a esa otra aplicación)." >&2
    echo "  Instala en otro puerto, por ejemplo el $FREE (está libre):" >&2
    echo "    MATIPANEL_PORT=$FREE bash scripts/install.sh${ADMIN_USER:+ $ADMIN_USER}" >&2
    echo "  El puerto queda guardado: en las próximas actualizaciones no hará falta indicarlo." >&2
    exit 1
fi

if [[ "$SOURCE_DIR" == /tmp/* ]]; then
    WARNINGS+="  ⚠ Instalaste desde $SOURCE_DIR. /tmp se vacía al reiniciar el servidor y"$'\n'
    WARNINGS+="    tendrías que volver a clonar para actualizar. Muévelo a una carpeta permanente:"$'\n'
    WARNINGS+="      sudo mv $SOURCE_DIR /opt/matipanel-src"$'\n'
    WARNINGS+="    y actualiza siempre así:"$'\n'
    WARNINGS+="      cd /opt/matipanel-src && sudo git pull && sudo bash scripts/install.sh${ADMIN_USER:+ $ADMIN_USER}"$'\n'
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
echo "  Puerto: $PORT ($PORT_FROM)"
echo "═══════════════════════════════════════════"

echo "→ Instalando dependencias del sistema…"
case "$FAMILY" in
    debian)
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip python3-dev \
            build-essential openssl unzip zip cifs-utils
        ;;
    rhel)
        if command -v dnf &>/dev/null; then PKG=dnf; else PKG=yum; fi
        $PKG install -y -q python3 python3-pip python3-devel gcc openssl unzip zip cifs-utils
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
rm -rf "$INSTALL_DIR/app"
cp -r "$SOURCE_DIR"/{app,wsgi.py,requirements.txt,CHANGELOG.md} "$INSTALL_DIR/"
# Qué versión se instaló y cuándo (el panel lo muestra en «Acerca de»)
PANEL_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SOURCE_DIR/app/__init__.py")"
GIT_COMMIT="$(git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" rev-parse --short HEAD 2>/dev/null || true)"
GIT_BRANCH="$(git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
printf '{"version": "%s", "commit": "%s", "branch": "%s", "installed_at": "%s"}\n' \
    "$PANEL_VERSION" "$GIT_COMMIT" "$GIT_BRANCH" "$(date -Iseconds)" > "$INSTALL_DIR/BUILD.json"
echo "→ Versión $PANEL_VERSION${GIT_COMMIT:+ (commit $GIT_COMMIT)}"
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
# MATIPANEL_PORT también en el entorno del servicio (el firewall lo usa para no bloquear el panel)
sed -e "s/0\.0\.0\.0:8443/0.0.0.0:${PORT}/" \
    -e "/^Environment=\"MATIPANEL_SECURE_COOKIE=1\"/a Environment=\"MATIPANEL_PORT=${PORT}\"" \
    "$SOURCE_DIR/systemd/matipanel.service" > "$UNIT_FILE"
# Recordar el puerto: las próximas actualizaciones lo usan sin indicar MATIPANEL_PORT
printf '# Puerto de MatiPanel (lo lee scripts/install.sh al actualizar)\nMATIPANEL_PORT=%s\n' "$PORT" > "$DEFAULTS_FILE"
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
echo "  Para dejar el explorador de archivos en solo lectura (opcional):"
echo "    systemctl edit matipanel"
echo "    [Service]"
echo "    Environment=\"MATIPANEL_FILE_WRITE=0\""
echo
[[ -n "$WARNINGS" ]] && printf '%s\n' "$WARNINGS"

# El panel inicia sesión con la contraseña de Linux (PAM). En VPS (Oracle, AWS…) se entra por SSH
# con clave y el usuario suele no tener contraseña: sin ella no podría entrar al panel.
if [[ -n "$ADMIN_USER" ]]; then
    PW_STATE="$(password_state "$ADMIN_USER")"
    if [[ "$PW_STATE" == none || "$PW_STATE" == locked ]]; then
        if [[ "$PW_STATE" == none ]]; then
            echo "  ⚠ El usuario '$ADMIN_USER' no tiene contraseña (entra por SSH con clave)."
        else
            echo "  ⚠ La contraseña del usuario '$ADMIN_USER' está bloqueada."
        fi
        echo "    El panel pide usuario y contraseña de Linux: así no podrás iniciar sesión."
        if [[ -t 0 && -t 1 ]]; then
            read -r -p "    ¿Ponerle una contraseña ahora? [S/n] " REPLY || REPLY=n
            if [[ ! "$REPLY" =~ ^[Nn] ]]; then
                passwd "$ADMIN_USER" || echo "    ⚠ No se cambió. Hazlo después con: sudo passwd $ADMIN_USER"
            else
                echo "    Ponla cuando quieras con: sudo passwd $ADMIN_USER"
            fi
        else
            echo "    Ponle una con: sudo passwd $ADMIN_USER"
        fi
        echo
    fi
fi
