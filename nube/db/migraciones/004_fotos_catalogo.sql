-- Fotos del catálogo (inmuebles): las sube el gestor al publicar la configuración y las ven los celulares.
-- Se guardan en la misma tabla de archivos, distinguidas por «proposito».
ALTER TABLE archivos
  ADD COLUMN proposito text NOT NULL DEFAULT 'registro' CHECK (proposito IN ('registro', 'catalogo'));

-- El identificador de una foto del catálogo es único en TODO el servicio (no solo por negocio): se usa en una dirección
-- pública, así que un negocio no puede registrar el de otro y hacer que se sirva su imagen en su lugar.
CREATE UNIQUE INDEX archivos_catalogo_id_uq ON archivos (id) WHERE proposito = 'catalogo';

-- Lectura pública de una foto del catálogo por su identificador (128 bits al azar: no se puede adivinar). El celular la
-- pide con una etiqueta <img>, que no puede mandar credenciales. La función solo devuelve fotos del catálogo y solo
-- dónde está el archivo; nada de otros datos del negocio.
CREATE FUNCTION imagen_catalogo(p_id uuid)
  RETURNS TABLE (nombre_blob text, tipo text)
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
  AS $$
    SELECT a.nombre_blob, a.tipo FROM archivos a WHERE a.id = p_id AND a.proposito = 'catalogo' LIMIT 1
  $$;
REVOKE ALL ON FUNCTION imagen_catalogo(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION imagen_catalogo(uuid) TO app_api;
