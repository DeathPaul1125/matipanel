# Firewall

El firewall decide qué conexiones pueden llegar al servidor. Desde esta pantalla puedes instalar UFW, activarlo, abrir puertos de servicios comunes con un clic, crear reglas propias y cambiar las políticas por defecto. Lo encuentras en **Menú → Red y acceso → Firewall**.

## Qué firewall usa tu servidor

MatiPanel detecta automáticamente cuál está instalado y muestra arriba su estado con un indicador de color:

| Motor | Dónde es habitual | Qué permite el panel |
|---|---|---|
| **UFW** | Debian y Ubuntu (recomendado) | Gestión completa: activar, reglas, accesos rápidos y políticas |
| **firewalld** | RHEL, Rocky, AlmaLinux, Fedora | Agregar y quitar puertos y servicios de la zona por defecto |
| **nftables** | Debian 12 y 13 sin UFW | Ver las reglas, editar `/etc/nftables.conf` y aplicarlo |
| **iptables** | Sistemas antiguos | Solo ver las reglas |

> **Importante:** en una instalación nueva de Debian 13 **no hay un firewall activo** y el servidor acepta conexiones a todos sus puertos. Instala y activa UFW como se explica abajo.

El indicador de la parte superior significa:

- **Verde — Firewall activo:** filtrando conexiones.
- **Amarillo — Instalado pero INACTIVO / no hay firewall:** el servidor no está protegido.
- **Rojo — Error:** no se pudo consultar; el mensaje explica la causa.

## Instalar UFW

Si el servidor no tiene UFW, verás la tarjeta **Gestiona el firewall desde el panel con UFW**.

1. Pulsa **Instalar UFW** y confirma.
2. Espera unos segundos. Al terminar, la página se recarga y aparece la gestión de UFW.

UFW se instala **inactivo**: todavía no bloquea nada. Así puedes preparar tus reglas antes de encenderlo.

> **Nota:** en Debian 13, UFW funciona sobre nftables. No necesitas tocar `/etc/nftables.conf`.

## Activar el firewall sin perder el acceso

1. Revisa la tabla **Reglas**. Mientras UFW está inactivo verás la nota *Guardadas (se aplicarán al activar)*.
2. Agrega lo que necesites (por ejemplo HTTP y HTTPS si el servidor publica sitios web).
3. Pulsa **Activar firewall** y confirma.

Antes de activarlo, el panel **permite automáticamente**:

- El puerto de **SSH** (lo lee de `/etc/ssh/sshd_config`; si no lo encuentra, usa el 22).
- El puerto por el que estás usando **MatiPanel** (normalmente 8443).

Así no te quedas fuera del servidor al encender el firewall.

> **Importante:** si administras el servidor por otro puerto o servicio (por ejemplo un VPN o un panel distinto), permítelo **antes** de activar.

## Permitir un servicio común con un clic

En la tarjeta **Permitir rápido** hay botones para los servicios más habituales. Cada uno abre el puerto para conexiones entrantes desde cualquier origen:

| Botón | Puerto | Protocolo |
|---|---|---|
| SSH | 22 | TCP |
| HTTP (web) | 80 | TCP |
| HTTPS (web) | 443 | TCP |
| MatiPanel | el que estás usando | TCP |
| MySQL / MariaDB | 3306 | TCP |
| PostgreSQL | 5432 | TCP |
| FTP | 21 | TCP |
| Correo SMTP | 25 | TCP |
| DNS | 53 | TCP y UDP |
| Samba | 445 | TCP |
| Webmin | 10000 | TCP |

1. Pulsa el servicio.
2. Confirma. La regla aparece en la tabla con el nombre del servicio como comentario.

> **Consejo:** las bases de datos (3306, 5432) normalmente **no** deben abrirse a todo internet. Si un equipo externo necesita conectarse, crea una regla con **Origen** limitado a su IP (ver la siguiente sección).

## Agregar una regla propia

Usa la tarjeta **Agregar regla**:

| Campo | Qué poner |
|---|---|
| **Acción** | **Permitir (allow)** deja pasar; **Bloquear (deny)** descarta en silencio; **Rechazar (reject)** descarta avisando al cliente; **Limitar intentos (limit)** permite pero bloquea una IP que abre más de 6 conexiones en 30 segundos (ideal para SSH). |
| **Protocolo** | TCP, UDP o ambos. Para un **rango** de puertos debes elegir TCP o UDP. |
| **Puerto o rango** | Un puerto (`8080`) o un rango con dos puntos (`8000:8100`). |
| **Origen** | Vacío = cualquiera. O una IP (`192.168.1.20`) o una red (`192.168.1.0/24`). |
| **Comentario** | Opcional. Hasta 60 caracteres: letras, números, espacios y `. , : / ( ) - _`. |

