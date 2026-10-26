# SSL / Let's Encrypt

Este módulo obtiene, lista, renueva y elimina certificados HTTPS gratuitos de Let's Encrypt usando `certbot`. Lo encuentras en **Menú → Servidores → SSL / Let's Encrypt**.

## Requisitos antes de pedir un certificado

Let's Encrypt comprueba desde Internet que el dominio es tuyo. Para eso necesitas:

1. **certbot instalado.** Si falta, la página muestra **certbot no está instalado** con el comando: `apt install certbot python3-certbot-apache python3-certbot-nginx`. Puedes instalarlo desde [Paquetes](27-paquetes.md).
2. **DNS público.** Cada dominio del certificado debe tener un registro **A** (y **AAAA** si usas IPv6) que apunte a la IP pública de este servidor.
3. **Puerto 80 abierto desde Internet.** La validación se hace por HTTP. Ábrelo en el [Firewall](20-firewall.md) del servidor y en el firewall perimetral o NAT de la institución.
4. **Puerto 443 abierto** para que los usuarios lleguen al sitio por HTTPS.
5. **Un sitio que responda a ese dominio** en [Servidor web](21-servidor-web.md), con el mismo nombre en **Dominio** o **Alias**.
6. **Un email válido** para los avisos de caducidad.

> **Importante:** No se pueden emitir certificados para nombres que solo existen en la red interna (por ejemplo `intranet.local` o un dominio sin DNS público). Let's Encrypt debe poder llegar al servidor desde Internet.

> **Nota:** Let's Encrypt limita los intentos fallidos. Si fallas varias veces seguidas con el mismo dominio, espera un rato antes de reintentar y revisa primero el DNS y los puertos.

## Obtener un certificado

1. Pulsa **+ Obtener certificado**. Se abre **Obtener certificado**.
2. Completa los campos (ver la tabla).
3. Pulsa **Obtener**. El botón cambia a **Obteniendo…** mientras trabaja. Puede tardar hasta 5 minutos: no cierres la página.
4. Si sale bien, verás la ventana **Certificado obtenido** con la salida de certbot. El certificado aparece en la tabla.
5. Si falla, verás **Error: certbot falló** con la salida completa. Lee el final: allí certbot explica el motivo.

| Campo | Qué poner |
|---|---|
| **Dominios** | Uno o varios dominios separados por coma, por ejemplo `sistema.ejemplo.gob.gt, www.sistema.ejemplo.gob.gt`. El primero da nombre al certificado. Máximo 100. |
| **Email** | Correo que recibirá los avisos de caducidad. |
| **Método** | **Apache (instala en el vhost)**, **Nginx (instala en el vhost)** o **Webroot (solo obtener)**. |
| **Ruta webroot** | Solo con **Webroot**. Carpeta pública del sitio, por ejemplo `/var/www/sistema.ejemplo.gob.gt/public_html`. Debe existir. |
| **Redirigir HTTP a HTTPS** | Solo con Apache o Nginx. Marcado por defecto. Hace que `http://` salte a `https://`. |

> **Importante:** El método preseleccionado es **Apache**. Si tu servidor usa Nginx, cámbialo a **Nginx (instala en el vhost)** antes de pulsar **Obtener**.

## Elegir el método adecuado

| Método | Qué hace | Cuándo usarlo |
|---|---|---|
| **Apache (instala en el vhost)** | Obtiene el certificado y configura Apache. Crea el sitio `<dominio>-le-ssl.conf` en el puerto 443 y, si marcaste la redirección, la agrega al sitio del puerto 80. | Sitios servidos por Apache. Es la opción más simple. |
| **Nginx (instala en el vhost)** | Obtiene el certificado y modifica el archivo del sitio para escuchar en 443. | Sitios servidos por Nginx. |
| **Webroot (solo obtener)** | Solo obtiene el certificado. No toca la configuración del servidor web. | Configuraciones hechas a mano, proxys o servicios que no son Apache/Nginx. Debes configurar tú las rutas del certificado. |

Con **Webroot**, las rutas del certificado aparecen en la columna **Rutas** de la tabla (normalmente `/etc/letsencrypt/live/<nombre>/fullchain.pem` y `privkey.pem`).

