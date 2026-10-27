# Dashboard

El Dashboard es la pantalla de inicio del panel. Te dice de un vistazo si el servidor está sano y muestra sus métricas en vivo: CPU, memoria, disco, red y procesos. Lo encuentras en **Menú → Sistema → Dashboard**.

## Leer el estado del servidor (barra superior)

El estado del servidor está en la **barra superior**, a la derecha, y se ve en **todas las pantallas** del panel, no solo en el Dashboard:

- **Todo en orden** con un indicador verde: no se detectó ningún problema.
- **Atención: …** en ámbar (aviso) o rojo (grave): hay uno o más problemas, separados por `·`, primero los graves. Si el texto es largo se corta; pasa el mouse para verlo completo.

Haz clic en el estado para volver al Dashboard. Se actualiza cada 30 segundos y se pausa mientras la pestaña no está visible.

### Qué revisa el veredicto

Los umbrales se configuran en [Notificaciones → Umbrales del servidor](33-notificaciones.md#umbrales-del-servidor) y son los mismos para la barra de estado y para los correos. Valores por defecto:

| Condición | Aviso | Grave |
|---|---|---|
| CPU, promedio de los últimos 5 minutos | 85 % | 95 % |
| Memoria (RAM) | 85 % | 95 % |
| Swap, promedio de 5 minutos (desactivada por defecto) | 50 % | 80 % |
| Carga, promedio de 5 minutos, por núcleo | 1,5 × núcleos | 3 × núcleos |
| Uso de cada partición | 80 % | 90 % |
| Inodos usados de cada partición | 85 % | 95 % |
| Temperatura de la CPU (si el servidor tiene sensores) | 80 °C | 90 °C |
| Uno o más servicios systemd en estado `failed` | — | Grave |
| Un contenedor de Docker (de una app o un stack) que se reinicia sin parar o se detuvo con error | — | Grave |
| Un contenedor de Docker que no responde a su comprobación de salud (*unhealthy*) | Aviso | — |

### Los tres contadores junto al estado

- **con fallo**: cantidad de servicios en estado `failed` (se pone en rojo si hay alguno; pasa el mouse para ver cuáles). Al hacer clic vas a [Servicios](13-servicios.md).
- **actualizaciones**: paquetes pendientes de actualizar. Al hacer clic vas a [Paquetes](27-paquetes.md).
- **sesiones**: usuarios con sesión abierta en el servidor (SSH, consola). Al hacer clic vas a [Sistema](11-sistema.md).

> **Nota:** El conteo de actualizaciones solo funciona en sistemas con APT (Debian). Se consulta como máximo cada 30 minutos para no cargar el servidor. Si acabas de actualizar, el número puede tardar en bajar. Si muestra `—` o "No disponible", la consulta falló o el sistema no usa APT.

## Interpretar los tacómetros

Los cuatro tacómetros muestran el uso actual y se actualizan cada 3 segundos:

| Tacómetro | Texto inferior | Amarillo desde | Rojo desde |
|---|---|---|---|
| **CPU** | carga de 1 minuto y número de núcleos | 75 % | 90 % |
| **Memoria** | usada de total | 85 % | 95 % |
| **Swap** | usada de total, o "sin swap" | 50 % | 80 % |
| **Disco /** | libres de total (solo la partición raíz) | 80 % | 90 % |

> **Consejo:** La memoria "usada" excluye la caché del sistema. Linux usa la RAM libre como caché de disco, así que es normal ver poca memoria libre en `free`, pero un porcentaje bajo aquí.

## Consultar el historial de métricas

La tarjeta **Historial** muestra seis gráficas: **CPU**, **Memoria y swap**, **Red** (bajada y subida), **E/S de disco** (lectura y escritura), **Carga (1 min)** y **Procesos**. Junto a cada título ves el valor más reciente.

1. Elige el rango con los botones **5 min**, **15 min**, **1 h**, **6 h**, **24 h** o **7 días**.
2. Las gráficas se redibujan al momento. En los rangos de hasta 1 hora se agrega un punto nuevo cada 5 segundos; los rangos largos se recalculan cada minuto.
3. Pasa el mouse sobre una gráfica para ver la fecha, la hora y los valores exactos de ese momento.

El panel toma una muestra cada 5 segundos y la guarda en disco (`/var/lib/matipanel/metrics.db`), así que **el historial se conserva aunque reinicies el servicio o el servidor**. Se guardan los últimos **7 días**; lo más antiguo se borra solo.

En los rangos largos (6 h, 24 h y 7 días) cada punto es el **promedio** de varias muestras, para que la gráfica siga siendo ágil. Por eso un pico muy breve puede verse más bajo que en el rango de 5 o 15 minutos.

> **Nota:** si el servidor o el panel estuvieron apagados, la línea se **corta** en ese tramo en lugar de unir los puntos. Así distingues un apagón de un periodo de poca actividad.

> **Nota:** El botón de rango que eliges aquí solo dura mientras tengas la página abierta. Para cambiar el rango predeterminado, usa **menú de usuario (tu nombre, abajo a la izquierda) → Apariencia → Rango del historial**.

## Revisar la información del sistema

La tarjeta **Información del sistema** resume los datos básicos del servidor:

- **Nombre del host**, **Sistema operativo**, **Kernel** (con la arquitectura) y **Procesador** (modelo y núcleos).
- **Encendido**: tiempo desde el último arranque.
- **Carga media**: carga de 1, 5 y 15 minutos.
- **Procesos**, **Memoria real**, **Swap** y **Disco local (/)**.
- **Actualizaciones**: "Todo está actualizado", "N paquetes por actualizar" o "No disponible".
- **Sesiones abiertas**.

En la esquina superior derecha de la tarjeta aparece la **Hora del servidor**. Es el reloj del servidor, pero tu navegador la muestra convertida a la zona horaria de tu computadora.

> **Consejo:** La carga media se compara con el número de núcleos. En un servidor de 4 núcleos, una carga de 4 significa que la CPU está ocupada al 100 %. Valores muy por encima indican que hay procesos esperando.

## Ver los procesos que más consumen

La tabla **Procesos con más CPU** muestra los 6 procesos más activos, con su usuario, PID, CPU y memoria. Se actualiza cada 15 segundos. El porcentaje de CPU es el promedio desde la consulta anterior.

Para ver la lista completa o terminar un proceso, haz clic en **Ver todos →**. Eso te lleva a [Procesos](12-procesos.md).

## Revisar almacenamiento y servicios con fallo

- **Almacenamiento**: cada partición montada con su uso y una barra de color (amarillo desde 80 %, rojo desde 90 %). Se omiten sistemas de archivos temporales (`tmpfs`, `squashfs`, `overlay`) y los montajes de snap. **Detalle →** abre [Discos](15-discos.md).
- **Servicios con fallo**: el nombre de cada servicio en estado `failed`, con un enlace **revisar** hacia [Servicios](13-servicios.md). Si no hay ninguno verás "Ningún servicio con fallo."

## Usar los accesos rápidos

La tarjeta **Accesos rápidos** tiene enlaces directos a **Terminal**, **Archivos**, **Servicios**, **Paquetes**, **Servidor web**, **Bases de datos**, **Logs** y **Respaldos**.

## Mostrar u ocultar secciones del Dashboard

Puedes ocultar las secciones que no uses. La configuración es por usuario.

1. Haz clic en **Personalizar →** en la tarjeta **Accesos rápidos**, o ve a **menú de usuario (tu nombre, abajo a la izquierda) → Apariencia**.
2. En la tarjeta **Dashboard**, marca o desmarca las **Secciones visibles**: **Tacómetros**, **Historial**, **Información del sistema**, **Procesos y almacenamiento**, **Accesos rápidos**.
3. Los cambios se guardan solos.

> **Nota:** El estado del servidor y sus contadores están en la barra superior y siempre se muestran. Si ocultas **Accesos rápidos**, el enlace **Personalizar →** también desaparece; vuelve por el menú **Apariencia**.

## Frecuencia de actualización

| Dato | Cada cuánto se actualiza |
|---|---|
| Tacómetros e información del sistema | 3 segundos |
| Estado del servidor en la barra superior | 30 segundos |
| Gráficas del historial | 5 segundos |
| Servicios con fallo, particiones, procesos top, sesiones | 15 segundos |
| Conteo de actualizaciones | 30 minutos |

No necesitas recargar la página. Todo se refresca solo mientras la pestaña esté abierta.

## Preguntas frecuentes

### ¿Por qué el historial está vacío o solo muestra unos minutos?

Si acabas de instalar MatiPanel, todavía no hay datos: el historial empieza a llenarse desde ese momento (una muestra cada 5 segundos) y llega hasta 7 días. Los reinicios del servicio o del servidor no lo borran; solo dejan un corte en la línea durante el tiempo que estuvo apagado.

### ¿Por qué dice "Atención" si el servidor funciona bien?

Revisa el mensaje: suele ser un disco por encima del 80 % o un servicio en estado `failed` que no usas. Un servicio fallido puede ser algo antiguo sin importancia. Revísalo en [Servicios](13-servicios.md) y, si no lo necesitas, deshabilítalo.

### Actualicé los paquetes pero sigue mostrando actualizaciones pendientes. ¿Es un error?

No. El conteo se guarda en caché durante 30 minutos. Espera ese tiempo o reinicia el servicio `matipanel` para forzar una nueva consulta.

### ¿La hora que aparece es la de mi computadora o la del servidor?

Es el reloj del servidor, pero convertido a la zona horaria de tu computadora. Si tu computadora y el servidor tienen zonas distintas, la hora mostrada no coincide con la zona configurada en el servidor. Para ver y cambiar la zona del servidor, usa **Fecha y hora** en [Sistema](11-sistema.md). El saludo ("Buenos días", etc.) usa la hora de tu computadora.

## Ver también

- [Sistema](11-sistema.md)
- [Procesos](12-procesos.md)
- [Servicios](13-servicios.md)
- [Discos](15-discos.md)
- [Paquetes](27-paquetes.md)
- [Apariencia](31-apariencia.md)
