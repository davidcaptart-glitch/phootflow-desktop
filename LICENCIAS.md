# Licencias de PhootFlow

Sirven para dar acceso a compañeros fotógrafos, ver quién usa la app y en qué equipo, y quitar el acceso cuando
quieras. Son el primer paso para venderla.

## Cómo funciona

| Pieza | Dónde | Qué hace |
|---|---|---|
| `license.py` | en la app | Al abrir PhootFlow pide la clave (la primera vez), la valida en línea y guarda un token firmado en `%APPDATA%\PhootFlow\licencia.json`. Se vuelve a comprobar cada 6 h. |
| Edge Function `licencias` | Supabase, proyecto **phootflow** (`exlegzodfrydddgcvbrp`, eu-central-1) | Comprueba la clave, registra el equipo y firma el token con Ed25519. Código: `servidor_licencias/functions/licencias/index.ts`. |
| Tablas `licencias`, `equipos`, `ajustes` | la misma base de datos | Las claves, los equipos vinculados y el par de claves de firma. RLS activo y sin políticas: solo entra el rol de servicio. Esquema: `servidor_licencias/migraciones/001_licencias.sql`. |
| `admin_licencias.py` | solo en tu PC | Crear, ver, revocar, reactivar, liberar equipos y cambiar límites. No va en el paquete de la app. |

Lo que acordamos:

- **1 equipo por clave** y **7 días sin conexión**, sin caducidad. Puedes cambiarlo para cada clave (`cambiar`).
- La clave queda vinculada al equipo con una huella (el `MachineGuid` de Windows pasado por SHA-256). No cambia al
  reinstalar PhootFlow ni al renombrar el PC. Sí cambia si se reinstala Windows: en ese caso, `liberar`.
- Si la clave está **revocada** o **caducada**, o lleva más de 7 días sin comprobarse, la app lo dice y no deja
  trabajar. No se toca nada de lo que ya se ha exportado. Si se revoca con la app abierta, una exportación
  en marcha termina igualmente.
- El token lo firma el servidor y la app solo tiene la clave pública, así que editar `licencia.json` no sirve.
  Copiarlo a otro PC tampoco, porque la huella no coincide. Retrasar el reloj del equipo se detecta.
- La firma se verifica en Python puro, sin DLL nuevas que Smart App Control pueda bloquear.
- Si Supabase está caído o en pausa, la app lo trata como «sin conexión»: sigue funcionando hasta 7 días.
- El selftest del build no necesita licencia.

## Puesta en marcha (una vez)

1. **Clave secreta de Supabase.** En el panel de Supabase, abre el proyecto *phootflow* → *Project Settings* →
   *API Keys* y copia una **secret key** (`sb_secret_…`; la antigua `service_role` también vale). Guárdala en
   `%APPDATA%\PhootFlow\admin.env` así:

   ```
   PHOOTFLOW_SERVICE_KEY=sb_secret_xxxxxxxx
   ```

   También vale la variable de entorno `PHOOTFLOW_SERVICE_KEY`. **Nunca la metas en el repo**: da acceso total a
   la base de datos. `.gitignore` ya ignora `admin.env` y `*.env`.
2. **Tu propia clave**, antes de instalar la versión con licencias:
   `python admin_licencias.py crear "David" --notas "mi PC"`.
3. **Repo de releases** (ver más abajo).

## Herramienta de administración

Se ejecuta desde la carpeta del código (`python admin_licencias.py …`). Donde se pide una licencia, vale la clave
o un trozo del nombre del titular, siempre que solo coincida una.

```
python admin_licencias.py crear "Ana Pérez" --email ana@ejemplo.com --notas "bodas Sevilla"
python admin_licencias.py listar
python admin_licencias.py ver ana
python admin_licencias.py revocar ana
python admin_licencias.py reactivar ana
python admin_licencias.py liberar ana                 # desvincula todos sus equipos
python admin_licencias.py liberar ana PORTATIL-ANA    # solo ese (nombre o inicio de la huella)
python admin_licencias.py cambiar ana --equipos 2 --dias 14
python admin_licencias.py cambiar ana --caduca 2027-12-31   # o --caduca nunca
```

- `crear` muestra la clave (`PF-XXXXX-XXXXX-XXXXX-XXXXX`, con 100 bits aleatorios) para que se la mandes al titular.
- `listar` y `ver` muestran el estado de cada clave y sus equipos: nombre del PC, versión de PhootFlow, primera
  activación y último uso.
