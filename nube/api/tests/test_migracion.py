"""Migración desde el SQLite del gestor de Windows."""
import json
import os
import sqlite3

from herramientas.migrar_sqlite import migrar

from .conftest import visita


def _sqlite_de_prueba(ruta):
    con = sqlite3.connect(ruta)
    con.execute("""CREATE TABLE visitas (id TEXT PRIMARY KEY, recibido TEXT NOT NULL, creado TEXT NOT NULL,
                   demo INTEGER NOT NULL DEFAULT 0, estado TEXT NOT NULL DEFAULT 'nuevo', datos TEXT NOT NULL)""")
    filas = [
        ("cel-000001", "2026-11-03T10:00:00+00:00", "2026-11-03T09:00:00+00:00", 0, "negociando", visita("cel-000001")),
        ("imp-aaaa0001", "2026-11-04T10:00:00+00:00", "2026-10-01T09:00:00Z", 0, "nuevo", visita("imp-aaaa0001", nombre="Cargado")),
        ("demo-000001", "2026-11-04T10:00:00+00:00", "2026-11-01T09:00:00+00:00", 1, "nuevo", visita("demo-000001")),
        ("estado-malo-1", "2026-11-04T10:00:00+00:00", "2026-11-01T09:00:00+00:00", 0, "raro", visita("estado-malo-1")),
        ("id con espacios", "2026-11-04T10:00:00+00:00", "2026-11-01T09:00:00+00:00", 0, "nuevo", visita("x")),
    ]
    for id_, recibido, creado, demo, estado, datos in filas:
        con.execute("INSERT INTO visitas VALUES (?, ?, ?, ?, ?, ?)", (id_, recibido, creado, demo, estado, json.dumps(datos)))
    con.execute("INSERT INTO visitas VALUES ('roto-000001', ?, ?, 0, 'nuevo', '{no es json')", ("2026-11-04T10:00:00+00:00",) * 2)
    con.commit()
    con.close()


def test_migracion_completa_y_repetible(tmp_path, cliente, admin_con):
    origen = tmp_path / "visitas.db"
    _sqlite_de_prueba(origen)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"negocio": {"nombre": "X"}, "propiedades": [{"id": "p1"}], "acceso": {"url_celular": "http://192.168.1.5:8080"}}),
                      encoding="utf-8")
    url = os.environ["DATABASE_ADMIN_URL"]
    nombre, correo = "Migrada prueba", "migrada.prueba@prueba.local"
    try:
        r1 = migrar(str(origen), str(config), nombre, correo, url)
        assert (r1["leidos"], r1["insertados"], r1["ya_existian"]) == (6, 3, 0)
        assert sorted(o["id"] for o in r1["omitidos"]) == ["estado-malo-1", "id con espacios", "roto-000001"]

        r2 = migrar(str(origen), str(config), nombre, correo, url)       # repetirla no duplica nada
        assert (r2["insertados"], r2["ya_existian"], r2["negocio_id"]) == (0, 3, r1["negocio_id"])

        nid = r1["negocio_id"]
        cfg = admin_con.execute("SELECT datos FROM configuraciones WHERE negocio_id = %s", (nid,)).fetchone()["datos"]
        assert "acceso" not in cfg and cfg["propiedades"] == [{"id": "p1"}]          # la URL del celular no viaja a la nube
        assert admin_con.execute("SELECT count(*) AS n FROM uso_mensual WHERE negocio_id = %s", (nid,)).fetchone()["n"] == 0

        token = cliente.post("/dev/token", json={"correo": correo}).json()["token"]
        lista = cliente.get("/api/visitas", headers={"Authorization": f"Bearer {token}"}).json()
        por_id = {v["id"]: v for v in lista}
        assert set(por_id) == {"cel-000001", "imp-aaaa0001", "demo-000001"}
        assert por_id["cel-000001"]["estado"] == "negociando"                    # conserva el seguimiento
        assert por_id["demo-000001"]["demo"] is True
        assert por_id["imp-aaaa0001"]["creado"].startswith("2026-10-01T09:00:00")  # la fecha original, no la de la migración
    finally:
        admin_con.execute("DELETE FROM negocios WHERE nombre = %s", (nombre,))
