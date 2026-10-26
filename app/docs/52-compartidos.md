# Recursos compartidos (Samba y NFS)

Este módulo convierte el servidor en un servidor de archivos. Con **Samba** compartes carpetas con equipos Windows, macOS y Linux de la red (se abren como `\\servidor\recurso`). Con **NFS** compartes carpetas con otros servidores Linux, que las montan como si fueran un disco local. Lo encuentras en **Menú → Servidores → Compartidos**.

La pantalla tiene dos pestañas: **Samba (Windows y red local)** y **NFS (Linux)**.

## ¿Samba o NFS?

| | Samba (SMB) | NFS |
|---|---|---|
| **Clientes típicos** | PC con Windows, Mac, escritorios Linux | Servidores Linux |
| **Cómo se entra** | Usuario y contraseña de Samba | Por la IP o red del cliente (sin contraseña) |
| **Puertos** | 445/TCP y 139/TCP (137-138/UDP para buscar equipos) | 2049/TCP |
| **Uso habitual** | Carpetas de oficina, documentos de departamento | Datos compartidos entre servidores, respaldos |

> **Importante:** ninguno de los dos debe abrirse a internet. Permite sus puertos en el [Firewall](20-firewall.md) **solo** desde tu red interna (por ejemplo, Origen `192.168.1.0/24`).

## Permisos de Linux y permisos del recurso

Para leer o escribir un archivo compartido se deben cumplir **dos** condiciones a la vez:

1. **El recurso lo permite:** en Samba, que el recurso tenga **Permitir escritura** y que el usuario esté en **Usuarios permitidos**; en NFS, que el cliente tenga la opción `rw`.
2. **Linux lo permite:** el usuario con el que se accede (en Samba, el usuario de Linux de la cuenta; en NFS, el UID que envía el cliente) debe tener permiso sobre la carpeta y sus archivos.

Si falta cualquiera de las dos, el cliente verá **Acceso denegado**. Por eso la tabla de recursos muestra debajo de cada carpeta su propietario, grupo y permisos de Linux (por ejemplo `root:contabilidad 2775`).

> **Consejo:** la forma más sencilla de dar acceso de escritura a varias personas es crear un grupo de Linux (por ejemplo `contabilidad`) en [Usuarios y grupos](17-usuarios.md), meter en él a los usuarios, y crear la carpeta con grupo `contabilidad` y permisos `2775`. El `2` inicial hace que los archivos nuevos hereden el grupo.

## Samba

### Instalar Samba

Si Samba no está instalado verás la tarjeta **Comparte carpetas con Samba**.

1. Pulsa **Instalar**.
2. Deja marcada **Instalar también smbclient** si quieres poder hacer pruebas desde el servidor.
3. Pulsa **Instalar** y espera. Al terminar aparece el resto de la pestaña.

Samba queda en marcha con la configuración por defecto de Debian, sin carpetas compartidas.

> **Nota:** la instalación automática funciona en Debian y Ubuntu. En otras distribuciones el panel muestra el comando que debes ejecutar en la [Terminal](29-terminal.md).

### Ver el estado y las conexiones

El indicador de arriba muestra si Samba está activo, la versión y cuántas sesiones y archivos abiertos hay. A la derecha están **Reiniciar** y **Detener** (o **Iniciar** si está detenido).

- **Verde — Samba activo.**
- **Amarillo — Samba detenido** o no instalado.
- **Rojo — Error:** el servicio falló al arrancar. Revisa la configuración global o abre los logs.

La tarjeta **Conexiones activas** muestra quién está conectado, desde qué equipo, con qué versión del protocolo, qué recursos está usando (**Recursos en uso**) y qué **Archivos abiertos** tiene.

> **Nota:** antes de **Iniciar** o **Reiniciar**, el panel valida `smb.conf` con `testparm`. Si tiene errores, no se aplica y verás la salida.

### Configuración global

En la tarjeta **Configuración global** puedes cambiar:

| Campo | Qué significa |
|---|---|
| **Grupo de trabajo (workgroup)** | Nombre del grupo de Windows. Normalmente `WORKGROUP`. Máximo 15 caracteres. |
| **Nombre NetBIOS** | Nombre con el que el servidor aparece en la red. Vacío = el nombre del equipo. Máximo 15 caracteres. |
| **Descripción del servidor** | Texto que ven los clientes. `%h` se reemplaza por el nombre del equipo. |
| **Seguridad** | Deja **user**: cada persona entra con su usuario de Samba. Las otras opciones son para unirse a un dominio. |
| **Invitados (map to guest)** | **Never**: nadie entra sin contraseña. **Bad User**: si el usuario no existe, entra como invitado (necesario para recursos con acceso de invitados). |
| **Protocolo mínimo** | Versión más antigua de SMB aceptada. **SMB3** es más seguro, pero equipos muy viejos (Windows 7 sin actualizar) no podrán conectarse. |

Pulsa **Guardar configuración global**. El panel solo escribe lo que cambiaste, guarda un respaldo en `/etc/samba/smb.conf.matipanel.bak`, valida con `testparm` y recarga Samba.

Si necesitas una opción que no está en el formulario, pulsa **Editar smb.conf** para abrir el archivo en el editor de [Archivos](26-archivos.md).

### Crear un recurso compartido

1. Pulsa **+ Nuevo recurso**.
2. Completa los campos (ver la tabla).
3. Pulsa **Guardar**.
4. Si la carpeta no existe, el panel pregunta si quieres crearla y con qué **Propietario**, **Grupo** y **Permisos (octal)**. Ajusta los valores y pulsa **Crear carpeta y guardar**.

| Campo | Qué poner |
|---|---|
| **Nombre del recurso** | Lo que verán los usuarios: `\\servidor\nombre`. Hasta 40 letras, números, `_`, `-` o `$`, sin espacios. Si termina en `$` (por ejemplo `admin$`), el recurso queda oculto en la lista de Windows. |
| **Carpeta en el servidor** | Ruta absoluta, por ejemplo `/srv/samba/documentos`. No se permiten carpetas del sistema (`/etc`, `/usr`, `/root`, `/proc`…). |
| **Descripción** | Opcional. Texto que ven los usuarios. |
| **Permitir escritura** | Si está desmarcado, el recurso es de solo lectura. |
| **Acceso de invitados** | Cualquiera puede entrar sin contraseña. Requiere **Invitados** en **Bad User**. |
| **Visible en la red** | Si se desmarca, el recurso no aparece al explorar el servidor, pero se puede abrir escribiendo su ruta. |
| **Usuarios permitidos** | Usuarios y grupos separados por espacios o comas. Los grupos llevan `@` delante: `juan ana @contabilidad`. Vacío = cualquier usuario de Samba. |
| **Pueden escribir aunque el recurso sea de solo lectura** | Usuarios o `@grupos` con permiso de escritura en un recurso de solo lectura. Ejemplo: todos leen, solo `@jefes` escriben. |
| **Permisos de archivos nuevos / carpetas nuevas** | Máscaras en octal. `0664` y `0775` permiten que el grupo modifique lo que otros crean. |

Todos los usuarios y grupos que escribas deben existir en Linux; si no, verás qué nombre falta.

Antes de aplicar, el panel valida la configuración con `testparm`. Si la rechaza, `smb.conf` no se toca, se deshace la carpeta recién creada y verás la salida con el error.

### Editar o eliminar un recurso

- **Editar** abre el mismo formulario. El nombre no se puede cambiar; para renombrar, crea uno nuevo y elimina el anterior.
- **Eliminar** quita el recurso de `smb.conf`. **Los archivos de la carpeta no se borran.**

Los recursos `[homes]` (carpeta personal de cada usuario) y `[printers]` (impresoras) aparecen con la etiqueta **especial**. En ellos la carpeta es opcional: si la dejas vacía, no se cambia.

> **Nota:** el panel conserva los comentarios y el resto de `smb.conf` intactos. Solo cambia las líneas de los parámetros que editas.

### Usuarios de Samba

Samba tiene sus propias contraseñas, distintas de las de Linux. Cada cuenta de Samba corresponde a un usuario de Linux que ya debe existir.

**Agregar un usuario:**

1. Si el usuario aún no existe en Linux, créalo en [Usuarios y grupos](17-usuarios.md). No necesita acceso por SSH.
2. En la tarjeta **Usuarios de Samba**, pulsa **+ Agregar usuario**.
3. Elige el **Usuario de Linux** (aparecen los que tienen UID 1000 o mayor y aún no tienen cuenta de Samba).
4. Escribe la **Contraseña de Samba** dos veces (mínimo 8 caracteres) y pulsa **Agregar**.

**Otras acciones** en cada fila:

