# Bases de datos

Este módulo administra MariaDB/MySQL y PostgreSQL: bases de datos, usuarios, permisos, consola SQL, respaldo y restauración. Lo encuentras en **Menú → Servidores → Bases de datos**.

## Requisitos y cómo se conecta el panel

- Necesitas MariaDB/MySQL (`apt install mariadb-server`) o PostgreSQL (`apt install postgresql`). Si no hay ninguno, la página muestra **No hay motores de base de datos instalados**.
- Si tienes los dos, verás las pestañas **MariaDB / MySQL** y **PostgreSQL**.
- El panel entra como administrador del motor, sin pedirte contraseña:
  - **MariaDB/MySQL:** como `root` por socket local. Si falla, usa las credenciales de `/etc/mysql/debian.cnf`.
  - **PostgreSQL:** como el usuario del sistema `postgres`.

> **Importante:** Todo lo que haces aquí tiene privilegios de administrador de la base de datos. Revisa bien antes de ejecutar acciones destructivas.

## Ver el estado del motor

Arriba verás cuatro indicadores:

| Indicador | Significado |
|---|---|
| **Estado** | `activo` o `detenido`, y el nombre del servicio (`mariadb`, `mysql` o `postgresql`). |
| **Versión** | Versión del servidor de base de datos. |
| **Bases de datos** | Cantidad de bases, incluidas las del sistema. |
| **Tamaño total** | Suma del tamaño de todas las bases. |

Si el panel no puede conectarse, aparece un recuadro rojo con el error. Revisa que el servicio esté iniciado en [Servicios](13-servicios.md).

## Cuentas y bases protegidas

Algunas bases y cuentas son del propio motor. El panel las marca con la etiqueta `sistema` y no permite modificarlas:

| Motor | Bases del sistema | Cuentas del sistema |
|---|---|---|
| MariaDB/MySQL | `mysql`, `information_schema`, `performance_schema`, `sys` | `root`, `mariadb.sys`, `mysql.sys`, `mysql.session`, `mysql.infoschema`, `debian-sys-maint` y el usuario anónimo |
| PostgreSQL | `postgres`, `template0`, `template1` | `postgres` |

- Las bases del sistema no se pueden eliminar ni restaurar. Sí puedes hacer **Backup** de `mysql` y de `postgres`.
- Las cuentas del sistema no tienen botones: no se les cambia la contraseña, los permisos ni se eliminan. Así el panel no pierde su propio acceso.

## Crear una base de datos

1. Escribe el nombre en el campo `nombre_nueva_bd`.
2. En MariaDB/MySQL, elige la codificación: `utf8mb4` (recomendada) o `latin1`.
3. Pulsa **+ Crear base de datos**.

El nombre solo admite letras, números y `_`, con un máximo de 63 caracteres. No se permiten guiones ni espacios.

## Eliminar una base de datos

1. Pulsa **Eliminar** en la fila de la base.
2. En **Eliminar base de datos**, escribe el nombre exacto de la base en el campo **Escribe `<nombre>` para confirmar**. El botón **Eliminar** se habilita solo cuando coincide.
3. Pulsa **Eliminar**.

> **Importante:** Esta acción borra todos los datos y no se puede deshacer. Haz un **Backup** antes. En PostgreSQL, el borrado cierra las conexiones abiertas a esa base.

## Crear un usuario

1. En la tarjeta **Usuarios**, pulsa **+ Nuevo usuario**.
2. Completa los campos:

| Campo | Qué poner |
|---|---|
| **Usuario** | Nombre de la cuenta. MariaDB/MySQL: letras, números, `_ . -` (hasta 80). PostgreSQL: letras, números y `_` (hasta 63). |
| **Host** | Solo MariaDB/MySQL. Desde dónde se conecta: `localhost` (por defecto), `%` (cualquier equipo) o un rango como `10.0.0.%`. |
| **Contraseña** | Hasta 256 caracteres. Pulsa **Generar** para crear una aleatoria; se mostrará en claro para que la copies. |
| **Otorgar todos los privilegios sobre (opcional)** | Una base de datos, o **(ninguna)**. |

3. Pulsa **Crear**.

> **Consejo:** Copia la contraseña generada antes de pulsar **Crear**. El panel no la vuelve a mostrar.

> **Nota:** En MariaDB/MySQL, `'app'@'localhost'` y `'app'@'%'` son dos cuentas distintas. Cada una aparece en su propia fila, con su propia contraseña y permisos.

## Cambiar la contraseña de un usuario

1. Pulsa **Contraseña** en la fila del usuario.
2. Escribe la nueva contraseña o pulsa **Generar**.
3. Pulsa **Guardar**. Verás **Contraseña actualizada**.

Recuerda actualizar la contraseña también en la configuración de la aplicación que usa esa cuenta.

## Otorgar o revocar permisos

