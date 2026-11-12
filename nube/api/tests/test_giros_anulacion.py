"""Anulación de ventas: solo administrador, motivo obligatorio, una sola vez, y la existencia vuelve al inventario."""
BASE = "/api/giros/venta/registros"


def _producto(cliente, n, existencia=10, precio=250):
    r = cliente.post("/api/catalogo", json={"tipo": "producto", "nombre": "Aceite 5W-30", "precio": precio,
                                            "tasa_iva": 0.16, "existencia": existencia}, headers=n.admin)
    return r.json()["articulo"]


def _servicio(cliente, n):
    r = cliente.post("/api/catalogo", json={"tipo": "servicio", "nombre": "Balanceo", "precio": 300},
                     headers=n.admin)
    return r.json()["articulo"]


def _venta(cliente, n, lineas, pago="tarjeta", **extra):
    r = cliente.post(BASE, json={"tipo": "venta", "datos": {"lineas": lineas, "pago": pago, **extra}},
                     headers=n.capturista)
    assert r.status_code == 200, r.text
    return r.json()["registro"]


def _existencia(cliente, n, articulo_id):
    lista = cliente.get("/api/catalogo?incluir_inactivos=true", headers=n.admin).json()["catalogo"]
    fila = next(a for a in lista if a["id"] == articulo_id)
    return None if fila["existencia"] is None else float(fila["existencia"])


def _anular(cliente, n, venta_id, motivo="Cobro duplicado en caja", headers=None):
    return cliente.post(f"{BASE}/{venta_id}/anular", json={"motivo": motivo}, headers=headers or n.admin)


def test_anular_devuelve_la_existencia_y_marca_la_venta(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, existencia=10)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 3}])
    assert _existencia(cliente, a, aceite["id"]) == 7
    r = _anular(cliente, a, venta["id"])
    assert r.status_code == 200, r.text
    assert r.json()["registro"]["estado"] == "anulada"
    assert _existencia(cliente, a, aceite["id"]) == 10
    assert r.json()["registro"]["datos"]["anulacion"]["motivo"] == "Cobro duplicado en caja"


def test_una_venta_se_anula_una_sola_vez(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, existencia=10)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 2}])
    assert _anular(cliente, a, venta["id"]).status_code == 200
    assert _anular(cliente, a, venta["id"]).status_code == 422
    assert _existencia(cliente, a, aceite["id"]) == 10   # no se devuelve dos veces


def test_solo_el_administrador_anula(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 1}])
    assert _anular(cliente, a, venta["id"], headers=a.capturista).status_code == 403


def test_el_motivo_es_obligatorio(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 1}])
    assert _anular(cliente, a, venta["id"], motivo="   ").status_code == 422
    assert _anular(cliente, a, venta["id"], motivo="ok").status_code == 422
    assert cliente.post(f"{BASE}/{venta['id']}/anular", json={}, headers=a.admin).status_code == 422


def test_una_venta_ajena_no_se_anula(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    aceite = _producto(cliente, a)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 1}])
    assert _anular(cliente, b, venta["id"]).status_code == 404


def test_una_venta_anulada_no_se_puede_corregir_ni_nacer_anulada(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 1}])
    _anular(cliente, a, venta["id"])
    r = cliente.patch(f"{BASE}/{venta['id']}", json={"estado": "cobrada"}, headers=a.admin)
    assert r.status_code == 422
    nacida = cliente.post(BASE, json={"tipo": "venta", "estado": "anulada",
                                      "datos": {"lineas": [{"catalogo_id": aceite["id"], "cantidad": 1}],
                                                "pago": "tarjeta"}}, headers=a.capturista)
    assert nacida.status_code == 422


def test_anular_una_venta_con_servicios_no_toca_su_existencia(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, existencia=5)
    servicio = _servicio(cliente, a)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 1},
                                {"catalogo_id": servicio["id"], "cantidad": 1}])
    assert _anular(cliente, a, venta["id"]).status_code == 200
    assert _existencia(cliente, a, aceite["id"]) == 5
    assert _existencia(cliente, a, servicio["id"]) is None


def test_la_anulacion_deja_historial_de_estado_motivo_y_existencia(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    aceite = _producto(cliente, a, existencia=10)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 4}])
    _anular(cliente, a, venta["id"], motivo="Error en la cantidad")
    campos = admin_con.execute(
        "SELECT entidad, campo, despues FROM historial WHERE entidad_id IN (%s, %s) ORDER BY id",
        (venta["id"], aceite["id"])).fetchall()
    tuplas = [(c["entidad"], c["campo"]) for c in campos]
    assert ("registro_giro", "estado") in tuplas and ("registro_giro", "anulacion") in tuplas
    assert ("articulo", "existencia") in tuplas
    assert [c["despues"] for c in campos if c["campo"] == "anulacion"] == ["Error en la cantidad"]


def test_anular_sigue_devolviendo_aunque_el_articulo_se_haya_desactivado(cliente, crear_negocio):
    a = crear_negocio()
    aceite = _producto(cliente, a, existencia=10)
    venta = _venta(cliente, a, [{"catalogo_id": aceite["id"], "cantidad": 2}])
    cliente.patch(f"/api/catalogo/{aceite['id']}", json={"activo": False}, headers=a.admin)
    assert _anular(cliente, a, venta["id"]).status_code == 200
    assert _existencia(cliente, a, aceite["id"]) == 10
