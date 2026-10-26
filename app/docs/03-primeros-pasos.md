# Primeros pasos

Esta página te guía en tu primera visita al panel: cómo entrar, cómo está organizada la pantalla y qué conviene configurar primero.

## Iniciar sesión

1. Abre `https://IP-DEL-SERVIDOR:8443` en el navegador.
2. Acepta la advertencia del certificado (**Avanzado → Continuar**). Solo pasa la primera vez en cada navegador.
3. Escribe tu **Usuario** y **Contraseña** de Linux y pulsa **Iniciar sesión**.
4. Si tienes 2FA activo, escribe el código de 6 dígitos de tu app (o un código de recuperación).

> **Nota:** después de 5 intentos fallidos desde la misma IP, el acceso se bloquea 5 minutos. Espera o pide a otro administrador que reinicie el servicio (`systemctl restart matipanel`).

## Conocer la pantalla

| Zona | Qué contiene |
|---|---|
| **Barra lateral** (izquierda) | El menú, agrupado en Sistema, Acceso, Red, Servidores, Herramientas y Cuenta. Abajo verás tu usuario y **Cerrar sesión**. |
| **Barra superior** | El nombre de la pantalla, el servidor, el botón **?** (ayuda de esa pantalla) y el botón de tema claro/oscuro. |
| **Contenido** | La pantalla elegida. |

En el celular, la barra lateral se abre con el botón **☰**.

### Mensajes y confirmaciones

- Las acciones correctas muestran un aviso breve abajo a la derecha. Haz clic sobre él para cerrarlo.
- Los errores aparecen en una ventana con el detalle que devolvió el sistema.
- Las acciones peligrosas piden confirmación con un botón rojo. Las más delicadas te piden **escribir un nombre** (por ejemplo, el de la base de datos o el del servidor) antes de habilitar el botón.

## Qué configurar primero

1. **Activa el doble factor** en [Seguridad (2FA)](30-seguridad-2fa.md). Es la mejor protección si alguien adivina tu contraseña.
2. **Revisa el firewall** en [Firewall](20-firewall.md). En Debian 13 no viene uno gestionable instalado: con un clic puedes instalar UFW y permitir solo lo necesario.
3. **Programa un respaldo** de tus carpetas importantes en [Respaldos](16-respaldos.md).
4. **Revisa las actualizaciones** pendientes en [Paquetes](27-paquetes.md) y activa las actualizaciones automáticas de seguridad.
5. **Personaliza** colores, tamaño de letra y barra lateral en [Apariencia](31-apariencia.md).

## Atajos de teclado

| Atajo | Dónde | Acción |
|---|---|---|
| **Ctrl+K** | Ayuda | Ir al buscador |
| **Ctrl+S** | Editor de archivos | Guardar |
| **Tab** | Editor de archivos | Insertar tabulación |
| **Ctrl+Enter** | Consola SQL | Ejecutar la consulta |
| **Ctrl+Shift+C / V** | Terminal | Copiar / pegar |
| **Escape** | Ventanas y menú móvil | Cerrar |

## Cerrar sesión

Pulsa **Cerrar sesión** al pie de la barra lateral. La sesión también expira al cerrar el navegador.

## Preguntas frecuentes

### ¿Puedo tener varias pestañas del panel abiertas?

Sí. Todas comparten la misma sesión.

### ¿Los cambios que hago en el panel se ven en la consola?

Sí. El panel ejecuta los mismos comandos del sistema; no guarda una configuración aparte.

### ¿Por qué no aparece un módulo, por ejemplo Docker o PHP?

Los módulos siempre están en el menú, pero si el software no está instalado verás una tarjeta con el comando para instalarlo.

## Ver también

- [Introducción](01-introduccion.md)
- [Dashboard](10-dashboard.md)
- [Seguridad (2FA)](30-seguridad-2fa.md)
