# Monitor de servicios

Vigila que tus servicios respondan: páginas web (HTTP/HTTPS), puertos TCP (bases de datos, correo, SSH…) y equipos de la red (ping). El panel los comprueba cada cierto tiempo, guarda el tiempo de respuesta, calcula la disponibilidad y te avisa por correo cuando algo deja de responder y cuando se recupera. Lo encuentras en **Menú → Herramientas → Monitor de servicios**.

Las comprobaciones salen **desde este servidor**: sirve tanto para servicios de la red interna (intranet, impresoras, routers, otros servidores) como para sitios de Internet.

## Agregar una comprobación

1. Pulsa **Nueva comprobación**.
2. Escribe un **Nombre** que reconozcas en los avisos (hasta 60 caracteres), por ejemplo `Intranet` o `Base de datos de nómina`.
3. Elige el **Tipo** y escribe el **Destino**:

| Tipo | Destino | Se considera correcto cuando… |
|---|---|---|
| **HTTP(S)** | La URL completa: `https://intranet.local/` o `http://192.168.1.20:8080/salud` | El servidor responde con un código esperado (por defecto 200-399) y, si lo pediste, la página contiene el texto indicado. |
| **TCP** | Equipo y puerto: `192.168.1.10:3306`, `db.local:5432` o `[fd00::5]:443` | Se puede abrir la conexión al puerto. |
| **Ping** | IP o nombre: `192.168.1.1` o `impresora.local` | El equipo contesta un ping. |

4. Ajusta el resto si hace falta:

| Campo | Para qué sirve |
|---|---|
| **Comprobar cada** | 30 s, 1 min, 5 min o 15 min. |
| **Fallos seguidos para darlo por caído** | Por defecto 2: un fallo aislado (un corte de un segundo) no dispara avisos. |
| **Tiempo de espera (s)** | Cuánto se espera la respuesta antes de contarla como fallo (1 a 30 s). |
| **Códigos HTTP esperados** | Solo HTTP. Un rango (`200-399`) o un código (`200`). |
| **Texto que debe aparecer** | Solo HTTP. Útil para detectar una página de error que responde 200, por ejemplo `Iniciar sesión`. |
| **Seguir redirecciones** | Solo HTTP. Si lo desmarcas, un 301/302 se evalúa tal cual. |
| **Ignorar errores del certificado** | Solo HTTP. Para servicios internos con certificado propio (autofirmado). |
| **Activa** | Desmárcalo para crearla en pausa. |

5. Pulsa **Crear**. La primera comprobación se hace a los pocos segundos.

Puedes tener hasta 200 comprobaciones.

> **Consejo:** Para una aplicación web, apunta a una página que use la base de datos (por ejemplo, el inicio de sesión) y pide un texto que solo aparece cuando funciona. Así detectas también cuando la web responde pero la aplicación falla.

## Estados

| Estado | Significado |
|---|---|
| **En línea** (verde) | La última comprobación fue correcta. |
| **Falla (1 de 2)** (amarillo) | Falló, pero aún no llega a los fallos seguidos necesarios para darlo por caído. |
| **Caído** (rojo) | Falló las veces seguidas configuradas. Se abre una **caída** y se envía un aviso. |
| **Sin datos** | Recién creada o recién editada: todavía no hay resultado. |
| **En pausa** | No se comprueba ni avisa. |

El primer resultado correcto devuelve la comprobación a **En línea**, cierra la caída y envía el aviso de recuperación. Bajo el destino aparece en rojo el error de la última comprobación fallida, por ejemplo `Conexión rechazada`, `Tiempo de espera agotado`, `HTTP 502 (se esperaba 200-399)` o `Certificado no válido`.

Las comprobaciones caídas también aparecen en la **barra de estado** de arriba.

## Acciones

- **Probar ahora**: hace la comprobación en el momento y muestra el resultado. Si está activa, cuenta como una comprobación más.
- **Editar**: cambia cualquier dato. Si cambias el tipo o el destino, el estado vuelve a **Sin datos**.
- **Pausar / Reanudar**: útil durante un mantenimiento programado para no recibir avisos. Al pausar se cierra la caída abierta, sin aviso.
- **Eliminar**: borra la comprobación con su historial y sus caídas.

## Disponibilidad y tiempo de respuesta

La columna **Disponibilidad** muestra el porcentaje de comprobaciones correctas de las últimas 24 horas (destacado) y de los últimos 7 y 30 días. Se marca en amarillo por debajo del 99 % y en rojo por debajo del 95 %.

Haz clic en una fila para ver su **Detalle**:

- **Tiempo de respuesta**: gráfica de las últimas 24 horas o 7 días (media por intervalo). Los cortes en la línea son momentos en que falló.
- **Caídas de los últimos 30 días**: inicio, fin (o **En curso**), duración y el error. La caída empieza con el primer fallo de la racha, no cuando se confirma.

Los resultados se guardan 30 días en `/var/lib/matipanel/monitor.db`; la configuración, en `/var/lib/matipanel/monitor.json` (legible solo por root). La pantalla se actualiza sola cada 15 s.

## Avisos por correo

Cuando una comprobación pasa a **Caído** se envía un correo «Caído: «Nombre» no responde» con el error y la hora del primer fallo; cuando vuelve, «Recuperado: «Nombre» vuelve a responder» con la duración de la caída. Solo se envía un aviso por cambio de estado: mientras siga caído no se repite.

Para recibirlos, configura el correo en [Notificaciones](33-notificaciones.md) y deja marcado el evento **Monitor: un servicio deja de responder o se recupera** (viene activado).

## Preguntas frecuentes

### El ping falla pero el servicio funciona

Muchos equipos y firewalls (incluido el de Windows) bloquean el ping. Usa una comprobación **TCP** a un puerto del servicio o **HTTP** si tiene web.

### Un servicio HTTPS interno aparece caído por «Certificado no válido»

Usa un certificado propio que este servidor no reconoce. Edita la comprobación y marca **Ignorar errores del certificado**. Si es un sitio público, revisa que el certificado no haya vencido.

### Recibo avisos por cortes de unos segundos

Sube **Fallos seguidos para darlo por caído** (por ejemplo a 3) o el **Tiempo de espera**. Con «cada 1 min» y 3 fallos, solo se avisa si el servicio lleva unos 3 minutos sin responder.

### ¿Se pierden los datos si reinicio el panel?

No. El historial está en disco y una caída abierta sigue abierta tras el reinicio, sin volver a avisar.

### ¿Cuánta carga genera?

Muy poca: cada comprobación es una petición, una conexión o un ping. Se hacen como máximo 8 a la vez; para una página web se leen como mucho 1 MB al buscar el texto.

## Ver también

- [Notificaciones](33-notificaciones.md): configurar el correo de los avisos.
- [Servicios](13-servicios.md): reiniciar un servicio de este servidor que dejó de responder.
- [Red](18-red.md): conexiones activas y comprobar si un servicio está escuchando.
- [Firewall](20-firewall.md): si una comprobación TCP falla por un puerto bloqueado.
