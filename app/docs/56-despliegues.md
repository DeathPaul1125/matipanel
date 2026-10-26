# Despliegues

Este módulo publica aplicaciones **Laravel** que están en **GitHub**, al estilo de Coolify: descarga el código, construye una imagen Docker con PHP, Nginx, Composer y los assets de npm, ejecuta las migraciones y deja la aplicación en línea en un puerto propio o con un nombre interno como `miapp.apps.empresa.local`. Cada nuevo commit se puede desplegar solo y **sin cortes**. Lo encuentras en **Menú → Web y aplicaciones → Despliegues**.

## Cómo funciona en un minuto

| Concepto | Qué es |
|---|---|
| **Aplicación** | Un repositorio de GitHub + una rama + su configuración (PHP, servicios, `.env`). |
| **Despliegue** | Una ejecución del proceso: descargar el código, construir la imagen, migrar y cambiar a la versión nueva. Se guardan los últimos 50. |
| **Imagen** | La aplicación empaquetada, con nombre `matipanel/<app>:<commit>`. Se conservan las de los últimos 5 despliegues exitosos para poder revertir. |
| **Slots blue/green** | Cada aplicación tiene dos puertos internos (solo en `127.0.0.1`). La versión nueva arranca en el slot libre; cuando ya responde, Nginx cambia a ella y recién entonces se apaga la anterior. |
| **Puerto público** | El puerto por el que entran los usuarios (`http://servidor:8081`). Lo atiende el Nginx del servidor y se asigna solo entre 8081 y 8999. |
| **Nombre interno** | Opcional. Un nombre como `miapp.apps.empresa.local` que el Nginx del servidor atiende en el puerto 80. |
| **Volúmenes** | Datos que sobreviven a cada despliegue: `storage` de Laravel, la base de datos y Redis. Nunca se borran al desplegar. |

Los despliegues de todo el servidor se hacen **de a uno**, en orden de llegada. Si el panel se reinicia en medio de uno, ese despliegue queda como **Falló** y los que esperaban en la cola continúan.

> **Nota:** Todo pasa en la red interna. No se usan certificados de Let's Encrypt: las aplicaciones se sirven por HTTP.

## Requisitos

Arriba de la página está la tarjeta **Requisitos del servidor**. Cada componente tiene una luz verde (listo), roja (falta) o ámbar (recomendado):

| Componente | Paquete de Debian | Para qué |
|---|---|---|
| **Docker Engine** | `docker.io` | Construir y correr las aplicaciones. |
| **Docker Compose v2** | `docker-compose` | Levantar la app, la base de datos y Redis juntos. |
| **Docker Buildx** (recomendado) | `docker-buildx` | Construcción más rápida (BuildKit). Sin él se usa el constructor clásico. |
| **Git** | `git` | Descargar el código de GitHub. |
| **Nginx** | `nginx` | Recibir las visitas y repartirlas a cada aplicación. |

1. Si algo falta, pulsa **Instalar** en su fila o **Instalar lo que falta**.
2. Confirma. La instalación usa apt y puede tardar varios minutos.
3. Al terminar, la tarjeta se actualiza sola. Si no, pulsa **Volver a comprobar**.

> **Nota:** Si instalaste Docker desde el repositorio oficial de Docker en vez del de Debian, el panel prueba también el paquete `docker-compose-plugin`.

> **Importante:** Si usas Apache en lugar de Nginx en el puerto 80, el nombre interno no funcionará: los dos no pueden escuchar en el mismo puerto. Los puertos propios (8081…) sí funcionan.

## Preparar la red

### El registro DNS comodín

Para usar nombres internos (`miapp.apps.empresa.local`) sin crear un registro por cada aplicación, crea **una sola vez** un registro comodín que apunte a la IP del servidor.

**En el DNS de Active Directory (Windows Server):**

1. Abre **Administrador de DNS** (`dnsmgmt.msc`) en un controlador de dominio.
2. Expande **Zonas de búsqueda directa** y elige tu zona, por ejemplo `empresa.local`.
3. Clic derecho en la zona → **Dominio nuevo…** → escribe `apps` → **Aceptar**.
4. Clic derecho en el nuevo dominio `apps` → **Host nuevo (A o AAAA)…**.
5. En **Nombre** escribe `*` (asterisco) y en **Dirección IP** la IP del servidor del panel. Pulsa **Agregar host**.

