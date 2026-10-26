# Changelog

## [0.4.0] - 2026-10-26

### Añadido
- 📧 **Correo** — Postfix y Dovecot: instalación, configuración de `main.cf`, cola, alias, SSL y correo de prueba.
- 🌐 **DNS** — BIND 9: zonas primarias/inversas/secundarias, registros, SOA, reenviadores, `dig`.
- 📂 **Compartidos** — Samba (recursos, usuarios, sesiones) y NFS (exports).
- 🧱 **Almacenamiento** — LVM, RAID (mdadm) y cuotas de disco.
- 🛰️ **Multi-servidor** — monitorea otros MatiPanel con tokens de solo lectura y certificado anclado.
- 📖 **Ayuda** — 30 capítulos de documentación de usuario con índice, buscador y ayuda contextual (botón ?).
- 🎨 **Apariencia** — acento, barra lateral (azul oscuro por defecto), tamaño, densidad, bordes, tipografía y secciones del dashboard, por usuario.
- 📈 **Historial persistente** — métricas en SQLite durante 7 días, rangos de 5 min a 7 días, cortes visibles cuando el servidor estuvo apagado.
- 🧱 **Firewall** — instalar UFW con un clic, accesos rápidos para servicios comunes, reglas visibles con UFW inactivo.
- 📤 **Subida de archivos** estilo Webmin: arrastrar y soltar, carpetas, sobrescribir, extraer al finalizar.
- 💙 Dedicatoria a Matías Aceituno.

### Corregido
- 2FA falla cerrado si su archivo está dañado o falta `pyotp`; desactivarlo pide el código.
- La sesión caduca tras 1 hora sin actividad.
- Editor: avisa antes de descartar cambios y abre en solo lectura archivos que no son UTF-8.
- Cron ya no muestra comentarios de ejemplo como tareas; la retención de respaldos solo borra los automáticos.
- Varios detalles en Docker, Nginx, SSL, Logs, Terminal, Servicios, Procesos y Usuarios.

## [0.3.0] - 2026-10-26

### Añadido (módulos estilo Webmin)
- 🖥️ **Sistema** — hostname, zona horaria/NTP, sesiones, hardware, reiniciar/apagar, configs clave.
- ⏰ **Cron** — crontabs por usuario: crear, editar, activar/desactivar, borrar.
- 👥 **Usuarios y grupos** — crear/borrar, contraseñas, bloqueo, shells, membresías.
- 🌐 **Servidor web** — Apache/Nginx: sitios, asistente de virtual hosts, módulos, recarga con prueba previa.
- 🔒 **SSL / Let's Encrypt** — emitir, renovar y borrar certificados con certbot.
- 🐘 **PHP** — versiones, php.ini, extensiones, PHP-FPM y pools.
- 🗄️ **Bases de datos** — MariaDB/MySQL y PostgreSQL: bases, usuarios, permisos, consola SQL, respaldo/restauración.
- 💾 **Respaldos** — tar.gz de rutas, restauración y programación.
- 🧱 **Firewall** — gestión de reglas UFW y firewalld; soporte nftables (default en Debian 12+).
- 📦 **Paquetes** — buscar, instalar, quitar, actualizar; actualizaciones automáticas.
- 📁 **Archivos** — crear, renombrar, copiar, borrar, permisos, propietario, subir, descargar, comprimir/extraer.
- Servicios: habilitar/deshabilitar al arranque y ver estado. Logs: archivos de `/var/log`.

### Cambiado
- El servicio corre como root (como Webmin). Se eliminó el archivo sudoers, que daba root al grupo `matipanel`.
- PAM con servicio propio (`/etc/pam.d/matipanel`), ejecutado sin bloquear el worker.
- `install.sh <usuario>` agrega el usuario al grupo de acceso.
- Escritura en el explorador activa por defecto (`MATIPANEL_FILE_WRITE=0` la desactiva).
- xterm.js servido localmente (el CDN anterior devolvía 404).

### Seguridad
- Bloqueo tras 5 intentos fallidos de login/2FA.
- Verificación de `Origin` contra CSRF y WebSocket hijacking; cookie `Secure`.
- Corregido XSS en varias plantillas (nombres de archivo, procesos, etc.).
- Abrir `/security/2fa/setup` ya no desactiva un 2FA activo.
- La terminal nunca cae en un shell root si falla el cambio de usuario.
- Sacar a un usuario del grupo revoca su sesión de inmediato.

## [0.2.0] - 2026-05-26

### Añadido
- 🐳 **Módulo Docker** — Listado de containers/images/volumes, start/stop/restart/rm, logs en modal.
- 🔐 **2FA con TOTP** — Compatible con Google Authenticator, Authy, FreeOTP. Incluye 8 códigos de recuperación de un solo uso.
- 📡 **Logs en vivo vía WebSocket** — `journalctl -f` streameado al navegador.
- 💻 **Terminal embebida** — xterm.js + PTY real con el shell del usuario.
- ✏️ **Editor de archivos** — Modal de edición con guardado opt-in (`MATIPANEL_FILE_WRITE=1`). Backup automático `.matipanel.bak`.
- 🌐 **Soporte multidistro** — Detección automática Debian/Ubuntu vs RHEL/Rocky/Fedora.
  - APT y DNF para paquetes
  - UFW, firewalld e iptables para firewall
  - Instalador adaptado a ambas familias
- 🛡️ Página de Seguridad con setup wizard para 2FA.

### Cambiado
- Gunicorn ahora usa worker `gevent` (necesario para WebSockets).
- Unidad systemd con timeout de 86400s para sesiones de terminal largas.
- Permisos del directorio `/var/lib/matipanel` para almacenar secretos 2FA.

## [0.1.0] - 2026-05-26

### Añadido
- 🎉 Primera versión de MatiPanel.
- Dashboard con métricas en vivo (CPU, RAM, disco, red).
- Módulos: procesos, servicios systemd, discos, usuarios, red, firewall (UFW), archivos, paquetes APT, logs.
- Login contra PAM con verificación de grupo.
- HTTPS con certificado autofirmado.
- Tema claro y oscuro automático.
