# Archivos

El administrador de archivos te permite navegar, editar, crear, mover, copiar, borrar, subir y descargar archivos del servidor, y cambiar permisos y propietarios. Lo encuentras en **Menú → Herramientas → Archivos**.

> **Importante:** El administrador de archivos trabaja como `root`. Puedes leer y modificar cualquier archivo del sistema, incluidos los críticos. Un error aquí puede dejar el servidor sin arrancar. Trabaja con calma y verifica la ruta antes de cada acción.

## Conocer la pantalla

La pantalla tiene tres zonas:

| Zona | Qué contiene |
|---|---|
| **Panel izquierdo** | **Accesos** a carpetas habituales (Raíz, Configuración `/etc`, Sitios web `/var/www`, Registros `/var/log`, Usuarios `/home`, `root`, Aplicaciones `/opt`, Temporales `/tmp`), tus **Favoritos** y el espacio libre del **disco** de la carpeta actual. |
| **Barra superior** | Botones **← → ↑ ⟳**, la ruta actual, el filtro, la casilla **Ocultos** y los botones **+ Carpeta**, **+ Archivo** y **Subir…**. |
| **Lista** | El contenido de la carpeta. Al pie: cuántas carpetas y archivos hay y cuánto ocupan. |

En pantallas angostas el panel izquierdo se oculta.

## Navegar por las carpetas

- **Entrar a una carpeta:** haz clic en su nombre o doble clic en la fila.
- **Ruta en migas:** la barra muestra la ruta como `/ › var › www › html`. Haz clic en cualquier tramo para saltar a esa carpeta.
- **Escribir una ruta:** haz clic en un espacio libre de la barra de ruta, escribe la ruta y pulsa `Enter` (`Esc` cancela).
- **← / →:** atrás y adelante por las carpetas que visitaste.
- **↑:** carpeta superior (también con la tecla `Retroceso`).
- **⟳:** vuelve a leer la carpeta (también `F5`).

La dirección del navegador cambia a `?path=/ruta/actual`. Puedes guardarla como marcador.

## Guardar carpetas en Favoritos

1. Entra en la carpeta.
2. Pulsa **☆** junto a **Favoritos** en el panel izquierdo. Cambia a **★**.
3. Para quitarla, entra en ella y pulsa **★**.

También puedes agregar una carpeta desde su menú (clic derecho → **Agregar a favoritos**). Los favoritos se guardan en tu navegador.

## Ordenar, filtrar y mostrar ocultos

- **Ordenar:** haz clic en el título de una columna (**Nombre**, **Tamaño**, **Modificado**, **Permisos**, **Propietario**). Un segundo clic invierte el orden. Las carpetas siempre van primero. El panel recuerda tu orden.
- **Filtrar:** escribe en **Filtrar…** y la lista muestra solo lo que contiene ese texto. `Esc` borra el filtro.
- **Ocultos:** los archivos que empiezan con `.` (por ejemplo `.htaccess` o `.env`) están ocultos. Marca **Ocultos** para verlos. El pie de la lista dice cuántos elementos están ocultos o filtrados.
- **Carpetas enormes:** si una carpeta tiene más de 20 000 entradas, la lista muestra solo las primeras 20 000 (por orden alfabético) y el pie lo indica (**solo se muestran 20000 de N entradas**). Usa **Buscar en subcarpetas** o la [Terminal](29-terminal.md) para encontrar el resto.

## Buscar en subcarpetas

1. Escribe al menos 2 caracteres en **Filtrar…**.
2. Pulsa `Enter`. El panel busca por **nombre** en la carpeta actual y todas sus subcarpetas, sin distinguir mayúsculas.
3. La lista muestra los resultados con su ruta relativa. Haz clic en uno para abrirlo, o pulsa **Volver a la carpeta**.

> **Nota:** la búsqueda se detiene en 300 resultados o 15 segundos y no entra en `/proc`, `/sys`, `/dev` ni `/run`. Si ves **límite alcanzado**, empieza desde una carpeta más concreta o escribe un nombre más preciso. Para buscar **dentro** del contenido de los archivos usa la [Terminal](29-terminal.md): `grep -rn "texto" /ruta`.

## Seleccionar varios elementos

