-- Agenda en línea: el cliente elige cómo quiere que lo contacten (teléfono, WhatsApp, correo o redes sociales).
-- La clave de la solicitud (columna tel) ya no siempre es un teléfono de 10 dígitos: un correo o un usuario de redes
-- también identifica a la persona para no duplicar su solicitud por confirmar. El teléfono sigue siendo de 10 dígitos.
ALTER TABLE solicitudes_cita DROP CONSTRAINT solicitudes_cita_tel_check;
ALTER TABLE solicitudes_cita ADD CONSTRAINT solicitudes_cita_tel_check
  CHECK (tel ~ '^[0-9]{10}$' OR tel ~ '^[^[:space:]]{3,120}$');

-- El administrador puede eliminar una solicitud (el gestor pide doble confirmación). Descartarla sigue siendo la vía normal.
GRANT DELETE ON solicitudes_cita TO app_api;
