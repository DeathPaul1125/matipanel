# Notificaciones

El panel puede avisarte **por correo** cuando algo pasa en el servidor, aunque nadie lo tenga abierto. Lo encuentras en **Menú → Herramientas → Notificaciones**.

## Configurar el correo

1. En **Servidor de correo (SMTP)** completa:
   - **Servidor SMTP** y **Puerto**: los de tu servidor de correo (por ejemplo `mail.empresa.gob.gt`).
   - **Seguridad**: STARTTLS (587), SSL/TLS (465) o sin cifrar (25, solo dentro de tu red).
   - **Remitente**: la dirección desde la que llegarán los avisos.
   - **Usuario** y **Contraseña**, si tu servidor pide autenticación. La contraseña no se vuelve a mostrar; dejarla vacía conserva la guardada.
   - **Verificar el certificado**: desmárcalo si tu servidor de correo usa un certificado propio (interno).
   - **Destinatarios**: hasta 10 correos separados por coma.
2. Pulsa **Guardar** y luego **Enviar correo de prueba**. Si falla, el mensaje muestra el error exacto del servidor de correo (credenciales, puerto, certificado…).
3. Marca **Notificaciones activas** y guarda.

> **Importante:** Guarda desde el mismo navegador y dirección con la que entras al panel (por ejemplo `https://192.100.10.64:8443`): esa dirección es la que usan los botones **Abrir en MatiPanel** de los correos.

## Cómo se ven los correos

Cada correo llega con diseño (HTML) y también en texto simple para los programas de correo que no muestran HTML:

- Arriba, la «M» de MatiPanel, el nombre del servidor y la hora.
- Una **franja de color** según el tipo: 🔴 rojo para problemas, 🟠 naranja para avisos, ✅ verde cuando algo salió bien o se resolvió y 🔔 verde azulado para información. El mismo símbolo va al principio del **asunto**, para distinguirlos en la bandeja.
- El título, una explicación corta y una **tabla con los detalles** (qué, dónde, cuándo, quién).
- Cuando hay un error, el **registro** en un bloque oscuro (por ejemplo, la salida de un despliegue fallido).
- Un botón **Abrir en MatiPanel** que lleva directo a la pantalla correspondiente.
- Al pie, el enlace **Cambiar qué se avisa**.

El asunto siempre incluye `[MatiPanel · <servidor>]`, así puedes filtrarlos en tu correo.

### Vista previa

En **Qué avisar**, cada aviso tiene **Ver ejemplo**: muestra en el panel cómo llega ese correo. **Ver cómo llega un correo** muestra el de prueba. Desde la vista previa, **Enviarme este ejemplo** lo manda a los destinatarios (con «(ejemplo)» en el asunto). El **Resumen diario** se arma con los datos reales del momento.

## Qué avisar

Los avisos están agrupados. Los marcados por defecto aparecen así al activar las notificaciones; cambia los que quieras y pulsa **Guardar**.

### Servidor

| Aviso | Por defecto | Cuándo llega |
|---|---|---|
| **Problemas del servidor** | Sí | Al aparecer un problema de la barra de estado: servicio caído, disco al 80 % o más, memoria al 85 % o más, CPU sostenida, carga alta, **contenedores de Docker** que se reinician sin parar, se detienen con error o no responden, y las alertas de [Tráfico de red](19-trafico.md). Se revisa cada minuto. El correo lista también los otros problemas activos. |
| **Cuando un problema se resuelve** | Sí | Al desaparecer, con cuánto duró. |
| **El servidor se reinició** | Sí | Al volver a arrancar el servidor: hora del arranque, cuánto estuvo fuera de servicio y cuánto llevaba encendido. Útil para detectar cortes de luz o reinicios que nadie programó. |
| **Certificados SSL por vencer o vencidos** | Sí | Cuando a un certificado de Let's Encrypt (o al del propio panel) le quedan 14 días o menos. Let's Encrypt los renueva solo 30 días antes, así que este aviso significa que **la renovación está fallando**. Se revisa cada 6 h y se repite como mucho una vez al día. |
| **Actualizaciones de paquetes** | Sí | Cuando aparecen versiones nuevas de paquetes (se revisa cada hora). Las **de seguridad** van en su propia tabla, en rojo, y el correo sale en rojo si hay alguna. Cada versión se avisa **una sola vez**. Ver [Paquetes](27-paquetes.md). |
| **MatiPanel se actualizó** | Sí | Al arrancar el panel con una versión nueva, con las novedades. |

### Seguridad

