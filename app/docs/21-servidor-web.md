# Servidor web (Apache y Nginx)

Este módulo administra Apache y Nginx: estado del servicio, sitios (virtual hosts), módulos de Apache y logs. Lo encuentras en **Menú → Servidores → Servidor web**.

## Requisitos para usar el módulo

- Debes tener instalado Apache (`apache2`) o Nginx (`nginx`). Si no hay ninguno, la página muestra **No hay servidor web instalado** con el comando para instalarlo. Puedes instalarlo desde [Paquetes](27-paquetes.md).
- Si tienes los dos instalados, verás una pestaña **Apache** y otra **Nginx**. Cada pestaña trabaja solo con su servidor.
- El módulo usa la estructura de Debian: `sites-available/` y `sites-enabled/` dentro de `/etc/apache2` o `/etc/nginx`.

> **Importante:** Apache y Nginx no pueden escuchar a la vez en el puerto 80. Si instalas los dos, uno de ellos no arrancará. En la práctica, usa solo uno por servidor.

## Ver el estado y aplicar cambios

En la parte superior verás el estado del servicio (`active`, `inactive` o `failed`) y la versión instalada. A la derecha están estos botones:

| Botón | Qué hace |
|---|---|
| **Probar config** | Ejecuta `apache2ctl configtest` o `nginx -t` y muestra el resultado. No cambia nada. |
| **Recargar** | Prueba la configuración y, si es válida, recarga el servicio. No corta las conexiones activas. |
| **Reiniciar** | Pide confirmación (**Reiniciar servidor web**). Prueba la configuración y reinicia el servicio. Corta las conexiones activas. |
| **error.log** | Muestra las últimas 200 líneas del log de errores general. |
| **access.log** | Muestra las últimas 200 líneas del log de accesos general. |

Si la configuración tiene errores, **Recargar** y **Reiniciar** no se aplican. Verás el mensaje **La configuración tiene errores; no se aplicó** junto con la salida de la prueba. Así el servidor sigue funcionando con la configuración anterior.

> **Consejo:** Usa **Recargar** para casi todo. Usa **Reiniciar** solo cuando cambias módulos de Apache o cuando una recarga no basta.

## Entender la tabla de sitios

La tarjeta **Sitios** lista los archivos de `sites-available` y `sites-enabled`. Pulsa **Refrescar** para volver a leerlos.

| Columna | Significado |
|---|---|
| **Archivo** | Nombre del archivo de configuración. La etiqueta `default` marca los sitios que trae el paquete (`000-default.conf`, `default-ssl.conf` en Apache; `default` en Nginx). |
| **Estado** | `habilitado` si está activo en `sites-enabled`; `deshabilitado` si solo existe en `sites-available`. |
| **Dominios** | Valores de `ServerName`/`ServerAlias` (Apache) o `server_name` (Nginx). |
| **Document root** | Carpeta desde la que se sirven los archivos. |
| **Puertos** | Puertos en los que escucha el sitio (por ejemplo `80` o `443 ssl`). |
| **Acciones** | **Habilitar** o **Deshabilitar**, **Editar** y **Eliminar**. |

- En Apache solo se listan archivos que terminan en `.conf`.
- Si un sitio aparece con **solo en sites-enabled**, el archivo está directamente en `sites-enabled` y no en `sites-available`. El panel no puede habilitarlo, deshabilitarlo ni eliminarlo. Edítalo o muévelo a mano.

## Crear un sitio nuevo

1. Elige la pestaña del servidor (**Apache** o **Nginx**).
2. Pulsa **+ Nuevo sitio**. Se abre **Nuevo virtual host**.
3. Completa los campos (ver la tabla).
4. Pulsa **Crear**.
5. Si todo sale bien, verás una ventana **Sitio creado:** con la ruta del archivo. Ciérrala con **Cerrar**.

| Campo | Qué poner | Reglas |
|---|---|---|
| **Dominio** | El nombre principal, por ejemplo `sistema.ejemplo.gob.gt`. | Nombre de host válido. Se guarda en minúsculas. |
| **Alias** | Otros nombres, separados por coma o espacio, por ejemplo `www.sistema.ejemplo.gob.gt`. | Opcional. Cada alias debe ser un nombre de host válido. |
| **Document root** | Carpeta del sitio. Se rellena sola con `/var/www/<dominio>/public_html`. | Ruta absoluta bajo `/var/www/`, `/srv/` o `/home/`. Sin `..`. Solo letras, números, `_`, `.`, `-` y `/`. |
| **PHP-FPM** | **Sin PHP** o una versión, por ejemplo **PHP 8.4-FPM**. | Solo aparecen las versiones de PHP-FPM que están en ejecución. |

### Qué hace el panel al pulsar Crear

