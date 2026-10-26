# Archivos

El administrador de archivos te permite navegar, editar, crear, mover, copiar, borrar, subir y descargar archivos del servidor, y cambiar permisos y propietarios. Lo encuentras en **Menú → Herramientas → Archivos**.

> **Importante:** El administrador de archivos trabaja como `root`. Puedes leer y modificar cualquier archivo del sistema, incluidos los críticos. Un error aquí puede dejar el servidor sin arrancar. Trabaja con calma y verifica la ruta antes de cada acción.

## Navegar por las carpetas

- Escribe una ruta en el campo superior y pulsa **Ir** o `Enter`.
- Pulsa **↑** (Subir un nivel) o la fila `..` para ir a la carpeta superior.
- Pulsa el nombre de una carpeta para entrar.
- Debajo de la barra verás la ruta actual y la cantidad de elementos.

La dirección del navegador cambia a `?path=/ruta/actual`. Puedes guardarla como marcador para volver directo a esa carpeta.

La tabla muestra primero las carpetas y luego los archivos, en orden alfabético. Incluye los archivos ocultos (los que empiezan con `.`).

| Columna | Significado |
|---|---|
| **Nombre** | Nombre del elemento. Los enlaces simbólicos muestran `→ destino`. |
| **Tamaño** | Tamaño del archivo. Las carpetas muestran `—`. |
| **Propietario** | Usuario y grupo dueños, en formato `usuario:grupo`. |
| **Permisos** | Permisos en formato `rwx`. Pasa el mouse para ver el valor octal (por ejemplo `644`). |
| **Modificado** | Fecha y hora de la última modificación. |
| **Acciones** | Botones de acción (ver la siguiente sección). |

## Identificar los botones de acción

Cada fila tiene botones pequeños con iconos. Pasa el mouse sobre cada uno para ver su nombre.

| Icono | Nombre | Qué hace |
|---|---|---|
| ⬇ | **Descargar** | Descarga el archivo, o la carpeta como `.tar.gz`. |
| ✎ | **Renombrar / mover** | Cambia el nombre o mueve a otra carpeta. |
| ⧉ | **Copiar** | Copia a otra ruta. |
| 🔑 | **Permisos** | Cambia modo, propietario y grupo. |
| 🗜 | **Comprimir** | Crea un `.tar.gz` al lado del elemento. |
| 📦 | **Extraer** | Solo en comprimidos: extrae en la misma carpeta. |
| 🗑 | **Borrar** | Borra el archivo o la carpeta completa. |

En modo solo lectura solo aparece **Descargar**.

## Abrir y editar un archivo

1. Pulsa el nombre del archivo. Se abre el editor.
2. Arriba verás la ruta, el tamaño y el tipo de archivo detectado.
3. Haz tus cambios.
4. Pulsa **Guardar** o `Ctrl+S`.
5. Confirma en **Guardar archivo**.
6. Verás **Guardado ✓ · Backup:** con la ruta de la copia de respaldo. El editor se cierra.

Atajos del editor:

| Tecla | Acción |
|---|---|
| `Tab` | Inserta un tabulador (no salta a otro campo). |
| `Ctrl+S` | Guarda (pide confirmación). |

### Límites del editor

- Solo abre archivos de hasta **2 MB**. Si es más grande verás **Archivo > 2048 KB (descárgalo)**.
- No abre archivos binarios. Verás **Archivo binario (descárgalo)**.
- El editor trabaja en UTF-8. Si el archivo usa otra codificación (por ejemplo Latin-1), los acentos se ven como `�`. **No guardes** ese archivo: los caracteres se dañarían.

### Qué pasa al guardar

- Antes de escribir, el panel copia el archivo original a `<archivo>.matipanel.bak` en la misma carpeta.
- El archivo se reemplaza de forma segura y conserva sus permisos y su propietario.
- Si el archivo es un enlace simbólico, se modifica el archivo al que apunta.

> **Importante:** **Cerrar** descarta los cambios sin preguntar. Guarda antes de cerrar.

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

1. Entra en la carpeta del archivo.
2. Pulsa **Renombrar / mover** en el archivo dañado y cámbiale el nombre, por ejemplo a `archivo.conf.roto`.
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

1. Pulsa **Renombrar / mover** en la fila.
2. En **Renombrar / mover** escribe:
    - Un nombre nuevo, para renombrar en la misma carpeta.
    - Una ruta absoluta, para mover. Por ejemplo `/var/www/archivo/viejo`.
3. Pulsa **Renombrar**.

- Si el destino es una carpeta existente, el elemento se mueve dentro de ella.
- Si el destino ya existe, verás **El destino ya existe**. Nunca se sobrescribe.

## Copiar

1. Pulsa **Copiar** en la fila.
2. En **Copiar a (ruta absoluta de archivo o directorio)** escribe el destino. Por defecto propone `<ruta>.copia`.
3. Pulsa **Copiar**.

- Las carpetas se copian completas. Los enlaces simbólicos se copian como enlaces.
- Si el destino es una carpeta existente, la copia se crea dentro.
- Nunca se sobrescribe: si el destino existe, verás **El destino ya existe**.

> **Importante:** Escribe siempre una ruta absoluta (que empiece con `/`). Una ruta relativa no se calcula desde la carpeta que estás viendo.

