# Introducción

MatiPanel es un panel web para administrar servidores Linux desde el navegador, al estilo de Webmin o Cockpit. Desde una sola pantalla puedes ver la salud del servidor, gestionar servicios, usuarios, archivos, sitios web, bases de datos, el firewall y mucho más, sin tener que recordar cada comando.

Esta ayuda explica cada pantalla del panel. Usa el buscador de la izquierda (o **Ctrl+K**) para encontrar un tema, o el botón **?** de la barra superior de cualquier pantalla para abrir directamente su ayuda.

## Qué puedes hacer con MatiPanel

| Área | Pantallas | Para qué sirve |
|---|---|---|
| Sistema | [Dashboard](10-dashboard.md), [Sistema](11-sistema.md), [Procesos](12-procesos.md), [Servicios](13-servicios.md), [Tareas (cron)](14-cron.md), [Paquetes](27-paquetes.md) | Ver el estado del servidor, reiniciarlo (también programado), procesos, servicios, tareas e instalar software |
| Archivos y discos | [Archivos](26-archivos.md), [Discos](15-discos.md), [Almacenamiento](53-almacenamiento.md), [Respaldos](16-respaldos.md), [Respaldos de apps](57-respaldos-apps.md), [Compartidos](52-compartidos.md) | Explorar y editar archivos, discos, LVM/RAID, respaldos locales y remotos, Samba/NFS |
| Red y acceso | [Usuarios y grupos](17-usuarios.md), [Permisos del panel](35-permisos.md), [Interfaces](18-red.md), [Tráfico de red](19-trafico.md), [Firewall](20-firewall.md), [DNS](51-dns.md), [Multi-servidor](54-multiservidor.md), [Seguridad del servidor](34-seguridad-servidor.md) | Cuentas y claves SSH, quién puede hacer qué en el panel, red y su tráfico, puertos, DNS, otros servidores y endurecimiento |
| Web y aplicaciones | [Servidor web](21-servidor-web.md), [SSL / Let's Encrypt](22-ssl.md), [PHP](23-php.md), [Despliegues](56-despliegues.md), [Stacks](59-stacks.md), [Docker](25-docker.md), [Correo](50-correo.md) | Sitios, certificados, PHP, apps desde GitHub, proyectos de Docker Compose, contenedores y correo |
| Bases de datos | [Bases de datos](24-bases-de-datos.md), [Explorador BD](55-explorador-bd.md) | MariaDB/PostgreSQL y un explorador estilo phpMyAdmin (también para las bases de las apps) |
| Herramientas | [Terminal](29-terminal.md), [Logs](28-logs.md), [Monitor](58-monitor.md), [Notificaciones](33-notificaciones.md), [Auditoría](32-auditoria.md) | Consola, registros, vigilancia de servicios de tu red, avisos por correo y quién hizo qué |
| Cuenta | [Seguridad (2FA)](30-seguridad-2fa.md), [Apariencia](31-apariencia.md) | Proteger tu acceso y personalizar el panel |

## Cómo funciona por dentro

- MatiPanel corre en el propio servidor como un servicio llamado `matipanel` y se abre en `https://IP-DEL-SERVIDOR:8443`.
- El servicio se ejecuta como **root**, igual que Webmin, porque necesita administrar todo el sistema. Por eso el acceso está muy protegido (ver más abajo).
- Cada acción del panel ejecuta los mismos comandos que usarías en la consola (`systemctl`, `apt`, `ufw`, `useradd`…). Lo que hagas en el panel es igual a hacerlo a mano.
- La conexión va cifrada con HTTPS usando un certificado autofirmado que se crea al instalar.

## Quién puede entrar

Entras con **tu usuario y contraseña de Linux** del servidor; no hay cuentas aparte. Solo pueden ingresar los usuarios que pertenecen a alguno de estos grupos:

| Grupo | Uso habitual |
|---|---|
| `matipanel` | Grupo propio del panel. El instalador agrega aquí al usuario que indiques. |
| `sudo` | Administradores en Debian y Ubuntu. |
| `wheel` | Administradores en RHEL, Rocky, Alma y Fedora. |
| `admin` | Administradores en algunas distribuciones antiguas. |

> **Importante:** el usuario `root` no puede entrar directamente al panel. Usa tu cuenta personal que esté en uno de esos grupos.

Si sacas a alguien de esos grupos, su sesión se cierra en la siguiente acción que haga en el panel.

Qué puede **hacer** cada persona dentro del panel (todo, solo mirar, solo algunos módulos) se elige en [Permisos del panel](35-permisos.md).

## Seguridad del panel

- **Bloqueo por intentos:** tras 5 intentos fallidos de inicio de sesión (o de código 2FA) desde la misma IP, el acceso se bloquea 5 minutos.
- **Doble factor (2FA):** puedes exigir un código de Google Authenticator, Aegis, Authy, etc. Ver [Seguridad (2FA)](30-seguridad-2fa.md).
- **Protección contra sitios maliciosos:** el panel rechaza peticiones que vengan de otras páginas web (CSRF) y conexiones de terminal desde otros orígenes.
- **Permisos y auditoría:** cada usuario tiene un rol (administrador, solo lectura, módulos concretos) y todo lo que se hace queda en la [Auditoría](32-auditoria.md).
- **Terminal sin privilegios:** la terminal web abre una sesión con tu propio usuario, nunca como root.
- **Confirmaciones:** las acciones destructivas piden confirmación, y las más delicadas (borrar una base de datos, reiniciar el servidor) te piden escribir el nombre para habilitar el botón.

> **Consejo:** no publiques el puerto 8443 en internet. Limítalo a tu red interna o a una VPN con el [Firewall](20-firewall.md).

## Sistemas compatibles

- **Recomendado:** Debian 12 y 13, Ubuntu 22.04 o superior.
- **Compatible con limitaciones:** RHEL, Rocky, AlmaLinux, CentOS y Fedora. La instalación funciona, pero algunos módulos (servidor web, PHP, actualizaciones automáticas) asumen la estructura de Debian.

## Ver también

- [Instalación y actualización](02-instalacion.md)
- [Primeros pasos](03-primeros-pasos.md)
- [Solución de problemas](90-solucion-de-problemas.md)
