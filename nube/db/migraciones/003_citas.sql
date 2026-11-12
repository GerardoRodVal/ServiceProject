-- Citas de los asesores y su ubicación. Una cita empieza al iniciar sesión en la app y dura hasta «Terminar cita»;
-- mientras está en curso, la app envía su ubicación cada cierto tiempo. Es un dato sensible: solo se recibe con una
-- cita en curso y la API borra el trayecto pasado el periodo de retención (ver RETENCION_UBICACION_DIAS).

CREATE TABLE citas (
  negocio_id        uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id                uuid NOT NULL DEFAULT gen_random_uuid(),
  usuario_id        uuid NOT NULL REFERENCES usuarios(id),
  inicio            timestamptz NOT NULL DEFAULT now(),
  fin               timestamptz,
  -- Último punto recibido ({lat, lng, precision, hora}); al terminar pasa a ser la ubicación final.
  ultima_ubicacion  jsonb,
  ubicacion_final   jsonb,
  PRIMARY KEY (negocio_id, id),
  CHECK (fin IS NULL OR fin >= inicio)
);
-- Un asesor tiene a lo sumo una cita en curso.
CREATE UNIQUE INDEX citas_una_en_curso ON citas (usuario_id) WHERE fin IS NULL;
CREATE INDEX citas_negocio_inicio ON citas (negocio_id, inicio DESC);

CREATE TABLE ubicaciones (
  id          bigserial PRIMARY KEY,
  negocio_id  uuid NOT NULL,
  cita_id     uuid NOT NULL,
  capturada   timestamptz NOT NULL DEFAULT now(),
  lat         double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
  lng         double precision NOT NULL CHECK (lng BETWEEN -180 AND 180),
  precision_m integer NOT NULL DEFAULT 0 CHECK (precision_m >= 0),
  FOREIGN KEY (negocio_id, cita_id) REFERENCES citas (negocio_id, id) ON DELETE CASCADE
);
CREATE INDEX ubicaciones_cita ON ubicaciones (negocio_id, cita_id, capturada);
CREATE INDEX ubicaciones_capturada ON ubicaciones (capturada);

ALTER TABLE citas        ENABLE ROW LEVEL SECURITY;
ALTER TABLE ubicaciones  ENABLE ROW LEVEL SECURITY;
CREATE POLICY aislamiento ON citas       USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON ubicaciones USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());

-- De una cita solo se puede cambiar cómo termina: no el negocio, el asesor ni la hora de inicio.
GRANT SELECT, INSERT                                  ON citas       TO app_api;
GRANT UPDATE (fin, ultima_ubicacion, ubicacion_final) ON citas       TO app_api;
GRANT SELECT, INSERT, DELETE                          ON ubicaciones TO app_api;     -- DELETE: purga por retención
GRANT USAGE ON SEQUENCE ubicaciones_id_seq TO app_api;
