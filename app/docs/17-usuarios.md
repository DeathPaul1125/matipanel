# Usuarios y grupos

Este módulo administra las cuentas de usuario y los grupos del sistema Linux: crear y eliminar usuarios, cambiar contraseñas, bloquear cuentas, asignar grupos y decidir quién puede entrar al panel. Lo encuentras en **Menú → Red y acceso → Usuarios y grupos**.

## Quién puede entrar al panel

MatiPanel usa las cuentas del propio servidor. Para iniciar sesión necesitas:

1. Un usuario del sistema con contraseña válida (la misma que usas por SSH).
2. Pertenecer al menos a uno de estos grupos: `matipanel`, `sudo`, `wheel` o `admin`. Cuenta tanto si es el grupo primario como si es un grupo adicional.

| Grupo | Qué permite |
|---|---|
| `matipanel` | Entrar al panel. No da permisos de `sudo` en la terminal del sistema |
| `sudo` | Usar `sudo` en el sistema **y** entrar al panel (en Debian es el grupo de administradores) |
| `wheel`, `admin` | Equivalentes a `sudo` en otras distribuciones; también dan acceso al panel |

> **Importante:** El panel funciona con permisos de `root`. Cualquier persona con acceso al panel tiene, en la práctica, control total del servidor, aunque solo esté en el grupo `matipanel`. Da acceso solo a personal de confianza.

> **Nota:** El usuario `root` no puede entrar al panel por defecto, porque no pertenece a ninguno de esos grupos. Usa tu cuenta personal.

### Comportamiento del inicio de sesión

- Si el usuario no existe, la contraseña es incorrecta o no pertenece a un grupo permitido, verás el mismo mensaje: "Usuario o contraseña incorrectos, o sin permiso para el panel."
- Tras 5 intentos fallidos en 5 minutos verás "Demasiados intentos fallidos. Espera unos minutos."
- La pertenencia a los grupos se revisa en cada clic. Si quitas a alguien de los grupos permitidos, pierde el acceso al panel de inmediato, aunque tenga una sesión abierta.

Para reforzar el acceso con un segundo factor, consulta [Seguridad y 2FA](30-seguridad-2fa.md).

## Dar acceso al panel a otra persona

1. Crea su usuario (ver "Crear un usuario") o búscalo en la pestaña **Usuarios**.
2. Haz clic en **Grupos** en su fila.
3. Marca `matipanel` (solo panel) o `sudo` (panel y administración por terminal).
4. Haz clic en **Guardar**.

La persona ya puede iniciar sesión con su usuario y contraseña.

## Ver los usuarios

La pestaña **Usuarios** muestra una tabla con:

| Columna | Contenido |
|---|---|
| **Usuario** | Nombre de la cuenta y, debajo, su nombre completo. Tu propia cuenta lleva la etiqueta `tú` |
| **UID** | Número de usuario |
| **Grupos** | Grupo primario y, en gris, los grupos adicionales |
| **Home** | Carpeta personal |
| **Shell** | Intérprete de comandos al iniciar sesión |
| **Estado** | Estado de la contraseña y tipo de cuenta |
| **Acciones** | Botones de gestión |

### Etiquetas de estado

| Etiqueta | Significado |
|---|---|
| `activo` | Tiene contraseña y no está bloqueado |
| `bloqueado` | La contraseña está bloqueada |
| `sin contraseña` | No tiene contraseña: no puede entrar con contraseña (sí con llave SSH, si tiene una) |
| `?` | El panel no pudo leer `/etc/shadow` |
| `regular` / `sistema` | Cuenta de persona (UID 1000 o más) o cuenta de servicio (UID menor de 1000) |

### Filtrar la lista

- Escribe en **Filtrar usuario…** para buscar por nombre de usuario o nombre completo.
- Usa el selector para ver **Todos**, **Regulares (UID ≥ 1000, sin nobody)** (opción por defecto) o **Sistema**.

> **Nota:** Por defecto solo ves las cuentas regulares. Para ver `root`, `www-data` u otras cuentas de servicio, elige **Todos** o **Sistema**. La cuenta `nobody` (UID 65534) se cuenta como de sistema aunque su UID sea alto.

## Crear un usuario

1. En la pestaña **Usuarios**, haz clic en **+ Nuevo usuario**.
2. Completa el formulario **Nuevo usuario**:

