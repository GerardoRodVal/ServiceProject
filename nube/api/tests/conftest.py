"""Pruebas contra la PostgreSQL y el Azurite reales del entorno local (docker compose run --rm pruebas).
Cada prueba crea sus propios negocios con nombre único y los borra al terminar."""
import os
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@dataclass
class Negocio:
    id: str
    admin_correo: str
    capturista_correo: str
    admin: dict        # cabeceras con el token del administrador
    capturista: dict


def visita(id_=None, **extra):
    base = {"id": id_ or f"v-{uuid.uuid4().hex[:12]}", "creado": "2026-11-04T15:00:00+00:00", "nombre": "Ana Ruiz",
            "tel": "5512345678", "asesor": "Laura", "propiedad": "modelo-encino", "presupuesto": 2000000, "enganche": 15,
            "credito": "tramite", "plazo": "3", "decide": "pareja", "notas": "", "score": 55, "cat": "media",
            "razones": [{"txt": "Presupuesto cerca del precio", "pts": 15}], "extras": {}, "provisionales": []}
    base.update(extra)
    return base


@pytest.fixture(scope="session")
def cliente():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_con():
    with psycopg.connect(os.environ["DATABASE_ADMIN_URL"], autocommit=True, row_factory=dict_row) as con:
        yield con


@pytest.fixture
def crear_negocio(admin_con, cliente):
    creados = []

    def _crear():
        sufijo = uuid.uuid4().hex[:10]
        nid = admin_con.execute("INSERT INTO negocios (nombre) VALUES (%s) RETURNING id", (f"Prueba {sufijo}",)).fetchone()["id"]
        correos = {}
        for rol in ("admin", "capturista"):
            correos[rol] = f"{rol}.{sufijo}@prueba.local"
            admin_con.execute("INSERT INTO usuarios (negocio_id, correo, rol) VALUES (%s, %s, %s)", (nid, correos[rol], rol))
        cabeceras = {}
        for rol, correo in correos.items():
            r = cliente.post("/dev/token", json={"correo": correo})
            assert r.status_code == 200, r.text
            cabeceras[rol] = {"Authorization": f"Bearer {r.json()['token']}"}
        creados.append(str(nid))
        return Negocio(str(nid), correos["admin"], correos["capturista"], cabeceras["admin"], cabeceras["capturista"])

    yield _crear
    for nid in creados:
        cliente.app.state.almacen.borrar_prefijo(f"{nid}/")
        admin_con.execute("DELETE FROM negocios WHERE id = %s", (nid,))
