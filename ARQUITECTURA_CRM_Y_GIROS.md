# Arquitectura: núcleo CRM + módulos por giro

Documento de diseño (ver la sección 9 para el estado de implementación). Objetivo: que lo general de cualquier CRM viva **fuera** de los giros,
y que cada giro tenga **solo** sus registros y reglas propias, sin repetir características.

## 1. Problema actual

Hoy cada giro reimplementa piezas comunes:

| Concepto repetido | Dónde aparece hoy |
|---|---|
| Datos de contacto (nombre, teléfono) | `visitas.datos` (gestor), `solicitudes.datos` (gestor y nube), `taller_vehiculos.datos.cliente` (taller) |
| Estado del seguimiento | `visitas.estado` (5 estados fijos), `taller` (estado calculado), `ventas` (sin estado) |
| Notas | visitas (`notas`), solicitudes (`nota`), taller (`recepcion.nota`, notas de foto) |
| Fotos o archivos | nube (`archivos`), taller (carpeta aparte), visitas (solo en nube) |
| Catálogo de servicios / productos | `config.taller.servicios_catalogo` (taller), `productos` (venta) |
| Responsable | `visitas.asesor`, `solicitudes.asesor`, usuarios de la nube |
| Importación Excel / JSON | solo para prospectos; el taller y la venta no la tienen |
| Historial de cambios | `historial_estados` (nube); el gestor no lo tiene |

Además hay **dos almacenes sin conexión entre sí**: el gestor usa SQLite (`visitas`, `solicitudes`, `taller_vehiculos`,
`productos`, `ventas`, `venta_lineas`) y la nube PostgreSQL solo guarda prospectos, solicitudes, citas y archivos.
El taller y la venta **no existen en la nube**.

## 2. Regla de separación

Una característica va al **núcleo** si cualquier negocio (inmobiliaria, taller, tienda, despacho contable) la usaría igual.
Va a un **giro** si tiene vocabulario, reglas o campos que solo tienen sentido en ese giro.

Prueba rápida: *¿una tienda de abarrotes y una inmobiliaria necesitan lo mismo aquí?* Si sí → núcleo. Si no → giro.

Principio de dependencia: **los giros apuntan al núcleo, nunca al revés.** El núcleo no conoce «propiedad», «vehículo» ni
«presupuesto»; solo conoce contactos, oportunidades, actividades, catálogo, archivos y usuarios.

## 3. Inventario: qué va a cada lado

### Núcleo (general, para todos los giros)

| Característica | Hoy | Entidad del núcleo |
|---|---|---|
| Personas y empresas | `visitas` / `solicitudes` / `cliente` del taller | **Contacto** |
| Trato o negocio en curso, con etapa y valor | `visitas.estado` (5 estados fijos) | **Oportunidad** (con pipeline y etapas configurables) |
| Notas, llamadas, mensajes, reuniones | notas sueltas | **Actividad** (tipo: nota, llamada, mensaje, reunión, sistema…) |
| Citas y trayecto en campo (GPS) | `citas`, `ubicaciones` (nube) | **Actividad** con duración y ubicaciones |
| Tareas y recordatorios con fecha | no existe | **Tarea** (vencimiento, responsable, contacto u oportunidad) |
| Fotos y archivos (con etiqueta libre) | `archivos` (nube) | **Archivo** |
| Catálogo de productos y servicios (precio, IVA, existencia opcional) | `productos` (venta), `servicios_catalogo` (taller) | **Catálogo** |
| Usuarios, roles, responsables | `usuarios` (solo admin / capturista) | **Usuario** y **Equipo** (roles ampliados) |
| Historial de cambios | `historial_estados` | **Historial** (genérico: entidad, campo, antes, después) |
| Formulario público de contacto o cita | `solicitudes` / `solicitudes_cita` | **Solicitud entrante** (datos del giro en un campo) |
| Campos personalizados y secciones del formulario | `formulario` en `config.json` | **Definición de campos** (por negocio y tipo de registro) |
| Importación desde Excel / JSON | `importar_visitas` | **Importación** (mapea columnas a campos del núcleo o del giro) |
| Conectores externos | Kommo (`gestor_visitas.py`), Odoo (planeado) | **Integración** (conector con mapeo) |
| Panel y métricas base (conteos, embudo, por asesor) | panel | **Panel** con widgets del núcleo |
| Asistente con IA local | `ia_*` en el gestor | **Asistente** (el giro aporta el contexto) |

