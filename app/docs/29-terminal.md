# Terminal

La terminal te da una consola real del servidor dentro del navegador, igual que una sesión SSH. Se abre con **tu usuario de Linux**, nunca como `root`. La encuentras en **Menú → Herramientas → Terminal**.

## Abrir una sesión

1. Ve a **Menú → Herramientas → Terminal**.
2. La sesión se conecta sola. En la barra del menú verás `conectado` y el indicador de la barra de título se enciende.
3. Haz clic dentro de la ventana negra y empieza a escribir.

La barra de título muestra `usuario@servidor: Terminal MatiPanel`.

## Con qué usuario trabajas

- El shell se abre como el usuario con el que entraste al panel, en su carpeta personal (`/home/usuario`).
- Aunque el servicio MatiPanel corre como `root`, la terminal baja siempre a tus privilegios. Si no puede hacerlo, no abre la sesión.
- Se carga tu entorno normal (`/etc/profile`, `~/.profile` y `~/.bashrc`), así que tus alias y variables funcionan.
- Si tu shell no es `bash`, se abre tu shell de login sin el banner ni el prompt de MatiPanel.

> **Nota:** El `PATH` inicial no incluye `/usr/sbin`. Algunos comandos de administración no se encuentran si no usas `sudo` o la ruta completa (por ejemplo `/usr/sbin/ufw`).

## Usar sudo para tareas de administración

Para tareas que necesitan privilegios, antepone `sudo`:

```
sudo systemctl restart nginx
sudo apt update
```

- `sudo` te pide **tu** contraseña de Linux, no la de `root`.
- Solo funciona si tu usuario está en el grupo `sudo`. Si estás solo en el grupo `matipanel`, puedes usar el panel pero no `sudo` en la terminal. Pide a un administrador que te agregue en [Usuarios y grupos](17-usuarios.md).
- Para abrir un shell de `root` usa `sudo -i`. Sal con `exit` en cuanto termines.

> **Importante:** Trabaja como `root` solo el tiempo necesario. Un comando equivocado como `root` puede dañar el sistema sin aviso.

## Leer el banner y el prompt

Al abrir la sesión verás un banner con:

- El nombre del sistema operativo y tu usuario.
- Fecha y hora.
- **Uso de memoria**, **Uso de disco** (de `/`) y **Carga**, con el tiempo encendido.
- Un recordatorio: `Ctrl+C` interrumpe y `Ctrl+D` cierra la sesión.

El prompt tiene segmentos de color:

| Segmento | Significado |
|---|---|
| Tu usuario | Fondo verde azulado para un usuario normal. **Fondo rojo si eres `root`** (por ejemplo, después de `sudo -i`). |
| La ruta | Carpeta actual, con `›` como separador. `~` es tu carpeta personal. La última carpeta aparece resaltada. |
| Número en rojo | Aparece solo si el último comando falló. Es su código de salida. |

## Usar los menús de la ventana

| Menú | Opciones |
|---|---|
| **Archivo** | **Nueva sesión** (conecta si estás desconectado) y **Cerrar sesión**. |
| **Editar** | **Copiar** (`Ctrl+Shift+C`), **Pegar** (`Ctrl+Shift+V`) y **Seleccionar todo**. |
| **Ver** | **Aumentar letra**, **Reducir letra**, **Tamaño normal** y **Pantalla completa**. |
| **Terminal** | **Limpiar pantalla** y **Reiniciar sesión**. |

**Reiniciar sesión** cierra el shell actual y abre uno nuevo desde cero. Los procesos que estaban corriendo en esa sesión se terminan.

## Copiar y pegar

- **Copiar:** selecciona el texto con el mouse y pulsa `Ctrl+Shift+C`. Verás **Copiado al portapapeles**.
- **Pegar:** pulsa `Ctrl+Shift+V`.

`Ctrl+C` **no** copia: en la terminal interrumpe el comando que se está ejecutando.

Si el navegador bloquea el acceso al portapapeles, verás un aviso. Permite el acceso al portapapeles para el sitio del panel, o usa el menú contextual del navegador (clic derecho) para pegar.

## Cambiar el tamaño de letra y usar pantalla completa

- Usa los botones **−** y **+** de la barra de título, o el menú **Ver**.
- El tamaño va de 9 a 28 puntos. **Tamaño normal** vuelve a 14.
- El tamaño se guarda en este navegador. En otro navegador o equipo verás el tamaño por defecto.
- Pulsa **⛶** (o **Ver → Pantalla completa**) para usar toda la pantalla. Sal con `Esc` o pulsando de nuevo el botón.

La ventana se adapta sola cuando cambias el tamaño del navegador. Guarda las últimas 5000 líneas para desplazarte hacia arriba.

## Cerrar la sesión correctamente

- Escribe `exit` o pulsa `Ctrl+D`, o usa **Archivo → Cerrar sesión**.
- Verás `── sesión cerrada · Archivo › Nueva sesión para reconectar ──`.
- Para volver a empezar, usa **Archivo → Nueva sesión**.

> **Importante:** Si cierras la pestaña, cambias de página en el panel o se corta la red, el shell se cierra y los comandos en curso se terminan. Para tareas largas (copias grandes, actualizaciones), usa `tmux` o `screen` si están instalados, o `nohup comando &`.

## Preguntas frecuentes

### `sudo` dice que mi usuario no está en el archivo sudoers.

Tu usuario no pertenece al grupo `sudo`. Un administrador puede agregarte desde [Usuarios y grupos](17-usuarios.md). Después cierra la sesión de la terminal y abre una nueva.

### Pulsé Ctrl+C para copiar y se canceló mi comando.

En la terminal, `Ctrl+C` interrumpe. Para copiar usa `Ctrl+Shift+C`.

### Escribo `ufw` o `useradd` y dice "command not found".

Esos comandos están en `/usr/sbin`, que no está en el `PATH` de tu usuario. Usa `sudo ufw status` o la ruta completa (`/usr/sbin/ufw`).

### La terminal se desconectó mientras corría un proceso largo. ¿Sigue corriendo?

No. Al cerrarse la sesión, el shell y sus procesos se terminan. Vuelve a lanzarlo dentro de `tmux` o `screen`, o con `nohup`.

## Ver también

- [Usuarios y grupos](17-usuarios.md)
- [Seguridad y 2FA](30-seguridad-2fa.md)
- [Archivos](26-archivos.md)
- [Paquetes](27-paquetes.md)
- [Logs](28-logs.md)
