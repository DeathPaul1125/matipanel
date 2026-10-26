# Procesos

El módulo Procesos lista los programas que se están ejecutando en el servidor, ordenados por consumo de CPU, y te permite terminar los que estén colgados o consumiendo demasiado. Lo encuentras en **Menú → Sistema → Procesos**.

## Ver la lista de procesos

Al abrir la página se carga la tabla de procesos. Cada fila muestra:

| Columna | Significado |
|---|---|
| **PID** | Número de identificación del proceso |
| **Usuario** | Usuario del sistema que lo ejecuta |
| **CMD** | Comando completo con sus argumentos (recortado a 120 caracteres; pasa el ratón encima para verlo) |
| **CPU%** | Uso de CPU medido durante unos 0,2 segundos |
| **MEM%** | Porcentaje de la memoria RAM total que usa |
| **Estado** | Estado del proceso según el kernel (ver tabla abajo) |
| **Tiempo** | Tiempo desde que arrancó: `hh:mm:ss`, o `Nd hh:mm` si lleva días |

La lista se ordena de mayor a menor CPU.

> **Importante:** Solo se muestran los **200 procesos** con más CPU. En servidores con muchos procesos, los que están inactivos pueden no aparecer. Si buscas uno concreto y no lo ves, usa la [Terminal](29-terminal.md) con `ps aux | grep nombre`.

> **Nota:** La tabla no se actualiza sola. Usa **Refrescar** para obtener valores nuevos.

### Estados más comunes

| Estado | Significado |
|---|---|
| `running` | Usando la CPU en este momento |
| `sleeping` | Esperando algo (red, disco, un temporizador). Es lo normal |
| `idle` | Hilo del kernel sin trabajo |
| `disk-sleep` | Esperando al disco. Si dura mucho, puede indicar un disco lento o con fallas |
| `zombie` | Terminó pero su proceso padre no lo ha recogido |
| `stopped` | Pausado (por ejemplo, con Ctrl+Z) |

## Buscar un proceso

1. Escribe en el campo **Filtrar por nombre, usuario o PID…**.
2. La tabla se filtra al instante mientras escribes.

El filtro busca dentro del comando completo, el usuario y el PID. Por ejemplo, `php` muestra todos los procesos de PHP, y `www-data` muestra todo lo que corre como ese usuario.

> **Nota:** El filtro trabaja sobre la última lista cargada. Si el proceso arrancó después, haz clic en **Refrescar**.

## Terminar un proceso

1. Busca el proceso en la tabla.
2. Haz clic en **Kill** en su fila.
3. En el diálogo **Terminar proceso** ("¿Enviar SIGTERM al proceso …?"), haz clic en **Terminar**. Si el proceso no responde, marca antes **Forzar (SIGKILL)**.

La tabla se recarga medio segundo después. Si el proceso ya no aparece, terminó.

### Qué hace exactamente

El botón envía la señal `SIGTERM`. Es una petición educada: el programa puede guardar su trabajo y cerrarse limpiamente. Algunos programas tardan unos segundos en cerrar, y otros pueden ignorar la señal.

Si el proceso sigue apareciendo después de refrescar:

1. Espera unos segundos y vuelve a hacer clic en **Refrescar**.
2. Si sigue ahí, vuelve a pulsar **Kill**, marca **Forzar (SIGKILL)** y pulsa **Terminar**. Equivale a `kill -9 PID`: el proceso muere al instante.

> **Importante:** Usa **Forzar (SIGKILL)** solo como último recurso: el programa no tiene ocasión de cerrar, y puede dejar archivos o bases de datos a medio escribir.

### Procesos que no se pueden terminar

- El proceso con PID `1` (`systemd`, el proceso inicial del sistema).
- El propio proceso del panel.

En esos casos verás el error "No se puede matar init ni el propio panel". Si el proceso ya terminó por su cuenta, verás "Proceso no existe".

> **Importante:** Si el proceso pertenece a un servicio (por ejemplo, `nginx`, `mariadbd`, `php-fpm`), no lo termines desde aquí. systemd puede volver a lanzarlo de inmediato o dejar el servicio en estado de fallo. Reinicia o detén el servicio desde [Servicios](13-servicios.md).

## Identificar qué consume recursos

- **CPU alta sostenida**: haz clic en **Refrescar** varias veces. Si el mismo proceso sigue arriba, es el responsable. Compara con la gráfica de CPU del [Dashboard](10-dashboard.md).
- **Memoria alta**: la tabla se ordena por CPU, no por memoria. Revisa la columna **MEM%** de arriba hacia abajo, o filtra por el nombre del programa sospechoso.
- **Muchos procesos iguales**: es normal en servidores web y bases de datos (PHP-FPM, Apache y PostgreSQL usan varios procesos trabajadores).

## Preguntas frecuentes

### ¿Por qué el CPU% de un proceso pasa del 100 %?

El porcentaje se mide por núcleo. Un proceso que usa dos núcleos completos marca 200 %. En un servidor de 4 núcleos el máximo posible es 400 %.

### Terminé un proceso y volvió a aparecer con otro PID. ¿Por qué?

Lo controla un servicio de systemd configurado para reiniciarse solo. Detén el servicio en [Servicios](13-servicios.md) en lugar de terminar el proceso.

### ¿Puedo ver los procesos zombie? ¿Cómo los elimino?

Aparecen con el estado `zombie`. No consumen CPU ni memoria, solo una entrada en la tabla de procesos. No se pueden terminar directamente: desaparecen cuando su proceso padre los recoge o termina. Si son muchos, reinicia el servicio que los genera.

### ¿Por qué no encuentro un proceso que sé que está corriendo?

La lista se limita a los 200 procesos con más CPU. Haz clic en **Refrescar** y filtra por su nombre. Si sigue sin aparecer, búscalo desde la [Terminal](29-terminal.md) con `pgrep -a nombre`.

## Ver también

- [Dashboard](10-dashboard.md)
- [Servicios](13-servicios.md)
- [Terminal](29-terminal.md)
- [Logs](28-logs.md)
