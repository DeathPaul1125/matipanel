# Seguridad del servidor

Revisa de un vistazo lo más importante para proteger el servidor: la configuración de SSH, el firewall, las actualizaciones de seguridad, si hace falta reiniciar, qué puertos están abiertos a la red, las cuentas con privilegios y fail2ban. Cada punto lleva un semáforo, una explicación y, cuando se puede, un botón **Corregir…**. Lo encuentras en **Menú → Red y acceso → Seguridad del servidor**.

## Resumen y puntuación

Arriba verás una **puntuación de 0 a 100** y cuántas comprobaciones están en verde, con aviso o con problema. Cada comprobación en verde suma 1 punto, cada aviso medio punto y cada problema 0; las filas informativas (gris) no cuentan.

| LED | Significado |
|---|---|
| Verde | Correcto. |
| Ámbar | Aviso: conviene revisarlo. |
| Rojo | Problema: corrígelo cuanto antes. |
| Gris | Información, sin valoración (por ejemplo, el puerto de SSH o la lista de administradores). |

Pulsa **Actualizar** para volver a analizar después de un cambio hecho fuera del panel.

> **Nota:** La puntuación es orientativa. Un 100 no significa que el servidor sea invulnerable, sino que los puntos básicos están cubiertos.

## Comprobaciones

| Comprobación | Qué mira | Recomendación |
|---|---|---|
| **SSH: acceso directo de root** | `PermitRootLogin` en la configuración efectiva de SSH (`sshd -T`). | `no`, o «Solo con llave SSH» (`prohibit-password`). |
| **SSH: inicio de sesión con contraseña** | `PasswordAuthentication`, y qué administradores tienen llaves en `~/.ssh/authorized_keys`. | `no`, una vez comprobado que entras con tu llave. |
| **SSH: puerto** | `Port`. | Informativo: cambiarlo solo reduce el ruido de los bots. |
| **SSH: intentos por conexión** | `MaxAuthTries`. | 3 o 4. |
| **SSH: reenvío X11** | `X11Forwarding`. | `no`. |
| **Firewall activo** | UFW, firewalld o nftables activos. | Botón **Ir a Firewall**. |
| **Actualizaciones de seguridad** | Paquetes por actualizar y cuántos vienen del repositorio `-security`. | Aplicarlas en **Paquetes**. |
| **Actualizaciones automáticas de seguridad** | Si `unattended-upgrades` está instalado y activado. | **Corregir…** lo instala y lo activa. |
| **Reinicio pendiente** | `/var/run/reboot-required` y los paquetes que lo piden. | Reiniciar en una ventana de mantenimiento. |
| **Puertos abiertos a la red** | Puertos que escuchan fuera de `localhost`. | Ver la tabla de puertos. |
| **Otras cuentas con UID 0** | Cuentas distintas de root con poderes de root. | Suelen ser una puerta trasera: elimínalas. |
| **Cuentas sin contraseña** | Cuentas con la contraseña vacía en `/etc/shadow`. | Asignar contraseña o bloquear la cuenta. |
| **Administradores (sudo/wheel)** | Miembros de los grupos `sudo`, `wheel` y `admin`. | Que solo estén quienes deben administrar. |
| **fail2ban** | Si está instalado, en marcha y protegiendo SSH. | Instalarlo y activar la protección SSH. |

> **Nota:** La lista de actualizaciones se basa en el último `apt update`. Si acabas de instalar el servidor, pulsa **Actualizar listas** en **Paquetes** primero.

## Corregir la configuración de SSH

1. Pulsa **Corregir…** (o **Ajustar…**) en cualquiera de las filas de SSH.
2. Cambia solo lo que quieras: acceso de root, contraseñas, puerto, intentos por conexión o reenvío X11.
3. Pulsa **Aplicar**.

El panel escribe **solo los valores que cambiaste** en `/etc/ssh/sshd_config.d/00-matipanel.conf`, valida la configuración completa con `sshd -t` y, si es correcta, recarga SSH. Si `sshd -t` encuentra un error, se restaura el archivo anterior y SSH no se toca. Recargar SSH **no corta las sesiones abiertas**.

> **Importante:** Mantén abierta tu sesión SSH actual mientras haces cambios y, antes de cerrarla, abre **otra** terminal y comprueba que puedes entrar. Si algo falla, desde la sesión abierta todavía puedes deshacerlo.

### Desactivar las contraseñas

Al elegir **No** en «Inicio de sesión con contraseña», el panel te muestra qué administradores tienen llaves SSH y te pide escribir **DESACTIVAR** para confirmar. También se desactiva `KbdInteractiveAuthentication`, que de otro modo seguiría pidiendo contraseña a través de PAM.

> **Importante:** Si ningún administrador tiene llave, o tu llave no funciona, **te quedarás fuera del servidor** y solo podrás entrar por la consola física o la consola web de tu proveedor. Antes de desactivar las contraseñas:
>
> 1. Crea una llave en tu equipo (`ssh-keygen -t ed25519`).
> 2. Cópiala al servidor (`ssh-copy-id usuario@servidor`).
> 3. Comprueba que entras **sin** que te pida contraseña.

### Cambiar el puerto

