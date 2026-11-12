-- Suspender a un cliente (por ejemplo, por falta de pago) sin borrar nada: con activo = false nadie de ese negocio entra a
-- la API y su vista del cliente (el QR de las casas) deja de abrir. Los datos se conservan; reactivarlo lo devuelve todo.
-- La API no puede cambiar esta columna (solo token_publico, ver 005): lo hace la herramienta de alta de clientes.
ALTER TABLE negocios ADD COLUMN activo boolean NOT NULL DEFAULT true;

CREATE OR REPLACE FUNCTION identificar_usuario(p_identidad text)
  RETURNS TABLE (usuario_id uuid, negocio_id uuid, rol text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
  AS $$
    SELECT u.id, u.negocio_id, u.rol
    FROM usuarios u JOIN negocios n ON n.id = u.negocio_id
    WHERE u.activo AND n.activo AND (u.sub_externo = p_identidad OR lower(u.correo) = lower(p_identidad))
    LIMIT 1
  $$;

CREATE OR REPLACE FUNCTION config_publica(p_token text)
  RETURNS TABLE (nombre text, datos jsonb)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
  AS $$
    SELECT n.nombre, COALESCE(c.datos, '{}'::jsonb)
    FROM negocios n LEFT JOIN configuraciones c ON c.negocio_id = n.id
    WHERE n.token_publico = p_token AND n.activo
  $$;
