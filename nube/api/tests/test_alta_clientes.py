"""Alta de clientes: la herramienta de quien opera el servicio (herramientas/clientes.py)."""
import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from herramientas import clientes


@pytest.fixture
def limpiar(admin_con, cliente):
    """Borra los negocios que la prueba cree (todos llevan el sufijo único) y sus archivos."""
    sufijo = uuid.uuid4().hex[:10]
    yield sufijo
    for f in admin_con.execute("SELECT id FROM negocios WHERE nombre LIKE %s", (f"%{sufijo}%",)).fetchall():
        cliente.app.state.almacen.borrar_prefijo(f"{f['id']}/")
    admin_con.execute("DELETE FROM negocios WHERE nombre LIKE %s", (f"%{sufijo}%",))


def correo(sufijo, quien="ana"):
    return f"{quien}.{sufijo}@alta.local"


def cli(*args):
    return clientes.main(list(args), admin_url=os.environ["DATABASE_ADMIN_URL"])


def token(cliente, c):
    r = cliente.post("/dev/token", json={"correo": c})
    return r, ({"Authorization": f"Bearer {r.json()['token']}"} if r.status_code == 200 else None)


def test_crear_deja_un_negocio_y_un_administrador_que_ya_puede_trabajar(cliente, limpiar, admin_con, capsys):
    s = limpiar
    assert cli("crear", "--nombre", f"Sol {s}", "--admin-correo", correo(s).upper(), "--admin-nombre", "Ana Sol") == 0
    salida = capsys.readouterr().out
    n = admin_con.execute("SELECT * FROM negocios WHERE nombre = %s", (f"Sol {s}",)).fetchone()
    assert n["giro"] == "inmobiliarias" and n["activo"] and f"id {n['id']}" in salida
    assert correo(s) in salida and n["token_publico"] not in salida                          # el enlace público no se imprime
    u = admin_con.execute("SELECT correo, nombre, rol, activo FROM usuarios WHERE negocio_id = %s", (n["id"],)).fetchall()
    assert u == [{"correo": correo(s), "nombre": "Ana Sol", "rol": "admin", "activo": True}]    # el correo se guarda en minúsculas
    # Entra a la API como administrador y da de alta a una asesora, sin SQL.
    r, cab = token(cliente, correo(s))
    assert r.status_code == 200
    assert cliente.get("/api/app/config", headers=cab).json()["negocio"]["nombre"] == f"Sol {s}"
    alta = cliente.post("/api/usuarios", json={"correo": correo(s, "laura"), "rol": "capturista"}, headers=cab)
    assert alta.status_code == 200, alta.text


def test_el_nombre_del_administrador_por_omision_sale_del_correo(limpiar, admin_con):
    s = limpiar
    assert cli("crear", "--nombre", f"Luna {s}", "--admin-correo", correo(s, "pepe")) == 0
    assert admin_con.execute("SELECT nombre FROM usuarios WHERE lower(correo) = %s", (correo(s, "pepe"),)).fetchone()["nombre"] == f"pepe.{s}"


@pytest.mark.parametrize("args, mensaje", [
    (["--nombre", "   ", "--admin-correo", "a@b.mx"], "nombre del negocio"),
    (["--nombre", "N" * 121, "--admin-correo", "a@b.mx"], "nombre del negocio"),
    (["--nombre", "Ok", "--admin-correo", "sin-arroba"], "no es un correo válido"),
    (["--nombre", "Ok", "--admin-correo", "a@b.mx", "--giro", "Con Espacios"], "giro"),
])
def test_datos_invalidos_no_crean_nada(args, mensaje, admin_con, capsys):
    antes = admin_con.execute("SELECT count(*) AS n FROM negocios").fetchone()["n"]
    assert cli("crear", *args) == 1
    assert mensaje in capsys.readouterr().err
    assert admin_con.execute("SELECT count(*) AS n FROM negocios").fetchone()["n"] == antes


def test_no_se_repite_el_nombre_ni_el_correo(limpiar, admin_con, capsys):
    s = limpiar
    assert cli("crear", "--nombre", f"Sol {s}", "--admin-correo", correo(s)) == 0
    capsys.readouterr()
    assert cli("crear", "--nombre", f"SOL {s}", "--admin-correo", correo(s, "otro")) == 1             # mismo nombre, otras mayúsculas
    assert "Ya existe un negocio" in capsys.readouterr().err
    assert cli("crear", "--nombre", f"Otro {s}", "--admin-correo", correo(s).upper()) == 1           # correo de otro negocio
    assert f"Sol {s}" in capsys.readouterr().err
    # Lo fallido no dejó medio negocio: el segundo intento no creó el negocio «Otro».
    assert admin_con.execute("SELECT count(*) AS n FROM negocios WHERE nombre = %s", (f"Otro {s}",)).fetchone()["n"] == 0


