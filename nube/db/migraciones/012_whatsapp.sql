-- WhatsApp con IA (complemento con costo aparte). Cada negocio conecta su propio número de WhatsApp Business (Cloud API
-- de Meta): Meta llama al webhook de la API con los mensajes de sus clientes y la IA contesta con el catálogo del negocio.
-- El webhook no trae sesión: el negocio se reconoce por un token propio en la dirección (128 bits al azar, distinto del de
-- los QR para que rotar uno no rompa el otro) y cada mensaje se comprueba con la firma de Meta antes de guardarse.

-- Complemento contratado. Como «activo», la API no puede cambiarlo: lo activa quien opera el servicio (herramienta de
-- clientes). Sin él, los mensajes se guardan pero la IA no contesta.
ALTER TABLE negocios ADD COLUMN whatsapp_ia boolean NOT NULL DEFAULT false;

CREATE TABLE whatsapp_cuentas (
  negocio_id          uuid PRIMARY KEY REFERENCES negocios(id) ON DELETE CASCADE,
  token_webhook       text NOT NULL CHECK (token_webhook ~ '^[0-9a-f]{32}$'),
  phone_number_id     text NOT NULL DEFAULT '' CHECK (phone_number_id ~ '^[0-9]{0,30}$'),
  waba_id             text NOT NULL DEFAULT '' CHECK (waba_id ~ '^[0-9]{0,30}$'),
  version             text NOT NULL DEFAULT 'v23.0' CHECK (version ~ '^v[0-9]{1,3}\.[0-9]$'),
  verify_token        text NOT NULL CHECK (verify_token ~ '^[A-Za-z0-9_-]{16,100}$'),
  -- Token de acceso y clave secreta de la app de Meta, cifrados por la API (AES-GCM) con una clave que no vive en la base.
  token_cifrado       bytea,
  app_secret_cifrado  bytea,
  ia_activa           boolean NOT NULL DEFAULT false,
  ia                  jsonb NOT NULL DEFAULT '{}'::jsonb,     -- nombre, tono, bienvenida e instrucciones de la IA
  actualizado         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX whatsapp_cuentas_token_uq ON whatsapp_cuentas (token_webhook);

-- Una fila por persona que le escribe al negocio. wa_id es el número que da Meta (con lada de país); tel10 son sus
-- últimos 10 dígitos, para ligarlo con los prospectos. ia_pausada: un asesor tomó la conversación y la IA ya no contesta.
CREATE TABLE whatsapp_contactos (
  negocio_id       uuid NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
  wa_id            text NOT NULL CHECK (wa_id ~ '^[0-9]{6,20}$'),
  nombre           text NOT NULL DEFAULT '' CHECK (length(nombre) <= 120),
  tel10            text NOT NULL DEFAULT '' CHECK (tel10 ~ '^([0-9]{10})?$'),
  ia_pausada       boolean NOT NULL DEFAULT false,
  ultimo_entrante  timestamptz,                -- abre la ventana de 24 horas en la que se puede escribir sin plantilla
  ultimo           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, wa_id)
);
CREATE INDEX whatsapp_contactos_ultimo ON whatsapp_contactos (negocio_id, ultimo DESC);

CREATE TABLE whatsapp_mensajes (
  negocio_id  uuid NOT NULL,
  id          bigserial,
  wa_id       text NOT NULL,
  -- Identificador de Meta. Meta reintenta los avisos: con él, un mismo mensaje no se guarda ni se contesta dos veces.
  wamid       text CHECK (length(wamid) <= 200),
  autor       text NOT NULL CHECK (autor IN ('cliente', 'ia', 'asesor')),
  tipo        text NOT NULL DEFAULT 'text' CHECK (length(tipo) <= 30),
  texto       text NOT NULL DEFAULT '' CHECK (length(texto) <= 4096),
  creado      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (negocio_id, id),
  FOREIGN KEY (negocio_id, wa_id) REFERENCES whatsapp_contactos (negocio_id, wa_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX whatsapp_mensajes_wamid_uq ON whatsapp_mensajes (negocio_id, wamid) WHERE wamid IS NOT NULL;
CREATE INDEX whatsapp_mensajes_conversacion ON whatsapp_mensajes (negocio_id, wa_id, id DESC);

-- Medición del complemento: respuestas de la IA por mes (base para cobrarlo aparte).
ALTER TABLE uso_mensual ADD COLUMN whatsapp_respuestas integer NOT NULL DEFAULT 0;

ALTER TABLE whatsapp_cuentas   ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_contactos ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_mensajes  ENABLE ROW LEVEL SECURITY;
CREATE POLICY aislamiento ON whatsapp_cuentas   USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON whatsapp_contactos USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());
CREATE POLICY aislamiento ON whatsapp_mensajes  USING (negocio_id = negocio_actual()) WITH CHECK (negocio_id = negocio_actual());

GRANT SELECT, INSERT, UPDATE ON whatsapp_cuentas   TO app_api;
GRANT SELECT, INSERT, UPDATE ON whatsapp_contactos TO app_api;
GRANT SELECT, INSERT         ON whatsapp_mensajes  TO app_api;
GRANT USAGE ON SEQUENCE whatsapp_mensajes_id_seq TO app_api;

-- El webhook llega sin sesión: esta función dice de qué negocio activo es un token, y nada más. Con ese negocio la API
-- abre una transacción normal (con su seguridad por fila) para leer la cuenta y comprobar la firma.
CREATE FUNCTION whatsapp_negocio(p_token text)
  RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
  AS $$
    SELECT c.negocio_id
    FROM whatsapp_cuentas c JOIN negocios n ON n.id = c.negocio_id
    WHERE c.token_webhook = p_token AND n.activo
  $$;
REVOKE ALL ON FUNCTION whatsapp_negocio(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION whatsapp_negocio(text) TO app_api;
