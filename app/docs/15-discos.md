# Discos

El módulo Discos muestra las particiones montadas en el servidor y cuánto espacio tienen, **qué es lo que ocupa más espacio** (carpeta por carpeta y los archivos más grandes) y **qué se puede limpiar** sin riesgo. Lo encuentras en **Menú → Archivos y discos → Discos**.

## Ver el uso de las particiones

Al abrir la página verás la tabla **Discos y particiones** con una fila por cada sistema de archivos montado:

| Columna | Significado |
|---|---|
| **Dispositivo** | Dispositivo de origen, por ejemplo `/dev/sda1`, `/dev/nvme0n1p2` o `/dev/mapper/vg-root` (LVM) |
| **Montaje** | Punto de montaje: la carpeta donde se ve ese disco, por ejemplo `/`, `/home` o `/var` |
| **FS** | Tipo de sistema de archivos: `ext4`, `xfs`, `vfat`, `btrfs`… |
| **Tamaño** | Capacidad total |
| **Usado** | Espacio ocupado |
| **Libre** | Espacio disponible |
| **Uso** | Barra y porcentaje de ocupación |
| **Analizar** | Muestra qué ocupa más espacio en esa partición (ver abajo) |

### Colores de la barra

| Color | Porcentaje de uso |
|---|---|
| Normal | Menos de 80 % |
| Amarillo | 80 % a 89 % |
| Rojo | 90 % o más |