### Giro: inmobiliarias

| Característica | Hoy | Registro propio del giro |
|---|---|---|
| Propiedades (casa, departamento, terreno; estatus) | `propiedades` en config | **Propiedad** |
| Zonas y desarrollos (colonia o desarrollo) | `zonas` en config | **Zona / Desarrollo** |
| Visita a una propiedad | `visitas` | **Actividad tipo «visita»** (núcleo) con `propiedad_id` del giro |
| Calificación de compra: presupuesto, crédito, enganche, plazo, quién decide | campos `builtin` de `CAMPOS_BASE` | **Datos del giro en la oportunidad** (`datos_giro`) |
| Score de compra y sus razones | `calcular_score` | **Regla del giro** (escribe puntaje y razones en la oportunidad) |
| Criterios y categorías de puntaje propios | `criterios`, `categorias` | **Regla del giro** (configurable) |
| Agenda pública de visitas (horarios libres) | `agendar.html`, `solicitudes_cita` | **Solicitud entrante** del giro + horario de atención del giro |

### Giro: taller (autos)

| Característica | Hoy | Registro propio del giro |
|---|---|---|
| Vehículo (marca, modelo, placas, año) | `taller_vehiculos.datos.vehiculo` | **Vehículo**, con `contacto_id` del dueño |
| Orden de servicio (recepción, promesa de entrega, estado) | `taller_vehiculos` | **Orden de servicio** (vinculada a vehículo y contacto) |
| Servicios de la orden (checklist con estado pendiente / en proceso / hecho) | `tareas` del taller | **Línea de servicio** que referencia el **Catálogo** |
| Fotos por etapa (recepción, avance, entrega) | `fotos` del taller | **Archivo** del núcleo con etiqueta de etapa |
| Enlace QR de seguimiento para el cliente | `token_qr` | **Acceso público** del giro (vista de la orden) |

Nota de nombres: la «tarea» del taller (una línea de servicio) **no es** la «tarea» del núcleo (un recordatorio con fecha).
Se llaman distinto para no confundirlos.

### Giro: venta

| Característica | Hoy | Registro propio del giro |
|---|---|---|
| Punto de venta: cobro, pago, cambio, IVA desglosado | `venta.py` | **Venta** (líneas, total, pago); `contacto_id` opcional |
| Líneas de venta | `venta_lineas` | **Línea de venta** que referencia el **Catálogo** |
| Existencias y mínimos | `productos.existencia` | Se queda en el **Catálogo** del núcleo; el giro solo registra movimientos |

## 4. Modelo de datos propuesto

### Núcleo (una sola vez por negocio, con `negocio_id` y seguridad por fila)

- **contactos**: `id`, `tipo` (persona | empresa), `nombre`, `telefono`, `correo`, `origen`, `responsable_id`,
  `etiquetas[]`, `creado`, `actualizado`.
- **pipelines** y **etapas**: cada pipeline tiene etapas ordenadas, configurables por negocio. El giro propone un
  pipeline por defecto (inmobiliarias: nuevo, contactado, negociando, cerrado, perdido).
- **oportunidades**: `id`, `contacto_id`, `pipeline_id`, `etapa_id`, `valor`, `probabilidad`, `cierre_estimado`,
  `responsable_id`, `giro`, `datos_giro` (jsonb, validado por el giro), `cerrado_en`, `motivo_cierre`, `creado`, `actualizado`.
- **actividades**: `id`, `tipo`, `contacto_id`, `oportunidad_id` (opcional), `responsable_id`, `ocurrido`, `duracion`,
  `datos` (jsonb), `ubicaciones` (para citas).
- **tareas**: `id`, `titulo`, `vence`, `responsable_id`, `contacto_id` u `oportunidad_id`, `estado`, `completada_en`.
- **archivos**: `id`, `contacto_id` u `oportunidad_id` (opcional), `etiqueta`, `bytes`, `tipo`, `subido_por`, `subido`.
- **catalogo**: `id`, `tipo` (producto | servicio), `nombre`, `codigo`, `precio`, `tasa_iva`, `existencia` (opcional), `minimo`, `activo`.
- **usuarios**, **equipos**, **roles** (ampliando `admin` / `capturista`).
- **historial**: `entidad`, `entidad_id`, `campo`, `antes`, `despues`, `usuario_id`, `cuando`.
- **solicitudes_entrantes**: `id`, `giro`, `contacto_id` (al confirmarla), `datos` (jsonb del giro), `estado`.
- **integraciones**: `id`, `tipo` (kommo, odoo…), `configuracion`, `mapeo`, `ultima_sincronizacion`.

