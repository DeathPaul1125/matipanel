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

## Qué avisar

| Evento | Cuándo llega |
|---|---|
| **Problemas del servidor** | Al aparecer un problema de la barra de estado: servicio caído, disco al 80 % o más, memoria al 85 % o más, CPU sostenida, carga alta y las alertas de [Tráfico de red](19-trafico.md). Se revisa cada minuto. |
| **Cuando un problema se resuelve** | Al desaparecer, con cuánto duró. |
| **Despliegue fallido / exitoso** | Al terminar cada despliegue de [Despliegues](56-despliegues.md), con el commit y el error. |
| **Respaldo de app fallido / correcto** | Al terminar cada [respaldo de app](57-respaldos-apps.md). Un fallo se avisa como mucho cada 6 h por app. |
| **Bloqueo por intentos fallidos** | Cuando una IP queda bloqueada en el inicio de sesión del panel por contraseñas o códigos 2FA incorrectos (como mucho cada 15 min por IP). |

**Recordar cada…**: si un problema sigue activo, vuelve a avisar con esa frecuencia («No recordar» = solo al aparecer).

Cada correo lleva en el asunto `[MatiPanel · <servidor>]` y al pie el enlace al panel.

## Últimos envíos

La tabla muestra los últimos 50 correos desde que arrancó el panel, con su resultado. Si uno falló, verás el motivo.

> **Nota:** Los correos se envían en segundo plano: si el servidor de correo tarda o no responde, el panel sigue funcionando normalmente.

La configuración se guarda en `/var/lib/matipanel/notify.json` (legible solo por root).

## Ver también

- [Dashboard](10-dashboard.md): la barra de estado.
- [Auditoría](32-auditoria.md)
