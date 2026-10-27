# Changelog

Versionado semántico: MAYOR.MENOR.PARCHE. Cada versión indica su fecha de publicación.

## [0.10.0] - 2026-10-27

### Añadido
- ↔️ **Colapsar el menú a un clic** — botón en la barra superior (en cualquier pantalla) que deja el menú solo con iconos y lo vuelve a expandir; se recuerda por usuario.
- ⛶ **Pantalla completa en el Explorador BD** — oculta el menú y la barra superior y usa toda la pantalla para el árbol, los datos, las consultas y el diagrama; se sale con **Salir** o **Esc**.

## [0.9.0] - 2026-10-27

### Añadido
- ✉️ **Correos con diseño** — cada aviso llega en HTML (y en texto para los programas que no lo muestran): franja y símbolo de color según el tipo (problema, aviso, correcto, información, también en el asunto), tabla de detalles, el registro del error en un bloque aparte y botón **Abrir en MatiPanel** a la pantalla correspondiente.
- 👁️ **Vista previa** — en Notificaciones, «Ver ejemplo» muestra cómo llega cada aviso y permite enviarlo de prueba.
- 🔔 **Avisos nuevos**:
  - El servidor se reinició (cuánto estuvo fuera de servicio).
  - Certificados SSL por vencer o vencidos (Let's Encrypt y el del panel).
  - Inicio de sesión en el panel desde una IP nueva y, opcionalmente, cada inicio de sesión.
  - Inicios de sesión por SSH en el servidor.
  - Cambios delicados hechos en el panel: usuarios, permisos, firewall, SSH, apagado, borrados y restauraciones, con quién y desde dónde.
  - Activar o desactivar el 2FA.
  - Respaldos programados de carpetas, correctos o fallidos.
  - **Resumen diario** a la hora elegida: estado, recursos, respaldos, despliegues, actualizaciones, monitor y seguridad de las últimas 24 h.
- 🐳 La barra de estado (y el aviso de problemas) incluye los contenedores de Docker que se reinician sin parar, se detienen con error o no responden.

### Cambiado
- Los avisos de despliegues, respaldos, monitor, actualizaciones, bloqueos y versión nueva traen sus datos en tablas; las actualizaciones de seguridad van en su propia tabla y en rojo.
- La pantalla de Notificaciones agrupa los avisos (Servidor, Seguridad, Apps y respaldos, Resumen).

## [0.8.0] - 2026-10-27

### Añadido
- 🆘 **Recuperación ante desastres** — en Respaldos de apps: con un MatiPanel recién instalado y el mismo servidor de respaldos, lista las apps respaldadas y las recrea con un clic: configuración original, base de datos y archivos restaurados **antes** del primer despliegue (las migraciones corren sobre los datos recuperados) y despliegue desde GitHub. Si el puerto está ocupado asigna otro; si algo falla deshace todo para reintentar. Guía paso a paso en la Ayuda.
- 🔑 El respaldo de configuración incluye la clave de despliegue de GitHub, para volver a clonar repositorios privados en otro servidor.

### Seguridad
- La configuración recuperada se lee en memoria (sin extraer rutas ni enlaces del archivo) y su `app.json` se valida campo por campo antes de crear nada.

### Cambiado
- Un respaldo incompleto o borrado muestra un mensaje claro en vez del error de `cat`.

## [0.7.0] - 2026-10-27

### Añadido
- 🪟 **Respaldos de apps en Windows** — carpetas compartidas (SMB) de Windows Server o NAS con una cuenta del Active Directory (`DOMINIO\usuario`), cifrado SMB 3, prueba de conexión que escribe un archivo real y mensajes claros para cada error (credenciales, recurso, firewall, SMB antiguo).
- 🗂️ **Varios servidores de respaldo** — Linux/Unix por SSH y Windows/NAS por SMB a la vez; cada app elige su servidor y la lista de respaldos permite ver, restaurar y eliminar en cualquiera de ellos. La configuración anterior se migra sola como primer servidor.

### Seguridad
- La contraseña de la cuenta de Windows se guarda solo en el servidor (0600), nunca vuelve al navegador ni a la Auditoría, y se entrega a `mount.cifs` en un archivo temporal, no en la línea de órdenes.
- Sobre la carpeta compartida montada, las órdenes se ejecutan como `nobody`: un enlace simbólico puesto en el servidor remoto no puede leer ni escribir archivos de root de este equipo.

### Cambiado
- El instalador agrega `cifs-utils`.

## [0.6.0] - 2026-10-27

### Añadido
- 🎨 **Estilo «Moderno»** — en Apariencia → Estilo, un aspecto nuevo para toda la interfaz inspirado en TailAdmin: menú claro, barra superior blanca, tarjetas con sombra suave y esquinas amplias, tablas con cabecera tintada, controles segmentados y versión oscura en azul noche. Se elige por usuario, se puede volver a «Clásico» en un clic y respeta acento, tema, tamaño, densidad y bordes.

## [0.5.1] - 2026-10-26

### Cambiado
- 💙 **Favicon nuevo** — la «M» de Matías con un corazón azul, en vectores (nítido en pestañas claras y oscuras), con versiones PNG/ICO para todos los navegadores y el ícono de acceso directo en móviles; ahora también en las pantallas de inicio de sesión.

## [0.5.0] - 2026-10-26

### Añadido
- 🏷️ **Versión e historial de actualizaciones** — «Acerca de» muestra la versión, el commit, la fecha de instalación, cada actualización y las novedades; la actualización queda en la Auditoría y avisa por correo.
- 🔗 **Diagrama: relaciones deducidas y lista de relaciones** — para bases sin claves foráneas declaradas (Laravel/PHP), con prefijos de tablas; líneas visibles con cualquier zoom, menos cruces y tabla de relaciones con clic para ubicarlas.
- 🗺️ **Diagrama entidad-relación** — pestaña «Diagrama» en el Explorador BD (MariaDB/MySQL y PostgreSQL): tablas con PK/FK, relaciones, distribución automática padre→hijo, mover, zoom, búsqueda, solo columnas clave y exportación a SVG/PNG.
- 📦 **Aviso de actualizaciones** — revisión horaria de paquetes (APT/DNF) con correo cuando aparecen versiones nuevas, las de seguridad primero; cada versión se avisa una sola vez y la barra de estado indica las de seguridad pendientes.
- 📡 **Monitor de servicios** — comprueba URLs (HTTP/HTTPS con código y palabra clave), puertos TCP y equipos (ping) de tu red; disponibilidad 24 h/7 d/30 d, latencia, incidentes, aviso por correo y en la barra de estado.
- 🛡️ **Seguridad del servidor** — chequeo con semáforo y puntuación (SSH, firewall, actualizaciones de seguridad, reinicio pendiente, puertos expuestos, cuentas) con correcciones validadas y gestión de fail2ban (jaula SSH, bloquear y desbloquear IPs).
- 🔐 **Permisos del panel** — roles por usuario: administrador, solo lectura (sin terminal, archivos ni datos), módulos concretos o sin acceso; el menú muestra solo lo permitido.
- 🔑 **Claves SSH** — en Usuarios y grupos: ver, agregar y quitar las claves autorizadas de cada usuario, con huella SHA-256 y escritura segura frente a enlaces simbólicos.
- 🧱 **Stacks** — proyectos de Docker Compose propios con plantillas, validación, avisos de configuraciones peligrosas, logs y acciones.
- 🖥️ **Sistema renovado** — resumen del equipo, reloj en vivo, servidor NTP, aviso de reinicio pendiente, reinicio/apagado programado y cancelable, sesiones en tablas, intentos fallidos de SSH por IP y detalle de procesador y dispositivos.
- 🧪 **Pruebas en el repositorio** — `python tests/run.py` corre todas las pruebas y auto-pruebas (Windows y Linux, nunca como root).
- 🧾 **Auditoría** — registro de quién hizo qué, cuándo y desde dónde (acciones, descargas sensibles, inicios de sesión, terminales), sin secretos, con filtros y exportación CSV; se conserva un año.
- ✉️ **Notificaciones por correo** — SMTP propio; avisos de problemas del servidor (y cuando se resuelven), despliegues, respaldos y bloqueos de inicio de sesión, con recordatorios opcionales.
- 🗄️ **Respaldos de apps** — base de datos, volúmenes y configuración de cada app desplegada a otro servidor Linux por SSH, programados (diario/semanal/6 h), con retención, verificación SHA-256 y restauración.
- 📶 **Tráfico de red** — ancho de banda por interfaz en vivo y con historial de 7 días, quién consume (equipos, servicios, procesos y conversaciones con tcpdump), capturas .pcap para Wireshark y alertas de uso y errores en la barra de estado.
- 📋 **Tablas** — filtro, orden por columna y paginador (10 por página por defecto) en las tablas del panel.
- 🐳 **Docker por proyecto** — contenedores agrupados por aplicación o proyecto de Compose (app, base de datos, Redis…), con nombres propios para grupos y contenedores.
- 🗄️ **Explorador BD para apps desplegadas** — selector «Servidor» para gestionar la base del contenedor de cada aplicación (MariaDB o PostgreSQL), con enlace desde el Resumen de la app.
- 🔀 **Nombre interno con Apache** — si Apache ocupa el puerto 80, el panel crea un sitio en Apache que reenvía el nombre interno a la app.
- 🐘 **Despliegues de PHP genérico** — tipo de proyecto «PHP» para frameworks que no son Laravel (Quetzal/Bee, PHP puro): Apache con `.htaccess`, carpeta pública, ruta del `.env`, carpetas persistentes y comando de migraciones propio. También en Docker, sin cortes.

### Seguridad (auditoría con security-review)
- Archivos: chmod y chown ya no siguen un enlace simbólico puesto entre la comprobación y el cambio (se opera sobre el descriptor abierto).
- Compartidos: la carpeta nueva de Samba/NFS se ajusta por descriptor; un enlace puesto justo tras crearla ya no desvía el chown/chmod.
- Sin sesión: se rechaza el cuerpo chunked (saltaba el tope de 64 KB) y el cuerpo se lee con tiempo máximo de 15 s; gunicorn admite 1000 conexiones.
- Login y 2FA: el intento se cuenta antes de leer el formulario (peticiones en paralelo ya no esquivan el límite).
- Webhook: una firma con caracteres no ASCII da 403 en vez de 500 (revelaba qué apps existen).
- Despliegues: claves de host de GitHub precargadas en `known_hosts`; los logs de contenedores ocultan los secretos del `.env`.
- Bases de datos: la restauración MySQL se niega si el cliente no soporta `--sandbox`.
- Despliegues: si Nginx no arranca al instalarlo, se muestra la causa (puerto 80 ocupado, `nginx -t`, journal).

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
