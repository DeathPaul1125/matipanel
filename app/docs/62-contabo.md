# Contabo (VPS)

Si tus servidores están en Contabo, desde aquí los controlas **por fuera**: encenderlos, apagarlos, reiniciarlos y guardar snapshots del disco, sin entrar a la web de Contabo. Lo encuentras en **Menú → Red y acceso → Contabo (VPS)**.

MatiPanel administra el servidor por dentro (sitios, bases de datos, servicios). Este módulo habla con la API de Contabo, que maneja la máquina virtual completa. Ves **todos los VPS de la cuenta**, no solo aquel donde está instalado el panel.

## Antes de empezar

- El servidor necesita salida a Internet por HTTPS (`443/tcp`) hacia `auth.contabo.com` y `api.contabo.com`.
- Los **Storage VPS** no aparecen: Contabo no los ofrece por su API.
- Necesitas cuatro datos de tu cuenta. Están en [my.contabo.com → API](https://my.contabo.com/api/details).

| Dato | Qué es |
|---|---|
| **Client ID** | Identificador de tu cuenta para la API, por ejemplo `INT-12345678` |
| **Client Secret** | Clave que acompaña al Client ID |
| **Usuario de la API** | El correo con el que entras a Contabo |
| **Contraseña de la API** | Una contraseña **aparte**, que defines en esa misma página. No es la de tu cuenta |

## Conectar la cuenta

1. Entra a [my.contabo.com → API](https://my.contabo.com/api/details). Si aún no tienes contraseña de la API, créala ahí.
2. En el panel ve a **Menú → Red y acceso → Contabo (VPS)**.
3. Escribe los cuatro datos y pulsa **Probar y guardar**.

El panel comprueba los datos contra Contabo antes de guardarlos. Si Contabo los rechaza, no se guarda nada y verás el motivo.

> **Importante:** Las credenciales se guardan solo en este servidor, en `/var/lib/matipanel/contabo.json`, que únicamente puede leer root. Nunca se muestran de nuevo en la pantalla ni quedan en la Auditoría. Quien tenga estos datos puede apagar o reinstalar tus VPS: si sospechas que se filtraron, cambia la contraseña de la API en Contabo.

Para cambiarlas o quitarlas, pulsa **Cuenta**. Al editar puedes dejar vacíos el secreto y la contraseña para conservar los guardados. **Desconectar cuenta** borra las credenciales de este servidor; tus VPS no cambian.

## Ver tus VPS

Cada VPS tiene una tarjeta con su nombre, IP, estado, núcleos, memoria, disco, plan y región. La lista se actualiza cada 30 segundos.

| Estado | Significado |
|---|---|
| **Encendido** | Funcionando |
| **Apagado** | Detenido. Sigue generando cobro en Contabo |
| **Preparando** / **Instalando** | Contabo lo está creando o reinstalando |
| **Modo rescate** | Arrancado con el sistema de rescate de Contabo |
| **Error** / **Pago pendiente** | Requiere atención en my.contabo.com |

El VPS donde corre este panel aparece primero, con la etiqueta **Este servidor**. El panel lo reconoce comparando sus IP con las que informa Contabo.

## Encender, apagar y reiniciar

| Botón | Qué hace | Cuándo usarlo |
|---|---|---|
| **Encender** | Arranca un VPS apagado | — |
| **Reiniciar** | Reinicia el VPS | Tras actualizar el kernel, o si dejó de responder |
| **Apagar** | Pide al sistema que cierre sus servicios en orden y se apague | La forma normal de apagar |
| **Forzar apagado** | Corta la energía, como desconectar el cable | Solo si **Apagar** no funciona. Puede dañar bases de datos o archivos a medio escribir |

Después de pulsar, Contabo tarda unos segundos en reflejar el cambio. El panel vuelve a consultar solo.

### Si es este mismo servidor

- **Reiniciar** funciona: el panel deja de responder uno o dos minutos y vuelve solo.
- **Apagar** y **Forzar apagado** apagan también el panel. **No podrás encenderlo desde aquí**: tendrás que hacerlo en my.contabo.com o desde otro MatiPanel conectado a la misma cuenta. Por eso el panel te pide escribir el nombre del VPS para confirmar.

> **Consejo:** Si administras varios VPS, conecta la cuenta de Contabo en más de uno. Así siempre queda un panel encendido desde el cual levantar a los demás.

## Snapshots

Un snapshot es una foto del disco completo del VPS en un momento. Sirve para volver atrás si una actualización o un cambio sale mal. Pulsa **Snapshots** en la tarjeta del VPS.

### Crear un snapshot

1. Pulsa **Crear snapshot**.
2. Escribe un nombre de hasta 30 caracteres. Admite letras sin acento, números, espacios y guiones. Por ejemplo, `antes-de-actualizar`.
3. Si quieres, agrega una descripción.
4. Pulsa **Crear snapshot**.

El VPS sigue funcionando mientras se crea. Cuántos snapshots puedes tener depende de tu plan en Contabo; si llegas al límite, Contabo rechaza el nuevo y el panel te lo dice. Cada snapshot se borra solo en la fecha de la columna **Se borra**.

### Revertir a un snapshot

**Revertir** devuelve el disco del VPS al momento del snapshot.

> **Importante:** Se pierde **todo** lo que cambió después: archivos, bases de datos, configuración y correos. No se puede deshacer. Si dudas, crea primero otro snapshot del estado actual.

El panel te pide escribir `REVERTIR` para confirmar. Si el VPS es este mismo servidor, el panel y sus datos (usuarios, permisos, auditoría) también vuelven a ese momento.

### Eliminar un snapshot

**Eliminar** lo borra de Contabo y libera el cupo. Ya no podrás volver a ese punto.

## Permisos y auditoría

- Un usuario de **solo lectura** ve la lista y los snapshots, pero no puede encender, apagar, revertir ni cambiar la cuenta. Ver [Permisos del panel](35-permisos.md).
- Cada acción queda en la [Auditoría](32-auditoria.md) con quién la hizo y desde dónde.
- Encender, apagar o reiniciar un VPS, revertir o eliminar un snapshot y cambiar las credenciales se avisan por correo como **cambios delicados**, si tienes activas las [Notificaciones](33-notificaciones.md).

## Problemas frecuentes

| Mensaje | Causa y solución |
|---|---|
| **Contabo rechazó las credenciales** | Alguno de los cuatro datos está mal. Lo más común es usar la contraseña de la cuenta en vez de la **contraseña de la API** |
| **No se pudo conectar con Contabo** | El servidor no tiene salida a Internet por `443/tcp`, o falla el DNS. Revisa el [Firewall](20-firewall.md) |
| **Esta acción requiere un servicio de pago adicional** | El plan no incluye esa función o llegaste al límite de snapshots |
| **Contabo no puede hacerlo ahora** | Hay otra operación en curso en ese VPS. Espera a que termine y reintenta |
| **Contabo limitó las peticiones** | Demasiadas llamadas seguidas. Espera un minuto |
| Ningún VPS dice **Este servidor** | El panel está en un servidor que no es de esa cuenta, o detrás de una IP distinta a la que conoce Contabo. No afecta al uso |
