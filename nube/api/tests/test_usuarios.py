"""Alta y gestión de usuarios por el administrador de un negocio."""
import os
import uuid

import psycopg
import pytest

from .conftest import visita


def correo():
    return f"nuevo.{uuid.uuid4().hex[:8]}@prueba.local"


def alta(cliente, n, **datos):
    return cliente.post("/api/usuarios", json={"correo": correo(), **datos}, headers=n.admin)


def test_el_administrador_ve_a_sus_usuarios_y_el_capturista_no(cliente, crear_negocio):
    n = crear_negocio()
    r = cliente.get("/api/usuarios", headers=n.admin)
    assert r.status_code == 200
    assert sorted(u["rol"] for u in r.json()["usuarios"]) == ["admin", "capturista"] and r.json()["activos"] == 2
    assert {"id", "correo", "nombre", "rol", "activo", "creado"} <= set(r.json()["usuarios"][0])
    assert cliente.get("/api/usuarios", headers=n.capturista).status_code == 403
    assert cliente.post("/api/usuarios", json={"correo": correo()}, headers=n.capturista).status_code == 403
    assert cliente.get("/api/usuarios").status_code == 401


def test_dar_de_alta_normaliza_el_correo_y_pone_un_nombre_por_defecto(cliente, crear_negocio):
    n = crear_negocio()
    c = correo()
    r = cliente.post("/api/usuarios", json={"correo": f"  {c.upper()} "}, headers=n.admin)
    assert r.status_code == 200, r.text
    u = r.json()["usuario"]
    assert u["correo"] == c and u["nombre"] == c.split("@")[0] and u["rol"] == "capturista" and u["activo"] is True
    otro = alta(cliente, n, nombre="  Laura Gómez ", rol="admin").json()["usuario"]
    assert otro["nombre"] == "Laura Gómez" and otro["rol"] == "admin"


def test_el_usuario_dado_de_alta_ya_puede_entrar_y_capturar(cliente, crear_negocio):
    n = crear_negocio()
    c = correo()
    assert cliente.post("/api/usuarios", json={"correo": c, "nombre": "Nueva Asesora"}, headers=n.admin).status_code == 200
    token = cliente.post("/dev/token", json={"correo": c}).json()["token"]
    cabecera = {"Authorization": f"Bearer {token}"}
    assert cliente.post("/api/visitas", json=visita(), headers=cabecera).status_code == 200
    assert cliente.get("/api/visitas", headers=n.admin).json()[0]["asesor"] == "Nueva Asesora"      # ya cae a su nombre
    assert cliente.get("/api/visitas", headers=cabecera).status_code == 403                          # capturista: no lee


def test_el_correo_es_unico_en_todo_el_servicio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    c = correo()
    assert cliente.post("/api/usuarios", json={"correo": c}, headers=a.admin).status_code == 200
    for n in (a, b):                                                       # ni en el mismo negocio ni en otro
        r = cliente.post("/api/usuarios", json={"correo": c.upper()}, headers=n.admin)
        assert r.status_code == 409 and "ya está registrado" in r.json()["detail"]
    assert cliente.post("/api/usuarios", json={"correo": a.admin_correo}, headers=b.admin).status_code == 409


@pytest.mark.parametrize("cuerpo", [
    {"correo": "sin-arroba"}, {"correo": "a@b"}, {"correo": "con espacio@x.com"}, {"correo": ""}, {"correo": "x" * 250 + "@y.com"},
    {"correo": "ok@prueba.local", "rol": "superadmin"}, {"correo": "ok@prueba.local", "nombre": "N" * 81},
])
def test_datos_invalidos_se_rechazan(cliente, crear_negocio, cuerpo):
    n = crear_negocio()
    assert cliente.post("/api/usuarios", json=cuerpo, headers=n.admin).status_code == 422
    assert len(cliente.get("/api/usuarios", headers=n.admin).json()["usuarios"]) == 2          # no se creó nada