def test_agregar_otro_administrador_o_recuperar_el_acceso(cliente, limpiar, admin_con, capsys):
    s = limpiar
    cli("crear", "--nombre", f"Sol {s}", "--admin-correo", correo(s))
    cli("crear", "--nombre", f"Mar {s}", "--admin-correo", correo(s, "mar"))
    capsys.readouterr()
    assert cli("admin", "--negocio", f"sol {s}", "--correo", correo(s, "luis")) == 0
    assert "agregado" in capsys.readouterr().out
    # Ana (la única administradora) se dio de baja y se reactiva con el mismo comando; una asesora se promueve.
    admin_con.execute("UPDATE usuarios SET activo = false, rol = 'capturista' WHERE lower(correo) = %s", (correo(s),))
    assert token(cliente, correo(s))[0].status_code == 404
    assert cli("admin", "--negocio", f"Sol {s}", "--correo", correo(s)) == 0
    assert "promovido" in capsys.readouterr().out
    r, cab = token(cliente, correo(s))
    assert r.status_code == 200 and cliente.get("/api/usuarios", headers=cab).status_code == 200      # vuelve a ser administradora
    # Un correo de otro negocio no se toca, y un negocio que no existe se avisa.
    assert cli("admin", "--negocio", f"Sol {s}", "--correo", correo(s, "mar")) == 1
    assert "otro negocio" in capsys.readouterr().err
    assert admin_con.execute("SELECT negocio_id FROM usuarios WHERE lower(correo) = %s", (correo(s, "mar"),)).fetchone()["negocio_id"] != \
        admin_con.execute("SELECT id FROM negocios WHERE nombre = %s", (f"Sol {s}",)).fetchone()["id"]
    assert cli("admin", "--negocio", f"No existe {s}", "--correo", correo(s, "x")) == 1
    assert "No existe el negocio" in capsys.readouterr().err


def test_suspender_bloquea_el_acceso_y_el_qr_sin_borrar_y_reactivar_lo_devuelve(cliente, limpiar, admin_con, capsys):
    s = limpiar
    cli("crear", "--nombre", f"Sol {s}", "--admin-correo", correo(s))
    cli("crear", "--nombre", f"Mar {s}", "--admin-correo", correo(s, "mar"))
    _, cab = token(cliente, correo(s))
    _, cab_mar = token(cliente, correo(s, "mar"))
    casa = {"id": "casa-uno", "nombre": "Casa Uno", "precio": 1000000}
    assert cliente.put("/api/config", json={"propiedades": [casa]}, headers=cab).status_code == 200
    n = cliente.get("/api/app/config", headers=cab).json()["negocio"]["token_publico"]
    assert cliente.get(f"/api/publico/{n}/casa/casa-uno").status_code == 200
    capsys.readouterr()

    assert cli("suspender", "--negocio", f"Sol {s}") == 0 and "suspendido" in capsys.readouterr().out
    assert cliente.get("/api/app/config", headers=cab).status_code == 403                    # el token que ya tenía deja de servir
    assert token(cliente, correo(s))[0].status_code == 404
    assert cliente.get(f"/api/publico/{n}/casa/casa-uno").status_code == 404                # y el QR deja de abrir
    assert cliente.get("/api/app/config", headers=cab_mar).status_code == 200                # el otro negocio sigue igual
    assert admin_con.execute("SELECT count(*) AS n FROM configuraciones c JOIN negocios n ON n.id = c.negocio_id WHERE n.nombre = %s",
                             (f"Sol {s}",)).fetchone()["n"] == 1                              # nada se borró
    assert cli("suspender", "--negocio", f"Sol {s}") == 0 and "ya estaba suspendido" in capsys.readouterr().out

    assert cli("reactivar", "--negocio", f"Sol {s}") == 0 and "reactivado" in capsys.readouterr().out
    assert cliente.get("/api/app/config", headers=cab).status_code == 200
    assert cliente.get(f"/api/publico/{n}/casa/casa-uno").status_code == 200


def test_listar_muestra_los_negocios(limpiar, capsys):
    s = limpiar
    cli("crear", "--nombre", f"Sol {s}", "--admin-correo", correo(s))
    cli("suspender", "--negocio", f"Sol {s}")
    capsys.readouterr()
    assert cli("listar") == 0
    linea = next(l for l in capsys.readouterr().out.splitlines() if l.startswith(f"Sol {s}"))
    assert "SUSPENDIDO" in linea and "1 usuarios" in linea and correo(s) in linea


def test_la_api_publica_no_puede_crear_ni_suspender_negocios(limpiar):
    s = limpiar
    cli("crear", "--nombre", f"Sol {s}", "--admin-correo", correo(s))
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as con:    # el rol que usa la API
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("INSERT INTO negocios (nombre) VALUES ('intruso')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("UPDATE negocios SET activo = false")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("DELETE FROM negocios")


def test_sin_conexion_de_administrador_avisa(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_ADMIN_URL")
    assert clientes.main(["listar"]) == 2
    assert "DATABASE_ADMIN_URL" in capsys.readouterr().err
