# Red (Interfaces)

El módulo Interfaces muestra las tarjetas de red del servidor con sus direcciones IP y tráfico, y las conexiones de red activas con el programa que las usa. Es de solo consulta. Lo encuentras en **Menú → Red y acceso → Interfaces**.

## Ver las interfaces de red

La tarjeta **Interfaces** tiene una fila por cada interfaz:

| Columna | Significado |
|---|---|
| **Iface** | Nombre de la interfaz: `lo`, `eth0`, `ens18`, `enp3s0`, `docker0`… |
| **IPv4** | Primera dirección IPv4 asignada, o `—` si no tiene |
| **IPv6** | Primera dirección IPv6 asignada, o `—` |
| **MAC** | Dirección física de la tarjeta |
| **MTU** | Tamaño máximo de paquete (normalmente `1500`) |
| **RX** | Total de datos recibidos desde el último arranque |
| **TX** | Total de datos enviados desde el último arranque |
| **Estado** | `UP` (activa) o `DOWN` (desactivada o sin cable) |

### Interfaces que verás con frecuencia

| Interfaz | Qué es |
|---|---|
| `lo` | Interfaz interna (loopback), siempre `127.0.0.1`. Es normal |
| `eth0`, `ens18`, `enp3s0` | Tarjetas de red reales o virtuales. Una de ellas es la principal |
| `docker0`, `br-…`, `veth…` | Redes internas creadas por [Docker](25-docker.md) |
| `wg0`, `tun0` | Túneles VPN |

> **Nota:** Si una interfaz tiene varias direcciones IPv4 o IPv6, solo se muestra la primera. Para verlas todas, usa `ip addr` en la [Terminal](29-terminal.md).

> **Nota:** La página no se actualiza sola ni tiene botón de refrescar. Recárgala (F5) para ver datos nuevos. Para ver el tráfico en tiempo real, usa la gráfica **Red** del [Dashboard](10-dashboard.md).

## Ver las conexiones activas y los puertos abiertos

La tarjeta **Conexiones activas (LISTEN/ESTABLISHED)** lista:

- **LISTEN**: puertos en los que un programa espera conexiones (por ejemplo, el servidor web en el `80` y el `443`).
- **ESTABLISHED**: conexiones abiertas en este momento (por ejemplo, una sesión SSH o un visitante del sitio).

| Columna | Significado |
|---|---|
| **Proto** | Protocolo (`tcp`) |
| **Local** | Dirección y puerto del servidor |
| **Remoto** | Dirección y puerto del otro extremo, o `—` si está en espera |
| **Estado** | `LISTEN` o `ESTABLISHED` |
| **PID** | Proceso que tiene la conexión |
| **Proceso** | Nombre del programa |

> **Importante:** Esta tabla solo muestra conexiones **TCP**. Los servicios que usan **UDP** (por ejemplo, DNS en el puerto 53, NTP en el 123 o WireGuard) no aparecen aunque estén escuchando. Para verlos usa `ss -ulpn` en la [Terminal](29-terminal.md).

### Cómo leer la columna Local

| Valor | Significado |
|---|---|
| `0.0.0.0:80` | Escucha en el puerto 80 de **todas** las direcciones IPv4. Accesible desde la red (si el firewall lo permite) |
| `:::80` | Lo mismo, en todas las direcciones IPv6 |
| `127.0.0.1:3306` | Solo accesible desde el propio servidor. Es lo recomendado para bases de datos |
| `192.168.1.10:22` | Solo en esa dirección concreta |

## Comprobar si un servicio está escuchando

Úsalo cuando un servicio aparece `active` pero no responde.

1. Abre **Menú → Red y acceso → Interfaces**.
2. En **Conexiones activas**, busca el puerto esperado en la columna **Local** con estado `LISTEN` (usa Ctrl+F del navegador).
3. Revisa la columna **Proceso**:
   - Si aparece el programa correcto, el servicio escucha bien. El problema suele estar en el [Firewall](20-firewall.md).
   - Si el puerto lo tiene otro programa, hay un conflicto: dos servicios quieren el mismo puerto.
   - Si el puerto no aparece, el servicio no está escuchando. Revisa su estado y log en [Servicios](13-servicios.md).
4. Revisa la dirección: si dice `127.0.0.1`, solo acepta conexiones locales y no será accesible desde otros equipos.

> **Consejo:** Revisa esta tabla de vez en cuando. Cualquier puerto en `LISTEN` sobre `0.0.0.0` que no reconozcas es un servicio expuesto que conviene cerrar o proteger con el firewall.

## Cambiar la dirección IP u otra configuración de red

Este módulo no permite modificar la red. En Debian, la configuración suele estar en `/etc/network/interfaces`:

1. Ve a **Menú → Sistema → Sistema**, pestaña **Configuración**.
2. Haz clic en **editar** en la tarjeta `/etc/network/interfaces`.
3. Edita y guarda el archivo.
4. Aplica los cambios reiniciando el servicio `networking` en [Servicios](13-servicios.md), o reiniciando el servidor.

> **Importante:** Un error en la configuración de red puede dejar el servidor inaccesible por la red, incluido el panel. Hazlo solo si tienes acceso a la consola física o del hipervisor. Antes, copia el archivo original como respaldo.

> **Nota:** Si tu servidor usa NetworkManager o systemd-networkd en lugar de `ifupdown`, `/etc/network/interfaces` no tendrá efecto. Pregunta a quien instaló el servidor o revisa en [Servicios](13-servicios.md) cuál de ellos está activo.

Para los servidores DNS, edita `/etc/resolv.conf` desde la misma pestaña (ver [Sistema](11-sistema.md)).

## Preguntas frecuentes

### ¿Por qué no veo mi servidor DNS o mi VPN WireGuard en las conexiones?

Usan UDP, y la tabla solo muestra conexiones TCP. Compruébalos desde la [Terminal](29-terminal.md) con `ss -ulpn`.

### ¿Los valores de RX y TX son por día?

No. Son el total acumulado desde el último arranque del servidor. Se reinician a cero al reiniciar.

### Una interfaz aparece DOWN. ¿Es un problema?

Depende. Si es la interfaz principal, el servidor no tiene red por ella: revisa el cable o la configuración de la máquina virtual. Si es una interfaz de Docker sin contenedores o una tarjeta que no usas, es normal.

### La columna Proceso dice "—". ¿Por qué?

Algunas conexiones pertenecen al kernel o a procesos que terminaron mientras se generaba la lista. Recarga la página para ver datos actualizados.

## Ver también

- [Firewall](20-firewall.md)
- [Servicios](13-servicios.md)
- [Sistema](11-sistema.md)
- [Dashboard](10-dashboard.md)
- [Docker](25-docker.md)
- [Terminal](29-terminal.md)