| Aviso | Por defecto | Cuándo llega |
|---|---|---|
| **Bloqueo por intentos fallidos** | Sí | Cuando una IP queda bloqueada en el inicio de sesión del panel por contraseñas o códigos 2FA incorrectos (como mucho cada 15 min por IP). |
| **Inicio de sesión desde una IP nueva** | Sí | Cuando un usuario entra al panel desde una IP que nunca había usado, con el navegador y el sistema. La primera vez que un usuario entra no se avisa (todavía no hay IPs conocidas). |
| **Cada inicio de sesión en el panel** | No | Cada vez que alguien entra al panel. |
| **Inicios de sesión por SSH** | No | Cuando alguien entra al servidor por SSH (del registro de `sshd`), con usuario, IP y método (clave o contraseña). Si entran varios en el mismo minuto, llega un solo correo con todos. |
| **Cambios delicados en el panel** | Sí | Cuando alguien, desde el panel: crea, borra o bloquea usuarios del sistema, cambia contraseñas, grupos o claves SSH; cambia los **permisos del panel**; activa, desactiva o cambia el **firewall**; cambia la configuración de **SSH**; **reinicia, apaga** o programa un reinicio; cambia el nombre del servidor; **elimina** apps, bases de datos, stacks, certificados, zonas DNS, volúmenes o recursos compartidos; **restaura o recupera** respaldos; o cambia estas notificaciones. Dice quién, desde qué IP y con qué datos (sin contraseñas). |
| **Doble factor (2FA)** | Sí | Cuando un usuario activa o desactiva su 2FA. |

### Apps y respaldos

| Aviso | Por defecto | Cuándo llega |
|---|---|---|
| **Despliegue fallido** | Sí | Al fallar un despliegue de [Despliegues](56-despliegues.md): commit, rama, quién lo disparó, duración y el error en un bloque de registro. |
| **Despliegue exitoso** | No | Al terminar bien, con el commit y la dirección de la app. |
| **Respaldo fallido** | Sí | Al fallar un [respaldo de app](57-respaldos-apps.md) (como mucho cada 6 h por app) o un respaldo **programado** de carpetas de [Respaldos](16-respaldos.md). |
| **Respaldo correcto** | No | Al terminar bien un respaldo de app o un respaldo programado de carpetas. |
| **Monitor** | Sí | Cuando un servicio vigilado por el [Monitor](58-monitor.md) deja de responder (rojo) y cuando se recupera (verde). |

### Resumen diario

| Aviso | Por defecto | Cuándo llega |
|---|---|---|
| **Resumen diario del servidor** | No | Una vez al día, a la **Hora del resumen diario** (07:00 por defecto). Si el panel estaba apagado a esa hora, llega en cuanto arranca. |

El resumen trae, en un solo correo:

- **Estado ahora**: los problemas y avisos activos, o «Todo en orden».
- **Recursos**: desde cuándo está encendido, CPU, memoria, carga y cada disco.
- **Respaldos de apps (24 h)**: cuáles salieron bien, cuáles fallaron y cuáles no se respaldaron.
- **Despliegues (24 h)**: cada despliegue con su resultado y commit.
- **Actualizaciones**: pendientes y de seguridad.
- **Monitor**: cuántas comprobaciones están en línea y cuáles caídas.
- **Seguridad (24 h)**: quién entró al panel, intentos fallidos, acciones registradas en la Auditoría e intentos fallidos de SSH (con la IP que más lo intentó).

El asunto dice el veredicto («Resumen diario: todo en orden» o «… 1 problema y 2 avisos») y la franja toma el color correspondiente.

### Otras opciones

- **Recordar cada…**: si un problema sigue activo, vuelve a avisar con esa frecuencia («No recordar» = solo al aparecer).
- **Hora del resumen diario**: de 00:00 a 23:00.

## Últimos envíos

La tabla muestra los últimos 50 correos desde que arrancó el panel, con su resultado. Si uno falló, verás el motivo.

> **Nota:** Los correos se envían en segundo plano: si el servidor de correo tarda o no responde, el panel sigue funcionando normalmente. Los respaldos programados de carpetas (que corre `cron`) envían su correo en el momento y no aparecen en esta tabla.

La configuración se guarda en `/var/lib/matipanel/notify.json` y el estado de los avisos (hora del último arranque, IPs conocidas de cada usuario, día del último resumen) en `/var/lib/matipanel/notify_state.json`, ambos legibles solo por root.

## Ver también

- [Dashboard](10-dashboard.md): la barra de estado.
- [Auditoría](32-auditoria.md): el registro completo de lo que se hace en el panel.
- [Seguridad del servidor](34-seguridad-servidor.md)