### Giros (registros propios)

Cada giro declara sus **tipos de registro**: nombre, campos, qué vínculos admite (contacto, oportunidad) y sus reglas.
Propuesta de almacenamiento: una tabla `registros_giro` (`negocio_id`, `giro`, `tipo`, `id`, `contacto_id`,
`oportunidad_id`, `datos` jsonb, `estado`, `creado`, `actualizado`), validada por el código del giro. Así cada giro tiene
sus propios tipos sin crear una tabla por cada uno, y la migración sigue siendo simple. La alternativa es una tabla por
giro; si se elige, la regla de separación no cambia.

Ejemplos de tipos del giro:

- **inmobiliarias**: `propiedad`, `zona`.
- **taller**: `vehiculo`, `orden_servicio`.
- **venta**: `venta`.

## 5. Reglas de frontera

1. **Los datos de contacto nunca se copian en un giro.** El giro guarda `contacto_id`.
2. **Las etapas, las notas, las tareas y las fotos son del núcleo.** El giro solo decide qué etapas usa su pipeline por defecto.
3. **Un contacto puede tener oportunidades y registros de varios giros** dentro del mismo negocio (un cliente compra una casa
   y lleva su auto al taller). Esto requiere que los giros de un negocio compartan contactos.
4. **La regla de puntaje es del giro.** El núcleo ofrece un lugar para guardar un número y una lista de razones, nada más.
5. **Un giro no escribe en tablas de otro giro.** Si necesita algo de otro, pasa por el núcleo (por ejemplo, un servicio del
   taller que usa el catálogo).

## 6. Fases propuestas

1. **Núcleo en la nube:** crear las tablas del núcleo con seguridad por fila, sin tocar el gestor.
2. **Migrar prospectos:** cada visita pasa a un contacto, una oportunidad y una actividad «visita». Migración idempotente,
   como la de `migrar_sqlite.py`.
3. **Giro inmobiliarias sobre el núcleo:** propiedades y zonas como registros del giro; calificación y score en `datos_giro`.
4. **Giro taller:** vehículo ligado a contacto; orden de servicio; servicios con el catálogo; fotos como archivos del núcleo.
5. **Giro venta:** productos al catálogo; ventas ligadas a contacto opcional.
6. **Quitar duplicados del gestor:** retirar la copia de contactos, notas y fotos en cada módulo.
7. **Tareas y pipeline configurable:** después de que el núcleo esté estable; es la base del «CRM completo».

Cada fase deja el sistema funcionando y con pruebas. No se borra SQLite hasta que la migración de esa fase esté probada.

## 7. Decisiones

1. **Fuente de verdad: nube (PostgreSQL). Confirmado.** El núcleo, el taller y la venta viven en la nube; el gestor es
   cliente con caché para trabajar sin señal.
2. **Contactos compartidos entre giros del mismo negocio:** adoptado por recomendación (regla 3). Pendiente de confirmar.
3. **Kommo: se ignora por ahora.** No se diseña ni se prioriza su conector; se decidirá más adelante.
4. **Registros de giro (unificado, ver sección 11):** un giro con catálogo estable y ids que ya usan el celular o los QR tiene tablas propias (inmobiliarias: `zonas`, `inmuebles`). Los demás usan `registros_giro` con `tipo` (taller, venta).

## 9. Estado de implementación

- **Fase 1 (tablas, hecha y probada):** `nube/db/migraciones/009_nucleo_crm.sql` crea las tablas del núcleo con seguridad
  por fila y permisos del rol de la API: `contactos`, `pipelines`, `etapas`, `oportunidades`, `actividades`, `tareas`,
  `catalogo`, `historial`, `registros_giro`. La tabla `archivos` gana `contacto_id`, `oportunidad_id`, `registro_giro_id`
  y `etiqueta`. Aplicada en la base local junto con la 008, que faltaba. Pruebas en `nube/api/tests/test_nucleo.py`
  (8 pruebas: aislamiento, vínculos del mismo negocio, permisos sin DELETE ni UPDATE en actividades e historial).
  La suite completa pasa: 143 pruebas, 0 fallos.
