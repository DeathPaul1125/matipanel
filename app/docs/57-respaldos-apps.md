# Respaldos de apps

Respalda automáticamente cada aplicación de [Despliegues](56-despliegues.md) —base de datos, archivos subidos y configuración— **en otros servidores**: Linux o Unix por SSH, o Windows (y NAS) por carpeta compartida con una cuenta del Active Directory. Puedes tener varios servidores y cada app elige el suyo. Nada queda en este servidor: si se daña, los respaldos están a salvo. Lo encuentras en **Menú → Archivos y discos → Respaldos de apps**.

## Qué incluye cada respaldo

| Archivo | Contenido |
|---|---|
| `db.sql.gz` / `db.pgdump` | Volcado completo de la base de datos de la app (MariaDB o PostgreSQL), consistente aunque la app esté en uso. |
| `vol-<nombre>.tar.gz` | Cada volumen de datos: `storage` en Laravel o las **carpetas persistentes** en proyectos PHP (subidas, logs…). |
| `config.tar.gz` | La configuración de la app en el panel: `.env`, credenciales de la base y ajustes, para poder recrearla. |
| `manifest.json` | Tamaño y huella SHA-256 de cada archivo. |

Cada respaldo es una carpeta `<carpeta>/<app>/<AAAAMMDD-HHMMSS>/` en el servidor de respaldos (en Windows, `\\servidor\recurso\<carpeta>\<app>\<AAAAMMDD-HHMMSS>\`).

> **Importante:** `config.tar.gz` contiene contraseñas y claves de la app. Protege el servidor de respaldos igual que este.

## Servidores de respaldo

La tarjeta **Servidores de respaldo** lista los destinos con su sistema, la ruta, cuántas apps lo usan y el resultado de la última prueba de conexión.

| Botón | Qué hace |
|---|---|
| **Agregar servidor** | Abre el formulario para un servidor nuevo. |
| **Probar** | Se conecta, escribe y borra un archivo de prueba en la carpeta y muestra el espacio libre. |
| **Editar** | Cambia sus datos. La contraseña no se muestra: déjala vacía para conservarla. |
| **Eliminar** | Lo quita del panel. Los respaldos que ya están en ese servidor **no** se borran; las apps que lo usaban pasan al primer servidor de la lista. |

Si ya tenías configurado un servidor Linux en una versión anterior, aparece como el primero de la lista y las apps siguen respaldándose ahí.

### Servidor Linux o Unix (SSH)

1. Pulsa **Agregar servidor** y elige **Linux / Unix (SSH)**. Escribe un nombre, la IP o nombre del servidor, el puerto SSH (22), el usuario (por ejemplo `respaldos`) y la carpeta (por ejemplo `/srv/respaldos/matipanel`). Pulsa **Guardar**.
2. En **Preparar el servidor de respaldos → Linux / Unix (SSH)** están los comandos ya completados con esos datos y con la **clave pública** del panel. Cópialos con **Copiar comandos** y ejecútalos **como root en el servidor de respaldos**. Crean el usuario, la carpeta y autorizan la clave con `restrict` (sin terminal ni reenvíos: solo sirve para respaldos).
3. Pulsa **Probar**: debe decir **Correcta** y mostrar el espacio libre. La primera vez se fija la **huella** del servidor; compárala con la del servidor (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) si quieres asegurarte.

> **Nota:** Si reinstalas un servidor de respaldos su huella cambia y las conexiones fallan (es una protección). Al editar el servidor con otra IP o puerto su huella se olvida sola; si es el mismo, pulsa **Olvidar todas** debajo de los comandos y vuelve a probar.

### Servidor Windows o NAS (carpeta compartida con Active Directory)

El panel se conecta a una **carpeta compartida** (SMB) con una cuenta del dominio. Solo una vez:

1. En el **Active Directory** crea una cuenta de servicio solo para respaldos (por ejemplo `svc-respaldos`), sin privilegios de administrador y con contraseña que no caduque.
2. En el **servidor Windows**, abre PowerShell como administrador y ejecuta los comandos de **Preparar el servidor de respaldos → Windows / NAS**: crean la carpeta, la comparten con cifrado obligatorio y dan permiso de modificación a esa cuenta. Cambia `D:\Respaldos` por el disco y la carpeta que prefieras.
3. En el panel pulsa **Agregar servidor** y elige **Windows o NAS (carpeta compartida SMB)**:

| Campo | Ejemplo | Qué es |
|---|---|---|
| **Servidor** | `srv-archivos` o `192.100.10.80` | Nombre o IP del servidor Windows. |
| **Puerto** | `445` | El de compartir archivos; cámbialo solo si tu red lo exige. |
| **Recurso compartido** | `Respaldos` | El nombre con que se compartió la carpeta (lo que va después de `\\servidor\`). |
| **Carpeta dentro del recurso** | `MatiPanel` | Opcional: subcarpeta donde guardar (se crea sola). |
| **Dominio (AD)** | `SENABED` | El dominio de la cuenta. También puedes escribir `SENABED\svc-respaldos` en **Usuario** o usar `usuario@dominio`. |
| **Usuario** y **Contraseña** | `svc-respaldos` | La cuenta de servicio. |
| **Cifrar la conexión (SMB 3)** | Marcado | Cifra todo el tráfico. Desmárcalo solo con servidores antiguos (anteriores a Windows Server 2012) o NAS sin SMB 3. |

Debajo del formulario ves la ruta final, por ejemplo `\\srv-archivos\Respaldos\MatiPanel\<app>\<fecha>`. Pulsa **Probar conexión** antes de **Guardar** para comprobar los datos sin guardarlos.

> **Nota:** El servidor Windows debe aceptar conexiones al puerto 445 desde este panel. La contraseña se guarda solo en este servidor (archivo de root, permisos 600), nunca se muestra en el navegador ni aparece en la Auditoría.

Cómo funciona por dentro: durante cada respaldo, listado o restauración el panel monta la carpeta compartida en `/run/matipanel-smb/` con `mount.cifs` (paquete `cifs-utils`, lo instala el instalador del panel) y la desmonta al terminar. Los archivos se escriben con un usuario sin privilegios.

### Errores frecuentes con Windows

| Mensaje | Qué revisar |
|---|---|
| **usuario, contraseña o dominio incorrectos** | La cuenta, su contraseña y el dominio; que la cuenta no esté bloqueada ni deshabilitada; que tenga permiso en el recurso compartido. |
| **no existe ese recurso compartido** | El nombre en **Recurso compartido** (en el servidor: `Get-SmbShare`). |
| **el servidor no responde** / **rechazó la conexión** | Que el servidor esté encendido y el firewall de Windows permita el puerto 445 desde este panel. |
| **no admite SMB3 cifrado** | Servidor antiguo o NAS: desmarca **Cifrar la conexión**. |
| **la cuenta no tiene permiso de escritura** | Permisos del recurso compartido **y** de la carpeta (pestaña Seguridad / `icacls`): la cuenta necesita **Modificar**. |
| **Falta cifs-utils** | Instálalo en [Paquetes](27-paquetes.md) (`cifs-utils`) o vuelve a ejecutar el instalador del panel. |

## Programación

En la tabla **Aplicaciones**, por cada app:

| Columna | Qué hace |
|---|---|
| **Servidor** | A qué servidor de respaldo va esa app. Por defecto, el primero de la lista. |
| **Programación** | **Diario** a la hora indicada (por defecto 02:00), **Semanal** (domingo a esa hora) o **Cada 6 horas**. |
| **Conservar** | Cuántos respaldos guardar en el servidor remoto (por defecto 14). Los más viejos se borran solos tras cada respaldo correcto. |
| **Activo** | Desmárcalo para pausar los respaldos automáticos de esa app. |

Pulsa **Guardar** en la fila para aplicar los cambios. Las apps nuevas se respaldan por defecto (diario, 14 copias) en cuanto haya un servidor configurado; la primera vez se hace un respaldo a los pocos minutos.

Si un respaldo falla se reintenta cada hora hasta que funcione, y con las [Notificaciones](33-notificaciones.md) activas recibes un correo.

**Respaldar ahora** lanza un respaldo en el momento. Solo se hace un respaldo o restauración a la vez en todo el servidor.

## Ver y restaurar respaldos

**Ver respaldos** abre la lista de una app en su servidor con su fecha y tamaño, y el registro del último trabajo (paso a paso). Si tienes varios servidores, el selector de arriba a la derecha muestra los respaldos de la app en cualquiera de ellos; desde ahí también puedes restaurar o eliminar (por ejemplo, restaurar desde el servidor Windows un respaldo hecho antes de cambiar de destino).

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

Lo que pese la base comprimida más los archivos subidos. La lista de respaldos muestra el tamaño de cada uno y **Probar** el espacio libre en el destino.

### ¿Puedo guardar la misma app en dos servidores?

Cada app se respalda en un solo servidor. Para tener una segunda copia, cambia su **Servidor** de vez en cuando y pulsa **Respaldar ahora**, o copia la carpeta de respaldos del servidor Linux al Windows (o al revés) con las herramientas de esos servidores.

### ¿Puedo restaurar en otro servidor?

Sí, a mano: copia la carpeta del respaldo, crea la app en el otro MatiPanel y restaura `db.sql.gz` (`gunzip -c db.sql.gz | mariadb`) y los volúmenes (`tar -xzf vol-….tar.gz -C <volumen>`). En `config.tar.gz` está el `.env` original.

## Ver también

- [Despliegues](56-despliegues.md)
- [Respaldos](16-respaldos.md): respaldos de carpetas de este servidor.
- [Notificaciones](33-notificaciones.md)