> **Nota:** Los avisos de la barra de estado y de los correos usan los umbrales que configures en [Notificaciones → Umbrales del servidor](33-notificaciones.md#umbrales-del-servidor), donde también puedes poner un umbral propio para cada partición.

## Entender por qué Usado más libre no suma el Tamaño

En particiones `ext4`, el sistema reserva un 5 % del espacio para el usuario `root`. Por eso:

- **Usado** más el espacio libre puede ser menor que **Tamaño**.
- El porcentaje de **Uso** se calcula sobre el espacio disponible para usuarios normales. Puede llegar al 100 % aunque `root` todavía pueda escribir un poco.

Cuando una partición llega al 100 %, los servicios que no corren como `root` (servidor web, PHP, bases de datos) empiezan a fallar al escribir.

## Qué particiones aparecen

La tabla muestra los sistemas de archivos montados en discos (físicos, virtuales o LVM). No aparecen los sistemas de archivos en memoria como `tmpfs`.

Si tienes un disco instalado pero sin montar, no aparecerá aquí. Para ver todos los discos, montados o no, usa la pestaña **Hardware** de [Sistema](11-sistema.md) (tabla **Dispositivos de bloque**).

> **Nota:** Este módulo no permite particionar, formatear ni montar discos. Para eso usa la [Terminal](29-terminal.md) y, para que un montaje sea permanente, edita `/etc/fstab` desde **Sistema → Hardware → editar**.

## Qué se puede limpiar

La tarjeta **Qué se puede limpiar** revisa lo que el sistema vuelve a generar o que ya no se usa, muestra cuánto ocupa cada cosa y el total que se puede liberar. Cada fila tiene un botón que usa el comando estándar para eso; tus archivos y datos no se tocan.

| Qué | Qué hace el botón | Riesgo |
|---|---|---|
| **Paquetes descargados (caché de APT)** | `apt-get clean`: borra los instaladores `.deb` ya instalados. | Seguro |
| **Registro del sistema (journal)** | `journalctl --vacuum-size=200M`: deja los últimos 200 MB y borra lo más viejo. Solo aparece si ocupa más. | Seguro |
| **Logs rotados viejos** | Borra en `/var/log` solo las copias que logrotate ya cerró (`.gz`, `.1`, `.old`, `-AAAAMMDD`). Los logs actuales no se tocan. | Seguro |
| **Volcados de memoria** | Vacía `/var/lib/systemd/coredump`. | Seguro |
| **Caché de compilación de Docker** | `docker builder prune -af`. Los despliegues siguientes pueden tardar un poco más. | Seguro |
| **Imágenes de Docker huérfanas** | `docker image prune -f`: imágenes sin nombre que nada usa. | Seguro |
| **Imágenes de Docker sin usar** | `docker image prune -af`. | Con cuidado: borra también las versiones anteriores de las apps de [Despliegues](56-despliegues.md), así que ya no podrás revertir a ellas sin compilar de nuevo. |
| **Paquetes que ya no se necesitan** | `apt-get autoremove --purge`: dependencias huérfanas y kernels viejos. | Seguro |
| **Caché de root** y **papeleras** | Vacía `/root/.cache` y las papeleras de los usuarios. | Seguro |
| **Volúmenes de Docker sin usar** | Solo informa: pueden tener datos. Revísalos en [Docker](25-docker.md). | Revísalo tú |
| **Respaldos de carpetas del panel** | Solo informa. Borra los que no necesites en [Respaldos](16-respaldos.md). | Revísalo tú |

Cada limpieza pide confirmación, muestra en vivo lo que hace y al terminar dice cuánto espacio se liberó. **Revisar de nuevo** vuelve a calcular los tamaños.

## Qué ocupa más espacio

La tarjeta **Qué ocupa más espacio** muestra las carpetas y archivos de una carpeta ordenados por tamaño, con el porcentaje que representa cada uno:

1. Al abrir la página se analiza `/`. Para otra partición, pulsa **Analizar** en su fila de la tabla.
2. Haz clic en una carpeta para ver qué hay dentro, y así hasta encontrar lo que ocupa espacio. Las migas de arriba (`/ › var › log`) te llevan de vuelta.
3. **Abrir** te lleva a esa carpeta en el administrador de [Archivos](26-archivos.md), donde puedes revisar, comprimir o borrar.

- El análisis no pasa a otras particiones: al analizar `/` no se cuenta lo que está montado en `/home` si es otro disco.
- En discos grandes puede tardar uno o dos minutos. Se hace con prioridad baja, así que el servidor sigue funcionando normal mientras tanto.
- El resultado se guarda 10 minutos: puedes entrar y salir de carpetas sin esperar. **Volver a analizar** lo calcula de nuevo (por ejemplo, después de borrar algo).

## Archivos más grandes

**Buscar en esta partición** lista los archivos de más de 50 MB dentro de la carpeta analizada, de mayor a menor, con su fecha. Suelen ser instaladores, imágenes de discos, volcados de bases, logs enormes o copias olvidadas. **Abrir carpeta** te lleva a donde está cada uno.

## Antes de borrar algo a mano

> **Importante:** No borres archivos de `/var/lib` (bases de datos, Docker, paquetes) ni logs que estén en uso sin saber qué son. Si un log de una aplicación es enorme, vacíalo en lugar de borrarlo: `truncate -s 0 /ruta/al/archivo.log`.

- **Respaldos antiguos**: revisa `/var/backups/matipanel` en [Respaldos](16-respaldos.md) y baja las **Copias a conservar** de los trabajos programados.
- **Datos de Docker** (`/var/lib/docker`): usa las limpiezas de arriba o [Docker](25-docker.md); nunca borres a mano dentro de esa carpeta.

> **Consejo:** Si borraste archivos grandes y el espacio no se liberó, algún proceso los tiene abiertos todavía. Reinicia el servicio que los usaba en [Servicios](13-servicios.md).

## Preguntas frecuentes

### Conecté un disco nuevo y no aparece. ¿Por qué?

Discos solo muestra particiones montadas. Revisa que el disco se vea en **Sistema → Hardware → Dispositivos de bloque**. Después tendrás que crear una partición, formatearla y montarla desde la [Terminal](29-terminal.md), y agregarla a `/etc/fstab`.

### ¿Por qué aparece /boot/efi con tipo vfat?

Es la partición de arranque UEFI. Es pequeña y normalmente casi vacía. No la modifiques.

### ¿Puedo ver el uso de inodos?

Sí: en [Notificaciones → Umbrales del servidor](33-notificaciones.md#umbrales-del-servidor), la tabla **Discos** muestra los inodos usados de cada partición y avisa cuando pasan del umbral. Si un disco tiene espacio libre pero no se pueden crear archivos, suele ser por inodos: busca con **Qué ocupa más espacio** la carpeta con muchísimos archivos pequeños (cachés, sesiones de PHP, colas de correo).

### El análisis dice que algunos elementos no se pudieron leer

Son archivos que desaparecieron mientras se analizaba (temporales) o sistemas de archivos especiales. No afecta al resultado del resto.

## Ver también

- [Dashboard](10-dashboard.md)
- [Sistema](11-sistema.md)
- [Respaldos](16-respaldos.md)
- [Archivos](26-archivos.md)
- [Docker](25-docker.md)
- [Terminal](29-terminal.md)
