# PHP

Este módulo administra las versiones de PHP instaladas: `php.ini`, PHP-FPM y sus pools, módulos y extensiones. Lo encuentras en **Menú → Web y aplicaciones → PHP**. Trabaja con la estructura de Debian: `/etc/php/<versión>/<sapi>/`.

## Requisitos e instalación de PHP

Si no hay PHP instalado, la página muestra **PHP no está instalado** con el comando para la versión por defecto de Debian:

```
apt install php-fpm php-cli
```

En Debian 13 la versión por defecto es PHP 8.4. Puedes instalarla desde [Paquetes](27-paquetes.md) (botón **Ir a Paquetes**).

La misma página muestra cómo agregar el repositorio de Ondřej Surý (`packages.sury.org`) si necesitas varias versiones en paralelo, por ejemplo 8.1 y 8.4.

> **Importante:** El repositorio de Surý es externo a Debian. Consulta la política de la institución antes de agregarlo en un servidor de producción.

### Qué es un SAPI

Cada versión de PHP puede tener varias configuraciones, una por forma de uso (SAPI):

| SAPI | Se usa para |
|---|---|
| `cli` | El comando `php` en la terminal, cron, Composer, Artisan. |
| `fpm` | Sitios web servidos con PHP-FPM (lo normal con Nginx y con los sitios creados en [Servidor web](21-servidor-web.md)). |
| `apache2` | Sitios con `mod_php` de Apache (opción antigua). |

Cada SAPI tiene su propio `php.ini`. Cambiar el de `cli` no afecta a los sitios web.

## Ver las versiones instaladas

- La barra superior muestra la versión del comando `php` (salida de `php -v`) y el modo de la alternativa (`auto` o `manual`).
- Debajo hay una pestaña por versión, por ejemplo **PHP 8.4**. Pulsa una pestaña para ver sus datos.
- La tarjeta de la versión muestra los SAPIs instalados y si existe el binario `php8.4` (**en PATH** o **sin binario CLI**).
- Pulsa **Refrescar** para volver a leer todo.

## Cambiar la versión de PHP de la línea de comandos

1. En la barra superior, elige la versión en el selector (por ejemplo `php8.4`).
2. Pulsa **Establecer CLI por defecto**.
3. Confirma en **PHP por defecto** con **Cambiar**.

Esto cambia solo el comando `php` (vía `update-alternatives`). Afecta a cron, Composer y scripts. **No** cambia la versión de los sitios web: cada sitio usa la versión de PHP-FPM indicada en su configuración.

## Cambiar valores de php.ini

Cada SAPI tiene una tarjeta **php.ini — `<sapi>`** con la ruta del archivo y seis directivas:

| Directiva | Para qué sirve | Ejemplo |
|---|---|---|
| `memory_limit` | Memoria máxima por script. | `256M` |
| `upload_max_filesize` | Tamaño máximo de cada archivo subido. | `64M` |
| `post_max_size` | Tamaño máximo de todo el formulario. Debe ser mayor o igual que `upload_max_filesize`. | `64M` |
| `max_execution_time` | Segundos máximos de ejecución. | `120` |
| `display_errors` | Mostrar errores en pantalla. En producción usa `Off`. | `Off` |
| `date.timezone` | Zona horaria. | `America/Guatemala` |

1. Abre la pestaña de la versión.
2. En la tarjeta del SAPI correcto (normalmente `fpm` para sitios web), cambia los valores.
3. Pulsa **Guardar y aplicar**.
4. Revisa la lista de cambios en **Guardar php.ini** y pulsa **Guardar**.

El panel crea un respaldo `php.ini.matipanel.bak` y escribe los cambios. Después aplica automáticamente:

- `fpm`: reinicia `phpX.Y-fpm` si está activo.
- `apache2`: recarga Apache si está activo.
- `cli`: no hace falta nada.

Verás un aviso como **Guardado ✓ · php8.4-fpm: restart OK**.

### Reglas y límites

- Los valores solo aceptan letras, números y `_ . / : + -`, hasta 64 caracteres. No se permiten espacios ni comillas.
- Si un campo dice **(sin definir)**, la directiva está comentada y PHP usa su valor interno.
- Un campo vacío se ignora: no puedes borrar una directiva desde aquí. Para eso usa **editar**.
- Si no cambiaste nada, verás **Sin cambios**.

> **Consejo:** Para subir archivos grandes con Nginx, además de PHP debes subir el límite de Nginx. Por defecto Nginx acepta solo 1 MB por petición. Agrega `client_max_body_size 64M;` dentro del bloque `server` del sitio.

## Editar php.ini completo

