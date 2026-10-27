# Permisos del panel

Decide **qué puede hacer cada persona** dentro del panel. Quién puede *entrar* lo siguen decidiendo los grupos del sistema (`matipanel`, `sudo`, `wheel` o `admin`, ver [Usuarios](17-usuarios.md)); aquí eliges su rol. Lo encuentras en **Menú → Red y acceso → Permisos del panel**. Solo los **administradores** ven esta pantalla.

## Roles

| Rol | Qué puede hacer |
|---|---|
| **Administrador** | Todo, incluida esta pantalla. |
| **Solo lectura** | Ver los módulos, sin cambiar nada (ningún botón que modifique el servidor funciona). No tiene **Terminal**, **Archivos** ni **Explorador BD**, ni puede descargar volcados de bases de datos, capturas de tráfico ni el `.env` de las apps: con esas herramientas se puede leer cualquier archivo del servidor. |
| **Personalizado** | Acceso completo (ver y cambiar) **solo** a los módulos que marques, por ejemplo Despliegues, Docker y Logs para un desarrollador. |
| **Sin acceso** | No puede entrar al panel, aunque esté en el grupo. |

Todos, sea cual sea su rol, pueden ver el **Dashboard** y la **Ayuda**, y cambiar su **Apariencia** y su **2FA**. El menú lateral solo muestra los módulos que cada uno puede usar; si alguien abre una dirección prohibida, vuelve al Dashboard con un aviso.

> **Nota:** Al instalar, todos los usuarios son administradores, igual que antes de existir esta pantalla. Nada cambia hasta que guardes permisos.

## Asignar roles

1. En la tabla aparecen los usuarios que pueden entrar por grupo (con sus grupos) y los que ya tienen un rol guardado.
2. Elige el **Rol** de cada uno. Si es **Personalizado**, marca sus **Módulos**.
3. Elige el **Rol para los usuarios que no estén en la lista**: se aplica a quienes dejas con «(por defecto)» y a quien se agregue más adelante a los grupos. Para mayor seguridad usa **Solo lectura** o **Sin acceso**, y da roles explícitos a cada administrador.
4. Pulsa **Guardar permisos**.

Los cambios se aplican en la siguiente acción de cada usuario. **Sin acceso** cierra su sesión de inmediato.

> **Importante:** No puedes quitarte a ti mismo el rol de administrador (siempre debe quedar al menos uno: tú). Si necesitas hacerlo, pídeselo a otro administrador.

> **Consejo:** Todo lo que hace cada persona queda en la [Auditoría](32-auditoria.md), incluidos los cambios de permisos.

## Preguntas frecuentes

### ¿El rol del panel cambia sus permisos en el servidor por SSH?

No. Solo limita lo que hace **dentro del panel**. Si la persona está en el grupo `sudo`, por SSH sigue siendo administradora del servidor. Para un acceso solo al panel, agrégala al grupo `matipanel` (no a `sudo`) y dale aquí el rol que corresponda.

### Un usuario aparece con «sin grupo»

Tiene un rol guardado pero ya no está en ningún grupo con acceso: no puede entrar. Puedes dejarlo (si vuelve al grupo recupera ese rol) o cambiarlo.

## Ver también

- [Usuarios y grupos](17-usuarios.md)
- [Seguridad y 2FA](30-seguridad-2fa.md)
- [Auditoría](32-auditoria.md)
