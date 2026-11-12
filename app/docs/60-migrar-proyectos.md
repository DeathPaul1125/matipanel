# Migrar proyectos existentes a Despliegues

Esta guía explica cómo pasar a [Despliegues](56-despliegues.md) un proyecto **Laravel** que hoy está **copiado directamente en la carpeta de Apache** (`/var/www/html/miproyecto`, `/var/www/miproyecto`…) y usa la **base de datos instalada en el mismo servidor**. Al terminar, el código vivirá en **GitHub**, cada `git push` se desplegará solo y sin cortes, y la app correrá en sus propios contenedores con respaldos automáticos.

La migración se hace **sin apagar el sitio actual**: el proyecto viejo sigue atendiendo a los usuarios mientras pruebas la versión nueva en su propio puerto, y solo al final cambias el tráfico. Si algo sale mal, vuelves atrás en un minuto.

> **Consejo:** Migra **un proyecto a la vez**, empezando por el menos crítico. Cuando el primero funcione, los demás son repetir los mismos pasos.

> **Atajo:** después de subir el código a GitHub y crear la app, la pestaña **Importar** de la app hace sola los pasos del `.env`, la base y los archivos. Ver [Atajo: la pestaña Importar](#atajo-la-pestana-importar).

> **¿La app ya está en otro MatiPanel?** No hace falta esta guía: usa [Recuperación ante desastres](57-respaldos-apps.md#puedo-pasar-las-apps-a-otro-servidor-con-matipanel), que la recrea con su configuración, su base y sus archivos.

## Antes de empezar

### Qué cambia

| Antes (Apache) | Después (Despliegues) |
|---|---|
| El código está en `/var/www/...` y se edita en el servidor. | El código está en **GitHub**. El servidor descarga cada commit y construye una imagen nueva. **Nunca más se edita en el servidor.** |
| `composer install` y `npm run build` se ejecutan a mano. | Se ejecutan solos en cada despliegue. |
| El `.env` es un archivo dentro de la carpeta. | El `.env` vive en el panel (pestaña **Entorno**) y se entrega al contenedor al arrancar. |
| Las subidas quedan en `storage/` de la carpeta. | Las subidas quedan en el **volumen** `storage` de la app, que sobrevive a cada despliegue. |
| La base es la MariaDB del servidor. | La base puede seguir siendo la del servidor o pasar a un contenedor propio de la app (lo eliges en el [Paso 2](#paso-2-elegir-donde-queda-la-base-de-datos)). |
| Las tareas programadas están en un cron y las colas en Supervisor. | Las ejecutan los contenedores **Programador** (`schedule:work`) y **Worker** (`queue:work`) de la app. |
| Apache sirve el sitio con un `VirtualHost`. | El panel publica la app en un puerto propio (`8081`…) y, si quieres, con su nombre de siempre a través de Apache o Nginx. |

### Qué necesitas

- MatiPanel instalado en el servidor y los **Requisitos del servidor** de [Despliegues](56-despliegues.md#requisitos) en verde (Docker, Docker Compose, Git y Nginx).
- Una cuenta de **GitHub** donde crear un repositorio **privado** por proyecto.
- Acceso de **root** (o `sudo`) al servidor. Todos los comandos de esta guía se pueden ejecutar desde **Menú → Herramientas → Terminal** del panel ([Terminal](29-terminal.md)) o por SSH.

## Inventario del proyecto

Antes de tocar nada, anota estos datos de cada proyecto. Los necesitarás en los pasos siguientes.

| Qué | Cómo averiguarlo | Para qué |
|---|---|---|
| **Carpeta del proyecto** | `ls /var/www` · `ls /var/www/html` | Desde ahí se sube el código a GitHub. |
| **Sitio de Apache** | `sudo apache2ctl -S` lista cada `VirtualHost` con su archivo `.conf`. Ábrelo y mira `ServerName`, `ServerAlias` y `DocumentRoot`. | Saber con qué nombre entran los usuarios y qué sitio desactivar al final. |
| **¿Subcarpeta o dominio propio?** | Si los usuarios entran por `http://servidor/miproyecto` (un `Alias` o carpeta dentro de `/var/www/html`), el proyecto está en una subcarpeta. | En Despliegues cada app tiene **su propio** puerto o nombre; ver [Paso 9](#paso-9-cambiar-el-trafico-al-proyecto-nuevo). |
| **Versión de PHP** | `php -v`. Si hay varias instaladas, mira qué `php-fpm` o módulo usa el sitio (`ls /etc/php`). | Elegir la misma versión en el panel (8.2, 8.3 u 8.4). Si es **menor a 8.2**, ver [PHP 8.1 o anterior](#php-81-o-anterior). |
| **Versión de Laravel** | `cd /var/www/html/miproyecto && php artisan --version` | Proyectos muy antiguos pueden necesitar ajustes (Mix, TrustProxies). |
| **Extensiones de PHP** | `php -m` | Las que pide `composer.json` se instalan solas; anota las demás que use el proyecto (por ejemplo `snmp` o `exif`). |
| **El `.env`** | `sudo cat /var/www/html/miproyecto/.env` | Copiar `APP_KEY`, correo y claves de servicios externos. |
| **Base de datos** | En el `.env`: `DB_DATABASE`, `DB_USERNAME`. Tamaño: `sudo mariadb -e "SELECT table_schema, ROUND(SUM(data_length+index_length)/1024/1024) AS MB FROM information_schema.tables GROUP BY table_schema;"` | Decidir dónde queda la base y cuánto tarda copiarla. |
| **Tareas programadas** | `sudo crontab -l` · `sudo crontab -u www-data -l` · `ls /etc/cron.d` | Si hay un `schedule:run`, al final se reemplaza por el **Programador**. |
| **Colas** | `ls /etc/supervisor/conf.d` · `ps aux \| grep queue:work` | Si hay un `queue:work`, al final se reemplaza por el **Worker**. |
| **Redis** | `QUEUE_CONNECTION`, `CACHE_DRIVER`/`CACHE_STORE` y `SESSION_DRIVER` del `.env`. | Activar Redis en el panel si el proyecto lo usaba. |
| **Archivos subidos** | `sudo du -sh storage/app` y busca carpetas de subidas en `public/` (`public/uploads`, `public/images/productos`…). | Copiarlos al volumen de la app ([Paso 7](#paso-7-copiar-los-archivos-subidos)). |
| **Assets** | En `package.json`, la sección `scripts`: ¿tiene `build` (Vite) o `production`/`prod` (Laravel Mix)? | El panel ejecuta `npm run build`; ver [Paso 1](#proyectos-con-laravel-mix). |

## Atajo: la pestaña Importar

Los pasos 4, 6 y 7 (el `.env`, la base y los archivos subidos) los hace el panel por ti en la pestaña **Importar** de la app, y además revisa lo que suele olvidarse. La carpeta y la base viejas **no se modifican**.

1. Crea la app (Paso 3) **sin** marcar **Desplegar al terminar**.
2. Abre la app → pestaña **Importar**. En **Carpeta del proyecto** escribe la ruta (se sugieren las carpetas de `/var/www` y `/srv` que tienen `artisan`) y pulsa **Analizar**.
3. Revisa **Qué se puede importar**:
   - **1 · Copiar el .env:** copia las variables del `.env` viejo, **con la misma `APP_KEY`**. No copia las que maneja el panel: `APP_ENV`, `APP_DEBUG`, `LOG_CHANNEL`, `APP_URL`, las `DB_*` si la app tiene base propia o externa, y las de sesión, caché y colas si la app usa el Redis del panel. Si la app no tiene base en el panel (opción A), copia las `DB_*` y cambia `127.0.0.1`/`localhost` por `host.docker.internal`. Las referencias `${APP_NAME}` se reemplazan por su valor. En **Ver qué variables se copian** está el detalle (sin mostrar los valores).
   - **2 · Copiar la base de datos:** `mysqldump` de la base que dice el `.env` viejo (`DB_DATABASE`) hacia la base del contenedor de la app. **Reemplaza** sus tablas; la base vieja no se toca. Corrige sola la collation `utf8mb4_0900_ai_ci` de MySQL 8. La base vieja tiene que ser MariaDB/MySQL de este servidor.
     - Si la app usa **PostgreSQL 16**, la base se **convierte** con pgloader (lo instala si falta). Solo copia los datos, porque las tablas ya las creó el primer despliegue, y omite las tablas y columnas que ya no existen en el código. Ver [De MariaDB o MySQL a PostgreSQL](#de-mariadb-o-mysql-a-postgresql). El análisis también lista las consultas del código que PostgreSQL no entiende (`FIELD`, `DATE_FORMAT`, `GROUP_CONCAT`, `IFNULL`, `STR_TO_DATE`).
   - **3 · Copiar los archivos subidos:** copia `storage/app` al volumen `storage` de la app, con dueño `www-data`.
4. Revisa **Revisar antes de cambiar el tráfico**: el panel avisa si la carpeta tiene **cambios sin subir a git**, **subidas en `public/`** que se perderían, **Laravel Mix** sin script `build`, la versión de PHP que pide `composer.json`, y qué **cron**, **Supervisor** y **sitio de Apache** del proyecto habrá que desactivar en el [Paso 9](#paso-9-cambiar-el-trafico-al-proyecto-nuevo).
5. Deja marcado solo **1 · Copiar el .env** y pulsa **Importar**. El panel ofrece desplegar enseguida.
6. Cuando el despliegue termine, vuelve a **Importar**, pulsa **Analizar**, marca **2** y **3** y pulsa **Importar** (la base pide escribir el nombre de la app para confirmar). La salida se ve en vivo.
7. En **Artisan** ejecuta `migrate:status` (y `migrate` si hay pendientes) y sigue con el [Paso 8](#paso-8-probar-sin-tocar-el-sitio-viejo).

Para volver a copiar la base y los archivos justo antes del cambio (opción B, Paso 9), repite el punto 6.

## Paso 1 · Subir el código a GitHub

Entra a la carpeta del proyecto y revisa si ya es un repositorio de git:

```
cd /var/www/html/miproyecto
sudo git status
```

> **Nota:** Si git responde `detected dubious ownership in repository`, es porque la carpeta pertenece a otro usuario (normalmente `www-data`). Usa `sudo git …` en todos los comandos, o autorízala una vez con `sudo git config --global --add safe.directory /var/www/html/miproyecto`.

### Si ya es un repositorio

Lo habitual es que en el servidor haya cambios hechos a mano que nunca se subieron. Esa copia es la que está en producción, así que súbela:

1. Mira qué cambió: `sudo git status` y `sudo git diff`.
2. Revisa que en la lista **no** aparezcan `.env`, `vendor/`, `node_modules/` ni archivos subidos por los usuarios (si aparecen, corrige el `.gitignore` como se explica abajo).
3. Súbelo:

```
sudo git add -A
sudo git commit -m "Estado del servidor de producción antes de migrar a MatiPanel"
sudo git push
```

Si el repositorio está en otro servicio (GitLab, Bitbucket, un servidor propio), crea uno nuevo **privado** en GitHub y súbelo ahí: `sudo git remote set-url origin git@github.com:TU_USUARIO/miproyecto.git` y `sudo git push -u origin main`.

### Si no es un repositorio

1. En GitHub, crea un repositorio **privado** y **vacío** (sin README ni `.gitignore`), por ejemplo `miproyecto`.
2. Revisa que exista el `.gitignore` de Laravel en la raíz del proyecto. Debe contener, como mínimo:

```
/vendor
/node_modules
/public/hot
/public/storage
/public/build
/storage/*.key
.env
.env.backup
.phpunit.result.cache
```

   Las carpetas de `storage/` ya traen sus propios `.gitignore` en un proyecto Laravel normal (`storage/app/.gitignore`, `storage/logs/.gitignore`…), así que los archivos subidos y los logs no se suben. Si alguno falta, cópialo de un proyecto Laravel nuevo.

3. Si tienes subidas dentro de `public/` (por ejemplo `public/uploads`), agrégalas también al `.gitignore` (`/public/uploads`). No deben ir al repositorio; se copian aparte en el [Paso 7](#paso-7-copiar-los-archivos-subidos).
4. Crea el repositorio y revisa **antes del primer commit** qué se va a subir:

```
sudo git init -b main
sudo git add -A
sudo git status
```

   En la lista **no** debe aparecer `.env`, `vendor/` ni `node_modules/`. Si aparece alguno, corrige el `.gitignore` y ejecuta `sudo git rm -r --cached .` y `sudo git add -A` de nuevo.

5. Haz el commit y súbelo:

```
sudo git commit -m "Versión en producción"
sudo git remote add origin git@github.com:TU_USUARIO/miproyecto.git
sudo git push -u origin main
```

### Permiso del servidor para subir a GitHub

El `git push` desde el servidor necesita una credencial. Lo más simple es una clave SSH en **tu cuenta** de GitHub:

1. Crea la clave (como root, porque los comandos usan `sudo`): `sudo ssh-keygen -t ed25519 -C "servidor-migracion"`. Pulsa Enter en todas las preguntas.
2. Muestra la clave pública: `sudo cat /root/.ssh/id_ed25519.pub`.
3. En GitHub: tu foto → **Settings** → **SSH and GPG keys** → **New SSH key**. Pega la clave y guarda.
4. Prueba: `sudo ssh -T git@github.com` debe responder `Hi TU_USUARIO! You've successfully authenticated`.

> **Importante:** Esta clave es **tuya** y puede escribir en todos tus repositorios. Cuando termines de migrar, bórrala de GitHub y del servidor (`sudo rm /root/.ssh/id_ed25519*`). MatiPanel no la usa: cada app tiene su propia **clave de despliegue** de solo lectura.

### Qué sí debe estar en el repositorio

- `composer.json` **y** `composer.lock`: así se instalan exactamente las mismas versiones que en producción.
- `package.json` **y** `package-lock.json`, si el proyecto compila assets.
- Las migraciones (`database/migrations`). Si el proyecto tiene tablas que se crearon a mano sin migración, no pasa nada: con la base existente o un volcado completo (Paso 6) las tablas ya vienen hechas.

> **Importante:** **Nunca** subas el `.env`: contiene contraseñas y la `APP_KEY`. Si alguna vez se subió por error, cambia esas contraseñas: quitarlo del repositorio no lo borra del historial.

### Proyectos con Laravel Mix

El panel compila los assets con `npm run build`. Los proyectos con **Vite** (Laravel 9.19 en adelante) ya tienen ese script. Los que usan **Laravel Mix** (`webpack.mix.js`) tienen `production` o `prod` en lugar de `build`. Agrega en `package.json` un script `build` que haga lo mismo:

```
"scripts": {
    "dev": "npm run development",
    "production": "mix --production",
    "build": "mix --production"
}
```

(Con Mix 5 o anterior, copia lo que tenga tu script `production`.) Haz commit y push. Si los assets compilados (`public/js`, `public/css`) ya están en el repositorio y no tienes `package.json`, no hace falta nada: el panel no ejecuta npm.

## Paso 2 · Elegir dónde queda la base de datos

| | **A · Seguir con la base del servidor** | **B · Base propia de la app** |
|---|---|---|
| Qué es | La app en Docker se conecta a la MariaDB que ya tienes, por `host.docker.internal`. | El panel crea un contenedor MariaDB solo para la app y copias los datos ahí. |
| Mover datos | No. | Sí (un volcado y una importación, [Paso 6](#paso-6-importar-los-datos-solo-opcion-b)). |
| Durante las pruebas | El sitio viejo y el nuevo usan **la misma base**: lo que hagas en uno se ve en el otro. | El sitio nuevo usa **una copia**: puedes probar sin afectar a los usuarios. |
| Respaldos de apps | Respalda los archivos y la configuración, **pero no la base** (no es de la app). Respáldala con [Respaldos](16-respaldos.md) o tu método actual. | Respalda base, archivos y configuración juntos, y se puede restaurar todo en otro servidor. |
| Explorador BD | La ves como siempre, en el servidor local. | Aparece como **App «nombre»** en **Servidor**. |
| Ideal para | Bases grandes, o bases que comparten otros sistemas (reportes, otra app). | La mayoría de los proyectos: queda todo ordenado e independiente. |

**Recomendación:** la **B** si la base es solo de este proyecto; la **A** si otros sistemas también la usan o si es tan grande que copiarla lleva mucho tiempo.

### Preparar la opción A

Los contenedores no pueden usar `localhost` para llegar a la base del servidor: dentro de un contenedor, `localhost` es el propio contenedor. Usan `host.docker.internal`, que el panel apunta al servidor. Para que la MariaDB del servidor los acepte:

1. **Que escuche en la red de Docker.** Busca la línea `bind-address` en la configuración (en Debian, `/etc/mysql/mariadb.conf.d/50-server.cnf`; con MySQL en Ubuntu, `/etc/mysql/mysql.conf.d/mysqld.cnf`) y cámbiala a:

```
bind-address = 0.0.0.0
```

   Reinicia con `sudo systemctl restart mariadb` (o `mysql`). El sitio viejo sigue funcionando: también escucha en `127.0.0.1`.

2. **Que el firewall no la deje expuesta.** Con `0.0.0.0` la base escucha en todas las interfaces. Permite el puerto solo desde las redes de Docker y nunca desde la red ni desde Internet. Con UFW (el [Firewall](20-firewall.md) del panel):

```
sudo ufw allow from 172.16.0.0/12 to any port 3306 proto tcp
```

   Comprueba que no haya otra regla que abra el 3306 a todos (`sudo ufw status numbered`). Si el servidor no tiene firewall, actívalo antes de seguir.

3. **Un usuario que pueda entrar desde los contenedores.** El usuario actual suele ser `'usuario'@'localhost'`, que solo sirve desde el propio servidor. Crea el mismo usuario para `172.%` (las redes de Docker) con la misma contraseña:

```
sudo mariadb
CREATE USER 'miusuario'@'172.%' IDENTIFIED BY 'la-misma-contraseña-del-env';
GRANT ALL PRIVILEGES ON mibase.* TO 'miusuario'@'172.%';
FLUSH PRIVILEGES;
EXIT;
```

   También se puede hacer en **Bases de datos → Usuarios** ([Bases de datos](24-bases-de-datos.md)). El usuario de `localhost` no se toca: el sitio viejo lo sigue usando.

4. **Prueba la conexión desde un contenedor**, antes de crear la app:

```
sudo docker run --rm --add-host host.docker.internal:host-gateway mariadb:11 \
  mariadb -h host.docker.internal -u miusuario -p mibase -e "SELECT COUNT(*) FROM migrations;"
```

   Escribe la contraseña. Si muestra un número, la opción A está lista. Si dice `Can't connect`, revisa el `bind-address` y el firewall. Si dice `Access denied`, revisa el usuario `172.%`.

> **Importante (opción A):** En el primer despliegue el panel ejecuta `php artisan migrate --force` contra **la base de producción**. Si el repositorio tiene migraciones que la base todavía no tiene, se aplican en ese momento. Haz un respaldo antes: `sudo mysqldump --single-transaction --routines --triggers mibase | gzip > /root/mibase-antes-de-migrar.sql.gz`.

## Paso 3 · Crear la aplicación en el panel

En **Menú → Web y aplicaciones → Despliegues**, pulsa **+ Nueva aplicación** (el asistente completo está en [Crear una aplicación](56-despliegues.md#crear-una-aplicacion)).

1. **Paso 1 · Repositorio**
   - **Nombre:** corto y en minúsculas, por ejemplo `miproyecto`. Se usa en los contenedores (`matipanel-miproyecto-…`) y en los volúmenes; no se puede cambiar.
   - **Repositorio:** la URL SSH, `git@github.com:TU_USUARIO/miproyecto.git`.
2. **Paso 2 · Acceso:** copia la **clave de despliegue** y agrégala en el repositorio de GitHub (**Settings → Deploy keys → Add deploy key**, sin marcar *Allow write access*). Pulsa **Probar acceso**.
3. **Paso 3 · Opciones**
   - **Tipo de proyecto:** Laravel.
   - **Rama:** la que subiste (`main`).
   - **Versión de PHP:** la misma del servidor o la más cercana hacia arriba (8.2, 8.3 u 8.4).
   - **Nombre interno:** déjalo **vacío** por ahora. Se pone al final, cuando cambies el tráfico.
   - **Base de datos:**
     - **Opción A:** **Ninguna o externa**. Motor **MySQL/MariaDB**, Servidor `host.docker.internal`, Puerto `3306`, y la base, el usuario y la contraseña del `.env` viejo.
     - **Opción B:** **MariaDB 11**. En **Nombre de la base de datos** puedes poner el mismo nombre que tenía (`mibase`). La contraseña la genera el panel.
   - **Redis:** actívalo si el proyecto usaba Redis, o si quieres sesiones, caché y colas más rápidas.
   - **Worker de colas** y **Programador de tareas:** **déjalos desactivados por ahora**. Si los activas mientras el sitio viejo sigue con su cron y su Supervisor, las tareas se ejecutarían **dos veces** (correos duplicados, reportes duplicados). Se activan en el [Paso 9](#paso-9-cambiar-el-trafico-al-proyecto-nuevo).
   - **Desplegar solo con cada push:** activado.
   - **Desplegar al terminar:** **desmárcalo**. Primero hay que poner el `.env` correcto.
4. Pulsa **Crear aplicación**.

## Paso 4 · Pasar el `.env`

El panel creó un `.env` nuevo con una `APP_KEY` **nueva** y las variables de la base. Ahora hay que llevar los valores del proyecto viejo.

1. Abre la app → pestaña **Entorno** → modo **Texto**.
2. **No reemplaces el archivo entero.** Las líneas `DB_*` y `REDIS_*` que escribió el panel son las correctas para los contenedores; las del `.env` viejo apuntan a `127.0.0.1` y no funcionarían. Cambia o agrega solo lo que dice esta tabla:

| Variable | Qué poner |
|---|---|
| `APP_KEY` | **La del `.env` viejo, exactamente igual.** Con otra clave, todo lo que Laravel cifró (contraseñas guardadas con `encrypt()`, columnas `encrypted`, tokens) deja de leerse, y todos los usuarios pierden la sesión. |
| `APP_NAME` | La del `.env` viejo. |
| `APP_URL` | Por ahora `http://IP-DEL-SERVIDOR:PUERTO` (el puerto aparece en el **Resumen**). En el Paso 9 se cambia al nombre definitivo. |
| `APP_ENV` / `APP_DEBUG` | Deja `production` y `false`. |
| `MAIL_*` | Las del `.env` viejo (servidor, puerto, usuario, contraseña, remitente). |
| Servicios externos | Todas las claves propias del proyecto: APIs, `AWS_*`, `PUSHER_*`, pasarelas de pago, `SERVICES_*`, etc. |
| `FILESYSTEM_DISK` / `FILESYSTEM_DRIVER` | La del `.env` viejo. |
| `SESSION_DRIVER`, `CACHE_STORE` (`CACHE_DRIVER` en Laravel 10 o anterior), `QUEUE_CONNECTION` | Si activaste Redis, deja `redis`. Si no, usa lo que tenía el proyecto (`file`, `database`…). |
| `LOG_CHANNEL` | Deja `stderr`: así los errores aparecen en la pestaña **Logs** del panel. |
| `DB_*` | **No las toques.** Opción A: ya tienen `host.docker.internal` y tus datos. Opción B: apuntan al contenedor `db` con la contraseña generada. |
| `REDIS_*` | No las toques si activaste Redis (`REDIS_HOST=redis`). |

3. Pulsa **Guardar**.

> **Nota:** Un valor con `$` se interpreta como variable. Si una contraseña lleva `$`, escríbela entre comillas simples: `MAIL_PASSWORD='pa$$word'`.

> **Consejo:** Para comparar rápido, abre el `.env` viejo en otra pestaña con el [Administrador de archivos](26-archivos.md) del panel.

## Paso 5 · Primer despliegue

1. Pulsa **Desplegar ahora** en la ficha de la app.
2. En la pestaña **Despliegues**, pulsa **Log** y sigue el proceso. El primer despliegue tarda más porque descarga las imágenes base (varios minutos).
3. Si termina **Exitoso**, sigue con el paso siguiente. Si falla, busca la última línea que empieza con `==>` y consulta [Solucionar un despliegue fallido](56-despliegues.md#solucionar-un-despliegue-fallido). Los errores más comunes al migrar:

| Mensaje | Causa y solución |
|---|---|
| `requires php ^8.1` o `your php version (8.x) does not satisfy` | `composer.lock` se generó con otra versión de PHP. Elige otra versión en **Configuración** o ejecuta `composer update` en tu equipo con la versión del panel y sube el lock. |
| `requires ext-xxx -> it is missing` | Agrega la extensión en **Configuración → PHP del contenedor → Extensiones de PHP adicionales**. |
| `Missing script: "build"` | El proyecto usa Laravel Mix: ver [Proyectos con Laravel Mix](#proyectos-con-laravel-mix). |
| `package-lock.json no coincide` | Solo es un aviso; el despliegue sigue. Corrige el lock más adelante. |
| `Las migraciones falló` con `SQLSTATE[HY000] [2002]` | La app no llega a la base. Opción A: repite la prueba del [Paso 2](#preparar-la-opcion-a). |
| `Las migraciones falló` con `Table 'x' already exists` | La base tiene tablas que no figuran en la tabla `migrations` (se crearon a mano o con otro método). En la base vieja, revisa `SELECT * FROM migrations;` y agrega las filas que falten, o corrige la migración en el repositorio. |
| `La aplicación no respondió en 90 s` | Mira las últimas líneas del contenedor en el log. Suele ser un error de PHP por una extensión que falta o un `.env` mal escrito. |

> **Opción B:** el primer despliegue crea la base **vacía** y ejecuta las migraciones, así que la app arranca sin datos. Es normal: los datos se importan en el paso siguiente.

## Paso 6 · Importar los datos (solo opción B)

Con la opción A sáltate este paso: la app ya usa la base de siempre.

### MariaDB o MySQL

1. **Saca el volcado** de la base vieja (no detiene el sitio):

```
sudo mysqldump --single-transaction --routines --triggers --events \
  --default-character-set=utf8mb4 mibase > /root/mibase.sql
```

2. **Busca el contenedor** de la base de la app:

```
sudo docker ps --filter "label=matipanel.app=miproyecto" --format "{{.Names}}"
```

   El de la base termina en `-db-1`, por ejemplo `matipanel-miproyecto-db-1`.

3. **Impórtalo** (reemplaza `mibase` por el **Nombre de la base de datos** que elegiste al crear la app):

```
sudo docker exec -i matipanel-miproyecto-db-1 \
  sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" mibase' < /root/mibase.sql
```

   No hace falta escribir la contraseña: el contenedor la tiene en la variable `MARIADB_ROOT_PASSWORD`. Una base de varios GB puede tardar bastante; no cierres la terminal.

   Las tablas que creó el primer despliegue se reemplazan solas: `mysqldump` agrega `DROP TABLE IF EXISTS` antes de cada tabla.

4. **Revisa las migraciones:** en la pestaña **Artisan** de la app ejecuta `migrate:status`. Si hay alguna en **Pending** (el repositorio tiene migraciones más nuevas que la base vieja), ejecuta `migrate`.
5. **Reinicia** la app (botón **Reiniciar** del **Resumen**) para que no quede nada en caché.
6. **Comprueba** en **Explorador BD**, eligiendo **App «miproyecto»** en **Servidor**, que estén las tablas y la cantidad de filas esperada.

> **Nota:** El [Explorador BD](55-explorador-bd.md) importa archivos CSV de una tabla, no volcados completos de una base. Para un volcado `.sql` usa el comando de arriba.

> **Nota:** Si el volcado da error de *collation* desconocida (`utf8mb4_0900_ai_ci`, típico de MySQL 8), reemplázala antes de importar: `sudo sed -i 's/utf8mb4_0900_ai_ci/utf8mb4_unicode_ci/g' /root/mibase.sql`.

### PostgreSQL

Si el proyecto usaba PostgreSQL y elegiste **PostgreSQL 16** en el panel:

```
sudo -u postgres pg_dump --no-owner --no-acl mibase > /root/mibase.sql
sudo docker exec -i matipanel-miproyecto-db-1 \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < /root/mibase.sql
```

Como el primer despliegue ya creó las tablas, la importación chocaría con ellas. Antes de importar, vacía el esquema en **Artisan** con `db:wipe` (pide escribir el nombre de la app) y después importa.

### De MariaDB o MySQL a PostgreSQL

Si el proyecto usaba MariaDB o MySQL y al crear la app elegiste **PostgreSQL 16**, un volcado `.sql` no sirve: la sintaxis de las dos bases es distinta. Los datos se pasan con **pgloader**, que lee la base vieja y escribe en las tablas que ya creó el primer despliegue con las migraciones de tu código.

**Antes de empezar, revisa el código.** Algunas consultas que funcionan en MySQL fallan o se comportan distinto en PostgreSQL:

| En MySQL | En PostgreSQL | Cambio |
|---|---|---|
| `orderByRaw("FIELD(estado, 'a', 'b')")` | `FIELD` no existe | `orderByRaw("CASE estado WHEN 'a' THEN 1 WHEN 'b' THEN 2 ELSE 3 END")` |
| `DATE_FORMAT(fecha, '%m%d')` | no existe | `to_char(fecha, 'MMDD')` |
| `where('nombre', 'like', "%$q%")` no distingue mayúsculas | `like` **sí** distingue | `whereLike('nombre', "%$q%")` (Laravel 11+) o `'ilike'` |
| `where('email', $email)` no distingue mayúsculas | **sí** distingue | guarda y compara en minúsculas |

**Por qué un script y no solo pgloader.** pgloader copia **todas** las columnas de la base vieja, y falla si en PostgreSQL falta alguna:

```
pgloader failed to find column "public"."contrataciones"."fecha_solicitud"
```

Pasa cuando en el repositorio se **borró una migración que ya se había ejecutado**, en lugar de crear otra que deshiciera el cambio. La base vieja conserva esas columnas, pero la nueva, creada con las migraciones actuales, no las tiene. El script de abajo lo resuelve sin tocar la base original:

1. Hace una copia temporal de la base vieja.
2. Compara las dos estructuras y quita de la copia las tablas y columnas que ya no existen en el código. Antes borra las llaves foráneas de esas columnas.
3. Avisa si, al revés, PostgreSQL tiene columnas que la base vieja no tiene.
4. Ejecuta pgloader desde la copia.
5. Al terminar, borra la copia, el usuario temporal y el archivo de configuración, aunque algo falle.

**Desde el panel (recomendado).** La pestaña **Importar** de la app hace todo lo anterior sin consola:

1. Despliega la app con **PostgreSQL 16** al menos una vez, para que las migraciones creen las tablas.
2. En **Importar**, escribe la carpeta del proyecto viejo y pulsa **Analizar**. En **2 · Copiar la base de datos** debe decir «(MariaDB) … → (PostgreSQL) …, con pgloader». En **Revisar antes de cambiar el tráfico** aparecen las líneas del código con `FIELD`, `DATE_FORMAT` y otras funciones que hay que cambiar.
3. Marca solo **2**, pulsa **Importar** y escribe el nombre de la app para confirmar. La salida se ve en vivo: las columnas que se omiten y la tabla final de pgloader. Puede tardar varios minutos.
4. El resultado dice qué se omitió y qué columnas no tenía la base vieja. Si pgloader tuvo errores, la importación se marca como fallida y muestra las líneas `ERROR`.
5. Pulsa **Reiniciar** y ejecuta `migrate:status` en **Artisan**.

Puedes repetirlo cuantas veces quieras: cada vez vacía las tablas de PostgreSQL antes de copiar.

**Por consola**, si la base vieja está en otro servidor o prefieres hacerlo a mano. En el servidor, como root; el script espera la base vieja en este mismo servidor, así que, si está en otro, cópiala primero con `mysqldump`:

1. Instala pgloader: `apt install -y pgloader`.
2. Crea el script con `nano /root/migrar_pg.sh`, pega lo de abajo y cambia `SRC` (la base vieja) y `APP` (el nombre de la app en el panel):

```
#!/bin/bash
# Copia los datos de una base MariaDB del servidor a la base PostgreSQL de una app de MatiPanel.
# Las columnas/tablas que ya no existen en el código se quitan en una COPIA temporal; la original no se toca.
set -euo pipefail
export LC_ALL=C
SRC=mibase                  # base MariaDB vieja
APP=miproyecto              # app de MatiPanel (PostgreSQL)
TMP=${SRC}_migra
C=matipanel-$APP-db-1
set -a; . /var/lib/matipanel/apps/$APP/db.env; set +a
PGIP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$C")
PGQ() { docker exec "$C" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F $'\t' -c "$1"; }
trap 'rm -f /root/migra.load; mariadb -e "DROP USER IF EXISTS '"'"'pgloader'"'"'@'"'"'127.0.0.1'"'"'; DROP DATABASE IF EXISTS \`$TMP\`"' EXIT

echo "== 1) Copia temporal $SRC -> $TMP"
mariadb -e "DROP DATABASE IF EXISTS \`$TMP\`; CREATE DATABASE \`$TMP\`"
mysqldump --single-transaction --routines --triggers "$SRC" | mariadb "$TMP"

echo "== 2) Comparando estructuras"
mariadb -N -e "SELECT c.table_name, c.column_name FROM information_schema.columns c
  JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name
  WHERE c.table_schema='$TMP' AND t.table_type='BASE TABLE'" | sort > /tmp/mig_my.txt
PGQ "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'" | sort > /tmp/mig_pg.txt
cut -f1 /tmp/mig_my.txt | sort -u > /tmp/mig_myt.txt
cut -f1 /tmp/mig_pg.txt | sort -u > /tmp/mig_pgt.txt

for t in $(comm -23 /tmp/mig_myt.txt /tmp/mig_pgt.txt); do
  echo "   - tabla que ya no existe en el código: $t"
  mariadb "$TMP" -e "SET FOREIGN_KEY_CHECKS=0; DROP TABLE \`$t\`"
done
comm -23 /tmp/mig_my.txt /tmp/mig_pg.txt | awk -F'\t' 'NR==FNR{t[$1]=1;next} ($1 in t)' /tmp/mig_pgt.txt - |
while IFS=$'\t' read -r t c; do
  for fk in $(mariadb -N -e "SELECT constraint_name FROM information_schema.key_column_usage
      WHERE table_schema='$TMP' AND table_name='$t' AND column_name='$c' AND referenced_table_name IS NOT NULL"); do
    mariadb "$TMP" -e "ALTER TABLE \`$t\` DROP FOREIGN KEY \`$fk\`"
  done
  echo "   - columna que ya no existe en el código: $t.$c"
  mariadb "$TMP" -e "ALTER TABLE \`$t\` DROP COLUMN \`$c\`"
done
FALTAN=$(comm -13 /tmp/mig_my.txt /tmp/mig_pg.txt | awk -F'\t' 'NR==FNR{t[$1]=1;next} ($1 in t)' /tmp/mig_myt.txt - || true)
if [ -n "$FALTAN" ]; then
  echo "   ! PostgreSQL tiene columnas que MariaDB no (quedarán vacías o con su valor por defecto):"
  echo "$FALTAN" | sed 's/^/       /'
fi

echo "== 3) Copiando datos con pgloader"
mariadb -e "CREATE USER IF NOT EXISTS 'pgloader'@'127.0.0.1' IDENTIFIED BY 'Migra2026tmp';
            GRANT SELECT, SHOW VIEW ON \`$TMP\`.* TO 'pgloader'@'127.0.0.1';"
cat > /root/migra.load <<EOF
LOAD DATABASE
  FROM mysql://pgloader:Migra2026tmp@127.0.0.1:3306/$TMP
  INTO postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@$PGIP:5432/$POSTGRES_DB
WITH data only, truncate, reset sequences, workers = 4, concurrency = 1
SET PostgreSQL PARAMETERS session_replication_role to 'replica'
EXCLUDING TABLE NAMES MATCHING 'migrations'
ALTER SCHEMA '$TMP' RENAME TO 'public';
EOF
pgloader /root/migra.load
echo "== Listo. Se borraron la copia temporal y el usuario pgloader."
```

3. Ejecútalo con `bash /root/migrar_pg.sh` y revisa la salida:
   - El paso 2 lista las columnas y tablas que se omiten. Comprueba que de verdad sean cosas que el código ya no usa.
   - En la tabla final de pgloader, la columna `errors` debe estar en `0` en todas las filas.
   - Si aparece «PostgreSQL tiene columnas que MariaDB no», la base vieja está atrasada respecto al código: esas columnas quedan vacías o con su valor por defecto.
4. En la app, pulsa **Reiniciar** y ejecuta `migrate:status` en **Artisan**.

Puedes repetir el script cuantas veces quieras: cada vez vacía las tablas de PostgreSQL antes de copiar. Así puedes hacer una prueba ahora y la copia definitiva el día del cambio de tráfico.

**Notas:**
- El script **no copia** la tabla `migrations`: la de PostgreSQL ya la llenó el primer despliegue.
- `session_replication_role = replica` desactiva las llaves foráneas solo durante la copia, para que no importe el orden de las tablas.
- `reset sequences` ajusta los contadores de los `id`. Sin esto, el primer registro nuevo chocaría con un `id` existente.
- Para que las bases de otros entornos (tu PC, pruebas) queden iguales, agrega al repositorio una migración que quite esas columnas si existen (`Schema::hasColumn`). La próxima vez que quieras deshacer una migración ya ejecutada, crea una nueva en lugar de borrarla.

### Borrar el volcado

El archivo `.sql` contiene todos los datos. Cuando la importación esté comprobada, bórralo: `sudo rm /root/mibase.sql`.

## Paso 7 · Copiar los archivos subidos

Lo que los usuarios subieron (fotos, PDF, adjuntos) **no está en GitHub** y hay que copiarlo a mano una vez.

### Lo que está en `storage/app`

En Laravel, las subidas suelen estar en `storage/app` (y las públicas en `storage/app/public`). En Despliegues, esa carpeta es el volumen `matipanel-<app>_storage`. Cópialas así:

```
sudo cp -a /var/www/html/miproyecto/storage/app/. \
  /var/lib/docker/volumes/matipanel-miproyecto_storage/_data/app/
sudo chown -R 33:33 /var/lib/docker/volumes/matipanel-miproyecto_storage/_data/app
```

- `33:33` es el usuario `www-data` dentro del contenedor. Sin el `chown`, la app no puede leer ni escribir esos archivos.
- **No copies** `storage/framework` ni `storage/logs`: son caché, sesiones y logs del servidor viejo.
- El enlace `public/storage` lo crea solo la imagen al arrancar (`storage:link`); no lo copies.

Después pulsa **Reiniciar** en el **Resumen** de la app.

> **Consejo:** Para comprobar que el volumen existe y ver su ruta exacta: `sudo docker volume inspect matipanel-miproyecto_storage --format '{{.Mountpoint}}'`.

### Lo que está en `public/`

> **Importante:** En Despliegues **solo `storage/` es persistente**. Todo lo demás se reemplaza en cada despliegue con lo que hay en GitHub. Si el proyecto guarda subidas en `public/` (`public/uploads`, `public/img/productos`…), **esos archivos desaparecerán** en el primer despliegue que no los traiga.

Tienes dos caminos:

1. **Recomendado:** cambiar el código para guardar en el disco `public` de Laravel (`Storage::disk('public')->put(...)` o `$request->file('foto')->store('productos', 'public')`). Los archivos quedan en `storage/app/public` y se ven en `/storage/...`. Copia las subidas viejas a `…/_data/app/public/` del volumen y actualiza las rutas guardadas en la base si hace falta.
2. **Sin cambiar código:** desplegarlo como [proyecto PHP](56-despliegues.md#proyectos-php-que-no-son-laravel), que permite declarar **Carpetas persistentes** (por ejemplo `public/uploads`). Pierdes la pestaña Artisan, el Worker y el Programador, así que solo conviene en proyectos simples.

## Paso 8 · Probar sin tocar el sitio viejo

Abre la app por su puerto (`http://IP-DEL-SERVIDOR:8081`, la dirección exacta está en el **Resumen**) y revisa:

- [ ] La página de inicio carga sin errores y con estilos (CSS y JS compilados).
- [ ] **Iniciar sesión** con un usuario real funciona. (Si dice que la contraseña es incorrecta pero en el sitio viejo funciona, revisa [Nadie puede iniciar sesión](#nadie-puede-iniciar-sesion-con-la-misma-contrasena-de-siempre).)
- [ ] Los listados muestran los **datos** de siempre.
- [ ] Las **imágenes y descargas** subidas por usuarios se ven.
- [ ] Subir un archivo nuevo funciona (permisos del volumen).
- [ ] Los reportes **PDF/Excel** se generan (si fallan por una extensión o un programa del sistema, agrégalo en **PHP del contenedor**).
- [ ] Enviar un **correo** de prueba (recuperar contraseña, por ejemplo).
- [ ] En **Artisan**, `schedule:list` muestra las tareas programadas del proyecto.
- [ ] La pestaña **Logs** no muestra errores.

> **Importante (opción B):** Mientras pruebas, la app nueva trabaja con una **copia** de los datos. Lo que crees ahí no aparece en el sitio viejo, y al revés. Si una prueba envía correos o notificaciones a clientes reales, ten cuidado: salen de verdad.

Si algo no funciona, el sitio viejo sigue intacto: corrige, haz `git push` y la app se vuelve a desplegar sola.

## Paso 9 · Cambiar el tráfico al proyecto nuevo

Cuando las pruebas estén bien, elige un momento de poco uso (unos minutos fuera de horario).

1. **Opción B: vuelve a copiar los datos.** Desde que hiciste el volcado, los usuarios siguieron trabajando en el sitio viejo. Para no perder nada:
   1. Pon el sitio viejo en mantenimiento: `cd /var/www/html/miproyecto && sudo -u www-data php artisan down`.
   2. Repite el [Paso 6](#paso-6-importar-los-datos-solo-opcion-b) (volcado e importación) y, si hubo subidas nuevas, el [Paso 7](#paso-7-copiar-los-archivos-subidos) (`cp -a` vuelve a copiar todo; no se pierde nada).
2. **Desactiva las tareas del servidor viejo:**
   - Cron: `sudo crontab -e` (y `sudo crontab -u www-data -e`) y comenta con `#` la línea del `schedule:run` de este proyecto.
   - Supervisor: `sudo supervisorctl stop miproyecto-worker:*` y quita o renombra su archivo en `/etc/supervisor/conf.d/`, y luego `sudo supervisorctl reread && sudo supervisorctl update`.
3. **Activa los del panel:** en **Configuración**, marca **Programador de tareas** y, si había colas, **Worker de colas**. Pulsa **Aplicar ahora**.
4. **Desactiva el sitio viejo en Apache**, para que no compita por el mismo nombre:

```
sudo a2dissite miproyecto.conf
sudo systemctl reload apache2
```

   (Usa el nombre del archivo que encontraste con `apache2ctl -S` en el inventario.)

5. **Pon el nombre de siempre en la app:** en **Configuración → Nombre interno** escribe el nombre con que entraban los usuarios (por ejemplo `miproyecto.empresa.local`) y guarda.
   - Si **Apache** ocupa el puerto 80 (lo normal en este servidor), el panel crea el sitio `/etc/apache2/sites-available/matipanel-app-miproyecto.conf`, que reenvía ese nombre a la app (ver [Nombre interno con Apache en el puerto 80](56-despliegues.md#nombre-interno-con-apache-en-el-puerto-80)).
   - Si usas **Nginx** en el 80, el sitio lo crea Nginx.
   - El nombre debe seguir apuntando a la IP del servidor en tu DNS. Si ya lo hacía, no hay que cambiar nada.
6. **Actualiza `APP_URL`** en **Entorno** con el nombre definitivo (`http://miproyecto.empresa.local`) y pulsa **Aplicar ahora**.
7. **Opción B:** si pusiste el sitio viejo en mantenimiento, déjalo así: ya no se usa.
8. Abre el sitio con su nombre de siempre desde un equipo de la red y repite las pruebas rápidas del Paso 8.

### Si el proyecto estaba en una subcarpeta

Si los usuarios entraban por `http://servidor/miproyecto`, la app nueva no puede vivir en esa subcarpeta: cada app de Despliegues tiene su propio **nombre** (`miproyecto.empresa.local`) o su propio **puerto** (`http://servidor:8081`). Elige uno, ponlo en `APP_URL` y avisa a los usuarios del cambio de dirección. Para no romper los accesos directos viejos, deja en el Apache del servidor una redirección (en el `VirtualHost` que servía `/var/www/html`):

```
Redirect permanent /miproyecto http://miproyecto.empresa.local
```

### Si los enlaces salen con el puerto o el protocolo equivocados

La app está detrás de un proxy (Apache o Nginx). Si genera enlaces con `127.0.0.1:20000` o con el puerto equivocado, haz que Laravel confíe en el proxy:

- **Laravel 11 o superior:** en `bootstrap/app.php`, `->withMiddleware(fn ($m) => $m->trustProxies(at: '*'))`.
- **Laravel 10 o anterior:** en `app/Http/Middleware/TrustProxies.php`, `protected $proxies = '*';`.

Haz commit y push.

## Paso 10 · Después de migrar

- **Activa HTTPS** si la app se usa desde Internet o quieres cifrarla en la red: **Configuración → HTTPS** ([HTTPS en Despliegues](56-despliegues.md#https)).
- **Activa los respaldos:** en [Respaldos de apps](57-respaldos-apps.md) asigna un servidor de respaldo a la app y pulsa **Respaldar ahora** para tener el primero. Con la opción A, sigue respaldando la base del servidor por tu cuenta.
- **Desde ahora, los cambios se hacen en GitHub.** El flujo es: cambias el código en tu equipo, haces `git push` y la app se despliega sola, sin cortes. Si algo sale mal, **Revertir** vuelve a la versión anterior en segundos. **No edites archivos dentro del contenedor**: se pierden en el próximo despliegue.
- **Guarda la carpeta vieja unos días.** No la borres de inmediato: renómbrala para que nadie la use por error y bórrala cuando estés seguro.

```
sudo mv /var/www/html/miproyecto /var/www/html/miproyecto.migrado-$(date +%F)
```

   Opción B: guarda también un volcado de la base vieja y, cuando ya no la necesites, bórrala.

- **Borra la clave SSH temporal** de tu cuenta de GitHub y del servidor (ver [Permiso del servidor para subir a GitHub](#permiso-del-servidor-para-subir-a-github)).

## Volver atrás

Si después del cambio algo grave no funciona y no puedes corregirlo enseguida, vuelve al sitio viejo:

1. En la app, **Configuración → Nombre interno**: bórralo y guarda (el panel quita su sitio de Apache).
2. Reactiva el sitio viejo: `sudo a2ensite miproyecto.conf && sudo systemctl reload apache2`.
3. Si lo pusiste en mantenimiento: `cd /var/www/html/miproyecto && sudo -u www-data php artisan up`.
4. Reactiva el cron y Supervisor que comentaste, y desactiva **Programador** y **Worker** en la app.

Con la **opción A** la base es la misma, así que no se pierde nada. Con la **opción B**, lo que los usuarios hayan hecho en la app nueva está en su base; si hace falta, sácalo con un volcado de la base de la app (`sudo docker exec matipanel-miproyecto-db-1 sh -c 'mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" mibase' > /root/desde-la-app.sql`).

## PHP 8.1 o anterior

El Dockerfile que genera el panel admite PHP **8.2, 8.3 y 8.4**. Si el proyecto necesita una versión anterior (Laravel 8 o 9 con PHP 7.4, 8.0 u 8.1), tienes dos caminos:

1. **Recomendado:** actualizar el proyecto a PHP 8.2 o superior (y, si hace falta, la versión de Laravel). Prueba en tu equipo con `composer update` y sube el nuevo `composer.lock`.
2. **Mientras tanto:** agregar tu propio `Dockerfile` en la raíz del repositorio (ver [Usar tu propio Dockerfile](56-despliegues.md#usar-tu-propio-dockerfile)). Este sigue el mismo modelo que el del panel, con PHP 8.1 y Laravel Mix:

```
FROM serversideup/php:8.1-fpm-nginx AS base
ENV AUTORUN_ENABLED=true \
    AUTORUN_LARAVEL_MIGRATION=false \
    PHP_OPCACHE_ENABLE=1
# Extensiones extra, si hacen falta:
# USER root
# RUN install-php-extensions gd intl
# USER www-data
WORKDIR /var/www/html
COPY --chown=www-data:www-data composer.json composer.lock ./
RUN composer install --no-dev --no-interaction --prefer-dist --no-scripts --no-autoloader
COPY --chown=www-data:www-data . .
RUN mkdir -p storage/framework/cache/data storage/framework/sessions storage/framework/views \
        storage/logs storage/app/public bootstrap/cache \
 && composer install --no-dev --no-interaction --prefer-dist --optimize-autoloader \
 && chmod -R ug+rwX storage bootstrap/cache

FROM node:16-alpine AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
COPY --from=base /var/www/html/vendor ./vendor
RUN npm run production

FROM base
COPY --from=assets --chown=www-data:www-data /app/public /var/www/html/public
```

   Agrega también un `.dockerignore` con `.git`, `.env`, `vendor` y `node_modules`. Si el proyecto no compila assets, borra la etapa `assets` y la última línea `COPY --from=assets…`. Con un Dockerfile propio, las opciones de **PHP del contenedor** del panel no se usan.

## Proyectos PHP que no son Laravel

Los mismos pasos sirven para un proyecto PHP de otro framework o PHP puro, con estas diferencias:

- Al crear la app elige **Tipo de proyecto → PHP**, y completa **Carpeta pública**, **Ruta del .env**, **Carpetas persistentes** y, si tiene, **Comando de migraciones** (ver [Proyectos PHP que no son Laravel](56-despliegues.md#proyectos-php-que-no-son-laravel)).
- En **Carpetas persistentes** declara **todas** las carpetas donde el proyecto guarda subidas o datos (por ejemplo `assets/uploads app/logs`). Cada una es un volumen; para ver sus nombres y rutas:

```
sudo docker volume ls --filter "label=com.docker.compose.project=matipanel-miproyecto"
sudo docker volume inspect NOMBRE_DEL_VOLUMEN --format '{{.Mountpoint}}'
```

   Copia ahí el contenido de cada carpeta con `cp -a` y `chown -R 33:33`, como en el [Paso 7](#paso-7-copiar-los-archivos-subidos).
- No hay Artisan, Worker ni Programador. Si el proyecto tiene tareas en el cron del servidor, pueden quedarse ahí usando `docker exec`: `* * * * * docker exec matipanel-miproyecto-app_blue-1 php tarea.php` (cuidado: el contenedor activo alterna entre `app_blue` y `app_green` en cada despliegue; mira cuál está en el **Resumen**).

## Preguntas frecuentes

### ¿Puedo migrar varios proyectos a la vez?

Sí, pero es mejor uno por uno: cada uno tiene su `.env`, su base y sus particularidades, y si algo falla es más fácil saber dónde. Los despliegues del servidor se ejecutan de a uno, en orden.

### ¿Qué pasa si no copio la `APP_KEY`?

La app arranca, pero todo lo cifrado con la clave vieja deja de leerse (`The MAC is invalid`), y las sesiones abiertas se cierran. Las contraseñas de los usuarios **no** dependen de la `APP_KEY` (usan bcrypt), así que el inicio de sesión sigue funcionando. Si ya desplegaste con una clave nueva, pon la vieja en **Entorno** y pulsa **Aplicar ahora**.

### Nadie puede iniciar sesión con la misma contraseña de siempre

Si el login responde «credenciales inválidas» aunque la contraseña sea correcta, revisa en este orden:

1. **Falta una extensión de PHP que el proyecto usa sin declararla.** Es el caso típico del login por **Active Directory/LDAP**: el código revisa `extension_loaded('ldap')` y, si la extensión no está, deja de consultar el directorio sin avisar. En el servidor viejo funcionaba porque PHP sí tenía `ldap`; la imagen del panel solo instala las extensiones que pide `composer.json`.
   - Compruébalo en la **Terminal** del panel, eligiendo en **Dónde** el contenedor de la app (`matipanel-miproyecto-app_blue-1` o `_green-1`). Ahí ya estás dentro del contenedor, así que no uses `docker`:

     ```
     php -m | grep -i ldap
     ```

     Si no imprime nada, falta la extensión.
   - **Arreglo:** en **Configuración → PHP del contenedor → Extensiones de PHP adicionales** agrega `ldap`, guarda y pulsa **Desplegar ahora**. El log debe decir «Extensiones de PHP a instalar: ldap».
   - **Arreglo definitivo:** en tu equipo, ejecuta `composer require ext-ldap:*` y sube `composer.json` y `composer.lock`. Así cualquier despliegue la instala solo.
   - Después, comprueba que el contenedor llega al controlador de dominio. Debe imprimir `bool(true)`:

     ```
     php -r 'var_dump((bool) @fsockopen("IP-DEL-AD", 389, $e, $s, 3));'
     ```

2. **Las mayúsculas, si cambiaste a PostgreSQL.** MySQL compara `Juan@Dominio.com` igual que `juan@dominio.com`; PostgreSQL no. Si el usuario quedó guardado con mayúsculas y el login busca en minúsculas (o al revés), no lo encuentra. Compara en minúsculas en el código, o normaliza la columna con `UPDATE users SET email = lower(email);`.
3. **La base no es la correcta.** En **Explorador BD**, comprueba que la tabla `users` tenga los usuarios del sitio viejo.

Las contraseñas no dependen de la `APP_KEY`: un hash de `bcrypt` copiado de la base vieja sigue funcionando en la nueva.

### ¿Puedo seguir usando phpMyAdmin u otra herramienta para la base?

Con la **opción A**, sí, como siempre. Con la **opción B**, la base vive dentro del contenedor y no se ve desde fuera: usa el [Explorador BD](55-explorador-bd.md) del panel, que tiene tablas, datos, consola SQL, exportar e importar.

### El repositorio pesa mucho porque se subieron archivos de usuarios

Quítalos del repositorio (sin borrarlos del disco), agrégalos al `.gitignore` y súbelo:

```
sudo git rm -r --cached public/uploads
echo "/public/uploads" | sudo tee -a .gitignore
sudo git commit -am "Quitar subidas del repositorio"
sudo git push
```

Siguen en el historial de git; si el repositorio es muy grande, conviene crear uno nuevo con un solo commit inicial (borrando la carpeta `.git` y repitiendo [Si no es un repositorio](#si-no-es-un-repositorio)).

### ¿El sitio viejo y el nuevo pueden convivir con el mismo nombre?

No. Un nombre lo atiende un solo sitio. Por eso, durante las pruebas, la app nueva se usa por su puerto y el nombre se cambia al final ([Paso 9](#paso-9-cambiar-el-trafico-al-proyecto-nuevo)).

### Instalé la app y después cambié de opinión sobre la base (A o B)

El tipo de base se cambia en **Configuración**. Si pasas de A a B, al pulsar **Aplicar ahora** se crea la base del contenedor (vacía) y las `DB_*` pasan a apuntar a ella; luego ejecuta `migrate` en **Artisan** o importa los datos (Paso 6). Si pasas de B a A, escribe las `DB_*` de la base del servidor en **Entorno** a mano.

## Ver también

- [Despliegues](56-despliegues.md)
- [Respaldos de apps](57-respaldos-apps.md)
- [Explorador BD](55-explorador-bd.md)
- [Bases de datos](24-bases-de-datos.md)
- [Terminal](29-terminal.md)
- [Servidor web](21-servidor-web.md)