| Campo | Descripción |
|---|---|
| **Usuario** | Nombre de la cuenta. Minúsculas, números, `_` y `-`; debe empezar con letra o `_`; máximo 32 caracteres. Ejemplo: `jperez` |
| **Nombre completo** | Opcional. Ejemplo: `Juan Pérez` |
| **Shell** | Por defecto `/bin/bash`. Elige `/usr/sbin/nologin` si la cuenta no debe abrir sesiones |
| **Contraseña** | Opcional. Sin ella, la cuenta queda sin acceso por contraseña |
| **Repetir contraseña** | Debe coincidir |
| **Grupos adicionales (separados por coma)** | Ejemplo: `docker, www-data`. Todos deben existir |
| **Crear directorio home** | Marcado por defecto. Crea `/home/usuario` |
| **Administrador (grupo sudo)** | Agrega el usuario al grupo `sudo` (o `wheel` si no existe `sudo`) |

3. Haz clic en **Crear**.

En Debian, al crear un usuario se crea también un grupo primario con su mismo nombre.

> **Importante:** Marcar **Administrador (grupo sudo)** también le da acceso al panel.

> **Nota:** El panel no exige una longitud mínima de contraseña. Usa contraseñas largas y únicas, de al menos 12 caracteres.

Si algo falla verás un mensaje con el motivo, por ejemplo "Las contraseñas no coinciden", "El usuario ya existe" o "Algún grupo no existe o es inválido". El formulario queda abierto para que lo corrijas.

## Cambiar la contraseña de un usuario

1. Haz clic en **Contraseña** en la fila del usuario.
2. Escribe la **Nueva contraseña** y repítela.
3. Haz clic en **Guardar**.

El cambio es inmediato. Si cambias tu propia contraseña, la nueva también es la del panel.

## Bloquear o desbloquear un usuario

1. Haz clic en **Bloquear** en la fila del usuario.
2. El diálogo **Bloquear contraseña** advierte que las llaves SSH siguen funcionando. Confirma con **Bloquear**.
3. Para revertirlo, haz clic en **Desbloquear** (no pide confirmación).

Bloquear impide entrar con contraseña: por SSH, en consola y en el panel. La contraseña se conserva y vuelve a funcionar al desbloquear.

> **Importante:** Bloquear no corta las sesiones abiertas ni impide entrar con llave SSH. Para deshabilitar una cuenta por completo, bloquéala **y** cambia su **Shell** a `/usr/sbin/nologin`.

> **Nota:** No puedes bloquear a `root` ni a tu propio usuario. Verás "No se puede bloquear root ni el usuario con sesión activa".

## Cambiar la shell de un usuario

1. Haz clic en **Shell** en la fila del usuario.
2. Elige la shell en la lista. Aparecen las de `/etc/shells` más `/usr/sbin/nologin` y `/bin/false`.
3. Haz clic en **Guardar**.

Con `/usr/sbin/nologin` o `/bin/false` el usuario no puede abrir una terminal, pero sus servicios o transferencias pueden seguir funcionando.

## Cambiar los grupos de un usuario

1. Haz clic en **Grupos** en la fila del usuario.
2. La ventana **Grupos de *usuario*** muestra el grupo primario (no se puede cambiar aquí) y una lista con todos los grupos.
3. Marca los grupos adicionales que debe tener y desmarca los que no.
4. Haz clic en **Guardar**.

La lista marcada **reemplaza** todos los grupos adicionales del usuario.

> **Nota:** Si el usuario tiene sesiones SSH abiertas, los grupos nuevos se aplican cuando vuelva a iniciar sesión. El acceso al panel sí cambia al instante.

## Eliminar un usuario

1. Haz clic en **Eliminar** en la fila del usuario.
2. Si quieres borrar también sus archivos personales, marca **Eliminar también su directorio home y correo (userdel -r)**.
3. Haz clic en **Eliminar**.
4. Confirma en el diálogo **Eliminar usuario** con **Eliminar**.

> **Importante:** Con la casilla marcada, la carpeta `/home/usuario` se borra y no se puede recuperar. Haz un [respaldo](16-respaldos.md) antes si tiene información útil.

