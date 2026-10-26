# Respaldos

El módulo Respaldos crea copias comprimidas (`.tar.gz`) de las carpetas que elijas, las guarda en el servidor y te permite descargarlas o restaurarlas. También puedes programar respaldos diarios o semanales con limpieza automática de copias antiguas. Lo encuentras en **Menú → Sistema → Respaldos**.

## Cómo está organizada la página

En la barra superior tienes:

- **Nuevo respaldo**: crea un respaldo ahora mismo, una sola vez.
- **Nuevo trabajo programado**: crea un respaldo que se repite automáticamente.
- **Refrescar**: vuelve a leer la lista.
- **Destino**: la carpeta donde se guardan los respaldos, `/var/backups/matipanel`.

Debajo hay dos tablas:

- **Trabajos programados**: los respaldos automáticos configurados.
- **Respaldos existentes**: todos los archivos de respaldo guardados, del más nuevo al más antiguo.

## Dónde se guardan los respaldos

| Qué | Dónde |
|---|---|
| Archivos de respaldo | `/var/backups/matipanel/` |
| Configuración de los trabajos programados | `/var/lib/matipanel/backups.json` |
| Tarea de cron que ejecuta los trabajos | `/etc/cron.d/matipanel-backups` |
| Registro (log) de los trabajos programados | `/var/log/matipanel-backups.log` |

Cada respaldo se llama `nombre-AAAAMMDD-HHMMSS.tar.gz`. Por ejemplo, `web-20261026-023000.tar.gz` es el respaldo `web` del 26 de octubre de 2026 a las 02:30:00. Los archivos solo los puede leer `root`.

> **Importante:** Los respaldos se guardan en el mismo servidor. Si el disco falla o el servidor se pierde, los respaldos se pierden con él. Descarga copias periódicamente y guárdalas en otro lugar.

## Crear un respaldo manual

1. Haz clic en **Nuevo respaldo**.
2. En **Nombre (letras, números, _ y -)**, escribe un nombre corto, por ejemplo `web` o `config-antes-cambio`.
3. En **Rutas absolutas (una por línea)**, escribe las carpetas o archivos a respaldar, cada uno en su propia línea. Por ejemplo, `/etc` en la primera línea y `/var/www` en la segunda.
4. Opcional: en **Exclusiones**, escribe lo que no quieres incluir (ver abajo).
5. Haz clic en **Crear**. El botón cambia a **Procesando…** mientras trabaja.
6. Al terminar verás el aviso "Respaldo creado: *archivo*" y el respaldo aparecerá en **Respaldos existentes**.

> **Nota:** La página espera a que el respaldo termine. Con carpetas grandes puede tardar varios minutos. No cierres la pestaña. El límite es de una hora por respaldo.

### Reglas y límites

| Campo | Regla |
|---|---|
| Nombre | Letras, números, `_` y `-`. Máximo 40 caracteres. Sin espacios ni tildes |
| Rutas | Entre 1 y 50. Deben empezar con `/`, existir y no contener `..` |
| Rutas | No se permite respaldar `/` completo; elige carpetas concretas |
| Exclusiones | Hasta 50 patrones, de hasta 200 caracteres. Solo letras, números y `_ . * ? / [ ] -`. Sin espacios |

Si algo no cumple las reglas, verás el diálogo **No se pudo crear el respaldo** con el motivo, por ejemplo "No existe: /var/wwww".

### Escribir exclusiones

Dentro del respaldo, las rutas se guardan **sin la barra inicial** (`var/www/...`). Por eso:

- Para excluir una carpeta concreta, escríbela sin `/` al inicio: `var/www/cache`.
- Para excluir por tipo de archivo en cualquier carpeta, usa comodines: `*.log`, `*.tmp`.
- Para excluir carpetas con un nombre que se repite: `node_modules`.

> **Importante:** Una exclusión que empieza con `/` (por ejemplo `/var/www/cache`) se acepta, pero no excluye nada. Quita la barra inicial.

> **Consejo:** Si un archivo cambia mientras se respalda (un log, por ejemplo), el respaldo se considera válido igual.

## Respaldar bases de datos

Copiar los archivos de una base de datos en funcionamiento (por ejemplo, `/var/lib/mysql`) puede producir un respaldo dañado. Para bases de datos:

- Descarga un volcado desde [Bases de datos](24-bases-de-datos.md), o
- Crea una [tarea programada](14-cron.md) que haga el volcado a una carpeta (por ejemplo, `/var/backups/sql`) un rato antes del respaldo, e incluye esa carpeta en el trabajo de respaldo.

## Descargar un respaldo

1. En **Respaldos existentes**, busca el archivo.
2. Haz clic en **Descargar**.
3. Tu navegador guarda el archivo `.tar.gz`.

Puedes abrirlo en Windows con 7-Zip o con `tar -tzf archivo.tar.gz` en Linux para ver su contenido.

## Restaurar un respaldo

Restaurar extrae el contenido del respaldo en una carpeta. Hay dos formas.

### Restaurar en otra carpeta (recomendado)

Así puedes revisar los archivos y copiar solo lo que necesitas, sin tocar nada del sistema.

1. Crea primero una carpeta vacía, por ejemplo `/root/restaurar`, desde [Archivos](26-archivos.md).
2. En **Respaldos existentes**, haz clic en **Restaurar**.
3. En el diálogo **Restaurar respaldo**, cambia el destino `/` por la carpeta creada: `/root/restaurar`.
4. Haz clic en **Continuar**.
5. Confirma con **Extraer**.
6. Verás "Restauración completada.".

Los archivos quedan con su ruta completa dentro del destino. Por ejemplo, `/etc/nginx/nginx.conf` queda en `/root/restaurar/etc/nginx/nginx.conf`.

### Restaurar sobre el sistema (/)

Devuelve cada archivo a su ubicación original y **sobrescribe** la versión actual.

