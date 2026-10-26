# Almacenamiento avanzado

El módulo Almacenamiento avanzado te permite organizar los discos del servidor sin escribir comandos: crear volúmenes LVM que puedes ampliar sin reiniciar, montar RAID por software para que un disco dañado no te haga perder datos y limitar cuánto espacio usa cada usuario con cuotas. Lo encuentras en **Menú → Sistema → Almacenamiento**.

La pantalla tiene tres pestañas: **Discos y LVM**, **RAID** y **Cuotas**. Arriba verás un indicador con el estado general:

- **Verde: Almacenamiento en orden.** No hay arrays RAID con problemas.
- **Amarillo: Sincronizando o Reconstruyendo.** Un array RAID está copiando datos entre sus discos. Es normal tras crearlo o tras cambiar un disco.
- **Rojo: RAID degradado.** Un array perdió un disco y ya no tolera otro fallo. Aparece además la tarjeta **Hay arrays RAID degradados** con el detalle.

> **Importante:** Haz un respaldo antes de cualquier cambio en el almacenamiento. Crear un PV, un array RAID o eliminar un volumen **borra datos**. Usa [Respaldos](16-respaldos.md) y descarga una copia fuera del servidor antes de empezar.

## Conceptos básicos

### LVM: PV, VG y LV

LVM (*Logical Volume Manager*) pone una capa entre los discos y las carpetas. En lugar de particiones fijas, trabajas con tres piezas:

| Pieza | Qué es | Ejemplo |
|---|---|---|
| **PV** (volumen físico) | Un disco, una partición o un array RAID preparado para LVM | `/dev/sdb` |
| **VG** (grupo de volúmenes) | Uno o más PV juntos, como un gran depósito de espacio | `vg_datos` (formado por `/dev/sdb` y `/dev/sdc`) |
| **LV** (volumen lógico) | Una porción del grupo que formateas y montas como si fuera un disco | `vg_datos/web` montado en `/srv/web` |

La ventaja: cuando un LV se llena, lo amplías en segundos con el espacio libre del grupo, y si el grupo se queda corto le agregas otro disco. Todo sin apagar el servidor.

### RAID: discos que se cubren entre sí

Un array RAID une varios discos en uno solo (`/dev/md0`, `/dev/md1`…). Según el nivel, gana redundancia (si falla un disco, los datos siguen ahí), velocidad o ambas:

| Nivel | Discos mínimos | Capacidad útil (con N discos iguales de tamaño T) | Discos que pueden fallar |
|---|---|---|---|
| **RAID 0** | 2 | N × T | Ninguno: si falla uno, se pierde todo |
| **RAID 1** (espejo) | 2 | T | Todos menos uno |
| **RAID 5** | 3 | (N − 1) × T | 1 |
| **RAID 6** | 4 | (N − 2) × T | 2 |
| **RAID 10** | 4 | N × T ÷ 2 | 1 por cada pareja de espejo |

> **Nota:** RAID **no es un respaldo**. Protege contra un disco averiado, pero no contra un archivo borrado por error, un virus o un incendio. Sigue haciendo respaldos.

### Cuotas: límites por usuario y grupo

Una cuota limita cuánto espacio (y cuántos archivos) puede ocupar un usuario o un grupo en un sistema de archivos:

| Límite | Qué pasa al alcanzarlo |
|---|---|
| **Límite blando** | Se puede superar durante el **periodo de gracia** (7 días por defecto). Sirve de aviso. |
| **Límite duro** | Nunca se puede superar: el sistema rechaza la escritura. |
| **Periodo de gracia** | Tiempo que el usuario puede estar por encima del blando. Al vencer, el blando se comporta como duro hasta que libere espacio. |

## Instalar las herramientas

Cada pestaña necesita un paquete. Si falta, verás una tarjeta con el botón **Instalar**:

| Pestaña | Paquete |
|---|---|
| Discos y LVM | `lvm2` |
| RAID | `mdadm` |
| Cuotas | `quota` |

Si quieres formatear volúmenes en XFS, junto al botón **Crear LV** aparece **Instalar xfsprogs**.

1. Pulsa **Instalar** y confirma.
2. Espera a que termine (puede tardar uno o dos minutos). La pantalla se actualiza sola.

> **Nota:** La instalación automática solo está disponible en Debian y Ubuntu.

## Ver los discos del servidor

La tabla **Dispositivos de bloque** muestra todos los discos, particiones, volúmenes y arrays, con sangría para indicar qué hay dentro de qué. La columna **Estado** indica si puedes usarlo:

| Estado | Significado |
|---|---|
| **libre** | Sin particiones, sin sistema de archivos y sin montar. Puedes usarlo para LVM o RAID. |
| **sistema** | Contiene el sistema raíz (`/`). El panel nunca lo modifica. |
| **montado** | Está en uso en alguna carpeta. |
| **swap** | Es memoria de intercambio activa. |
| **PV de LVM** / **miembro RAID** | Ya forma parte de un grupo LVM o de un array. |
| **con datos** | Tiene un sistema de archivos, aunque no esté montado. |
| **tabla vacía** | Disco con tabla de particiones pero sin particiones. |

> **Nota:** Un disco con **tabla vacía** o **con datos** no se considera libre a propósito, para que no borres algo por error. Si estás seguro de que no lo necesitas, límpialo desde la [Terminal](29-terminal.md) con `wipefs -a /dev/sdX` y pulsa **Refrescar**.

## Preparar un disco para LVM (crear un PV)

1. En **Dispositivos de bloque**, busca un disco o partición con estado **libre**.
2. Pulsa **Crear PV**.
3. Escribe la ruta del dispositivo (por ejemplo `/dev/sdb`) para confirmar y pulsa **Crear PV**.

El disco aparece en **Volúmenes físicos (PV)** como **sin asignar**.

## Crear un grupo de volúmenes (VG)

1. Pulsa **Crear VG**.
2. Escribe un **Nombre del grupo**, por ejemplo `vg_datos`. Se permiten letras, números y `_ . + -`.
3. Marca los PV que formarán el grupo.
4. Pulsa **Crear VG**.

El grupo aparece en **Grupos de volúmenes (VG)** con una barra de uso.

### Agregar otro disco a un grupo

1. Crea un PV en el disco nuevo (ver arriba).
2. En la tarjeta del grupo, pulsa **Agregar PV**.
3. Elige el PV y pulsa **Agregar**.

El espacio libre del grupo crece en el tamaño del disco.

## Crear un volumen lógico (LV)

1. Pulsa **Crear LV** (arriba de la tabla o en la tarjeta del grupo).
2. Completa el formulario:

| Campo | Qué poner |
|---|---|
| **Grupo** | El VG del que saldrá el espacio |
| **Nombre** | Por ejemplo `web` o `backups` |
| **Tamaño** | `10G`, `500M`, o un porcentaje como `100%` para usar todo el espacio libre del grupo |
| **Formatear** | **ext4** (recomendado), **XFS** o **No formatear** |
| **Punto de montaje** | Opcional. Carpeta donde se verá el volumen, por ejemplo `/srv/web` |

3. Pulsa **Crear**.

Si indicaste un punto de montaje, el panel:

1. Crea la carpeta si no existe.
2. Hace una copia de `/etc/fstab` (`/etc/fstab.matipanel-FECHA.bak`).
3. Agrega la línea del volumen identificándolo por su UUID, con la opción `nofail` para que el servidor arranque aunque el volumen falte.
4. Comprueba el archivo con `findmnt --verify` y monta el volumen.

Si algo falla, restaura `/etc/fstab` automáticamente y te muestra el error.

> **Nota:** Por seguridad no puedes montar sobre carpetas del sistema (`/`, `/etc`, `/usr`, `/var`, `/home`, `/srv`…) ni sobre una carpeta que tenga archivos, porque quedarían ocultos. Usa una subcarpeta nueva como `/srv/web` o `/mnt/respaldos`.

> **Nota:** El punto de montaje y todas las carpetas que lo contienen deben pertenecer a `root`, no ser enlaces simbólicos y no ser escribibles por otros usuarios. Por eso no se acepta, por ejemplo, `/home/ana/datos` ni `/tmp/vol`: verás "… pertenece a otro usuario" o "Otros usuarios pueden escribir en …". Motivo: `mount` (y el arranque, a través de `/etc/fstab`) sigue enlaces simbólicos, y ese usuario podría cambiar la carpeta por un enlace a `/etc` para que el volumen la tape. Si un usuario necesita el volumen, móntalo en `/srv/…` y dale permisos sobre la carpeta montada.

## Ampliar un volumen

1. En **Volúmenes lógicos (LV)**, pulsa **Ampliar** en el volumen.
2. Escribe cuánto espacio **agregar**: `5G`, `500M` o `100%` (todo lo libre del grupo).
3. Pulsa **Ampliar**.

Si el volumen tiene ext4 o XFS, el sistema de archivos también se amplía, incluso con el volumen montado y en uso.

> **Importante:** El panel solo amplía, **nunca reduce**. Reducir un volumen puede destruir datos y XFS no lo permite. Si de verdad necesitas reducir uno, hazlo desde la [Terminal](29-terminal.md) con un respaldo reciente.

## Usar snapshots

Un snapshot congela el estado de un volumen en un instante. Sirve, por ejemplo, para tener un punto de retorno antes de actualizar una aplicación.

### Crear un snapshot