| Cómo | Resultado |
|---|---|
| Clic en una fila | Selecciona solo esa fila. |
| `Ctrl` + clic | Agrega o quita una fila de la selección. |
| `Shift` + clic | Selecciona un rango de filas. |
| Casilla de la fila | Agrega o quita esa fila. |
| Casilla del título o `Ctrl+A` | Selecciona todo lo visible. |
| `Esc` o **✕** | Quita la selección. |

Con algo seleccionado aparece una barra con la cantidad, el tamaño y las acciones: **Copiar**, **Cortar**, **Comprimir**, **Descargar**, **Permisos** y **Borrar**. Todas funcionan con uno o con varios elementos.

## Usar el menú de acciones

Haz **clic derecho** sobre una fila (o pulsa **⋯** al final de la fila) para ver todas las acciones disponibles para ese elemento o la selección:

| Acción | Qué hace |
|---|---|
| **Abrir** / **Abrir en el editor** / **Ver imagen** | Entra en la carpeta, abre el archivo en el editor o muestra la imagen. |
| **Descargar** | Descarga el archivo, o la carpeta/selección como `.tar.gz`. |
| **Renombrar / mover** | Cambia el nombre o lo mueve a otra ruta (`F2`). |
| **Copiar** / **Cortar** | Lo deja listo para pegar en otra carpeta (`Ctrl+C` / `Ctrl+X`). |
| **Duplicar** | Crea una copia en la misma carpeta con el nombre `… (copia)`. |
| **Comprimir…** | Crea un `.tar.gz` o `.zip`. |
| **Extraer aquí** | Solo en comprimidos. |
| **Permisos y propietario…** | Cambia modo, dueño y grupo. |
| **Calcular tamaño** | Solo en carpetas: calcula cuánto ocupan con todo su contenido. |
| **Copiar ruta** | Copia la ruta completa al portapapeles. |
| **Agregar a favoritos** | Solo en carpetas. |
| **Borrar** | Borra lo seleccionado (`Supr`). |

Haz clic derecho en un espacio vacío de la lista para **Nueva carpeta**, **Nuevo archivo**, **Subir archivos…**, **Pegar aquí**, **Seleccionar todo**, **Copiar ruta de la carpeta** y **Actualizar**.

En modo solo lectura solo aparecen las acciones que no modifican nada.

### Atajos de teclado

Funcionan cuando no estás escribiendo en un campo:

| Tecla | Acción |
|---|---|
| `Enter` | Abrir el elemento seleccionado |
| `Retroceso` | Carpeta superior |
| `Alt+←` / `Alt+→` | Atrás / adelante |
| `Ctrl+A` | Seleccionar todo |
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` | Copiar / cortar / pegar |
| `F2` | Renombrar |
| `Supr` | Borrar |
| `F5` | Actualizar |
| `Esc` | Quitar la selección |

## Ver una imagen

Haz clic en una imagen PNG, JPG, GIF, WebP, BMP o ICO para verla en grande, con un botón **Descargar**. El máximo para la vista previa es 20 MB.

> **Nota:** los archivos SVG no se muestran en vista previa por seguridad (pueden contener código que se ejecutaría en el panel). Descárgalos para verlos.

## Abrir y editar un archivo

1. Haz clic en el nombre del archivo. Se abre el editor.
2. Arriba verás la ruta, el tamaño y el tipo de archivo detectado. A la izquierda, los **números de línea**.
3. Haz tus cambios. Junto al nombre aparece un **●** mientras haya cambios sin guardar.
4. Pulsa **Guardar** o `Ctrl+S`. Verás **Guardado ✓** y el editor sigue abierto para que continúes.
5. Pulsa **Cerrar** (o `Esc`) para salir.

La barra inferior muestra la línea y columna del cursor (`Ln 12, Col 5`) y el total de líneas.

| Tecla o botón | Acción |
|---|---|
| `Ctrl+S` | Guardar. |
| `Tab` | Inserta un tabulador (no salta a otro campo). |
| `Ctrl+G` o **Ir a línea** | Salta a un número de línea. |
| **Ajuste de línea** | Parte las líneas largas para verlas completas. Con el ajuste activo se ocultan los números de línea (dejarían de coincidir). El panel recuerda tu elección. |
| `Esc` o **Cerrar** | Salir del editor. |

Si un archivo no se puede abrir en el editor (muy grande o binario), el panel te ofrece **Descargarlo**.

### Límites del editor

- Solo abre archivos de hasta **2 MB**. Si es más grande verás **Archivo > 2048 KB (descárgalo)**.
- No abre archivos binarios. Verás **Archivo binario (descárgalo)**.
- El editor trabaja en UTF-8. Si el archivo usa otra codificación (por ejemplo Latin-1), se abre en **solo lectura**: los acentos se ven como `�`, el botón **Guardar** desaparece y la línea de información bajo el nombre lo explica (**solo lectura: no es UTF-8…**). Así no se puede dañar el archivo por accidente.

### Qué pasa al guardar

- Antes de escribir, el panel copia el archivo original a `<archivo>.matipanel.bak` en la misma carpeta.
- El archivo se reemplaza de forma segura y conserva sus permisos y su propietario.
- Si el archivo es un enlace simbólico, se modifica el archivo al que apunta.
- Si el archivo cambió en el disco mientras lo tenías abierto (otro usuario o programa lo modificó, o lo reemplazó por otro archivo o enlace), **no se guarda**: verás **El archivo cambió en el disco desde que lo abriste**. Copia tus cambios, cierra el editor y vuelve a abrir el archivo.

Si pulsas **Cerrar** con cambios sin guardar, aparece **Cambios sin guardar**: pulsa **Descartar cambios** para cerrar perdiéndolos, o **Seguir editando** para volver al editor.

> **Nota:** Solo se guarda **una** copia `.matipanel.bak` por archivo. Cada vez que guardas, la copia anterior se reemplaza.

## Abrir un archivo con un enlace directo (?edit=)

Puedes abrir el editor directamente con la dirección:

```
https://SERVIDOR:8443/files/?edit=/etc/hosts
```

El panel abre la carpeta del archivo y luego el editor. Otros módulos usan este enlace en sus botones **Editar** o **editar** (por ejemplo [Servidor web](21-servidor-web.md) y [PHP](23-php.md)).

Para abrir solo una carpeta usa `?path=`, por ejemplo `https://SERVIDOR:8443/files/?path=/var/www`.

