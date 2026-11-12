"""Vista del cliente: la ficha pública de una casa, por enlace (token del negocio + identificador de la casa)."""
import os

import psycopg
import pytest

IMAGEN = "ab" * 16
CASA = {
    "id": "casa-uno", "nombre": "Casa Uno", "direccion": "Calle 1 #23", "descripcion": "Casa amplia con jardín.", "precio": 2500000,
    "recamaras": 3, "banos": 2, "estacionamientos": 2, "m2_construccion": 120, "m2_terreno": 150, "entrega": "inmediata",
    "estatus": "disponible", "zona": "z1", "imagen": IMAGEN,
    # Datos internos del inmueble que el cliente NO debe ver:
    "cuota_mantenimiento": 750, "predial": 3900, "motivo_venta": "divorcio", "garantia": "10 años", "ubicacion_mapa": "19.4,-99.1",
    "atributos": ["pago_infonavit"], "antiguedad": 4,
}
CONFIG = {"negocio": {"nombre": "Inmobiliaria Prueba"}, "propiedades": [CASA], "zonas": [{"id": "z1", "nombre": "El Pedregal", "seguridad": 4}],
          "score": {"umbral_alta": 70}, "formulario": {"campos": []}}
CAMPOS_PUBLICOS = {"id", "nombre", "direccion", "descripcion", "precio", "recamaras", "banos", "estacionamientos", "m2_construccion",
                   "m2_terreno", "entrega", "estatus", "imagen"}


def publicar(cliente, n, config=CONFIG):
    assert cliente.put("/api/config", json=config, headers=n.admin).status_code == 200


def token_de(cliente, cabecera):
    return cliente.get("/api/app/config", headers=cabecera).json()["negocio"]["token_publico"]


def ver(cliente, token, casa="casa-uno"):
    return cliente.get(f"/api/publico/{token}/casa/{casa}")


def test_cada_negocio_tiene_su_token_y_lo_ven_sus_asesores(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    ta = token_de(cliente, a.admin)
    assert len(ta) == 32 and set(ta) <= set("0123456789abcdef")
    assert token_de(cliente, a.capturista) == ta                       # el asesor lo necesita para armar el QR
    assert token_de(cliente, b.admin) != ta


def test_la_ficha_se_ve_sin_sesion_y_solo_trae_lo_que_se_muestra(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    r = ver(cliente, token_de(cliente, n.capturista))
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"negocio", "zona", "casa"} and set(d["casa"]) == CAMPOS_PUBLICOS
    assert d["negocio"] == {"nombre": "Inmobiliaria Prueba"} and d["zona"] == {"nombre": "El Pedregal"}
    assert d["casa"]["nombre"] == "Casa Uno" and d["casa"]["precio"] == 2500000 and d["casa"]["estatus"] == "disponible"
    assert d["casa"]["imagen"] == IMAGEN and d["casa"]["entrega"] == "inmediata"
    texto = r.text
    for privado in ("cuota_mantenimiento", "predial", "divorcio", "garantia", "ubicacion_mapa", "pago_infonavit", "score", "umbral_alta", "seguridad"):
        assert privado not in texto, privado
    assert r.headers["cache-control"] == "no-cache" and r.headers["referrer-policy"] == "no-referrer" and r.headers["x-robots-tag"] == "noindex"


def test_enlaces_que_no_valen_dan_el_mismo_404(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    publicar(cliente, a)
    publicar(cliente, b, {**CONFIG, "propiedades": [{**CASA, "id": "casa-de-b"}]})
    ta, tb = token_de(cliente, a.admin), token_de(cliente, b.admin)
    mensajes = set()
    for token, casa in ((ta, "no-existe"), ("0" * 32, "casa-uno"), (tb, "casa-uno"), (ta, "casa-de-b"),      # casa de otro negocio con el token propio
                        ("x" * 32, "casa-uno"), (ta.upper(), "casa-uno"), (ta, "CASA-UNO"), (ta, "a" * 61), (ta[:31], "casa-uno")):
        r = ver(cliente, token, casa)
        assert r.status_code == 404, (token, casa)
        mensajes.add(r.json()["detail"])
    assert mensajes == {"No encontramos la casa."}                      # no se revela qué parte del enlace estaba mal
    assert ver(cliente, tb, "casa-de-b").status_code == 200 and ver(cliente, ta).status_code == 200


def test_un_negocio_sin_configuracion_no_publica_nada(cliente, crear_negocio):
    n = crear_negocio()
    assert ver(cliente, token_de(cliente, n.admin)).status_code == 404


def test_los_campos_raros_se_limpian(cliente, crear_negocio):
    n = crear_negocio()
    raro = {"id": "casa-uno", "nombre": "N" * 500, "precio": "mucho", "recamaras": True, "banos": None, "m2_terreno": [1],
            "estatus": "inventado", "entrega": "mañana", "imagen": "../../etc/passwd", "descripcion": "x" * 5000, "zona": "no-existe"}
    publicar(cliente, n, {"propiedades": [raro]})
    d = ver(cliente, token_de(cliente, n.admin)).json()
    c = d["casa"]
    assert len(c["nombre"]) == 120 and len(c["descripcion"]) == 2000
    assert (c["precio"], c["recamaras"], c["banos"], c["m2_terreno"]) == (0, 0, 0, 0)
    assert (c["estatus"], c["entrega"], c["imagen"]) == ("", "", "")
    assert d["zona"] is None and d["negocio"]["nombre"].startswith("Prueba")                 # sin nombre en la config: el del negocio


def test_rotar_el_enlace_invalida_los_qr_anteriores(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    publicar(cliente, a)
    publicar(cliente, b)
    viejo_a, tb = token_de(cliente, a.admin), token_de(cliente, b.admin)
    assert cliente.post("/api/publico/rotar", headers=a.capturista).status_code == 403
    assert cliente.post("/api/publico/rotar").status_code == 401
    r = cliente.post("/api/publico/rotar", headers=a.admin)
    nuevo = r.json()["token_publico"]
    assert r.status_code == 200 and nuevo != viejo_a and token_de(cliente, a.capturista) == nuevo
    assert ver(cliente, viejo_a).status_code == 404 and ver(cliente, nuevo).status_code == 200
    assert token_de(cliente, b.admin) == tb and ver(cliente, tb).status_code == 200            # el otro negocio no se entera


def test_la_pagina_del_cliente_se_sirve_sin_enviar_el_token_a_terceros(cliente):
    r = cliente.get("/cliente.html")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["x-robots-tag"] == "noindex"
    assert 'name="referrer" content="no-referrer"' in r.text and "api/publico/" in r.text


def test_la_base_solo_deja_rotar_el_token_del_negocio(crear_negocio):
    n = crear_negocio()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as con:
        con.execute("SELECT set_config('app.negocio_id', %s, false)", (n.id,))
        assert con.execute("UPDATE negocios SET token_publico = md5('x') WHERE id = %s", (n.id,)).rowcount == 1
        for cambio in ("nombre = 'otro'", "giro = 'otro'", "id = gen_random_uuid()"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                con.execute(f"UPDATE negocios SET {cambio}")
        with pytest.raises(psycopg.errors.CheckViolation):                 # el formato lo vigila la base
            con.execute("UPDATE negocios SET token_publico = 'corto'")
        assert con.execute("SELECT count(*) FROM config_publica('no-es-un-token')").fetchone()[0] == 0
