# Stacks

Un **stack** es un proyecto de **Docker Compose** cualquiera: pegas (o subes) un `compose.yaml`, opcionalmente un `.env`, y el panel lo valida, lo guarda y te deja iniciarlo, detenerlo, actualizar sus imágenes y ver sus logs, al estilo de Portainer. Sirve para publicar aplicaciones ya empaquetadas: un monitor como Uptime Kuma, un Adminer, una wiki, un sitio estático… Lo encuentras en **Menú → Web y aplicaciones → Stacks**.

## Requisitos

Docker Engine y Docker Compose v2 instalados en el servidor. Si faltan, la página lo avisa; instálalos desde **Despliegues → Requisitos del servidor** (botón **Instalar lo que falta**).

## Stacks y Despliegues: ¿cuál uso?

| | **Stacks** | **Despliegues** |
|---|---|---|
| Origen | Un `compose.yaml` que escribes tú. Las imágenes vienen de Docker Hub u otro registro. | Un repositorio de GitHub con una app Laravel o PHP. |
| Imagen | Ya existe; el panel solo la descarga. | El panel la construye en cada commit. |
| Actualizar | **Actualizar imágenes** (descarga la versión nueva y recrea). Hay un corte breve. | Automático con cada push, **sin cortes** (blue/green) y con reversión. |
| Configuración | Total: cualquier opción de Compose. | Guiada: PHP, base de datos, Redis, worker… |
| Nginx / nombre interno | No: los servicios se publican con los `ports:` del archivo. | Sí, lo configura el panel. |

Regla práctica: si es **tu** código en GitHub, usa Despliegues; si es un programa de terceros que ya tiene imagen Docker, usa Stacks.

## Crear un stack

1. Pulsa **+ Nuevo stack**.
2. Escribe el **Nombre**: minúsculas, números y guiones, empezando con letra (2 a 31 caracteres). No se puede cambiar después.
3. Elige una **Plantilla** o pega tu `compose.yaml` en el editor (también puedes usar **Subir archivo…**, hasta 256 KB). La tecla Tab inserta dos espacios: YAML no admite tabuladores.
4. Si el archivo usa variables como `${PUERTO}`, defínelas en el cuadro **.env** (`PUERTO=8080`, una por línea).
5. Pulsa **Validar** para revisarlo sin guardar, **Crear** para guardarlo o **Crear e iniciar** para guardarlo y arrancarlo. La salida de Docker (descarga de imágenes incluida) se ve en la ventana de terminal.

Plantillas incluidas:

| Plantilla | Imagen | Puerto |
|---|---|---|
| **Uptime Kuma** | `louislam/uptime-kuma:1` | 3001 |
| **Portainer CE** | `portainer/portainer-ce` | 9443 (HTTPS) — monta `docker.sock`, lee la sección de seguridad |
| **Sitio estático con Nginx** | `nginx:alpine` | 8088 (variable `PUERTO` del `.env`) |
| **Adminer** | `adminer` | 8089 |

> **Nota:** Las imágenes se descargan de Docker Hub al iniciar el stack, así que el servidor necesita salida a Internet (o un registro interno configurado en Docker).

## Validación y advertencias

Antes de guardar y antes de cada inicio, el panel ejecuta `docker compose config -q`. Si el archivo tiene un error (sangría, clave desconocida, variable mal escrita…) **no se guarda** y verás el mensaje de Compose tal cual.

Además revisa la configuración final y **avisa, sin bloquear**, de estos ajustes:

| Ajuste | Por qué es delicado |
|---|---|
| `privileged: true` | El contenedor tiene acceso total al servidor (dispositivos, kernel). |
| `network_mode: host` | Comparte la red del servidor: todo lo que escuche queda expuesto en todas sus IPs. |
| `pid: host` | Ve y puede enviar señales a todos los procesos del servidor. |
| `cap_add: ALL` o `SYS_ADMIN` | Casi equivalente a `privileged`. |
| Montar `/`, `/etc` o `/root` | El contenedor puede leer (o cambiar) la configuración y las claves del servidor. |
| Montar `/var/run/docker.sock` | Quien controle el contenedor controla Docker y, por tanto, **todo el servidor como root**. |
| Puertos en `0.0.0.0` | Se listan aparte: el servicio queda accesible desde toda la red. |

Las advertencias se guardan con el stack y se muestran en la lista (columna **Avisos**) y en la pestaña **Servicios**.

> **Importante:** Docker abre los puertos publicados directamente en iptables/nftables, **por delante** de las reglas del [Firewall](20-firewall.md). Si un servicio solo debe verse desde el propio servidor, publícalo así: `"127.0.0.1:8080:80"`.

## La lista de stacks

Cada fila muestra:

- **LED**: verde si todos los servicios están en marcha, ámbar si solo algunos (o alguno informa `unhealthy`), rojo si ninguno, gris si el stack no tiene contenedores (nunca se inició o se **bajó**).
- **Servicios**: en marcha / total.
- **Puertos** publicados (`8080→80/tcp`; con IP delante si no es para todas las interfaces).
- **Avisos**: cantidad de ajustes peligrosos.
- **Iniciar** o **Detener** directo, y **Abrir** para el detalle.

