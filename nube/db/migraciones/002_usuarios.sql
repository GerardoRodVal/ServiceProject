-- El administrador de un negocio da de alta y gestiona a sus usuarios desde la API.
-- Sigue siendo la seguridad por fila la que impide tocar usuarios de otro negocio (la política de «usuarios»
-- tiene WITH CHECK). Del UPDATE se excluyen negocio_id, correo y sub_externo: cambiar de negocio o de
-- identidad no es algo que la API pueda hacer; para eso se da de baja y se crea otro usuario.
GRANT INSERT ON usuarios TO app_api;
GRANT UPDATE (nombre, rol, activo) ON usuarios TO app_api;