1. Pulsa **Snapshot** en el volumen.
2. Revisa el **Nombre del snapshot** (se propone uno con la fecha).
3. En **Espacio para cambios** indica cuánto espacio reservar. Ahí se guardan los bloques que cambian en el original; entre un 10 y un 20 % del volumen suele bastar.
4. Pulsa **Crear snapshot**.

En la tabla verás el snapshot con la etiqueta **snapshot de** *origen* y el porcentaje de su espacio que ya está usado.

> **Importante:** Si el espacio para cambios llega al 100 %, el snapshot queda **inválido** y ya no sirve. No los dejes mucho tiempo: elimínalos cuando ya no los necesites. Además, mientras exista un snapshot las escrituras en el volumen original son algo más lentas.

### Eliminar un snapshot

Pulsa **Eliminar** en la fila del snapshot y escribe `grupo/nombre` para confirmar. El volumen original no se toca.

## Eliminar un volumen

1. Asegúrate de que no está montado y de que no figura en `/etc/fstab`.
2. Pulsa **Eliminar** en el volumen.
3. Escribe `grupo/nombre` (por ejemplo `vg_datos/web`) y pulsa **Eliminar**.

El panel rechaza la operación si el volumen está montado, si figura en `/etc/fstab`, si tiene snapshots o si otro dispositivo lo usa.

## RAID por software

### Ver el estado de los arrays

En la pestaña **RAID**, cada array tiene su tarjeta con:

| Dato | Significado |
|---|---|
| **Estado** | **correcto**, **degradado** (falta un disco), **inactivo** o la tarea en curso (sincronizando, reconstruyendo…) |
| **Discos** | Cuántos funcionan de los que debería tener, por ejemplo `1 de 2` y el mapa `[U_]` (U = disco bien, _ = falta) |
| **En mdadm.conf** | Si el array está guardado para ensamblarse igual al arrancar |
| Barra de progreso | Porcentaje, tiempo restante y velocidad de la sincronización o reconstrucción |

Cada miembro aparece como **activo**, **repuesto** (spare, entra solo si otro falla) o **fallido**.

### Crear un array

1. Pulsa **Crear array**.
2. Revisa el **Nombre** (por ejemplo `md0`) y elige el **Nivel**.
3. Marca los discos libres que formarán el array. Lo ideal es que tengan el mismo tamaño: se usa el del más pequeño.
4. Pulsa **Continuar**, escribe el nombre del array para confirmar y pulsa **Crear array**.

El array aparece con la barra **Sincronizando**. Puedes usarlo desde ese momento, aunque irá algo más lento hasta que termine.

Después:

- Pulsa **Guardar en mdadm.conf** (ver abajo) para que se ensamble al arrancar.
- Para usarlo, en **Discos y LVM** aparecerá como **libre**: pulsa **Crear PV** sobre `/dev/md0` y crea un grupo con él.

### Guardar en mdadm.conf y actualizar el initramfs

1. Pulsa **Guardar en mdadm.conf**. El panel agrega a `/etc/mdadm/mdadm.conf` (con respaldo) los arrays que aún no estaban; nunca duplica líneas.
2. Cuando te pregunte, pulsa **Actualizar ahora** para ejecutar `update-initramfs -u`. También puedes hacerlo luego con el botón **Actualizar initramfs**.

> **Importante:** Sin este paso, tras reiniciar el array puede aparecer con otro nombre (por ejemplo `md127`) y las líneas de `/etc/fstab` que dependan de su nombre fallarían.

### Reemplazar un disco dañado

Cuando un disco falla, el array queda **degradado** y el panel lo marca en rojo.

1. **Haz un respaldo** de los datos del array ahora mismo.
2. Si el disco dañado aún figura como **activo**, pulsa **Marcar fallido y quitar**. Si ya figura como **fallido**, pulsa **Quitar**. Escribe la ruta del disco para confirmar.
3. Cambia físicamente el disco (o agrega uno nuevo en tu proveedor de nube) y pulsa **Refrescar**.
4. Pulsa **Agregar disco** en el array, elige el disco nuevo (debe estar **libre** y ser igual o más grande que los demás) y confirma escribiendo su ruta.
5. La reconstrucción empieza sola. Sigue el avance en la barra **Reconstruyendo**.

> **Nota:** Si el array ya está degradado, el panel no te deja quitar otro disco **activo**, porque eso podría destruir el array. En RAID 0 tampoco se puede quitar ningún disco.

> **Consejo:** Agregar un disco a un array sano lo deja como **repuesto**: entra automáticamente en cuanto otro falle.

## Cuotas de disco

### Ver qué sistemas de archivos tienen cuotas

La tabla **Sistemas de archivos** lista los sistemas ext4 y XFS montados:

| Columna | Significado |
|---|---|
| **Cuotas en opciones** | Si se montó con `usrquota`/`grpquota` (o equivalentes) |
| **Usuarios** / **Grupos** | Si las cuotas están **activas** o **apagadas** |

