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
| **Barra lateral** (izquierda) | El menú, en secciones plegables: Sistema, Archivos y discos, Red y acceso, Web y aplicaciones, Bases de datos y Herramientas. Arriba, el buscador de módulos. Abajo, tu **menú de usuario** y el 💙. |
| **Barra superior** | El nombre de la pantalla, el servidor, el botón **?** (ayuda de esa pantalla) y el botón de tema claro/oscuro. |
| **Contenido** | La pantalla elegida. |

En el celular, la barra lateral se abre con el botón **☰**.

### Mensajes y confirmaciones

- Las acciones correctas muestran un aviso breve abajo a la derecha. Haz clic sobre él para cerrarlo.
- Los errores aparecen en una ventana con el detalle que devolvió el sistema.
- Las acciones peligrosas piden confirmación con un botón rojo. Las más delicadas te piden **escribir un nombre** (por ejemplo, el de la base de datos o el del servidor) antes de habilitar el botón.

## Moverte por el menú

- **Secciones plegables:** haz clic en el título de una sección (por ejemplo **Web y aplicaciones**) para abrirla o cerrarla. El panel recuerda tu elección; la sección de la pantalla en la que estás siempre queda abierta.
- **Buscar un módulo:** pulsa la tecla `/` (o haz clic en **Buscar módulo…**), escribe parte del nombre, por ejemplo `fire` o `php`, y pulsa `Enter` para abrir el primero. `Esc` limpia la búsqueda. No importan los acentos.
- **Menú de usuario:** haz clic en tu nombre, abajo a la izquierda, para abrir **Seguridad (2FA)**, **Apariencia**, **Ayuda**, **Acerca de** y **Cerrar sesión**.
- **Barra compacta:** en [Apariencia](31-apariencia.md) puedes dejar la barra lateral solo con iconos.

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
| **/** | Cualquier pantalla | Buscar un módulo en el menú |
| **Escape** | Ventanas y menú móvil | Cerrar |

## Cerrar sesión

Haz clic en tu nombre al pie de la barra lateral y elige **Cerrar sesión**. La sesión también caduca tras 1 hora sin actividad.

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