Pulsa **Agregar regla**. Si algo no es válido, el panel te dice qué campo corregir.

### Ejemplos

- **Permitir el sitio web a todos:** Acción *Permitir*, Protocolo *TCP*, Puerto `80` (y otra regla para `443`).
- **Permitir MariaDB solo desde la red interna:** Acción *Permitir*, Protocolo *TCP*, Puerto `3306`, Origen `192.168.10.0/24`.
- **Proteger SSH contra ataques de fuerza bruta:** Acción *Limitar intentos*, Protocolo *TCP*, Puerto `22`.

## Eliminar una regla

1. En la tabla **Reglas**, pulsa **Eliminar** en la fila.
2. Confirma. La regla deja de aplicarse de inmediato.

Funciona tanto con el firewall activo como inactivo.

> **Importante:** no elimines la regla de SSH ni la de MatiPanel si el firewall está activo, o perderás el acceso remoto.

## Cambiar las políticas por defecto

La política por defecto decide qué pasa con el tráfico que **no coincide con ninguna regla**.

- **Entrante:** se recomienda **Bloquear (deny)**: solo entra lo que permitas con reglas.
- **Saliente:** se recomienda **Permitir (allow)**: el servidor puede salir a internet (actualizaciones, correo, etc.).

Cambia el valor en la lista y confirma.

> **Importante:** poner la política entrante en *Permitir* desactiva en la práctica la protección. Poner la saliente en *Bloquear* puede impedir actualizaciones, DNS y certificados SSL si no creas reglas de salida.

## Desactivar el firewall

Pulsa **Desactivar** y confirma. Las reglas se conservan y vuelven a aplicarse cuando lo actives otra vez.

## Gestionar firewalld (RHEL, Rocky, Fedora)

Si tu servidor usa firewalld, verás dos tarjetas para la **zona por defecto**:

- **Puertos:** escribe `80` o un rango `8000-8100`, elige TCP/UDP y pulsa **Agregar**. Para quitar uno, pulsa la **✕** junto al puerto.
- **Servicios:** escribe un servicio conocido por firewalld (`http`, `https`, `ssh`, `mysql`…; la lista sugiere los disponibles) y pulsa **Agregar**.

Los cambios se guardan de forma permanente y se aplican al momento.

## Trabajar con nftables directamente

Si el servidor solo tiene nftables (y prefieres no instalar UFW):

1. Pulsa **editar /etc/nftables.conf**; se abre el archivo en el editor del panel.
2. Guarda tus cambios.
3. Vuelve al Firewall y pulsa **Aplicar**. El panel primero **valida** el archivo (`nft -c -f`) y solo si es correcto reinicia el servicio `nftables`.

> **Importante:** reiniciar nftables borra las reglas cargadas en memoria y carga solo las del archivo. Si usas Docker, sus reglas se pierden hasta reiniciar Docker.

## Ver el estado detallado

Abre **Estado detallado (salida del firewall)** al final de la página para ver la salida completa del comando del firewall (`ufw status verbose`, `firewall-cmd --list-all`, etc.).

## Recuperar el acceso si te bloqueaste

Si activaste el firewall o borraste una regla y ya no puedes entrar por SSH ni al panel:

1. Entra por la **consola física** o la consola web de tu proveedor/hipervisor (VMware, Proxmox, etc.).
2. Inicia sesión como root y ejecuta:
   ```bash
   ufw allow 22/tcp
   ufw allow 8443/tcp
   ```
   o, para apagarlo por completo: `ufw disable`.

## Preguntas frecuentes

### Activé el firewall y mi sitio web dejó de verse

Falta permitir HTTP y HTTPS. Pulsa **HTTP (web)** y **HTTPS (web)** en **Permitir rápido**.

### Instalé UFW por la terminal, ¿tengo que reiniciar el panel?

No. Recarga la página del Firewall y el panel lo detecta.

### ¿Qué diferencia hay entre Bloquear y Rechazar?

**Bloquear** descarta la conexión sin responder (el cliente espera hasta agotar el tiempo). **Rechazar** responde que el puerto está cerrado (el cliente se entera al instante). Bloquear da menos información a un atacante.

### ¿Las reglas aplican también a IPv6?

Sí. UFW crea automáticamente la regla equivalente para IPv6; en la tabla la verás marcada con `(v6)`.

### Let's Encrypt no puede emitir el certificado

Let's Encrypt necesita llegar al puerto **80** (y 443 para el plugin de Apache/Nginx). Permítelos antes de pedir el certificado.

## Ver también

- [Red](18-red.md)
- [SSL / Let's Encrypt](22-ssl.md)
- [Instalación](02-instalacion.md)
- [Solución de problemas](90-solucion-de-problemas.md)
