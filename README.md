# 🐧 MatiPanel

> Administrador web para servidores Linux — inspirado en Webmin/Cockpit, hecho con Python + Flask.

Administra tu servidor Linux desde el navegador: sistema, usuarios, servicios, archivos, web, PHP, bases de datos, firewall, certificados y más. Pensado para Debian 12 (también Ubuntu y, parcialmente, RHEL/Rocky).

![Status](https://img.shields.io/badge/status-beta-orange)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Módulos

| Área | Qué puedes hacer |
|------|------------------|
| **Dashboard** | CPU, memoria, disco, red y carga en vivo |
| **Sistema** | Hostname, zona horaria/NTP, sesiones (`who`, `last`), hardware, reiniciar/apagar, editar configs clave (hosts, resolv, sshd) |
| **Procesos** | Lista estilo `htop`, filtro, kill |
| **Servicios** | systemd: iniciar, detener, reiniciar, habilitar/deshabilitar al arranque, ver estado |
| **Tareas (cron)** | Crontabs por usuario: crear, editar, activar/desactivar, borrar |
| **Discos** | Particiones y uso |
| **Respaldos** | Backups `.tar.gz` de rutas, descarga, restauración y programación |
| **Usuarios y grupos** | Crear/borrar usuarios, contraseñas, bloqueo, shells, grupos y membresías |
| **Red** | Interfaces y conexiones activas |
| **Firewall** | UFW (reglas, políticas), firewalld (puertos/servicios), nftables |
| **Servidor web** | Apache y Nginx: sitios, crear virtual hosts, activar/desactivar, módulos, recarga segura |
| **SSL / Let's Encrypt** | Certificados con certbot: emitir, renovar, borrar |
| **PHP** | Versiones, php.ini, extensiones, PHP-FPM y pools |
| **Bases de datos** | MariaDB/MySQL y PostgreSQL: bases, usuarios, permisos, consola SQL, respaldo/restauración |
| **Docker** | Contenedores, imágenes, volúmenes, logs |
| **Archivos** | Navegar, editar, crear, renombrar/mover, copiar, borrar, permisos, propietario, subir, descargar, comprimir/extraer |
| **Paquetes** | Buscar, instalar, quitar, actualizar (APT), actualizaciones automáticas |
| **Logs** | journalctl, archivos de `/var/log` y vista en vivo |
| **Terminal** | Shell real en el navegador como tu usuario |
| **Seguridad** | 2FA TOTP (Google Authenticator, Aegis…) con códigos de recuperación |

## 🔐 Seguridad

- El servicio corre como **root** (igual que Webmin), porque administra el sistema completo. Por eso el acceso está muy restringido:
- Login contra **PAM** con tus usuarios Linux. Solo entran los miembros de los grupos `matipanel`, `sudo`, `wheel` o `admin`.
- Bloqueo temporal tras 5 intentos fallidos (login y 2FA). 2FA TOTP opcional por usuario.
- HTTPS con certificado autofirmado, cookies `Secure` + `HttpOnly` + `SameSite=Lax`, verificación de `Origin` contra CSRF y WebSocket hijacking.
- La terminal baja a los privilegios de tu usuario (nunca abre un shell root).
- Todos los comandos se ejecutan sin shell y con validación de parámetros.

> Recomendación: no expongas el puerto 8443 a internet. Limítalo a tu red o VPN con el firewall.

## 📦 Requisitos

- Debian 12+ (recomendado) o Ubuntu 22.04+. RHEL/Rocky/Fedora: instalación soportada, algunos módulos son solo Debian.
- Acceso root para instalar.
- Conexión a internet en el servidor durante la instalación (descarga paquetes Python).

## 🚀 Instalación en Debian

**1. Copia el proyecto al servidor.** Desde tu PC (Windows con PowerShell o Linux):

```bash
scp -r matipanel usuario@IP-DEL-SERVIDOR:/tmp/
```

O, si lo subes a un repositorio git, en el servidor: `git clone <url> /tmp/matipanel`.

**2. Conéctate al servidor y entra como root:**

```bash
ssh usuario@IP-DEL-SERVIDOR
su -            # o: sudo -i
```

**3. Ejecuta el instalador** indicando tu usuario Linux, el que usarás para entrar al panel:

```bash
cd /tmp/matipanel
bash scripts/install.sh usuario
```

El instalador:

1. Instala dependencias (`python3-venv`, compiladores, `openssl`).
2. Crea el grupo `matipanel` y agrega a tu usuario.
3. Copia la app a `/opt/matipanel` y crea un virtualenv con las dependencias.
4. Genera un certificado TLS autofirmado en `/etc/matipanel/`.
5. Configura PAM (`/etc/pam.d/matipanel`).
6. Instala y arranca el servicio systemd `matipanel`.

Usa otro puerto con `MATIPANEL_PORT=9443 bash scripts/install.sh usuario`.

**4. Abre el puerto en el firewall** (si tienes uno activo):

```bash
ufw allow 8443/tcp
```

**5. Entra desde el navegador:**

```
https://IP-DEL-SERVIDOR:8443
```

El navegador advertirá sobre el certificado autofirmado: es lo esperado. Acepta la excepción y entra con tu usuario y contraseña de Linux.

### Si algo falla

```bash
systemctl status matipanel          # ¿está corriendo?
journalctl -u matipanel -n 100      # errores del servicio
```

- **"Usuario o contraseña incorrectos"**: verifica que el usuario esté en el grupo (`id usuario`). Si lo acabas de agregar, ya funciona: no hace falta reiniciar.
- **No carga la página**: revisa el firewall (`ufw status`) y que el puerto sea el correcto.

### Actualizar

Copia la nueva versión al servidor y vuelve a ejecutar `bash scripts/install.sh`. Se conservan el certificado, los secretos 2FA y la configuración.

### Desinstalar

```bash
bash scripts/uninstall.sh
```

## 👤 Dar acceso a otro usuario

```bash
usermod -aG matipanel <usuario>
```

Para quitarlo: `gpasswd -d <usuario> matipanel`. Su sesión se cierra en la siguiente petición.

## ⚙️ Opciones

Se configuran con `systemctl edit matipanel`:

```ini
[Service]
Environment="MATIPANEL_FILE_WRITE=0"   # explorador de archivos en solo lectura
```

## 🛠️ Desarrollo

```bash
./scripts/run-dev.sh
```

Levanta el servidor en `http://127.0.0.1:8080`, sin HTTPS y como tu usuario: las funciones que requieren root fallarán. Para trabajar contra un servidor remoto, usa un túnel: `ssh -L 8080:localhost:8080 usuario@servidor`.

## 📁 Estructura

```
matipanel/
├── app/
│   ├── __init__.py       # Flask factory, CSRF por Origin
│   ├── auth.py           # Login PAM + 2FA + rate limit
│   ├── totp.py           # Secretos TOTP y códigos de recuperación
│   ├── distro.py         # Detección de distro / gestor de paquetes / firewall
│   ├── util.py           # Helpers compartidos (run, validación, rate limiter)
│   ├── modules/          # Un blueprint por módulo
│   ├── templates/        # Jinja2
│   └── static/           # CSS, JS, xterm.js
├── scripts/              # install.sh, uninstall.sh, run-dev.sh
├── systemd/matipanel.service
├── wsgi.py
└── requirements.txt
```

## 🗺️ Roadmap

- [ ] Gestión de correo (Postfix/Dovecot)
- [ ] DNS (BIND)
- [ ] Samba / NFS
- [ ] Cuotas de disco y RAID/LVM
- [ ] Multi-servidor

## 📜 Licencia

MIT — ver [LICENSE](LICENSE).

---

Hecho con 💙 dedicado a **Matías Aceituno**.
