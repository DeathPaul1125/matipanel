# Auditoría

El registro de auditoría guarda quién hizo qué en el panel, cuándo y desde qué IP. Lo encuentras en **Menú → Herramientas → Auditoría**.

## Qué se registra

- **Toda acción que cambia algo:** reiniciar un servicio, crear un usuario, cambiar una regla del firewall, desplegar, guardar un archivo, borrar, restaurar… (cualquier botón que modifica el servidor).
- **Descargas sensibles:** archivos, volcados de bases de datos, exportaciones, capturas de tráfico, logs de despliegue y el `.env` de las apps.
- **Inicios de sesión**, correctos y fallidos (con el usuario que se intentó), y el segundo factor (2FA).
- **Apertura de terminales** web.

No se registran las consultas (abrir una pantalla, refrescar una tabla).

> **Nota:** Las contraseñas, claves, tokens, el contenido de archivos y los valores del `.env` **nunca** se guardan: aparecen como `•••`.

## Consultar el registro

| Filtro | Uso |
|---|---|
| **Periodo** | Último día, 7, 30, 90 o 365 días. |
| **Usuario** | Solo las acciones de una cuenta. |
| **Módulo** | Solo un área del panel (Servicios, Firewall, Despliegues…). |
| **Resultado** | Solo las acciones correctas o solo las fallidas. |
| **Buscar** | Texto en la acción, el objetivo, la IP o el detalle, por ejemplo `nginx` o `192.168.1.50`. |

Cada fila muestra la fecha, el usuario, la IP, el módulo, la acción, el objetivo (el servicio, archivo, app… afectado), el resultado con su código HTTP y el detalle de los parámetros. La tabla se puede ordenar por columna y paginar. Se actualiza sola cada 30 s.

**Exportar CSV** descarga todos los eventos que cumplen los filtros (sin el tope de 2000 de la pantalla), listo para Excel.

## Dónde se guarda

En `/var/lib/matipanel/audit.db` (legible solo por root). Se conserva **un año**; los eventos más antiguos se borran solos.

> **Consejo:** Si una IP aparece con muchos inicios de sesión fallidos, bloquéala en el [Firewall](20-firewall.md). Con las [Notificaciones](33-notificaciones.md) activas recibirás un correo cuando el panel la bloquee.

## Ver también

- [Seguridad y 2FA](30-seguridad-2fa.md)
- [Notificaciones](33-notificaciones.md)