- **Cuánto tarda en notarse un `revocar`:** al abrir la app o en la siguiente comprobación (cada 6 h). Si el
  equipo está sin conexión, en 7 días como mucho.
- **Cambio de PC:** el titular verá «Esta clave ya está en uso en otro equipo». Usa `liberar` y podrá activarla
  en el nuevo equipo.
- Para consultas puntuales también puedes usar el *Table Editor* de Supabase (tablas `licencias` y `equipos`).
  **No toques la tabla `ajustes`**: guarda la clave de firma. Si se borra, el servidor crea otra y todas las
  apps instaladas dejarían de aceptar sus tokens.

## Repo privado y repo público de releases

Con el código a la vista, cualquiera puede quitar la comprobación. Por eso el código pasa a privado y las
versiones se publican en un repo público que solo tiene releases: `davidcaptart-glitch/phootflow-releases`.
Ya está cambiado en esta rama:

- `updater.py` (`REPO`) e `instalar.ps1` buscan las versiones en `phootflow-releases`. El enlace del README
  también apunta ahí.
- `build.yml` publica la release en los dos repos. En `phootflow-releases` usa el secreto `RELEASES_TOKEN` y
  sube también `instalar.ps1`, para que los compañeros puedan descargarlo sin acceso al código. Si falta el
  secreto, el build falla a propósito: si no, las apps instaladas no verían la versión nueva.
- `license.py` va en el `.exe` y en `PhootFlow-src.zip`. `admin_licencias.py` y `servidor_licencias/` no.

**Pendiente (lo haces tú; no he cambiado nada en GitHub):**

1. Crear el repo **público** `phootflow-releases` con un README, para que tenga al menos un commit.
2. Crear un *fine-grained token* con permiso *Contents: Read and write* solo sobre `phootflow-releases` y
   guardarlo como secreto `RELEASES_TOKEN` en `phootflow-desktop` (*Settings → Secrets and variables → Actions*).
3. **Orden de la transición.** Las copias instaladas (0.6.0 y anteriores) buscan versiones en `phootflow-desktop`.
   Publica la primera versión con licencias **mientras `phootflow-desktop` sigue siendo público**. El build la
   sube a los dos repos, y así esas copias se actualizan y pasan a mirar `phootflow-releases`. Cuando tus equipos
   ya la tengan, haz `phootflow-desktop` privado.
4. Ten en cuenta:
   - Lo que ya se publicó sigue en clones y forks que existan.
   - En repos privados, GitHub Actions tiene un cupo gratuito de minutos y los runners de Windows gastan el doble.
   - La instalación «source» lleva los `.py` legibles: frena a la mayoría, pero no a alguien que sepa Python.
     Para endurecerlo más adelante: un `.exe` firmado con certificado de código (además, Smart App Control lo
     aceptaría) o compilar con Nuitka.

## Supabase: cosas a vigilar

- El proyecto está en el plan **gratis**. Para crearlo se pausó **linked-and-in** (límite de 2 proyectos activos).
  Para reanudarlo habría que pausar otro.
- Los proyectos gratuitos se pausan tras una semana sin actividad. Mientras se usen las apps habrá peticiones,
  pero si nadie abre PhootFlow en 7 días el proyecto puede pausarse. Entonces las apps funcionan sin conexión
  hasta 7 días, y después hay que reanudar el proyecto desde el panel. Cuando haya clientes de pago, conviene
  pasar a Pro.
- Para volver a desplegar la función: el código está en `servidor_licencias/functions/licencias/index.ts`.
  Despliégala con `verify_jwt = false` (la app no lleva claves de Supabase).

## Pendiente para cobrar

- **Pasarela de pago.** Stripe Checkout o Lemon Squeezy, este último si quieres que gestione el IVA como
  *merchant of record*. Un webhook iría a una segunda Edge Function que crea la licencia (`crear`) y manda la
  clave por email (Resend, por ejemplo). La tabla ya tiene `email` y `caduca`.
- **Suscripciones.** Renovar es alargar `caduca` cuando llega el webhook del pago. La app ya trata «caducada».
- **Autoservicio.** Que el usuario libere su propio equipo desde la app («desactivar en este equipo»). Hoy lo
  hace David con `liberar`.
- **Panel web de administración**, si `admin_licencias.py` se queda corto.
