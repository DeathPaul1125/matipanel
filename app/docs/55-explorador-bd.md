# Explorador de bases de datos

El explorador te deja ver y editar el contenido de tus bases de MariaDB/MySQL y PostgreSQL desde el navegador, al estilo de phpMyAdmin o Adminer: navegar tablas, filtrar y ordenar datos, editar filas, cambiar la estructura, ejecutar SQL, exportar e importar. Lo encuentras en **Menú → Bases de datos → Explorador BD**. También puedes entrar desde [Bases de datos](24-bases-de-datos.md) con el botón **Explorar** de cada base.

> **Importante:** El explorador trabaja como administrador del motor (`root` en MariaDB/MySQL, `postgres` en PostgreSQL). Puedes leer y cambiar cualquier tabla. Antes de una operación destructiva, haz un respaldo con **Exportar** o con el botón **Backup** de [Bases de datos](24-bases-de-datos.md).

## Conocer la pantalla

| Zona | Qué contiene |
|---|---|
| **Panel izquierdo** | El selector de motor (si tienes los dos instalados), el filtro **Filtrar bases y tablas…** y el árbol: bases → (esquemas en PostgreSQL) → tablas y vistas, con el número aproximado de filas. |
| **Migas** | Dónde estás: **Motor › Base › Tabla**. Haz clic en un tramo para volver a él. |
| **Pestañas** | Cambian según lo que elegiste: una base o una tabla. |

- Al abrir una base, el árbol carga sus tablas en ese momento. El filtro busca en las bases y en las tablas ya cargadas.
- La dirección del navegador guarda la base, la tabla y la pestaña. Puedes guardarla como marcador o usar los botones **atrás** y **adelante** del navegador.
- **Refrescar** (abajo del árbol) vuelve a leer la lista de bases y tablas.

> **Nota:** El número de filas del árbol y de la lista de tablas es una **estimación** del motor (en MySQL puede tardar en actualizarse). El número exacto aparece en la pestaña **Datos**.

## Ver las tablas de una base

1. Haz clic en la base en el panel izquierdo. En PostgreSQL elige también el esquema (por defecto `public`).
2. En la pestaña **Tablas** verás cada tabla y vista con sus filas aproximadas, el tamaño y, en MariaDB/MySQL, el motor y el cotejamiento.
3. Usa **Datos** o **Estructura** para abrir una tabla, o los botones **Vaciar** y **Eliminar**.

En PostgreSQL puedes cambiar de esquema con la lista **Esquema** de la barra superior.

## Ver y filtrar los datos

1. Abre una tabla. Se muestra la pestaña **Datos**.
2. Elige cuántas filas ver en **Filas por página** (25, 50, 100 o 500) y muévete con **‹ Anterior**, **Siguiente ›** o escribiendo el número en **Página**.
3. Para **ordenar**, haz clic en el nombre de una columna: una vez ascendente (▲), otra descendente (▼) y una tercera vez quita el orden.
4. Para **filtrar**, usa la fila bajo los encabezados: elige el operador y escribe el valor; pulsa `Enter` para aplicarlo. Si escribes sin elegir operador, se usa **contiene**.
5. **Quitar filtros** borra todos los filtros.

| Operador | Qué busca |
|---|---|
| **contiene** | El valor aparece en cualquier parte (en PostgreSQL sin distinguir mayúsculas). |
| **=**, **≠**, **>**, **<** | Comparación con el valor. **≠** incluye las filas con NULL. |
| **empieza con** | El valor está al principio. |
| **es NULL** / **no es NULL** | La celda está vacía (NULL) o tiene algo. |

En columnas binarias puedes buscar un valor exacto en hexadecimal, por ejemplo `0x89504e47`.

### Cómo se muestran los valores

| Se ve | Significa |
|---|---|
| *NULL* en cursiva gris | El valor es NULL (sin dato). Es distinto del texto `NULL`, que se ve normal. |
| vacío (recuadro punteado) | Texto vacío `''`. |
| Texto subrayado con puntos y `…` | Texto largo recortado. Haz clic para verlo completo. |
| `0x…` en morado con el tamaño | Dato binario (BLOB, bytea): se muestran los primeros bytes en hexadecimal. |

Al hacer clic en una celda larga se abre el visor. Si el valor está recortado, pulsa **Cargar valor completo** (hasta 256 KB). Con **Copiar** lo copias al portapapeles. El JSON se muestra con formato.