- **API del núcleo (hecha y probada):** `nube/api/app/nucleo.py`, con estas rutas bajo `/api`:
  - `GET/POST /contactos`, `GET/PATCH /contactos/{id}` (la ficha trae oportunidades y actividades). Búsqueda por nombre o teléfono.
  - `GET/POST /pipelines` (crear, solo administrador; un solo pipeline por defecto; mínimo una etapa abierta).
  - `GET/POST /oportunidades`, `GET/PATCH /oportunidades/{id}`. Cambiar de etapa pone o quita la fecha de cierre; la
    etapa debe pertenecer al pipeline de la oportunidad.
  - `GET/POST /actividades`. Son históricas: se agregan, no se editan ni se borran.
  - Cada cambio de contacto u oportunidad deja una fila en `historial` por campo, con el usuario que lo hizo.
  - Pruebas en `nube/api/tests/test_nucleo_api.py` (15 pruebas). La suite completa pasa: 158 pruebas, 0 fallos.
- **Tareas y catálogo (hechos y probados):**
  - `GET/POST /tareas`, `PATCH /tareas/{id}`. Filtros por estado, responsable, contacto, oportunidad y vencidas.
    Marcar una tarea como hecha pone la fecha de cumplimiento; reabrirla la quita.
  - `GET/POST /catalogo`, `PATCH /catalogo/{id}`, `POST /catalogo/{id}/existencia`. Crear y editar artículos es solo del
    administrador; los movimientos de existencia los puede hacer cualquier usuario. La existencia nunca queda negativa,
    cada movimiento deja historial y el motivo queda guardado. Filtro «bajo mínimo» para reponer. Un artículo se desactiva
    en vez de borrarse.
  - Pruebas en `nube/api/tests/test_nucleo_tareas_catalogo.py` (15 pruebas). La suite completa pasa: 173 pruebas, 0 fallos.
- **Giros y giro inmobiliarias (hechos y probados):**
  - `app/giros.py` registra los tipos de registro de cada giro, sus estados y las reglas de `datos_giro` de sus oportunidades.
    El núcleo no conoce ningún giro: llama a `validar_oportunidad()`, y un giro sin reglas no valida nada.
  - `app/giros_rutas.py`: `GET /giros`, `GET/POST /giros/{giro}/registros`, `GET/PATCH /giros/{giro}/registros/{id}`
    (los datos se reemplazan completos y se validan de nuevo). Historial de cambios en `registro_giro`.
  - Inmobiliarias: tipos `propiedad` (estado disponible, apartado o vendido; la zona debe ser del mismo negocio) y `zona`.
  - Visitas: `POST/GET /giros/inmobiliarias/visitas`. Son actividades del núcleo tipo «visita» con la propiedad en `datos`.
  - Puntaje de compra: el servidor lo calcula al guardar `datos_giro` (cinco criterios, 100 puntos, con los mismos pesos
    que el gestor de Windows por omisión). Si el cliente manda `score`, `categoria` o `razones`, se descartan.
  - Pruebas en `nube/api/tests/test_giros.py` (15 pruebas). La suite completa pasa: 187 pruebas, 0 fallos.
- **Limitaciones conocidas:**
  - El puntaje no se recalcula solo si cambia el precio de la propiedad: hay que volver a guardar la oportunidad.
  - Los pesos del puntaje están fijos en código; cuando cada negocio pueda ajustarlos, pasarán a su configuración.
  - Migración de los prospectos del gestor a estas tablas (fase 2): todavía no se ha hecho.
- **Taller y venta sobre el núcleo (hechos y probados):**
  - Taller: tipos `vehiculo` (contacto dueño en `contacto_id`) y `orden_servicio` (vehículo, fecha de entrega, kilometraje y
    lista de servicios tomados del catálogo con su estado). Fotos por etapa (recepción, avance, entrega) con
    `POST/GET /giros/taller/registros/{id}/fotos`, guardadas como archivos del núcleo (máximo 30 por registro).
  - Venta: tipo `venta` con `POST /giros/venta/registros`. Precios, IVA y nombres salen del catálogo; el cliente solo manda
    artículos, cantidades, descuento y pago. Al crear la venta baja la existencia (con historial) y falla si no alcanza.
    Efectivo exige lo recibido; el cambio se calcula. Una venta no se corrige (`editable = False`).
  - El registro de tipos en `giros.py` ahora tiene un efecto al crear (`al_crear`) y la marca `editable`.
  - Pruebas en `nube/api/tests/test_giros_taller_venta.py` (18 pruebas). La suite completa pasa: 205 pruebas, 0 fallos.
