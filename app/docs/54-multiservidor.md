# Multi-servidor

Si administras varios servidores con MatiPanel, puedes vigilarlos todos desde un solo panel: ves de un vistazo cuáles están en línea, su CPU, memoria y disco, los servicios con fallo y las actualizaciones pendientes, y saltas al panel de cualquiera con un clic. Lo encuentras en **Menú → Red → Servidores**.

Cada servidor sigue siendo independiente. El panel que vigila solo **lee** métricas de los demás: no puede cambiar nada en ellos.

## Cómo funciona

Hay dos papeles. Un mismo servidor puede tener los dos a la vez.

| Papel | Qué haces en él | Dónde |
|---|---|---|
| **Servidor vigilado** | Generas un token de solo lectura y copias la huella de su certificado | Tarjeta **Acceso remoto a este servidor** |
| **Panel que vigila** | Agregas el otro servidor con su dirección, su token y su huella | Botón **Agregar servidor** |

El panel que vigila consulta a cada servidor cada **15 segundos** por HTTPS, en el mismo puerto del panel (`8443` por defecto).

## Antes de empezar

- Los dos servidores deben tener MatiPanel **0.3.0 o posterior** con este módulo.
- El panel que vigila debe poder llegar al puerto del panel del otro servidor (normalmente `8443/tcp`). Si el servidor vigilado tiene firewall, permite ese puerto **solo desde la IP del panel que vigila**. Ver [Firewall](20-firewall.md): en **Agregar regla**, escribe el puerto `8443`, protocolo TCP y en el origen la IP del panel que vigila.

> **Importante:** No abras el puerto del panel a todo Internet para usar esta función. Limítalo a la IP del otro servidor, a la red interna o a la VPN.

## Paso 1: preparar el servidor que quieres vigilar

Hazlo en el servidor **vigilado**.

1. Entra a su panel y ve a **Menú → Red → Servidores**.
2. Baja hasta la tarjeta **Acceso remoto a este servidor**.
3. Pulsa **Generar token**.
4. Escribe una etiqueta para reconocer quién lo usa, por ejemplo `monitor-central`, y pulsa **Generar**.
5. Aparece un recuadro con el token y el aviso **Copia este token ahora: no se volverá a mostrar.** Pulsa **Copiar** y guárdalo temporalmente.
6. En la misma tarjeta, anota la **Dirección** y la **Huella SHA-256** (puedes pulsar **Copiar** junto a la huella).

> **Importante:** El servidor guarda solo una huella (hash) del token, no el token. Si lo pierdes antes de usarlo, revócalo y genera otro.

## Paso 2: agregar el servidor en el panel que vigila

Hazlo en el panel **que vigila**.

1. Ve a **Menú → Red → Servidores** y pulsa **Agregar servidor**.
2. Completa el formulario:

| Campo | Qué escribir |
|---|---|
| **Nombre** | Un nombre para la tarjeta, por ejemplo `Web producción`. |
| **Dirección del panel** | `https://servidor:puerto`, por ejemplo `https://10.0.0.5:8443`. Si no escribes el puerto, se usa `8443`. Sin rutas ni usuario. |
| **Token de acceso** | El token que copiaste en el Paso 1. |
| **Huella SHA-256 del certificado** | La huella del otro servidor. Usa **Obtener huella** (ver abajo). |

3. Pulsa **Obtener huella**. El panel se conecta una vez al otro servidor y te muestra la huella de su certificado en **Confirma la huella**.
4. **Compárala carácter por carácter** con la huella que anotaste en el Paso 1. Si es idéntica, pulsa **Sí, es idéntica**. Si no, pulsa **Cancelar** y no continúes.
5. Pulsa **Guardar servidor**.

A los pocos segundos aparece la tarjeta del servidor nuevo.

> **Nota:** También puedes pegar la huella directamente en el campo en lugar de usar **Obtener huella**. Se aceptan con o sin los dos puntos (`AB:CD:…` o `abcd…`).

