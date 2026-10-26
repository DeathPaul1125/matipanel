# Sistema

El módulo Sistema reúne la configuración básica del servidor: nombre del host, fecha y hora, reinicio y apagado, sesiones de usuarios, hardware y los archivos de configuración más comunes. Lo encuentras en **Menú → Sistema → Sistema**.

## Qué muestra el resumen superior

Arriba de las pestañas hay cuatro recuadros:

| Recuadro | Valor principal | Detalle inferior |
|---|---|---|
| **Hostname** | nombre del servidor | sistema operativo |
| **Kernel** | versión del kernel | arquitectura y tiempo encendido (`arriba 3d 4h 10m`) |
| **CPU** | número de núcleos | modelo del procesador |
| **Memoria** | usada / total | uso de swap |

Debajo están las pestañas **General**, **Sesiones**, **Hardware** y **Configuración**.

## Cambiar el nombre del servidor (hostname)

1. Abre la pestaña **General**.
2. En la tarjeta **Hostname**, escribe el nuevo nombre.
3. Haz clic en **Cambiar**.
4. Confirma en el diálogo **Cambiar hostname** con el botón **Cambiar**.

Verás el aviso "Hostname actualizado". El panel hace dos cosas:

- Ejecuta `hostnamectl set-hostname`.
- Reescribe la línea `127.0.1.1` de `/etc/hosts` con el nombre nuevo (y su forma corta, si usas un nombre con puntos). Antes guarda una copia en `/etc/hosts.matipanel.bak`.

### Reglas del nombre

- Solo letras, números, guiones (`-`) y puntos (`.`).
- Cada parte entre puntos tiene de 1 a 63 caracteres y no puede empezar ni terminar en guion.
- Máximo 253 caracteres en total.
- Ejemplos válidos: `srv-web01`, `srv-web01.senabed.gob.gt`.

> **Nota:** Si el cambio de hostname funciona pero `/etc/hosts` no se puede escribir, verás un diálogo **Atención** que lo explica. El nombre sí quedó cambiado; revisa `/etc/hosts` a mano desde [Archivos](26-archivos.md).

> **Consejo:** La línea de comandos de las sesiones SSH abiertas sigue mostrando el nombre anterior hasta que vuelvas a conectarte.

## Cambiar la zona horaria

1. En la pestaña **General**, ubica la tarjeta **Fecha y hora**.
2. En el campo de zona (ejemplo: `America/Guatemala`), empieza a escribir. El navegador te sugiere las zonas válidas.
3. Haz clic en **Cambiar zona**.
4. Confirma con **Cambiar**.

La zona debe existir en la lista de `timedatectl list-timezones`. Si escribes una zona que no existe, verás "Zona horaria desconocida". Para Guatemala usa `America/Guatemala`.

> **Importante:** La zona horaria afecta a cron. Las tareas programadas y los respaldos automáticos se ejecutan según la hora local del servidor.

> **Nota:** El valor de **Hora local** se lee al abrir la página; no avanza en vivo. Recarga la página para verlo actualizado.

## Activar o desactivar la sincronización NTP

NTP mantiene el reloj del servidor en hora usando servidores de internet.

1. En la tarjeta **Fecha y hora**, revisa la línea **NTP**:
   - `activo` + `sincronizado`: todo bien.
   - `activo` + `sin sincronizar`: el servicio está encendido pero aún no logra sincronizar (revisa la conexión o el firewall, puerto UDP 123 de salida).
   - `inactivo`: el reloj no se sincroniza.
2. Haz clic en **Activar NTP** o **Desactivar NTP**.
3. Confirma en el diálogo **Sincronización NTP**.

> **Nota:** Si el botón aparece deshabilitado, el sistema no tiene un servicio NTP compatible con `timedatectl`. En Debian se instala con el paquete `systemd-timesyncd` (ver [Paquetes](27-paquetes.md)).

## Reiniciar o apagar el servidor

1. En la pestaña **General**, ve a la tarjeta **Encendido**.
2. Haz clic en **Reiniciar servidor** o **Apagar servidor**.
3. En el diálogo, escribe el **hostname exacto** del servidor en el campo "Escribe … para confirmar". El botón **Reiniciar** o **Apagar** se habilita solo cuando el texto coincide.
4. Confirma.

La acción se ejecuta 3 segundos después. Verás el aviso "El servidor se reiniciará en unos segundos." (o "se apagará").

> **Importante:** Si apagas el servidor, no podrás encenderlo desde el panel. Necesitarás acceso físico, a la consola del hipervisor o al sistema de gestión remota (iLO, iDRAC, etc.).

> **Consejo:** Tras un reinicio, espera uno o dos minutos y recarga la página. El panel vuelve a estar disponible cuando arranca el servicio `matipanel`.

## Ver quién está conectado y los últimos accesos

1. Abre la pestaña **Sesiones**. Los datos se cargan al entrar.
2. Revisa las tres tarjetas:
   - **Usuarios conectados (who)**: sesiones abiertas ahora mismo (SSH, consola).
   - **Últimos accesos (last)**: los últimos 50 inicios de sesión, con fecha y hora completas.
   - **Accesos fallidos (lastb)**: los últimos 30 intentos de inicio de sesión fallidos.