- **Limitaciones conocidas:**
  - No hay anulación de ventas: si una venta está mal, hoy no se revierte la existencia. Hay que diseñarlo antes de usar la venta en caja.
  - El seguimiento público del taller por QR (lo que ve el cliente) todavía no está en la API.
  - El estado de la orden se captura a mano; el gestor lo calculaba a partir de sus servicios.
  - No hay IVA desglosado por tasa en el comprobante; solo el total de IVA de la venta.
- **Pendiente:** anulación de ventas, seguimiento público del taller, y la migración de datos del gestor (fase 2). El
  gestor de Windows todavía no usa estas rutas.
- **Nota operativa:** Docker Desktop no arrancaba porque la carpeta `%LOCALAPPDATA%\Docker\run` tenía dos sockets rotos.
  Se movió a `run.roto-20261109` (no se borró). Puede eliminarse cuando se quiera.

## 8. Lo que no cambia

- El formulario de captura del celular sigue igual para el usuario; cambia de dónde toma y dónde guarda sus datos.
- Las pruebas actuales (`nube/api/tests`) deben seguir pasando en cada fase.
- La separación no cambia el aislamiento entre negocios: cada tabla nueva lleva `negocio_id` y su política de seguridad por fila.


## 10. Anulación de ventas (diseño aprobado; implementada)

**Estado:** implementada en `POST /giros/venta/registros/{id}/anular` (`giros.anular_venta`), solo administrador, motivo de
5 a 300 caracteres, una sola vez. Pruebas en `nube/api/tests/test_giros_anulacion.py` (9 pruebas). La suite completa pasa:
214 pruebas, 0 fallos (después, 222). Una venta tampoco puede nacer anulada.

**Devolución (implementada):** tipo `devolucion` dentro del giro venta, creado con `POST /giros/venta/registros` por el administrador. Solo se admite para una venta anulada, una sola vez por venta. El monto y la forma de pago salen de la venta; el cliente no los escribe. Es un registro: no mueve inventario ni dinero en el sistema, y no se corrige. Pruebas en `nube/api/tests/test_giros_devolucion.py` (8 pruebas). La suite completa pasa: 222 pruebas, 0 fallos. Pendiente: el corte de caja y los reportes deben excluir ventas anuladas y contar las devoluciones aparte.

**Problema.** Hoy una venta cobrada no se puede corregir (`editable = False`) y no hay forma de revertirla. Si se cobró mal,
la existencia queda descontada de más y el total de ventas queda inflado. Hay que resolverlo antes de usar la venta en caja.

**Decisión de diseño: la anulación es una transición de estado, no una edición.** La venta se conserva con su historia; solo
cambia su estado de `cobrada` a `anulada`. Nunca se borra.

**Cómo se hace.**
- Ruta propia: `POST /giros/venta/registros/{id}/anular`, con cuerpo `{"motivo": "..."}` (obligatorio, 5 a 300 caracteres).
  La ruta genérica `PATCH` sigue sin poder cambiar una venta.
- Todo en una transacción, con la venta bloqueada (`FOR UPDATE`):
  1. Solo se anula una venta en estado `cobrada`. Una segunda anulación responde 422.
  2. Por cada línea con inventario (`existencia` no nula), la existencia vuelve a sumar la cantidad vendida. El precio no
     cambia nada aquí: se devuelve cantidad, no dinero.
  3. Cada devolución deja historial en `articulo` (campo `existencia`), igual que una venta.
  4. La venta pasa a `anulada` y guarda en `datos.anulacion`: motivo, usuario y fecha. Ese bloque no se vuelve a escribir.
- Estados de la venta: `cobrada` y `anulada`. Una venta anulada no admite más cambios.

**Reglas que se proponen.**
- Solo el administrador puede anular. Cualquier usuario puede vender, pero corregir el inventario y el dinero requiere
  responsable. (Para cambiarlo basta con permitir `ambos_roles` en la ruta.)
- Anulación completa, no parcial. Si solo una parte estaba mal, se anula la venta y se registra una venta nueva correcta.
- Un artículo desactivado después de la venta igual recibe su devolución: lo que se vendió sí salió del inventario.
- Un artículo que ya no existe no es posible, porque el catálogo no borra artículos.

**Lo que no cubre este diseño (queda para después).**
- Corte de caja: si existe, las ventas anuladas deben quedar fuera de los totales del periodo.
- Reportes de ventas: deben filtrar por estado. Hoy no hay reportes.
- Ventas anuladas que ya se entregaron en efectivo: la devolución del dinero no se registra en el sistema.

