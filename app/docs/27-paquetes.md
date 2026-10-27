# Paquetes

Este módulo instala, elimina y actualiza el software del servidor con APT, y controla las actualizaciones automáticas de seguridad. Lo encuentras en **Menú → Sistema → Paquetes**.

## Antes de empezar

- En Debian el título de la página es **Paquetes APT**. Todas las funciones de esta guía son para APT.
- Las operaciones se ejecutan con `apt-get` sin preguntas: no verás menús interactivos. Si un paquete trae un archivo de configuración nuevo, se conserva el que ya tienes.
- Las operaciones largas son síncronas: la página espera hasta que terminen (máximo 30 minutos). Verás **Puede tardar varios minutos, no cierres la página.**
- Mientras una operación está en curso, los botones de paquetes se desactivan. Si intentas otra, verás **Ya hay una operación de paquetes en curso**.
- La salida completa de cada operación aparece en un recuadro arriba de las pestañas.

> **Nota:** En servidores RHEL/Rocky la búsqueda y las actualizaciones automáticas no están disponibles. Este manual asume Debian.

## Ver el resumen de paquetes

Arriba hay tres indicadores:

| Indicador | Significado |
|---|---|
| **Instalados** | Total de paquetes instalados. |
| **Actualizables** | Paquetes con una versión nueva disponible. |
| **De seguridad** | Cuántos de esos vienen del repositorio de seguridad de Debian. |

Los números dependen de las listas de paquetes descargadas. Pulsa **Actualizar listas** para tener datos al día.

## Buscar actualizaciones disponibles

1. Abre la pestaña **Actualizaciones**.
2. Pulsa **Actualizar listas** (`apt-get update`). No pide confirmación.
3. Espera a ver **refresh: completado ✓**.
4. Revisa la tabla **Actualizaciones disponibles**: **Paquete**, **Nueva versión**, **Origen** y **Tipo** (`seguridad` en rojo o `normal`).

La tabla muestra como máximo 50 paquetes. Si hay más, verás **Mostrando 50 de N. Usa "Actualizar todo" para el resto.**

## Actualizar todos los paquetes

1. Pulsa **Actualizar listas** primero.
2. Pulsa **Actualizar todo**.
3. Confirma en **Actualizar todo** con **Actualizar**.
4. Espera el resultado.

**Actualizar todo** ejecuta `apt-get upgrade`. No instala ni elimina paquetes para resolver dependencias. Por eso algunos paquetes pueden quedar retenidos ("kept back").

> **Consejo:** Antes de actualizar un servidor en producción, haz un respaldo ([Respaldos](16-respaldos.md)) y elige un horario de poco uso. Algunos servicios se reinician durante la actualización.

## Ejecutar full-upgrade

1. Pulsa **Full-upgrade**.
2. Lee el aviso: **Puede instalar o eliminar paquetes para resolver dependencias.**
3. Confirma en **Full-upgrade** con **Ejecutar**.

Ejecuta `apt-get dist-upgrade`. Úsalo cuando **Actualizar todo** deja paquetes retenidos, por ejemplo un kernel nuevo. Revisa bien la salida: puede eliminar paquetes.

> **Importante:** Si se instala un kernel nuevo, debes reiniciar el servidor para usarlo. Puedes hacerlo desde [Sistema](11-sistema.md).

## Actualizar solo algunos paquetes

1. Marca las casillas de los paquetes en la tabla. La casilla del encabezado marca todos.
2. Pulsa **Actualizar seleccionados**.
3. Revisa la lista y confirma en **Actualizar paquetes** con **Actualizar**.

Si no marcaste ninguno, verás **Selecciona al menos un paquete**.

## Eliminar paquetes que ya no se necesitan (autoremove)

1. Pulsa **Autoremove**.
2. Confirma en **Autoremove** con **Eliminar**.

Elimina dependencias que se instalaron automáticamente y ya ningún paquete usa, por ejemplo kernels antiguos. Revisa la salida para ver qué se borró.

## Buscar e instalar un paquete

1. Abre la pestaña **Buscar e instalar**.
2. Escribe un término, por ejemplo `nginx` o `php-gd`, y pulsa **Buscar** o `Enter`.
3. Revisa los resultados: **Paquete**, **Descripción** y **Estado** (`instalado` con su versión, o `no instalado`).
4. Pulsa **Instalar** en el paquete que quieres.
5. Confirma en **Instalar paquete** con **Instalar**.

- Puedes buscar hasta 5 palabras. Solo se permiten letras, números y `+ . _ -`.
- Se muestran como máximo 200 resultados. Si hay más, verás **refina la búsqueda**.
- Las coincidencias exactas con el nombre aparecen primero.
- Se instalan también las dependencias necesarias.

> **Consejo:** Si la búsqueda no encuentra un paquete que sabes que existe, pulsa **Actualizar listas** en la pestaña **Actualizaciones** y vuelve a buscar.

## Eliminar o purgar un paquete

Puedes hacerlo desde la pestaña **Buscar e instalar** o desde **Instalados**.

