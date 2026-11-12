-- El simulador del gestor puede reiniciar una conversación de prueba (empezar de cero con el catálogo actualizado).
-- La API solo borra conversaciones de prueba (número que empieza con «0»); las reales no se borran desde el gestor.
GRANT DELETE ON whatsapp_contactos TO app_api;
GRANT DELETE ON whatsapp_mensajes  TO app_api;
