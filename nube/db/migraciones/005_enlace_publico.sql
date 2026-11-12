-- Enlace público de cada negocio para la vista del cliente: el QR que el asesor le muestra a un cliente abre una página
-- con los datos de UNA casa, sin sesión. El enlace lleva este token (128 bits al azar: no se puede adivinar) y el
-- identificador de la casa. Quien tenga el QR puede ver esa ficha; nada más del negocio.
ALTER TABLE negocios
  ADD COLUMN token_publico text NOT NULL DEFAULT replace(gen_random_uuid()::text, '-', '');
ALTER TABLE negocios ADD CONSTRAINT negocios_token_publico_formato CHECK (token_publico ~ '^[0-9a-f]{32}$');
CREATE UNIQUE INDEX negocios_token_publico_uq ON negocios (token_publico);

-- La API no cambia datos de un negocio salvo para rotar este token (si un QR se filtra, se invalida): se le quita el UPDATE
-- amplio que tenía y se le deja solo esa columna.
REVOKE UPDATE ON negocios FROM app_api;
GRANT UPDATE (token_publico) ON negocios TO app_api;

-- Lectura pública por token: devuelve el nombre del negocio y su configuración; la API se queda solo con la casa pedida y
-- con los campos que se muestran al cliente. Sin token válido no devuelve nada.
CREATE FUNCTION config_publica(p_token text)
  RETURNS TABLE (nombre text, datos jsonb)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
  AS $$
    SELECT n.nombre, COALESCE(c.datos, '{}'::jsonb)
    FROM negocios n LEFT JOIN configuraciones c ON c.negocio_id = n.id
    WHERE n.token_publico = p_token
  $$;
REVOKE ALL ON FUNCTION config_publica(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION config_publica(text) TO app_api;
