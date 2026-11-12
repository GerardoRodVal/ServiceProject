-- Agenda en línea: el cliente pide una cita desde el enlace o el QR que reparte el asesor (página agendar.html), sin sesión.
-- El asesor la confirma por llamada o mensaje desde el gestor; la visita presencial después se registra en la app.
-- Datos personales mínimos (nombre, teléfono, interés): solo los ve el administrador de su negocio.
CREATE TABLE solicitudes_cita (
  negocio_id      uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  id              uuid NOT NULL DEFAULT gen_random_uuid(),
  creada          timestamptz NOT NULL DEFAULT now(),
  actualizada     timestamptz NOT NULL DEFAULT now(),
  tel             text NOT NULL CHECK (tel ~ '^[0-9]{10}$'),
  -- nombre, tel, zona, casa, fecha, horario, comentarios, asesor: ya validados por la API contra el catálogo del negocio.
  datos           jsonb NOT NULL,
  estado          text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'confirmada', 'descartada')),
  confirmada_para timestamptz,
  nota            text NOT NULL DEFAULT '' CHECK (length(nota) <= 300),
  PRIMARY KEY (negocio_id, id)
);
CREATE INDEX solicitudes_cita_negocio ON solicitudes_cita (negocio_id, creada DESC);
-- Un mismo teléfono tiene a lo sumo una solicitud por confirmar: si vuelve a pedir, se actualiza la que ya está.
CREATE UNIQUE INDEX solicitudes_cita_una_pendiente ON solicitudes_cita (negocio_id, tel) WHERE estado = 'pendiente';

ALTER TABLE solicitudes_cita ENABLE ROW LEVEL SECURITY;
CREATE POLICY aislamiento ON solicitudes_cita USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());

-- El administrador las lee, las crea (el gestor reenvía las que llegan por su red local) y cambia su estado.
-- No se cambia el negocio, el teléfono ni la fecha en que llegó.
GRANT SELECT, INSERT                                              ON solicitudes_cita TO app_api;
GRANT UPDATE (datos, estado, confirmada_para, nota, actualizada) ON solicitudes_cita TO app_api;

-- Alta pública por token, sin negocio en la sesión: la función exige el token de un negocio activo. Si ese teléfono ya
-- tiene una solicitud por confirmar, se actualiza en vez de duplicarla. Sin token válido no hace nada y devuelve NULL.
CREATE FUNCTION crear_solicitud_publica(p_token text, p_tel text, p_datos jsonb)
  RETURNS uuid
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
  AS $$
  DECLARE
    v_negocio uuid;
    v_id      uuid;
  BEGIN
    SELECT id INTO v_negocio FROM negocios WHERE token_publico = p_token AND activo;
    IF v_negocio IS NULL THEN
      RETURN NULL;
    END IF;
    UPDATE solicitudes_cita SET datos = p_datos, actualizada = now()
      WHERE negocio_id = v_negocio AND tel = p_tel AND estado = 'pendiente'
      RETURNING id INTO v_id;
    IF v_id IS NULL THEN
      INSERT INTO solicitudes_cita (negocio_id, tel, datos) VALUES (v_negocio, p_tel, p_datos) RETURNING id INTO v_id;
    END IF;
    RETURN v_id;
  END
  $$;
REVOKE ALL ON FUNCTION crear_solicitud_publica(text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION crear_solicitud_publica(text, text, jsonb) TO app_api;