También puedes hacerlo con PowerShell en el controlador de dominio:

```
Add-DnsServerResourceRecordA -ZoneName "empresa.local" -Name "*.apps" -IPv4Address "10.0.0.20"
```

**En el módulo [DNS](51-dns.md) del panel (BIND):**

1. Abre la pestaña **Registros** y elige tu zona.
2. Agrega un registro de tipo **A** con nombre `*.apps` y como valor la IP del servidor.

Comprueba desde un equipo de la red: `nslookup prueba.apps.empresa.local` debe responder con la IP del servidor.

### Puertos del firewall

| Puerto | Para qué |
|---|---|
| **80/tcp** | Nombres internos (`miapp.apps.empresa.local`). |
| **8081–8999/tcp** | Puertos propios de las aplicaciones (puedes abrir solo los que uses). |

En el [Firewall](20-firewall.md) del panel agrega reglas para esos puertos, limitadas a la red interna si es posible. Con UFW desde la terminal: `sudo ufw allow from 10.0.0.0/8 to any port 80 proto tcp` y `sudo ufw allow from 10.0.0.0/8 to any port 8081:8999 proto tcp`.

> **Nota:** Los puertos internos de los slots (20000 en adelante) se publican solo en `127.0.0.1`, así que no hace falta (ni conviene) abrirlos.

## Crear una aplicación

1. Pulsa **+ Nueva aplicación**.
2. **Paso 1 · Repositorio:**
   - **Nombre:** minúsculas, números y guiones, empezando con letra (por ejemplo `portal-rrhh`). No se puede cambiar después.
   - **Repositorio de GitHub:** para un repositorio **público** usa `https://github.com/dueño/repo`; para uno **privado**, la URL SSH `git@github.com:dueño/repo.git`.
3. Pulsa **Siguiente**. El panel prepara la aplicación y genera su **clave de despliegue**.
4. **Paso 2 · Acceso:** si el repositorio es privado, agrega la clave en GitHub (ver abajo). Pulsa **Probar acceso**: debe decir **Acceso correcto** y la cantidad de ramas.
5. **Paso 3 · Opciones:**
   - **Rama:** la que se despliega (se sugieren las del repositorio).
   - **Versión de PHP:** 8.2, 8.3 o 8.4.
   - **Nombre interno (opcional):** por ejemplo `portal-rrhh.apps.empresa.local`.
   - **Base de datos:** ninguna, **MariaDB 11** o **PostgreSQL 16**. Se crea en un contenedor propio con usuario y contraseña generados, y las variables `DB_*` se agregan solas al `.env`.
   - **Redis**, **Worker de colas** (`queue:work`) y **Programador de tareas** (`schedule:work`).
   - **Desplegar solo con cada push a la rama** (activado por defecto).
   - **Desplegar al terminar**.
6. Pulsa **Crear aplicación**. Se abre la ficha de la aplicación con el log del primer despliegue.

Si cierras el asistente a la mitad, la aplicación queda como **Borrador** en la lista; pulsa **Continuar** para terminarla o **Eliminar** para descartarla.

### Agregar la clave de despliegue en GitHub

La clave de despliegue le da al panel acceso de **solo lectura** a un único repositorio.

1. Copia la clave con **Copiar clave** (empieza con `ssh-ed25519` y termina con `matipanel-<app>`).
2. En GitHub, abre el repositorio → **Settings** → **Deploy keys** → **Add deploy key**.
3. En **Title** escribe algo como `MatiPanel servidor01`, pega la clave en **Key** y **deja sin marcar Allow write access**.
4. Pulsa **Add key**.
5. Vuelve al panel y pulsa **Probar acceso**.

> **Nota:** GitHub no permite usar la misma clave de despliegue en dos repositorios. Por eso cada aplicación tiene la suya.

## Desplegar

Pulsa **Desplegar ahora** en la ficha de la aplicación (o **Desplegar** en la lista). El despliegue entra en la cola y su log aparece en la pestaña **Despliegues**.

### Qué pasa en un despliegue

