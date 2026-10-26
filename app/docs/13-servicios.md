# Servicios

El módulo Servicios te permite ver y controlar los servicios de systemd del servidor: iniciarlos, detenerlos, reiniciarlos, decidir si arrancan con el sistema y consultar su estado. Lo encuentras en **Menú → Sistema → Servicios**.

## Ver la lista de servicios

Al abrir la página se cargan todos los servicios que systemd tiene cargados. Cada fila muestra:

| Columna | Significado |
|---|---|
| **Unidad** | Nombre del servicio, por ejemplo `nginx.service` |
| **Estado** | `active` (verde), `failed` (rojo), `inactive` u otros (gris) |
| **Sub** | Detalle del estado: `running`, `exited`, `dead`, `failed`… |
| **Arranque** | `enabled` (arranca con el sistema), `disabled`, `static`, `masked`, etc. |
| **Descripción** | Texto descriptivo del servicio |
| **Acciones** | Botones para controlarlo |

> **Nota:** `active` + `exited` es normal en servicios que hacen una tarea al arrancar y terminan (por ejemplo, configurar la red o el firewall). No es un error.

> **Nota:** La lista no se actualiza sola. Después de cada acción se recarga automáticamente; en otros casos usa **Refrescar**.

## Buscar un servicio

1. Escribe en el campo **Buscar servicio…**.
2. La tabla se filtra mientras escribes.

El filtro busca en el nombre de la unidad y en la descripción. Por ejemplo, `sql` encuentra `mariadb.service` y `postgresql.service`.

> **Nota:** El filtro no busca por estado. Para encontrar servicios con fallo, revisa la tarjeta **Servicios con fallo** del [Dashboard](10-dashboard.md) o busca las filas con la etiqueta roja `failed`.

## Botones de acción

| Botón | Acción (`systemctl`) | Qué hace |
|---|---|---|
| **▶** | `start` | Inicia el servicio |
| **■** | `stop` | Detiene el servicio |
| **↻** | `restart` | Lo detiene y lo vuelve a iniciar |
| **⏻ on** | `enable` | Hace que arranque con el sistema (solo aparece si está `disabled`) |
| **⏻ off** | `disable` | Evita que arranque con el sistema (solo aparece si está `enabled`) |
| **estado** | `status` | Muestra el estado detallado y las últimas 40 líneas de log |

Todas las acciones, salvo **estado**, piden confirmación. El diálogo explica qué va a pasar (por ejemplo "¿Reiniciar el servicio nginx.service? Se cortará unos segundos.") y el botón de confirmar dice **Iniciar**, **Detener**, **Reiniciar**, **Habilitar al arranque** o **Deshabilitar al arranque**. Detener, reiniciar y deshabilitar se muestran como peligrosas (botón rojo).

> **Nota:** Si un servicio está `static`, `masked` u otro estado, no verás los botones **⏻ on** / **⏻ off**. Los servicios `static` arrancan porque otro servicio los necesita, no por sí mismos.

## Reiniciar un servicio

Úsalo después de cambiar la configuración de un servicio (por ejemplo, `nginx`, `php8.4-fpm` o `mariadb`).

1. Busca el servicio con el campo **Buscar servicio…**.
2. Haz clic en **↻** en su fila.
3. Confirma con **Reiniciar**.
4. Revisa que el **Estado** quede en `active` y el **Sub** en `running`.

Si el servicio no arranca, verás un diálogo **Error** con el mensaje de systemd. Haz clic en **estado** para ver el detalle y el log.

> **Consejo:** Para servidores web, prueba la configuración antes de reiniciar (por ejemplo, `nginx -t` o `apachectl configtest`). Si la configuración tiene un error, el reinicio deja el sitio caído. El módulo [Servidor web](21-servidor-web.md) hace esta prueba por ti.

## Iniciar o detener un servicio

1. Busca el servicio.
2. Haz clic en **▶** para iniciarlo o en **■** para detenerlo.
3. Confirma con **Iniciar** o **Detener**.

> **Importante:** Detener un servicio no evita que vuelva a arrancar en el próximo reinicio del servidor. Para eso, además, deshabilítalo con **⏻ off**.

> **Importante:** El panel no te impide detener servicios críticos. Si detienes `ssh` perderás el acceso por SSH; si detienes `matipanel` perderás el acceso al panel y tendrás que iniciarlo desde la consola del servidor con `systemctl start matipanel`. Si detienes `networking`, el servidor puede quedar sin red.

## Hacer que un servicio arranque (o no) con el sistema

1. Busca el servicio.
2. Si la columna **Arranque** dice `disabled`, haz clic en **⏻ on** y confirma con **Habilitar al arranque**.
3. Si dice `enabled`, haz clic en **⏻ off** y confirma con **Deshabilitar al arranque**.

Esto solo cambia el comportamiento en el próximo arranque. No inicia ni detiene el servicio ahora. Si quieres ambas cosas, usa también **▶** o **■**.

## Ver el estado detallado y el log de un servicio

1. Haz clic en **estado** en la fila del servicio.
2. Se abre una ventana con la salida de `systemctl status`: si está activo, desde cuándo, su PID principal, la memoria que usa y las últimas 40 líneas de su log.
3. Haz clic en **Cerrar** para volver.

> **Consejo:** Para ver más líneas de log o seguirlo en vivo, usa [Logs](28-logs.md) o **Logs en vivo** y elige la unidad del servicio.

## Recuperar un servicio en estado failed

1. Haz clic en **estado** y lee las últimas líneas. Suelen indicar la causa (archivo de configuración con error, puerto ocupado, permiso denegado).
2. Corrige la causa (por ejemplo, editando la configuración en [Archivos](26-archivos.md)).
3. Haz clic en **↻** y confirma con **Reiniciar**.
4. Si el servicio no lo necesitas, detenlo con **■** y deshabilítalo con **⏻ off** para que deje de aparecer como fallido tras el próximo reinicio.

## Preguntas frecuentes

### No encuentro un servicio que sé que está instalado. ¿Por qué?

La lista muestra los servicios que systemd tiene cargados en memoria. Un servicio instalado pero deshabilitado y detenido puede no aparecer. Inícialo una vez desde la [Terminal](29-terminal.md) con `systemctl start nombre` o habilítalo con `systemctl enable nombre`, y luego haz clic en **Refrescar**.

### ¿Cuál es la diferencia entre restart y reload?

`restart` detiene y vuelve a iniciar el servicio, con un corte breve. `reload` le pide que relea su configuración sin cortar conexiones, pero no todos los servicios lo soportan. El panel solo ofrece **↻** (`restart`). Si necesitas `reload`, usa la [Terminal](29-terminal.md): `systemctl reload nginx`.

### El servicio aparece active pero el sitio no responde. ¿Qué reviso?

Revisa que esté escuchando en el puerto esperado en [Red (Interfaces)](18-red.md), en la tabla **Conexiones activas**. Luego verifica que el puerto esté abierto en [Firewall](20-firewall.md) y revisa el log del servicio con **estado**.

### ¿Puedo reiniciar el propio panel desde aquí?

Sí, reiniciando `matipanel.service` con **↻**. La página dejará de responder unos segundos y quizá tengas que volver a iniciar sesión. Además, el historial del [Dashboard](10-dashboard.md) se borra.

## Ver también

- [Dashboard](10-dashboard.md)
- [Procesos](12-procesos.md)
- [Logs](28-logs.md)
- [Servidor web](21-servidor-web.md)
- [Terminal](29-terminal.md)
- [Solución de problemas](90-solucion-de-problemas.md)
