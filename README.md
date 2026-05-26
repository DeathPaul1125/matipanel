# 🐧 MatiPanel

> Administrador web ligero para servidores Linux — inspirado en Webmin/Cockpit, hecho con Python + Flask.

Una alternativa moderna y minimalista para administrar tu servidor Linux desde el navegador. Ideal para VPS, servidores headless, homelab y entornos institucionales pequeños.

![Status](https://img.shields.io/badge/status-alpha-orange)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Características

### Sistema
- **Dashboard en vivo** — CPU, memoria, disco, red, loadavg actualizados cada 3 s.
- **Procesos** — Vista estilo `htop`, filtrado, kill con confirmación.
- **Servicios systemd** — Start / stop / restart / enable / disable.
- **Discos** — Particiones, puntos de montaje, uso por filesystem.
- **Usuarios y grupos** — Listado de `/etc/passwd` y `/etc/group`.

### Red y seguridad
- **Red** — Interfaces, IPs, MAC, conexiones activas (`ss`-like).
- **Firewall** — Soporte para UFW, firewalld e iptables (autodetectado).
- **2FA con TOTP** — Compatible con Google Authenticator, Authy, FreeOTP, Aegis. Códigos de recuperación incluidos.

### Operaciones
- **Archivos** — Explorador con editor opcional (`MATIPANEL_FILE_WRITE=1`), backup automático.
- **Paquetes** — APT y DNF con detección de actualizaciones de seguridad.
- **Logs** — Consulta filtrada de `journalctl` + **vista en vivo** con WebSocket.
- **Docker** — Containers, images, volúmenes, logs en modal, start/stop/restart.
- **Terminal embebida** — xterm.js + PTY real, sesión interactiva como tu usuario.

### Multidistro
Detección automática y adaptación a:
- Debian, Ubuntu, Linux Mint, Pop!_OS (APT + UFW)
- RHEL, Rocky, AlmaLinux, CentOS, Fedora (DNF + firewalld)

## 🔐 Seguridad

- Login contra **PAM** (mismos usuarios del sistema, sin DB extra).
- Solo usuarios del grupo `matipanel` o `sudo` pueden ingresar.
- HTTPS con certificado autofirmado (generado en la instalación).
- Acciones privilegiadas vía `sudoers.d` con allowlist explícita de comandos.
- Cookies `HttpOnly` + `SameSite=Lax`, secret key persistente.
- Validación de nombres de unidad systemd para evitar inyección.
- Servicio corre como usuario `matipanel` sin privilegios.

## 📦 Requisitos

- Ubuntu 22.04+ / Debian 12+ (probado en Ubuntu 24.04 LTS)
- Python 3.10 o superior
- Acceso root para la instalación (solo una vez)

## 🚀 Instalación

```bash
git clone https://github.com/TU_USUARIO/matipanel.git
cd matipanel
sudo ./scripts/install.sh
```

El instalador:

1. Instala dependencias (`python3-venv`, `libpam0g-dev`, `openssl`).
2. Crea el usuario y grupo de servicio `matipanel`.
3. Copia los archivos a `/opt/matipanel`.
4. Configura un virtualenv con las dependencias Python.
5. Genera un certificado TLS autofirmado en `/etc/matipanel/`.
6. Instala la regla `sudoers.d` para acciones privilegiadas.
7. Habilita y arranca el servicio systemd.

Cuando termine, accede a:

```
https://IP-DEL-SERVIDOR:8443
```

El navegador advertirá sobre el certificado autofirmado — es esperado. Acepta la excepción y entra con tu usuario Linux.

## 👤 Dar acceso a un usuario

```bash
sudo usermod -aG matipanel <nombre-usuario>
```

El cambio toma efecto en el siguiente login.

## 🛠️ Desarrollo

```bash
git clone https://github.com/TU_USUARIO/matipanel.git
cd matipanel
./scripts/run-dev.sh
```

Levanta el servidor en `http://127.0.0.1:8080` (sin HTTPS, solo para desarrollo).

Si estás trabajando contra un servidor remoto, usa un túnel SSH:

```bash
ssh -L 8080:localhost:8080 usuario@servidor
```

## 📁 Estructura

```
matipanel/
├── app/
│   ├── __init__.py          # Flask factory
│   ├── auth.py              # Login con PAM
│   ├── modules/             # Un blueprint por módulo
│   │   ├── dashboard.py
│   │   ├── processes.py
│   │   ├── services.py
│   │   ├── disk.py
│   │   ├── users.py
│   │   ├── network.py
│   │   ├── firewall.py
│   │   ├── files.py
│   │   ├── packages.py
│   │   └── logs.py
│   ├── templates/           # Jinja2 templates
│   └── static/
│       ├── css/style.css
│       └── js/app.js
├── scripts/
│   ├── install.sh
│   ├── uninstall.sh
│   ├── run-dev.sh
│   └── matipanel.sudoers
├── systemd/
│   └── matipanel.service
├── wsgi.py
└── requirements.txt
```

## 🔧 Comandos útiles

```bash
# Estado del servicio
sudo systemctl status matipanel

# Logs en vivo
sudo journalctl -u matipanel -f

# Reiniciar tras cambios
sudo systemctl restart matipanel

# Desinstalar todo
sudo ./scripts/uninstall.sh
```

## 🗺️ Roadmap

- [x] ~~Editor de archivos con permisos write opcionales~~
- [x] ~~Gestión de Docker~~
- [x] ~~2FA con TOTP~~
- [x] ~~WebSocket para logs en vivo~~
- [x] ~~Soporte multidistro (RHEL/Rocky con `dnf` y `firewalld`)~~
- [x] ~~Terminal embebida (xterm.js + PTY)~~
- [ ] Edición visual de reglas UFW/firewalld
- [ ] Crontab GUI
- [ ] Sesiones SSH activas (`who`, `last`)
- [ ] Backups con borg/restic
- [ ] Nginx vhosts manager
- [ ] Let's Encrypt automation

## 📜 Licencia

MIT — ver [LICENSE](LICENSE).

---

Hecho con 💙 dedicado a **Matías Aceituno**.
