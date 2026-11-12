"""Giros taller y venta sobre el núcleo (reglas en app/giros.py, rutas en app/giros_rutas.py)."""
from .conftest import PNG

BASE = "/api/giros/{giro}/registros"


def _alta(cliente, n, cuerpo):
    r = cliente.post("/api/catalogo", json=cuerpo, headers=n.admin)
    assert r.status_code == 200, r.text
    return r.json()["articulo"]


def _registro(cliente, n, giro, tipo, datos, **extra):
    r = cliente.post(BASE.format(giro=giro), json={"tipo": tipo, "datos": datos, **extra}, headers=n.capturista)
    assert r.status_code == 200, r.text
    return r.json()["registro"]


def _vehiculo(cliente, n, **extra):
    return _registro(cliente, n, "taller", "vehiculo", {"marca": "Nissan", "modelo": "Versa", **extra})


def _servicio(cliente, n, nombre="Cambio de aceite", precio=350):
    return _alta(cliente, n, {"tipo": "servicio", "nombre": nombre, "precio": precio})


def _producto(cliente, n, nombre="Aceite 5W-30", precio=250, existencia=10):
    return _alta(cliente, n, {"tipo": "producto", "nombre": nombre, "precio": precio, "tasa_iva": 0.16,
                              "existencia": existencia})


def _orden(cliente, n, vehiculo, servicios, **extra):
    return _registro(cliente, n, "taller", "orden_servicio",
                     {"vehiculo_id": vehiculo["id"], "servicios": servicios, **extra})


# --- Taller ------------------------------------------------------------------------------------------------------------

def test_un_vehiculo_exige_marca_y_modelo(cliente, crear_negocio):
    a = crear_negocio()
    r = cliente.post(BASE.format(giro="taller"), json={"tipo": "vehiculo", "datos": {"marca": "Nissan"}},
                     headers=a.capturista)
    assert r.status_code == 422


def test_la_orden_toma_el_nombre_de_los_servicios_del_catalogo(cliente, crear_negocio):
    a = crear_negocio()
    v = _vehiculo(cliente, a, placas="abc-123")
    s = _servicio(cliente, a)
    orden = _orden(cliente, a, v, [{"catalogo_id": s["id"], "nota": "Filtro incluido"}], promesa="2026-11-20")
    assert orden["estado"] == "recibido"
    assert orden["datos"]["servicios"][0]["nombre"] == "Cambio de aceite"
    assert orden["datos"]["servicios"][0]["estado"] == "pendiente"
    assert orden["datos"]["vehiculo_id"] == v["id"]
    assert orden["datos"]["promesa"] == "2026-11-20"


def test_la_orden_no_acepta_productos_como_servicios(cliente, crear_negocio):
    a = crear_negocio()
    v = _vehiculo(cliente, a)
    p = _producto(cliente, a)
    r = cliente.post(BASE.format(giro="taller"),
                     json={"tipo": "orden_servicio", "datos": {"vehiculo_id": v["id"],
                                                               "servicios": [{"catalogo_id": p["id"]}]}},
                     headers=a.capturista)
    assert r.status_code == 422


def test_la_orden_debe_apuntar_a_un_vehiculo_del_negocio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    v_b = _vehiculo(cliente, b)
    s = _servicio(cliente, a)
    r = cliente.post(BASE.format(giro="taller"),
                     json={"tipo": "orden_servicio", "datos": {"vehiculo_id": v_b["id"],
                                                               "servicios": [{"catalogo_id": s["id"]}]}},
                     headers=a.capturista)
    assert r.status_code == 422


def test_la_fecha_de_entrega_debe_ser_una_fecha(cliente, crear_negocio):
    a = crear_negocio()
    v = _vehiculo(cliente, a)
    s = _servicio(cliente, a)
    r = cliente.post(BASE.format(giro="taller"),
                     json={"tipo": "orden_servicio", "datos": {"vehiculo_id": v["id"], "promesa": "mañana",
                                                               "servicios": [{"catalogo_id": s["id"]}]}},
                     headers=a.capturista)
    assert r.status_code == 422


