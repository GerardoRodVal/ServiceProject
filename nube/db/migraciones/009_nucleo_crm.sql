-- Núcleo general del CRM (fase 1 de ARQUITECTURA_CRM_Y_GIROS.md): lo que sirve a cualquier giro.
-- Contactos, pipelines y etapas, oportunidades, actividades, tareas, catálogo de productos y servicios, historial
-- genérico y registros de giro (propiedades, vehículos, ventas…, cada giro con sus tipos). Todo lleva negocio_id y
-- seguridad por fila, igual que el resto del esquema. Esta migración solo crea tablas: la API todavía no las usa.

-- --- Contactos: personas o empresas. Un contacto puede tener oportunidades y registros de varios giros del mismo negocio.
CREATE TABLE contactos (
  negocio_id     uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id             uuid NOT NULL DEFAULT gen_random_uuid(),
  tipo           text NOT NULL DEFAULT 'persona' CHECK (tipo IN ('persona', 'empresa')),
  nombre         text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 120),
  telefono       text NOT NULL DEFAULT '' CHECK (length(telefono) <= 30),
  correo         text NOT NULL DEFAULT '' CHECK (length(correo) <= 200),
  origen         text NOT NULL DEFAULT '' CHECK (length(origen) <= 60),
  responsable_id uuid REFERENCES usuarios(id),
  etiquetas      text[] NOT NULL DEFAULT '{}',
  creado         timestamptz NOT NULL DEFAULT now(),
  actualizado    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id)
);
CREATE INDEX contactos_negocio_nombre ON contactos (negocio_id, lower(nombre));
CREATE INDEX contactos_negocio_telefono ON contactos (negocio_id, telefono);

-- --- Pipelines y etapas: configurables por negocio. El giro propone uno por defecto (p. ej. inmobiliarias: nuevo,
-- contactado, negociando, cerrado, perdido). tipo marca si la etapa deja el trato abierto, ganado o perdido.
CREATE TABLE pipelines (
  negocio_id     uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id             uuid NOT NULL DEFAULT gen_random_uuid(),
  nombre         text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 80),
  giro           text NOT NULL DEFAULT '' CHECK (length(giro) <= 40),
  por_defecto    boolean NOT NULL DEFAULT false,
  PRIMARY KEY (negocio_id, id)
);
CREATE UNIQUE INDEX pipelines_un_defecto ON pipelines (negocio_id) WHERE por_defecto;

CREATE TABLE etapas (
  negocio_id     uuid NOT NULL,
  pipeline_id    uuid NOT NULL,
  id             uuid NOT NULL DEFAULT gen_random_uuid(),
  nombre         text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 80),
  orden          integer NOT NULL CHECK (orden >= 0),
  tipo           text NOT NULL DEFAULT 'abierta' CHECK (tipo IN ('abierta', 'ganada', 'perdida')),
  PRIMARY KEY (negocio_id, id),
  UNIQUE (negocio_id, pipeline_id, orden),
  FOREIGN KEY (negocio_id, pipeline_id) REFERENCES pipelines (negocio_id, id) ON DELETE CASCADE
);

-- --- Oportunidades: un trato en curso con un contacto, en una etapa de un pipeline. Los datos propios del giro
-- (presupuesto, crédito, cotización, etc.) van en datos_giro; los valida el código de ese giro, no esta tabla.
CREATE TABLE oportunidades (
  negocio_id      uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id              uuid NOT NULL DEFAULT gen_random_uuid(),
  contacto_id     uuid NOT NULL,
  pipeline_id     uuid NOT NULL,
  etapa_id        uuid NOT NULL,
  giro            text NOT NULL DEFAULT '' CHECK (length(giro) <= 40),
  valor           numeric(14, 2) NOT NULL DEFAULT 0 CHECK (valor >= 0),
  probabilidad    integer CHECK (probabilidad BETWEEN 0 AND 100),
  cierre_estimado date,
  responsable_id  uuid REFERENCES usuarios(id),
  datos_giro      jsonb NOT NULL DEFAULT '{}'::jsonb,
  motivo_cierre   text NOT NULL DEFAULT '' CHECK (length(motivo_cierre) <= 300),
  cerrado_en      timestamptz,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id),
  FOREIGN KEY (negocio_id, contacto_id) REFERENCES contactos (negocio_id, id),
  FOREIGN KEY (negocio_id, pipeline_id) REFERENCES pipelines (negocio_id, id),
  FOREIGN KEY (negocio_id, etapa_id)    REFERENCES etapas (negocio_id, id)
);
CREATE INDEX oportunidades_contacto ON oportunidades (negocio_id, contacto_id);
CREATE INDEX oportunidades_etapa ON oportunidades (negocio_id, pipeline_id, etapa_id);

