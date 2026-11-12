-- BORRADOR: no está en migraciones/, así que 00_migraciones.sh no lo aplica. Se mueve allá cuando la API use estas tablas.
--
-- Mismas tablas que la base local del gestor (gestor-windows/datos_locales.py), con negocio_id y seguridad por fila:
--   configuraciones (ya existe)  ajustes del negocio en jsonb; zonas, propiedades y asesores salen de ahí
--   zonas, inmuebles             catálogo
--   usuarios (ya existe)         los asesores son usuarios con rol 'capturista': no hay una tabla aparte de personas
--   registros (ya existe)        prospectos; se agregan inmueble_id y asesor_id
--   citas (ya existe)            se amplía para guardar también las solicitudes del enlace público
--
-- Equivalencias con SQLite: TEXT -> text, INTEGER 0/1 -> boolean, TEXT con JSON -> jsonb, fecha ISO -> timestamptz.
-- Los ids de zonas e inmuebles son el mismo texto que genera el gestor (p. ej. 'modelo-encino').

CREATE TABLE zonas (
  negocio_id   uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id           text NOT NULL CHECK (id ~ '^[a-z0-9-]{1,40}$'),
  orden        integer NOT NULL DEFAULT 0,
  nombre       text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 60),
  tipo         text NOT NULL DEFAULT 'colonia' CHECK (tipo IN ('desarrollo', 'colonia')),
  ciudad       text NOT NULL DEFAULT '',
  imagen       text NOT NULL DEFAULT '',
  ficha        jsonb NOT NULL DEFAULT '{}'::jsonb,
  actualizado  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id)
);

CREATE TABLE inmuebles (
  negocio_id        uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id                text NOT NULL CHECK (id ~ '^[a-z0-9-]{1,60}$'),
  orden             integer NOT NULL DEFAULT 0,
  zona_id           text,
  nombre            text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 80),
  tipo              text NOT NULL DEFAULT 'casa' CHECK (tipo IN ('casa', 'departamento', 'terreno')),
  estatus           text NOT NULL DEFAULT 'disponible' CHECK (estatus IN ('disponible', 'apartado', 'vendido')),
  precio            bigint NOT NULL CHECK (precio > 0),
  recamaras         integer NOT NULL DEFAULT 0 CHECK (recamaras >= 0),
  banos             integer NOT NULL DEFAULT 0 CHECK (banos >= 0),
  estacionamientos  integer NOT NULL DEFAULT 0 CHECK (estacionamientos >= 0),
  m2_construccion   integer NOT NULL DEFAULT 0 CHECK (m2_construccion >= 0),
  m2_terreno        integer NOT NULL DEFAULT 0 CHECK (m2_terreno >= 0),
  imagen            text NOT NULL DEFAULT '',
  ficha             jsonb NOT NULL DEFAULT '{}'::jsonb,
  actualizado       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id),
  -- Si se borra la zona, el inmueble queda sin zona (solo esa columna: negocio_id no se toca).
  FOREIGN KEY (negocio_id, zona_id) REFERENCES zonas (negocio_id, id) ON DELETE SET NULL (zona_id)
);
CREATE INDEX inmuebles_zona ON inmuebles (negocio_id, zona_id);

-- Asesores = usuarios con rol 'capturista'. Lo que el panel muestra del asesor y hoy no tiene usuarios:
ALTER TABLE usuarios ADD COLUMN telefono text NOT NULL DEFAULT '';
ALTER TABLE usuarios ADD COLUMN foto text NOT NULL DEFAULT '';
ALTER TABLE usuarios ADD COLUMN orden integer NOT NULL DEFAULT 0;
-- Para relacionar prospectos y citas con un asesor por (negocio, id), como el resto de las tablas.
CREATE UNIQUE INDEX usuarios_negocio_id_uq ON usuarios (negocio_id, id);

ALTER TABLE registros ADD COLUMN inmueble_id text;
ALTER TABLE registros ADD COLUMN asesor_id uuid;
ALTER TABLE registros ADD FOREIGN KEY (negocio_id, inmueble_id) REFERENCES inmuebles (negocio_id, id) ON DELETE SET NULL (inmueble_id);
ALTER TABLE registros ADD FOREIGN KEY (negocio_id, asesor_id) REFERENCES usuarios (negocio_id, id) ON DELETE SET NULL (asesor_id);
CREATE INDEX registros_inmueble ON registros (negocio_id, inmueble_id);
CREATE INDEX registros_asesor ON registros (negocio_id, asesor_id);

-- Una sola tabla de citas: las del enlace público (hoy en solicitudes_cita) y las realizadas por un asesor (hoy en citas).
-- Las realizadas ya tienen usuario_id e inicio; las del enlace aún no tienen asesor ni hora de inicio.
ALTER TABLE citas ALTER COLUMN usuario_id DROP NOT NULL;
ALTER TABLE citas ALTER COLUMN inicio DROP NOT NULL;
ALTER TABLE citas ADD COLUMN origen text NOT NULL DEFAULT 'asesor' CHECK (origen IN ('enlace', 'asesor'));
ALTER TABLE citas ADD COLUMN estado text NOT NULL DEFAULT 'terminada'
  CHECK (estado IN ('pendiente', 'confirmada', 'descartada', 'en_curso', 'terminada'));