1. **Código:** la primera vez se clona el repositorio; después se descarga solo el último commit de la rama y se descartan los cambios locales.
2. **Imagen:** si el repositorio trae un `Dockerfile` en la raíz, se usa ese. Si no, el panel genera uno para Laravel basado en `serversideup/php` (PHP-FPM + Nginx): `composer install --no-dev --optimize-autoloader` y, si hay `package.json`, `npm ci` (o `npm install`) y `npm run build` en una etapa con Node 22.
3. **Servicios:** se levantan la base de datos y Redis (si los usas) y se espera a que estén sanos.
4. **Migraciones:** `php artisan migrate --force` en un contenedor temporal con la imagen nueva. Si fallan, el despliegue se detiene y **la versión anterior sigue en línea**.
5. **Arranque:** la versión nueva arranca en el slot libre y el panel espera hasta 90 segundos a que responda con un código HTTP menor que 500.
6. **Cambio:** se reescribe el sitio de Nginx de la aplicación, se valida con `nginx -t` y se recarga. Si algo falla, se deja la configuración anterior.
7. **Limpieza:** se apaga el slot anterior, se reinician el worker y el programador con la imagen nueva y se borran las imágenes viejas (quedan las de los últimos 5 despliegues exitosos).

Al arrancar, la imagen generada ejecuta sola `storage:link` y las cachés de Laravel (`config:cache`, `route:cache`, `view:cache`), así que no hace falta correrlas a mano. Si usas tu propio Dockerfile, esas tareas son responsabilidad tuya.

> **Importante:** El `.env` **nunca** se copia dentro de la imagen. Se entrega al contenedor al arrancar. Así una imagen no contiene contraseñas y cambiar el `.env` no obliga a compilar de nuevo.

### Seguir el log y cancelar

- En **Despliegues**, pulsa **Log** en cualquier fila. Si el despliegue está en curso, el log se actualiza solo; desmarca **Seguir** para que no se desplace hacia abajo mientras lees.
- Los valores secretos (contraseñas, `APP_KEY`, claves) se reemplazan por `••••••` en el log.
- Para detener uno en curso o en cola, pulsa **Cancelar**. La versión anterior sigue en línea.
- Cada log guarda como máximo 5 MB: si la salida es mayor, se conserva el principio y el final.

### Revertir a una versión anterior

1. En **Despliegues**, busca un despliegue **Exitoso** y pulsa **Revertir**.
2. Escribe el commit corto para confirmar.
3. La imagen de ese despliegue vuelve a ponerse en línea, sin compilar y con el mismo cambio sin cortes.

> **Importante:** Revertir **no deshace migraciones** de la base de datos. Si la versión nueva cambió tablas, revisa que la anterior funcione con ellas (o corre `migrate:rollback` en **Artisan** antes de revertir).

## Auto-despliegue y webhook

Con **Desplegar solo con cada push** activado, el panel consulta GitHub cada 60 segundos (`git ls-remote`, una petición muy liviana) y, si la rama tiene un commit distinto del último compilado, encola un despliegue. Un commit que falló no se reintenta solo: espera al siguiente push o pulsa **Desplegar ahora**. Si GitHub no responde, las consultas se espacian (hasta una por hora) para no insistir.

El **webhook** es opcional. Como el servidor está en la red interna, GitHub.com no puede llamarlo; sirve si tienes un **runner propio de GitHub Actions** dentro de la red. En **Configuración → Webhook de GitHub** encuentras la **URL** y el **Secreto**. Solo los eventos `push` a la rama configurada despliegan, y la firma `X-Hub-Signature-256` se verifica siempre. Un paso del workflow podría ser:

```
body='{"ref":"refs/heads/main"}'
sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$MATIPANEL_SECRET" | sed 's/^.* //')
curl -fsS -X POST -H "Content-Type: application/json" -H "X-GitHub-Event: push" \
  -H "X-Hub-Signature-256: sha256=$sig" --data "$body" https://panel.empresa.local/despliegues/webhook/miapp
```

## Variables de entorno

La pestaña **Entorno** edita el `.env` de la aplicación.

1. Elige el modo **Tabla** (una fila por variable) o **Texto** (el archivo tal cual, con comentarios).
2. Los valores están ocultos; marca **Mostrar valores** para verlos.
3. Pulsa **Guardar**. El panel valida los nombres (MAYÚSCULAS, números y `_`) y las comillas.
4. Te pregunta si quieres **Aplicar ahora**: los contenedores se recrean con la misma imagen, sin compilar y sin cortes.

