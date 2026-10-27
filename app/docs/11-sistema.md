# Sistema

El módulo Sistema reúne la información y la configuración básica del servidor: qué equipo es, cuánto lleva encendido, el nombre, la fecha y hora, el reinicio y apagado (también programado), quién se conecta, el hardware y los archivos de configuración más comunes. Lo encuentras en **Menú → Sistema → Sistema**.

## Resumen superior

Arriba de las pestañas hay cuatro recuadros que se actualizan cada 30 segundos:

| Recuadro | Valor principal | Detalle inferior |
|---|---|---|
| **Servidor** | nombre del servidor (hostname) | sistema operativo |
| **Encendido desde hace** | tiempo encendido (`3 d 4 h 10 min`) | fecha y hora del último arranque |
| **CPU** | número de núcleos | carga promedio de 1, 5 y 15 minutos |
| **Memoria** | usada / total, con barra (verde, ámbar desde 80 %, roja desde 90 %) | uso de swap |

Debajo pueden aparecer dos avisos:

- **Reinicio pendiente** (ámbar): las actualizaciones instaladas (por ejemplo un kernel nuevo) necesitan reiniciar para aplicarse. Muestra los paquetes y un botón **Programar reinicio…**.
- **Reinicio/Apagado programado** (azul): hay uno programado; muestra la fecha, el mensaje y **Cancelar**.

## Pestaña General

### Resumen

| Dato | Qué significa |
|---|---|
| **Sistema operativo** | Distribución y versión (por ejemplo `Debian GNU/Linux 13 (trixie)`). |
| **Kernel** | Versión del kernel y arquitectura (`x86_64`, `aarch64`…). |
| **Equipo** | Fabricante y modelo del hardware, y si es **virtual** (`kvm`, `vmware`, `microsoft`…) o **físico**. |
| **Procesador** | Modelo de la CPU. |
| **Carga (1 · 5 · 15 min)** | Procesos esperando CPU. Verde si es menor que los núcleos, ámbar si los iguala, rojo si los supera en 1,5 veces. |
| **Procesos** | Procesos en ejecución. |
| **Sesiones abiertas** | Usuarios conectados ahora (SSH, consola). |
| **Idioma y teclado** | Idioma del sistema (`LANG`) y distribución del teclado. |

### Fecha y hora

El reloj grande muestra la **hora del servidor en vivo** (en su zona horaria), aunque tu equipo esté en otra zona o con otra hora. Debajo:

- **Zona horaria**: escribe para buscar (por ejemplo `America/Guatemala`), pulsa **Cambiar** y confirma. Debe existir en `timedatectl list-timezones`.
- **Sincronización (NTP)**: `activa` + `sincronizada` es lo correcto. **Activar/Desactivar** cambia la sincronización automática.
- **Servidor de hora**: el servidor NTP que se está usando.
- **Hora UTC**: la hora universal, útil para comparar con logs de otros sistemas.

> **Importante:** La zona horaria afecta a cron, a los respaldos programados y a las fechas de los logs.

> **Nota:** Si **Activar** aparece deshabilitado, falta un servicio NTP compatible con `timedatectl`. En Debian se instala con el paquete `systemd-timesyncd` (ver [Paquetes](27-paquetes.md)). Si queda `sin sincronizar` varios minutos, revisa que el servidor tenga salida por UDP 123.

### Nombre del servidor

1. Escribe el nuevo nombre y pulsa **Cambiar**.
2. Confirma.

El panel ejecuta `hostnamectl set-hostname` y reescribe la línea `127.0.1.1` de `/etc/hosts` (antes guarda una copia en `/etc/hosts.matipanel.bak`).

**Reglas:** letras, números, guiones y puntos; cada parte de 1 a 63 caracteres sin empezar ni terminar en guion; máximo 253. Ejemplos: `srv-web01`, `srv-web01.senabed.gob.gt`.

> **Nota:** Si el nombre cambia pero `/etc/hosts` no se puede escribir, verás un aviso **Atención**; revisa el archivo a mano desde [Archivos](26-archivos.md). Las sesiones SSH abiertas siguen mostrando el nombre anterior hasta reconectarse.

### Encendido

| Botón | Qué hace |
|---|---|
| **Reiniciar ahora** / **Apagar ahora** | Se ejecuta a los 3 segundos. |
| **Programar…** | Reinicio o apagado en **N minutos** (1 a 10080, es decir hasta 7 días) o a una **hora HH:MM** (la próxima vez que llegue esa hora). Puedes escribir un **mensaje** que verán los usuarios conectados por SSH. |
| **Cancelar programado** | Anula el reinicio o apagado programado. |

Todas piden escribir el **nombre del servidor** para confirmar (cópialo del recuadro **Servidor**).

