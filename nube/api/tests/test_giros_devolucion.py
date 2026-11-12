"""Registro de devolución de una venta anulada: solo administrador, una por venta, monto y pago tomados de la venta."""
BASE = "/api/giros/venta/registros"


def _producto(cliente, n, existencia=10, precio=250):
    r = cliente.post("/api/catalogo", json={"tipo": "producto", "nombre": "Aceite", "precio": precio,
                                            "tasa_iva": 0.16, "existencia": existencia}, headers=n.admin)
    return r.json()["articulo"]


def _venta(cliente, n, articulo, pago="tarjeta", cantidad=2):
    r = cliente.post(BASE, json={"tipo": "venta", "datos": {"lineas": [{"catalogo_id": articulo["id"],
                                                                         "cantidad": cantidad}],
                                                            "pago": pago, "recibido": 10000}},
                     headers=n.capturista)
    assert r.status_code == 200, r.text
    return r.json()["registro"]


def _anular(cliente, n, venta_id):
    r = cliente.post(f"{BASE}/{venta_id}/anular", json={"motivo": "Cobro duplicado"}, headers=n.admin)
    assert r.status_code == 200, r.text


def _devolucion(cliente, n, venta_id, headers=None, **extra):
    return cliente.post(BASE, json={"tipo": "devolucion", "datos": {"venta_id": venta_id, **extra}},
                        headers=headers or n.admin)


def test_el_administrador_registra_la_devolucion_con_el_monto_de_la_venta(cliente, crear_negocio):
    a = crear_negocio()
    venta = _venta(cliente, a, _producto(cliente, a), pago="efectivo")
    _anular(cliente, a, venta["id"])
    r = _devolucion(cliente, a, venta["id"], motivo="Devolución en caja")
    assert r.status_code == 200, r.text
    reg = r.json()["registro"]
    assert reg["estado"] == "registrada"
    assert reg["datos"]["monto"] == venta["datos"]["total"]
    assert reg["datos"]["pago"] == "efectivo"
    assert reg["datos"]["motivo"] == "Devolución en caja"


def test_solo_el_administrador_registra_devoluciones(cliente, crear_negocio):
    a = crear_negocio()
    venta = _venta(cliente, a, _producto(cliente, a))
    _anular(cliente, a, venta["id"])
    assert _devolucion(cliente, a, venta["id"], headers=a.capturista).status_code == 403


def test_solo_se_registra_devolucion_de_una_venta_anulada(cliente, crear_negocio):
    a = crear_negocio()
    venta = _venta(cliente, a, _producto(cliente, a))
    assert _devolucion(cliente, a, venta["id"]).status_code == 422


def test_una_venta_tiene_una_sola_devolucion(cliente, crear_negocio):
    a = crear_negocio()
    venta = _venta(cliente, a, _producto(cliente, a))
    _anular(cliente, a, venta["id"])
    assert _devolucion(cliente, a, venta["id"]).status_code == 200
    assert _devolucion(cliente, a, venta["id"]).status_code == 422


def test_el_monto_no_lo_manda_el_cliente(cliente, crear_negocio):
    a = crear_negocio()
    venta = _venta(cliente, a, _producto(cliente, a))
    _anular(cliente, a, venta["id"])
    assert _devolucion(cliente, a, venta["id"], monto=1).status_code == 422


def test_una_devolucion_ajena_o_inexistente_no_se_registra(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    venta_b = _venta(cliente, b, _producto(cliente, b))
    _anular(cliente, b, venta_b["id"])
    assert _devolucion(cliente, a, venta_b["id"]).status_code == 422


def test_una_devolucion_no_se_corrige_ni_nace_en_otro_estado(cliente, crear_negocio):
    a = crear_negocio()
    venta = _venta(cliente, a, _producto(cliente, a))
    _anular(cliente, a, venta["id"])
    reg = _devolucion(cliente, a, venta["id"]).json()["registro"]
    assert cliente.patch(f"{BASE}/{reg['id']}", json={"estado": "registrada"}, headers=a.admin).status_code == 422
    otra = {"tipo": "devolucion", "estado": "pagada", "datos": {"venta_id": venta["id"]}}
    assert cliente.post(BASE, json=otra, headers=a.admin).status_code == 422


def test_la_devolucion_no_mueve_la_existencia_otra_vez(cliente, crear_negocio):
    a = crear_negocio()
    articulo = _producto(cliente, a, existencia=10)
    venta = _venta(cliente, a, articulo, cantidad=3)
    _anular(cliente, a, venta["id"])
    _devolucion(cliente, a, venta["id"])
    existencia = next(x for x in cliente.get("/api/catalogo", headers=a.admin).json()["catalogo"]
                      if x["id"] == articulo["id"])["existencia"]
    assert float(existencia) == 10