> **Importante:** Abre el nuevo puerto en el [Firewall](20-firewall.md) **antes** de aplicar el cambio. Si no, la próxima conexión SSH quedará bloqueada. En RHEL/Rocky con SELinux, además hay que permitir el puerto con `semanage port -a -t ssh_port_t -p tcp <puerto>`. En Ubuntu con activación por socket (`ssh.socket`), el puerto lo define el socket: tras el cambio ejecuta `systemctl daemon-reload && systemctl restart ssh.socket`.

### «Guardado, pero sshd sigue usando otro valor»

SSH usa el **primer** valor que encuentra. Si otro archivo de `/etc/ssh/sshd_config.d/` con un nombre que se ordena antes (por ejemplo `50-cloud-init.conf` en servidores en la nube) o una línea de `sshd_config` anterior al `Include` define la misma opción, gana sobre la del panel. El panel lo detecta y te dice qué opciones no se aplicaron: edita o elimina ese otro archivo desde la terminal.

## Puertos a la escucha

La tabla muestra cada puerto TCP en escucha y cada puerto UDP abierto, con su dirección y el proceso que lo usa:

| Alcance | Significado |
|---|---|
| **Todas las interfaces** | Escucha en `0.0.0.0`, `::` o `*`: cualquiera que llegue al servidor puede conectarse, salvo que el firewall lo impida. |
| **IP concreta** | Escucha solo en una dirección del servidor (por ejemplo, la de la red interna). |
| **Solo local** | Escucha en `127.0.0.1` o `::1`: solo los programas del propio servidor pueden conectarse. |

> **Consejo:** Una base de datos (MariaDB, PostgreSQL, Redis) que solo usan las aplicaciones del propio servidor debería aparecer como **Solo local**.

## Cuentas relevantes

Muestra root, los usuarios con acceso de consola (UID 1000 o mayor) y cualquier cuenta con UID 0, sin contraseña o en `sudo`/`wheel`. Para cada una: su shell, privilegios, estado de la contraseña (**Definida**, **Bloqueada** o **Vacía**) y cuántas llaves SSH tiene. Los hashes de las contraseñas **nunca salen del servidor**. Para cambiar contraseñas o grupos usa [Usuarios y grupos](17-usuarios.md).

## fail2ban

fail2ban vigila los registros y bloquea durante un tiempo las IPs que fallan demasiadas veces al entrar por SSH.

- **Instalar fail2ban** (Debian/Ubuntu): lo instala con `apt-get`.
- **Activar / Ajustar protección SSH…**: define la jail `sshd` en `/etc/fail2ban/jail.d/matipanel.conf`:

| Campo | Ejemplo | Significado |
|---|---|---|
| **Tiempo de bloqueo (bantime)** | `1h` | Cuánto dura el bloqueo. `-1` = para siempre. |
| **Ventana de fallos (findtime)** | `10m` | Periodo en que se cuentan los fallos. |
| **Intentos fallidos permitidos (maxretry)** | `5` | Fallos dentro de la ventana que provocan el bloqueo (1 a 100). |
| **No bloquear nunca** | `192.168.1.0/24` | IPs o redes que nunca se bloquean; viene con tu IP actual. `127.0.0.1` y `::1` se añaden siempre. |

Los tiempos aceptan segundos (`600`) o un número con `s`, `m`, `h`, `d` o `w`. El panel usa el puerto efectivo de SSH, valida con `fail2ban-client -t` y recarga fail2ban (o lo arranca y lo activa si estaba detenido). Si la validación falla, se restaura el archivo anterior.

Con fail2ban en marcha verás:

- **Jails**: fallos actuales y totales, y bloqueos actuales y totales de cada una.
- **IPs bloqueadas**: con el botón **Desbloquear** para levantar el bloqueo al momento.
- **Bloquear una IP a mano**: elige la jail, escribe la IP y pulsa **Bloquear**. El bloqueo dura lo que indique el `bantime` de esa jail. Por seguridad no puedes bloquear tu propia IP ni la dirección local.

> **Consejo:** Si te bloqueaste a ti mismo desde otro equipo, entra desde una IP distinta (o por la consola del proveedor) y pulsa **Desbloquear** en la IP afectada. Añade tu red de administración en **No bloquear nunca** para que no vuelva a pasar.

## Preguntas frecuentes

### ¿El panel modifica mi `sshd_config`?

No. Solo escribe sus propios archivos (`/etc/ssh/sshd_config.d/00-matipanel.conf`, `/etc/fail2ban/jail.d/matipanel.conf` y `/etc/apt/apt.conf.d/20auto-upgrades`). Para volver a la configuración original basta con borrar el archivo correspondiente y recargar el servicio.

### Me quedé sin acceso por SSH, ¿qué hago?

Entra por la consola física o la consola web de tu proveedor (o por la **Terminal** de este panel, si sigue accesible), borra `/etc/ssh/sshd_config.d/00-matipanel.conf` y ejecuta `systemctl reload ssh` (`sshd` en RHEL).

### ¿Por qué el puerto de SSH sale en gris y no en verde?

Porque cambiar el puerto no protege por sí mismo: solo reduce el ruido de los bots. Lo importante son las llaves, el firewall y fail2ban.

## Ver también

- [Firewall](20-firewall.md): activar el firewall y abrir o cerrar puertos.
- [Paquetes](27-paquetes.md): aplicar las actualizaciones pendientes.
- [Usuarios y grupos](17-usuarios.md): contraseñas, bloqueo de cuentas y grupos.
- [Seguridad y 2FA](30-seguridad-2fa.md): proteger el acceso al propio panel.