## Restaurar un archivo desde su respaldo .matipanel.bak

Si un cambio rompió algo, vuelve a la versión anterior:

1. Entra en la carpeta del archivo y marca **Ocultos** si no ves la copia.
2. Pulsa **Renombrar / mover** (clic derecho o `F2`) en el archivo dañado y cámbiale el nombre, por ejemplo a `archivo.conf.roto`.
3. Pulsa **Renombrar / mover** en `archivo.conf.matipanel.bak` y ponle el nombre original, `archivo.conf`.
4. Revisa el **Propietario**: la copia de respaldo pertenece a `root`. Si el original era de otro usuario, corrígelo con **Permisos**.
5. Aplica el cambio en el servicio correspondiente (por ejemplo **Probar config** y **Recargar** en Servidor web).

## Crear un archivo o una carpeta

1. Entra en la carpeta donde quieres crearlo.
2. Pulsa **+ Archivo** o **+ Carpeta**.
3. Escribe el nombre y pulsa **Crear**.

- Al crear un archivo, se abre en el editor vacío.
- El nombre no puede contener `/` ni ser `.` o `..`. Si ya existe, verás **Ya existe**.
- Lo nuevo pertenece a `root`. Las carpetas se crean con permisos `755`. Cambia el propietario si otro usuario o servicio debe escribir ahí.

## Renombrar o mover

### Un elemento

1. Clic derecho → **Renombrar / mover** (o selecciónalo y pulsa `F2`).
2. Escribe:
    - Un nombre nuevo, para renombrar en la misma carpeta.
    - Una ruta absoluta, para mover. Por ejemplo `/var/www/archivo/viejo`.
3. Pulsa **Renombrar**.

### Varios elementos (cortar y pegar)

1. Selecciónalos y pulsa **Cortar** (o `Ctrl+X`). Las filas se ven atenuadas.
2. Ve a la carpeta de destino. Aparece una barra amarilla: **N elemento(s) para mover desde …**.
3. Pulsa **Pegar aquí** (o `Ctrl+V`).

- Si el destino ya existe, verás **El destino ya existe** para ese elemento. Nunca se sobrescribe.
- **Cancelar** en la barra amarilla descarta lo cortado sin mover nada.

## Copiar

1. Selecciona uno o varios elementos y pulsa **Copiar** (o `Ctrl+C`).
2. Ve a la carpeta de destino y pulsa **Pegar aquí** (o `Ctrl+V`).

