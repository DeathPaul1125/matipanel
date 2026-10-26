# Logs

MatiPanel tiene dos páginas para revisar los registros del sistema. **Logs** consulta el journal de systemd (`journalctl`) y los archivos de `/var/log`. **Logs en vivo** muestra el journal en tiempo real. Las encuentras en **Menú → Herramientas → Logs** y **Menú → Herramientas → Logs en vivo**.

## Consultar el journal del sistema

1. Ve a **Menú → Herramientas → Logs**. Al abrir, la página ya carga las últimas 200 líneas del journal.
2. Deja el primer selector en **journalctl**.
3. Ajusta los filtros (ver la tabla).
4. Pulsa **Cargar**.

| Filtro | Qué hace |
|---|---|
| **Unidad** | Muestra solo los mensajes de un servicio, por ejemplo `ssh.service`, `nginx` o `mariadb`. Vacío: todo el sistema. |
| **Prioridad** | **Todas las prioridades**, `err`, `warning` o `info`. Cada nivel incluye también los más graves. Por ejemplo, `err` muestra errores y mensajes críticos. |
| **Líneas** | 100, 200, 500 o 1000. La opción **5000 (archivos)** solo vale para archivos; con journalctl el máximo es 1000. |

Las líneas se muestran con fecha y hora en formato ISO. La vista baja sola hasta las más recientes.

> **Nota:** Si escribes un nombre de unidad con caracteres no válidos (espacios, `;`, etc.), el filtro se ignora y verás los mensajes de todo el sistema.

> **Consejo:** La página no tiene buscador propio. Usa `Ctrl+F` del navegador para buscar dentro de las líneas cargadas.

## Leer un archivo de /var/log

1. Abre el primer selector y elige un archivo, por ejemplo `/var/log/apache2/error.log` o `/var/log/nginx/<dominio>.error.log`.
2. El archivo se carga al elegirlo. Los campos **Unidad** y **Prioridad** se ocultan.
3. Para cambiar la cantidad, elige un valor en **Líneas** (hasta **5000 (archivos)**) y pulsa **Cargar**.

- Se muestran las últimas líneas del archivo (como `tail`).
- La lista incluye todos los archivos dentro de `/var/log` y sus subcarpetas. No incluye los rotados comprimidos (`.gz`, `.xz`, `.bz2`, `.zst`) ni `wtmp`, `btmp` y `lastlog`.
- La lista se arma al abrir la página. Si se crea un log nuevo, recarga la página.

> **Nota:** Algunos archivos de la lista son binarios, por ejemplo los de `/var/log/journal/`. Si los abres verás caracteres sin sentido. Para el journal usa la opción **journalctl**.

> **Consejo:** Para leer un log rotado y comprimido (por ejemplo `syslog.2.gz`), descárgalo desde [Archivos](26-archivos.md) o usa `zcat` en la [Terminal](29-terminal.md).

## Ver los logs en vivo

1. Ve a **Menú → Herramientas → Logs en vivo**.
2. Si quieres, escribe una unidad en **Unidad opcional**, por ejemplo `nginx.service`.
3. Pulsa **Conectar**. El indicador cambia a `conectado`.
4. Verás las últimas 50 líneas y luego cada línea nueva en cuanto llega.
5. Pulsa **Detener** para cortar. El indicador vuelve a `desconectado`.

- El botón **Vista histórica** te lleva a la página **Logs**.
- La vista en vivo no tiene filtro de prioridad.
- Para cambiar la unidad, pulsa **Detener**, escribe la nueva unidad y pulsa **Conectar**.
- Al salir de la página, la conexión se cierra sola.

### Límites de la vista en vivo

- La pantalla guarda como máximo unas 5000 líneas. Al superarlas, conserva las últimas 3000.
- Cada conexión envía como máximo 10 000 líneas. Después deja de recibir líneas nuevas, aunque el indicador siga en `conectado`. Pulsa **Detener** y **Conectar** para seguir.

> **Consejo:** Usa la vista en vivo mientras reproduces un problema. Por ejemplo: conecta con `nginx.service`, recarga el sitio en otra pestaña y mira qué aparece.

## Encontrar el log correcto

| Quieres revisar | Dónde mirar |
|---|---|
| Inicios de sesión SSH | journalctl, unidad `ssh.service` |
| Errores de un sitio Apache | Archivo `/var/log/apache2/<dominio>-error.log` o `/var/log/apache2/error.log` |
| Errores de un sitio Nginx | Archivo `/var/log/nginx/<dominio>.error.log` o `/var/log/nginx/error.log` |
| PHP-FPM | journalctl, unidad `php8.4-fpm` (cambia la versión), o el archivo `/var/log/php8.4-fpm.log` |
| MariaDB/MySQL | journalctl, unidad `mariadb` |
| PostgreSQL | Archivos en `/var/log/postgresql/` |
| Instalaciones de paquetes | Archivos `/var/log/apt/history.log` y `/var/log/dpkg.log` |
| Respaldos programados del panel | Archivo `/var/log/matipanel-backups.log` |
| El propio MatiPanel | journalctl, unidad `matipanel` |

## Preguntas frecuentes

### Filtré por unidad y no aparece nada.

Revisa el nombre exacto de la unidad en [Servicios](13-servicios.md). En Debian el servicio SSH se llama `ssh`, no `sshd`. Prueba también sin filtro de prioridad.

### ¿Por qué solo veo 1000 líneas del journal aunque elegí 5000?

Con journalctl el máximo es 1000 líneas. La opción de 5000 solo aplica a archivos de `/var/log`.

### La vista en vivo dejó de mostrar líneas pero sigue "conectado".

Llegó al límite de 10 000 líneas por conexión. Pulsa **Detener** y luego **Conectar**.

### ¿Puedo borrar o vaciar logs desde aquí?

No. Estas páginas son solo de lectura. Para liberar espacio del journal, en la [Terminal](29-terminal.md) usa por ejemplo `sudo journalctl --vacuum-time=30d`.

## Ver también

- [Servicios](13-servicios.md)
- [Servidor web](21-servidor-web.md)
- [Archivos](26-archivos.md)
- [Terminal](29-terminal.md)
- [Solución de problemas](90-solucion-de-problemas.md)