**Pruebas que tendría.** Anular devuelve la existencia y deja historial; una segunda anulación falla; una venta ajena no se
puede anular; un capturista no puede anular; anular no toca artículos sin inventario; `PATCH` de una venta anulada falla.

**Preguntas abiertas para decidir antes de implementar:**
1. ¿Solo el administrador puede anular, o también el capturista que hizo la venta?
2. ¿Hay límite de tiempo (por ejemplo, solo el mismo día) o se puede anular en cualquier momento con motivo?
3. ¿Se registra la devolución del dinero (efectivo, tarjeta) como movimiento aparte, o queda fuera del sistema por ahora?


## 11. Unificación con el borrador de tablas inmobiliarias

**Qué pasó.** El 2026-11-09, en paralelo al núcleo, el gestor pasó su base local a tablas (`gestor-windows/datos_locales.py`:
configuracion, zonas, inmuebles, asesores, prospectos, citas) y se escribió su equivalente para la nube como
`nube/db/borradores/009_tablas_inmobiliaria.sql`. Chocaba con el núcleo en el número (la 009 del núcleo ya está aplicada) y en
el modelo (propiedades como `registros_giro` vs. tabla `inmuebles`; visitas y citas en `actividades` vs. `registros` y `citas`).

**Resultado.** Un solo borrador, `nube/db/borradores/011_inmobiliaria_sobre_nucleo.sql`. El anterior queda en
`borradores/historico/` sin cambios. La referencia en `datos_locales.py` apunta al nuevo.

**Decisiones (se queda lo mejor de cada uno):**

| Tema | Borrador del gestor | Núcleo | Unificado |
|---|---|---|---|
| Catálogo inmobiliario | Tablas `zonas`, `inmuebles` con ids de texto | `registros_giro` tipos `propiedad`/`zona` (uuid) | **Tablas propias.** Los ids de texto ya los usan el celular, los QR y los prospectos guardados; cambiarlos rompería enlaces. |
| Asesores | Usuarios con rol `capturista` + teléfono, foto, orden | Usuarios del núcleo | **Igual en ambos:** usuarios del núcleo con esas columnas. |
| Prospectos / visitas | `registros` + `inmueble_id`, `asesor_id` | Contacto + oportunidad + actividad «visita» | **`registros` es la visita del giro** (lo que captura el celular) y se liga al núcleo con `contacto_id` y `oportunidad_id`. El estado comercial vive en la oportunidad; `registros.estado` queda por compatibilidad. |
| Citas | Una tabla `citas` (enlace + asesor) | Actividad «cita» | **`citas` es la agenda del núcleo** (cualquier giro agenda), con `contacto_id` y `oportunidad_id`; `zona_id` e `inmueble_id` son la extensión inmobiliaria. |
| Actividades | — | Tipos incluyen «visita» y «cita» | Dejan de usarse esos dos tipos (viven en `registros` y `citas`). Se quitan del CHECK al final. |
| Taller y venta | — | `registros_giro` | Sin cambio. Pasan a tablas propias solo si sus ids se vuelven públicos o necesitan consultas por columna. |

Ajustes al borrador original: el número (primero 010; ahora 011, porque la 010 pasó a ser `010_visitas_al_nucleo.sql`, que liga visitas con contacto y oportunidad), `inmuebles.precio` admite 0 como la base local, los vínculos al núcleo en
`registros` y `citas`, una comprobación que detiene la migración si hay propiedades o zonas en `registros_giro`, y todo
dentro de una transacción (si falla, no queda a medias).

**Cómo se probó.** En bases temporales de PostgreSQL (borradas al terminar): se aplicaron 001 a 009 y luego el borrador (entonces 010)
con datos sembrados (configuración con zonas y propiedades, un prospecto, una solicitud y una cita). Las zonas e inmuebles
salen de la configuración, el prospecto queda ligado a su inmueble y asesor, las citas se unifican y la configuración deja de
duplicar el catálogo. Con una propiedad en `registros_giro`, la migración se detiene y no cambia nada.

