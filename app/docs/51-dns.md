# Servidor DNS (BIND 9)

El DNS traduce nombres como `www.ejemplo.com` a direcciones IP. Con este módulo el servidor puede **publicar tus dominios** (zonas primarias), guardar una **copia** de un dominio que se administra en otro servidor (zonas secundarias), resolver **IP a nombre** (zonas inversas) y servir de **resolvedor** para tu red. Usa BIND 9 (`named`), el servidor DNS estándar de Debian. Lo encuentras en **Menú → Red y acceso → DNS**.

## Conceptos en un minuto

| Término | Qué es |
|---|---|
| **Zona** | El conjunto de registros de un dominio, por ejemplo todo lo que hay bajo `ejemplo.com`. Se guarda en un archivo de zona. |
| **Zona primaria** | La copia "original": se edita en este servidor. |
| **Zona secundaria** | Una copia de solo lectura que este servidor descarga de otro (el primario) y mantiene sincronizada. |
| **Registro** | Una línea de la zona: un **nombre**, un **tipo** (A, MX, TXT…) y un **valor**. |
| **SOA** | El primer registro de toda zona. Indica el servidor primario, el correo del responsable, el **serial** y los tiempos de sincronización. |
| **TTL** | Cuántos segundos pueden los demás servidores guardar en caché una respuesta antes de volver a preguntar. |
| **NS** | Los servidores de nombres que responden por la zona. |

## Instalar BIND

Si BIND no está instalado, el indicador superior dice **BIND 9 no está instalado** y verás la tarjeta **Ten tu propio servidor DNS con BIND 9**.

1. Pulsa **Instalar** y confirma.
2. Espera. El panel instala `bind9`, `bind9-utils` y `bind9-dnsutils` con apt.
3. Al terminar, la página muestra el estado del servicio y las pestañas del módulo.

Recién instalado, BIND funciona como **resolvedor** para el propio servidor y tu red local, pero **no publica ningún dominio** hasta que crees una zona.

> **Nota:** la instalación automática solo está disponible en Debian y Ubuntu. En otras distribuciones verás el comando equivalente para hacerlo a mano.

## El estado del servicio

El indicador de la parte superior significa:

- **Verde — Servidor DNS activo:** `named` está en marcha y escucha en el puerto 53.
- **Amarillo — named activo, pero no escucha en el puerto 53:** revisa la configuración `listen-on` o si otro programa ocupa el puerto (el texto lo indica).
- **Rojo — named está detenido:** nadie recibe respuestas de este servidor.

Debajo se ven la versión de BIND, cuántas zonas tienes, si arranca con el sistema y en qué direcciones escucha. Los botones hacen lo siguiente:

| Botón | Qué hace |
|---|---|
| **Probar config** | Ejecuta `named-checkconf` y muestra el resultado. No cambia nada. |
| **Recargar** | Valida la configuración y, si es correcta, recarga BIND sin cortar el servicio. |
| **Reiniciar** | Valida y reinicia `named`. Durante unos segundos no responde y se vacía la caché. |
| **Iniciar** / **Detener** | Arranca o detiene el servicio. Detener pide confirmación. |
| **Ver log** | Últimas 200 líneas del registro de `named` (`journalctl -u named`). |

Si la configuración tiene errores, **Recargar**, **Reiniciar** e **Iniciar** no se ejecutan y verás la salida de `named-checkconf` con el archivo y la línea del problema.

## Crear una zona primaria (publicar un dominio)

1. Abre la pestaña **Zonas** y pulsa **+ Zona primaria**.
2. Completa los campos (ver la tabla).
3. Pulsa **Crear zona**. Si todo sale bien, se abre la pestaña **Registros** con la zona nueva.

| Campo | Qué poner |
|---|---|
| **Dominio** | El dominio completo, por ejemplo `ejemplo.com` o `sistema.ejemplo.gob.gt`. Solo letras, números, guiones y puntos. |
| **Servidor de nombres primario** | El nombre de **este** servidor como DNS. Vacío = `ns1.<dominio>`. |
| **IP de este servidor** | La IP pública por la que se llega a este servidor. Se crea el registro A del servidor de nombres. Es obligatoria si el servidor de nombres está dentro del dominio (como `ns1.ejemplo.com`). |
| **Correo del administrador** | Vacío = `hostmaster@<dominio>`. Se guarda en el SOA. |
| **IP del sitio web** | Opcional. Si la indicas se crean registros A para el dominio (`@`) y para `www`. |

