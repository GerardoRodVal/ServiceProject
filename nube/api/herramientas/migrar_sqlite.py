"""Pasa los datos del gestor de Windows (SQLite + config.json) a un negocio de PostgreSQL.

Se puede correr las veces que haga falta: lo que ya existe no se duplica ni se sobrescribe.
Los registros migrados no cuentan como uso del mes (son historial, no capturas nuevas).

  docker compose run --rm -v "<carpeta con visitas.db>:/datos:ro" migracion \\
      --sqlite /datos/visitas.db --config /datos/config.json --negocio "Mi inmobiliaria" --admin-correo yo@empresa.com
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ESTADOS = ("nuevo", "contactado", "negociando", "cerrado", "perdido")
# Claves que nunca deben ir a la nube aunque estén en el config.json de un equipo.
CLAVES_EXCLUIDAS = {"acceso", "password", "token", "clave", "secret"}


def _fecha(texto):
    d = datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _config_limpia(ruta):
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    return {k: v for k, v in datos.items() if k.lower() not in CLAVES_EXCLUIDAS}


def migrar(sqlite_path, config_path, nombre_negocio, correo_admin, admin_url, giro="inmobiliarias"):
    origen = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    origen.row_factory = sqlite3.Row
    # Gestores nuevos guardan los prospectos en la tabla «prospectos»; los anteriores, en «visitas».
    tablas = {f[0] for f in origen.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    tabla = "prospectos" if "prospectos" in tablas else "visitas"
    filas = origen.execute(f"SELECT id, recibido, creado, demo, estado, datos FROM {tabla}").fetchall()
    origen.close()
    configuracion = _config_limpia(config_path) if config_path else None

    resumen = {"leidos": len(filas), "insertados": 0, "ya_existian": 0, "omitidos": []}
    with psycopg.connect(admin_url, row_factory=dict_row) as con:   # una sola transacción: o entra todo o nada
        negocio = con.execute("SELECT id FROM negocios WHERE nombre = %s ORDER BY creado LIMIT 1", (nombre_negocio,)).fetchone()
        if not negocio:
            negocio = con.execute("INSERT INTO negocios (nombre, giro) VALUES (%s, %s) RETURNING id", (nombre_negocio, giro)).fetchone()
        negocio_id = negocio["id"]

        usuario = con.execute("SELECT negocio_id FROM usuarios WHERE lower(correo) = lower(%s)", (correo_admin,)).fetchone()
        if usuario and usuario["negocio_id"] != negocio_id:
            raise SystemExit(f"El correo {correo_admin} ya pertenece a otro negocio.")
        if not usuario:
            con.execute("INSERT INTO usuarios (negocio_id, correo, nombre, rol) VALUES (%s, %s, %s, 'admin')",
                        (negocio_id, correo_admin, correo_admin.split("@")[0]))

        if configuracion is not None:
            con.execute(
                """INSERT INTO configuraciones (negocio_id, datos) VALUES (%s, %s)
                   ON CONFLICT (negocio_id) DO NOTHING""", (negocio_id, Jsonb(configuracion)))

        for f in filas:
            try:
                datos = json.loads(f["datos"])
                if not isinstance(datos, dict):
                    raise ValueError("los datos no son un objeto")
                if f["estado"] not in ESTADOS:
                    raise ValueError(f"estado desconocido «{f['estado']}»")
                # Un punto de guardado por fila: si la base rechaza una, las demás siguen.
                with con.transaction():
                    insertado = con.execute(
                        """INSERT INTO registros (negocio_id, id, creado, recibido, demo, estado, datos)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (negocio_id, id) DO NOTHING RETURNING 1 AS ok""",
                        (negocio_id, f["id"], _fecha(f["creado"]), _fecha(f["recibido"]), bool(f["demo"]), f["estado"], Jsonb(datos))).fetchone()
            except (ValueError, psycopg.errors.CheckViolation) as e:
                resumen["omitidos"].append({"id": f["id"], "motivo": str(e)})
                continue
            resumen["insertados" if insertado else "ya_existian"] += 1
    resumen["negocio_id"] = str(negocio_id)
    return resumen


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sqlite", required=True, help="visitas.db del gestor de Windows")
    p.add_argument("--config", help="config.json del gestor (opcional)")
    p.add_argument("--negocio", required=True, help="nombre del negocio; si ya existe, se reutiliza")
    p.add_argument("--admin-correo", required=True, help="correo del usuario administrador del negocio")
    p.add_argument("--giro", default="inmobiliarias")
    a = p.parse_args()
    import os
    r = migrar(a.sqlite, a.config, a.negocio, a.admin_correo, os.environ["DATABASE_ADMIN_URL"], a.giro)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r["omitidos"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