## Leer las tarjetas

La primera tarjeta es siempre **este servidor**. Después vienen los demás en el orden en que los agregaste.

Cada tarjeta muestra:

- El **indicador de color**, el nombre y la dirección.
- Tres tacómetros: **CPU**, **Memoria** y **Disco** (la partición `/`).
- Una línea con la CPU de los últimos 5 minutos.
- **servicios con fallo** y **actualizaciones** pendientes. Deja el ratón sobre los servicios con fallo para ver sus nombres.
- Sistema operativo, tiempo encendido, carga media y versión de MatiPanel.
- La hora de la última actualización y el tiempo de respuesta.
- **Abrir** lleva al panel de ese servidor en una pestaña nueva. Como el certificado es autofirmado, el navegador te pedirá aceptarlo la primera vez. Allí inicias sesión con tu usuario de ese servidor.

Los estados posibles son:

| Estado | Indicador | Qué significa |
|---|---|---|
| **En línea** | Verde | Todo bien. |
| **Con avisos** | Amarillo | Responde, pero hay servicios con fallo, el disco está al 90 % o más, la memoria al 95 % o más, o la CPU al 90 % o más. |
| **Sin conexión** | Rojo | No responde: está apagado, el panel está detenido, hay un firewall en medio o la dirección es incorrecta. |
| **Token rechazado** | Rojo | El otro servidor no acepta el token (fue revocado o está mal copiado), o bloqueó esta IP por intentos fallidos. |
| **Huella distinta** | Rojo | **Alerta de seguridad.** Ver la sección siguiente. |
| **Error** | Rojo | El otro servidor respondió algo inesperado; por ejemplo, tiene una versión de MatiPanel sin este módulo. |

Cuando un servidor deja de responder, la tarjeta se atenúa y conserva los **Últimos datos** que recibió.

Arriba de todo, el **Resumen** dice cuántos servidores están en línea y cuántos tienen problemas. Se actualiza solo cada 15 segundos; pulsa **Actualizar** para hacerlo al momento.

## Si aparece "Huella distinta"

La tarjeta muestra **Alerta de seguridad: la huella del certificado no coincide.** con la huella **Recibida** y la **Guardada**. Cuando esto pasa, el panel **no envía el token**, así que el token sigue a salvo.

Hay dos causas posibles:

- **El otro servidor cambió de certificado.** Pasa si se reinstaló MatiPanel, si alguien regeneró `/etc/matipanel/cert.pem` o si la dirección ahora apunta a otra máquina.
- **Alguien se está haciendo pasar por el servidor** (ataque de intermediario).

Qué hacer:

1. Entra al otro servidor por un camino de confianza (consola, SSH o su panel desde la red interna).
2. Abre **Menú → Red → Servidores** y mira la **Huella SHA-256** en **Acceso remoto a este servidor**.
3. Si es igual a la **Recibida**, el cambio es legítimo: en el panel que vigila pulsa **Editar**, luego **Obtener huella**, confirma y pulsa **Guardar servidor**.
4. Si no es igual, **no actualices la huella**. Alguien está interceptando el tráfico: avisa al responsable de la red.

> **Importante:** Nunca aceptes una huella nueva solo porque la tarjeta dice "Huella distinta". Compruébala siempre en el otro servidor.

## Editar o quitar un servidor

- **Editar**: cambia el nombre, la dirección, el token o la huella. Deja **Token de acceso** vacío para conservar el actual (el formulario muestra en qué caracteres termina). Pulsa **Guardar servidor**.
- **Quitar**: confirma en **Quitar servidor**. Se borra el servidor y su token de este panel.

> **Nota:** Quitar un servidor aquí no invalida el token en el otro servidor. Para eso, revócalo allá (siguiente sección).

## Revocar un token

Hazlo en el servidor **vigilado** cuando un panel ya no deba verlo, o si crees que un token se filtró.

