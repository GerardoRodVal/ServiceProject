"""Lo más importante de un servicio multi-negocio: un negocio nunca ve ni toca los datos de otro."""
import os

import psycopg
import pytest

from .conftest import PNG, visita


def test_un_negocio_no_ve_los_registros_de_otro(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    v = visita()
    assert cliente.post("/api/visitas", json=v, headers=a.capturista).status_code == 200
    assert [x["id"] for x in cliente.get("/api/visitas", headers=a.admin).json()] == [v["id"]]
    assert cliente.get("/api/visitas", headers=b.admin).json() == []


def test_no_se_puede_cambiar_el_estado_de_un_registro_ajeno(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    v = visita()
    cliente.post("/api/visitas", json=v, headers=a.capturista)
    r = cliente.post("/api/visitas/estado", json={"id": v["id"], "estado": "cerrado"}, headers=b.admin)
    assert r.status_code == 404
    estado = cliente.get("/api/visitas", headers=a.admin).json()[0]["estado"]
    assert estado == "nuevo"


def test_el_mismo_id_en_dos_negocios_no_choca(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    v = visita("compartido-001", nombre="De A")
    w = visita("compartido-001", nombre="De B")
    assert cliente.post("/api/visitas", json=v, headers=a.capturista).json()["duplicado"] is False
    assert cliente.post("/api/visitas", json=w, headers=b.capturista).json()["duplicado"] is False
    assert cliente.get("/api/visitas", headers=a.admin).json()[0]["nombre"] == "De A"
    assert cliente.get("/api/visitas", headers=b.admin).json()[0]["nombre"] == "De B"


def test_borrar_importados_solo_afecta_al_propio_negocio(cliente, crear_negocio, admin_con):
    a, b = crear_negocio(), crear_negocio()
    for n in (a, b):
        admin_con.execute("INSERT INTO registros (negocio_id, id, creado, datos) VALUES (%s, 'imp-abcdef12', now(), '{}'::jsonb)", (n.id,))
    r = cliente.post("/api/visitas/borrar-importados", headers=a.admin)
    assert r.json()["borrados"] == 1
    assert len(cliente.get("/api/visitas", headers=b.admin).json()) == 1


def test_un_archivo_ajeno_no_se_puede_descargar(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    r = cliente.post("/api/archivos", files={"archivo": ("foto.png", PNG, "image/png")}, headers=a.capturista)
    assert r.status_code == 200, r.text
    archivo_id = r.json()["id"]
    assert cliente.get(f"/api/archivos/{archivo_id}", headers=a.admin).content == PNG
    assert cliente.get(f"/api/archivos/{archivo_id}", headers=b.admin).status_code == 404


def test_no_se_puede_ligar_un_archivo_a_un_registro_ajeno(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    v = visita()
    cliente.post("/api/visitas", json=v, headers=a.capturista)
    r = cliente.post(f"/api/archivos?registro_id={v['id']}", files={"archivo": ("f.png", PNG, "image/png")}, headers=b.capturista)
    assert r.status_code == 404


# --- Directo contra PostgreSQL, con el mismo rol que usa la API (sin pasar por el código de la API) ---

def _con_app():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def test_sin_negocio_en_la_sesion_la_base_no_devuelve_nada(cliente, crear_negocio):
    a = crear_negocio()
    cliente.post("/api/visitas", json=visita(), headers=a.capturista)
    with _con_app() as con:
        for tabla in ("registros", "usuarios", "negocios", "archivos", "uso_mensual", "historial_estados", "configuraciones",
                      "citas", "ubicaciones"):
            assert con.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0] == 0, tabla


def test_la_base_rechaza_escribir_en_otro_negocio(crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    with _con_app() as con:
        con.execute("SELECT set_config('app.negocio_id', %s, false)", (a.id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("INSERT INTO registros (negocio_id, id, creado, datos) VALUES (%s, 'intruso-001', now(), '{}'::jsonb)", (b.id,))


def test_el_rol_de_la_api_no_puede_crear_negocios_ni_usuarios_de_otro_negocio(crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    with _con_app() as con:
        con.execute("SELECT set_config('app.negocio_id', %s, false)", (a.id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("INSERT INTO negocios (nombre) VALUES ('no debería')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):          # dar de alta en el propio negocio sí se puede; en otro, no
            con.execute("INSERT INTO usuarios (negocio_id, correo, rol) VALUES (%s, 'x@y.com', 'admin')", (b.id,))
