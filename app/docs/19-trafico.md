# Tráfico de red

Analiza todo el tráfico que pasa por las interfaces del servidor: cuánto ancho de banda usa cada una, en vivo y con historial de 7 días; qué equipos, servicios y procesos lo consumen; capturas de paquetes para Wireshark, y avisos cuando una interfaz se satura o tiene errores. Lo encuentras en **Menú → Red y acceso → Tráfico de red**.

## Requisitos

- **Ancho de banda** y **Alertas** funcionan siempre: usan los contadores del sistema.
- **Quién consume** y **Capturas** necesitan `tcpdump`. Si falta, la pantalla muestra el botón **Instalar tcpdump** (Debian/Ubuntu); en otras distribuciones instálalo con su gestor de paquetes.

> **Nota:** Se omiten la interfaz de loopback (`lo`) y los extremos `veth…` de cada contenedor; el tráfico de Docker se ve en `docker0` y en los puentes `br-…`.

## Ancho de banda

La tabla **Interfaces** muestra cada interfaz con su estado (LED), velocidad del enlace y MTU, direcciones IP, la velocidad actual de bajada y subida (medida cada 2 s) y los totales, errores y descartes desde que arrancó el servidor.

Haz clic en una interfaz para ver sus gráficas:

| Gráfica | Qué muestra |
|---|---|
| **En vivo · Ancho de banda** | Bajada y subida de los últimos 5 minutos, punto cada 2 s. |
| **En vivo · Paquetes por segundo** | Paquetes recibidos y enviados. Muchos paquetes con poco ancho de banda suelen indicar tráfico de control, escaneos o DNS. |
| **Historial · Ancho de banda** | De 5 minutos a 7 días (un punto cada 30 s, promediado en rangos largos). |
| **Historial · Errores y descartes** | Errores de la interfaz (cable, dúplex, controlador) y paquetes descartados. |

Pasa el ratón sobre una gráfica para ver el valor exacto de cada momento.

## Quién consume

1. Elige la **Interfaz** (o **Todas (any)**) y la duración de la **Muestra** (3 a 20 s).
2. Pulsa **Analizar**. Marca **Repetir sin parar** para que se actualice continuamente, como `iftop`.

El panel escucha la interfaz durante ese tiempo (solo las cabeceras de los paquetes) y muestra, con su velocidad media:

| Tabla | Contenido |
|---|---|
| **Equipos** | Las IPs externas a este servidor que más envían y reciben. |
| **Servicios (puertos)** | Protocolo y puerto, por ejemplo `tcp/443` (HTTPS) o `udp/53` (DNS). |
| **Procesos de este servidor** | El programa local que abrió la conexión, por ejemplo `nginx (812)` o `sshd (530)`. |
| **Conversaciones** | Pares de extremos (`IP:puerto`) con más tráfico entre sí. |

Arriba verás el tráfico medio, los paquetes y el reparto por protocolo (tcp, udp, icmp…).

> **Consejo:** Tu propio navegador conectado al panel también aparece (puerto 8443). Es normal.

## Capturas

Para analizar en detalle, por ejemplo con Wireshark:

1. En **Nueva captura** elige la interfaz, la **Duración** (hasta 300 s) y el **Máximo de paquetes** (hasta 200 000). La captura termina con lo que ocurra primero.
2. Filtra si quieres: **Protocolo**, **Equipo** (IP o nombre), **Puerto**, **Red** (`192.168.1.0/24`) o un **Filtro avanzado** con sintaxis de `tcpdump`/BPF, por ejemplo `not port 22` o `tcp[tcpflags] & tcp-syn != 0`.
3. Pulsa **Iniciar captura**. La ventana de terminal muestra el progreso de `tcpdump`.

Al terminar aparece el **Resumen** (equipos, servicios, conversaciones y los primeros 200 paquetes). En **Capturas guardadas** puedes volver a ver el resumen, **Descargar** el `.pcap` o **Eliminar** la captura. Se conservan las últimas 20 (hasta 1 GB en total) en `/var/lib/matipanel/captures`, legibles solo por root.

> **Importante:** Una captura contiene el contenido de los paquetes, incluidas contraseñas de protocolos sin cifrar. Trátala como información confidencial y elimínala cuando ya no la necesites.

## Alertas

Las alertas aparecen en la **barra de estado** de arriba (la que dice «Todo en orden») cuando la condición se mantiene durante un minuto. Para cada interfaz:

| Campo | Vacío | Valor |
|---|---|---|
| **Uso máximo (Mbit/s)** | Automático: 90 % de la velocidad del enlace, si se conoce. | Aviso cuando la bajada o la subida supera ese valor. `0` = sin aviso. |
| **Errores por minuto** | 60. | Aviso con esa cantidad de errores de la interfaz. `0` = sin aviso. |
| **Descartes por minuto** | Sin aviso. | Aviso con esa cantidad de paquetes descartados. |

Pulsa **Guardar alertas**. La configuración se guarda en `/var/lib/matipanel/traffic.json`.

> **Nota:** En redes con mucho broadcast, multicast o IPv6, el sistema descarta paquetes que no le interesan y eso es normal; por eso los descartes no avisan si no les pones un valor.

## Preguntas frecuentes

### ¿Por qué el historial empieza vacío?

Se guarda un punto cada 30 s desde que se instaló esta versión del panel. A los pocos minutos verás datos.

### ¿El análisis afecta el rendimiento?

El ancho de banda y las alertas no: solo leen contadores. **Quién consume** escucha unos segundos con un tope de 60 000 paquetes y solo las cabeceras; en enlaces muy cargados usa algo de CPU mientras dura la muestra.

### ¿Puedo ver el tráfico de un contenedor concreto?

Sí: elige la interfaz `br-…` de su red de Docker (o `docker0`) y filtra por la IP del contenedor en **Capturas** o mira la tabla **Equipos** en **Quién consume**.

## Ver también

- [Red](18-red.md): interfaces, rutas y conexiones abiertas.
- [Firewall](20-firewall.md): bloquear un equipo o puerto que encontraste aquí.
- [Dashboard](10-dashboard.md): tráfico total del servidor.
