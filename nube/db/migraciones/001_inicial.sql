-- Esquema multi-negocio. Cada tabla de datos lleva negocio_id y tiene seguridad por fila (RLS):
-- la API fija el negocio de la sesión con set_config('app.negocio_id', ...) y PostgreSQL filtra solo.
-- Aunque la API tuviera un error, un negocio no puede leer ni escribir filas de otro.

CREATE TABLE negocios (
  id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre  text NOT NULL CHECK (length(nombre) BETWEEN 1 AND 120),
  giro    text NOT NULL DEFAULT 'inmobiliarias',
  creado  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usuarios (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  negocio_id   uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  correo       text NOT NULL CHECK (length(correo) BETWEEN 3 AND 200),
  nombre       text NOT NULL DEFAULT '',
  rol          text NOT NULL CHECK (rol IN ('admin', 'capturista')),
  -- Identificador que da el proveedor de identidad (en Entra, el claim "oid"). Vacío en desarrollo local.
  sub_externo  text,
  activo       boolean NOT NULL DEFAULT true,
  creado       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX usuarios_correo_uq ON usuarios (lower(correo));
CREATE UNIQUE INDEX usuarios_sub_uq ON usuarios (sub_externo) WHERE sub_externo IS NOT NULL;
CREATE INDEX usuarios_negocio ON usuarios (negocio_id);

-- Reemplaza a config.json: campos del formulario, catálogo, pesos del score. Nada de secretos aquí.
CREATE TABLE configuraciones (
  negocio_id   uuid PRIMARY KEY REFERENCES negocios(id) ON DELETE CASCADE,
  datos        jsonb NOT NULL DEFAULT '{}'::jsonb,
  actualizado  timestamptz NOT NULL DEFAULT now()
);

-- Prospectos / visitas. Los datos del formulario van en "datos" (jsonb) porque cada negocio
-- personaliza sus campos; el estado y las fechas son columnas porque se filtran y se ordenan.
CREATE TABLE registros (
  negocio_id  uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id          text NOT NULL CHECK (id ~ '^[A-Za-z0-9_.-]{6,64}$'),
  creado      timestamptz NOT NULL,
  recibido    timestamptz NOT NULL DEFAULT now(),
  demo        boolean NOT NULL DEFAULT false,
  estado      text NOT NULL DEFAULT 'nuevo'
              CHECK (estado IN ('nuevo', 'contactado', 'negociando', 'cerrado', 'perdido')),
  datos       jsonb NOT NULL,
  -- La clave incluye el negocio: dos negocios pueden tener el mismo id sin chocar.
  PRIMARY KEY (negocio_id, id)
);
CREATE INDEX registros_negocio_creado ON registros (negocio_id, creado DESC);
CREATE INDEX registros_negocio_estado ON registros (negocio_id, estado);

CREATE TABLE historial_estados (
  id               bigserial PRIMARY KEY,
  negocio_id       uuid NOT NULL,
  registro_id      text NOT NULL,
  estado_anterior  text,
  estado_nuevo     text NOT NULL,
  usuario_id       uuid,
  cambiado         timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (negocio_id, registro_id) REFERENCES registros (negocio_id, id) ON DELETE CASCADE
);
CREATE INDEX historial_registro ON historial_estados (negocio_id, registro_id, cambiado);

-- Metadatos de archivos (fotos). El contenido vive en Blob Storage; aquí solo se guarda dónde está.
CREATE TABLE archivos (
  negocio_id   uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id           uuid NOT NULL DEFAULT gen_random_uuid(),
  registro_id  text,
  nombre_blob  text NOT NULL,
  tipo         text NOT NULL,
  bytes        integer NOT NULL CHECK (bytes > 0),
  subido_por   uuid,
  subido       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id)
);

-- Medición de uso por mes: base para los límites y los paquetes extra de los planes.
CREATE TABLE uso_mensual (
  negocio_id      uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  periodo         date NOT NULL,            -- primer día del mes (UTC)
  registros       integer NOT NULL DEFAULT 0,
  archivos_bytes  bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (negocio_id, periodo)
);

-- --- Seguridad por fila ---------------------------------------------------------------------------

CREATE FUNCTION negocio_actual() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$ SELECT nullif(current_setting('app.negocio_id', true), '')::uuid $$;

ALTER TABLE negocios          ENABLE ROW LEVEL SECURITY;
ALTER TABLE usuarios          ENABLE ROW LEVEL SECURITY;
ALTER TABLE configuraciones   ENABLE ROW LEVEL SECURITY;
ALTER TABLE registros         ENABLE ROW LEVEL SECURITY;
ALTER TABLE historial_estados ENABLE ROW LEVEL SECURITY;
ALTER TABLE archivos          ENABLE ROW LEVEL SECURITY;
ALTER TABLE uso_mensual       ENABLE ROW LEVEL SECURITY;

CREATE POLICY aislamiento ON negocios USING (id = negocio_actual());
CREATE POLICY aislamiento ON usuarios          USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON configuraciones   USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON registros         USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON historial_estados USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON archivos          USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON uso_mensual       USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());

-- Permisos mínimos del rol de la API. Crear negocios y usuarios no está aquí a propósito:
-- eso lo hace el alta de clientes (herramientas con el usuario administrador), no la API pública.
GRANT SELECT, UPDATE                 ON negocios          TO app_api;
GRANT SELECT                         ON usuarios          TO app_api;
GRANT SELECT, INSERT, UPDATE         ON configuraciones   TO app_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON registros         TO app_api;
GRANT SELECT, INSERT                 ON historial_estados TO app_api;
GRANT SELECT, INSERT, DELETE         ON archivos          TO app_api;
GRANT SELECT, INSERT, UPDATE         ON uso_mensual       TO app_api;
GRANT USAGE ON SEQUENCE historial_estados_id_seq TO app_api;

-- Para saber a qué negocio pertenece quien llega con un token hay que leer usuarios antes de conocer
-- el negocio. Esta función lo resuelve sin abrir la tabla completa: devuelve solo al usuario pedido.
CREATE FUNCTION identificar_usuario(p_identidad text)
  RETURNS TABLE (usuario_id uuid, negocio_id uuid, rol text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
  AS $$
    SELECT u.id, u.negocio_id, u.rol
    FROM usuarios u
    WHERE u.activo AND (u.sub_externo = p_identidad OR lower(u.correo) = lower(p_identidad))
    LIMIT 1
  $$;
REVOKE ALL ON FUNCTION identificar_usuario(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION identificar_usuario(text) TO app_api;