## Borrar

1. Pulsa **Borrar** en la fila.
2. Lee el diálogo **Borrar archivo** o **Borrar carpeta**. Para carpetas dice **¿Borrar la carpeta y TODO su contenido?**
3. Pulsa **Borrar**.

> **Importante:** No hay papelera. Lo borrado no se puede recuperar. Si borras un enlace simbólico, se borra solo el enlace, no su destino.

## Rutas protegidas del sistema

Estas carpetas no se pueden borrar ni renombrar/mover, y no aceptan cambios recursivos de permisos o propietario:

`/`, `/bin`, `/boot`, `/dev`, `/etc`, `/home`, `/lib`, `/lib64`, `/opt`, `/proc`, `/root`, `/run`, `/sbin`, `/srv`, `/sys`, `/tmp`, `/usr`, `/var`

Si lo intentas, verás **Ruta inválida o protegida** u **Origen inválido o protegido**, o **No se permite chmod recursivo en una ruta del sistema**.

> **Importante:** La protección cubre solo esas carpetas exactas. Su contenido **no** está protegido. Por ejemplo, `/etc/passwd` o `/var/www` sí se pueden borrar.

## Cambiar permisos y propietario

1. Pulsa **Permisos** en la fila. Se abre **Permisos y propietario**.
2. Cambia lo necesario:

| Campo | Qué poner |
|---|---|
| **Modo** | 3 o 4 dígitos octales, por ejemplo `644` o `0755`. |
| **Propietario** | Un usuario existente, por ejemplo `www-data`. |
| **Grupo** | Un grupo existente, por ejemplo `www-data`. |
| **Aplicar recursivamente** | Aplica a la carpeta y a todo su contenido. |

3. Pulsa **Aplicar**. No hay confirmación adicional.

Si el usuario o el grupo no existen, verás **Usuario o grupo inexistente**.

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

> **Importante:** Los archivos subidos pertenecen a `root`. Si los usa un sitio web u otro servicio, cambia el propietario con **Permisos** (por ejemplo `www-data:www-data`).

## Descargar archivos y carpetas

1. Pulsa **Descargar** en la fila.
2. Un archivo se descarga tal cual.
3. Una carpeta se descarga como `<nombre>.tar.gz`, generado en el momento.

Las carpetas grandes tardan en empezar y el navegador no puede mostrar el porcentaje, porque el tamaño final no se conoce. La descarga funciona también en modo solo lectura.

## Comprimir y extraer en el servidor

### Comprimir

1. Pulsa **Comprimir** en la fila.
2. Confirma en **Comprimir**.
3. Se crea `<nombre>.tar.gz` en la misma carpeta.

Si ya existe un archivo con ese nombre, verás **Ya existe** con la ruta. La página espera hasta que termine (máximo 1 hora). Con carpetas grandes puede tardar varios minutos.

### Extraer

1. Pulsa **Extraer** en la fila del comprimido.
2. Confirma en **Extraer** (**¿Extraer aquí?**).
3. El contenido se extrae en la misma carpeta del comprimido.

> **Importante:** **Extraer** desde la fila **sobrescribe** los archivos existentes sin preguntar. Si no quieres eso, extrae en una carpeta vacía: crea una carpeta, mueve el comprimido dentro y extrae allí.

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
- En las filas solo queda **Descargar**.
- El editor abre los archivos sin permitir cambios y no muestra **Guardar**.

Para volver al modo normal, quita esa línea con `sudo systemctl edit matipanel` y reinicia el panel.

> **Nota:** Este modo afecta solo al administrador de archivos (y a los botones **Editar** de otros módulos, que lo usan). Otros módulos, como [PHP](23-php.md) al guardar `php.ini`, siguen pudiendo escribir.

## Preguntas frecuentes

### Subí mi sitio y la aplicación no puede escribir en sus carpetas.

Los archivos subidos pertenecen a `root`. Abre **Permisos** en la carpeta del sitio, pon **Propietario** y **Grupo** `www-data`, marca **Aplicar recursivamente** y pulsa **Aplicar**.

### No puedo abrir un archivo: dice "Archivo > 2048 KB (descárgalo)".

El editor solo abre archivos de hasta 2 MB. Descárgalo, edítalo en tu equipo y súbelo de nuevo con **Sobrescribir si ya existe**. Para ver logs grandes usa [Logs](28-logs.md).

### ¿Hay una papelera para recuperar lo borrado?

No. Borrar es definitivo. Antes de borrar algo importante, descárgalo o usa [Respaldos](16-respaldos.md).

### Guardé un archivo de configuración y el servicio dejó de funcionar.

Restaura la copia `.matipanel.bak` como se explica en "Restaurar un archivo desde su respaldo .matipanel.bak" y vuelve a aplicar la configuración del servicio.

### En el editor veo caracteres raros (�) en lugar de acentos.

El archivo no está en UTF-8. Cierra el editor sin guardar. Edítalo desde la [Terminal](29-terminal.md) o conviértelo antes a UTF-8.

## Ver también

- [Servidor web](21-servidor-web.md)
- [PHP](23-php.md)
- [Respaldos](16-respaldos.md)
- [Usuarios y grupos](17-usuarios.md)
- [Terminal](29-terminal.md)
- [Logs](28-logs.md)
