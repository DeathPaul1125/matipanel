# Seguridad y autenticación en dos pasos (2FA)

Esta página explica cómo protege MatiPanel el inicio de sesión y cómo activar la autenticación en dos pasos (2FA) con una app de códigos. La configuración de 2FA está en **Menú → Cuenta → Seguridad**.

## Cómo funciona el inicio de sesión

- Entras con tu usuario y contraseña de Linux.
- Solo pueden entrar los usuarios de los grupos `matipanel`, `sudo`, `wheel` o `admin`.
- Si el usuario, la contraseña o el grupo no son correctos, verás siempre el mismo mensaje: **Usuario o contraseña incorrectos, o sin permiso para el panel.** Así no se revela qué usuarios existen.
- El panel comprueba tus grupos en cada clic. Si un administrador te quita del grupo, tu sesión se cierra en la siguiente acción.
- Si tienes 2FA activo, después de la contraseña se pide un código.
- Para salir, pulsa **Cerrar sesión** al pie de la barra lateral.

## Bloqueo por intentos fallidos

Después de **5 intentos fallidos en 5 minutos**, el panel bloquea los nuevos intentos. Verás **Demasiados intentos fallidos. Espera unos minutos.**

- Cuentan tanto las contraseñas incorrectas como los códigos 2FA incorrectos.
- El bloqueo es por **dirección IP**, no por usuario. Si varias personas salen a Internet por la misma IP (NAT o proxy institucional), comparten el contador.
- Mientras dura el bloqueo, se rechaza incluso la contraseña correcta.
- El bloqueo se levanta solo cuando pasan 5 minutos desde los intentos fallidos.
- Un inicio de sesión correcto reinicia el contador.
- Reiniciar el servicio `matipanel` también borra los contadores.

## Activar la autenticación en dos pasos

### Antes de empezar

- Instala en tu teléfono una app de códigos: Google Authenticator, Authy, FreeOTP, Aegis, 1Password o similar.
- Comprueba que la hora del servidor sea correcta y esté sincronizada (NTP). Los códigos dependen de la hora. Ver [Sistema](11-sistema.md).
- Si la página dice que el módulo `pyotp` no está instalado, sigue el comando que muestra y reinicia el panel.

### Pasos

1. Ve a **Menú → Cuenta → Seguridad** y pulsa **Configurar 2FA**.
2. **Paso 1 · Escanea el QR con tu app.** Abre la app y escanea el código QR. Si no puedes escanearlo, pulsa **¿No puedes escanear? Mostrar secret manualmente** y escribe esa clave en la app (tipo "basada en tiempo", 6 dígitos).
3. **Paso 2 · Guarda estos códigos de recuperación.** Verás 8 códigos. Guárdalos fuera del teléfono: impresos en un lugar seguro o en el gestor de contraseñas de la institución.
4. **Paso 3 · Confirma con tu primer código.** Escribe el código de 6 dígitos que muestra la app y pulsa **Activar 2FA**.
5. Verás **2FA activado correctamente. Guarda tus códigos de recuperación.** y la etiqueta **2FA activado**.

> **Importante:** Los códigos de recuperación se muestran **una sola vez**. No podrás verlos de nuevo.

> **Importante:** Si al activar ves **Código inválido. Intenta de nuevo.**, vuelves a la página de Seguridad. Al pulsar otra vez **Configurar 2FA** se genera un **QR nuevo y códigos nuevos**. Borra en la app la entrada anterior de MatiPanel, escanea el QR nuevo y descarta los códigos viejos.

Si pulsas **Cancelar** en la página de configuración, el 2FA no se activa.

## Iniciar sesión con 2FA

1. Escribe tu usuario y contraseña y pulsa **Iniciar sesión**.
2. Aparece **Código de verificación**.
3. Abre la app y escribe el código de 6 dígitos de MatiPanel.
4. Pulsa **Verificar**.

El panel acepta el código actual y también el inmediatamente anterior o siguiente, para tolerar pequeñas diferencias de hora. Si te equivocas de cuenta, pulsa **Volver al login**.

## Entrar con un código de recuperación

Úsalo si no tienes tu teléfono a mano.