| Botón | Qué hace |
|---|---|
| **Contraseña** | Cambia la contraseña de Samba. No toca la de Linux. |
| **Deshabilitar / Habilitar** | Bloquea o permite el acceso del usuario a los recursos, sin borrar la cuenta. |
| **Eliminar** | Borra la cuenta de Samba. El usuario de Linux y sus archivos se conservan. |

Si una fila muestra **sin cuenta Linux**, el usuario de Linux fue borrado; elimina también su cuenta de Samba.

### Conectarse desde Windows

1. Abre el **Explorador de archivos**.
2. En la barra de direcciones escribe `\\IP-del-servidor\recurso`, por ejemplo `\\192.168.1.5\documentos`, y pulsa Enter.
3. Escribe el usuario y la contraseña de Samba. Marca **Recordar mis credenciales** si quieres.

Para que aparezca como una unidad (por ejemplo `Z:`), haz clic derecho en **Este equipo → Conectar a unidad de red**, o en CMD:

```
net use Z: \\192.168.1.5\documentos /user:juan /persistent:yes
```

El botón **Conectar** de cada recurso muestra estos comandos ya completados.

### Conectarse desde Linux o macOS

- **Escritorio Linux (Archivos/Nautilus) o macOS (Finder → Ir → Conectarse al servidor):** `smb://192.168.1.5/documentos`.
- **Terminal Linux:**

```bash
apt install cifs-utils
mkdir -p /mnt/documentos
mount -t cifs //192.168.1.5/documentos /mnt/documentos -o username=juan,uid=$(id -u juan),gid=$(id -g juan)
```

Para montarlo al arrancar, guarda las credenciales en `/root/.smbcred` (líneas `username=juan` y `password=…`, con `chmod 600`) y agrega a `/etc/fstab`:

```
//192.168.1.5/documentos  /mnt/documentos  cifs  credentials=/root/.smbcred,uid=1000,gid=1000,_netdev  0  0
```

## NFS

### Instalar el servidor NFS

1. Abre la pestaña **NFS (Linux)**.
2. En la tarjeta **Comparte carpetas con NFS**, pulsa **Instalar** y confirma.

Se instala `nfs-kernel-server`. No se exporta ninguna carpeta hasta que la agregues.

### Ver el estado

El indicador muestra si el servidor NFS está activo, las versiones de NFS habilitadas, cuántas carpetas están exportadas y cuántos clientes están conectados. Debajo encontrarás:

- **Exportaciones:** lo que dice `/etc/exports`, con el estado **exportada** (en uso ahora) o **no activa**.
- **Clientes conectados:** equipos con conexión abierta al puerto 2049.
- **Exportado ahora:** salida de `exportfs -v`, con las opciones efectivas (incluidas las que NFS añade por defecto).

### Exportar una carpeta

1. Pulsa **+ Nueva exportación**.
2. En **Carpeta a exportar** escribe la ruta, por ejemplo `/srv/nfs/datos`. Sin espacios.
3. En cada fila de **Clientes autorizados y opciones** escribe el cliente y sus opciones. Usa **+ Agregar cliente** para más filas y **✕** para quitar una.
4. Pulsa **Guardar**. Si la carpeta no existe, el panel ofrece crearla igual que en Samba.

**Clientes válidos:**

| Ejemplo | Significa |
|---|---|
| `192.168.1.20` | Un solo equipo |
| `192.168.1.0/24` | Toda una red |
| `pc01.oficina.local` | Un equipo por nombre |
| `*.oficina.local` | Todos los equipos de ese dominio |
| `*` | Cualquiera (no recomendado) |

**Opciones permitidas** (separadas por comas; la sugerida es `rw,sync,no_subtree_check`):

| Opción | Qué hace |
|---|---|
| `rw` / `ro` | Lectura y escritura / solo lectura. |
| `sync` / `async` | `sync` escribe en disco antes de responder (más seguro); `async` es más rápido pero puede perder datos si se corta la luz. |
| `no_subtree_check` / `subtree_check` | Deja `no_subtree_check` (recomendado). |
| `root_squash` / `no_root_squash` | Con `root_squash` (por defecto) el root del cliente se trata como el usuario anónimo. `no_root_squash` le da control total: úsalo solo con equipos de confianza. |
| `all_squash` | Todos los usuarios del cliente se tratan como el usuario anónimo. |
| `anonuid=N` / `anongid=N` | UID y GID del usuario anónimo (por ejemplo, el de `www-data` es 33). |
| `secure` / `insecure` | `insecure` acepta clientes que usan puertos altos (algunos macOS lo necesitan). |

