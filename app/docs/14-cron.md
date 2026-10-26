# Tareas programadas (cron)

Este módulo te permite crear, editar, activar, desactivar y eliminar tareas que el servidor ejecuta automáticamente según un horario (cron). También puedes consultar las tareas del sistema. Lo encuentras en **Menú → Sistema → Tareas (cron)**.

## Cómo está organizada la página

Hay dos pestañas:

- **Crontabs de usuario**: las tareas de cada usuario. Aquí puedes crear y modificar.
- **Sistema (solo lectura)**: el contenido de `/etc/crontab` y de los archivos de `/etc/cron.d/`. Solo se puede ver.

En la pestaña de usuario, la barra superior tiene:

- Un selector con el usuario cuyas tareas estás viendo.
- **+ Nueva tarea**: abre el formulario para crear una tarea.
- **↻ Recargar**: vuelve a leer las tareas del usuario.

### Qué usuarios aparecen en el selector

- `root` (siempre, en primer lugar).
- Los usuarios normales (UID de 1000 en adelante).
- Cualquier otro usuario que ya tenga una crontab, por ejemplo `www-data`.

> **Consejo:** Ejecuta cada tarea con el usuario que corresponde. Por ejemplo, las tareas de una aplicación web suelen ir en la crontab de `www-data`, no en la de `root`. Así no generan archivos con dueño incorrecto.

## Ver las tareas de un usuario

1. Elige el usuario en el selector.
2. La tabla muestra cada tarea con su **Estado** (`activa` o `desactivada`), su **Programación**, su **Comando** y los botones de **Acciones**.

Si la crontab tiene variables de entorno (por ejemplo `MAILTO=` o `PATH=`), aparecen en una tarjeta aparte, **Variables de entorno**. Esa tarjeta es de solo lectura.

> **Nota:** Los comentarios y las líneas que no son tareas se conservan tal cual al guardar. El panel solo modifica la línea de la tarea que editas.

## Crear una tarea programada

1. Elige el usuario en el selector.
2. Haz clic en **+ Nueva tarea**. El título del formulario indica el usuario: "Nueva tarea (*usuario*)".
3. En **Frecuencia**, elige una opción predefinida o deja **Personalizada**.
4. Si elegiste **Personalizada**, completa los cinco campos: **Minuto**, **Hora**, **Día mes**, **Mes** y **Día semana** (ver la tabla de sintaxis más abajo).
5. En **Comando**, escribe el comando completo. Usa rutas absolutas.
6. Deja marcada la casilla **Activa** si quieres que empiece a ejecutarse ya.
7. Haz clic en **Guardar**.

Si algo no es válido, verás el diálogo **No se pudo guardar la tarea** con el motivo: "Programación inválida" o "Comando inválido (vacío o con saltos de línea)".

### Frecuencias predefinidas

| Opción | Equivale a | Cuándo se ejecuta |
|---|---|---|
| **Al arrancar (@reboot)** | — | Una vez, cada vez que arranca el servidor |
| **Cada hora (@hourly)** | `0 * * * *` | Al minuto 0 de cada hora |
| **Diaria (@daily)** | `0 0 * * *` | Todos los días a medianoche |
| **Medianoche (@midnight)** | `0 0 * * *` | Igual que @daily |
| **Semanal (@weekly)** | `0 0 * * 0` | Domingos a medianoche |
| **Mensual (@monthly)** | `0 0 1 * *` | El día 1 de cada mes a medianoche |
| **Anual (@yearly)** / **Anual (@annually)** | `0 0 1 1 *` | El 1 de enero a medianoche |

Al elegir una opción predefinida, los cinco campos se ocultan.

## Entender la sintaxis de cron

Una programación personalizada tiene cinco campos separados por espacios, en este orden:

| Campo | Valores permitidos |
|---|---|
| **Minuto** | `0`–`59` |
| **Hora** | `0`–`23` |
| **Día mes** | `1`–`31` |
| **Mes** | `1`–`12` o `jan`–`dec` |
| **Día semana** | `0`–`7` (0 y 7 son domingo, 1 es lunes) o `sun`–`sat` |

Dentro de cada campo puedes usar:

| Símbolo | Significado | Ejemplo |
|---|---|---|
| `*` | Cualquier valor | `*` en Hora = cada hora |
| `,` | Lista de valores | `1,15` = los días 1 y 15 |
| `-` | Rango | `1-5` = de lunes a viernes |
| `/` | Intervalo | `*/10` = cada 10 |

Los nombres de meses y días van en inglés y con tres letras: `jan`, `feb`… `dec`; `sun`, `mon`, `tue`, `wed`, `thu`, `fri`, `sat`.

> **Nota:** El panel solo acepta esos símbolos. Caracteres de otras variantes de cron, como `?`, `L` o `#`, se rechazan con "Programación inválida". Tampoco se permiten espacios dentro de un campo.

### Ejemplos comunes

| Minuto | Hora | Día mes | Mes | Día semana | Se ejecuta |
|---|---|---|---|---|---|
| `*/5` | `*` | `*` | `*` | `*` | Cada 5 minutos |
| `0` | `*` | `*` | `*` | `*` | Cada hora, en punto |
| `30` | `2` | `*` | `*` | `*` | Todos los días a las 02:30 |
| `0` | `7` | `*` | `*` | `1-5` | De lunes a viernes a las 07:00 |
| `0` | `8,13,18` | `*` | `*` | `*` | Todos los días a las 08:00, 13:00 y 18:00 |
| `0` | `22` | `*` | `*` | `sun` | Domingos a las 22:00 |
| `0` | `3` | `1` | `*` | `*` | El día 1 de cada mes a las 03:00 |
| `0` | `*/2` | `*` | `*` | `*` | Cada 2 horas |
| `15` | `0` | `1` | `jan,jul` | `*` | 1 de enero y 1 de julio a las 00:15 |

