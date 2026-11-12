# Respaldos de apps

Respalda automáticamente cada aplicación de [Despliegues](56-despliegues.md) —base de datos, archivos subidos y configuración— **en otros servidores**: Linux o Unix por SSH, o Windows (y NAS) por carpeta compartida con una cuenta del Active Directory. Puedes tener varios servidores y cada app elige el suyo. Nada queda en este servidor: si se daña, los respaldos están a salvo. Lo encuentras en **Menú → Archivos y discos → Respaldos de apps**.

## Qué incluye cada respaldo

| Archivo | Contenido |
|---|---|
| `db.sql.gz` / `db.pgdump` | Volcado completo de la base de datos de la app (MariaDB o PostgreSQL), consistente aunque la app esté en uso. |
| `vol-<nombre>.tar.gz` | Cada volumen de datos: `storage` en Laravel o las **carpetas persistentes** en proyectos PHP (subidas, logs…). |
| `config.tar.gz` | La configuración de la app en el panel: `.env`, credenciales de la base, ajustes y clave de despliegue de GitHub, para poder recrearla en otro servidor. |
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

## Configuración del panel

La tarjeta **Configuración del panel** respalda la configuración de **este MatiPanel** en uno de los servidores de respaldo, para no tener que volver a configurarlo a mano si el servidor se pierde o para llevarla a otro servidor.

### Qué incluye y qué no

| Incluye | No incluye |
|---|---|
| Usuarios con **2FA** (`2fa.json`) y la clave de las sesiones | Las **apps** de Despliegues: tienen sus propios respaldos (arriba) |
| **Permisos** por rol (`roles.json`) | Historiales: gráficos del dashboard (`metrics.db`), historial del monitor (`monitor.db`) y **auditoría** (`audit.db`). Pueden pesar cientos de MB y son de este servidor |
| **Notificaciones**: correo SMTP, eventos y **umbrales** (`notify.json`) | El estado de avisos ya enviados, actualizaciones e historial de versiones |
| **Apariencia** de cada usuario (`prefs.json`) | Capturas de tráfico, cachés y logs |
| **Monitor** de servicios (`monitor.json`) y Tráfico (`traffic.json`) | Los **volúmenes** y carpetas de datos de los stacks (por ejemplo `html/`): respáldalos con [Respaldos](16-respaldos.md) |
| **Multiservidor**: servidores y tokens (`remote_servers.json`, `remote_tokens.json`) | Lo que no es del panel: usuarios del sistema, firewall, sitios de Apache/Nginx, bases de datos del servidor, correo, DNS |
| **Respaldos** de carpetas (`backups.json`) y nombres de contenedores (`docker_names.json`) | |
| Servidores de respaldo y programaciones de esta pantalla (`appbackup.json`) | |
| **Stacks**: `compose.yaml`, `.env` y `stack.json` de cada uno | |
| **Certificado HTTPS** del panel (`/etc/matipanel/cert.pem` y `key.pem`) | |

> **Importante:** El respaldo contiene **secretos**: las semillas del 2FA, la contraseña SMTP, las contraseñas de los servidores Windows de respaldo, los tokens de multiservidor y la clave privada del certificado. Por eso el respaldo programado viene **desactivado**: actívalo sabiendo que el servidor de respaldos debe estar tan protegido como este.

Cada respaldo es una carpeta `<carpeta>/_panel/<nombre-del-servidor>/<AAAAMMDD-HHMMSS>/` con `panel.tar.gz` y `manifest.json` (versión del panel, lista de archivos y huella SHA-256). Ocupa unos pocos KB.

### Respaldar

1. Elige el **Servidor** de respaldo, la **Programación** (cada 6 horas, diario o semanal), la hora y cuántos **Conservar**.
2. Marca **Respaldo programado activo** y pulsa **Guardar**.
3. Pulsa **Respaldar ahora** para tener el primero. Si un respaldo programado falla, se reintenta a la hora y llega el aviso **Respaldo fallido** de [Notificaciones](33-notificaciones.md).

### Restaurar

La tabla **Respaldos de configuración en el servidor** muestra los de **todos** los paneles que respaldan en el servidor elegido (columna **Panel**); el de esta máquina dice **este panel**.

1. Pulsa **Restaurar…** en el respaldo que quieras.
2. Escribe el nombre del servidor del respaldo (columna **Panel**) para confirmar.
3. El panel comprueba la huella del archivo, escribe la configuración y **se reinicia solo**. La página se recarga; vuelve a iniciar sesión.

Al restaurar:

- Solo se escriben los archivos de la lista de arriba, aunque el archivo traiga otros. Nada se escribe a través de enlaces simbólicos.
- Los **servidores de respaldo que ya tienes en este panel se conservan**, y también su **clave SSH**: con ellos se acaba de leer el respaldo, así que funcionan. Los del respaldo que no estén se agregan. Si un servidor del respaldo es el mismo que uno de aquí (misma IP, puerto y carpeta), las apps que lo usaban pasan al de aquí.
- Los servidores **Linux** del respaldo que se agreguen usan la clave SSH de **este** panel: agrégala en cada uno (comandos de **Preparar el servidor de respaldos**). Los **Windows** traen su usuario y contraseña y funcionan al momento.
- Los stacks del respaldo se escriben; los que solo existen en este panel no se tocan. Arráncalos desde [Stacks](59-stacks.md).
- Los usuarios entran con el **2FA del respaldo**: con la app de autenticación que ya tenían configurada para el panel anterior.
- El navegador verá el **certificado del respaldo**: si ya lo habías aceptado en el servidor anterior y entras con la misma IP o nombre, no vuelve a preguntar.

## Recuperación ante desastres

Si este servidor se pierde (falla de disco, reinstalación, robo), las apps se recrean en un servidor nuevo a partir de sus respaldos: configuración, base de datos, archivos y código.