### Activar cuotas en ext4

1. Pulsa **Activar cuotas** en el sistema de archivos.
2. Lee el resumen y pulsa **Activar cuotas**.

El panel hace estos pasos y, si alguno falla, restaura `/etc/fstab`:

1. Respalda `/etc/fstab` y agrega `usrquota,grpquota` a las opciones de ese sistema de archivos.
2. Lo vuelve a montar con esas opciones, sin desmontarlo.
3. Cuenta el uso actual de cada usuario y grupo con `quotacheck -cugm`. En discos grandes puede tardar varios minutos.
4. Enciende las cuotas con `quotaon`.

> **Nota:** El sistema de archivos debe figurar en `/etc/fstab` para que las cuotas sigan activas tras reiniciar.

### Activar cuotas en XFS

En XFS las cuotas se deciden al montar y no se activan con un remontaje, así que el panel solo muestra las instrucciones (botón **Cómo activarlas**):

1. Haz un respaldo y edita `/etc/fstab`.
2. Agrega `uquota,gquota` a las opciones de ese sistema de archivos, por ejemplo `defaults,uquota,gquota`.
3. Si es la raíz (`/`), agrega también `rootflags=uquota,gquota` a `GRUB_CMDLINE_LINUX` en `/etc/default/grub` y ejecuta `update-grub`.
4. Reinicia el servidor.

XFS no necesita `quotacheck`.

### Ver el uso por usuario y grupo

La tarjeta **Uso por usuario y grupo** muestra, para cada sistema de archivos con cuotas, una tabla de **Usuarios** y otra de **Grupos**:

- **Espacio usado** con una barra respecto al límite (amarilla al superar el blando, roja al llegar al duro).
- **Límite blando** y **Límite duro** (o *sin límite*).
- **Gracia**: tiempo que queda para bajar del límite blando, o **vencido**.
- **Archivos**: cuántos archivos tiene y sus límites.

### Fijar límites

1. Pulsa **Editar** en la fila del usuario o grupo, o **Fijar límites** para uno nuevo.
2. Completa el formulario:

| Campo | Ejemplo |
|---|---|
| **Tipo** | Usuario o Grupo |
| **Nombre** | `juan` (debe existir en el servidor) |
| **Sistema de archivos** | `/home` |
| **Espacio · límite blando** | `2G` |
| **Espacio · límite duro** | `3G` |
| **Archivos · límite blando / duro** | `0` (sin límite) |

3. Pulsa **Guardar límites**.

Los tamaños aceptan `K`, `M`, `G` y `T`. `0` o vacío significa sin límite. El límite blando no puede ser mayor que el duro.

## Preguntas frecuentes

### Conecté un disco nuevo y no aparece como libre

Pulsa **Refrescar**. Si aparece como **tabla vacía** o **con datos**, tiene restos de un uso anterior. Si estás seguro de que no lo necesitas, límpialo en la [Terminal](29-terminal.md) con `wipefs -a /dev/sdX` (cambia `sdX` por el disco correcto).

### ¿Puedo ampliar el disco del sistema?

Sí, si el sistema se instaló con LVM (por ejemplo `/dev/mapper/debian--vg-root`). Agrega un disco al grupo con **Crear PV** y **Agregar PV**, y luego pulsa **Ampliar** en el volumen raíz. Si se instaló en una partición normal, el panel no puede ampliarlo.

### ¿Qué pasa si el servidor se reinicia mientras un array se sincroniza?

Nada grave: la sincronización continúa donde quedó al arrancar.

### El array apareció como md127 tras reiniciar

No estaba guardado en `mdadm.conf`. Pulsa **Guardar en mdadm.conf**, luego **Actualizar initramfs** y reinicia.

### Al activar cuotas aparece "Quota format not supported in kernel"

El kernel del servidor no incluye el módulo de cuotas (pasa con algunos kernels mínimos de nube). Instala el kernel completo (`linux-image-amd64`) desde [Paquetes](27-paquetes.md), reinicia y vuelve a intentarlo.

### ¿Puedo reducir un volumen o quitar un disco de un grupo LVM?

No desde el panel, porque son operaciones con riesgo de perder datos. Hazlas desde la [Terminal](29-terminal.md) con `pvmove`, `vgreduce` y `lvreduce`, siempre con un respaldo reciente.

### El panel dice "Otra operación de almacenamiento está en curso"

Para evitar conflictos, el panel ejecuta una sola operación de almacenamiento a la vez. Espera a que termine la anterior (una ampliación grande o un `quotacheck` pueden tardar) y vuelve a intentarlo.

## Ver también

- [Discos](15-discos.md)
- [Respaldos](16-respaldos.md)
- [Sistema](11-sistema.md)
- [Paquetes](27-paquetes.md)
- [Terminal](29-terminal.md)