> **Consejo:** Para aplicar un kernel nuevo sin cortar a nadie en horario laboral, usa **Programar…** con una hora nocturna, por ejemplo `23:00`.

> **Importante:** Si apagas el servidor no podrás encenderlo desde el panel: necesitarás acceso físico, la consola del hipervisor o la gestión remota (iLO, iDRAC…). Tras un reinicio espera uno o dos minutos y recarga la página.

## Pestaña Sesiones

Se carga al abrirla; **Refrescar** vuelve a consultar. Todas las tablas se pueden filtrar, ordenar y paginar.

- **Conectados ahora**: usuario, terminal, desde qué IP (`local` si es la consola), cuándo entró y cuánto lleva inactivo.
- **Intentos fallidos por SSH**: los últimos 7 días, agrupados por **IP**, con el número de intentos, los **usuarios que probaron** y el último intento. Se lee del journal de `sshd` (en Debian 13 ya no existe `lastb`).
- **Últimos accesos y arranques**: los últimos 60 inicios de sesión con inicio, fin y duración (`conectado` si sigue dentro, `caída` si el servidor se cayó con la sesión abierta) y los **arranques** del servidor con su kernel.
- **Accesos fallidos (lastb)**: solo en sistemas que todavía guardan ese registro.

> **Consejo:** Muchos intentos desde la misma IP probando `root`, `admin` u otros nombres son un ataque de fuerza bruta. Bloquéala en el [Firewall](20-firewall.md) y considera instalar fail2ban desde [Seguridad del servidor](34-seguridad-servidor.md).

## Pestaña Hardware

- **Procesador**: modelo, fabricante, arquitectura, CPU lógicas, zócalos, núcleos e hilos, frecuencias, virtualización y cachés (de `lscpu`).
- **Dispositivos de bloque**: discos y particiones (`lsblk`) con tamaño, tipo, sistema de archivos, montaje y modelo.
- **Dispositivos PCI** y **USB**: listado de `lspci` y `lsusb` (si faltan, instala los paquetes `pciutils` y `usbutils`).
- **/etc/fstab**: los montajes al arranque, con **editar** para abrirlo en [Archivos](26-archivos.md).

> **Importante:** Un error en `/etc/fstab` puede impedir que el servidor arranque. Antes de reiniciar tras editarlo, prueba con `mount -a` en la [Terminal](29-terminal.md).

## Pestaña Configuración

Accesos directos para editar archivos clave en [Archivos](26-archivos.md):

| Archivo | Para qué sirve |
|---|---|
| `/etc/hosts` | Resolución local de nombres |
| `/etc/resolv.conf` | Servidores DNS |
| `/etc/ssh/sshd_config` | Servidor SSH |
| `/etc/network/interfaces` | Interfaces de red (ifupdown) |
| `/etc/fstab` | Montajes al arranque |
| `/etc/sysctl.conf` | Parámetros del kernel |

Si un archivo no existe verás `no existe`. Editar no aplica el cambio por sí solo: por ejemplo, tras `/etc/sysctl.conf` ejecuta `sysctl -p`, y tras `/etc/network/interfaces` reinicia `networking` en [Servicios](13-servicios.md).

### Probar y reiniciar SSH

En la tarjeta de `sshd_config`, **Probar y reiniciar SSH** valida el archivo con `sshd -t`: si hay un error no reinicia nada y muestra el detalle; si es válido reinicia el servicio. Para cambios habituales de SSH (acceso de root, contraseñas, puerto) es más seguro usar [Seguridad del servidor](34-seguridad-servidor.md).

> **Importante:** Mantén abierta tu sesión SSH actual y prueba una conexión nueva antes de cerrarla. Si cambias el puerto, ábrelo antes en el [Firewall](20-firewall.md).

## Preguntas frecuentes

### Cambié el nombre, ¿tengo que reiniciar?

No. El cambio es inmediato; solo las sesiones abiertas y algunos servicios que leen el nombre al arrancar (por ejemplo el correo) siguen con el anterior hasta reiniciarse.

### ¿Por qué el reloj del panel no coincide con el de mi computadora?

Porque muestra la hora del **servidor** en su zona horaria. Si el servidor está mal, activa NTP.

### Programé un reinicio y me arrepentí

Pulsa **Cancelar** en el aviso azul o **Cancelar programado** en **Encendido**.

### Me equivoqué al editar /etc/hosts

Si el último cambio lo hizo el panel al cambiar el nombre, hay una copia en `/etc/hosts.matipanel.bak`. La copia se reemplaza cada vez que cambias el nombre.

## Ver también

- [Dashboard](10-dashboard.md)
- [Servicios](13-servicios.md)
- [Seguridad del servidor](34-seguridad-servidor.md)
- [Discos](15-discos.md)
- [Archivos](26-archivos.md)
- [Terminal](29-terminal.md)
- [Firewall](20-firewall.md)