También sirve para **mudar apps de un MatiPanel a otro** sin que nada falle: ver [¿Puedo pasar las apps a otro servidor con MatiPanel?](#puedo-pasar-las-apps-a-otro-servidor-con-matipanel).

La tarjeta **Recuperación ante desastres** está **al final** de la página **Respaldos de apps** (menú **Archivos y discos**), debajo de **Configuración del panel**. No confundir con **Respaldos**, que respalda carpetas.

### Antes de que pase (hazlo ya)

- **Guarda fuera de este servidor** los datos del servidor de respaldos: IP, usuario y carpeta (Linux) o recurso, dominio, usuario y **contraseña** de la cuenta (Windows). Sin ellos no podrás conectar el panel nuevo.
- **Activa el respaldo de la [configuración del panel](#configuracion-del-panel)** y guarda fuera del servidor el nombre de esta máquina (aparece en la tarjeta).
- **Respalda de nuevo cada app** después de actualizar a la versión 0.8.0 (**Respaldar ahora** o espera al respaldo programado). Desde esa versión el respaldo incluye la **clave de despliegue** de GitHub; con respaldos anteriores habrá que registrar una clave nueva en GitHub.
- **Ensáyalo** de vez en cuando en un servidor de prueba: la recuperación no toca el servidor de respaldos, solo lee de él.

### Paso a paso en el servidor nuevo

1. Instala Debian y, si puedes, dale la **misma IP** que tenía el servidor perdido (así siguen funcionando los nombres internos, los accesos directos y el webhook de GitHub).
2. Instala MatiPanel como la primera vez: `git clone https://github.com/DeathPaul1125/matipanel /tmp/matipanel && sudo bash /tmp/matipanel/scripts/install.sh <tu-usuario>`.
3. En **Despliegues**, instala **Docker** y **Nginx** con sus botones.
4. En **Respaldos de apps → Servidores de respaldo**, pulsa **Agregar servidor** con los mismos datos de antes.
   - **Linux:** el panel nuevo tiene **otra clave SSH**. En el servidor de respaldos agrega la nueva: de los comandos de **Preparar el servidor de respaldos** basta con la línea `echo 'restrict ssh-ed25519 …' >> …/authorized_keys` (el usuario y la carpeta ya existen). Puedes borrar la línea de la clave vieja.
   - **Windows:** la misma cuenta del dominio y su contraseña.
   Pulsa **Probar**: debe decir **Correcta**.
5. **(Opcional, recomendado)** En **Configuración del panel**, elige ese servidor, busca en la tabla el respaldo del servidor perdido (columna **Panel**) y pulsa **Restaurar…**. Recuperas usuarios con 2FA, permisos, notificaciones, monitor, stacks y los demás servidores de respaldo ([Configuración del panel](#configuracion-del-panel)). El panel se reinicia; vuelve a entrar y sigue con el paso siguiente.
6. En **Recuperación ante desastres**, elige el servidor y pulsa **Buscar apps en el servidor**. Aparecen todas las apps que tienen respaldos, cuántos y la fecha del último.
7. En cada app pulsa **Recuperar…**, elige el respaldo (por defecto el más reciente), escribe el nombre de la app y pulsa **Recuperar**. Se recupera una app a la vez.
8. Sigue el registro. Cuando diga **Datos recuperados**, abre **sigue el avance en Despliegues**: la app se está compilando desde GitHub. Al terminar queda en línea.
9. Comprueba que la app abre y tiene sus datos. Si la IP cambió, actualiza el DNS o el archivo `hosts` de los equipos que usan el nombre interno, y la URL del webhook en GitHub (**Settings → Webhooks**).

### Qué hace «Recuperar»

| Paso | Detalle |
|---|---|
| 1. Configuración | Recrea la app con su `app.json`, `.env`, credenciales de la base, secreto del webhook y clave de despliegue originales. El estado de ejecución del servidor viejo se descarta. Si su puerto ya está ocupado en el servidor nuevo, se le asigna otro (lo dice el registro). |
| 2. Base de datos | Levanta solo el contenedor de la base y le carga el volcado del respaldo. |
| 3. Archivos | Crea los volúmenes y restaura su contenido (subidas, `storage`, carpetas persistentes). |
| 4. Despliegue | Pone en cola un despliegue normal desde GitHub. Las migraciones corren sobre los datos ya recuperados, así que solo aplican las que falten. Configura Nginx y el nombre interno. |
| 5. Respaldos | La app queda respaldándose en el mismo servidor del que se recuperó. |

> **Importante:** Se despliega el código **actual de la rama** en GitHub, no necesariamente el commit que estaba en línea al hacer el respaldo (`manifest.json` indica cuál era). Si la rama avanzó con cambios que no quieres, revierte en GitHub y vuelve a desplegar.

Si algo falla durante la recuperación, se deshace todo lo creado (contenedores, volúmenes y carpeta de la app): revisa el registro, corrige la causa y vuelve a pulsar **Recuperar…**, o elige un respaldo anterior. Si la app ya existe en este servidor, usa **Ver respaldos → Restaurar…** en su lugar.

Si el respaldo es de una versión anterior a la 0.8.0 y el repositorio es privado por SSH, el registro muestra una **clave de despliegue nueva**: agrégala en GitHub (**Settings → Deploy keys**) y pulsa **Desplegar** en la app.

### Qué no recupera

Esto recupera las **apps desplegadas**, y la [configuración del panel](#configuracion-del-panel) si la respaldaste. Lo que es del servidor y no del panel se vuelve a hacer a mano: usuarios del sistema, firewall, sitios de Apache/Nginx, bases de datos del servidor (fuera de las apps), los datos de los stacks y las carpetas que respaldabas con [Respaldos](16-respaldos.md) (sus trabajos sí vuelven con la configuración del panel; los archivos están en su propio destino).

## Preguntas frecuentes

### ¿Cuánto espacio ocupan?

Lo que pese la base comprimida más los archivos subidos. La lista de respaldos muestra el tamaño de cada uno y **Probar** el espacio libre en el destino.

### ¿Puedo guardar la misma app en dos servidores?

Cada app se respalda en un solo servidor. Para tener una segunda copia, cambia su **Servidor** de vez en cuando y pulsa **Respaldar ahora**, o copia la carpeta de respaldos del servidor Linux al Windows (o al revés) con las herramientas de esos servidores.

### ¿Puedo pasar las apps a otro servidor con MatiPanel?

Sí, con [Recuperación ante desastres](#recuperacion-ante-desastres). Los dos paneles tienen que usar el **mismo servidor de respaldos**:

1. **En el panel viejo:** en cada app, pulsa **Respaldar ahora**. Opcional: en **Configuración del panel**, pulsa **Respaldar** para llevarte también usuarios, permisos y ajustes.
2. **En el panel nuevo:** en **Servidores de respaldo**, agrega el mismo servidor.
   - Si es Linux, agrega en ese servidor la clave SSH del panel nuevo, como se explica en [Paso a paso en el servidor nuevo](#paso-a-paso-en-el-servidor-nuevo).
   - Si quieres la configuración del panel viejo, restáurala ahora, en **Configuración del panel → Restaurar…**.
3. Al final de la página, en **Recuperación ante desastres**, pulsa **Buscar apps en el servidor** y luego **Recuperar…** en cada app.
4. **Prueba la app nueva.** El panel viejo sigue funcionando y las dos copias trabajan por separado: lo que se guarde en una no aparece en la otra.
5. **Para el cambio definitivo:**
   1. Haz un último **Respaldar ahora** en el panel viejo.
   2. En el nuevo, abre **Ver respaldos → Restaurar…** con ese respaldo.
   3. Actualiza el DNS o el archivo `hosts` de los equipos, y la URL del webhook en GitHub.
   4. Detén o elimina la app del panel viejo.

Si el otro servidor tiene proyectos en Apache, fuera de Despliegues, no se recuperan así. Copia su carpeta y su base a este servidor y usa la pestaña **Importar** ([Migrar proyectos](60-migrar-proyectos.md)).

## Ver también

- [Despliegues](56-despliegues.md)
- [Respaldos](16-respaldos.md): respaldos de carpetas de este servidor.
- [Notificaciones](33-notificaciones.md)