ALTER TABLE citas ADD COLUMN creada timestamptz NOT NULL DEFAULT now();
ALTER TABLE citas ADD COLUMN actualizada timestamptz NOT NULL DEFAULT now();
ALTER TABLE citas ADD COLUMN nombre text NOT NULL DEFAULT '';
ALTER TABLE citas ADD COLUMN tel text NOT NULL DEFAULT '' CHECK (tel = '' OR tel ~ '^[0-9]{10}$');
ALTER TABLE citas ADD COLUMN zona_id text;
ALTER TABLE citas ADD COLUMN inmueble_id text;
ALTER TABLE citas ADD COLUMN prospecto_id text;
ALTER TABLE citas ADD COLUMN confirmada_para timestamptz;
ALTER TABLE citas ADD COLUMN nota text NOT NULL DEFAULT '';
ALTER TABLE citas ADD COLUMN datos jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE citas ADD FOREIGN KEY (negocio_id, zona_id) REFERENCES zonas (negocio_id, id) ON DELETE SET NULL (zona_id);
ALTER TABLE citas ADD FOREIGN KEY (negocio_id, inmueble_id) REFERENCES inmuebles (negocio_id, id) ON DELETE SET NULL (inmueble_id);
ALTER TABLE citas ADD FOREIGN KEY (negocio_id, prospecto_id) REFERENCES registros (negocio_id, id) ON DELETE SET NULL (prospecto_id);
-- Un teléfono tiene a lo sumo una solicitud por confirmar.
CREATE UNIQUE INDEX citas_tel_pendiente ON citas (negocio_id, tel) WHERE origen = 'enlace' AND estado = 'pendiente';
CREATE INDEX citas_origen_creada ON citas (negocio_id, origen, creada DESC);

-- --- Seguridad por fila y permisos (igual que el resto) -------------------------------------------
ALTER TABLE zonas     ENABLE ROW LEVEL SECURITY;
ALTER TABLE inmuebles ENABLE ROW LEVEL SECURITY;
CREATE POLICY aislamiento ON zonas     USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON inmuebles USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
GRANT SELECT, INSERT, UPDATE, DELETE ON zonas     TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON inmuebles TO app_api;
-- Las citas del enlace se confirman o descartan desde el gestor (antes: el mismo permiso sobre solicitudes_cita).
GRANT UPDATE (estado, confirmada_para, nota, actualizada, datos) ON citas TO app_api;
-- --- Datos: del jsonb de configuraciones a las tablas ------------------------------------------------
INSERT INTO zonas (negocio_id, id, orden, nombre, tipo, ciudad, imagen, ficha)
SELECT c.negocio_id, z.v->>'id', z.n - 1, z.v->>'nombre', coalesce(nullif(z.v->>'tipo', ''), 'colonia'),
       coalesce(z.v->>'ciudad', ''), coalesce(z.v->>'imagen', ''), z.v - 'id' - 'nombre' - 'tipo' - 'ciudad' - 'imagen'
FROM configuraciones c, jsonb_array_elements(coalesce(c.datos->'zonas', '[]'::jsonb)) WITH ORDINALITY AS z(v, n)
WHERE coalesce(z.v->>'id', '') <> ''
ON CONFLICT DO NOTHING;

INSERT INTO inmuebles (negocio_id, id, orden, zona_id, nombre, tipo, estatus, precio, recamaras, banos, estacionamientos,
                       m2_construccion, m2_terreno, imagen, ficha)
SELECT c.negocio_id, p.v->>'id', p.n - 1,
       (SELECT z.id FROM zonas z WHERE z.negocio_id = c.negocio_id AND z.id = p.v->>'zona'),
       p.v->>'nombre', coalesce(nullif(p.v->>'tipo', ''), 'casa'), coalesce(nullif(p.v->>'estatus', ''), 'disponible'),
       (p.v->>'precio')::bigint, coalesce((p.v->>'recamaras')::int, 0), coalesce((p.v->>'banos')::int, 0),
       coalesce((p.v->>'estacionamientos')::int, 0), coalesce((p.v->>'m2_construccion')::int, 0),
       coalesce((p.v->>'m2_terreno')::int, 0), coalesce(p.v->>'imagen', ''),
       p.v - 'id' - 'nombre' - 'precio' - 'tipo' - 'zona' - 'estatus' - 'recamaras' - 'banos' - 'estacionamientos'
           - 'm2_construccion' - 'm2_terreno' - 'imagen'
FROM configuraciones c, jsonb_array_elements(coalesce(c.datos->'propiedades', '[]'::jsonb)) WITH ORDINALITY AS p(v, n)
WHERE coalesce(p.v->>'id', '') <> ''
ON CONFLICT DO NOTHING;

UPDATE registros r SET inmueble_id = i.id
FROM inmuebles i WHERE i.negocio_id = r.negocio_id AND i.id = r.datos->>'propiedad';

UPDATE registros r SET asesor_id = u.id
FROM usuarios u
WHERE u.negocio_id = r.negocio_id AND r.datos->>'asesor_id' ~ '^[0-9a-f-]{36}$' AND u.id = (r.datos->>'asesor_id')::uuid;

INSERT INTO citas (negocio_id, id, origen, estado, creada, actualizada, nombre, tel, confirmada_para, nota, datos)
SELECT s.negocio_id, s.id, 'enlace', s.estado, s.creada, s.actualizada, coalesce(s.datos->>'nombre', ''), s.tel,
       s.confirmada_para, s.nota, s.datos
FROM solicitudes_cita s
ON CONFLICT DO NOTHING;
-- Cuando la API ya lea y escriba las solicitudes en citas: DROP TABLE solicitudes_cita (y su función crear_solicitud_publica
-- pasa a insertar en citas con origen 'enlace').
-- Nota: en la nube los ids de citas son uuid; las citas que suba un gestor local (ids «sol-…», «cita-…», «L…») reciben uno nuevo.

UPDATE citas SET estado = CASE WHEN fin IS NULL THEN 'en_curso' ELSE 'terminada' END, creada = inicio
WHERE origen = 'asesor';

-- Por último, la configuración deja de duplicar el catálogo:
UPDATE configuraciones SET datos = datos - 'zonas' - 'propiedades';
