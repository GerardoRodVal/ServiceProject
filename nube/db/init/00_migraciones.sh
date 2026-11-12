#!/bin/bash
# Aplica las migraciones de /migraciones que todavía no se hayan aplicado (las registra en schema_migraciones).
# Corre solo cuando el volumen de PostgreSQL está vacío; para una base que ya existe:
#   docker compose exec -T db bash /docker-entrypoint-initdb.d/00_migraciones.sh
# En Azure se corren estos mismos .sql con psql contra el servidor (ver README, "Paso a Azure").
set -euo pipefail

PSQL=(psql -v ON_ERROR_STOP=1 -v app_password="$APP_DB_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")

"${PSQL[@]}" -q -c "CREATE TABLE IF NOT EXISTS schema_migraciones (nombre text PRIMARY KEY, aplicada timestamptz NOT NULL DEFAULT now())"

# Una base creada antes de existir este registro ya tiene aplicadas la 000 y la 001.
if [ "$("${PSQL[@]}" -At -c "SELECT to_regclass('public.negocios') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM schema_migraciones)")" = "t" ]; then
  "${PSQL[@]}" -q -c "INSERT INTO schema_migraciones (nombre) VALUES ('000_roles.sql'), ('001_inicial.sql')"
fi

for archivo in /migraciones/*.sql; do
  nombre="$(basename "$archivo")"
  if [ "$("${PSQL[@]}" -At -c "SELECT count(*) FROM schema_migraciones WHERE nombre = '$nombre'")" = "0" ]; then
    echo ">> aplicando $nombre"
    "${PSQL[@]}" -f "$archivo"
    "${PSQL[@]}" -q -c "INSERT INTO schema_migraciones (nombre) VALUES ('$nombre')"
  fi
done
echo ">> migraciones al día"