Un `.env` típico de producción (el panel crea uno parecido al crear la aplicación):

```
APP_NAME="Portal RRHH"
APP_ENV=production
APP_KEY=base64:...            # generada por el panel; no la cambies si ya hay datos cifrados
APP_DEBUG=false
APP_URL=http://portal-rrhh.apps.empresa.local
LOG_CHANNEL=stderr            # así los errores se ven en la pestaña Logs
LOG_LEVEL=info
SESSION_DRIVER=redis
CACHE_STORE=redis
QUEUE_CONNECTION=redis
DB_CONNECTION=mysql           # lo completa el panel si activas MariaDB (pgsql con PostgreSQL)
DB_HOST=db
DB_PORT=3306
DB_DATABASE=laravel
DB_USERNAME=laravel
DB_PASSWORD=...
REDIS_HOST=redis
MAIL_MAILER=smtp
MAIL_HOST=correo.empresa.local
MAIL_PORT=25
```

> **Nota:** Un valor con `$` se interpreta como variable. Si una contraseña lleva `$`, escríbela entre comillas simples: `MAIL_PASSWORD='pa$$word'` (el modo tabla lo hace solo).

> **Consejo:** Si la aplicación genera URLs con `https` o con el puerto equivocado, revisa `APP_URL` y que Laravel confíe en el proxy (en Laravel 11: `->withMiddleware(fn ($m) => $m->trustProxies(at: '*'))` en `bootstrap/app.php`).

## Logs, Artisan y estado

- **Resumen:** direcciones de la aplicación (con el puerto propio sobre la IP o nombre con que abriste el panel, y el nombre interno si lo hay), commit en línea, imagen, slot activo y el estado de cada contenedor. Los botones **Reiniciar**, **Detener** e **Iniciar** actúan sobre los contenedores. **Detener** apaga también la base de datos y Redis.
- **Logs:** últimas 100, 300 o 1000 líneas de la aplicación, el worker, el programador, la base de datos o Redis.
- **Artisan:** ejecuta `php artisan <comando>` dentro del contenedor en línea. Hay una lista de **Comandos frecuentes** (`migrate:status`, `cache:clear`, `optimize:clear`, `queue:restart`…). Los comandos interactivos (`tinker`, `serve`, `queue:work`) no se permiten, y los destructivos (`db:wipe`, `migrate:fresh`, `migrate:rollback`, `down`…) piden escribir el nombre de la aplicación.

## Configuración

En **Configuración** puedes cambiar el repositorio, la rama, la versión de PHP, el puerto público, el nombre interno, las extensiones de PHP adicionales, los servicios y los límites de recursos.

| Cambio | Cuándo se aplica |
|---|---|
| Puerto público o nombre interno | Al guardar (se reescribe Nginx; si `nginx -t` falla, no se guarda). |
| Servicios, memoria, CPUs, puerto interno | Al pulsar **Aplicar ahora** (recrea contenedores sin compilar). |
| Repositorio, rama, PHP, extensiones | En el próximo despliegue. |

- **Memoria máxima (MB)** y **CPUs máximas** limitan la app, el worker y el programador (0 = sin límite).
- **Extensiones de PHP adicionales** (por ejemplo `intl gd bcmath`) se instalan en la imagen generada. La imagen ya trae las más comunes (`pdo_mysql`, `pdo_pgsql`, `redis`, `zip`, `opcache`, `pcntl`…).
- **Clave de despliegue:** **Probar acceso** y **Regenerar clave** (después tendrás que reemplazarla en GitHub).
- **Webhook:** URL y secreto; **Regenerar** invalida el anterior.

### Usar tu propio Dockerfile

Si el repositorio trae un `Dockerfile` en la raíz, el panel lo usa en lugar del generado. Debe:

- Servir HTTP en el **puerto interno** configurado (8080 por defecto).
- Tener la aplicación en `/var/www/html` (el volumen `storage` se monta en `/var/www/html/storage`).
- No copiar el `.env` (agrega `.env` a tu `.dockerignore`).
- Responder en `/` con un código menor que 500 cuando esté lista.