1. Crea la carpeta del document root si no existe.
2. Si la carpeta no tiene `index.html` ni `index.php`, crea un `index.html` de ejemplo con el nombre del dominio.
3. Asigna a `www-data` las carpetas y el archivo que acaba de crear.
4. En Apache con PHP, habilita los módulos `proxy_fcgi` y `setenvif`.
5. Escribe el archivo `sites-available/<dominio>.conf`.
6. Habilita el sitio, prueba la configuración y recarga el servicio.

Si la prueba falla, el panel deshace el sitio y muestra **La prueba de configuración falló; se revirtió el sitio** con la salida del error. También borra el `index.html` de ejemplo y las carpetas del document root que creó en ese mismo intento (si siguen vacías). Lo que ya existía antes no se toca.

### Qué contiene la configuración generada

- El sitio escucha solo en el puerto **80** (HTTP). Para HTTPS usa [SSL / Let's Encrypt](22-ssl.md).
- **Apache:** desactiva el listado de carpetas (`Options -Indexes`) y permite `.htaccess` (`AllowOverride All`). Los logs del sitio van a `/var/log/apache2/<dominio>-error.log` y `<dominio>-access.log`.
- **Nginx:** con PHP, las rutas que no existen se envían a `index.php` (útil para Laravel, WordPress y similares). Bloquea los archivos `.ht*`. Los logs van a `/var/log/nginx/<dominio>.error.log` y `<dominio>.access.log`.

> **Nota:** Si ya existe un archivo `<dominio>.conf`, verás **Ya existe un sitio con ese nombre**. Elige otro dominio o elimina el sitio anterior.

## Publicar un sitio con PHP y HTTPS (recorrido completo)

Este ejemplo publica `sistema.ejemplo.gob.gt` con PHP y certificado gratuito de Let's Encrypt.

### 1. Prepara el DNS y la red

1. Pide al responsable del DNS un registro **A** para `sistema.ejemplo.gob.gt` que apunte a la IP pública del servidor (y **AAAA** si usas IPv6).
2. Comprueba que el nombre ya resuelve. En la [Terminal](29-terminal.md) ejecuta `getent hosts sistema.ejemplo.gob.gt`. Debe mostrar la IP correcta.
3. Abre los puertos **80** y **443** en el firewall del servidor ([Firewall](20-firewall.md)) y en el firewall perimetral o NAT de la institución.

### 2. Verifica PHP-FPM

1. Ve a **Menú → Servidores → PHP**.
2. Revisa que la versión que quieres usar muestre **PHP-FPM** `activo`.
3. Si no está instalado, instala `php-fpm` desde [Paquetes](27-paquetes.md). En Debian 13 la versión por defecto es PHP 8.4.

### 3. Crea el sitio

1. Vuelve a **Servidor web** y pulsa **+ Nuevo sitio**.
2. **Dominio:** `sistema.ejemplo.gob.gt`.
3. **Alias:** déjalo vacío o agrega `www.sistema.ejemplo.gob.gt` (solo si ese nombre también existe en el DNS).
4. **Document root:** deja el valor propuesto (`/var/www/sistema.ejemplo.gob.gt/public_html`).
5. **PHP-FPM:** elige la versión, por ejemplo **PHP 8.4-FPM**.
6. Pulsa **Crear**. El sitio aparece `habilitado` en la tabla.

### 4. Sube el contenido

1. Ve a **Menú → Herramientas → Archivos** y entra en `/var/www/sistema.ejemplo.gob.gt/public_html`.
2. Pulsa **Subir…** y arrastra tus archivos. Si subes un `.zip`, elige **Extraer y borrar el comprimido**.
3. Borra el `index.html` de ejemplo si tu sitio usa `index.php`.
4. Los archivos subidos quedan con propietario `root`. Abre **Permisos** sobre la carpeta `public_html`, pon **Propietario** `www-data`, **Grupo** `www-data`, marca **Aplicar recursivamente** y pulsa **Aplicar**.

### 5. Prueba por HTTP

Abre `http://sistema.ejemplo.gob.gt` en el navegador. Debes ver tu sitio.

### 6. Obtén el certificado HTTPS

1. Ve a **Menú → Servidores → SSL / Let's Encrypt**.
2. Pulsa **+ Obtener certificado**.
3. **Dominios:** `sistema.ejemplo.gob.gt` (agrega el alias si lo usaste, separado por coma).
4. **Email:** una cuenta institucional que reciba los avisos de caducidad.
5. **Método:** **Apache (instala en el vhost)** o **Nginx (instala en el vhost)**, según tu servidor.
6. Deja marcado **Redirigir HTTP a HTTPS**.
7. Pulsa **Obtener** y espera. Puede tardar varios minutos.
8. Abre `https://sistema.ejemplo.gob.gt`. Debe cargar con candado.

> **Consejo:** En Apache, certbot crea un sitio nuevo llamado `<dominio>-le-ssl.conf`. Lo verás en la tabla de sitios, en el puerto `443`.

## Habilitar o deshabilitar un sitio

1. Busca el sitio en la tabla **Sitios**.
2. Pulsa **Habilitar** o **Deshabilitar**.
3. Confirma en el diálogo (**Habilitar sitio** o **Deshabilitar sitio**).

Al habilitar, el panel prueba la configuración y recarga. Si la prueba falla, deshace la habilitación y muestra **La configuración falló; se deshizo la habilitación**.

Al deshabilitar, el sitio deja de responder tras la recarga. Si la prueba de configuración falla por otro motivo, el sitio queda deshabilitado y verás **Deshabilitado, pero la prueba de config falló**.

## Editar la configuración de un sitio

1. Pulsa **Editar** en la fila del sitio. Se abre el archivo en el editor de [Archivos](26-archivos.md).
2. Haz los cambios y pulsa **Guardar** (o `Ctrl+S`). Se crea una copia `.matipanel.bak` del archivo anterior.
3. Vuelve a **Servidor web** y pulsa **Probar config**.
4. Si la prueba es correcta, pulsa **Recargar**.

> **Importante:** Guardar el archivo no aplica los cambios. Siempre debes **Probar config** y luego **Recargar**.

> **Nota:** La tabla de sitios no muestra respaldos ni temporales de editores: archivos que terminan en `.bak` (incluido `.matipanel.bak`), `~`, `.dpkg-old`, `.dpkg-dist` o `.swp`, ni los que empiezan con `.`. Siguen en el disco; verlos o borrarlos se hace desde [Archivos](26-archivos.md).

## Eliminar un sitio

1. Pulsa **Eliminar** en la fila del sitio.
2. Confirma en **Eliminar sitio**. Si es un sitio por defecto del paquete, se pide una segunda confirmación (**Sitio por defecto**).

El panel deshabilita el sitio, borra su archivo de `sites-available`, prueba la configuración y recarga.

> **Importante:** Eliminar un sitio **no** borra su document root, sus logs ni su certificado SSL. Bórralos aparte si ya no los necesitas (desde [Archivos](26-archivos.md) y [SSL / Let's Encrypt](22-ssl.md)).

## Activar o desactivar módulos de Apache

La tarjeta **Módulos de Apache** aparece solo en la pestaña **Apache**.

1. Escribe en **Filtrar módulo…** para buscar (por ejemplo `rewrite`, `headers` o `ssl`).
2. Usa el selector **Todos / Habilitados / Deshabilitados** para acotar la lista.
3. Pulsa **Habilitar** o **Deshabilitar** y confirma en **Módulo de Apache**.
4. Lee el resultado:
    - **Módulo actualizado — config OK (reinicia para aplicar)**: todo bien.
    - **Módulo actualizado — ¡la prueba de config FALLÓ!**: corrige el problema antes de reiniciar.
5. Pulsa **Reiniciar** arriba para aplicar el cambio.

> **Nota:** Los cambios de módulos no se aplican con **Recargar**. Algunos, como los MPM, necesitan **Reiniciar**.

## Ver los logs del servidor web

- **error.log** y **access.log** muestran las últimas 200 líneas de los logs generales (`/var/log/apache2/` o `/var/log/nginx/`).
- Los sitios creados desde el panel tienen sus propios logs. Ábrelos en **Menú → Herramientas → Logs**, eligiendo el archivo en el selector de origen. Ver [Logs](28-logs.md).

## Preguntas frecuentes

### ¿Por qué no aparece mi versión de PHP en la lista PHP-FPM?

La lista solo muestra las versiones cuyo PHP-FPM está en ejecución (tienen su socket en `/run/php/`). Instala el paquete `phpX.Y-fpm` y comprueba en [PHP](23-php.md) que PHP-FPM esté `activo`. Luego vuelve a abrir **+ Nuevo sitio**.

### Al crear el sitio sale "Document root inválido". ¿Qué hago?

La ruta debe ser absoluta y estar bajo `/var/www/`, `/srv/` o `/home/`. No puede contener `..`, espacios ni caracteres especiales. Ejemplo válido: `/var/www/sistema.ejemplo.gob.gt/public_html`.

### Habilité un módulo de Apache y no noto el cambio.

Los módulos se aplican al reiniciar. Pulsa **Reiniciar** en la barra superior del módulo.

### Edité un sitio y ahora Recargar dice "La configuración tiene errores; no se aplicó".

El servidor sigue funcionando con la configuración anterior. Lee la salida de la prueba: indica el archivo y la línea con el error. Corrígelo con **Editar**, o restaura la copia `.matipanel.bak` desde [Archivos](26-archivos.md). Después pulsa **Probar config** y **Recargar**.

### ¿Eliminar un sitio borra la página web?

No. Solo borra el archivo de configuración. La carpeta del document root sigue en el disco.

## Ver también

- [SSL / Let's Encrypt](22-ssl.md)
- [PHP](23-php.md)
- [Archivos](26-archivos.md)
- [Firewall](20-firewall.md)
- [Logs](28-logs.md)
- [Servicios](13-servicios.md)
- [Solución de problemas](90-solucion-de-problemas.md)
