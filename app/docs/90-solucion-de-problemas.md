# Solución de problemas

Aquí están los problemas más comunes y cómo resolverlos. Casi todos se diagnostican con dos comandos en la consola del servidor (como root):

```bash
systemctl status matipanel --no-pager       # ¿está corriendo?
journalctl -u matipanel -n 60 --no-pager     # últimos mensajes y errores
```

## La página no carga ("No se puede acceder a este sitio")

El navegador muestra `ERR_CONNECTION_REFUSED` o se queda esperando.

1. Comprueba que el servicio esté activo: `systemctl status matipanel --no-pager`. Debe decir **active (running)**.
2. Si dice **activating (auto-restart)** o **failed**, mira el error con `journalctl -u matipanel -n 60 --no-pager` (ver las secciones siguientes).
3. Revisa que estés usando **https://** (no http) y el puerto correcto (por defecto **8443**).
4. Si hay firewall activo, permite el puerto: `ufw allow 8443/tcp`.
5. Comprueba que el servicio esté escuchando: `ss -ltnp | grep 8443`.

## El servicio no arranca: "No module named 'packaging'"

En el registro aparece `Error: class uri 'gevent' invalid or not found` y `ModuleNotFoundError: No module named 'packaging'`.

Es un fallo conocido de gunicorn 23. Las versiones actuales del instalador ya lo incluyen; si instalaste una versión anterior:

```bash
/opt/matipanel/venv/bin/pip install packaging
systemctl restart matipanel
```

## "Internal Server Error" al iniciar sesión

1. Mira el detalle con `journalctl -u matipanel -n 60 --no-pager | grep -v "GET /static"`.
2. Si el error menciona `child watchers are only available on the default loop`, tienes una versión antigua: actualiza MatiPanel (ver [Instalación](02-instalacion.md#actualizar-matipanel)).
3. Comprueba que PAM valide tu contraseña fuera del panel:
   ```bash
   /opt/matipanel/venv/bin/python -c "import pam,getpass; p=pam.pam(); print(p.authenticate('TU_USUARIO', getpass.getpass('Clave: '), service='matipanel'), p.reason)"
   ```
   Debe responder `True Success`.

## "No se pudo leer la configuración de 2FA; revisa /var/lib/matipanel/2fa.json"

El archivo de 2FA existe pero está dañado (JSON mal formado) o no se puede leer. Por seguridad el panel **no deja entrar a nadie** mientras tanto, en lugar de saltarse el 2FA, y no sobrescribe el archivo.

1. Revisa el archivo en la consola o por SSH: `sudo python3 -m json.tool /var/lib/matipanel/2fa.json` indica la línea con el error.
2. Corrígelo, o restáuralo desde un respaldo.
3. Si no se puede reparar, bórralo con `sudo rm /var/lib/matipanel/2fa.json`. Todos los usuarios entrarán solo con contraseña y tendrán que volver a configurar su 2FA.

## "Usuario o contraseña incorrectos, o sin permiso para el panel"

El mensaje es el mismo en ambos casos a propósito, para no revelar qué usuarios existen. Revisa:

1. **Que el usuario esté en un grupo permitido:** `id TU_USUARIO` debe mostrar `matipanel`, `sudo`, `wheel` o `admin`. Si no: `usermod -aG matipanel TU_USUARIO`.
2. **Que no sea root:** root no puede entrar al panel; usa tu cuenta personal.
3. **Que la contraseña sea la de Linux** del servidor (no la de otro sistema).
4. **Que la cuenta no esté bloqueada o vencida:** `passwd -S TU_USUARIO`.

## "Demasiados intentos fallidos. Espera unos minutos"

Hubo 5 intentos fallidos desde tu IP. Espera 5 minutos. Si es urgente, otro administrador puede liberar el bloqueo reiniciando el servicio: `systemctl restart matipanel`.

## Perdí el teléfono con el código 2FA

1. Entra con uno de tus **códigos de recuperación** (8 caracteres) en lugar del código de 6 dígitos.
2. Ya dentro, ve a [Seguridad (2FA)](30-seguridad-2fa.md) y desactiva el 2FA escribiendo **otro** código de recuperación (el que usaste para entrar ya está gastado). Luego vuelve a configurarlo con el teléfono nuevo.

Si tampoco tienes códigos de recuperación, un administrador con acceso a la consola puede borrar tu 2FA:

```bash
python3 -c "import json;p='/var/lib/matipanel/2fa.json';d=json.load(open(p));d.pop('TU_USUARIO',None);json.dump(d,open(p,'w'),indent=2)"
```

## El firewall me dejó sin acceso

Ver [Recuperar el acceso si te bloqueaste](20-firewall.md#recuperar-el-acceso-si-te-bloqueaste) en la ayuda del Firewall.

## El instalador falla con "orden no encontrada"

Entraste como root con `su` **sin guion**, y no se cargaron las rutas `/usr/sbin`. Sal con `exit` y entra con `su -`, o ejecuta:

```bash
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"
```

## "Incapaz de leer el directorio de trabajo actual" al clonar

Borraste la carpeta en la que estaba tu consola. Ejecuta `cd /` y repite el `git clone`.

## Una acción de paquetes dice que el gestor está bloqueado

Otro proceso está usando apt (por ejemplo, las actualizaciones automáticas). Espera unos minutos y vuelve a intentar. Si el mensaje dice que dpkg quedó interrumpido, ejecuta en la [Terminal](29-terminal.md): `sudo dpkg --configure -a`.

## Los gráficos del dashboard están vacíos

El historial se guarda en `/var/lib/matipanel/metrics.db` y se conserva 7 días, incluso tras reiniciar. Si está vacío:

1. Si acabas de instalar, espera unos minutos: se toma una muestra cada 5 segundos.
2. Comprueba que el archivo exista y crezca: `ls -lh /var/lib/matipanel/metrics.db`.
3. Si el disco estaba lleno, el panel no puede escribir; libera espacio y reinicia el servicio.

## La terminal no conecta

1. Recarga la página; la terminal se conecta sola.
2. Si se cierra al instante, revisa que tu usuario tenga un shell válido (`/bin/bash`) en [Usuarios y grupos](17-usuarios.md).
3. Si usas un proxy inverso delante del panel, debe permitir WebSockets.

## Los cambios de estilo no se ven después de actualizar

El navegador guardó la versión anterior. Recarga con **Ctrl+F5**.

## Reunir información para pedir ayuda

Si necesitas reportar un problema, incluye la salida de:

```bash
systemctl status matipanel --no-pager
journalctl -u matipanel -n 100 --no-pager
cat /etc/os-release
```

## Ver también

- [Instalación y actualización](02-instalacion.md)
- [Firewall](20-firewall.md)
- [Seguridad (2FA)](30-seguridad-2fa.md)
