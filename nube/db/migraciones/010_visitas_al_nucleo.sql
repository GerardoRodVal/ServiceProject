-- Prospectos y embudo en uno (ARQUITECTURA_CRM_Y_GIROS.md, sección 14): cada visita que captura el celular (registros)
-- queda ligada a su contacto y a su oportunidad del núcleo. La API hace el enlace al recibir la visita
-- (app/visitas_nucleo.py); las visitas de antes se ligan con POST /api/visitas/ligar.
BEGIN;

ALTER TABLE registros ADD COLUMN contacto_id uuid;
ALTER TABLE registros ADD COLUMN oportunidad_id uuid;
ALTER TABLE registros ADD FOREIGN KEY (negocio_id, contacto_id) REFERENCES contactos (negocio_id, id);
ALTER TABLE registros ADD FOREIGN KEY (negocio_id, oportunidad_id) REFERENCES oportunidades (negocio_id, id);
CREATE INDEX registros_contacto ON registros (negocio_id, contacto_id);
CREATE INDEX registros_oportunidad ON registros (negocio_id, oportunidad_id, creado DESC);

COMMIT;
