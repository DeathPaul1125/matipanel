# Archivos de contenedores

Es como [Archivos](26-archivos.md), pero **dentro de un contenedor**: el de una app de [Despliegues](56-despliegues.md), su base de datos, un [stack](59-stacks.md) o cualquier contenedor en marcha. Sirve para revisar un `.env` o un log, ver qué subió un usuario a `storage`, cambiar una configuración para probar o bajar un archivo. Lo encuentras en **Menú → Archivos y discos → Archivos de contenedores**, y con el botón **Archivos** de la tabla **Contenedores** de cada app y de la página **Docker**.

## Usarlo

1. En el selector de arriba, elige el contenedor. Están agrupados por proyecto: `matipanel-<app>` son las apps de Despliegues.
2. Navega con doble clic en las carpetas, con la ruta de arriba (haz clic en un espacio libre para escribirla) o con **↑**.
3. En cada archivo:
   - **Abrir** lo muestra en el editor; con **Guardar** o Ctrl+S se guarda.
   - **Descargar** baja el archivo. En una carpeta, la baja como `.tar`.
   - **Renombrar** y **Eliminar**. Eliminar pide escribir el nombre.
4. **Nueva carpeta**, **Nuevo archivo** y **Subir** trabajan en la carpeta actual. Lo nuevo queda con el **mismo dueño que la carpeta**, normalmente `www-data` en las apps, para que la app pueda leerlo y escribirlo.

El editor abre archivos de texto de hasta 2 MB. Los binarios y los más grandes se descargan. Si el archivo cambió mientras lo editabas, por ejemplo porque la app escribió un log, **Guardar** avisa en lugar de pisar el cambio.

## Lo que se conserva y lo que no

Un contenedor se crea desde una imagen, y **en cada despliegue se crea uno nuevo**. Por eso:

- Las carpetas marcadas como **volumen** (por ejemplo `/var/www/html/storage` en las apps de Laravel, o los datos de la base) **se conservan**.
- Todo lo demás (el código en `/var/www/html/app`, `config`, `vendor`…) es de la imagen: **lo que cambies ahí se pierde** en el próximo despliegue o al recrear el contenedor. Sirve para probar algo rápido; el cambio de verdad va en el repositorio.

Arriba de la lista, el panel dice si la carpeta actual es **volumen** o **temporal**, y enlaza a las carpetas persistentes del contenedor.

## Seguridad

- Todo se hace con `docker exec` como root dentro del contenedor y con `docker cp`. Docker resuelve las rutas y los enlaces simbólicos **dentro** del contenedor, así que un enlace creado ahí no puede apuntar a archivos del servidor.
- No se pueden borrar carpetas del sistema (`/`, `/etc`, `/usr`…).
- Los usuarios con rol **Solo lectura** no entran, igual que a Archivos y a la Terminal. Con rol **Personalizado** se da por separado: **Archivos de contenedores** en [Permisos del panel](35-permisos.md).
- Con `MATIPANEL_FILE_WRITE=0` (ver [Archivos](26-archivos.md)) solo se puede ver y descargar.
- Cada cambio queda en [Auditoría](32-auditoria.md).

## Preguntas frecuentes

### Dice «La imagen de este contenedor no tiene sh»

Hay imágenes mínimas (*distroless*) que no traen ni `sh`: no se pueden explorar así. Usa `docker cp contenedor:/ruta .` desde la [Terminal](29-terminal.md) del servidor.

### Subí un archivo y la app no lo ve

Revisa que lo hayas subido dentro de una carpeta que la app usa. En Laravel, lo público va en `storage/app/public`, y necesita el enlace `public/storage` (`php artisan storage:link`, que el panel ya ejecuta al arrancar).

## Ver también

- [Archivos](26-archivos.md): los archivos del servidor.
- [Terminal](29-terminal.md#abrir-una-terminal-dentro-de-un-contenedor): una consola dentro del contenedor.
- [Despliegues](56-despliegues.md)
- [Docker](25-docker.md)
