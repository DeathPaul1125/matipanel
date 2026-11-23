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
| Iniciar, detener y reiniciar contenedores | Eliminar contenedores o imágenes |
| Ver las últimas 200 líneas de logs de un contenedor | Descargar imágenes (`docker pull`) |
| Ver imágenes y volúmenes; eliminar volúmenes sin usar | Usar Docker Compose |

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

La sección **Contenedores** muestra todos los contenedores, también los detenidos, **agrupados por proyecto**:

- **Aplicaciones desplegadas** (módulo [Despliegues](56-despliegues.md)): un grupo por aplicación con el nombre de la app, por ejemplo **quetzal**, y dentro su **Aplicación** (con el slot *blue* o *green*), **Base de datos**, **Redis**, **Worker** o **Programador**. El botón **Ver despliegue** abre la aplicación.
- **Otros proyectos de Docker Compose**: un grupo por proyecto, con el nombre del servicio en cada contenedor.
- **Contenedores sueltos**: los creados con `docker run`, sin proyecto.

La cabecera de cada grupo tiene un LED: verde si todos sus contenedores están en ejecución, ámbar si solo algunos y gris si ninguno.

| Columna | Significado |
|---|---|
| **Nombre** | El nombre que le pusiste (o su función, por ejemplo *Base de datos*) y debajo el nombre real del contenedor. Pasa el ratón para ver el ID. |
| **Imagen** | Imagen desde la que se creó. |
| **Estado** | `running` (verde), `paused` (ámbar) u otro estado (gris), con el detalle de Docker, por ejemplo `Up 3 hours`. |
| **Puertos** | Puertos publicados, por ejemplo `0.0.0.0:8080->80/tcp`. |
| **Acciones** | Botones para iniciar, detener, reiniciar, ver logs y, si está en marcha, **consola**: abre una [terminal dentro del contenedor](29-terminal.md#abrir-una-terminal-dentro-de-un-contenedor), y **archivos** abre sus [archivos](61-archivos-contenedores.md). |

### Ponerles nombre

- **Grupo:** botón **Renombrar** en su cabecera.
- **Contenedor:** el lápiz (✎) junto a su nombre.

Escribe un nombre de hasta 60 caracteres, o déjalo vacío para volver al nombre automático. Son etiquetas del panel (se guardan en `/var/lib/matipanel/docker_names.json`): el contenedor en Docker no cambia de nombre, porque Docker Compose lo volvería a crear con el original en el siguiente despliegue.

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

**Imágenes:** ID, **Repositorio**, **Tag**, **Tamaño** y antigüedad (**Creada**). Se borran las que no se usan desde [Discos → Qué se puede limpiar](15-discos.md).

## Volúmenes

Un **volumen** guarda datos que sobreviven al contenedor: la base de datos de una app, su `storage` o los datos de un stack. Al volver a desplegar, el contenedor se reemplaza y el volumen sigue ahí.

La tarjeta **Volúmenes** muestra arriba cuántos hay, cuánto pesan en total y cuánto ocupan **los que no se usan**. Por cada volumen:

| Columna | Qué dice |
|---|---|
| **Volumen** | Su nombre y **de quién es**: <br>• **App «x» · base de datos / archivos subidos**: de una app de [Despliegues](56-despliegues.md) que existe.<br>• **App «x» (ya eliminada)**: quedó de una app que se borró sin marcar «borrar también los datos».<br>• **Proyecto «x»**: de un [stack](59-stacks.md) u otro proyecto de Docker Compose.<br>• **Anónimo**: sin nombre; lo creó una imagen (por ejemplo, la de una base de datos que se probó con `docker run`).<br>• **Creado a mano**: con `docker volume create` u otro programa. |
| **Tamaño** | Lo que ocupa en el disco. |
| **Estado** | **en uso**, con los contenedores que lo usan (aunque estén detenidos), o **sin usar**. |

Los que no se usan aparecen primero, de mayor a menor. Marca **Solo los que no se usan** para ver solo esos. Desde **Discos → Volúmenes de Docker sin usar → Revisar y eliminar** se llega directo con ese filtro.

### Revisar y eliminar un volumen

1. Pulsa **Ver contenido**: abre la carpeta del volumen en [Archivos](26-archivos.md) para revisar qué tiene. En una base de datos verás sus archivos internos; si dudas, no lo borres.
2. Pulsa **Eliminar** y escribe el nombre del volumen. Solo aparece en los que **no se usan**, y el panel lo vuelve a comprobar antes de borrar.
   - Si es de una **app que todavía existe** (por ejemplo, porque sus contenedores están detenidos), el aviso lo dice: borrarlo es perder su base o sus archivos. Haz antes un respaldo en [Respaldos de apps](57-respaldos-apps.md).

**Eliminar los anónimos sin usar** borra de una vez los volúmenes **sin nombre** que ningún contenedor usa. Los que tienen nombre, como las bases y el `storage` de las apps, no se tocan. Necesita Docker 23 o superior; en versiones anteriores el botón no aparece.

> **Importante:** Borrar un volumen no se puede deshacer.

## Preguntas frecuentes

### Instalé Docker y el panel sigue diciendo "Docker no está instalado".

Recarga la página. Si sigue igual, comprueba en la [Terminal](29-terminal.md) que el comando exista (`which docker`) y que el servicio esté activo (`systemctl status docker`).

### ¿Cómo elimino un contenedor o una imagen que ya no uso?

Las imágenes sin usar se borran en [Discos → Qué se puede limpiar](15-discos.md), y los volúmenes en [Volúmenes](#volumenes). Para un contenedor detenido usa la terminal: `sudo docker rm <nombre>`.

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