1. En **Código de verificación**, escribe uno de tus códigos de recuperación en el mismo campo (**Código (o uno de recuperación)**). Son 8 caracteres con números y letras de la `a` a la `f`.
2. Pulsa **Verificar**.
3. Verás **Usaste un código de recuperación. Genera uno nuevo en Seguridad.**

Cada código sirve **una sola vez**. Táchalo de tu lista.

> **Nota:** No hay un botón para generar solo códigos nuevos ni para ver cuántos te quedan. Para tener una lista nueva, desactiva el 2FA y vuelve a configurarlo.

## Desactivar 2FA

1. Ve a **Menú → Cuenta → Seguridad**.
2. Pulsa **Desactivar 2FA**.
3. Confirma en **Desactivar 2FA** con **Desactivar**.
4. Verás **2FA desactivado.**

Se borran la clave y los códigos de recuperación. Desde ese momento entras solo con contraseña.

> **Importante:** Desactivar el 2FA no pide un código, solo tu sesión abierta. Bloquea tu equipo cuando te alejes y cierra la sesión del panel en equipos compartidos.

## Cambiar de teléfono

Si todavía tienes el teléfono anterior:

1. Entra al panel con el código del teléfono anterior.
2. Pulsa **Desactivar 2FA** y confirma.
3. Pulsa **Configurar 2FA** y sigue los pasos con el teléfono nuevo.
4. Guarda los códigos de recuperación nuevos y descarta los viejos.

## Qué hacer si perdiste el teléfono

### Si tienes códigos de recuperación

1. Entra con un código de recuperación.
2. Ve a **Seguridad**, pulsa **Desactivar 2FA** y confirma.
3. Pulsa **Configurar 2FA** con tu teléfono nuevo.
4. Guarda la lista nueva de códigos.

### Si no tienes códigos de recuperación

Otro administrador con acceso `sudo` al servidor debe quitar tu 2FA. Puede hacerlo desde su propia [Terminal](29-terminal.md) del panel o por SSH:

```
sudo python3 -c "import json; p='/var/lib/matipanel/2fa.json'; d=json.load(open(p)); d.pop('USUARIO', None); json.dump(d, open(p, 'w'), indent=2)"
```

Cambia `USUARIO` por tu nombre de usuario de Linux. No hace falta reiniciar el panel. Después entra solo con contraseña y vuelve a configurar el 2FA.

> **Importante:** No edites `2fa.json` a mano con un editor de texto. Si el archivo queda con un error de formato, el panel deja de pedir el 2FA a **todos** los usuarios.

## Recomendaciones de seguridad

- Activa el 2FA en todas las cuentas que usan el panel.
- No expongas el puerto del panel (`8443` por defecto) a Internet. Limítalo a la red interna o a la VPN con el [Firewall](20-firewall.md).
- Cuando alguien deje de administrar servidores, quítalo del grupo `matipanel` (y de `sudo` si corresponde) en [Usuarios y grupos](17-usuarios.md).
- Usa contraseñas de Linux largas y únicas.
- Cierra la sesión del panel al terminar, sobre todo en equipos compartidos.

## Preguntas frecuentes

### La app me da un código y el panel siempre dice "Código inválido".

Casi siempre es la hora. Revisa que el teléfono tenga la hora automática y que el servidor esté sincronizado por NTP ([Sistema](11-sistema.md)). Revisa también que estés usando la entrada correcta de MatiPanel en la app (si configuraste varias veces, borra las viejas).

### Me bloqueé por intentos fallidos. ¿Cuánto debo esperar?

Hasta 5 minutos desde los intentos fallidos. Si es urgente, otro administrador puede reiniciar el servicio con `sudo systemctl restart matipanel`, lo que borra los contadores.

### ¿Puedo activar el 2FA a otro usuario?

No. Cada usuario activa su propio 2FA desde su sesión, porque necesita escanear el QR con su teléfono.

### ¿Cuánto dura mi sesión en el panel?

Hasta que pulses **Cerrar sesión** o cierres el navegador. Algunos navegadores restauran la sesión al volver a abrirse, así que en equipos compartidos pulsa siempre **Cerrar sesión**.

## Ver también

- [Primeros pasos](03-primeros-pasos.md)
- [Usuarios y grupos](17-usuarios.md)
- [Firewall](20-firewall.md)
- [Sistema](11-sistema.md)
- [Terminal](29-terminal.md)
- [Solución de problemas](90-solucion-de-problemas.md)
