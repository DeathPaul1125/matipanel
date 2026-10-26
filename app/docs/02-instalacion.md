# Instalación y actualización

Esta guía explica cómo instalar MatiPanel en un servidor Debian (también sirve para Ubuntu), cómo actualizarlo y cómo desinstalarlo. Todo se hace una sola vez desde la consola del servidor; después, el día a día es desde el navegador.

## Requisitos

- Un servidor con **Debian 12 o 13** (o Ubuntu 22.04+), con acceso a internet durante la instalación.
- Acceso **root** (con `su -` o `sudo -i`).
- Un usuario Linux personal que usarás para entrar al panel (por ejemplo `pablo`).
- El puerto **8443/tcp** libre (puedes usar otro, ver más abajo).

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
3. Descarga el proyecto:
   ```bash
   apt update && apt install -y git
   cd /
   git clone https://github.com/DeathPaul1125/matipanel.git /tmp/matipanel
   ```
4. Ejecuta el instalador indicando el usuario que entrará al panel:
   ```bash
   cd /tmp/matipanel
   bash scripts/install.sh pablo
   ```
5. Espera unos minutos. Al final verás **✓ MatiPanel instalado** con la dirección para entrar.
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

## Copiar el proyecto sin GitHub

Si el servidor no tiene acceso a GitHub, copia la carpeta desde tu PC. En **PowerShell de tu PC** (no en el servidor):

```powershell
scp -r C:\ruta\a\matipanel pablo@IP-DEL-SERVIDOR:/tmp/
```

Luego, en el servidor como root: `cd /tmp/matipanel && bash scripts/install.sh pablo`.

## Usar otro puerto

Antepón la variable `MATIPANEL_PORT` al instalador:

```bash
MATIPANEL_PORT=9443 bash scripts/install.sh pablo
```

> **Importante:** si tienes un firewall activo, abre el puerto elegido antes de intentar entrar (ver [Firewall](20-firewall.md)).

## Dar acceso a otros usuarios

Agrega al usuario al grupo `matipanel`, desde la consola o desde [Usuarios y grupos](17-usuarios.md):

```bash
usermod -aG matipanel juan
```

El cambio aplica de inmediato en el siguiente inicio de sesión. Para quitar el acceso: `gpasswd -d juan matipanel`.

## Actualizar MatiPanel

1. En el servidor, como root, descarga la versión nueva:
   ```bash
   cd /tmp/matipanel && git pull
   ```
   Si la carpeta ya no existe, clónala de nuevo (paso 3 de la instalación).
2. Vuelve a ejecutar el instalador:
   ```bash
   bash scripts/install.sh
   ```
3. En el navegador, recarga con **Ctrl+F5** para tomar los estilos nuevos.

Se conservan el certificado, la configuración de 2FA, las preferencias de apariencia y los respaldos.

> **Importante:** no borres la carpeta `/tmp/matipanel` mientras tu consola está dentro de ella. Si lo haces, ejecuta `cd /` antes de volver a clonar.

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
| `/etc/pam.d/matipanel` | Validación de contraseñas. |

## Preguntas frecuentes

### ¿Puedo usar un certificado propio en lugar del autofirmado?

Sí. Reemplaza `/etc/matipanel/cert.pem` y `/etc/matipanel/key.pem` por tu certificado y su clave (en formato PEM) y ejecuta `systemctl restart matipanel`.

### El instalador dice "groupadd: orden no encontrada"

Entraste como root con `su` sin guion. Las versiones recientes del instalador ya lo corrigen solas; si usas una antigua, ejecuta `su -` o `export PATH="$PATH:/usr/sbin:/sbin"` y repite.

### ¿Cómo sé qué versión tengo?

Abre **Menú → Cuenta → Acerca de**.

## Ver también

- [Primeros pasos](03-primeros-pasos.md)
- [Firewall](20-firewall.md)
- [Solución de problemas](90-solucion-de-problemas.md)