Al guardar, el panel hace un respaldo en `/etc/exports.matipanel.bak` y aplica con `exportfs -ra`. Si `exportfs` falla, restaura el archivo anterior y muestra el error.

> **Importante:** NFS no pide contraseña: confía en la IP del cliente y en el UID que envía. Autoriza solo IP o redes concretas y protege el puerto 2049 con el [Firewall](20-firewall.md).

### Editar o eliminar una exportación

- **Editar** permite cambiar la carpeta, los clientes y las opciones.
- Si una exportación usa opciones avanzadas que el formulario no conoce (por ejemplo `fsid=0` o `crossmnt`), verás **Opciones avanzadas: edítala en el archivo** y el botón **Editar archivo**, que abre `/etc/exports` en el editor. Después de editar el archivo a mano, aplica los cambios con `exportfs -ra` en la [Terminal](29-terminal.md).
- **Eliminar** deja de exportar la carpeta. Los archivos no se borran.

### Montar la carpeta en otro servidor Linux

El botón **Montar** de cada exportación muestra los comandos ya completados. En el cliente, como root:

```bash
apt install nfs-common
showmount -e 192.168.1.5
mkdir -p /mnt/datos
mount -t nfs 192.168.1.5:/srv/nfs/datos /mnt/datos
```

Para montarla al arrancar, agrega a `/etc/fstab` del cliente:

```
192.168.1.5:/srv/nfs/datos  /mnt/datos  nfs  defaults,_netdev  0  0
```

> **Nota:** en NFS los permisos se comparan por **número** de usuario (UID), no por nombre. Si `juan` tiene UID 1000 en el cliente y en el servidor, todo coincide; si no, verá los archivos como de otro usuario.

## Preguntas frecuentes

### Windows dice "Acceso denegado" al copiar un archivo

Revisa las dos capas de permisos: que el recurso tenga **Permitir escritura** (o el usuario esté en **Pueden escribir…**) y que, en Linux, el usuario tenga permiso de escritura en la carpeta. La columna **Carpeta** muestra propietario, grupo y permisos. Lo habitual es poner la carpeta con el grupo correcto y permisos `2775`.

### Windows pide usuario y contraseña y no acepta la mía

Usa el usuario y la contraseña **de Samba**, no la de Windows. Comprueba que el usuario aparezca como **activo** en **Usuarios de Samba** y que esté en **Usuarios permitidos** del recurso (si la lista no está vacía). Si Windows guardó credenciales viejas, bórralas en **Panel de control → Administrador de credenciales**.

### El servidor no aparece en "Red" de Windows

Windows moderno ya no muestra los equipos de la red de forma fiable. Escribe directamente `\\IP-del-servidor` en la barra de direcciones del Explorador.

### Windows dice que no puede conectarse por SMB1

El cliente es muy antiguo y solo habla SMB1, que Samba desactiva por seguridad. Actualiza el equipo; no se recomienda reactivar SMB1.

### Guardé un recurso y sale "testparm rechazó la configuración"

`smb.conf` no se modificó. Lee la salida: indica qué parámetro tiene el problema. Si editaste el archivo a mano antes, corrígelo con **Editar smb.conf** o restaura `/etc/samba/smb.conf.matipanel.bak` desde [Archivos](26-archivos.md).

### El cliente NFS dice "access denied by server while mounting"

La IP del cliente no coincide con ningún cliente autorizado de la exportación, o la exportación aparece como **no activa**. Revisa la IP real del cliente en **Clientes conectados** o con `ip a` en el cliente.

### En NFS los archivos aparecen como "nobody"

El usuario del cliente no tiene el mismo UID que en el servidor, o se está aplicando `root_squash`/`all_squash`. Crea los usuarios con el mismo UID en ambos equipos, o usa `anonuid`/`anongid`.

### ¿Eliminar un recurso o una exportación borra los archivos?

No. Solo quita la configuración. La carpeta y su contenido quedan en el disco.

## Ver también

- [Usuarios y grupos](17-usuarios.md)
- [Firewall](20-firewall.md)
- [Archivos](26-archivos.md)
- [Discos](15-discos.md)
- [Servicios](13-servicios.md)
- [Solución de problemas](90-solucion-de-problemas.md)
