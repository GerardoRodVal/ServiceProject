-- Rol con el que se conecta la API. No es dueño de las tablas ni puede saltarse la seguridad por fila:
-- todo lo que hace pasa por las políticas de aislamiento entre negocios.
-- La contraseña llega como variable de psql: psql -v app_password='...' -f 000_roles.sql
CREATE ROLE app_api LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