1. Pulsa **Eliminar** o **Purgar** en la fila del paquete.
2. Confirma en **Eliminar paquete** o **Purgar paquete**.

| Botón | Qué hace |
|---|---|
| **Eliminar** | Quita el programa y conserva sus archivos de configuración (`apt-get remove`). |
| **Purgar** | Quita el programa y también su configuración (`apt-get purge`). |

> **Importante:** Si otros paquetes dependen del que eliminas, APT los elimina también, sin preguntar. Nunca elimines paquetes base como `python3`, `openssh-server`, `sudo` o `systemd`: puedes perder el acceso al servidor o al propio MatiPanel. Revisa la salida después de cada eliminación.

## Revisar los paquetes instalados

1. Abre la pestaña **Instalados**.
2. Escribe en **Filtrar instalados…** para buscar por nombre.
3. Usa **Ordenar por nombre** u **Ordenar por tamaño** para encontrar los paquetes más pesados.

Se muestran como máximo 300 filas. Si hay más, verás **Mostrando 300 de N — usa el filtro**.

## Activar las actualizaciones automáticas de seguridad

La tarjeta **Actualizaciones automáticas (unattended-upgrades)** está en la pestaña **Actualizaciones**.

1. Si ves `no instalado`, pulsa **Instalar unattended-upgrades** y confirma.
2. Cuando esté instalado, verás el estado `activadas` o `desactivadas`.
3. Pulsa **Activar** o **Desactivar** y confirma en **Actualizaciones automáticas**.

Con las actualizaciones activadas, el sistema actualiza a diario la lista de paquetes e instala las actualizaciones de seguridad. El panel escribe el archivo `/etc/apt/apt.conf.d/20auto-upgrades`.

> **Nota:** Mientras unattended-upgrades trabaja, APT está bloqueado. Si en ese momento lanzas una operación, verás que el gestor de paquetes está bloqueado.

## Resolver errores frecuentes

| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| **El gestor de paquetes está bloqueado por otro proceso…** | Otro proceso (apt, dpkg o unattended-upgrades) está usando APT. El panel espera hasta 60 segundos. | Espera unos minutos y reintenta. |
| **dpkg quedó interrumpido: ejecuta 'dpkg --configure -a'…** | Una instalación anterior se cortó. | En la [Terminal](29-terminal.md) ejecuta `sudo dpkg --configure -a` y reintenta. |
| **Búsqueda inválida (letras, números y + . _ -)** | El término tiene caracteres no permitidos o más de 5 palabras. | Simplifica la búsqueda. |

## Aviso de actualizaciones por correo

El panel revisa **cada hora** qué paquetes se pueden actualizar. Cada 12 horas refresca antes la lista de paquetes (`apt-get update`), aunque el temporizador `apt-daily` del sistema esté apagado.

Cuando aparecen **versiones nuevas** te llega un correo, si activaste [Notificaciones](33-notificaciones.md) y el evento **Actualizaciones de paquetes disponibles**:

- El asunto dice cuántas son y cuántas de **seguridad**, por ejemplo `3 actualizaciones nuevas (1 de seguridad)`.
- El cuerpo lista primero las de seguridad y luego las demás, con la versión nueva.
- Cada versión se avisa **una sola vez**: si no actualizas, no recibes el mismo correo cada hora. Cuando la instalas, se olvida; si después sale otra versión del mismo paquete, se avisa de nuevo.

Además, mientras haya actualizaciones **de seguridad** pendientes, la barra de estado de arriba lo indica, y el contador **actualizaciones** muestra el total.

Al pulsar **Actualizar listas**, **Actualizar todo** o instalar algo, el panel vuelve a revisar en el momento.

> **Consejo:** Para no depender del aviso, activa las **actualizaciones automáticas de seguridad** (unattended-upgrades) en esta misma pantalla o desde [Seguridad del servidor](34-seguridad-servidor.md).

## Preguntas frecuentes

### Actualicé todo pero siguen apareciendo paquetes actualizables.

Son paquetes retenidos: necesitan instalar o quitar otros paquetes. Usa **Full-upgrade** y revisa la salida.

### ¿Tengo que reiniciar el servidor después de actualizar?

Solo si se actualizó el kernel o una biblioteca base como `libc`. Los servicios normales se reinician solos al actualizarse.

### ¿Puedo cerrar la página mientras se actualiza?

Es mejor no hacerlo. La operación sigue en el servidor, pero no verás el resultado. Si la cerraste, espera unos minutos y revisa de nuevo la pestaña **Actualizaciones**.

### ¿Cómo instalo un archivo .deb descargado?

El panel no instala archivos `.deb` sueltos. Súbelo con [Archivos](26-archivos.md) e instálalo desde la [Terminal](29-terminal.md) con `sudo apt install ./archivo.deb`.

## Ver también

- [Sistema](11-sistema.md)
- [Respaldos](16-respaldos.md)
- [PHP](23-php.md)
- [Terminal](29-terminal.md)
- [Logs](28-logs.md)