1. Haz clic en **Restaurar**.
2. Deja el destino en `/` y haz clic en **Continuar**.
3. En el diálogo **Restaurar sobre /**, escribe `RESTAURAR` (en mayúsculas) en el campo "Escribe RESTAURAR para confirmar". El botón **Restaurar** se habilita solo entonces.
4. Haz clic en **Restaurar**.

> **Importante:** Restaurar sobre `/` no se puede deshacer. Los archivos actuales con el mismo nombre se reemplazan. Los archivos que no estaban en el respaldo **no se borran**. Si tienes dudas, crea antes un respaldo manual del estado actual.

> **Nota:** El destino debe ser una carpeta que ya exista y empezar con `/`. Si no, verás "Destino inválido: debe ser un directorio absoluto existente". Al restaurar se conservan los dueños y permisos originales de los archivos.

> **Consejo:** Después de restaurar archivos de configuración, reinicia los servicios afectados en [Servicios](13-servicios.md).

## Restaurar un respaldo traído de otro servidor

El módulo no tiene botón para subir respaldos. Para usar un respaldo de otro servidor:

1. Sube el archivo a `/var/backups/matipanel/` con [Archivos](26-archivos.md). El límite de subida es 1 GB.
2. El nombre debe seguir el formato `nombre-AAAAMMDD-HHMMSS.tar.gz` (por ejemplo, `web-20261020-023000.tar.gz`). Si no, el panel no lo mostrará.
3. Haz clic en **Refrescar** y restáuralo como cualquier otro.

## Eliminar un respaldo

1. En **Respaldos existentes**, haz clic en **Eliminar**.
2. Confirma en el diálogo **Eliminar respaldo** con **Eliminar**.

El archivo se borra definitivamente.

## Crear un trabajo programado

Un trabajo programado crea un respaldo automáticamente cada día o cada semana, y borra las copias más antiguas.

1. Haz clic en **Nuevo trabajo programado**.
2. Completa **Nombre**, **Rutas absolutas** y, si quieres, **Exclusiones** (mismas reglas que un respaldo manual).
3. En **Frecuencia**, elige **Diario** o **Semanal**. Si eliges **Semanal**, aparece **Día** (de **Lunes** a **Domingo**).
4. En **Hora**, indica la hora de ejecución (por defecto `02:30`).
5. En **Copias a conservar**, indica cuántos respaldos guardar (entre 1 y 365; por defecto 7).
6. Haz clic en **Guardar**.

El trabajo aparece en **Trabajos programados**, con su frecuencia, por ejemplo "Diario a las 02:30" o "Semanal, domingo a las 03:00".

> **Importante:** La hora es la del servidor. Verifica la zona horaria en [Sistema](11-sistema.md).

> **Importante:** Si guardas un trabajo nuevo con el nombre de uno que ya existe, el anterior se reemplaza sin aviso. Revisa la tabla antes de guardar.

> **Consejo:** Programa los respaldos en horas de poco uso (madrugada) y escalónalos si tienes varios, para no saturar el disco.

### Cómo funciona la retención (copias a conservar)

Después de cada ejecución **exitosa**, el panel borra los respaldos más antiguos con ese mismo nombre y deja solo los últimos *N*. Con 7 copias en un trabajo diario tienes una semana de historial.

> **Importante:** La limpieza incluye los respaldos manuales que tengan el mismo nombre que el trabajo. Si quieres guardar una copia manual para siempre, usa un nombre distinto (por ejemplo, `web-manual`).

> **Nota:** Si un respaldo falla, no se borra ninguna copia anterior.

## Ejecutar un trabajo ahora

1. En **Trabajos programados**, haz clic en **Ejecutar ahora**.
2. Confirma con **Ejecutar**.
3. Espera. Al terminar verás **Trabajo completado** con la ruta del respaldo creado y, si corresponde, los respaldos eliminados por la retención. Si falla, verás **El trabajo falló** con el motivo.

Úsalo para probar un trabajo recién creado. La ejecución manual también aplica la retención.

## Editar o eliminar un trabajo programado

- **Editar**: abre el formulario **Editar trabajo**. Puedes cambiar todo menos el nombre. Haz clic en **Guardar**. Para cambiar el nombre, crea un trabajo nuevo y elimina el anterior.
- **Eliminar**: confirma con **Eliminar**. El trabajo deja de ejecutarse, pero los respaldos que ya creó se conservan.

## Revisar el log de los trabajos programados

Cada ejecución automática escribe una línea en `/var/log/matipanel-backups.log`:

```
2026-10-26 02:30:04 [web] OK: /var/backups/matipanel/web-20261026-023001.tar.gz (eliminados: web-20261019-023001.tar.gz)
2026-10-27 02:30:01 [web] ERROR: No existe: /var/www/sitio-viejo
```

Puedes verlo en [Logs](28-logs.md), en la lista de archivos de `/var/log`.

> **Consejo:** Revisa este log una vez por semana. Un trabajo puede fallar en silencio si, por ejemplo, borraste una de sus rutas o el disco se llenó.

## Preguntas frecuentes

### Mi trabajo programado no se ejecutó. ¿Qué reviso?

1. El log `/var/log/matipanel-backups.log`: si hay una línea `ERROR`, el motivo aparece ahí.
2. Que todas las rutas del trabajo sigan existiendo. Si una ruta desaparece, el trabajo falla completo.
3. Que el servicio `cron` esté activo en [Servicios](13-servicios.md).
4. Que la zona horaria del servidor sea la esperada.

### ¿Puedo guardar los respaldos en otro disco o en un recurso de red?

El destino `/var/backups/matipanel` es fijo. Si quieres que los respaldos queden en otro disco, móntalo en esa ruta (ver [Discos](15-discos.md)) o copia los archivos a otro lugar con una [tarea programada](14-cron.md).

### ¿Qué pasa con los respaldos si desinstalo MatiPanel?

El script de desinstalación no borra la carpeta `/var/backups/matipanel`; tus respaldos quedan en el servidor. Sí se borra la configuración de los trabajos programados (`/var/lib/matipanel`), pero el archivo `/etc/cron.d/matipanel-backups` queda y seguirá intentando ejecutarse sin éxito. Elimina todos los trabajos programados desde el panel antes de desinstalar, o borra ese archivo a mano después.

### ¿Los respaldos están cifrados?

No. Son archivos `.tar.gz` normales, solo legibles por `root` en el servidor. Al descargarlos, guárdalos en un lugar seguro: pueden contener contraseñas y archivos de configuración.

### ¿Qué rutas conviene respaldar en un servidor web típico?

Como punto de partida: `/etc` (toda la configuración), `/var/www` (los sitios), `/home` (archivos de usuarios) y la carpeta donde guardes los volcados de bases de datos. Excluye cachés y logs grandes, por ejemplo `*.log` y `var/www/*/cache`.

## Ver también

- [Tareas programadas (cron)](14-cron.md)
- [Bases de datos](24-bases-de-datos.md)
- [Archivos](26-archivos.md)
- [Discos](15-discos.md)
- [Logs](28-logs.md)
- [Sistema](11-sistema.md)