def test_cambiar_el_estado_de_un_servicio_deja_historial(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    v = _vehiculo(cliente, a)
    s = _servicio(cliente, a)
    orden = _orden(cliente, a, v, [{"catalogo_id": s["id"]}])
    nuevos = {"vehiculo_id": v["id"], "servicios": [{"catalogo_id": s["id"], "estado": "hecho"}]}
    r = cliente.patch(BASE.format(giro="taller") + f"/{orden['id']}", json={"datos": nuevos}, headers=a.capturista)
    assert r.status_code == 200, r.text
    assert r.json()["registro"]["datos"]["servicios"][0]["estado"] == "hecho"
    filas = admin_con.execute(
        "SELECT campo FROM historial WHERE entidad = 'registro_giro' AND entidad_id = %s", (orden["id"],)).fetchall()
    assert [f["campo"] for f in filas] == ["datos"]


def test_las_fotos_de_una_orden_se_guardan_por_etapa(cliente, crear_negocio):
    a = crear_negocio()
    v = _vehiculo(cliente, a)
    s = _servicio(cliente, a)
    orden = _orden(cliente, a, v, [{"catalogo_id": s["id"]}])
    url = BASE.format(giro="taller") + f"/{orden['id']}/fotos"
    r = cliente.post(url + "?etapa=recepcion", files={"archivo": ("f.png", PNG, "image/png")}, headers=a.capturista)
    assert r.status_code == 200, r.text
    assert cliente.post(url + "?etapa=inventada", files={"archivo": ("f.png", PNG, "image/png")},
                        headers=a.capturista).status_code == 422
    fotos = cliente.get(url, headers=a.admin).json()["fotos"]
    assert [f["etapa"] for f in fotos] == ["recepcion"]


def test_las_fotos_de_una_orden_ajena_no_se_suben(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    v = _vehiculo(cliente, a)
    s = _servicio(cliente, a)
    orden = _orden(cliente, a, v, [{"catalogo_id": s["id"]}])
    url = BASE.format(giro="taller") + f"/{orden['id']}/fotos?etapa=avance"
    assert cliente.post(url, files={"archivo": ("f.png", PNG, "image/png")}, headers=b.capturista).status_code == 404


# --- Venta -------------------------------------------------------------------------------------------------------------

def _venta(cliente, n, datos, **extra):
    return cliente.post(BASE.format(giro="venta"), json={"tipo": "venta", "datos": datos, **extra},
                        headers=n.capturista)


def test_una_venta_toma_precios_del_catalogo_y_calcula_el_iva(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, precio=250, existencia=10)
    balanceo = _servicio(cliente, a, nombre="Balanceo", precio=300)
    r = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 2},
                                       {"catalogo_id": balanceo["id"], "cantidad": 1}],
                            "pago": "efectivo", "recibido": 1000})
    assert r.status_code == 200, r.text
    d = r.json()["registro"]["datos"]
    assert d["subtotal"] == 800.0
    assert d["total"] == 800.0
    assert d["iva"] == 110.34
    assert d["cambio"] == 200.0
    assert r.json()["registro"]["estado"] == "cobrada"


def test_una_venta_baja_la_existencia_y_deja_historial(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    aceite = _producto(cliente, a, existencia=10)
    _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 3}], "pago": "tarjeta"})
    articulo = cliente.get("/api/catalogo", headers=a.admin).json()["catalogo"][0]
    assert float(articulo["existencia"]) == 7
    filas = admin_con.execute(
        "SELECT antes, despues FROM historial WHERE entidad = 'articulo' AND entidad_id = %s AND campo = 'existencia'",
        (aceite["id"],)).fetchall()
    assert len(filas) == 1


def test_una_venta_sin_existencia_suficiente_no_se_registra(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, existencia=2)
    r = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 5}], "pago": "tarjeta"})
    assert r.status_code == 422
    assert float(cliente.get("/api/catalogo", headers=a.admin).json()["catalogo"][0]["existencia"]) == 2
    assert cliente.get(BASE.format(giro="venta"), headers=a.admin).json()["registros"] == []


def test_una_venta_no_acepta_precios_que_mande_el_cliente(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a)
    r = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 1, "precio": 1}], "pago": "tarjeta"})
    assert r.status_code == 422


def test_efectivo_exige_recibido_suficiente(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, precio=250)
    r = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 2}], "pago": "efectivo", "recibido": 400})
    assert r.status_code == 422


def test_con_tarjeta_lo_recibido_es_el_total(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, precio=250)
    r = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 2}], "pago": "tarjeta",
                            "recibido": 9999})
    d = r.json()["registro"]["datos"]
    assert d["total"] == 500.0 and d["recibido"] == 500.0 and d["cambio"] == 0.0


def test_el_descuento_no_puede_pasar_del_subtotal(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, precio=250)
    r = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 1}], "pago": "tarjeta",
                            "descuento": 300})
    assert r.status_code == 422


def test_un_articulo_inactivo_no_se_vende(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a)
    cliente.patch(f"/api/catalogo/{aceite['id']}", json={"activo": False}, headers=a.admin)
    assert _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 1}], "pago": "tarjeta"}).status_code == 422


def test_una_venta_no_se_corrige(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a)
    venta = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 1}], "pago": "tarjeta"}).json()["registro"]
    r = cliente.patch(BASE.format(giro="venta") + f"/{venta['id']}", json={"estado": "cobrada"}, headers=a.admin)
    assert r.status_code == 422


def test_una_venta_puede_ligarse_a_un_contacto(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a)
    c = cliente.post("/api/contactos", json={"nombre": "Pedro"}, headers=a.capturista).json()["contacto"]
    r = _venta(cliente, a, {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 1}], "pago": "tarjeta"},
               contacto_id=c["id"])
    assert r.status_code == 200
    assert r.json()["registro"]["contacto_id"] == c["id"]