### Total de filas

En tablas pequeñas (hasta unas 50 000 filas estimadas) el total es exacto. En tablas grandes se muestra la estimación del motor para no hacer lento el servidor; pulsa **Contar exacto** si necesitas el número real (respeta los filtros).

## Editar, insertar y borrar filas

Solo puedes modificar datos de una tabla que tenga **clave primaria** o un **índice único sobre columnas NOT NULL**. Con esa clave el panel identifica cada fila sin ambigüedad y nunca toca más de una fila por cambio. Si la tabla no tiene clave, o es una vista, verás un aviso y los datos serán de solo lectura. Para poder editarla, agrega una clave primaria en **Estructura → + Agregar índice**.

### Editar una fila

1. Pulsa el lápiz al inicio de la fila (o haz doble clic en una celda).
2. Cambia los valores. Marca **NULL** para dejar una columna sin dato.
3. Pulsa **Guardar cambios**.

- Solo se envían las columnas que cambiaste.
- Las columnas binarias no se cambian salvo que marques **Reemplazar** y escribas el nuevo valor en hexadecimal.
- Los textos muy largos (más de 256 KB) no se pueden editar aquí: usa la pestaña **SQL**.
- Si otra persona borró la fila o cambió su clave mientras la editabas, verás **No se actualizó ninguna fila** y no se modifica nada. Recarga la tabla.

### Insertar o duplicar una fila

1. Pulsa **+ Insertar fila**, o el icono de copiar de una fila para **duplicarla**.
2. Las columnas con **Predeterminado** marcado no se envían: el motor pone su valor por defecto (por ejemplo el siguiente número de un `AUTO_INCREMENT` o la fecha actual). Desmárcalo para escribir un valor.
3. Pulsa **Insertar**.

### Borrar filas

1. Marca las casillas de las filas (la casilla del encabezado marca todas las de la página).
2. Pulsa **Eliminar seleccionadas** y confirma con **Eliminar**.

Si borras más de 10 filas a la vez, el panel te pide escribir `ELIMINAR` para confirmar. Todas las filas se borran en una sola transacción: si una falla (por ejemplo, por una clave foránea), no se borra ninguna.

> **Importante:** Borrar filas no se puede deshacer. Exporta la tabla antes si tienes dudas.

## Ver y cambiar la estructura

La pestaña **Estructura** muestra:

- **Columnas:** nombre, tipo, si admite NULL, valor por defecto, clave, extra (por ejemplo `auto_increment` o identidad) y comentario.
- **Índices:** nombre, columnas y tipo (PRIMARY, UNIQUE o INDEX).
- **Claves foráneas:** columnas, tabla referenciada (haz clic para abrirla) y las reglas ON UPDATE / ON DELETE.
- **Información:** motor, cotejamiento, filas aproximadas, tamaño y la clave que usa el panel para identificar filas.

Cada cambio de estructura funciona igual:

1. Completa el formulario.
2. Pulsa **Vista previa y aplicar…**. El panel te muestra el SQL exacto que va a ejecutar.
3. Revísalo y pulsa **Ejecutar**.

### Agregar o modificar una columna

1. Pulsa **+ Agregar columna**, o **Modificar** en la fila de una columna.
2. Completa:

| Campo | Qué poner |
|---|---|
| **Nombre** | Letras, números y `_`; empieza con letra o `_`. |
| **Tipo** | Uno de la lista. Al modificar puedes elegir **(mantener: …)** para no cambiarlo. |
| **Longitud** | Según el tipo: longitud de texto (`255`), precisión de número (`10,2`) o decimales de segundo (`0` a `6`). |
| **Valores permitidos** | Solo para `ENUM` y `SET`: uno por línea. |
| **Valor por defecto** | Ninguno, NULL, un valor fijo o una expresión de la lista (por ejemplo `CURRENT_TIMESTAMP`). |
| **Permite NULL** | Marca si la columna puede quedar vacía. |
| **UNSIGNED** / **AUTO_INCREMENT** / **Identidad** | Solo para números enteros. |
| **Posición** | Solo en MariaDB/MySQL al agregar: al final, al principio o después de otra columna. |

> **Nota:** Cambiar el tipo de una columna con datos puede fallar o recortar valores si no caben en el tipo nuevo. Exporta la tabla antes de cambios grandes.