3. Usa **Refrescar** para volver a consultar.

> **Nota:** Si una tarjeta muestra "lastb no disponible" o un mensaje de error, el comando no está instalado o el sistema no guarda ese registro. En versiones recientes de Debian el registro clásico de accesos puede no estar disponible. Revisa los intentos fallidos de SSH en [Logs](28-logs.md) (unidad `ssh`).

> **Consejo:** Muchos accesos fallidos desde la misma IP indican un ataque de fuerza bruta. Considera bloquear esa IP en [Firewall](20-firewall.md).

## Ver los discos y el archivo fstab

La pestaña **Hardware** tiene dos tarjetas:

- **Dispositivos de bloque**: salida de `lsblk` con **Nombre**, **Tamaño**, **Tipo** (`disk`, `part`, `lvm`, `rom`…), **FS** (sistema de archivos), **Montaje** y **Modelo**. Las particiones aparecen sangradas bajo su disco con `└`.
- **/etc/fstab**: el contenido actual del archivo de montajes al arranque. El botón **editar** lo abre en el editor de [Archivos](26-archivos.md).

> **Importante:** Un error en `/etc/fstab` puede impedir que el servidor arranque. Antes de reiniciar tras editarlo, prueba con `mount -a` desde la [Terminal](29-terminal.md). Si no muestra errores, el archivo está bien.

## Editar archivos de configuración del sistema

La pestaña **Configuración** muestra accesos directos a seis archivos clave:

| Archivo | Para qué sirve |
|---|---|
| `/etc/hosts` | Resolución local de nombres |
| `/etc/resolv.conf` | Servidores DNS |
| `/etc/ssh/sshd_config` | Servidor SSH |
| `/etc/network/interfaces` | Interfaces de red (ifupdown) |
| `/etc/fstab` | Montajes al arranque |
| `/etc/sysctl.conf` | Parámetros del kernel |

1. Haz clic en **editar** en la tarjeta del archivo.
2. Se abre el editor de [Archivos](26-archivos.md) con ese archivo cargado.
3. Guarda los cambios desde el editor.

Si el archivo no existe en el servidor, verás la etiqueta `no existe` en lugar del botón.

> **Nota:** Editar un archivo no aplica el cambio por sí solo. Por ejemplo, tras editar `/etc/network/interfaces` debes reiniciar el servicio `networking` en [Servicios](13-servicios.md), y tras editar `/etc/sysctl.conf` debes ejecutar `sysctl -p` en la [Terminal](29-terminal.md).

> **Nota:** En algunos servidores `/etc/resolv.conf` lo genera otro programa (por ejemplo, `systemd-resolved` o NetworkManager). En ese caso tus cambios se pierden al reiniciar.

## Probar y reiniciar SSH tras editar sshd_config

En la tarjeta de `/etc/ssh/sshd_config` hay un botón extra: **Probar y reiniciar SSH**.

1. Edita y guarda `sshd_config`.
2. Vuelve a **Menú → Sistema → Sistema → Configuración**.
3. Haz clic en **Probar y reiniciar SSH**.
4. Confirma con **Reiniciar**.

El panel primero valida el archivo con `sshd -t`:

- Si hay un error, verás "sshd -t falló, NO se reinició SSH" con el detalle. SSH sigue funcionando con la configuración anterior.
- Si es válido, reinicia el servicio `ssh` y verás "Configuración válida. SSH reiniciado."

> **Importante:** Una configuración válida puede igual dejarte fuera (por ejemplo, si cambias el puerto o desactivas el acceso por contraseña sin tener llaves). Mantén abierta tu sesión SSH actual y prueba una conexión nueva antes de cerrarla. Si cambias el puerto, ábrelo antes en [Firewall](20-firewall.md).

## Preguntas frecuentes

### Cambié el hostname, ¿tengo que reiniciar?

No. El cambio es inmediato. Solo las sesiones SSH abiertas y algunos servicios que leen el nombre al arrancar (por ejemplo, el correo) siguen con el nombre anterior hasta que se reinician.

### ¿Por qué no puedo confirmar el reinicio?

El botón se habilita solo cuando escribes el hostname exactamente igual, respetando mayúsculas y puntos. Cópialo del recuadro **Hostname** de la parte superior.

### Me equivoqué al editar /etc/hosts. ¿Cómo lo recupero?

Si el último cambio lo hizo el panel al cambiar el hostname, hay una copia en `/etc/hosts.matipanel.bak`. Ábrela desde [Archivos](26-archivos.md) y copia su contenido. La copia se reemplaza cada vez que cambias el hostname.

### La hora del servidor está atrasada. ¿Qué hago?

Activa NTP con **Activar NTP**. Si aparece `sin sincronizar` durante varios minutos, verifica que el servidor tenga salida a internet por el puerto UDP 123 y que el servicio `systemd-timesyncd` esté activo en [Servicios](13-servicios.md).

## Ver también

- [Dashboard](10-dashboard.md)
- [Servicios](13-servicios.md)
- [Discos](15-discos.md)
- [Red (Interfaces)](18-red.md)
- [Archivos](26-archivos.md)
- [Terminal](29-terminal.md)
- [Firewall](20-firewall.md)
