# Instalación y actualización

Esta guía explica cómo instalar MatiPanel en un servidor Debian (también sirve para Ubuntu), cómo actualizarlo y cómo desinstalarlo. Todo se hace una sola vez desde la consola del servidor; después, el día a día es desde el navegador.

## Requisitos

- Un servidor con **Debian 12 o 13** (o Ubuntu 22.04+), con acceso a internet durante la instalación.
- Acceso **root** (con `su -` o `sudo -i`).
- Un usuario Linux personal **con contraseña**, que usarás para entrar al panel (por ejemplo `pablo`). El panel pide el usuario y la contraseña de Linux; ver [Usuario sin contraseña](#usuario-sin-contrasena-vps-con-clave-ssh) si entras al servidor solo con clave SSH.
- El puerto **8443/tcp** libre (puedes usar otro, ver más abajo). Si está ocupado, el instalador te avisa.

## Instalar MatiPanel

1. Conéctate al servidor por SSH con tu usuario:
   ```bash
   ssh pablo@IP-DEL-SERVIDOR
   ```
2. Conviértete en root **con guion** (carga el entorno completo de root):
   ```bash
   su -
   ```
   Si tu usuario tiene sudo, también sirve `sudo -i`.
3. Descarga el proyecto en una carpeta **permanente**:
   ```bash
   apt update && apt install -y git
   cd /
   git clone https://github.com/DeathPaul1125/matipanel.git /opt/matipanel-src
   ```
   No uses `/tmp`: se vacía al reiniciar el servidor y tendrías que volver a clonar para actualizar.
4. Ejecuta el instalador indicando el usuario que entrará al panel:
   ```bash
   cd /opt/matipanel-src
   bash scripts/install.sh pablo
   ```
   Al empezar muestra el **puerto** que usará y de dónde lo tomó (por ejemplo `Puerto: 8443 (predeterminado)`).
5. Espera unos minutos. Al final verás **✓ MatiPanel instalado** con la dirección para entrar, y debajo los avisos que haya (por ejemplo, si tu usuario no tiene contraseña).
6. Abre en tu navegador `https://IP-DEL-SERVIDOR:8443`. El navegador avisará que el certificado no es de confianza: es normal porque es autofirmado. Elige **Avanzado → Continuar** y entra con tu usuario y contraseña de Linux.

> **Nota:** si `git clone` te pide usuario y contraseña, el repositorio es privado. Usa un token de GitHub o copia la carpeta con `scp` desde tu PC (ver la siguiente sección).

### Qué hace el instalador

| Paso | Detalle |
|---|---|
| Dependencias | Instala Python, `python3-venv`, compiladores, `openssl` y `unzip`. |
| Grupo de acceso | Crea el grupo `matipanel` y agrega al usuario que indicaste. |
| Archivos | Copia la aplicación a `/opt/matipanel` y crea un entorno de Python aislado. |
| Certificado | Genera un certificado HTTPS autofirmado en `/etc/matipanel/` (válido 5 años). |
| PAM | Configura `/etc/pam.d/matipanel` para validar las contraseñas del sistema. |
| Servicio | Instala y arranca el servicio `matipanel` (se inicia solo al encender el servidor). |
| Puerto | Antes de instalar, comprueba que el puerto esté libre. Lo guarda en `/etc/default/matipanel` para las actualizaciones. |
| Contraseña | Al final, avisa si el usuario no tiene contraseña de Linux y ofrece ponérsela. |

### Usuario sin contraseña (VPS con clave SSH)

En los VPS de la nube (Oracle Cloud, AWS, Azure…) se entra por SSH con una **clave**, y el usuario suele **no tener contraseña**. El panel inicia sesión con la contraseña de Linux, así que sin ella no podrías entrar. El instalador lo detecta y, al final, muestra:

```
⚠ El usuario 'matiadmin' no tiene contraseña (entra por SSH con clave).
  El panel pide usuario y contraseña de Linux: así no podrás iniciar sesión.
  ¿Ponerle una contraseña ahora? [S/n]
```

Pulsa **Enter** y escribe la contraseña dos veces. Si ejecutaste el instalador sin una consola interactiva, ponla después con `sudo passwd matiadmin`. Lo mismo aplica si la contraseña está **bloqueada**. Ponerle contraseña no cambia el acceso por SSH: sigues entrando con tu clave.

> **Consejo:** En vez de dar acceso al usuario de la nube (`ubuntu`, `opc`…), crea uno propio para el panel: `sudo adduser matiadmin` (te pide la contraseña) y luego `sudo bash scripts/install.sh matiadmin`.

## Copiar el proyecto sin GitHub

Si el servidor no tiene acceso a GitHub, copia la carpeta desde tu PC. En **PowerShell de tu PC** (no en el servidor):

```powershell
scp -r C:\ruta\a\matipanel pablo@IP-DEL-SERVIDOR:/tmp/
```

Luego, en el servidor como root, muévela a una carpeta permanente e instala:

```bash
mv /tmp/matipanel /opt/matipanel-src
cd /opt/matipanel-src && bash scripts/install.sh pablo
```

## Usar otro puerto

Antepón la variable `MATIPANEL_PORT` al instalador:

```bash
MATIPANEL_PORT=9443 bash scripts/install.sh pablo
```

Solo hace falta **la primera vez**: el puerto queda guardado en `/etc/default/matipanel` y las actualizaciones lo usan solas (`bash scripts/install.sh pablo` basta). Para cambiarlo más adelante, vuelve a instalar con `MATIPANEL_PORT=` y el puerto nuevo; el instalador muestra `Puerto: 8443 (variable MATIPANEL_PORT; antes era 9443)` para que veas el cambio.

### Si el puerto está ocupado

Si otro programa ya escucha en el puerto (por ejemplo otra aplicación en el 8443), el instalador **se detiene antes de instalar nada**, dice qué programa lo usa y sugiere un puerto libre:

```
✗ El puerto 8443 ya lo usa otro programa: java.
  MatiPanel no podría arrancar ahí (y entrarías a esa otra aplicación).
  Instala en otro puerto, por ejemplo el 9443 (está libre):
    MATIPANEL_PORT=9443 bash scripts/install.sh pablo
```

Copia el comando sugerido. Si quien ocupa el puerto es el propio MatiPanel (una actualización), el instalador sigue normalmente.

> **Importante:** si tienes un firewall activo, abre el puerto elegido antes de intentar entrar (ver [Firewall](20-firewall.md)). En la nube (Oracle, AWS…) ábrelo también en las reglas de red del proveedor.

## Dar acceso a otros usuarios

Agrega al usuario al grupo `matipanel`, desde la consola o desde [Usuarios y grupos](17-usuarios.md):

```bash
usermod -aG matipanel juan
```

El cambio aplica de inmediato en el siguiente inicio de sesión. Para quitar el acceso: `gpasswd -d juan matipanel`.

## Actualizar MatiPanel

1. En el servidor, descarga la versión nueva y vuelve a ejecutar el instalador, en un solo paso:
   ```bash
   cd /opt/matipanel-src && sudo git pull && sudo bash scripts/install.sh pablo
   ```
   No hace falta indicar el puerto: se usa el de la instalación anterior.
2. En el navegador, recarga con **Ctrl+F5** para tomar los estilos nuevos.

Se conservan el certificado, el puerto, la configuración de 2FA, las preferencias de apariencia y los respaldos.

> **Nota:** usa `sudo` también en `git pull`. La carpeta se clonó como root; si haces `git pull` con tu usuario, git responde `detected dubious ownership in repository`.

### Si instalaste desde /tmp

Las instalaciones antiguas se clonaban en `/tmp/matipanel`, que se vacía al reiniciar. El instalador lo detecta y, al final, sugiere moverla. Hazlo una sola vez:

```bash
sudo mv /tmp/matipanel /opt/matipanel-src
```

Desde entonces actualiza con el comando de arriba. Si `/tmp/matipanel` ya desapareció (el servidor se reinició), clona de nuevo en `/opt/matipanel-src` (paso 3 de la instalación) y ejecuta el instalador: el puerto y la configuración se conservan.

> **Importante:** no borres ni muevas la carpeta mientras tu consola está dentro de ella. Si lo haces, ejecuta `cd /` antes de volver a clonar.

## Opciones del servicio

Se configuran con `systemctl edit matipanel`, que abre un archivo donde puedes agregar:

```ini
[Service]
Environment="MATIPANEL_FILE_WRITE=0"
```

| Variable | Efecto |
|---|---|
| `MATIPANEL_FILE_WRITE=0` | El administrador de archivos queda en solo lectura. |

Después de guardar: `systemctl restart matipanel`.

## Desinstalar

Como root, desde la carpeta del proyecto:

```bash
bash scripts/uninstall.sh
```

Elimina el servicio, `/opt/matipanel`, el certificado, los secretos 2FA, las preferencias y la configuración de PAM. **No** borra tus respaldos de `/var/backups/matipanel` ni nada de lo que hayas configurado con el panel (sitios, usuarios, reglas de firewall).

## Archivos y carpetas importantes

| Ruta | Contenido |
|---|---|
| `/opt/matipanel` | La aplicación y su entorno de Python. |
| `/etc/matipanel/` | Certificado HTTPS (`cert.pem`, `key.pem`). |
| `/var/lib/matipanel/` | Secretos 2FA, preferencias de apariencia, clave de sesiones y trabajos de respaldo. |
| `/var/backups/matipanel/` | Respaldos creados desde el panel. |
| `/etc/systemd/system/matipanel.service` | Definición del servicio. |
| `/etc/default/matipanel` | El puerto del panel (`MATIPANEL_PORT=…`), que el instalador recuerda al actualizar. |
| `/opt/matipanel-src` | La copia del repositorio desde la que se instala y actualiza. |
| `/etc/pam.d/matipanel` | Validación de contraseñas. |

## Preguntas frecuentes

### ¿Puedo usar un certificado propio en lugar del autofirmado?

Sí. Reemplaza `/etc/matipanel/cert.pem` y `/etc/matipanel/key.pem` por tu certificado y su clave (en formato PEM) y ejecuta `systemctl restart matipanel`.

### El instalador dice "groupadd: orden no encontrada"

Entraste como root con `su` sin guion. Las versiones recientes del instalador ya lo corrigen solas; si usas una antigua, ejecuta `su -` o `export PATH="$PATH:/usr/sbin:/sbin"` y repite.

### ¿Cómo sé qué versión tengo?

Abre **menú de usuario (tu nombre, abajo a la izquierda) → Acerca de**.

## Ver también

- [Primeros pasos](03-primeros-pasos.md)
- [Firewall](20-firewall.md)
- [Solución de problemas](90-solucion-de-problemas.md)