1. Ve a **Menú → Red → Servidores**, tarjeta **Acceso remoto a este servidor**.
2. En la tabla, busca el token por su **Etiqueta**. Las columnas **Último uso** y **Desde** te dicen cuándo y desde qué IP se usó por última vez.
3. Pulsa **Revocar** y confirma.

El efecto es inmediato: en la siguiente consulta, el panel que vigila verá **Token rechazado**.

## Seguridad

- **Los tokens son de solo lectura.** Solo sirven para pedir el resumen de métricas. No dan acceso a la terminal, a los archivos ni a ninguna otra pantalla, y no sirven para iniciar sesión.
- **El servidor vigilado guarda solo el hash SHA-256 del token**, en `/var/lib/matipanel/remote_tokens.json` (permisos `600`, solo root). Quien lea ese archivo no puede recuperar el token.
- **El panel que vigila sí guarda el token**, porque necesita enviarlo. Está en `/var/lib/matipanel/remote_servers.json` (permisos `600`, solo root) y **nunca se envía al navegador**: en pantalla solo se ven sus últimos 4 caracteres.
- **Fijación de huella.** Los certificados del panel son autofirmados, así que no hay una autoridad que los valide. En su lugar, el panel guarda la huella SHA-256 del certificado del otro servidor la primera vez (cuando tú la confirmas) y la comprueba en cada conexión, **antes** de enviar el token.
- **Bloqueo por intentos fallidos.** Después de **10 tokens incorrectos en 5 minutos** desde una misma IP, el servidor vigilado rechaza esa IP durante 5 minutos, incluso con un token válido.
- Quien tenga acceso al panel que vigila puede ver las métricas de todos los servidores agregados.

## Preguntas frecuentes

### La tarjeta dice "Sin conexión" pero el servidor está encendido.

Revisa, en este orden:

1. Que la dirección y el puerto sean correctos (pulsa **Abrir**: si el navegador no llega, el panel tampoco).
2. Que el firewall del servidor vigilado permita el puerto `8443/tcp` desde la IP del panel que vigila. Ver [Firewall](20-firewall.md).
3. Que el servicio `matipanel` esté activo en el otro servidor: `systemctl status matipanel`.

### La tarjeta dice "Token rechazado".

El token fue revocado o se copió mal. Genera uno nuevo en el otro servidor, pulsa **Editar** aquí, pégalo en **Token de acceso** y guarda. Si el mensaje habla de **bloqueó esta IP**, espera 5 minutos después de corregir el token.

### ¿Puedo usar el mismo token en varios paneles?

Funciona, pero no conviene. Genera un token por cada panel que vigila, con una etiqueta clara: así puedes revocar uno sin afectar a los demás y ver quién consulta desde dónde.

### ¿Qué pasa si reinstalo MatiPanel en un servidor vigilado?

Se genera un certificado nuevo, así que los paneles que lo vigilan mostrarán **Huella distinta**. Además, si se borró `/var/lib/matipanel`, los tokens dejan de existir. Genera un token nuevo y actualiza el servidor con **Editar** y **Obtener huella**.

### ¿Cada cuánto se consultan los servidores? ¿Consume mucho?

Cada 15 segundos, solo mientras tengas abierta la pantalla **Servidores** en primer plano. Cada consulta es una petición pequeña de pocos kilobytes. Cada servidor tiene 5 segundos para responder.

### ¿Por qué el historial de la tarjeta está vacío justo después de reiniciar?

El historial se guarda en memoria y se pierde al reiniciar el panel del otro servidor. Se vuelve a llenar en unos minutos.

## Ver también

- [Dashboard](10-dashboard.md)
- [Firewall](20-firewall.md)
- [Seguridad y 2FA](30-seguridad-2fa.md)
- [Servicios](13-servicios.md)
- [Paquetes](27-paquetes.md)
- [Solución de problemas](90-solucion-de-problemas.md)