### Qué hace el panel al pulsar Crear zona

1. Crea el archivo `/etc/bind/zones/db.<dominio>` (propietario `root:bind`, permisos 644) con:
    - `$TTL 3600` (una hora).
    - Un **SOA** con serial `AAAAMMDD01`, refresh 3600, retry 900, expire 1209600 y minimum 300.
    - El registro **NS** y los registros **A** que pediste.
2. Valida el archivo con `named-checkzone`.
3. Agrega el bloque `zone` a `/etc/bind/named.conf.local` (antes guarda una copia `named.conf.local.matipanel.bak`). La zona se crea con `allow-transfer { none; }`: nadie puede copiarla hasta que autorices un secundario.
4. Valida toda la configuración con `named-checkconf`.
5. Recarga BIND (`rndc reload`).

Si cualquier validación falla, el panel **deshace todo** (borra el archivo de zona y restaura `named.conf.local`) y te muestra la salida del validador.

> **Importante:** para que internet use tu servidor, todavía falta **delegar** el dominio en tu registrador. Sigue la siguiente sección.

## Delegar tu dominio a este servidor

Crear la zona no basta: el registrador (la entidad donde registraste el dominio) debe decirle al mundo que **tu servidor** es quien responde por ese dominio.

1. Abre el **puerto 53 en TCP y UDP** en el firewall del servidor y en el firewall perimetral o NAT. En [Firewall](20-firewall.md) usa el acceso rápido **DNS**.
2. Comprueba desde la pestaña **Probar** que tu servidor responde: nombre `ejemplo.com`, tipo **SOA**, servidor `127.0.0.1`.
3. Entra al panel de tu registrador y busca la opción **Servidores de nombres** (DNS, *nameservers*).
4. Cambia los servidores de nombres por los tuyos, por ejemplo `ns1.ejemplo.com` (y `ns2.ejemplo.com` si tienes un secundario).
5. Si tus servidores de nombres están **dentro del mismo dominio** (`ns1.ejemplo.com` para `ejemplo.com`), debes crear también los **registros glue**. Los registradores los llaman *glue records*, *host records*, *registrar nameservers* o *registrar servidores DNS propios*: es asociar `ns1.ejemplo.com` con su IP **en el registrador**.
6. Espera la propagación. Los cambios en el registrador pueden tardar desde minutos hasta 48 horas.
7. Comprueba desde **Probar** con servidor `8.8.8.8`: si responde con tus datos, la delegación funciona.

> **¿Por qué hace falta el glue?** Para encontrar `ns1.ejemplo.com` habría que preguntarle a… `ns1.ejemplo.com`. El registro glue rompe ese círculo: el registro superior (`.com`, `.gt`…) entrega directamente la IP de tu servidor de nombres.

