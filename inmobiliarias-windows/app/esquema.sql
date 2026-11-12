-- Base de NearLeads Inmobiliarias (edición independiente para Windows, PostgreSQL local).
--
-- Un solo negocio por instalación: no hay negocio_id ni seguridad por fila (cada PC tiene su propia base).
-- Dos bloques:
--   giro inmobiliario  configuracion, zonas, inmuebles, asesores, prospectos, citas, cargas, prospectos_espera
--                      (equivalente a gestor-windows/datos_locales.py, con tipos de PostgreSQL)
--   núcleo del CRM     contactos, pipelines, etapas, oportunidades, actividades, tareas, historial
--                      (equivalente a nube/db/migraciones/009 y 010, sin negocio_id)
--
-- Idempotente: la app lo aplica en cada arranque (CREATE ... IF NOT EXISTS). Los cambios futuros van al final como
-- pasos numerados en esquema_version, nunca editando lo de arriba.

CREATE TABLE IF NOT EXISTS esquema_version (
  version     integer PRIMARY KEY,
  aplicado    timestamptz NOT NULL DEFAULT now()
);

-- --- Giro inmobiliario -------------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS configuracion (
  id           integer PRIMARY KEY CHECK (id = 1),
  datos        jsonb NOT NULL,
  actualizado  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS zonas (
  id           text PRIMARY KEY,
  orden        integer NOT NULL DEFAULT 0,
  nombre       text NOT NULL,
  tipo         text NOT NULL DEFAULT 'colonia' CHECK (tipo IN ('desarrollo', 'colonia')),
  ciudad       text NOT NULL DEFAULT '',
  imagen       text NOT NULL DEFAULT '',
  ficha        jsonb NOT NULL DEFAULT '{}'::jsonb,
  actualizado  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inmuebles (
  id                text PRIMARY KEY,
  orden             integer NOT NULL DEFAULT 0,
  zona_id           text REFERENCES zonas(id) ON DELETE SET NULL,
  nombre            text NOT NULL,
  tipo              text NOT NULL DEFAULT 'casa' CHECK (tipo IN ('casa', 'departamento', 'terreno')),
  estatus           text NOT NULL DEFAULT 'disponible' CHECK (estatus IN ('disponible', 'apartado', 'vendido')),
  precio            bigint NOT NULL DEFAULT 0 CHECK (precio >= 0),
  recamaras         integer NOT NULL DEFAULT 0,
  banos             integer NOT NULL DEFAULT 0,
  estacionamientos  integer NOT NULL DEFAULT 0,
  m2_construccion   integer NOT NULL DEFAULT 0,
  m2_terreno        integer NOT NULL DEFAULT 0,
  imagen            text NOT NULL DEFAULT '',
  ficha             jsonb NOT NULL DEFAULT '{}'::jsonb,
  actualizado       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inmuebles_zona ON inmuebles (zona_id);

CREATE TABLE IF NOT EXISTS asesores (
  id             text PRIMARY KEY,
  orden          integer NOT NULL DEFAULT 0,
  nombre         text NOT NULL,
  activo         boolean NOT NULL DEFAULT true,
  foto           text NOT NULL DEFAULT '',
  correo         text NOT NULL DEFAULT '',
  telefono       text NOT NULL DEFAULT '',
  hash_password  text NOT NULL DEFAULT '',
  ficha          jsonb NOT NULL DEFAULT '{}'::jsonb,
  actualizado    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS asesores_correo_uq ON asesores (lower(correo)) WHERE correo <> '';

-- --- Núcleo del CRM ----------------------------------------------------------------------------------------------------
-- responsable_id apunta a un asesor (texto). El historial guarda quién hizo el cambio como texto libre.

CREATE TABLE IF NOT EXISTS contactos (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo           text NOT NULL DEFAULT 'persona' CHECK (tipo IN ('persona', 'empresa')),
  nombre         text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 120),
  telefono       text NOT NULL DEFAULT '' CHECK (length(telefono) <= 30),
  correo         text NOT NULL DEFAULT '' CHECK (length(correo) <= 200),
  origen         text NOT NULL DEFAULT '' CHECK (length(origen) <= 60),
  responsable_id text REFERENCES asesores(id) ON DELETE SET NULL,
  etiquetas      text[] NOT NULL DEFAULT '{}',
  creado         timestamptz NOT NULL DEFAULT now(),
  actualizado    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contactos_nombre ON contactos (lower(nombre));
CREATE INDEX IF NOT EXISTS contactos_telefono ON contactos (telefono);

CREATE TABLE IF NOT EXISTS pipelines (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre         text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 80),
  giro           text NOT NULL DEFAULT '' CHECK (length(giro) <= 40),
  por_defecto    boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX IF NOT EXISTS pipelines_un_defecto ON pipelines (por_defecto) WHERE por_defecto;

CREATE TABLE IF NOT EXISTS etapas (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  pipeline_id    uuid NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
  nombre         text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 80),
  orden          integer NOT NULL CHECK (orden >= 0),
  tipo           text NOT NULL DEFAULT 'abierta' CHECK (tipo IN ('abierta', 'ganada', 'perdida')),
  UNIQUE (pipeline_id, orden)
);

CREATE TABLE IF NOT EXISTS oportunidades (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  contacto_id     uuid NOT NULL REFERENCES contactos(id),
  pipeline_id     uuid NOT NULL REFERENCES pipelines(id),
  etapa_id        uuid NOT NULL REFERENCES etapas(id),
  giro            text NOT NULL DEFAULT '' CHECK (length(giro) <= 40),
  valor           numeric(14, 2) NOT NULL DEFAULT 0 CHECK (valor >= 0),
  probabilidad    integer CHECK (probabilidad BETWEEN 0 AND 100),
  cierre_estimado date,
  responsable_id  text REFERENCES asesores(id) ON DELETE SET NULL,
  datos_giro      jsonb NOT NULL DEFAULT '{}'::jsonb,
  motivo_cierre   text NOT NULL DEFAULT '' CHECK (length(motivo_cierre) <= 300),
  cerrado_en      timestamptz,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oportunidades_contacto ON oportunidades (contacto_id);
CREATE INDEX IF NOT EXISTS oportunidades_etapa ON oportunidades (pipeline_id, etapa_id);

CREATE TABLE IF NOT EXISTS actividades (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo            text NOT NULL CHECK (tipo IN ('nota', 'llamada', 'mensaje', 'reunion', 'visita', 'cita', 'sistema')),
  contacto_id     uuid REFERENCES contactos(id),
  oportunidad_id  uuid REFERENCES oportunidades(id),
  responsable_id  text REFERENCES asesores(id) ON DELETE SET NULL,
  ocurrido        timestamptz NOT NULL DEFAULT now(),
  duracion_min    integer CHECK (duracion_min IS NULL OR duracion_min >= 0),
  texto           text NOT NULL DEFAULT '' CHECK (length(texto) <= 4000),
  datos           jsonb NOT NULL DEFAULT '{}'::jsonb,
  creado          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS actividades_contacto ON actividades (contacto_id, ocurrido DESC);
CREATE INDEX IF NOT EXISTS actividades_oportunidad ON actividades (oportunidad_id, ocurrido DESC);

CREATE TABLE IF NOT EXISTS tareas (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  titulo          text NOT NULL CHECK (length(titulo) BETWEEN 1 AND 200),
  vence           timestamptz,
  responsable_id  text REFERENCES asesores(id) ON DELETE SET NULL,
  contacto_id     uuid REFERENCES contactos(id),
  oportunidad_id  uuid REFERENCES oportunidades(id),
  estado          text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'hecha', 'cancelada')),
  completada_en   timestamptz,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tareas_pendientes ON tareas (estado, vence);

CREATE TABLE IF NOT EXISTS historial (
  id           bigserial PRIMARY KEY,
  entidad      text NOT NULL CHECK (length(entidad) BETWEEN 1 AND 60),
  entidad_id   text NOT NULL CHECK (length(entidad_id) BETWEEN 1 AND 64),
  campo        text NOT NULL CHECK (length(campo) BETWEEN 1 AND 60),
  antes        text,
  despues      text,
  usuario      text NOT NULL DEFAULT '',
  cuando       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS historial_entidad ON historial (entidad, entidad_id, cuando);

-- --- Visitas (prospectos) y citas: ligadas al CRM ----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS prospectos (
  id              text PRIMARY KEY,
  recibido        timestamptz NOT NULL DEFAULT now(),
  creado          timestamptz NOT NULL,
  demo            boolean NOT NULL DEFAULT false,
  estado          text NOT NULL DEFAULT 'nuevo',
  datos           jsonb NOT NULL,
  nombre          text NOT NULL DEFAULT '',
  tel             text NOT NULL DEFAULT '',
  inmueble_id     text REFERENCES inmuebles(id) ON DELETE SET NULL,
  asesor_id       text REFERENCES asesores(id) ON DELETE SET NULL,
  contacto_id     uuid REFERENCES contactos(id),
  oportunidad_id  uuid REFERENCES oportunidades(id)
);
CREATE INDEX IF NOT EXISTS prospectos_creado ON prospectos (creado DESC);
CREATE INDEX IF NOT EXISTS prospectos_inmueble ON prospectos (inmueble_id);
CREATE INDEX IF NOT EXISTS prospectos_asesor ON prospectos (asesor_id);
CREATE INDEX IF NOT EXISTS prospectos_oportunidad ON prospectos (oportunidad_id, creado DESC);

-- origen «enlace»: solicitudes de la página pública para agendar. origen «asesor»: citas hechas en campo (con trayecto).
CREATE TABLE IF NOT EXISTS citas (
  id               text PRIMARY KEY,
  origen           text NOT NULL CHECK (origen IN ('enlace', 'asesor')),
  estado           text NOT NULL DEFAULT 'pendiente'
                   CHECK (estado IN ('pendiente', 'confirmada', 'descartada', 'terminada')),
  creada           timestamptz NOT NULL DEFAULT now(),
  actualizada      timestamptz NOT NULL DEFAULT now(),
  nombre           text NOT NULL DEFAULT '',
  tel              text NOT NULL DEFAULT '',
  zona_id          text REFERENCES zonas(id) ON DELETE SET NULL,
  inmueble_id      text REFERENCES inmuebles(id) ON DELETE SET NULL,
  asesor_id        text REFERENCES asesores(id) ON DELETE SET NULL,
  prospecto_id     text REFERENCES prospectos(id) ON DELETE SET NULL,
  confirmada_para  text NOT NULL DEFAULT '',
  nota             text NOT NULL DEFAULT '',
  inicio           timestamptz,
  fin              timestamptz,
  duracion_seg     integer NOT NULL DEFAULT 0,
  ubicacion_final  jsonb,
  trayecto         jsonb NOT NULL DEFAULT '[]'::jsonb,
  datos            jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS citas_origen_creada ON citas (origen, creada DESC);
CREATE INDEX IF NOT EXISTS citas_tel ON citas (tel);
CREATE UNIQUE INDEX IF NOT EXISTS citas_un_pendiente_por_tel ON citas (tel) WHERE origen = 'enlace' AND estado = 'pendiente';

-- «Cargar datos» del embudo: cada archivo es una carga y sus filas esperan aquí hasta agregarse o descartarse.
CREATE TABLE IF NOT EXISTS cargas (
  id           text PRIMARY KEY,
  archivo      text NOT NULL DEFAULT '',
  cargada      timestamptz NOT NULL DEFAULT now(),
  reconocidas  jsonb NOT NULL DEFAULT '[]'::jsonb,
  descartadas  jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS prospectos_espera (
  id           text PRIMARY KEY,
  carga_id     text NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
  fila         integer NOT NULL,
  revision     text NOT NULL DEFAULT 'validar' CHECK (revision IN ('correcto', 'falta', 'validar')),
  problemas    jsonb NOT NULL DEFAULT '[]'::jsonb,
  confirmado   boolean NOT NULL DEFAULT false,
  valores      jsonb NOT NULL DEFAULT '{}'::jsonb,
  actualizado  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prospectos_espera_carga ON prospectos_espera (carga_id, fila);

INSERT INTO esquema_version (version) VALUES (1) ON CONFLICT DO NOTHING;

-- Fotos del catálogo (inmuebles, desarrollos y asesores): JPEG de hasta 4 MB, ya reducidas por el panel.
CREATE TABLE IF NOT EXISTS imagenes (
  id      text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
  datos   bytea NOT NULL,
  creado  timestamptz NOT NULL DEFAULT now()
);
