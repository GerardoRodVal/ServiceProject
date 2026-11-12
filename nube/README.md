# Captura · entorno en la nube (simulado en local)

Es la versión «de nube» del servicio, pensada para correr en tu PC con las mismas piezas que se usarán en Azure.
Cambiar de la PC a Azure es cambiar variables de entorno y publicar el contenedor; el código no cambia.

> **Estado (2026-11-05):** las 121 pruebas pasan contra PostgreSQL 16, Azurite y el proveedor OIDC simulado
> (`docker compose --profile herramientas run --rm pruebas`): aislamiento entre negocios, duplicados, permisos, fotos,
> medición de uso, carga por lotes, migración, inicio de sesión con PKCE y validación de tokens. El gestor de Windows se
> puede conectar a esta API (ver «Conectar el gestor de Windows») y `integracion/probar_gestor_nube.py` recorre ese flujo
> de punta a punta. La app del celular también puede trabajar directo con la API (ver «El celular directo a la nube»);
> esa parte del navegador se probó a mano en el navegador integrado, no hay una prueba automática de la interfaz.
> Lo que **no** se ha probado es todo lo que solo existe en Azure: Entra External ID real, identidad administrada,
> SSL de PostgreSQL Flexible Server, HTTPS y el rendimiento en la nube.

## Equivalencias local → Azure

| Pieza | En tu PC | En Azure |
|---|---|---|
| Base de datos | PostgreSQL 16 (`db`) | Azure Database for PostgreSQL Flexible Server |
| Archivos y fotos | Azurite (`azurite`) | Azure Blob Storage |
| API | Contenedor `api` | App Service o Container Apps |
| Inicio de sesión del celular | Proveedor OIDC simulado (`idp`, puerto 9000) | Microsoft Entra External ID (mismo flujo: código con PKCE) |
| Inicio de sesión del gestor | Tokens de prueba (`/dev/token`) | Pendiente: credenciales propias del gestor (ver «Lo que falta») |
| Contraseñas y llaves | Variables de entorno / `.env` | Key Vault + identidad administrada |

## Arranque

Desde esta carpeta (`nube/`), con Docker Desktop abierto:

```powershell
docker compose up -d --build        # PostgreSQL + Azurite + proveedor de login (:9000) + API y app del celular (:8000)
docker compose --profile herramientas run --rm pruebas   # todas las pruebas contra la base y el almacenamiento reales
docker compose logs -f api          # ver la API
docker compose down                 # apagar (los datos se conservan en los volúmenes)
docker compose down -v              # apagar y BORRAR todos los datos locales
```

Todo escucha solo en `127.0.0.1`: nada se ve desde la red. Las contraseñas por omisión son solo de desarrollo;
para cambiarlas copia `.env.example` como `.env`.

### Probar a mano

El alta de negocios no está en la API pública a propósito (el rol de la API no puede crearlos). Se hace con la herramienta
de alta de clientes (ver «Alta de clientes»):

```powershell
docker compose --profile herramientas run --rm clientes crear --nombre "Mi negocio" --admin-correo yo@empresa.com
```

