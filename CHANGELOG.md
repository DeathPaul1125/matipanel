# Changelog

Todos los cambios notables a MatiPanel se documentan aquí.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es/1.1.0/)
y este proyecto adhiere a [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-26

### Añadido
- 🎉 Primera versión de MatiPanel.
- Dashboard con métricas en vivo (CPU, RAM, disco, red).
- Módulo de procesos con filtrado y kill.
- Módulo de servicios systemd con acciones start/stop/restart.
- Módulo de discos y particiones.
- Módulo de usuarios y grupos.
- Módulo de red (interfaces + conexiones).
- Módulo de firewall (UFW, solo lectura).
- Módulo de explorador de archivos (read-only).
- Módulo de paquetes APT con detección de actualizaciones de seguridad.
- Módulo de logs vía journalctl.
- Login contra PAM con verificación de grupo.
- HTTPS con certificado autofirmado en la instalación.
- Script de instalación/desinstalación automatizado.
- Servicio systemd con hardening (NoNewPrivileges, ProtectSystem, etc.).
- Tema claro y oscuro automático según preferencia del sistema.
