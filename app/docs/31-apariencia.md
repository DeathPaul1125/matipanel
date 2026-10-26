# Apariencia

Esta página te permite personalizar cómo se ve el panel: tema claro u oscuro, colores, barra lateral, tamaño del texto, densidad y secciones del dashboard. La encuentras en **Menú → Cuenta → Apariencia**.

## Dónde se guarda cada preferencia

| Preferencia | Dónde se guarda | Qué significa |
|---|---|---|
| **Tema** (claro/oscuro) | En **este navegador** | Si entras desde otro equipo o navegador, el tema puede ser distinto. |
| Todo lo demás | En el **servidor**, por usuario | Te sigue en cualquier navegador donde entres con tu usuario. No afecta a los demás usuarios. |

> **Nota:** El tamaño de letra de la [Terminal](29-terminal.md) también se guarda en el navegador, no aquí.

Los cambios se aplican y se guardan al instante, sin botón de guardar. Verás el aviso **Apariencia guardada** (excepto con el tema, que no se envía al servidor).

## Cambiar entre tema claro y oscuro

Tienes dos formas:

- **Rápida:** pulsa el botón de sol/luna en la esquina superior derecha de cualquier página. También está en la pantalla de inicio de sesión.
- **En Apariencia:** en la tarjeta **Tema**, elige **Según el sistema**, **Claro** u **Oscuro**.

**Según el sistema** sigue la configuración de tu sistema operativo. Es la opción por defecto. Si usas el botón de sol/luna, el tema queda fijo en claro u oscuro; vuelve a **Según el sistema** desde esta página.

## Elegir el color de acento

El color de acento se usa en botones, enlaces, selección y gráficos.

1. En la tarjeta **Color de acento**, pulsa uno de los círculos de color. Pasa el mouse para ver su nombre: **Fósforo (predeterminado)**, **Azul**, **Índigo**, **Violeta**, **Rosa**, **Rojo**, **Naranja**, **Ámbar**, **Verde**, **Cian** o **Pizarra**.
2. Para otro color, pulsa **Personalizado** y elígelo en el selector.

El panel ajusta solo el color del texto sobre el acento para que se lea bien, tanto en tema claro como oscuro.

## Personalizar la barra lateral

En la tarjeta **Barra lateral**, elige un estilo:

| Opción | Resultado |
|---|---|
| **Azul oscuro** | Predeterminado. |
| **Clara** | Barra con fondo claro, igual al del panel. |
| **Grafito** | Gris muy oscuro. |
| **Color de acento** | Versión oscura del color de acento. |
| **Personalizado** | Cualquier color, con el selector de color. Si el color es claro, el texto de la barra pasa a oscuro. |

### Usar la barra compacta

Marca **Barra compacta → Activar** para mostrar solo los iconos del menú. El nombre de cada sección aparece al pasar el mouse. Útil en pantallas pequeñas.

## Ajustar texto y espaciado

En la tarjeta **Texto y espaciado**:

| Opción | Valores | Para qué sirve |
|---|---|---|
| **Tamaño** | **90 %**, **100 %**, **110 %**, **120 %** | Escala todo el panel. Sube el tamaño si lees con dificultad. |
| **Densidad** | **Cómoda**, **Compacta** | Compacta muestra más filas por pantalla. |
| **Tipografía** | **IBM Plex**, **Del sistema** | La fuente del panel o la de tu sistema operativo. |
| **Bordes** | **Rectos**, **Suaves**, **Redondeados** | Forma de las esquinas de tarjetas y botones. |
| **Animaciones** | **Activadas**, **Desactivadas** | Desactívalas si prefieres un panel más quieto o el equipo es lento. |

## Configurar el dashboard

En la tarjeta **Dashboard**:

1. En **Secciones visibles**, marca o desmarca: **Tacómetros**, **Historial**, **Información del sistema**, **Procesos y almacenamiento** y **Accesos rápidos**.
2. En **Rango del historial**, elige **5 min**, **15 min**, **1 h**, **6 h**, **24 h** o **7 días** para las gráficas.

Ver [Dashboard](10-dashboard.md) para saber qué muestra cada sección.

## Restablecer la apariencia

1. Pulsa **Restablecer apariencia** al final de la página.
2. Confirma en **Restablecer apariencia** con **Restablecer**.

Vuelven a sus valores por defecto los colores, la barra lateral, los tamaños y las secciones del dashboard.

> **Nota:** **Restablecer apariencia** no cambia el tema. Si quieres volver al predeterminado, elige **Según el sistema** en la tarjeta **Tema**.

## Preguntas frecuentes

### Cambié a tema oscuro y en otro equipo sigue claro.

Es lo esperado. El tema se guarda en cada navegador. Cámbialo también en el otro equipo.

### ¿Mis cambios afectan a los otros administradores?

No. Cada usuario tiene su propia apariencia. Los demás siguen viendo la suya.

### Sale "No se pudo guardar" al cambiar una opción.

El servidor no pudo escribir el archivo de preferencias (`/var/lib/matipanel/prefs.json`). Revisa que haya espacio en disco ([Discos](15-discos.md)) y que el servicio del panel funcione bien.

### Puse un color personalizado y el texto de la barra no se lee.

Prueba un color más oscuro o más claro. Con colores intermedios el contraste puede ser bajo. También puedes volver a **Azul oscuro**.

## Ver también

- [Dashboard](10-dashboard.md)
- [Primeros pasos](03-primeros-pasos.md)
- [Terminal](29-terminal.md)
- [Seguridad y 2FA](30-seguridad-2fa.md)
