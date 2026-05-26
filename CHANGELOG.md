# Changelog

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