1. Pulsa **editar** junto a la ruta del `php.ini`. Se abre en el editor de [Archivos](26-archivos.md).
2. Haz los cambios y pulsa **Guardar**.
3. Vuelve a **PHP** y pulsa **Reiniciar phpX.Y-fpm** para aplicar (o recarga Apache si usas `mod_php`).

> **Importante:** Al editar con el editor, los cambios no se aplican solos. Debes reiniciar PHP-FPM.

## Administrar PHP-FPM y sus pools

Si la versión tiene FPM instalado, verás la tarjeta **PHP-FPM** con su estado (`activo` o `inactivo`).

### Reiniciar PHP-FPM

1. Pulsa **Reiniciar phpX.Y-fpm**.
2. Confirma en **Reiniciar PHP-FPM**.

Durante el reinicio, las peticiones PHP en curso se cortan. Hazlo en horarios de poco uso.

### Revisar los pools

La tabla lista cada pool de `/etc/php/<versión>/fpm/pool.d/`:

| Columna | Significado |
|---|---|
| **Pool** | Nombre del pool (por defecto `www`). |
| **Archivo** | Ruta del archivo `.conf`. |
| **listen** | Socket o puerto, por ejemplo `/run/php/php8.4-fpm.sock`. |
| **pm** | Modo de gestión de procesos (`dynamic`, `static` u `ondemand`). |
| **pm.max_children** | Máximo de procesos PHP simultáneos. |

Para cambiar un pool, pulsa **editar**, guarda y luego pulsa **Reiniciar phpX.Y-fpm**. El panel no crea pools nuevos.

> **Consejo:** `pm.max_children` limita cuántas peticiones PHP atiendes a la vez. Si lo subes mucho y no hay memoria suficiente, el servidor puede quedarse sin RAM.

## Activar o desactivar módulos

La tarjeta **Módulos** muestra:

- **Cargados en CLI**: los módulos que carga el comando `php` de esa versión.
- Una tabla con los módulos instalados (`mods-available`) y una casilla por SAPI.

1. Marca o desmarca la casilla del módulo en la columna del SAPI.
2. Confirma en **Módulo PHP** con **Habilitar** o **Deshabilitar**.

El panel usa `phpenmod`/`phpdismod` y aplica el cambio igual que al guardar `php.ini` (reinicia FPM o recarga Apache si están activos). Si cancelas, la casilla vuelve a su estado anterior.

## Instalar una extensión

1. Abre la pestaña de la versión.
2. En **Instalar extensión**, escribe el nombre sin prefijo. El prefijo `php8.4-` ya aparece al lado.
3. Pulsa **Instalar** y confirma en **Instalar extensión**.
4. Espera. Verás **Instalando php8.4-…** y después **php8.4-…: instalado ✓** o el error de APT.

Nombres habituales: `mysql` (MariaDB/MySQL), `pgsql`, `gd`, `intl`, `curl`, `mbstring`, `xml`, `zip`, `bcmath`, `sqlite3`.

- El nombre solo admite minúsculas, números y `+ . -`.
- El paquete debe existir en los repositorios. Si no existe, APT devuelve un error.
- La instalación puede tardar varios minutos. No cierres la página.
- Si la extensión no aparece en tu sitio después de instalarla, pulsa **Reiniciar phpX.Y-fpm**.

> **Nota:** Algunas extensiones de Debian no llevan la versión en el nombre del paquete (por ejemplo `php-redis` o `php-imagick`). Instálalas desde [Paquetes](27-paquetes.md).

## Preguntas frecuentes

### Cambié upload_max_filesize y mi sitio sigue rechazando archivos grandes. ¿Por qué?

Revisa tres cosas: que cambiaste el `php.ini` de `fpm` (no el de `cli`); que `post_max_size` sea igual o mayor; y, si usas Nginx, que el sitio tenga `client_max_body_size` suficiente.

### Cambié la versión de PHP por defecto y mi sitio sigue con la anterior.

Es normal. **Establecer CLI por defecto** solo afecta al comando `php`. Para cambiar la versión de un sitio, edita su configuración en [Servidor web](21-servidor-web.md) y cambia la ruta del socket (por ejemplo `/run/php/php8.4-fpm.sock`).

### Al guardar sale "Valor inválido para date.timezone".

El valor tiene espacios, comillas u otros caracteres no permitidos. Usa el formato `Region/Ciudad`, por ejemplo `America/Guatemala`.

### ¿Puedo tener varias versiones de PHP a la vez?

Sí, si las instalas (normalmente desde el repositorio de Surý). Cada versión tiene su propia pestaña, su `php.ini` y su PHP-FPM. Cada sitio elige su versión.

## Ver también

- [Servidor web](21-servidor-web.md)
- [Paquetes](27-paquetes.md)
- [Archivos](26-archivos.md)
- [Servicios](13-servicios.md)
- [Bases de datos](24-bases-de-datos.md)