## Revisar los certificados instalados

La tarjeta **Certificados** lista todo lo que certbot administra. Pulsa **Refrescar** para actualizarla.

| Columna | Significado |
|---|---|
| **Nombre** | Nombre del certificado (el primer dominio). |
| **Dominios** | Todos los nombres que cubre. |
| **Expira** | Fecha de caducidad y estado que informa certbot. |
| **Días** | Días que faltan. Verde: 30 o más. Ámbar: menos de 30. Rojo: menos de 7. `?` si no se pudo calcular. |
| **Rutas** | Ruta del certificado y de la clave privada. |
| **Acciones** | **Eliminar**. |

Los certificados de Let's Encrypt duran 90 días. Si ves uno en ámbar o rojo, revisa la renovación automática.

## Comprobar la renovación automática

Arriba de la página verás **Renovación automática:** con el estado del temporizador de certbot (`certbot.timer`, o `snap.certbot.renew.timer` si certbot se instaló con snap). Al lado aparece la próxima ejecución.

- `active` (verde): todo bien. El temporizador corre dos veces al día y renueva los certificados que vencen en menos de 30 días.
- Otro estado (ámbar): la renovación automática no está funcionando.

Para activarlo, abre la [Terminal](29-terminal.md) y ejecuta:

```
sudo systemctl enable --now certbot.timer
```

> **Nota:** Con los métodos Apache y Nginx, certbot recarga el servidor web después de renovar. Con **Webroot** no: el programa que use el certificado debe recargarse por otra vía para tomar el certificado nuevo.

## Probar la renovación sin cambiar nada

1. Pulsa **Renovar (simulación)**.
2. Espera. Se ejecuta `certbot renew --dry-run`.
3. Si ves **Simulación de renovación: OK**, la renovación real funcionará.

La simulación no pide confirmación y no modifica los certificados. Úsala después de cambiar DNS, firewall o la configuración de un sitio.

## Renovar los certificados ahora

1. Pulsa **Renovar todos**.
2. Confirma en **Renovar certificados**.
3. Espera el resultado (**Renovación completada** o el error de certbot).

certbot solo renueva los certificados que vencen en menos de 30 días. Los demás se omiten. No hay botón para renovar un certificado concreto.

## Eliminar un certificado

1. Antes de borrar, quita el uso del certificado en el servidor web:
    - **Apache:** deshabilita o elimina el sitio `<dominio>-le-ssl.conf` en [Servidor web](21-servidor-web.md).
    - **Nginx:** edita el sitio y quita las líneas `ssl_certificate` y `listen 443`.
2. Pulsa **Eliminar** en la fila del certificado.
3. Confirma en **Eliminar certificado**.

> **Importante:** Si borras un certificado que un sitio todavía usa, la prueba de configuración fallará. Apache o Nginx no podrán recargar ni reiniciar hasta que corrijas el sitio.

## Preguntas frecuentes

### certbot falla con "Timeout", "Connection refused" o "unauthorized". ¿Qué reviso?

Casi siempre es red o DNS. Comprueba que el dominio resuelve a la IP pública del servidor, que el puerto 80 está abierto desde Internet (también en el firewall perimetral) y que el sitio responde por `http://` con ese nombre.

### ¿Puedo usar Let's Encrypt para un sistema que solo se ve dentro de la institución?

No con este módulo. La validación necesita llegar al servidor desde Internet por el puerto 80. Para sistemas internos usa un certificado de tu autoridad interna y configúralo a mano en el sitio.

### ¿Cada cuánto se renuevan los certificados?

El temporizador de certbot corre dos veces al día. Renueva cada certificado cuando le quedan menos de 30 días. No tienes que hacer nada si **Renovación automática** está en `active`.

### ¿Este módulo cambia el certificado del propio MatiPanel?

No. El panel usa su propio certificado autofirmado en `/etc/matipanel/`. Este módulo solo administra certificados de sitios web.

## Ver también

- [Servidor web](21-servidor-web.md)
- [Firewall](20-firewall.md)
- [Paquetes](27-paquetes.md)
- [Terminal](29-terminal.md)
- [Solución de problemas](90-solucion-de-problemas.md)