> **Nota:** Si el home del usuario es una carpeta del sistema (por ejemplo `www-data` con `/var/www`, o cuentas con `/`, `/bin` o `/var/lib`), el panel no borra el usuario si marcaste la casilla del home: verás "… una carpeta del sistema: elimina el usuario sin borrar su home".

> **Nota:** El botón **Eliminar** no aparece para `root` ni para tu propio usuario. Si el usuario tiene procesos en ejecución, la eliminación falla con un error; termínalos primero en [Procesos](12-procesos.md).

## Protecciones contra quedarte fuera del panel

El panel evita que te quites el acceso a ti mismo por accidente:

- No puedes eliminar ni bloquear tu propio usuario ni a `root`.
- No puedes quitarte del último grupo que te da acceso (`matipanel`, `sudo`, `wheel` o `admin`), ni con el botón **Grupos** de la pestaña **Usuarios** ni con la **×** de la pestaña **Grupos**. Verás "Perderías el acceso al panel (matipanel, sudo, wheel, admin)".
- No se pueden eliminar los grupos `matipanel`, `sudo`, `wheel` ni `admin`, ni ningún grupo del sistema (GID menor de 1000).

> **Importante:** Estas protecciones solo cubren tu propia cuenta. Otro administrador sí puede quitarte el acceso, y tú se lo puedes quitar a él. Mantén siempre al menos dos cuentas con acceso al panel.

## Ver y crear grupos

1. Abre la pestaña **Grupos**.
2. La tabla muestra **Grupo**, **GID**, **Miembros** y **Acciones**.
3. Para crear uno, haz clic en **+ Nuevo grupo**, escribe el **Nombre** (mismas reglas que un usuario) y haz clic en **Crear**.

> **Nota:** Por defecto el filtro muestra **Todos** los grupos, incluidos `sudo` y `matipanel` (que son grupos del sistema). Elige **Regulares (GID ≥ 1000)** para ver solo los grupos creados para personas.

> **Nota:** La columna **Miembros** muestra los miembros adicionales. Un usuario cuyo grupo primario es ese grupo no aparece en la lista.

## Agregar o quitar miembros de un grupo

### Agregar

1. En la pestaña **Grupos**, haz clic en **+ Miembro** en la fila del grupo.
2. Elige el usuario en la ventana **Añadir miembro a *grupo***.
3. Haz clic en **Añadir**.

### Quitar

1. Haz clic en la **×** junto al nombre del miembro.
2. Confirma en el diálogo **Quitar del grupo** con **Quitar**.

## Eliminar un grupo

1. Haz clic en **Eliminar** en la fila del grupo. El botón solo aparece en grupos con GID de 1000 o más.
2. Confirma con **Eliminar**.

Si el grupo es el grupo primario de algún usuario, la eliminación falla con un error. Cambia o elimina antes ese usuario.

## Preguntas frecuentes

### ¿Cómo doy acceso al panel sin darle sudo en el servidor?

Agrégalo al grupo `matipanel`. En la pestaña **Grupos**, cambia el filtro a **Todos**, busca `matipanel` y usa **+ Miembro**. Recuerda que, aun así, desde el panel podrá hacer cualquier cosa en el servidor.

### Bloqueé a un usuario pero sigue entrando por SSH. ¿Por qué?

Bloquear solo afecta a la contraseña. Si entra con llave SSH, sigue pudiendo. Cambia su shell a `/usr/sbin/nologin` o borra su archivo `~/.ssh/authorized_keys` desde [Archivos](26-archivos.md). Si tiene una sesión abierta, termina sus procesos en [Procesos](12-procesos.md).

### Un compañero dejó la institución. ¿Qué hago con su cuenta?

1. Bloquéala con **Bloquear** y cambia su shell a `/usr/sbin/nologin`.
2. Quítala de los grupos `sudo` y `matipanel`.
3. Respalda su carpeta personal si contiene algo útil.
4. Cuando ya no la necesites, elimínala con **Eliminar**.

### ¿Por qué no aparece el usuario root en la lista?

El filtro por defecto muestra solo usuarios regulares. Elige **Todos** o **Sistema** en el selector.

## Ver también

- [Seguridad y 2FA](30-seguridad-2fa.md)
- [Primeros pasos](03-primeros-pasos.md)
- [Instalación](02-instalacion.md)
- [Procesos](12-procesos.md)
- [Tareas programadas (cron)](14-cron.md)
- [Respaldos](16-respaldos.md)