Luego pide un token y úsalo (la documentación interactiva está en http://127.0.0.1:8000/docs solo en local):

```powershell
$t = (Invoke-RestMethod -Method Post http://127.0.0.1:8000/dev/token -ContentType application/json -Body '{"correo":"yo@empresa.com"}').token
Invoke-RestMethod http://127.0.0.1:8000/api/visitas -Headers @{ Authorization = "Bearer $t" }
```

## Migrar tus datos del gestor de Windows

Copia lo que hay en `%LOCALAPPDATA%\CapturaVisitas` (`visitas.db`) y el `config.json`. Se puede repetir sin duplicar:

```powershell
docker compose --profile herramientas run --rm -v "$env:LOCALAPPDATA\CapturaVisitas:/datos:ro" migracion `
  --sqlite /datos/visitas.db --config /datos/config.json --negocio "Mi inmobiliaria" --admin-correo yo@empresa.com
```

- Conserva estado de seguimiento, fecha original y los marcados como ejemplo o importados.
- No migra `acceso` (la URL del celular) ni nada que parezca contraseña o token.
- Los registros migrados no cuentan como uso del mes.
- Si alguna fila se omite (estado raro, id inválido, JSON roto) la lista sale al final y el comando termina con código 2.

## Conectar el gestor de Windows

El gestor sigue siendo el punto de entrada del celular; con la nube activa guarda y lee los prospectos en esta API
en vez de en su SQLite:

```
celular ──(Wi-Fi, como hoy)──▶ gestor de Windows ──(HTTP/HTTPS)──▶ API ──▶ PostgreSQL
                               panel (127.0.0.1) ──────────────────▶ API
```

1. Enciende el entorno (`docker compose up -d`) y crea el negocio y su administrador (ver «Probar a mano»).
2. En el gestor: **Conexiones → Nube**. Escribe la dirección (`http://127.0.0.1:8000`) y el correo del administrador,
   pulsa **Probar conexión**, luego **Subir mis datos actuales** (copia tus prospectos de SQLite a la nube sin
   duplicar ni pisar estados) y por último **Activar**.
3. Desde ese momento el panel muestra los prospectos de la nube: cambiar estados, cargar un Excel, «Borrar cargados» y
   los datos de ejemplo operan en la nube. Taller y ventas siguen en SQLite.

Detalles que conviene saber:

- **Si la nube no responde**, el gestor contesta 503 al celular, que **conserva la visita y reintenta** (ya lo hacía), y el
  panel avisa una vez y sigue mostrando los últimos datos. Al volver la conexión no se duplica nada.
- **La configuración** (dirección y correo, sin contraseñas) vive en `%LOCALAPPDATA%\CapturaVisitas\nube.json`, fuera de
  OneDrive y de git. Una dirección `http://` solo se acepta hacia esta misma PC; hacia fuera debe ser `https://`.
- **Desactivar la nube** vuelve a SQLite. Los dos almacenes **no se sincronizan entre sí**: lo capturado mientras la
  nube estuvo activa no aparece en SQLite. Para volver a la nube sin perder nada, usa «Subir mis datos actuales».
- **Uso del mes:** solo cuentan las visitas capturadas en el celular. Las cargas de archivo, Kommo, ejemplos y la
  subida inicial no cuentan (hay que decidir si esto debe cambiar cuando se definan los planes).
- **Quién es el usuario ante la API:** el gestor se identifica con el correo del administrador configurado. En local
  obtiene un token de prueba (`/dev/token`); en una nube real (`AUTH_MODO=oidc`) ese endpoint no existe y el gestor
  mostrará que falta conectar el inicio de sesión (ver «Lo que falta»).
- **Prueba de punta a punta:** `python nube/integracion/probar_gestor_nube.py` (con el entorno encendido). Arranca un
  gestor de prueba con base temporal, crea un negocio de prueba, recorre el flujo (incluida una caída de la nube) y
  borra todo al terminar. Modifica `captura-visitas-pwa/config.json` unos segundos (asesor de prueba) y lo restaura.

## El celular directo a la nube

La API también sirve la app del celular. Abierta desde la API (`http://127.0.0.1:8000/` en local), la app detecta que está
en la nube y trabaja sin el gestor de Windows:

```
celular ──(HTTPS)──▶ API ──▶ PostgreSQL          inicio de sesión: celular ──▶ proveedor OIDC (Entra en Azure)
```

- **Cómo lo sabe:** la API publica `/app-config.json` (dónde iniciar sesión). El gestor no lo tiene, así que la app abierta
  desde el gestor sigue como siempre (modo clásico), sin cambios.
- **Inicio de sesión:** OpenID Connect con código de autorización y PKCE, el mismo flujo de Entra External ID. En local lo
  atiende el servicio `idp` (solo desarrollo; contraseñas en memoria). La app guarda los tokens en el celular, los renueva
  sola con el refresh token (que se rota en cada uso) y, sin señal, sigue capturando: las visitas quedan en el celular y
  salen al volver la red.
- **Quién captura:** la visita queda a nombre de quien inició sesión (nombre y id los pone la API, no el celular). Si otra
  persona entra en el mismo celular, las visitas pendientes de la anterior no se envían con la sesión nueva.
- **Configuración:** el formulario, las propiedades y los pesos del score salen de `/api/app/config`, por negocio. El
  gestor la publica solo: al activar la nube y cada vez que guarda la configuración. No viajan los accesos, la IA, el taller
  ni la lista de asesores.
- **Qué sirve la API:** solo los archivos de la app, la vista del cliente y la página de presentación de NearLeads
  (`/inicio`, archivo `captura-visitas-pwa/inicio.html`; ver `app/web.py`). `config.json` y las páginas del taller no.
  «Iniciar sesión» en la presentación lleva a la app (`/`); «Crear cuenta» solo muestra una confirmación, todavía no
  envía la solicitud a ningún lado.

Probarlo en local (con el entorno encendido):

1. Crea el negocio con su administrador (ver «Alta de clientes») y, desde el gestor, da de alta a un `capturista` con su nombre.
2. Registra al capturista en el proveedor simulado:
   `Invoke-RestMethod -Method Post http://127.0.0.1:9000/dev/usuarios -ContentType application/json -Body '{"correo":"laura@empresa.com","clave":"UnaClave-123","nombre":"Laura"}'`
3. Publica la configuración (activando la nube en el gestor, o `PUT /api/config` como administrador).
4. Abre `http://127.0.0.1:8000/`, pulsa **Entrar** y usa ese correo y esa clave.

En el navegador de tu PC sirve `127.0.0.1` (cuenta como contexto seguro). **Desde un celular real no**: el celular necesita
HTTPS para instalar la app y trabajar sin señal, y `127.0.0.1` es el propio celular. Eso se resuelve al llevarlo a Azure
(App Service da HTTPS) o con un túnel HTTPS.

En Azure: se reemplaza `idp` por Entra External ID (se registra la app como SPA con la dirección de la API como URI de
redirección `https://<tu-api>/`), y se configuran `PWA_OIDC_EMISOR`, `PWA_OIDC_CLIENT_ID`, `PWA_OIDC_SCOPE` y los `OIDC_*` de
la API (con `AUTH_MODO=oidc`, que además quita `/dev/token`).

## La app Android (APK) con la nube

La APK (`captura-visitas-android/`, versión 1.1.0) envuelve la misma pantalla de captura y ahora trabaja con **la PC o con la
nube**, según la dirección que guarde el usuario (menú ⋮ → «Dirección de la PC o la nube»):

- **PC:** `192.168.1.50:8765` (con puerto). Todo como antes.
- **Nube:** `https://captura.tuempresa.com` (solo https; el puerto es opcional). La app le pregunta a esa dirección
  `/app-config.json`: si responde que es una nube, entra en modo nube; si el gestor responde 404, sigue en modo PC. Sin
  señal recuerda el último modo. Con la nube, una dirección `http://` se rechaza (viajan sesiones y datos).
- **Inicio de sesión en el navegador del sistema, no dentro de la app** (lo que recomiendan Microsoft y Google y lo que
  exigen algunos proveedores): la app abre la pantalla del proveedor, y al terminar el navegador devuelve al usuario a la app
  con el enlace `com.nearustech.capturavisitas:/oauth?code=…&state=…`. La app canjea el código con PKCE y valida el `state`
  antes de usarlo (una app ajena que dispare ese enlace no inicia sesión). Si Android cierra la app mientras el usuario está
  en el navegador, la vuelta se recoge al reabrir.
- **A dónde puede hablar la parte nativa** (`Destinos.java`): solo a la dirección guardada y, en modo nube, al **proveedor
  que esa nube declara** en `/app-config.json`, que debe ser https; se comparan origen contra origen (no «empieza con»). La
  página no puede usar el puente para hablar con otros sitios, y solo puede abrir en el navegador el inicio de sesión de ese
  proveedor.
- **Lo que hay que registrar en el proveedor:** el URI de redirección `com.nearustech.capturavisitas:/oauth` (en Entra,
  plataforma «Aplicaciones móviles y de escritorio»; en el proveedor simulado ya está en `IDP_REDIRECTS`). El proveedor debe
  tener el punto de autorización y el de tokens en el mismo host que el emisor (así es en Entra).
- **Arreglo de paso:** la APK anterior no incluía el lector de QR, así que en la pestaña Casa decía «No se pudo generar el QR»;
  la 1.1.0 lo trae.

Cómo se probó: las reglas de `Destinos.java` con 38 comprobaciones en Java a secas
(`app/src/test`, ver `LEEME.md`); la APK compila, se firma y se revisó su manifiesto y sus archivos; y la lógica web
dentro de la APK en el navegador con un puente simulado que copia esas reglas (inicio de sesión con regreso por el enlace de
la app, arranque en frío, renovación del token, envío de una visita a la nube, rechazo de otros sitios, y el modo PC contra
un gestor). **No se probó en un teléfono ni en un emulador** (no hay uno instalado), ni contra una nube https real ni Entra.
El proveedor simulado local (`http://127.0.0.1:9000`) no sirve para una APK en un teléfono: el emisor debe ser https.

## Cita y ubicación

En modo nube la app tiene la misma cita que con el gestor: empieza al iniciar sesión, el cronómetro corre aunque la app
esté en segundo plano y dura hasta «Terminar cita» (o hasta cerrar sesión). Mientras la cita está en curso y la app está a la
vista, envía la ubicación cada 30 segundos. Las rutas de la app son las mismas que las del gestor (`/api/app/estado`,
`/api/app/cita`, `/api/app/ubicacion`, `/api/app/salir`), así que la app las usa igual en los dos modos.

- **Guardado:** tablas `citas` y `ubicaciones` (migración 003), con seguridad por fila como el resto. Cada asesor tiene a lo
  sumo una cita en curso (lo garantiza la base), y de una cita solo se puede cambiar cómo termina.
- **Límites:** la ubicación solo se acepta con una cita en curso; mínimo 5 segundos entre puntos del trayecto y un tope
  de 2000 puntos por cita (la última ubicación sí se sigue actualizando).
- **Sin señal:** si la cita empezó sin conexión, la nube no la conoce; la app la registra con su hora real (hasta 12 h
  atrás) al enviar el primer punto o al terminarla, y el resumen sale con la duración correcta.
- **Privacidad:** la ubicación de las personas es un dato sensible. Al terminar cada cita la API borra el trayecto y las
  ubicaciones finales de más de `RETENCION_UBICACION_DIAS` (30 por omisión). El aviso de privacidad del negocio debe decirle a su
  gente que se registra la ubicación durante la cita; la app ya muestra cuándo está activa.
- **Administrador, en la API:** `GET /api/citas/activas` (quién está en cita y su última ubicación), `GET /api/citas`
  (terminadas) y `GET /api/citas/{id}/trayecto`.
- **Administrador, en el gestor:** pestaña **Citas** del giro Inmobiliarias. Muestra quién está en cita ahora (con el
  cronómetro en vivo y un enlace a su última ubicación), las citas terminadas con filtro por periodo y asesor, y el
  trayecto de cada una: un dibujo del recorrido con inicio, fin, distancia aproximada y la tabla de puntos. Se actualiza
  sola cada 10 segundos. Lee de la nube cuando está activa y, si no, de esta PC (citas en curso: memoria del gestor;
  terminadas: `citas.jsonl`). El trayecto no se envía a ningún servicio de mapas; los enlaces a OpenStreetMap solo se
  abren si pulsas uno.

## Consejos y fotos de las casas

**Consejos.** `GET /api/app/consejos` calcula, con las visitas de quien inició sesión y los pesos de su negocio, los mismos
consejos que el gestor (misma lógica y mismos textos, máximo seis). `integracion/probar_gestor_nube.py` comprueba que la
respuesta de la API sea idéntica a la de `asesor_app.consejos` del gestor con las mismas visitas.

**Fotos del catálogo.** Las sube el gestor al publicar la configuración (al guardarla con la nube activa, al activarla y
con «Subir mis datos actuales»): pregunta a la API qué fotos de los inmuebles faltan, sube solo esas (JPEG, hasta 4 MB) y la
API borra las que ya no están en el catálogo. Se guardan en Blob Storage (Azurite en local) con el mismo identificador
que tienen en el gestor.

- **La lectura es pública por identificador** (`GET /api/imagen/{id}`, sin sesión): el celular las pide con una
  etiqueta `<img>`, que no puede mandar credenciales. El identificador son 128 bits al azar, así que no se puede
  adivinar, pero **quien tenga el enlace puede ver la foto**; son fotos de casas en venta, pensadas para mostrarse a
  clientes. Si algún negocio necesitara fotos privadas habría que pasar a enlaces firmados.
- **Un identificador solo puede ser de un negocio** (índice único en toda la base): un negocio no puede registrar el de otro
  para que se sirva su imagen.
- **Sin señal:** la app guarda las fotos que ya vio (el service worker las cachea: su dirección cambia cuando cambia la foto).
- **Si una foto del catálogo ya no está en la PC del gestor** (por ejemplo, borraron la carpeta) no se puede subir: el
  gestor lo avisa al guardar y la casa se ve «Sin foto» en el celular.

## Vista del cliente (el QR de cada casa)

En la pestaña **Casa** el asesor elige una casa y la app muestra un QR; el cliente lo escanea y ve la ficha en su propio
celular (`/cliente.html`). En modo nube el enlace es `https://<tu-api>/cliente.html?n=<enlace del negocio>&p=<casa>`.

- **Es pública, sin sesión** (el cliente no tiene cuenta), y se protege con dos cosas: el **enlace del negocio** (`n`,
  128 bits al azar, columna `negocios.token_publico`) y el identificador de la casa. `GET /api/publico/{n}/casa/{id}`
  devuelve **solo una ficha con campos permitidos**: nombre del negocio, zona, y de la casa nombre, dirección,
  descripción, precio, recámaras, baños, cajones, m², entrega, estatus y foto. No salen cuotas, motivo de venta, score,
  asesores ni ninguna otra parte de la configuración.
- **Cualquier enlace malo da el mismo 404** («No encontramos la casa.»): token incorrecto, casa inexistente, casa de otro
  negocio o caracteres raros. No se puede averiguar qué parte falló ni listar casas.
- **Lo que sí implica:** quien tenga el QR o el enlace puede ver esa casa, y el enlace del negocio sirve para todas las
  casas de ese negocio (si alguien lo conoce, puede probar identificadores de casa). Los ids de casa vienen del nombre
  del modelo, así que son adivinables; lo que protege es el enlace del negocio. Por eso la respuesta no lleva nada que
  no se le mostraría a un cliente, y la página no manda el enlace a terceros (`Referrer-Policy: no-referrer`,
  `X-Robots-Tag: noindex`, `Cache-Control: no-cache`).
- **Rotar el enlace:** en el gestor, **Conexión → Nube → «Cambiar el enlace de los QR»** (pide confirmación; solo con la
  nube activa). Por debajo es `POST /api/publico/rotar` (solo administrador). **Los QR ya impresos o enviados dejan de
  abrir.** Los celulares reciben el nuevo con `/api/app/config`: la app lo revisa cada minuto con señal y repinta el QR de
  la casa elegida sin esperar. Un celular sin señal sigue mostrando el QR viejo (que ya no abre) hasta que se conecte.
- **En la base**, `app_api` solo puede modificar `token_publico` de `negocios` (no el nombre, giro ni id), y la lectura
  pública pasa por la función `config_publica`, que solo responde con un enlace válido.
- **Sin QR todavía:** si el celular nunca ha cargado la configuración (primera vez, sin señal) la pestaña lo avisa y el QR
  aparece al conectarse.
- Con el gestor (modo clásico) todo sigue igual: sin `n`, `cliente.html` lee `config.json` del gestor.
- Se corrigieron los estatus: el gestor guarda «apartado» y «vendido» y la página solo reconocía el femenino, así que esas
  casas no mostraban su estatus.

## Dar de alta a los usuarios

Quién puede entrar lo decide la tabla `usuarios`, no el proveedor de identidad: el proveedor solo comprueba que la persona es
quien dice ser, y la API la deja pasar únicamente si su correo está dado de alta. Un administrador gestiona a su gente:

- **Desde el gestor:** Conexiones → Nube (con la nube activa) → **Usuarios de la nube**: lista, alta (nombre, correo y rol),
  cambio de rol y baja o reactivación. Al dar de alta, el gestor indica qué decirle a la persona.
- **Desde la API** (administrador): `GET /api/usuarios`, `POST /api/usuarios` `{correo, nombre, rol}`, `PATCH /api/usuarios/{id}`.
- **Primer ingreso de la persona:** abre la app, pulsa Entrar y en la pantalla del proveedor elige «Crea tu cuenta» con ese
  mismo correo (en Azure, el flujo de registro de Entra External ID). Si su correo no está dado de alta, la app lo dice y no la deja pasar.
- **Reglas:** el correo es único en todo el servicio; un administrador no puede darse de baja ni quitarse el rol a sí
  mismo, y siempre debe quedar uno activo; la baja corta el acceso a la API de inmediato (la cuenta del proveedor sigue
  existiendo, pero ya no vale para este negocio). El correo no se cambia: para eso se da de baja y se crea otro.
- **Límite:** con `LIMITE_USUARIOS` se avisa al excederlo, sin impedir el alta (los planes aún no están definidos).
- **El negocio y su primer administrador** los crea quien opera el servicio con la herramienta de alta de clientes
  (siguiente sección).

## Alta de clientes (negocios)

Es una herramienta de quien opera el servicio, **no una ruta de la API**: crear un negocio cruza el aislamiento entre
clientes, así que usa la conexión de administrador de la base (`DATABASE_ADMIN_URL`) y el rol de la API pública no puede
hacerlo (la base se lo niega; hay prueba). Cada comando es una sola transacción.

```powershell
docker compose --profile herramientas run --rm clientes crear --nombre "Inmobiliaria Sol" --admin-correo ana@sol.mx --admin-nombre "Ana"
docker compose --profile herramientas run --rm clientes listar
docker compose --profile herramientas run --rm clientes admin --negocio "Inmobiliaria Sol" --correo luis@sol.mx
docker compose --profile herramientas run --rm clientes suspender --negocio "Inmobiliaria Sol"
docker compose --profile herramientas run --rm clientes reactivar --negocio "Inmobiliaria Sol"
```

- **crear:** negocio + primer administrador (giro `inmobiliarias` por omisión; `--giro` para otro). No deja repetir el
  nombre (sin distinguir mayúsculas) ni un correo que ya esté en otro negocio. Después la persona entra a la app con ese
  correo (primera vez: «Crea tu cuenta») y da de alta a sus asesores desde el gestor.
- **admin:** agrega otro administrador o, si el correo ya es de ese negocio, lo promueve y lo reactiva (sirve cuando el
  único administrador perdió el acceso). Un correo de otro negocio no se toca.
- **suspender / reactivar:** bloquea el acceso de **todo** el negocio (los tokens que ya tenían dejan de servir, la app los
  saca) y deja de abrir sus QR de casas, sin borrar nada. Reactivar lo devuelve todo. Es la columna `negocios.activo`
  (migración 006); la API no puede cambiarla. La app muestra «Tu cuenta no tiene acceso a este negocio», sin decir por qué.
- **listar:** negocios, estado, usuarios activos, registros y administradores.
- **En Azure** es el mismo comando con `DATABASE_ADMIN_URL` apuntando al servidor de PostgreSQL (desde tu equipo o un
  contenedor puntual); esa conexión no debe vivir en el App Service.
- **No hace:** crear la cuenta en el proveedor de identidad, mandar invitaciones ni borrar negocios (borrar un cliente
  con sus datos y archivos es una decisión que se toma a mano, con respaldo).

## WhatsApp con IA (complemento con costo aparte)

Cada negocio conecta **su propio** número de WhatsApp Business (Cloud API de Meta): la cuenta es suya y Meta le cobra
los mensajes directo a él. Lo configura el administrador en el gestor (Configuración → «Tu WhatsApp con IA»); al guardar,
el gestor copia la cuenta a la nube y muestra la dirección del webhook que se pega en la app de Meta del negocio.

- **Flujo:** Meta llama a `POST /api/whatsapp/{token}/webhook`. El token de la dirección (distinto del de los QR) dice
  de qué negocio es, y la firma `X-Hub-Signature-256` (HMAC con la clave secreta de la app de Meta del negocio) prueba
  que viene de Meta. El mensaje se guarda una sola vez aunque Meta reintente y la API responde 200 de inmediato; después,
  si toca, la IA contesta con el catálogo publicado y la respuesta sale por la Cloud API.
- **Qué sabe la IA:** solo zonas e inmuebles del catálogo y, si la persona ya es prospecto, su nombre y lo que le
  interesa. Nunca presupuestos, notas, score ni datos de otros prospectos (hay prueba).
- **Cuándo contesta:** el negocio tiene el complemento (`negocios.whatsapp_ia`), la IA está activa en su cuenta y ningún
  asesor tomó la conversación. Un asesor la toma al escribirle desde el gestor (pestaña Meta AI) o con «Pausar la IA».
  El asesor solo puede escribir dentro de las 24 h desde el último mensaje del cliente (regla de Meta; fuera de ella
  hacen falta plantillas, que aún no están).
- **Complemento:** lo activa quien opera el servicio, como `activo`; la API no puede cambiarlo.
  `docker compose --profile herramientas run --rm clientes whatsapp-ia --negocio "Inmobiliaria Sol" --activar si`.
  Las respuestas de la IA se cuentan por mes en `uso_mensual.whatsapp_respuestas` (base para cobrarlo).
- **Secretos:** el token de Meta y la clave secreta de la app se guardan cifrados (AES-GCM) con `WHATSAPP_CLAVE`, que no
  vive en la base. Nunca regresan al panel. Si se cambia la clave, cada negocio debe volver a guardar su cuenta.
- **Variables:** `WHATSAPP_CLAVE` (32 bytes en base64; en local va una de desarrollo en `docker-compose.yml`),
  `IA_PROVEEDOR` (`anthropic` en Azure, `ollama` en local, vacío = sin IA), `IA_MODELO` (vacío = `claude-opus-5-5`
  con Claude o `llama3.1:8b` con Ollama), `ANTHROPIC_API_KEY` (con Claude) y `OLLAMA_URL`.
- **Probar en local con tu celular:** Meta no puede llegar a `127.0.0.1`, y un túnel directo a `:8000` dejaría en
  internet toda la API de desarrollo (incluido `/dev/token`). Por eso hay una puerta que solo deja pasar el webhook:
  `python nube/integracion/puerta_webhook.py` (escucha en `127.0.0.1:8002`) y, en otra terminal,
  `cloudflared tunnel --url http://127.0.0.1:8002`. La dirección `https://….trycloudflare.com` va en el gestor
  («Dirección pública» de la sección 3) y la URL del webhook resultante, en tu app de Meta. Sirve el número de prueba
  gratis de Meta con tu celular en su lista de destinatarios; la IA es el Ollama de tu PC. Los celulares de México
  llegan como `521…` y se contestan como `52…` (si no, el número de prueba los rechaza).
- **Sin número real,** `tests/test_whatsapp.py` cubre el flujo con Meta e IA simuladas, y el Simulador del gestor
  (pestaña Meta AI, con la nube activa) manda mensajes de prueba con `POST /api/whatsapp/prueba`: siguen el flujo real
  (se guardan, la IA contesta con las mismas reglas, pausa y asesor funcionan), pero su número empieza con «0» (ningún
  número de WhatsApp lo usa), así que nunca salen a Meta, el webhook no los acepta y no cuentan como uso.

## Migraciones de la base

Las migraciones están en `db/migraciones/` y se registran en la tabla `schema_migraciones`. Con el volumen vacío se aplican
solas; para una base que ya existe (por ejemplo, tras actualizar el código):

```powershell
docker compose exec -T db bash /docker-entrypoint-initdb.d/00_migraciones.sh
```

Solo aplica las que falten, así que se puede repetir. En Azure se corren los mismos `.sql` con `psql`, en orden.

## Cómo se aísla un negocio de otro

Cada tabla de datos lleva `negocio_id` y tiene seguridad por fila (RLS) en PostgreSQL. La API fija el negocio de la
sesión en cada transacción y la base filtra sola, aunque una consulta olvide el filtro. El rol `app_api` con el que se
conecta la API no es dueño de las tablas, no puede saltarse la seguridad por fila y no puede crear negocios ni usuarios.
`tests/test_aislamiento.py` lo comprueba también conectándose directo a la base, sin pasar por el código de la API.

## Paso a Azure (lista de verificación)

1. **Cuenta:** confirma si NEARUSTECH ya tiene suscripción (la oferta gratis es solo para cuentas nuevas) y crea un
   presupuesto con alerta antes de crear recursos.
2. **Recursos:** PostgreSQL Flexible Server (nivel más pequeño), Storage Account (contenedor `archivos`), Key Vault,
   y App Service o Container Apps. Publica la imagen en un registro (Azure Container Registry). Se construye desde la
   carpeta del proyecto (incluye la app del celular): `docker build -f nube/api/Dockerfile --build-arg DEV=0 .`
3. **Base de datos:** con el usuario administrador del servidor, aplica en orden `db/migraciones/*.sql`:
   `psql "host=... dbname=captura user=... sslmode=require" -v app_password='<clave>' -f 000_roles.sql` y luego `001_inicial.sql`.
   Azure exige SSL: la `DATABASE_URL` lleva `?sslmode=require`.
4. **Identidad administrada:** dale a la API el rol *Storage Blob Data Contributor* sobre la cuenta de almacenamiento y usa
   `BLOB_ACCOUNT_URL` en vez de `BLOB_CONNECTION_STRING` (así no hay llaves que guardar).
5. **Inicio de sesión:** registra la API en Entra External ID y configura `AUTH_MODO=oidc`, `OIDC_EMISOR`, `OIDC_JWKS_URL`,
   `OIDC_AUDIENCIA` y `OIDC_CLAIM_IDENTIDAD`. Cada usuario se da de alta con su `sub_externo` (el `oid` de Entra) o su correo.
   En ese modo el endpoint `/dev/token` y la documentación `/docs` dejan de existir.
6. **Variables:** `DATABASE_URL`, `BLOB_ACCOUNT_URL`, y los `OIDC_*` van como secretos o referencias a Key Vault, nunca en el código.
   Para WhatsApp con IA: `WHATSAPP_CLAVE` (una nueva, no la de desarrollo) y `ANTHROPIC_API_KEY` en Key Vault, más
   `IA_PROVEEDOR=anthropic`.
7. **Ensayo:** despliega, corre el flujo completo con datos de mentira, revisa el gasto y borra los recursos si todavía no hay cliente.

## Lo que falta

- **Fotos de zonas o desarrollos y de los asesores** no están en la nube: solo las de los inmuebles, que son las que usa
  el celular.
- **Vista del cliente:** no cuenta cuántas veces se abrió cada casa.
- **Citas:** la pantalla **Citas** del gestor no tiene mapa de fondo (el trayecto se dibuja en un SVG propio), ni exportación
  a Excel, ni alertas (por ejemplo «lleva más de X horas en cita»).
- **En esta PC, el trayecto de las citas se guarda en `citas.jsonl` sin vencimiento** (así era antes de la pantalla); la
  retención de 30 días solo aplica a la nube. Si el negocio pasa a la nube, conviene borrar o archivar ese archivo.
- **Alta de clientes:** es una herramienta de línea de comandos, sin pantalla ni autoservicio. Tampoco se manda un correo de
  invitación: el administrador le avisa a la persona. La verificación del correo la haría Entra (aquí el proveedor
  simulado no la hace), y dar de baja a alguien no borra su cuenta en el proveedor.
- **La app Android (APK)** ya trabaja con la nube (ver su sección), pero **no se ha probado en un teléfono ni en un
  emulador**. Falta: probarla contra una nube https y Entra reales, y publicarla en una tienda si se quiere. El inicio de
  sesión usa el navegador por omisión del teléfono (sin Custom Tabs, para no sumar dependencias).
- **Validación real con Entra External ID:** el flujo se probó contra el proveedor simulado. Las diferencias de Entra
  (claim `oid` en vez de `email`, scopes de la API, vida de los refresh tokens, que caducan y obligan a iniciar sesión
  de nuevo) hay que verificarlas en Azure.
- **Que el gestor se identifique ante una nube real.** En Azure el gestor necesita credenciales propias (por ejemplo un
  registro de aplicación con flujo de credenciales de cliente) en vez del token de prueba; no está implementado.
- **Módulos de taller y venta** (vehículos con QR público y sus fotos, productos y ventas) siguen en SQLite. Las fotos de
  las visitas (`/api/archivos`) existen en la API y están probadas, pero nada del celular ni del gestor las usa todavía.
- **Validación real con Entra External ID.** El validador está probado con claves propias, no con el servicio de Microsoft.
- Límites de cada plan (hoy sin límite; las variables `LIMITE_REGISTROS_MES`, `LIMITE_ARCHIVOS_MB_MES` y
  `LIMITE_USUARIOS` ya existen), cobro, y que la suspensión de un cliente dependa del pago.
- Respaldos, monitoreo, límite de peticiones por usuario y la infraestructura como código (Bicep).

## Problemas conocidos de Docker Desktop en este equipo

- **No arranca** y el registro (`%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log`) dice
  `initializing Inference manager … dockerInference: The file cannot be accessed by the system`: queda un archivo de
  socket viejo en `%LOCALAPPDATA%\Docker\run` que Windows no deja borrar. Reiniciar no lo limpió. Lo que sí funcionó fue
  **renombrar la carpeta** `run` (con Docker Desktop cerrado); Docker la vuelve a crear. La carpeta renombrada
  (`run.viejo-20261105`) se puede borrar cuando quieras.
- **`docker pull` falla con `EOF`** a ratos: la conexión del motor se corta. Reintentar casi siempre basta (las capas ya
  bajadas se conservan). `postgres:16` falló siempre desde Docker Hub; funcionó desde el espejo oficial de Google:
  `docker pull mirror.gcr.io/library/postgres:16` y luego `docker tag mirror.gcr.io/library/postgres:16 postgres:16`.
