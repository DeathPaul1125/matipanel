# Respaldos de apps

Respalda automáticamente cada aplicación de [Despliegues](56-despliegues.md) —base de datos, archivos subidos y configuración— **en otro servidor Linux**, por SSH. Nada queda en este servidor: si se daña, los respaldos están a salvo. Lo encuentras en **Menú → Archivos y discos → Respaldos de apps**.

## Qué incluye cada respaldo

| Archivo | Contenido |
|---|---|
| `db.sql.gz` / `db.pgdump` | Volcado completo de la base de datos de la app (MariaDB o PostgreSQL), consistente aunque la app esté en uso. |
| `vol-<nombre>.tar.gz` | Cada volumen de datos: `storage` en Laravel o las **carpetas persistentes** en proyectos PHP (subidas, logs…). |
| `config.tar.gz` | La configuración de la app en el panel: `.env`, credenciales de la base y ajustes, para poder recrearla. |
| `manifest.json` | Tamaño y huella SHA-256 de cada archivo. |

Cada respaldo es una carpeta `<carpeta>/<app>/<AAAAMMDD-HHMMSS>/` en el servidor de respaldos.

> **Importante:** `config.tar.gz` contiene contraseñas y claves de la app. Protege el servidor de respaldos igual que este.

## Preparar el servidor de respaldos

Necesitas otro servidor Linux con SSH. Solo una vez:

1. En **Servidor de respaldos** escribe su IP o nombre, el puerto SSH, el usuario (por ejemplo `respaldos`) y la carpeta (por ejemplo `/srv/respaldos/matipanel`). Pulsa **Guardar**.
2. La tarjeta **Preparar el servidor de respaldos** muestra los comandos ya completados con esos datos y con la **clave pública** del panel. Cópialos con **Copiar comandos** y ejecútalos **como root en el servidor de respaldos**. Crean el usuario, la carpeta y autorizan la clave con `restrict` (sin terminal ni reenvíos: solo sirve para respaldos).
3. Pulsa **Probar conexión**: debe decir «Conexión correcta» y mostrar el espacio libre. La primera vez se fija la **huella** del servidor; compárala con la del servidor (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) si quieres asegurarte.

> **Nota:** Si reinstalas el servidor de respaldos su huella cambia y las conexiones fallan (es una protección). Pulsa **Olvidar huella** y vuelve a probar.

## Programación

En la tabla **Aplicaciones**, por cada app:

| Columna | Qué hace |
|---|---|
| **Programación** | **Diario** a la hora indicada (por defecto 02:00), **Semanal** (domingo a esa hora) o **Cada 6 horas**. |
| **Conservar** | Cuántos respaldos guardar en el servidor remoto (por defecto 14). Los más viejos se borran solos tras cada respaldo correcto. |
| **Activo** | Desmárcalo para pausar los respaldos automáticos de esa app. |

Pulsa **Guardar** en la fila para aplicar los cambios. Las apps nuevas se respaldan por defecto (diario, 14 copias) en cuanto haya un servidor configurado; la primera vez se hace un respaldo a los pocos minutos.

Si un respaldo falla se reintenta cada hora hasta que funcione, y con las [Notificaciones](33-notificaciones.md) activas recibes un correo.

**Respaldar ahora** lanza un respaldo en el momento. Solo se hace un respaldo o restauración a la vez en todo el servidor.

## Ver y restaurar respaldos

**Ver respaldos** abre la lista de una app en el servidor remoto con su fecha y tamaño, y el registro del último trabajo (paso a paso).

Para **restaurar**:

1. Pulsa **Restaurar…** en el respaldo elegido.
2. Marca qué restaurar:
   - **Base de datos**: se reemplaza por completo por la del respaldo.
   - **Volúmenes de datos**: se vacían y se reemplazan por los del respaldo.
3. Escribe el nombre de la app para confirmar.

La app se detiene mientras se restaura y vuelve a arrancar al terminar; la base de datos sigue encendida. No se puede restaurar mientras la app tiene un despliegue en curso o en cola.

> **Consejo:** Antes de restaurar algo importante, pulsa **Respaldar ahora**: así puedes volver al estado actual si te equivocas de respaldo.

**Eliminar** borra un respaldo del servidor remoto.

## Preguntas frecuentes

### ¿Cuánto espacio ocupan?

Lo que pese la base comprimida más los archivos subidos. La lista de respaldos muestra el tamaño de cada uno y **Probar conexión** el espacio libre en el destino.

### ¿Puedo restaurar en otro servidor?

Sí, a mano: copia la carpeta del respaldo, crea la app en el otro MatiPanel y restaura `db.sql.gz` (`gunzip -c db.sql.gz | mariadb`) y los volúmenes (`tar -xzf vol-….tar.gz -C <volumen>`). En `config.tar.gz` está el `.env` original.

## Ver también

- [Despliegues](56-despliegues.md)
- [Respaldos](16-respaldos.md): respaldos de carpetas de este servidor.
- [Notificaciones](33-notificaciones.md)