1. Pulsa **Permisos** en la fila del usuario.
2. Elige la **Base de datos**.
3. Pulsa **Otorgar todo** o **Revocar todo**. Revocar pide confirmación (**Revocar privilegios**).

El panel trabaja con todos los privilegios sobre una base completa. No asigna permisos finos (solo lectura, tablas concretas). Para eso usa la consola SQL.

- **MariaDB/MySQL:** otorga `ALL PRIVILEGES` sobre `base.*`.
- **PostgreSQL:** otorga todo sobre la base, el esquema `public` y sus tablas y secuencias existentes.

La columna **Acceso total a** muestra las bases a las que el usuario tiene acceso. En PostgreSQL, `todas` indica un superusuario.

> **Nota:** En PostgreSQL, el permiso cubre las tablas que existen en ese momento. Si otro usuario crea tablas después, vuelve a pulsar **Otorgar todo**.

## Eliminar un usuario

1. Pulsa **Eliminar** en la fila del usuario.
2. Confirma en **Eliminar usuario**.

En PostgreSQL no se puede eliminar un rol que es dueño de objetos. Primero cambia el dueño o elimina esos objetos.

## Usar la consola SQL

1. En **Consola SQL**, elige la base en el selector. También puedes pulsar **Consola** en la fila de una base.
2. Escribe la consulta.
3. Pulsa **Ejecutar** o `Ctrl+Enter`.

El resultado aparece como tabla, con la cantidad de filas y el tiempo. Los errores aparecen en rojo.

| Límite | Valor |
|---|---|
| Filas mostradas | 1000 |
| Salida máxima | 2 MB |
| Tiempo máximo | 60 segundos |
| Tamaño de la consulta | 1 MB |

Si se supera un límite de salida verás **resultado truncado (máx. 1000 filas / 2 MB)**. Si la consulta tarda más de 60 segundos, se cancela.

- En MariaDB/MySQL, la opción **(sin base de datos)** ejecuta sin base seleccionada.
- En PostgreSQL, la primera opción es la base `postgres`.

> **Importante:** La consola se ejecuta como administrador del motor y no pide confirmación. Un `DROP` o `DELETE` se ejecuta al instante.

> **Consejo:** Ejecuta una consulta a la vez. Si escribes varias, la tabla mezcla los resultados.

## Hacer un respaldo (backup) de una base

1. Pulsa **Backup** en la fila de la base.
2. El navegador descarga un archivo `<base>-AAAAMMDD-HHMM.sql.gz`.

El respaldo se genera con `mysqldump` (incluye rutinas y triggers) o `pg_dump`, comprimido con gzip. Si el volcado falla a mitad, el error queda escrito como comentario al final del archivo (`-- ERROR: el volcado falló`).

> **Consejo:** Para respaldos programados del servidor, mira también [Respaldos](16-respaldos.md).

## Restaurar una base de datos

1. Si la base no existe, créala primero con **+ Crear base de datos**.
2. Pulsa **Restaurar** en la fila de la base.
3. Elige un archivo `.sql` o `.sql.gz` (máximo 512 MB).
4. Pulsa **Restaurar** y confirma en **Restaurar base de datos** con **Ejecutar**.
5. Espera el mensaje **Restauración completada**. No cierres la ventana mientras dice **Restaurando…**.

- El script se ejecuta sobre la base existente. Las tablas que contenga el archivo pueden reemplazarse.
- La importación se detiene en el primer error y muestra el mensaje del motor. Lo que ya se importó antes del error queda aplicado.

> **Importante:** Restaurar puede sobrescribir datos. Haz un **Backup** de la base antes de restaurar.

## Preguntas frecuentes

### Mi aplicación dice "Access denied" con el usuario que creé.

En MariaDB/MySQL revisa el **Host**: si la aplicación se conecta por IP o desde otro equipo, `localhost` no sirve. Crea la cuenta con el host correcto (por ejemplo `%` o `10.0.0.%`). Revisa también que tenga permisos sobre la base (**Permisos → Otorgar todo**).

### ¿Por qué no puedo cambiar la contraseña de root?

`root` es una cuenta protegida: el panel la necesita para administrar el motor. Cámbiala solo desde la terminal y sabiendo lo que haces.

### ¿Cómo copio una base a otra?

Haz **Backup** de la base original, crea la base nueva con **+ Crear base de datos** y usa **Restaurar** en la nueva con el archivo descargado.

### Mi archivo de respaldo pesa más de 512 MB. ¿Cómo lo restauro?

El panel no acepta archivos mayores de 512 MB. Súbelo al servidor con [Archivos](26-archivos.md) y restáuralo desde la [Terminal](29-terminal.md), por ejemplo: `zcat respaldo.sql.gz | sudo mysql nombre_bd`.

## Ver también

- [Respaldos](16-respaldos.md)
- [Servicios](13-servicios.md)
- [PHP](23-php.md)
- [Terminal](29-terminal.md)
- [Firewall](20-firewall.md)