**Lo que falta para aplicarlo (en este orden):**
1. API: rutas de zonas e inmuebles sobre las tablas; `/api/app/config` arma `zonas` y `propiedades` desde ellas (el celular no cambia).
2. API: quitar los tipos `propiedad` y `zona` de `giros.py`; `datos_giro.propiedad_id` pasa a `inmueble_id` (texto) validado contra `inmuebles`.
3. API: `/api/visitas` llena `inmueble_id`, `asesor_id`, `contacto_id` y `oportunidad_id`; `/giros/inmobiliarias/visitas` (actividades) se retira.
4. API: solicitudes y citas sobre la tabla `citas` unificada; luego `DROP TABLE solicitudes_cita`.
5. Mover el borrador a `migraciones/011_…` y aplicar; después, el bloque final que quita «visita» y «cita» de `actividades`.
6. Herramienta de la fase 2: contactos y oportunidades a partir de los prospectos (agrupar por teléfono, pipeline por defecto).


## 12. Interfaz del CRM en el gestor de Windows

**Dónde está.** Tres pestañas del panel del gestor (`http://127.0.0.1:8765/`): **Contactos**, **Embudo** y **Tareas**. No
llevan `data-giro`: aparecen igual en todos los giros, porque son el núcleo. El menú queda ordenado así: inicio del giro
(Panel o Vender) · Contactos · Embudo · Tareas · pestañas del giro · Asesores · Conexión · ⚙. En inmobiliarias son 11 opciones
mientras «Prospectos» siga aparte; baja a 10 cuando las visitas se liguen a contactos y oportunidades (sección 11, paso 3).
A 1280 px de ancho cabe en una línea; más angosto, el menú se desplaza de lado.

- **Contactos:** búsqueda por nombre o teléfono, alta de contacto y ficha con sus oportunidades, su actividad (nota, llamada,
  mensaje, reunión) y una tarea ligada.
- **Embudo:** una columna por etapa con número de oportunidades y suma de valor; cada tarjeta se mueve de etapa con su
  selector. Si el negocio no tiene embudo, un botón crea «Ventas» (Nuevo, Contactado, Negociando, Cerrado, Perdido).
- **Tareas:** pendientes, vencidas y hechas; agregar con fecha límite y marcar como hecha.

**Cómo habla con la nube.** El panel pide `/api/crm/...` al gestor y el gestor lo reenvía a la API (`Manager.crm` en
`gestor_visitas.py`), con la misma cuenta de administrador que usa para los prospectos. Solo pasan las rutas de contactos,
oportunidades, embudos, actividades y tareas (lista fija, ids uuid); escribir exige JSON, como el resto del panel. Sin la
nube activa, la pestaña muestra «Conecta la nube» con un botón a Conexión: el CRM no guarda nada en esta PC.

**Cómo se probó.** Con una copia del gestor en otro puerto (base copiada, `config.json` respaldado y verificado sin cambios):
alta de contacto, ficha, oportunidad, actividad, tarea, marcar hecha, crear el embudo, mover una oportunidad de etapa,
cambiar de pestaña sin aviso de cambios pendientes, sin errores en consola, y el aviso «Conecta la nube» con la nube apagada.
Esas pruebas dejaron datos de ejemplo en «Negocio demo»: el contacto Laura Méndez, el embudo «Ventas», una oportunidad, una
actividad y una tarea.

**Pendiente.** Editar y desactivar contactos desde la ficha, cambiar valor o motivo de cierre de una oportunidad, el
catálogo, y las vistas de taller y venta sobre la nube (hoy siguen en SQLite).

## 13. Perfiles del equipo y grupos del menú

**Perfiles** (se asignan por persona en **Asesores**, columna «Perfil»; un asesor sin perfil, o con los nombres anteriores
«campo»/«maestro», se lee como capturador/administrador):

| Perfil | Qué hace |
|---|---|
| Administrador | Gestiona la app: configuración y conexión, formulario y equipo. Ve y cambia todo. |
| Asesor | Trabaja en el panel sus contactos, embudo, tareas y citas, y también captura visitas. No cambia configuración, formulario ni equipo. |
| Capturador | Solo captura visitas en la app del celular. No entra al panel ni modifica información. |

**Menú en dos colores** (el capturador no tiene pestañas, así que solo quedan dos grupos):

- **Administrador y asesor (azul):** Panel, Inmuebles, Contactos, Embudo, Tareas, Citas; en venta, Ventas, Productos y Vender.
- **Solo administrador (ámbar):** Registro, Asesores y Configuración (que ahora incluye Conexión).