## Eliminar una aplicación

1. Abre la pestaña **Eliminar**.
2. Si también quieres borrar los datos, marca **Borrar también los volúmenes (base de datos y storage)**.
3. Pulsa **Eliminar aplicación** y escribe su nombre.

Se borran los contenedores, las imágenes, el sitio de Nginx y la carpeta de la aplicación (`.env`, clave, historial). Quita también la clave en GitHub (**Settings → Deploy keys**).

> **Importante:** Si no borras los volúmenes y más adelante creas otra aplicación con el **mismo nombre**, esa aplicación usará los datos viejos.

## Solucionar un despliegue fallido

Abre el **Log** del despliegue y busca la última línea que empieza con `==>`: indica el paso que falló.

| Paso / mensaje | Causa probable y solución |
|---|---|
| `git clone` o `git fetch` falló, `Permission denied (publickey)` | Falta la clave de despliegue en GitHub, o se regeneró y no se actualizó. Pulsa **Probar acceso**. |
| `Repository not found` con URL `https://` | El repositorio es privado: cambia a la URL SSH `git@github.com:…` y agrega la clave. |
| `couldn't find remote ref` | La rama configurada no existe. Corrígela en **Configuración**. |
| `docker build` falló en `composer install` | Falta una extensión de PHP (agrégala en **Extensiones de PHP adicionales**), `composer.lock` no corresponde a la versión de PHP, o un paquete privado necesita credenciales. |
| `docker build` falló en `npm run build` | Error de Vite/Mix. Prueba `npm ci && npm run build` en tu equipo con Node 22. |
| `Las migraciones falló` | Lee el error SQL en el log. La versión anterior sigue en línea; corrige la migración y vuelve a desplegar. Revisa también las variables `DB_*`. |
| `La aplicación no respondió en 90 s` | El log muestra las últimas líneas del contenedor. Lo habitual: `APP_KEY` vacía, error de PHP al arrancar, o un Dockerfile propio que escucha en otro puerto. |
| `nginx -t falló` | Otro sitio de Nginx tiene un error, o el puerto público choca con otro servicio. Revisa en [Servidor web](21-servidor-web.md). |
| `Interrumpido: el panel se reinició` | El panel se reinició durante el despliegue. Vuelve a desplegar. |
| `La imagen … ya no existe` (al revertir) | Solo se conservan las imágenes de los últimos 5 despliegues exitosos. |

Si la aplicación despliega bien pero muestra **500**, mira la pestaña **Logs** (con `LOG_CHANNEL=stderr` los errores de Laravel aparecen ahí) o activa temporalmente `APP_DEBUG=true` en **Entorno**.

## Preguntas frecuentes

### ¿Puedo desplegar algo que no sea Laravel?

Sí, si el repositorio trae su propio `Dockerfile` que sirva HTTP en el puerto interno configurado. Desactiva **Ejecutar php artisan migrate** en **Configuración** si no es Laravel.

### ¿Dónde guarda el panel los datos de cada aplicación?

En `/var/lib/matipanel/apps/<app>/`: configuración, `.env`, clave de despliegue, historial y logs, todos legibles solo por root. El sitio de Nginx es `/etc/nginx/sites-available/matipanel-app-<app>.conf`; el panel no toca ningún otro sitio.

### ¿Se pierden los archivos subidos o la base de datos al desplegar?

No. `storage/` de Laravel, la base de datos y Redis viven en volúmenes de Docker que se conservan en cada despliegue y reversión. Solo se borran si lo pides al eliminar la aplicación.

### ¿Por qué el commit nuevo no se desplegó solo?

Revisa que **Desplegar solo con cada push** esté activado y que el push haya sido a la rama configurada. Si ese mismo commit ya falló una vez, no se reintenta solo: pulsa **Desplegar ahora**.

### ¿Las aplicaciones se ven en el módulo Docker?

Sí. Sus contenedores se llaman `matipanel-<app>-…`. Gestiónalas desde este módulo para que el estado del panel no quede desincronizado.

## Ver también

- [Docker](25-docker.md)
- [DNS](51-dns.md)
- [Servidor web](21-servidor-web.md)
- [Firewall](20-firewall.md)