### Eliminar una columna

Pulsa **Eliminar** en la fila de la columna y escribe su nombre para confirmar. Se borran los datos de esa columna en todas las filas.

### Agregar o eliminar un índice

1. Pulsa **+ Agregar índice**.
2. Elige el tipo (**INDEX**, **UNIQUE**, **PRIMARY KEY** si la tabla aún no tiene, o **FULLTEXT** en MariaDB/MySQL) y, si quieres, un nombre.
3. Marca las columnas en el orden que quieras: el número junto a cada una indica su posición en el índice.
4. Pulsa **Vista previa y crear…** y luego **Ejecutar**.

En MariaDB/MySQL, las columnas TEXT o BLOB se indexan por sus primeros 191 caracteres. Para borrar un índice, pulsa **Eliminar** en su fila.

## Crear una tabla

1. Abre la base y entra en la pestaña **Crear tabla** (o pulsa **+ Crear tabla** en la lista de tablas).
2. Escribe el **Nombre de la tabla**.
3. Ajusta las columnas. Por defecto hay una columna `id` entera, auto-incremental y clave primaria, y una columna de texto.
4. Usa **+ Columna** para agregar más y ✕ para quitar.
5. Pulsa **Vista previa y crear…**, revisa el SQL y pulsa **Ejecutar**.

Al terminar se abre la nueva tabla en su pestaña **Estructura**. La columna auto-incremental (o de identidad en PostgreSQL) debe ser parte de la clave primaria.

## Ejecutar SQL

La pestaña **SQL** de una tabla trae escrito `SELECT * FROM tabla LIMIT 100`; la de una base aparece vacía.

1. Escribe una o varias sentencias separadas por `;`.
2. Pulsa **Ejecutar** o `Ctrl+Enter`.
3. Cada sentencia muestra su resultado en una tabla, o el número de **filas afectadas**. Arriba ves el tiempo total y cuántas sentencias se ejecutaron.