## Detalle de un stack

Arriba están las acciones; cada una muestra su salida en la ventana de terminal:

| Acción | Comando | Efecto |
|---|---|---|
| **Iniciar** | `up -d --remove-orphans` | Crea lo que falte y arranca todo. Quita contenedores de servicios que ya no están en el archivo. |
| **Reiniciar** | `restart` | Reinicia los contenedores existentes (no lee cambios del archivo). |
| **Actualizar imágenes** | `pull` + `up -d` | Descarga la versión más reciente de cada etiqueta y recrea solo lo que cambió. |
| **Detener** | `stop` | Detiene los contenedores sin borrarlos. |
| **Bajar** | `down --remove-orphans` | Borra contenedores y redes. Los volúmenes y el archivo se conservan; **Iniciar** lo vuelve a levantar. |

Pestañas:

- **Servicios**: cada servicio con su estado y salud, puertos, imagen y nombre del contenedor. Los servicios que aún no tienen contenedor aparecen como «sin contenedor».
- **Logs**: últimas 100, 300 o 1000 líneas de `docker compose logs`, de todos los servicios o de uno (como máximo 1 MB).
- **compose.yaml**: el editor. **Validar** revisa sin guardar; **Guardar** valida y guarda, y después ofrece **Aplicar** (`up -d`, recrea solo los contenedores cuya configuración cambió); **Guardar y aplicar** hace las dos cosas.
- **.env**: los valores están borrosos hasta que haces clic en el cuadro o marcas **Mostrar valores**. Se guarda y aplica igual que el compose.yaml.
- **Eliminar**: ejecuta `docker compose down` y borra la carpeta del stack. Marca **Borrar también los volúmenes** para agregar `-v` (los volúmenes con nombre del stack se pierden). Hay que escribir el nombre del stack para confirmar. Las imágenes descargadas se conservan; bórralas desde [Docker](25-docker.md) si quieres liberar espacio.

> **Nota:** El `.env` sirve para reemplazar `${VARIABLE}` dentro del compose.yaml. Para que una variable llegue **dentro** del contenedor, nómbrala en `environment:` (por ejemplo `DB_PASSWORD: ${DB_PASSWORD}`) o agrega `env_file: .env` al servicio.

## Dónde se guarda

Cada stack es una carpeta en `/var/lib/matipanel/stacks/<nombre>/`, legible solo por root (carpeta `0700`, archivos `0600`):

| Archivo | Contenido |
|---|---|
| `compose.yaml` | Tu archivo, tal como lo guardaste. |
| `.env` | Las variables (puede estar vacío). |
| `stack.json` | Servicios, advertencias y fechas; el panel lo rehace al guardar. |

Las rutas relativas del compose.yaml (`./html`, `./data`) se resuelven dentro de esa carpeta, así que puedes poner ahí tus archivos con el módulo [Archivos](26-archivos.md). El proyecto de Compose se llama `stack-<nombre>`: así aparece agrupado en el módulo [Docker](25-docker.md) y así se llaman sus volúmenes y redes (`stack-<nombre>_data`).

## Seguridad

- Un `compose.yaml` puede hacer **cualquier cosa** en el servidor: montar carpetas del sistema, usar la red del host, correr como privilegiado. Pega solo archivos que entiendas y que vengan de fuentes confiables.
- **`docker.sock` = root.** Portainer, Traefik, Watchtower y similares lo necesitan; protégelos con contraseña fuerte y no los publiques fuera de la red de administración (`"127.0.0.1:9443:9443"` o una red restringida).
- Fija versiones en las imágenes (`nginx:1.27-alpine` mejor que `nginx:latest`) para que **Actualizar imágenes** no traiga cambios inesperados.
- Las contraseñas del `.env` se guardan en texto claro (solo root las puede leer). El registro de auditoría anota cada acción, pero no el contenido del compose.yaml ni del `.env`.

## Preguntas frecuentes

### Cambié el compose.yaml pero no pasa nada

Guardar no toca los contenedores. Pulsa **Aplicar** (o **Guardar y aplicar**). **Reiniciar** no sirve para esto: reinicia los contenedores con la configuración vieja.

### ¿Puedo usar `build:`?

Sí, si el contexto está dentro de la carpeta del stack (súbelo con Archivos). **Actualizar imágenes** no reconstruye; para eso usa la terminal (`docker compose -p stack-<nombre> build`) o, para código propio, mejor [Despliegues](56-despliegues.md).

### El puerto ya está en uso

Otro servicio del servidor (o de otro stack o despliegue) usa ese puerto. Cambia el número de la izquierda en `ports:` (`"8090:80"`) y aplica.

### Borré el stack pero sigo viendo los datos

Si no marcaste **Borrar también los volúmenes**, siguen en Docker (`stack-<nombre>_…`). Al crear otra vez un stack con el mismo nombre los vuelve a usar. Las carpetas montadas del servidor (bind mounts) nunca se borran.

## Ver también

- [Docker](25-docker.md): contenedores, imágenes, volúmenes y redes de todo el servidor.
- [Despliegues](56-despliegues.md): aplicaciones propias desde GitHub, sin cortes.
- [Firewall](20-firewall.md): qué puertos están abiertos.