- Si pegas en la **misma** carpeta, la copia se llama `nombre (copia).ext` (o `(copia 2)`, etc.).
- Para una copia rápida en la misma carpeta usa clic derecho → **Duplicar**.
- Las carpetas se copian completas. Los enlaces simbólicos se copian como enlaces.
- Nunca se sobrescribe: si el destino existe, verás **El destino ya existe**.
- No se puede pegar una carpeta dentro de sí misma (ni en una de sus subcarpetas): verás **No se puede copiar una carpeta dentro de sí misma**.

## Borrar

1. Selecciona uno o varios elementos y pulsa **Borrar** (o `Supr`, o clic derecho → **Borrar**).
2. El diálogo lista lo que se va a borrar. Si hay carpetas, avisa que se borran **con todo su contenido**.
3. Si hay una **carpeta**, o algo dentro de `/etc`, `/boot`, `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, `/var/lib` o `/root`, tienes que escribir para confirmar: el **nombre** del elemento si es uno solo, o la palabra `BORRAR` si son varios.
4. Pulsa **Borrar**.

> **Importante:** No hay papelera. Lo borrado no se puede recuperar. Si borras un enlace simbólico, se borra solo el enlace, no su destino.

## Rutas protegidas del sistema

Estas carpetas no se pueden borrar ni renombrar/mover, y no aceptan cambios recursivos de permisos o propietario:

`/`, `/bin`, `/boot`, `/dev`, `/etc`, `/home`, `/lib`, `/lib32`, `/lib64`, `/libx32`, `/media`, `/mnt`, `/opt`, `/proc`, `/root`, `/run`, `/sbin`, `/srv`, `/sys`, `/tmp`, `/usr`, `/usr/bin`, `/usr/lib`, `/usr/local`, `/usr/sbin`, `/var`, `/var/lib`, `/var/log`

Si lo intentas, verás **Ruta inválida o protegida** u **Origen inválido o protegido**, o **No se permite chmod recursivo en una ruta del sistema**.

> **Importante:** La protección cubre solo esas carpetas exactas. Su contenido **no** está protegido. Por ejemplo, `/etc/passwd` o `/var/www` sí se pueden borrar.

## Cambiar permisos y propietario

1. Selecciona uno o varios elementos y pulsa **Permisos** (o clic derecho → **Permisos y propietario…**).
2. Marca o desmarca **Leer**, **Escribir** y **Ejecutar** para **Propietario**, **Grupo** y **Otros**. El campo **Modo** se actualiza solo (y al revés: si escribes `750`, las casillas cambian).
3. Cambia **Propietario** y **Grupo** si hace falta.
4. Marca **Aplicar recursivamente (carpetas)** para aplicar también a todo el contenido de las carpetas seleccionadas.
5. Pulsa **Aplicar**. Con varios elementos, se aplica a cada uno.

| Campo | Qué poner |
|---|---|
| **Modo** | 3 o 4 dígitos octales, por ejemplo `644` o `0755`. |
| **Propietario** | Un usuario existente, por ejemplo `www-data`. |
| **Grupo** | Un grupo existente, por ejemplo `www-data`. |

Si el usuario o el grupo no existen, verás **Usuario o grupo inexistente**.

Los **enlaces simbólicos** no tienen permisos propios: al aplicar permisos a una selección, el modo se salta los enlaces (el propietario sí se cambia, sobre el enlace mismo). Si eliges un enlace solo, verás **Es un enlace simbólico**. Para cambiar el modo del archivo al que apunta, ábrelo en su carpeta real. Así, un enlace que otro usuario haya dejado entre sus archivos (por ejemplo hacia `/etc/shadow`) no recibe por accidente un `chmod 644`.

Valores habituales:

| Modo | Uso típico |
|---|---|
| `644` | Archivos normales (el dueño escribe, todos leen). |
| `755` | Carpetas y scripts ejecutables. |
| `640` | Archivos de configuración con datos sensibles, legibles por un grupo. |
| `600` | Claves y secretos (solo el dueño). |

> **Importante:** **Aplicar recursivamente** pone el mismo modo a archivos y carpetas. `755` recursivo vuelve ejecutables todos los archivos. `644` recursivo impide entrar a las subcarpetas. Para un sitio web, usa recursivo solo para el propietario y el grupo.

## Subir archivos

1. Entra en la carpeta de destino.
2. Pulsa **Subir…**. Se abre **Subir a** con la ruta actual.
3. Agrega archivos de una de estas formas:
    - Arrástralos a la zona **Arrastra archivos o carpetas aquí**, o haz clic en ella para elegirlos.
    - Pulsa **Seleccionar carpeta** para subir una carpeta completa.
    - Suelta archivos en cualquier parte de la página: se abre la subida sola (**Suelta para subir a**).
4. Revisa la lista. Pulsa **✕** para quitar un archivo o **Limpiar** para vaciarla.
5. Elige las opciones (ver la tabla).
6. Pulsa **Subir**. Verás una barra de progreso y luego **Procesando en el servidor…**.
7. Al terminar verás cuántos archivos se subieron y extrajeron. La ventana se cierra sola.

| Opción | Efecto |
|---|---|
| **Sobrescribir si ya existe** | Reemplaza archivos con el mismo nombre. |
| **No extraer** | Guarda los comprimidos tal cual. |
| **Extraer al finalizar** | Extrae cada `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz` o `.txz` en la carpeta de destino y conserva el comprimido. |
| **Extraer y borrar el comprimido** | Extrae y luego borra el comprimido. |

- Las carpetas se suben con su estructura de subcarpetas.
- El límite es **1 GB por subida** (suma de todos los archivos). Si lo superas verás **El total supera el límite de 1 GB por subida.** Divide la subida en partes.
- **Cancelar** detiene una subida en curso (**Subida cancelada.**). Sin subida en curso, cierra la ventana.

### Si algunos archivos ya existen

Si no marcaste **Sobrescribir si ya existe** y hay archivos repetidos, **no se sube nada**. Aparece **Archivos existentes** con la lista. Pulsa **Sobrescribir** para reemplazarlos, o **Cancelar** (verás **No se subió nada.**).

Al extraer sin sobrescribir, los archivos que ya existen en el destino se conservan.

> **Importante:** Los archivos subidos pertenecen a `root`. Si los usa un sitio web u otro servicio, cambia el propietario con **Permisos** (por ejemplo `www-data:www-data`). Al sobrescribir un archivo existente se conservan su modo y su propietario.

Por seguridad, la subida nunca escribe **a través de enlaces simbólicos**: si una subcarpeta de la subida ya existe en el destino como enlace (por ejemplo `web -> /etc`), no se sube nada y verás **… es un enlace simbólico; no se sube a través de enlaces**. Si el archivo de destino es un enlace, se reemplaza el enlace (no el archivo al que apunta).

## Descargar archivos y carpetas

1. Selecciona lo que quieres y pulsa **Descargar** (o clic derecho → **Descargar**).
2. Un solo archivo se descarga tal cual.
3. Una carpeta, o **varios elementos**, se descargan juntos en un `.tar.gz` generado en el momento.

Las descargas grandes tardan en empezar y el navegador no puede mostrar el porcentaje, porque el tamaño final no se conoce. La descarga funciona también en modo solo lectura.

No se puede descargar la raíz `/` completa: verás **No se puede descargar la raíz del sistema**. Descarga carpetas concretas, o usa [Respaldos](16-respaldos.md).

## Comprimir y extraer en el servidor

### Comprimir

1. Selecciona uno o varios elementos de la misma carpeta y pulsa **Comprimir**.
2. Escribe el **Nombre del archivo** y elige el **Formato**:
    - **.tar.gz (Linux)**: el habitual en servidores; conserva permisos y propietarios.
    - **.zip (Windows)**: para enviar a alguien que usa Windows.
3. Pulsa **Comprimir**. El comprimido se crea en la misma carpeta, con permisos `600` (solo `root` lo puede leer), porque puede contener archivos privados. Si otro usuario lo necesita, cambia sus permisos o propietario con **Permisos**.

Si ya existe un archivo con ese nombre, verás **Ya existe**. La página espera hasta que termine (máximo 1 hora).

### Extraer

1. Clic derecho en el comprimido → **Extraer aquí**.
2. Lee el aviso y pulsa **Extraer y sobrescribir**.
3. El contenido se extrae en la misma carpeta del comprimido.

Formatos: `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz` y `.txz`.

> **Importante:** **Extraer aquí** **sobrescribe** los archivos existentes con el mismo nombre. Si no quieres eso, extrae en una carpeta vacía: crea una carpeta, mueve el comprimido dentro y extrae allí.

Antes de extraer (con **Extraer aquí** o al subir), el panel revisa el contenido y **rechaza el comprimido completo** (verás **comprimido rechazado: …**) si:

- trae rutas absolutas (`/etc/…`) o con `..`;
- trae enlaces simbólicos o duros que apuntan fuera de la carpeta;
- alguna ruta pasaría por un enlace simbólico que ya existe en la carpeta y apunta fuera de ella;
- trae dispositivos, FIFOs o archivos con bit setuid/setgid.

Además, lo extraído pertenece a `root` (no se adoptan los dueños guardados en el `.tar`) y los permisos de las carpetas que ya existían no cambian. Si el comprimido está dañado verás **comprimido dañado o ilegible**.

## Ver cuánto ocupa una carpeta

La columna **Tamaño** de las carpetas muestra `—` porque calcularlo puede tardar. Para saberlo:

1. Clic derecho en la carpeta → **Calcular tamaño**.
2. Al terminar, el tamaño aparece en la fila.

Para ver el espacio libre del disco donde está la carpeta actual, mira **Disco de esta carpeta** en el panel izquierdo.

## Activar el modo solo lectura (MATIPANEL_FILE_WRITE=0)

Puedes dejar el administrador de archivos en solo lectura para todos los usuarios del panel.

1. Abre la [Terminal](29-terminal.md) y ejecuta `sudo systemctl edit matipanel`.
2. Agrega estas líneas en la zona indicada y guarda:

```
[Service]
Environment="MATIPANEL_FILE_WRITE=0"
```

3. Reinicia el panel: `sudo systemctl restart matipanel`.

Con el modo activo:

- Aparece la etiqueta **solo lectura** en la barra.
- Desaparecen **+ Archivo**, **+ Carpeta** y **Subir…**.
- El menú y la barra de selección solo ofrecen acciones que no modifican nada (abrir, descargar, calcular tamaño, copiar ruta).
- El editor abre los archivos sin permitir cambios y no muestra **Guardar**.

Para volver al modo normal, quita esa línea con `sudo systemctl edit matipanel` y reinicia el panel.

> **Nota:** Este modo afecta solo al administrador de archivos (y a los botones **Editar** de otros módulos, que lo usan). Otros módulos, como [PHP](23-php.md) al guardar `php.ini`, siguen pudiendo escribir.

## Preguntas frecuentes

### ¿Cómo copio varios archivos a otra carpeta?

Selecciónalos (con `Ctrl` + clic o las casillas), pulsa **Copiar**, ve a la otra carpeta y pulsa **Pegar aquí**.

### ¿Por qué no veo el archivo .htaccess?

Los archivos que empiezan con punto están ocultos. Marca **Ocultos** en la barra superior.

### Subí mi sitio y la aplicación no puede escribir en sus carpetas.

Los archivos subidos pertenecen a `root`. Abre **Permisos** en la carpeta del sitio (clic derecho → **Permisos y propietario…**), pon **Propietario** y **Grupo** `www-data`, marca **Aplicar recursivamente** y pulsa **Aplicar**.

### No puedo abrir un archivo: dice "Archivo > 2048 KB (descárgalo)".

El editor solo abre archivos de hasta 2 MB. Descárgalo, edítalo en tu equipo y súbelo de nuevo con **Sobrescribir si ya existe**. Para ver logs grandes usa [Logs](28-logs.md).

### ¿Hay una papelera para recuperar lo borrado?

No. Borrar es definitivo. Antes de borrar algo importante, descárgalo o usa [Respaldos](16-respaldos.md).

### Guardé un archivo de configuración y el servicio dejó de funcionar.

Restaura la copia `.matipanel.bak` como se explica en "Restaurar un archivo desde su respaldo .matipanel.bak" y vuelve a aplicar la configuración del servicio.

### En el editor veo caracteres raros (�) en lugar de acentos.

El archivo no está en UTF-8, por eso el editor lo abrió en solo lectura. Edítalo desde la [Terminal](29-terminal.md) o conviértelo antes a UTF-8 (por ejemplo `iconv -f latin1 -t utf-8`).

## Ver también

- [Servidor web](21-servidor-web.md)
- [PHP](23-php.md)
- [Respaldos](16-respaldos.md)
- [Usuarios y grupos](17-usuarios.md)
- [Terminal](29-terminal.md)
- [Logs](28-logs.md)