-- --- Actividades: lo que pasó (nota, llamada, mensaje, reunión, visita, cita, cambio de sistema). Son un registro
-- histórico: la API las agrega, no las edita. contacto_id y oportunidad_id son opcionales (una nota puede ser general).
CREATE TABLE actividades (
  negocio_id      uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id              uuid NOT NULL DEFAULT gen_random_uuid(),
  tipo            text NOT NULL CHECK (tipo IN ('nota', 'llamada', 'mensaje', 'reunion', 'visita', 'cita', 'sistema')),
  contacto_id     uuid,
  oportunidad_id  uuid,
  responsable_id  uuid REFERENCES usuarios(id),
  ocurrido        timestamptz NOT NULL DEFAULT now(),
  duracion_min    integer CHECK (duracion_min IS NULL OR duracion_min >= 0),
  texto           text NOT NULL DEFAULT '' CHECK (length(texto) <= 4000),
  datos           jsonb NOT NULL DEFAULT '{}'::jsonb,
  creado          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id),
  FOREIGN KEY (negocio_id, contacto_id)    REFERENCES contactos (negocio_id, id),
  FOREIGN KEY (negocio_id, oportunidad_id) REFERENCES oportunidades (negocio_id, id)
);
CREATE INDEX actividades_contacto ON actividades (negocio_id, contacto_id, ocurrido DESC);
CREATE INDEX actividades_oportunidad ON actividades (negocio_id, oportunidad_id, ocurrido DESC);

-- --- Tareas y recordatorios con fecha. No es lo mismo que una línea de servicio del taller (eso es de su giro).
CREATE TABLE tareas (
  negocio_id      uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id              uuid NOT NULL DEFAULT gen_random_uuid(),
  titulo          text NOT NULL CHECK (length(titulo) BETWEEN 1 AND 200),
  vence           timestamptz,
  responsable_id  uuid REFERENCES usuarios(id),
  contacto_id     uuid,
  oportunidad_id  uuid,
  estado          text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'hecha', 'cancelada')),
  completada_en   timestamptz,
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id),
  FOREIGN KEY (negocio_id, contacto_id)    REFERENCES contactos (negocio_id, id),
  FOREIGN KEY (negocio_id, oportunidad_id) REFERENCES oportunidades (negocio_id, id)
);
CREATE INDEX tareas_pendientes ON tareas (negocio_id, estado, vence);

-- --- Catálogo de productos y servicios, compartido por los giros. El stock es opcional: null = no lleva inventario.
CREATE TABLE catalogo (
  negocio_id      uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id              uuid NOT NULL DEFAULT gen_random_uuid(),
  tipo            text NOT NULL CHECK (tipo IN ('producto', 'servicio')),
  nombre          text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 120),
  codigo          text NOT NULL DEFAULT '' CHECK (length(codigo) <= 60),
  categoria       text NOT NULL DEFAULT '' CHECK (length(categoria) <= 60),
  unidad          text NOT NULL DEFAULT 'pieza' CHECK (length(unidad) <= 20),
  precio          numeric(12, 2) NOT NULL CHECK (precio >= 0),
  costo           numeric(12, 2) NOT NULL DEFAULT 0 CHECK (costo >= 0),
  tasa_iva        numeric(5, 4) NOT NULL DEFAULT 0.16 CHECK (tasa_iva BETWEEN 0 AND 1),
  existencia      numeric(12, 3) CHECK (existencia IS NULL OR existencia >= 0),
  minimo          numeric(12, 3) NOT NULL DEFAULT 0 CHECK (minimo >= 0),
  activo          boolean NOT NULL DEFAULT true,
  actualizado     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id)
);

