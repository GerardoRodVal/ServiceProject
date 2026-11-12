"""Núcleo del CRM (migración 009): aislamiento entre negocios, vínculos del mismo negocio y permisos del rol de la API.
Se prueba directo contra PostgreSQL con el rol de la API (app_api), como lo usa la aplicación."""
import os
import uuid

import psycopg
import pytest
from psycopg import errors

NUCLEO = ["contactos", "pipelines", "etapas", "oportunidades", "actividades", "tareas", "catalogo", "historial",
          "registros_giro"]


@pytest.fixture
def como_api():
    """Conexión con el rol de la API. Cada prueba fija el negocio con set_config, igual que db.transaccion()."""
    con = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    yield con
    con.close()


def _fijar(con, negocio_id):
    con.execute("SELECT set_config('app.negocio_id', %s, false)", (str(negocio_id),))


def _contacto(admin_con, negocio_id, nombre="Ana Ruiz"):
    return admin_con.execute(
        "INSERT INTO contactos (negocio_id, nombre) VALUES (%s, %s) RETURNING id", (negocio_id, nombre)
    ).fetchone()["id"]


def _pipeline_con_etapas(admin_con, negocio_id):
    pid = admin_con.execute(
        "INSERT INTO pipelines (negocio_id, nombre, giro, por_defecto) VALUES (%s, 'Ventas', 'inmobiliarias', true) RETURNING id",
        (negocio_id,)).fetchone()["id"]
    etapas = []
    for orden, (nombre, tipo) in enumerate([("nuevo", "abierta"), ("cerrado", "ganada"), ("perdido", "perdida")]):
        etapas.append(admin_con.execute(
            "INSERT INTO etapas (negocio_id, pipeline_id, nombre, orden, tipo) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (negocio_id, pid, nombre, orden, tipo)).fetchone()["id"])
    return pid, etapas[0]


def test_las_tablas_del_nucleo_tienen_seguridad_por_fila(admin_con):
    filas = admin_con.execute(
        "SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(%s)", (NUCLEO,)).fetchall()
    assert {f["relname"] for f in filas} == set(NUCLEO)
    assert all(f["relrowsecurity"] for f in filas)


def test_un_negocio_no_ve_los_contactos_de_otro(crear_negocio, admin_con, como_api):
    a, b = crear_negocio(), crear_negocio()
    _contacto(admin_con, a.id)
    _fijar(como_api, b.id)
    assert como_api.execute("SELECT count(*) FROM contactos").fetchone()[0] == 0
    _fijar(como_api, a.id)
    assert como_api.execute("SELECT count(*) FROM contactos").fetchone()[0] == 1


def test_no_se_puede_insertar_un_contacto_en_otro_negocio(crear_negocio, como_api):
    a, b = crear_negocio(), crear_negocio()
    _fijar(como_api, b.id)
    with pytest.raises(errors.InsufficientPrivilege):
        como_api.execute("INSERT INTO contactos (negocio_id, nombre) VALUES (%s, 'Intruso')", (a.id,))


def test_una_oportunidad_solo_puede_ligarse_a_un_contacto_del_mismo_negocio(crear_negocio, admin_con):
    a, b = crear_negocio(), crear_negocio()
    contacto_b = _contacto(admin_con, b.id)
    pid, etapa = _pipeline_con_etapas(admin_con, a.id)
    with pytest.raises(errors.ForeignKeyViolation):
        admin_con.execute(
            "INSERT INTO oportunidades (negocio_id, contacto_id, pipeline_id, etapa_id) VALUES (%s, %s, %s, %s)",
            (a.id, contacto_b, pid, etapa))


def test_solo_un_pipeline_por_defecto_por_negocio(crear_negocio, admin_con):
    a = crear_negocio()
    _pipeline_con_etapas(admin_con, a.id)
    with pytest.raises(errors.UniqueViolation):
        admin_con.execute(
            "INSERT INTO pipelines (negocio_id, nombre, por_defecto) VALUES (%s, 'Otro', true)", (a.id,))


def test_el_rol_de_la_api_no_puede_borrar_contactos_ni_oportunidades(crear_negocio, admin_con, como_api):
    a = crear_negocio()
    contacto = _contacto(admin_con, a.id)
    _fijar(como_api, a.id)
    with pytest.raises(errors.InsufficientPrivilege):
        como_api.execute("DELETE FROM contactos WHERE id = %s", (contacto,))


def test_el_historial_y_las_actividades_no_se_editan_desde_la_api(crear_negocio, admin_con, como_api):
    a = crear_negocio()
    contacto = _contacto(admin_con, a.id)
    actividad = admin_con.execute(
        "INSERT INTO actividades (negocio_id, tipo, contacto_id, texto) VALUES (%s, 'nota', %s, 'Llamó') RETURNING id",
        (a.id, contacto)).fetchone()["id"]
    _fijar(como_api, a.id)
    with pytest.raises(errors.InsufficientPrivilege):
        como_api.execute("UPDATE actividades SET texto = 'editado' WHERE id = %s", (actividad,))


def test_un_archivo_puede_colgar_de_un_contacto_con_etiqueta(crear_negocio, admin_con, como_api):
    a = crear_negocio()
    contacto = _contacto(admin_con, a.id)
    _fijar(como_api, a.id)
    como_api.execute(
        "INSERT INTO archivos (negocio_id, id, nombre_blob, tipo, bytes, contacto_id, etiqueta) "
        "VALUES (%s, %s, 'x.jpg', 'image/jpeg', 10, %s, 'recepcion')",
        (a.id, str(uuid.uuid4()), contacto))
    assert como_api.execute("SELECT count(*) FROM archivos WHERE contacto_id = %s", (contacto,)).fetchone()[0] == 1
