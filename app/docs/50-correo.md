# Correo (Postfix y Dovecot)

Este módulo administra el correo del servidor: **Postfix** (el servidor SMTP, que envía y recibe correo) y **Dovecot** (el servidor IMAP/POP3, que permite leer los buzones desde un programa de correo). Desde aquí puedes instalarlos, cambiar la configuración principal, revisar la cola de mensajes, editar los alias, ver el registro y enviar un correo de prueba. Lo encuentras en **Menú → Servidores → Correo**.

## Qué necesitas saber antes de empezar

- **Solo enviar avisos** (notificaciones del sistema, formularios de tus sitios web): basta con **Postfix**.
- **Recibir correo para tus usuarios** y que lo lean desde Thunderbird, Outlook o el celular: necesitas **Postfix** y **Dovecot**.
- Un servidor de correo **público** (que recibe correo de internet o envía a Gmail, Outlook, etc.) necesita registros DNS correctos: **MX**, **SPF** y **PTR** (DNS inverso). Sin ellos, tus mensajes no llegarán o terminarán en spam. Ver [DNS](51-dns.md).

> **Importante:** muchos proveedores de internet y nubes bloquean la salida por el puerto **25**. Si los correos se quedan en la cola con `Connection timed out`, pide a tu proveedor que lo habilite o usa un **relay** (ver más abajo).

## Ver el estado del correo

En la parte superior verás un indicador de color y el estado de los dos servicios:

- **Verde — Correo funcionando:** Postfix (y Dovecot, si está instalado) están activos.
- **Amarillo:** falta instalar algo o un servicio está detenido.
- **Rojo:** un servicio terminó con error (`failed`). Revisa la pestaña **Registro**.

Debajo aparece la versión de cada servicio, si está activo y si arranca con el sistema. Las etiquetas de puertos indican cuáles están **en escucha** (verde) o cerrados (gris):

| Puerto | Uso | Servicio |
|---|---|---|
| 25 | SMTP: recepción de correo entre servidores | Postfix |
| 465 | SMTPS: envío desde clientes con TLS directo | Postfix (hay que habilitarlo en `master.cf`) |
| 587 | Submission: envío desde clientes con STARTTLS | Postfix (hay que habilitarlo en `master.cf`) |
| 110 / 995 | POP3 / POP3 con TLS | Dovecot |
| 143 / 993 | IMAP / IMAP con TLS | Dovecot |

Pulsa **Refrescar** para volver a consultar.

## Instalar Postfix y Dovecot

Si falta alguno, verás la tarjeta **Postfix no está instalado** o **Dovecot no está instalado**.

1. Pulsa **Instalar Postfix** y confirma.
2. Espera. Puede tardar unos minutos. Al terminar verás la salida de la instalación.
3. Si también necesitas buzones, pulsa **Instalar Dovecot** y confirma.

Qué hace el panel:

- **Postfix:** lo preconfigura como **Sitio de Internet** y usa como nombre de correo el nombre completo del servidor (`hostname -f`), que se muestra en la tarjeta. Así la instalación no hace preguntas.
- **Dovecot:** instala `dovecot-imapd` y `dovecot-pop3d`.

> **Nota:** la instalación automática solo funciona en Debian y Ubuntu. En otras distribuciones la tarjeta muestra el comando para instalarlo a mano.

> **Importante:** si el servidor tenía otro servidor de correo (por ejemplo `exim4`), apt lo reemplaza por Postfix.

Después de instalar, abre los puertos que necesites en el [Firewall](20-firewall.md):

- Para **recibir** correo de internet: **25**.
- Para que los usuarios **envíen** desde su programa de correo: **587** (y **465** si lo habilitas).
- Para que los usuarios **lean** su correo: **993** (IMAP) y/o **995** (POP3). Evita abrir 143 y 110 a internet: no van cifrados.

## Cambiar la configuración de Postfix

Abre la pestaña **Postfix**. El formulario muestra los parámetros principales de `/etc/postfix/main.cf`:

| Campo | Qué poner | Ejemplo |
|---|---|---|
| **Nombre del servidor (myhostname)** | Nombre completo con el que el servidor se presenta. Debe coincidir con su registro DNS y su PTR. | `mail.ejemplo.gob.gt` |
| **Dominio (mydomain)** | Tu dominio de correo. | `ejemplo.gob.gt` |
| **Origen (myorigin)** | Dominio que se agrega a los correos de usuarios locales: `$myhostname`, `$mydomain`, `/etc/mailname` o un dominio. | `$mydomain` |
| **Destinos locales (mydestination)** | Dominios cuyo correo se entrega en este servidor, separados por coma. | `$myhostname, $mydomain, localhost` |
| **Relay de salida (relayhost)** | Vacío para entregar directo. O el servidor por el que debe salir todo el correo. | `[smtp.ejemplo.gob.gt]:587` |
| **Interfaces de escucha (inet_interfaces)** | `all` para recibir de internet; `loopback-only` para que solo envíe (no recibe de fuera). | `all` |
| **Protocolos IP (inet_protocols)** | `all`, `ipv4` o `ipv6`. Usa `ipv4` si el servidor no tiene IPv6 configurado. | `ipv4` |
| **Tamaño máximo de mensaje** | En bytes, con adjuntos. `0` = sin límite. | `26214400` (25 MB) |
| **Tamaño máximo del buzón** | En bytes, solo para buzones mbox. `0` = sin límite. Debe ser `0` o mayor que el tamaño de mensaje. | `0` |
| **Buzón en el home (home_mailbox)** | Vacío = `/var/mail/usuario`. `Maildir/` = carpeta Maildir en el home del usuario. | `Maildir/` |
| **Certificado TLS** | Ruta del certificado. El archivo debe existir. | `/etc/letsencrypt/live/mail.ejemplo.gob.gt/fullchain.pem` |
| **Llave privada TLS** | Ruta de la llave privada. El archivo debe existir. | `/etc/letsencrypt/live/mail.ejemplo.gob.gt/privkey.pem` |

Para guardar:

1. Cambia los campos que necesites.
2. Pulsa **Guardar y aplicar**.
3. Si un campo no es válido, aparece un mensaje en rojo debajo de él. Corrígelo y vuelve a pulsar **Guardar y aplicar**.

Qué hace el panel al guardar:

1. Guarda una copia de `main.cf` como `main.cf.matipanel.bak`.
2. Aplica solo los campos que cambiaron con `postconf -e`.
3. Valida con `postfix check`. Si falla, **restaura el archivo anterior** y muestra el error.
4. Recarga Postfix. Si cambiaste **inet_interfaces** o **inet_protocols**, lo reinicia (esos cambios no se aplican con una recarga).

Pulsa **Descartar cambios** para volver a leer los valores actuales.

> **Consejo:** si usas Dovecot, pon **Buzón en el home** en `Maildir/` para que Postfix y Dovecot usen el mismo formato.

