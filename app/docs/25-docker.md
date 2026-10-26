# Docker

Este módulo muestra los contenedores, imágenes y volúmenes de Docker, y permite iniciar, detener y reiniciar contenedores y ver sus logs. Lo encuentras en **Menú → Web y aplicaciones → Docker**.

## Requisitos

- Docker debe estar instalado. Si no lo está, la página muestra **Docker no está instalado** con el comando de instalación desde los repositorios de Debian: `sudo apt install docker.io` (también puedes instalar el paquete `docker.io` desde [Paquetes](27-paquetes.md)).
- MatiPanel detecta Docker cada vez que abres la página. Después de instalarlo, basta con recargarla; no hace falta reiniciar el panel.

> **Nota:** El panel corre como `root`, así que no necesita estar en el grupo `docker`.

## Qué puedes y qué no puedes hacer

| Puedes | No puedes (usa la Terminal) |
|---|---|
| Ver resumen de contenedores, imágenes y versión | Crear contenedores nuevos (`docker run`) |
| Iniciar, detener y reiniciar contenedores | Eliminar contenedores, imágenes o volúmenes |
| Ver las últimas 200 líneas de logs de un contenedor | Descargar imágenes (`docker pull`) |
| Ver la lista de imágenes y volúmenes | Usar Docker Compose |

Para todo lo de la segunda columna usa la [Terminal](29-terminal.md) con `sudo docker …`.

## Ver el resumen de Docker

Arriba hay cuatro indicadores:

| Indicador | Significado |
|---|---|
| **Containers** | Total de contenedores, y cuántos están corriendo y detenidos. |
| **Imágenes** | Cantidad de imágenes descargadas. |
| **Versión** | Versión del motor Docker y el driver de almacenamiento. |
| **SO** | Sistema operativo que informa Docker. |

El resumen y la tabla de contenedores se actualizan solos cada 10 segundos.

## Revisar los contenedores

La tabla **Containers** muestra todos los contenedores, también los detenidos:

| Columna | Significado |
|---|---|
| **ID** | Identificador corto (12 caracteres). |
| **Nombre** | Nombre del contenedor. |
| **Imagen** | Imagen desde la que se creó. |
| **Estado** | `running` (verde), `paused` (ámbar) u otro estado (gris), con el detalle de Docker, por ejemplo `Up 3 hours`. |
| **Puertos** | Puertos publicados, por ejemplo `0.0.0.0:8080->80/tcp`. |
| **Acciones** | Botones para iniciar, detener, reiniciar y ver logs. |

> **Importante:** Docker abre los puertos publicados (`0.0.0.0:…`) con sus propias reglas de red, por delante de UFW. Un puerto publicado puede quedar accesible aunque el [Firewall](20-firewall.md) no lo permita. Si un servicio debe ser solo local, publícalo en `127.0.0.1`.

## Iniciar, detener o reiniciar un contenedor

En la columna **Acciones** de cada contenedor:

| Botón | Acción |
|---|---|
| **▶** | Inicia el contenedor (`docker start`). |
| **■** | Detiene el contenedor (`docker stop`). |
| **↻** | Reinicia el contenedor (`docker restart`). |

1. Pulsa el botón de la acción.
2. Confirma en el diálogo (**Iniciar container**, **Detener container** o **Reiniciar container**). Detener y reiniciar se muestran como peligrosos (botón rojo), porque cortan el servicio que presta el contenedor.
3. Espera un momento. La tabla se actualiza sola.
4. Si algo falla, aparece un diálogo **Error** con el mensaje de Docker.

> **Nota:** Si un contenedor tiene una política de reinicio (`--restart always`), Docker puede volver a iniciarlo por su cuenta.

## Ver los logs de un contenedor

1. Pulsa **logs** en la fila del contenedor.
2. Se abre una ventana con las últimas 200 líneas (`docker logs --tail 200`). Si esas líneas suman más de 1 MB, se muestra solo el primer MB con el aviso **salida recortada**.
3. Pulsa **Cerrar** para volver.

Los logs no se actualizan en vivo. Vuelve a pulsar **logs** para ver las líneas nuevas. Para seguirlos en tiempo real usa la [Terminal](29-terminal.md): `sudo docker logs -f <nombre>`.

## Revisar imágenes y volúmenes

- **Imágenes:** ID, **Repositorio**, **Tag**, **Tamaño** y antigüedad (**Creada**).
- **Volúmenes:** **Nombre**, **Driver** y **Mountpoint** (la carpeta del volumen en el disco, normalmente bajo `/var/lib/docker/volumes/`).

Estas tablas son solo de consulta y se cargan al abrir la página. Recarga la página para actualizarlas.

> **Consejo:** Puedes ver los archivos de un volumen con [Archivos](26-archivos.md) navegando a su **Mountpoint**. Ten cuidado al modificarlos con el contenedor en marcha.

## Preguntas frecuentes

### Instalé Docker y el panel sigue diciendo "Docker no está instalado".

Recarga la página. Si sigue igual, comprueba en la [Terminal](29-terminal.md) que el comando exista (`which docker`) y que el servicio esté activo (`systemctl status docker`).

### ¿Cómo elimino un contenedor o una imagen que ya no uso?

El panel no tiene botón para eso. Usa la terminal: `sudo docker rm <nombre>` para un contenedor detenido, y `sudo docker image prune` para borrar imágenes sin usar.

### Detuve un contenedor y volvió a arrancar solo.

Tiene una política de reinicio (`always` o `unless-stopped`), o lo controla Docker Compose o un servicio de systemd. Revisa cómo se creó.

### ¿Los logs que veo aquí son los de la aplicación?

Son la salida estándar del contenedor (`docker logs`). Si la aplicación escribe sus logs en archivos dentro del contenedor o en un volumen, búscalos allí.

## Ver también

- [Terminal](29-terminal.md)
- [Archivos](26-archivos.md)
- [Servicios](13-servicios.md)
- [Firewall](20-firewall.md)
- [Paquetes](27-paquetes.md)