def test_dar_de_baja_corta_el_acceso_y_reactivar_lo_devuelve(cliente, crear_negocio):
    n = crear_negocio()
    capturista = next(u for u in cliente.get("/api/usuarios", headers=n.admin).json()["usuarios"] if u["rol"] == "capturista")
    assert cliente.post("/api/visitas", json=visita(), headers=n.capturista).status_code == 200
    r = cliente.patch(f"/api/usuarios/{capturista['id']}", json={"activo": False}, headers=n.admin)
    assert r.status_code == 200 and r.json()["usuario"]["activo"] is False
    assert cliente.post("/api/visitas", json=visita(), headers=n.capturista).status_code == 403
    assert cliente.get("/api/uso", headers=n.admin).json()["usuarios_activos"] == 1
    cliente.patch(f"/api/usuarios/{capturista['id']}", json={"activo": True}, headers=n.admin)
    assert cliente.post("/api/visitas", json=visita(), headers=n.capturista).status_code == 200


def test_cambiar_nombre_y_rol(cliente, crear_negocio):
    n = crear_negocio()
    u = alta(cliente, n).json()["usuario"]
    r = cliente.patch(f"/api/usuarios/{u['id']}", json={"nombre": "Nombre Nuevo", "rol": "admin"}, headers=n.admin)
    assert r.json()["usuario"]["nombre"] == "Nombre Nuevo" and r.json()["usuario"]["rol"] == "admin"
    assert r.json()["usuario"]["correo"] == u["correo"]                                         # el correo no cambia
    assert cliente.patch(f"/api/usuarios/{u['id']}", json={"nombre": "  "}, headers=n.admin).status_code == 422
    assert cliente.patch(f"/api/usuarios/{u['id']}", json={"rol": "otro"}, headers=n.admin).status_code == 422


def test_el_administrador_no_puede_quitarse_su_propio_acceso(cliente, crear_negocio):
    n = crear_negocio()
    yo = next(u for u in cliente.get("/api/usuarios", headers=n.admin).json()["usuarios"] if u["correo"] == n.admin_correo)
    for cambio in ({"activo": False}, {"rol": "capturista"}):
        r = cliente.patch(f"/api/usuarios/{yo['id']}", json=cambio, headers=n.admin)
        assert r.status_code == 409 and "propio acceso" in r.json()["detail"]
    # Con otro administrador sí puede quitar a un compañero, pero no a sí mismo.
    otro = alta(cliente, n, rol="admin").json()["usuario"]
    assert cliente.patch(f"/api/usuarios/{otro['id']}", json={"activo": False}, headers=n.admin).status_code == 200
    assert cliente.patch(f"/api/usuarios/{yo['id']}", json={"activo": False}, headers=n.admin).status_code == 409


def test_un_negocio_no_ve_ni_toca_los_usuarios_de_otro(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    de_a = alta(cliente, a, nombre="De A").json()["usuario"]
    assert de_a["id"] not in [u["id"] for u in cliente.get("/api/usuarios", headers=b.admin).json()["usuarios"]]
    assert cliente.patch(f"/api/usuarios/{de_a['id']}", json={"activo": False}, headers=b.admin).status_code == 404
    assert cliente.patch(f"/api/usuarios/{uuid.uuid4()}", json={"activo": False}, headers=b.admin).status_code == 404
    assert cliente.get("/api/usuarios", headers=a.admin).json()["usuarios"][-1]["activo"] is True


def test_la_base_no_deja_cambiar_correo_ni_negocio_de_un_usuario(crear_negocio):
    a = crear_negocio()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as con:
        con.execute("SELECT set_config('app.negocio_id', %s, false)", (a.id,))
        assert con.execute("UPDATE usuarios SET nombre = 'permitido' WHERE lower(correo) = lower(%s)", (a.admin_correo,)).rowcount == 1
        for columna in ("correo = 'otro@x.com'", "negocio_id = gen_random_uuid()", "sub_externo = 'suplantado'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                con.execute(f"UPDATE usuarios SET {columna}")


def test_con_limite_se_avisa_pero_no_se_impide(cliente, crear_negocio):
    n = crear_negocio()
    cfg = cliente.app.state.cfg
    cliente.app.state.cfg = type(cfg)(**{**cfg.__dict__, "limite_usuarios": 3})
    try:
        avisos = [alta(cliente, n).json()["aviso"] for _ in range(3)]
        listado = cliente.get("/api/usuarios", headers=n.admin).json()
    finally:
        cliente.app.state.cfg = cfg
    assert avisos == [None, "excedido", "excedido"]                          # 3.º usuario activo: aún en el límite; luego se excede
    assert listado["activos"] == 5 and listado["limite"] == 3 and listado["aviso"] == "excedido"