> **Consejo:** para el certificado TLS, obtén uno gratuito para `mail.tudominio` en [SSL / Let's Encrypt](22-ssl.md) y copia aquí sus rutas.

### Botones de servicio

| Botón | Qué hace |
|---|---|
| **Comprobar config** | Ejecuta `postfix check` y muestra los avisos. No cambia nada. |
| **Recargar** | Comprueba la configuración y, si es válida, recarga Postfix sin cortar conexiones. |
| **Reiniciar** | Comprueba la configuración y reinicia Postfix. |
| **Detener / Iniciar** | Detiene o inicia Postfix. Detenerlo pide confirmación. |
| **Editar main.cf / master.cf** | Abre el archivo en el editor de [Archivos](26-archivos.md). Después pulsa **Comprobar config** y **Recargar**. |

Si la configuración tiene errores, **Recargar**, **Reiniciar** e **Iniciar** no se aplican y verás **La configuración tiene errores; no se aplicó** con la salida.

### Usar un relay para enviar

Si tu proveedor bloquea el puerto 25, o tu institución tiene un servidor de correo central, haz que Postfix envíe todo a través de él:

1. En **Relay de salida (relayhost)** escribe el servidor entre corchetes y el puerto, por ejemplo `[smtp.ejemplo.gob.gt]:587`.
2. Pulsa **Guardar y aplicar**.

> **Nota:** si el relay pide usuario y contraseña, hay que configurar `smtp_sasl_auth_enable` y un archivo de contraseñas en `main.cf`. Hazlo con **Editar main.cf**.

## Revisar la cola de correo

La pestaña **Cola de correo** lista los mensajes que Postfix todavía no pudo entregar:

| Columna | Significado |
|---|---|
| **ID** | Identificador del mensaje en la cola. |
| **Cola** | `active` (enviándose), `deferred` (diferido: se reintentará), `hold` (retenido: no se envía hasta liberarlo). |
| **Llegada** | Cuándo entró a la cola. |
| **Tamaño** | Tamaño del mensaje. |
| **Remitente / Destinatarios** | Direcciones del sobre. `<>` es un aviso del sistema (rebote). |
| **Motivo** | Por qué no se entregó, por ejemplo `Connection timed out` o `Host not found`. |

Escribe en **Filtrar…** para buscar por ID, remitente o destinatario.

Acciones:

- **Ver:** muestra el mensaje completo (sobre, cabeceras y cuerpo), hasta 200 KB.
- **Retener / Liberar:** pausa un mensaje o lo devuelve a la cola de envío.
- **Eliminar:** borra el mensaje. Pide confirmación. No se puede deshacer.
- **Reintentar envío:** pide a Postfix que intente entregar ya todos los mensajes diferidos (`postqueue -f`).
- **Eliminar todo:** vacía la cola completa. Debes escribir `ELIMINAR` para confirmar.

> **Consejo:** si la cola se llena de mensajes a destinos desconocidos o de spam, puede que un sitio web del servidor esté siendo usado para enviar correo masivo. Revisa el **Motivo** y el **Remitente** antes de vaciarla.

## Gestionar los alias

Un alias reenvía el correo de una dirección a uno o más destinos. Por ejemplo, `soporte` → `ana, luis@ejemplo.gob.gt`. Los alias viven en `/etc/aliases`.

### Agregar o editar un alias

1. Abre la pestaña **Alias**.
2. Pulsa **+ Nuevo alias**, o **Editar** en la fila del alias.
3. Completa:
    - **Alias:** la parte antes de la `@` (letras minúsculas, números y `. _ + -`).
    - **Destinos:** separados por coma. Pueden ser usuarios locales (`ana`), correos (`luis@ejemplo.gob.gt`), un archivo (`/var/log/correo-soporte.mbox`) o una lista (`:include:/etc/postfix/lista-soporte`).
4. Pulsa **Guardar**.

### Eliminar un alias

1. Pulsa **Eliminar** en la fila.
2. Confirma en **Eliminar alias**.

Qué hace el panel al guardar o eliminar:

1. Guarda una copia como `/etc/aliases.matipanel.bak`.
2. Modifica solo la entrada afectada. Los comentarios y el resto del archivo se conservan.
3. Ejecuta `newaliases`. Si falla, restaura el archivo anterior.

> **Importante:** el alias `root` debería apuntar a una persona real (por ejemplo `root: admin@ejemplo.gob.gt`). Así recibirás los avisos del sistema, de cron y de las actualizaciones automáticas.

> **Nota:** las entradas que envían el correo a un programa (`|/ruta/comando`) no se pueden crear desde el formulario por seguridad. Usa **Editar archivo** y después guarda cualquier alias o ejecuta `newaliases` en la [Terminal](29-terminal.md).

## Configurar Dovecot

La pestaña **Dovecot** muestra:

| Dato | Significado |
|---|---|
| **Configuración** | `válida` si `doveconf -n` no encontró errores. |
| **Protocolos** | Protocolos activos (`imap`, `pop3`). Dependen de los paquetes instalados. |
| **Ubicación del buzón** | Dónde busca Dovecot el correo, por ejemplo `maildir:~/Maildir`. Debe coincidir con **Buzón en el home** de Postfix. |
| **SSL / TLS** | `activado`, `obligatorio` o `desactivado`. |

### Cambiar el SSL

Usa los botones junto a **SSL / TLS**:

- **Sí:** acepta conexiones cifradas y sin cifrar.
- **Obligatorio:** no acepta contraseñas por conexiones sin cifrar. Es lo recomendado para un servidor público.
- **No:** desactiva el cifrado. Las contraseñas viajan en texto plano. No lo uses fuera de pruebas.

El panel cambia la línea `ssl` de `/etc/dovecot/conf.d/10-ssl.conf` (con copia `.matipanel.bak`), valida con `doveconf -n` y reinicia Dovecot. Si la validación falla, restaura el archivo.

### Editar otros ajustes

La tarjeta **Archivos de configuración** lista `dovecot.conf` y los archivos de `conf.d/`. Pulsa **Editar** para abrir uno en el editor. Los más usados:

| Archivo | Para qué |
|---|---|
| `10-mail.conf` | Ubicación y formato de los buzones. |
| `10-auth.conf` | Métodos de autenticación. |
| `10-ssl.conf` | Certificado y llave TLS de Dovecot. |
| `10-master.conf` | Puertos de escucha y el socket de autenticación para Postfix. |

Después de editar, pulsa **Comprobar config** y luego **Reiniciar**. El panel no reinicia Dovecot si `doveconf -n` encuentra errores.

Abre **Configuración efectiva (doveconf -n)** al final de la pestaña para ver todos los ajustes que difieren de los valores por defecto.

> **Nota:** Debian 13 incluye Dovecot 2.4, que cambió el nombre de varios ajustes respecto a la 2.3 (por ejemplo, `mail_location` se divide en `mail_driver` y `mail_path`). Si copias configuraciones de guías antiguas, revisa que sean para la versión 2.4.

## Ver el registro de correo

La pestaña **Registro** muestra las últimas 300 líneas del registro de correo. Elige el origen:

- **Automático:** usa `/var/log/mail.log` si existe; si no, el journal de systemd.
- **mail.log:** solo el archivo (existe si tienes `rsyslog` instalado).
- **journal:** los mensajes de correo del journal (`journalctl`).

Escribe en **Filtrar líneas…** para buscar, por ejemplo, una dirección de correo o un ID de la cola.

Líneas útiles para diagnosticar:

| Texto en el registro | Significado |
|---|---|
| `status=sent` | Entregado correctamente. |
| `status=deferred` | No se pudo entregar todavía; Postfix reintentará. |
| `status=bounced` | El servidor de destino lo rechazó de forma definitiva. |
| `Connection timed out` | No se pudo conectar al destino (a menudo, puerto 25 bloqueado). |
| `authentication failed` | Usuario o contraseña incorrectos al leer o enviar correo. |

## Enviar un correo de prueba

1. Abre la pestaña **Enviar prueba**.
2. En **Para**, escribe una cuenta tuya, por ejemplo de tu correo institucional.
3. Cambia **Asunto** y **Mensaje** si quieres.
4. Pulsa **Enviar**.

Si aparece **Correo enviado**, Postfix aceptó el mensaje. Eso no garantiza que llegue: revisa la bandeja de entrada y la carpeta de spam. Si no llega en unos minutos, mira la **Cola de correo** y el **Registro**.

## Preguntas frecuentes

### Mis correos llegan a spam o los rechazan

Casi siempre es el DNS. Revisa que tengas:

- Un registro **SPF** que autorice la IP del servidor.
- Un **PTR** (DNS inverso) de la IP que apunte a tu **myhostname**. Lo configura tu proveedor de internet.
- Que **myhostname** resuelva a la IP del servidor.

Ver [DNS](51-dns.md).

### Los mensajes se quedan en la cola con "Connection timed out"

Tu proveedor probablemente bloquea el puerto 25 de salida. Pide que lo habiliten o configura un **Relay de salida**.

### No recibo correo de internet

Comprueba, en este orden: que el registro **MX** de tu dominio apunte a este servidor; que el puerto **25** esté abierto en el [Firewall](20-firewall.md) y en el firewall perimetral; que **inet_interfaces** sea `all`; y que tu dominio esté en **Destinos locales**.

### Guardé la configuración y salió "postfix check falló"

El panel ya restauró el `main.cf` anterior, así que Postfix sigue funcionando como antes. Lee la salida del error, corrige el valor y vuelve a guardar.

### Thunderbird u Outlook no se conectan

Revisa que Dovecot esté activo, que los puertos **993**/**995** estén abiertos en el firewall y que el usuario exista en el sistema ([Usuarios](17-usuarios.md)). Si usas un certificado autofirmado, el programa de correo mostrará un aviso: usa un certificado de [SSL / Let's Encrypt](22-ssl.md).

### ¿Cómo creo una cuenta de correo?

En esta configuración básica, cada cuenta es un usuario del sistema. Crea el usuario en [Usuarios](17-usuarios.md); su dirección será `usuario@tudominio` (siempre que tu dominio esté en **Destinos locales**). Para direcciones adicionales que no son usuarios, usa **Alias**.

## Ver también

- [DNS](51-dns.md)
- [Firewall](20-firewall.md)
- [SSL / Let's Encrypt](22-ssl.md)
- [Usuarios](17-usuarios.md)
- [Servicios](13-servicios.md)
- [Logs](28-logs.md)
- [Solución de problemas](90-solucion-de-problemas.md)
