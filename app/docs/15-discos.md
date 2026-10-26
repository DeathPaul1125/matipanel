# Discos

El módulo Discos muestra las particiones montadas en el servidor, cuánto espacio tienen y cuánto está en uso. Es de solo consulta: sirve para detectar a tiempo un disco que se está llenando. Lo encuentras en **Menú → Sistema → Discos**.

## Ver el uso de las particiones

Al abrir la página verás la tabla **Discos y particiones** con una fila por cada sistema de archivos montado:

| Columna | Significado |
|---|---|
| **Dispositivo** | Dispositivo de origen, por ejemplo `/dev/sda1`, `/dev/nvme0n1p2` o `/dev/mapper/vg-root` (LVM) |
| **Mount** | Punto de montaje: la carpeta donde se ve ese disco, por ejemplo `/`, `/home` o `/var` |
| **FS** | Tipo de sistema de archivos: `ext4`, `xfs`, `vfat`, `btrfs`… |
| **Tamaño** | Capacidad total |
| **Usado** | Espacio ocupado |
| **Uso** | Barra y porcentaje de ocupación |

### Colores de la barra

| Color | Porcentaje de uso |
|---|---|
| Normal | Menos de 65 % |
| Amarillo | 65 % a 84 % |
| Rojo | 85 % o más |

> **Nota:** Estos umbrales son más estrictos que los del [Dashboard](10-dashboard.md), donde el aviso empieza en 80 % y la alerta grave en 90 %. Es normal ver una barra amarilla aquí mientras el Dashboard dice "Todo en orden".

> **Nota:** La página no se actualiza sola ni tiene botón de refrescar. Recarga la página del navegador (F5) para ver valores nuevos.

## Entender por qué Usado más libre no suma el Tamaño

En particiones `ext4`, el sistema reserva un 5 % del espacio para el usuario `root`. Por eso:

- **Usado** más el espacio libre puede ser menor que **Tamaño**.
- El porcentaje de **Uso** se calcula sobre el espacio disponible para usuarios normales. Puede llegar al 100 % aunque `root` todavía pueda escribir un poco.

Cuando una partición llega al 100 %, los servicios que no corren como `root` (servidor web, PHP, bases de datos) empiezan a fallar al escribir.

## Qué particiones aparecen

La tabla muestra los sistemas de archivos montados en discos (físicos, virtuales o LVM). No aparecen los sistemas de archivos en memoria como `tmpfs`.

Si tienes un disco instalado pero sin montar, no aparecerá aquí. Para ver todos los discos, montados o no, usa la pestaña **Hardware** de [Sistema](11-sistema.md) (tabla **Dispositivos de bloque**).

> **Nota:** Este módulo no permite particionar, formatear ni montar discos. Para eso usa la [Terminal](29-terminal.md) y, para que un montaje sea permanente, edita `/etc/fstab` desde **Sistema → Hardware → editar**.

## Liberar espacio cuando un disco se llena

Cuando una partición pase del 85 %, revisa estas causas habituales. Todas se pueden hacer desde la [Terminal](29-terminal.md) del panel:

1. **Encontrar qué ocupa espacio**: `du -xh --max-depth=1 / | sort -h` (cambia `/` por el punto de montaje lleno).
2. **Logs del sistema (journal)**: revisa cuánto ocupan con `journalctl --disk-usage` y redúcelos con `journalctl --vacuum-size=500M`.
3. **Caché de paquetes**: `apt clean` borra los paquetes descargados que ya se instalaron.
4. **Respaldos antiguos**: revisa `/var/backups/matipanel` en [Respaldos](16-respaldos.md). Elimina los que ya descargaste o baja las **Copias a conservar** de los trabajos programados.
5. **Docker**: imágenes y contenedores sin usar pueden ocupar mucho. Revisa [Docker](25-docker.md).
6. **Logs de aplicaciones**: archivos grandes en `/var/log` o dentro de `/var/www`. Puedes verlos desde [Archivos](26-archivos.md).

> **Importante:** No borres archivos de `/var/lib` (bases de datos, paquetes) ni de `/var/log` que estén en uso sin saber qué son. Si un log de una aplicación es enorme, vacíalo en lugar de borrarlo: `truncate -s 0 /ruta/al/archivo.log`.

> **Consejo:** Si borraste archivos grandes y el espacio no se liberó, algún proceso los tiene abiertos todavía. Reinicia el servicio que los usaba en [Servicios](13-servicios.md).

## Preguntas frecuentes

### El Dashboard y Discos muestran porcentajes de colores distintos para la misma partición. ¿Cuál vale?

Ambos muestran el mismo porcentaje; lo que cambia es el umbral de color. Discos marca amarillo desde 65 % y rojo desde 85 %. El Dashboard avisa desde 80 % y alerta desde 90 %. Usa Discos para anticiparte.

### Conecté un disco nuevo y no aparece. ¿Por qué?

Discos solo muestra particiones montadas. Revisa que el disco se vea en **Sistema → Hardware → Dispositivos de bloque**. Después tendrás que crear una partición, formatearla y montarla desde la [Terminal](29-terminal.md), y agregarla a `/etc/fstab`.

### ¿Por qué aparece /boot/efi con tipo vfat?

Es la partición de arranque UEFI. Es pequeña y normalmente casi vacía. No la modifiques.

### ¿Puedo ver el uso de inodos?

No desde este módulo. Si un disco tiene espacio libre pero no se pueden crear archivos, revisa los inodos en la [Terminal](29-terminal.md) con `df -i`.

## Ver también

- [Dashboard](10-dashboard.md)
- [Sistema](11-sistema.md)
- [Respaldos](16-respaldos.md)
- [Archivos](26-archivos.md)
- [Docker](25-docker.md)
- [Terminal](29-terminal.md)