-- --- Historial genérico de cambios (campo por campo) para cualquier entidad del núcleo o de un giro.
-- Complementa historial_estados, que es específico de los prospectos.
CREATE TABLE historial (
  negocio_id   uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id           bigserial PRIMARY KEY,
  entidad      text NOT NULL CHECK (length(entidad) BETWEEN 1 AND 60),
  entidad_id   text NOT NULL CHECK (length(entidad_id) BETWEEN 1 AND 64),
  campo        text NOT NULL CHECK (length(campo) BETWEEN 1 AND 60),
  antes        text,
  despues      text,
  usuario_id   uuid REFERENCES usuarios(id),
  cuando       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX historial_entidad ON historial (negocio_id, entidad, entidad_id, cuando);

-- --- Registros de giro: cada giro define sus tipos (propiedad, zona, vehiculo, orden_servicio, venta…) y su validación.
-- Se guardan aquí en vez de tener una tabla por giro. Los datos de contacto nunca se copian: van por contacto_id.
CREATE TABLE registros_giro (
  negocio_id      uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id              uuid NOT NULL DEFAULT gen_random_uuid(),
  giro            text NOT NULL CHECK (length(giro) BETWEEN 1 AND 40),
  tipo            text NOT NULL CHECK (length(tipo) BETWEEN 1 AND 40),
  contacto_id     uuid,
  oportunidad_id  uuid,
  datos           jsonb NOT NULL DEFAULT '{}'::jsonb,
  estado          text NOT NULL DEFAULT '' CHECK (length(estado) <= 40),
  creado          timestamptz NOT NULL DEFAULT now(),
  actualizado     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id),
  FOREIGN KEY (negocio_id, contacto_id)    REFERENCES contactos (negocio_id, id),
  FOREIGN KEY (negocio_id, oportunidad_id) REFERENCES oportunidades (negocio_id, id)
);
CREATE INDEX registros_giro_tipo ON registros_giro (negocio_id, giro, tipo, creado DESC);
CREATE INDEX registros_giro_contacto ON registros_giro (negocio_id, contacto_id);

-- --- Archivos del núcleo: ahora pueden colgar de un contacto, una oportunidad o un registro de giro, con una etiqueta
-- (p. ej. la etapa de una foto del taller). registro_id (prospectos) se conserva para no romper lo existente.
ALTER TABLE archivos ADD COLUMN contacto_id    uuid;
ALTER TABLE archivos ADD COLUMN oportunidad_id uuid;
ALTER TABLE archivos ADD COLUMN registro_giro_id uuid;
ALTER TABLE archivos ADD COLUMN etiqueta       text NOT NULL DEFAULT '' CHECK (length(etiqueta) <= 40);
ALTER TABLE archivos ADD FOREIGN KEY (negocio_id, contacto_id)    REFERENCES contactos (negocio_id, id);
ALTER TABLE archivos ADD FOREIGN KEY (negocio_id, oportunidad_id) REFERENCES oportunidades (negocio_id, id);
ALTER TABLE archivos ADD FOREIGN KEY (negocio_id, registro_giro_id) REFERENCES registros_giro (negocio_id, id);

-- --- Seguridad por fila: mismo criterio que el resto. Un negocio no ve ni escribe filas de otro.
ALTER TABLE contactos       ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipelines       ENABLE ROW LEVEL SECURITY;
ALTER TABLE etapas          ENABLE ROW LEVEL SECURITY;
ALTER TABLE oportunidades   ENABLE ROW LEVEL SECURITY;
ALTER TABLE actividades     ENABLE ROW LEVEL SECURITY;
ALTER TABLE tareas          ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalogo        ENABLE ROW LEVEL SECURITY;
ALTER TABLE historial       ENABLE ROW LEVEL SECURITY;
ALTER TABLE registros_giro  ENABLE ROW LEVEL SECURITY;

CREATE POLICY aislamiento ON contactos      USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON pipelines      USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON etapas         USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON oportunidades  USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON actividades    USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON tareas         USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON catalogo       USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON historial      USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON registros_giro USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());

-- --- Permisos mínimos del rol de la API. Se agrega y se edita; el historial y las actividades no se borran desde la API.
-- Los contactos, las oportunidades y las tareas no tienen DELETE: se cierran o se cancelan, para no perder trazabilidad.
GRANT SELECT, INSERT, UPDATE ON contactos      TO app_api;
GRANT SELECT, INSERT, UPDATE ON pipelines      TO app_api;
GRANT SELECT, INSERT, UPDATE ON etapas         TO app_api;
GRANT SELECT, INSERT, UPDATE ON oportunidades  TO app_api;
GRANT SELECT, INSERT         ON actividades    TO app_api;
GRANT SELECT, INSERT, UPDATE ON tareas         TO app_api;
GRANT SELECT, INSERT, UPDATE ON catalogo       TO app_api;
GRANT SELECT, INSERT         ON historial      TO app_api;
GRANT SELECT, INSERT, UPDATE ON registros_giro TO app_api;
GRANT USAGE ON SEQUENCE historial_id_seq TO app_api;