- Se ejecuta dentro de la base elegida (y del esquema, en PostgreSQL).
- Límites: 1000 filas por resultado, 2 MB de salida, 60 segundos y 50 sentencias por ejecución.
- Si una sentencia falla, se detiene ahí. Verás el error y los resultados de las anteriores.
- **Historial…** guarda tus últimas 30 consultas de cada base en este navegador.
- **Exportar resultado (CSV)** descarga el último resultado con filas que se muestra en pantalla.
- Los comandos del cliente (los que empiezan con `\`, como `\!`, o `source`, `system`, `delimiter`) no están permitidos.

> **Importante:** Las sentencias que modifican datos (`UPDATE`, `DELETE`, `DROP`…) se aplican al instante y no se pueden deshacer. Si dudas, ejecuta primero un `SELECT` con el mismo `WHERE` para ver qué filas tocaría.

## Operaciones de tabla

En la pestaña **Operaciones**:

| Operación | Qué hace |
|---|---|
| **Renombrar…** | Cambia el nombre de la tabla o vista. Revisa vistas, código y permisos que usen el nombre anterior. |
| **Copiar…** | Crea una tabla nueva con la misma estructura, con o sin los datos. |
| **OPTIMIZE TABLE** / **ANALYZE TABLE** (MariaDB/MySQL) | Recupera espacio y actualiza estadísticas. |
| **VACUUM (ANALYZE)** / **ANALYZE** (PostgreSQL) | Recupera el espacio de filas borradas y actualiza estadísticas. |
| **Vaciar tabla (TRUNCATE)…** | Borra todas las filas y conserva la estructura. Escribe el nombre de la tabla para confirmar. |
| **Eliminar tabla…** | Borra la tabla con sus datos. Escribe el nombre para confirmar. |

> **Nota:** Al copiar en MariaDB/MySQL no se copian las claves foráneas. En PostgreSQL, las columnas `serial` de la copia siguen usando la secuencia de la tabla original.

## Exportar

En la pestaña **Exportar / Importar** de una tabla:

- **SQL (.sql.gz):** estructura y datos de la tabla, comprimidos.
- **CSV:** una fila por registro, con encabezados. NULL es un campo vacío y el texto vacío va entre comillas (`""`). Los binarios van en hexadecimal.
- **JSON:** una lista de objetos, uno por fila.

Para exportar la **base completa**, abre la base y usa la pestaña **Exportar** → **Descargar SQL (.sql.gz)**. Es el mismo volcado que el botón **Backup** de [Bases de datos](24-bases-de-datos.md).

Los archivos se descargan con el nombre de la base, la tabla y la fecha, por ejemplo `tienda-clientes-20250101-0930.csv`.

## Importar un CSV

1. Abre la tabla y entra en **Exportar / Importar**.
2. Elige el archivo (máximo 100 MB) y la **Codificación**: **UTF-8** o **Latin-1 (Windows)** para archivos de Excel antiguos.
3. Pulsa **Vista previa**. Verás las primeras 20 filas.
4. Revisa las opciones:
   - **Separador:** se detecta solo (coma, punto y coma, tabulador o barra). Cámbialo si la vista previa se ve mal.
   - **La primera fila son encabezados:** márcalo si la primera fila tiene nombres de columnas.
   - **Celdas vacías como NULL:** las celdas vacías quedan como NULL en columnas que lo admiten.
   - **Omitir filas duplicadas:** las filas que chocan con una clave única se saltan en vez de detener la importación.
5. En la fila de selectores bajo los encabezados, elige a qué columna de la tabla va cada columna del CSV, o **(ignorar)**. Si el CSV tiene encabezados con los mismos nombres, se asignan solos.
6. Pulsa **Importar…** y confirma.

Al terminar verás cuántas filas se insertaron, cuántas se omitieron por duplicadas y qué filas tenían errores de formato (por ejemplo, menos campos de los necesarios).

- Todo se inserta en una sola transacción: si el motor rechaza una fila (un número inválido, un texto demasiado largo…), no se inserta ninguna y verás el error.
- Las filas con menos campos de los necesarios se saltan y aparecen en la lista de errores.
- El valor `\N` siempre se importa como NULL.

> **Nota:** En MariaDB/MySQL, las tablas con motor MyISAM no admiten transacciones: si la importación falla a la mitad, las filas anteriores al error quedan insertadas.

## Bases y esquemas del sistema

Puedes ver las bases del sistema (`mysql`, `information_schema`, `performance_schema`, `sys` en MariaDB/MySQL; `postgres`, `template1` en PostgreSQL) y los esquemas `pg_catalog` e `information_schema`, pero en **solo lectura**: no se editan filas, no se cambia la estructura y la consola SQL solo acepta consultas de lectura (`SELECT`, `SHOW`, `EXPLAIN`…).

## Preguntas frecuentes

### ¿Por qué no puedo editar los datos de una tabla?

La tabla no tiene clave primaria ni un índice único sobre columnas NOT NULL, es una vista, o está en una base del sistema. El aviso de la pestaña **Datos** dice cuál es el motivo. Si es una tabla tuya sin clave, agrega una en **Estructura → + Agregar índice → PRIMARY KEY**.

### El total dice "≈" y no coincide con lo que veo.

Es la estimación del motor para tablas grandes. Pulsa **Contar exacto**. Después de muchos cambios, **ANALYZE** en **Operaciones** actualiza la estimación.

### Un texto aparece cortado con "…".

La tabla muestra los primeros caracteres para no cargar demasiado. Haz clic en la celda para verlo y pulsa **Cargar valor completo**.

### Mi base no aparece o sale atenuada en la lista.

El explorador solo abre bases cuyo nombre tiene letras, números, `_`, `$` o `-`. Las demás se muestran atenuadas; adminístralas desde la consola de [Bases de datos](24-bases-de-datos.md) o la [Terminal](29-terminal.md).

### La importación dice que el archivo no está en la codificación elegida.

El archivo no es UTF-8. Vuelve a elegirlo con **Codificación: Latin-1 (Windows)** y pulsa **Vista previa** otra vez.

### ¿El explorador reemplaza a phpMyAdmin?

Cubre el trabajo diario: ver, filtrar, editar, estructura, SQL, exportar e importar. Para tareas avanzadas (usuarios y permisos, rutinas, triggers) usa [Bases de datos](24-bases-de-datos.md) o la pestaña **SQL**.

## Ver también

- [Bases de datos](24-bases-de-datos.md)
- [Respaldos](16-respaldos.md)
- [Terminal](29-terminal.md)
- [Solución de problemas](90-solucion-de-problemas.md)