> **Nota:** la mayoría de registradores exige **al menos dos** servidores de nombres, idealmente en redes distintas. Puedes usar un segundo servidor con MatiPanel como [secundario](#zonas-secundarias) o el DNS secundario que ofrezca tu proveedor.

## Administrar registros

1. Abre la pestaña **Registros** (o pulsa **Registros** en la fila de la zona).
2. Elige la zona en el selector **Zona**. Arriba ves los datos del SOA; abajo, la tabla de registros.
3. Para buscar, escribe en **Buscar nombre o valor…** o filtra por tipo.

### Agregar un registro

1. Elige el tipo en la lista junto a **+ Agregar registro** (A, AAAA, CNAME, MX, TXT, NS, SRV, CAA o PTR).
2. Pulsa **+ Agregar registro**.
3. Completa el formulario y pulsa **Agregar**.

### Editar o eliminar

Usa **Editar** o **Eliminar** en la fila. Los registros de tipos que el panel no sabe editar (por ejemplo DNSKEY o TLSA) solo se pueden eliminar; para cambiarlos usa **Editar archivo**.

### Qué pasa al guardar

En cada alta, cambio o baja el panel:

1. Guarda una copia del archivo de zona como `<archivo>.matipanel.bak`.
2. Sube el **serial** del SOA.
3. Reescribe la zona: la cabecera (comentarios iniciales, `$TTL` y SOA) se conserva tal cual; los registros se escriben en un formato ordenado y uniforme, con los nombres de destino completos y terminados en punto.
4. Valida con `named-checkzone`. Si falla, **restaura el archivo anterior** y te muestra el error.
5. Recarga solo esa zona (`rndc reload <zona>`).

> **Importante:** si dos personas editan la misma zona a la vez, el panel lo detecta y muestra **La zona cambió desde que la abriste; recarga la lista**. Pulsa **Refrescar** y repite el cambio.

### Cómo escribir los nombres

| En el campo **Nombre** escribes | Se convierte en |
|---|---|
| vacío o `@` | el dominio mismo (`ejemplo.com.`) |
| `www` | `www.ejemplo.com.` |
| `mail.interno` | `mail.interno.ejemplo.com.` |
| `*.dev` | comodín: cualquier nombre bajo `dev.ejemplo.com.` |
| `www.ejemplo.com` | se reconoce como nombre completo de la zona: `www.ejemplo.com.` |

En los campos de **destino** (CNAME, MX, NS, SRV, PTR):

| Escribes | Significa |
|---|---|
| `mail` (una sola palabra) | `mail.ejemplo.com.`, un nombre de esta zona |
| `mail.otro.com` (con algún punto) | el nombre completo `mail.otro.com.` |
| `@` | el dominio mismo |

> **Nota:** el panel no acepta una IP donde va un nombre. Un MX, CNAME o NS **siempre** apunta a un nombre, que a su vez tiene su registro A.

### Tipos de registro

| Tipo | Para qué sirve | Ejemplo de nombre | Ejemplo de valor |
|---|---|---|---|
| **A** | Nombre → IPv4 | `www` | `203.0.113.10` |
| **AAAA** | Nombre → IPv6 | `www` | `2001:db8::10` |
| **CNAME** | Alias de otro nombre. No puede convivir con otros registros del mismo nombre ni usarse en `@`. | `portal` | `www` |
| **MX** | Servidor que recibe el correo del dominio. Menor prioridad = se prueba primero. | `@` | prioridad `10`, servidor `mail` |
| **TXT** | Texto: SPF, DKIM, DMARC, verificaciones de Google/Microsoft. Escríbelo **sin comillas**; si pasa de 255 caracteres se divide solo. | `@` | `v=spf1 mx -all` |
| **NS** | Servidores de nombres de la zona, o delegación de un subdominio a otro servidor. | `@` | `ns2.ejemplo.com` |
| **SRV** | Ubicación de un servicio (VoIP, Autodiscover, XMPP). | `_sip._tcp` | prioridad `10`, peso `5`, puerto `5060`, servidor `sip` |
| **CAA** | Qué autoridades pueden emitir certificados SSL para el dominio. | `@` | `0 issue letsencrypt.org` |
| **PTR** | IP → nombre. Solo en zonas inversas. | `10` (o la IP completa) | `servidor.ejemplo.com` |

### Ejemplo: registros para el correo

Para un servidor de correo en `mail.ejemplo.com` con IP `203.0.113.25`:

1. **A** — Nombre `mail`, dirección `203.0.113.25`.
2. **MX** — Nombre `@`, prioridad `10`, servidor `mail`.
3. **TXT (SPF)** — Nombre `@`, texto `v=spf1 mx -all`.
4. **TXT (DMARC)** — Nombre `_dmarc`, texto `v=DMARC1; p=quarantine; rua=mailto:postmaster@ejemplo.com`.
5. **TXT (DKIM)** — Nombre `<selector>._domainkey` (por ejemplo `default._domainkey`) y la clave que te dé el módulo de [Correo](50-correo.md). Pégala tal cual, sin comillas.
6. Pide a tu proveedor de internet un **PTR** de `203.0.113.25` a `mail.ejemplo.com`: muchos servidores rechazan correo sin él.

## El serial y el TTL

### Serial del SOA

Los servidores secundarios comparan el **serial** para saber si la zona cambió: solo copian la zona si el serial del primario es **mayor** que el suyo. El panel usa el formato **AAAAMMDDnn** (año, mes, día y un contador de 2 cifras):

- El primer cambio del día pone `AAAAMMDD00` (o `01` en una zona nueva).
- Cada cambio del mismo día suma 1: `2026102601`, `2026102602`…
- Si el serial ya es mayor (por ejemplo más de 99 cambios en un día), simplemente suma 1.
- Si tu zona usaba un contador simple (como el `2` de los ejemplos de Debian), el primer cambio lo convierte al formato de fecha. Eso es seguro: el número siempre crece.

> **Importante:** si editas el archivo a mano con **Editar archivo**, **sube tú el serial** antes de guardar. Si no, los secundarios no verán el cambio. Después pulsa **Recargar**.

### TTL

El **TTL** dice cuánto tiempo (en segundos) pueden los demás guardar una respuesta en caché. El panel usa `$TTL 3600` (1 hora) por defecto; en cada registro puedes indicar otro.

- **Antes de cambiar una IP** (por ejemplo al mudar el sitio web), baja el TTL de ese registro a `300` con un día de antelación. Así el cambio se nota en 5 minutos en lugar de una hora.
- El campo **Minimum** del SOA es el tiempo que se recuerda que un nombre **no existe**. El panel usa 300 segundos.

## Editar el SOA

En la pestaña **Registros**, pulsa **Editar SOA** para cambiar el servidor primario, el correo del responsable y los tiempos:

| Campo | Significado | Valor del panel |
|---|---|---|
| **Refresh** | Cada cuánto revisan los secundarios si hay cambios. | 3600 (1 hora) |
| **Retry** | Cada cuánto reintentan si el primario no responde. | 900 (15 minutos) |
| **Expire** | Tras cuánto tiempo sin contactar al primario dejan de responder por la zona. | 1209600 (14 días) |
| **Minimum** | Caché de respuestas negativas ("ese nombre no existe"). | 300 (5 minutos) |

El serial se sube solo al guardar.

## Zonas inversas

Una zona inversa (`in-addr.arpa`) responde "¿qué nombre tiene la IP 192.168.1.10?".

1. En **Zonas**, pulsa **+ Inversa**.
2. **Red IPv4:** `192.168.1.0/24` (también /16 y /8).
3. **Servidor de nombres:** el nombre completo de este servidor, por ejemplo `ns1.ejemplo.com`.
4. Deja marcado **Crear PTR para los registros A de mis zonas que estén en esta red** para que el panel genere los PTR a partir de tus registros A.
5. Pulsa **Crear zona inversa**. La zona se llama, por ejemplo, `1.168.192.in-addr.arpa`.

Para agregar más PTR, abre la zona en **Registros**, elige **PTR** y escribe como nombre el último número de la IP (`10`) o la IP completa (`192.168.1.10`).

> **Nota:** en **redes privadas** la zona inversa funciona de inmediato para los equipos que usan este DNS. Para **IP públicas** el PTR lo controla quien te asignó las IP (tu proveedor de internet): pídeles que lo creen o que te deleguen la zona.

## Zonas secundarias

Un secundario guarda una copia de la zona de otro servidor y responde aunque el primario se caiga.

### En el servidor primario

1. En la fila de la zona, pulsa **Secundarios**.
2. Escribe las IP de los servidores secundarios, separadas por coma.
3. Pulsa **Guardar**. El panel configura `allow-transfer` (quién puede copiar la zona) y `also-notify` (a quién avisar de cada cambio).
4. Agrega también un registro **NS** con el nombre del secundario en la zona.

Dejar el campo vacío vuelve a `allow-transfer { none; }`: nadie puede copiar la zona.

### En el servidor secundario

1. Pulsa **+ Secundaria**.
2. **Dominio:** el mismo dominio del primario.
3. **IP de los servidores primarios:** la IP del primario (puedes poner varias).
4. Pulsa **Crear secundaria**. La copia se guarda en `/var/cache/bind/db.<dominio>`.
5. Pulsa **Estado** en la fila para ver si la transferencia funcionó (serial cargado, próxima actualización).

> **Nota:** las zonas secundarias son de solo lectura: sus registros se editan en el primario. Recuerda abrir el puerto **53 TCP** entre ambos servidores: las transferencias usan TCP.

## Eliminar una zona

1. Pulsa **Eliminar** en la fila de la zona.
2. Marca **Borrar también el archivo de zona del disco** si ya no lo necesitas.
3. Escribe el nombre de la zona para confirmar y pulsa **Eliminar zona**.

El panel quita el bloque de `named.conf.local` (guardando `named.conf.local.matipanel.bak`), valida y recarga. Si marcaste la casilla, borra el archivo de zona y su diario `.jnl`.

> **Importante:** si el dominio sigue delegado a este servidor en el registrador, dejará de resolver. Cambia antes los servidores de nombres en el registrador.

## Reenviadores y opciones

La pestaña **Opciones** muestra la configuración efectiva de BIND (`named-checkconf -p`): recursión, quién puede consultar, escucha IPv4/IPv6, DNSSEC, etc. Abre **Bloque options completo** para verlo entero.

### Configurar reenviadores (forwarders)

Los reenviadores son servidores DNS a los que BIND pregunta los nombres que no conoce, en lugar de recorrer el árbol DNS desde los servidores raíz. Útiles si tu red solo permite DNS hacia ciertos servidores o si quieres usar los de tu proveedor.

1. Escribe las IP en **Direcciones IP**, una por línea (máximo 8).
2. Elige el **Modo**:
    - **first:** usa los reenviadores y, si fallan, resuelve por su cuenta.
    - **only:** solo usa los reenviadores.
3. Pulsa **Guardar y aplicar**.

El panel modifica `/etc/bind/named.conf.options` (con copia `.matipanel.bak`), valida con `named-checkconf` y recarga. Si la validación falla, restaura el archivo. Deja la lista vacía para quitar los reenviadores.

Para otras opciones (por ejemplo `recursion no;` en un servidor solo autoritativo), pulsa **Editar named.conf.options**, guarda y luego usa **Probar config** y **Recargar**.

> **Nota:** la configuración de Debian solo permite usar el servidor como **resolvedor** a la red local (`allow-recursion` por defecto: `localnets; localhost`). Internet puede consultar tus zonas, pero no usar tu servidor para resolver otros dominios. No lo abras a todos: los resolvedores abiertos se usan en ataques de amplificación.

## Probar con dig

La pestaña **Probar** ejecuta `dig` con los datos que indiques:

1. **Nombre:** por ejemplo `www.ejemplo.com`. Para un PTR escribe la IP.
2. **Tipo:** A, AAAA, MX, TXT, NS, SOA, etc.
3. **Servidor:** `127.0.0.1` pregunta a este mismo BIND. Pulsa **Usar 8.8.8.8** para ver lo que ve internet.
4. **Formato:** **Corto** muestra solo los valores; **Completo** muestra la respuesta entera (útil para ver el TTL, el estado `NOERROR`/`NXDOMAIN` y si la respuesta es autoritativa, `aa`).
5. Pulsa **Consultar** (o Enter).

Si con `127.0.0.1` ves el dato correcto pero con `8.8.8.8` no, el problema está en la delegación o en la caché: revisa el registrador y espera a que venza el TTL.

## Preguntas frecuentes

### Creé el registro pero en internet todavía sale la IP anterior

Es la caché. Los demás servidores guardan la respuesta durante el TTL del registro (por defecto 1 hora). Para la próxima vez, baja el TTL antes del cambio.

### Al guardar sale "named-checkzone rechazó el cambio"

El archivo se restauró y nada cambió. Lee la salida: suele decir el nombre y el motivo. Los casos más comunes son un CNAME junto a otros registros, un NS dentro de la zona sin su registro A, o un nombre con caracteres no permitidos (como `_` en un registro A).

### La zona aparece como "Solo lectura"

El archivo usa algo que el panel no reescribe con seguridad (`$INCLUDE`, `$GENERATE`, una línea no reconocida) o la zona es **dinámica** (`allow-update`/`update-policy`, se modifica con `nsupdate`). Edítala con **Editar archivo** y recuerda subir el serial.

### ¿Por qué no veo mis zonas que están en otro archivo?

El panel administra solo las zonas de `/etc/bind/named.conf.local`. Las que estén en otros archivos incluidos, o dentro de bloques `view`, siguen funcionando pero no se muestran.

### ¿Se pierden mis comentarios al editar desde el panel?

Los comentarios de la cabecera, los de cada línea y los comentarios de sección se conservan. Sí cambia el formato: los registros se reescriben alineados, con nombres completos y sin directivas `$TTL` intermedias (cuando un registro dependía de una, su TTL queda escrito en la misma línea).

### ¿Puedo volver a la versión anterior de un archivo?

Sí. Antes de cada cambio queda una copia `<archivo>.matipanel.bak` junto al original (solo la última). Restáurala desde [Archivos](26-archivos.md), sube el serial y pulsa **Recargar**.

### named está activo pero no responde desde fuera

Revisa el [Firewall](20-firewall.md) (puerto 53 TCP y UDP) y el firewall del proveedor. Mira el estado: debe escuchar en una IP pública o en `0.0.0.0`, no solo en `127.0.0.1`.

## Ver también

- [Firewall](20-firewall.md)
- [Correo](50-correo.md)
- [Servidor web](21-servidor-web.md)
- [SSL / Let's Encrypt](22-ssl.md)
- [Archivos](26-archivos.md)
- [Servicios](13-servicios.md)
- [Solución de problemas](90-solucion-de-problemas.md)