> **Importante:** Si llenas **Día mes** y **Día semana** a la vez (ninguno con `*`), cron ejecuta la tarea cuando se cumple **cualquiera** de los dos, no ambos. Por ejemplo, `0 9 1 * mon` corre el día 1 de cada mes **y** todos los lunes.

> **Importante:** Las horas son las del servidor. Revisa la zona horaria en [Sistema](11-sistema.md) antes de programar.

## Escribir bien el comando

### El carácter % debe escaparse

En una crontab, el carácter `%` significa "salto de línea". Si tu comando lleva `%` (muy común en `date`), escríbelo como `\%`.

- Incorrecto: `tar -czf /root/web-$(date +%F).tar.gz /var/www`
- Correcto: `tar -czf /root/web-$(date +\%F).tar.gz /var/www`

Si no lo escapas, el comando se corta en el primer `%` y fallará sin un error visible.

### Otras recomendaciones

- **Usa rutas absolutas.** Cron trabaja con un `PATH` mínimo. Escribe `/usr/bin/php` en lugar de `php`.
- **Guarda la salida en un log.** Si no, se pierde (o se envía por correo local, si hay un servidor de correo). Ejemplo: `/usr/local/bin/limpieza.sh >> /var/log/limpieza.log 2>&1`.
- **Silencia la salida si no la necesitas:** `>/dev/null 2>&1` al final.
- El comando no puede tener saltos de línea y admite hasta 4096 caracteres. Para algo largo, crea un script y llama al script.

> **Consejo:** Prueba el comando primero en la [Terminal](29-terminal.md) con el mismo usuario. Si funciona ahí con la ruta completa, funcionará en cron.

## Editar una tarea

1. Haz clic en **Editar** en la fila de la tarea.
2. Se abre el formulario **Editar tarea** con los valores actuales.
3. Haz los cambios y haz clic en **Guardar**.

> **Nota:** Si otra persona (u otro programa) cambió la crontab mientras tenías el formulario abierto, verás "La crontab cambió mientras la editabas; recarga". Haz clic en **↻ Recargar** y repite el cambio.

## Desactivar o activar una tarea

1. Haz clic en **Desactivar** en la fila de la tarea. No pide confirmación.
2. La tarea queda como `desactivada`: el panel la convierte en un comentario (`# ` al inicio de la línea). No se ejecuta, pero se conserva.
3. Para reactivarla, haz clic en **Activar**.

Es la forma segura de pausar una tarea sin perder su configuración.

## Eliminar una tarea

1. Haz clic en **Eliminar** en la fila de la tarea.
2. El diálogo **Eliminar tarea** muestra la programación y el comando.
3. Confirma con **Eliminar**.

La línea se borra de la crontab. No se puede deshacer.

## Consultar las tareas del sistema

1. Abre la pestaña **Sistema (solo lectura)**.
2. Verás una tarjeta por archivo: `/etc/crontab` y cada archivo de `/etc/cron.d/`.

Estos archivos los instalan los paquetes del sistema. A diferencia de las crontabs de usuario, tienen un campo extra con el usuario que ejecuta la tarea (después del día de la semana).

Aquí también aparece `/etc/cron.d/matipanel-backups`, que el panel genera para los respaldos programados. No lo edites a mano; se gestiona desde [Respaldos](16-respaldos.md).

> **Nota:** El panel no muestra los scripts de `/etc/cron.daily/`, `/etc/cron.weekly/` ni `/etc/cron.monthly/`. Puedes verlos desde [Archivos](26-archivos.md).

## Preguntas frecuentes

### Aparece una tarea "desactivada" que nunca creé: `0 5 * * 1 tar -zcf /var/backups/home.tgz /home/`. ¿Qué es?

Es un ejemplo que Debian incluye como comentario cuando una crontab se crea con `crontab -e`. El panel lo interpreta como una tarea desactivada. No la actives. Puedes eliminarla con **Eliminar** sin ningún riesgo.

### Mi tarea no se ejecuta. ¿Qué reviso?

1. Que el **Estado** diga `activa`.
2. Que el comando use rutas absolutas y tenga los `%` escapados como `\%`.
3. Que el usuario tenga permiso sobre los archivos que usa el comando.
4. Que el servicio `cron` esté `active` en [Servicios](13-servicios.md).
5. El log del sistema: en [Logs](28-logs.md), filtra por la unidad `cron`. Ahí se registra cada ejecución.

### ¿A qué hora se ejecuta @daily?

A las 00:00 hora del servidor. Si necesitas otra hora, usa **Personalizada**: por ejemplo, `30` en **Minuto** y `2` en **Hora** para las 02:30.

### ¿Puedo editar las variables de entorno (MAILTO, PATH) desde el panel?

No. La tarjeta **Variables de entorno** es solo informativa. Para cambiarlas, usa `crontab -e -u usuario` en la [Terminal](29-terminal.md).

## Ver también

- [Respaldos](16-respaldos.md)
- [Sistema](11-sistema.md)
- [Terminal](29-terminal.md)
- [Logs](28-logs.md)
- [Usuarios y grupos](17-usuarios.md)