En inmobiliarias el menú queda en 9 opciones. La tabla `PERMISOS_ROL` de `gestor-windows/panel.html` es la única fuente: de
ella salen los colores, el orden, la tarjeta «Quién entra a cada pestaña» y su **vista previa del menú por perfil** (una imagen
sin interacción que cambia al elegir Administrador, Asesor o Capturador).

**Conexión dentro de Configuración.** La pestaña Conexión ya no existe: su contenido (celular, base de datos, nube) se muestra
al principio de Configuración (⚙).

**Lo que todavía no se aplica.** El perfil se guarda (tabla `asesores`, columna `ficha`, y `config.json`), pero no restringe
nada: el panel lo usa el administrador en su PC y la nube solo distingue `admin` y `capturista`. Falta: el rol `asesor` en la
nube, sincronizar el perfil de cada persona con su usuario en la nube, y un acceso con cuenta para asesores.

## 14. Prospectos y embudo en uno, y copia local

**Decisiones** (acordadas en la conversación del 2026-11-09):
1. Una oportunidad por persona; sus visitas quedan como historial.
2. La persona se reconoce por su teléfono a 10 dígitos; sin teléfono, cada visita es un contacto nuevo.
3. Los prospectos viven en la nube, con **copia local** opcional en el gestor y un **aviso** cuando se trabaja con ella.
4. La oportunidad toma el score de la **última visita**.
5. «Cargar datos» y «Borrar cargados» pasan a la vista Lista del Embudo (Kommo queda fuera por ahora).
6. El embudo lo ven administrador y asesor.

**En la nube (API).**
- Migración `010_visitas_al_nucleo.sql`: `registros.contacto_id` y `registros.oportunidad_id`. El borrador inmobiliario pasó a
  `borradores/011_inmobiliaria_sobre_nucleo.sql` (sin esas columnas, que ya están en la 010).
- `app/visitas_nucleo.py`: al recibir una visita (`POST /visitas` y `/visitas/lote`) se liga a su contacto y a su oportunidad
  abierta del giro inmobiliarias (o a una abierta creada a mano sin giro). Si no hay embudo, se crea «Ventas». La oportunidad
  toma calificación, propiedad, score y asesor de la visita más reciente, y su valor el precio de la propiedad (si nadie lo
  escribió). Las visitas de ejemplo no se ligan.
- La etapa de la oportunidad y el estado de sus visitas se mantienen iguales en los dos sentidos (el Panel y el celular siguen
  leyendo el estado de la visita).
- `POST /visitas/ligar` (administrador) liga las visitas de antes; se puede repetir. El Embudo lo llama al abrirse.
- Pruebas en `nube/api/tests/test_visitas_nucleo.py` (12). La suite completa pasa: 234 pruebas, 0 fallos.

**En el gestor.**
- La pestaña Prospectos desaparece del menú. El **Embudo** tiene dos vistas: **Tablero** (columnas por etapa) y **Lista** (la
  tabla de antes: contacto, teléfono, propiedad, presupuesto, score, asesor, última visita y etapa editable, con búsqueda,
  filtro por etapa, «Cargar datos» y «Borrar cargados»). Clic en una fila abre la ficha del contacto.
- **Copia local:** opción en Configuración → Nube, encendida por omisión. El gestor guarda lo último que leyó de cada vista del
  CRM en `%LOCALAPPDATA%\CapturaVisitas\copia_nube_crm.json` (fuera de OneDrive y de git). Si la nube no responde, contesta
  con esa copia y el panel muestra el aviso «Sin conexión con la nube… copia local guardada el …». Tras un fallo no vuelve a
  intentar durante 30 s, para que las pantallas no esperen. Apagar la opción borra la copia.
- Sin la nube activa, las pestañas del núcleo ofrecen «Ver los prospectos de esta PC» (la vista de antes, sin pestaña).

**Limitaciones.**
- Sin conexión solo se consulta: los cambios (mover una etapa, agregar una nota) se rechazan con un aviso; no hay cola para
  guardarlos después. El celular sí sigue capturando sin señal, como siempre.
- «Borrar cargados» borra las visitas cargadas, pero no los contactos y oportunidades que se crearon con ellas.
- El score que se recalcula al editar `datos_giro` usa los pesos por omisión, no los que publica cada negocio.
- Posibles contactos duplicados (misma persona con teléfonos distintos o sin teléfono): falta una función para unirlos.
- Pruebas de ejemplo en «Negocio demo»: tres visitas (Laura Méndez, Jorge Salas, Marta Ríos) con sus oportunidades.
