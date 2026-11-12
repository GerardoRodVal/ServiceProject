"""Giros sobre el núcleo: registros de giro, visitas y el giro inmobiliarias (reglas en app/giros.py)."""

PIPELINE = {"nombre": "Ventas", "giro": "inmobiliarias", "por_defecto": True,
            "etapas": [{"nombre": "nuevo", "tipo": "abierta"}, {"nombre": "cerrado", "tipo": "ganada"}]}
BASE = "/api/giros/inmobiliarias/registros"


def _pipeline(cliente, n):
    assert cliente.post("/api/pipelines", json=PIPELINE, headers=n.admin).status_code == 200


def _contacto(cliente, n, nombre="Ana Ruiz"):
    return cliente.post("/api/contactos", json={"nombre": nombre}, headers=n.capturista).json()["contacto"]


def _zona(cliente, n, nombre="Lomas", tipo="colonia"):
    r = cliente.post(BASE, json={"tipo": "zona", "datos": {"nombre": nombre, "tipo_zona": tipo}}, headers=n.capturista)
    assert r.status_code == 200, r.text
    return r.json()["registro"]


def _propiedad(cliente, n, precio=3000000, **extra):
    datos = {"titulo": "Casa Encino", "tipo_inmueble": "casa", "precio": precio, **extra}
    r = cliente.post(BASE, json={"tipo": "propiedad", "datos": datos}, headers=n.capturista)
    assert r.status_code == 200, r.text
    return r.json()["registro"]


def _oportunidad(cliente, n, datos, giro="inmobiliarias"):
    contacto = _contacto(cliente, n)
    r = cliente.post("/api/oportunidades", json={"contacto_id": contacto["id"], "giro": giro, "datos_giro": datos},
                     headers=n.capturista)
    assert r.status_code == 200, r.text
    return r.json()["oportunidad"]


def test_los_giros_disponibles_y_sus_tipos(cliente, crear_negocio):
    a = crear_negocio()
    giros = cliente.get("/api/giros", headers=a.admin).json()["giros"]
    assert {"giro": "inmobiliarias", "tipos": ["propiedad", "zona"]} in giros


def test_un_giro_no_registrado_no_existe(cliente, crear_negocio):
    a = crear_negocio()
    assert cliente.get("/api/giros/panaderia/registros", headers=a.admin).status_code == 404


def test_una_propiedad_exige_sus_datos_y_no_acepta_campos_extra(cliente, crear_negocio):
    a = crear_negocio()
    sin_precio = {"tipo": "propiedad", "datos": {"titulo": "Casa", "tipo_inmueble": "casa"}}
    assert cliente.post(BASE, json=sin_precio, headers=a.capturista).status_code == 422
    tipo_malo = {"tipo": "propiedad", "datos": {"titulo": "Casa", "tipo_inmueble": "castillo", "precio": 1}}
    assert cliente.post(BASE, json=tipo_malo, headers=a.capturista).status_code == 422
    extra = {"tipo": "propiedad", "datos": {"titulo": "Casa", "tipo_inmueble": "casa", "precio": 1, "piscina": True}}
    assert cliente.post(BASE, json=extra, headers=a.capturista).status_code == 422


def test_la_zona_de_una_propiedad_debe_ser_del_mismo_negocio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    zona_b = _zona(cliente, b)
    r = cliente.post(BASE, json={"tipo": "propiedad", "datos": {"titulo": "Casa", "tipo_inmueble": "casa",
                                                                "precio": 1, "zona_id": zona_b["id"]}},
                     headers=a.capturista)
    assert r.status_code == 422
    zona_a = _zona(cliente, a)
    assert _propiedad(cliente, a, zona_id=zona_a["id"])["datos"]["zona_id"] == zona_a["id"]


def test_el_estado_de_la_propiedad_y_de_la_zona(cliente, crear_negocio):
    a = crear_negocio()
    prop = _propiedad(cliente, a)
    assert prop["estado"] == "disponible"
    r = cliente.patch(f"{BASE}/{prop['id']}", json={"estado": "vendido"}, headers=a.capturista)
    assert r.json()["registro"]["estado"] == "vendido"
    assert cliente.patch(f"{BASE}/{prop['id']}", json={"estado": "raro"}, headers=a.capturista).status_code == 422
    zona_con_estado = {"tipo": "zona", "datos": {"nombre": "Centro", "tipo_zona": "colonia"}, "estado": "disponible"}
    assert cliente.post(BASE, json=zona_con_estado, headers=a.capturista).status_code == 422


def test_el_puntaje_de_compra_suma_los_cinco_criterios(cliente, crear_negocio):
    a = crear_negocio()
    _pipeline(cliente, a)
    prop = _propiedad(cliente, a, precio=3000000)
    op = _oportunidad(cliente, a, {"propiedad_id": prop["id"], "presupuesto": 3000000, "credito": "preaprobado",
                                   "enganche": 20, "plazo": "0", "decide": "solo"})
    assert op["datos_giro"]["score"] == 100
    assert op["datos_giro"]["categoria"] == "alta"
    assert len(op["datos_giro"]["razones"]) == 5


def test_el_puntaje_se_recalcula_al_cambiar_los_datos_y_queda_historial(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    _pipeline(cliente, a)
    prop = _propiedad(cliente, a, precio=3000000)
    op = _oportunidad(cliente, a, {"propiedad_id": prop["id"], "presupuesto": 3000000, "credito": "preaprobado",
                                   "enganche": 20, "plazo": "0", "decide": "solo"})
    r = cliente.patch(f"/api/oportunidades/{op['id']}",
                      json={"datos_giro": {"propiedad_id": prop["id"], "presupuesto": 2000000, "credito": "no",
                                           "enganche": 20, "plazo": "0", "decide": "solo"}}, headers=a.capturista)
    assert r.json()["oportunidad"]["datos_giro"]["score"] == 50
    assert r.json()["oportunidad"]["datos_giro"]["categoria"] == "media"
    filas = admin_con.execute(
        "SELECT campo FROM historial WHERE entidad = 'oportunidad' AND entidad_id = %s", (op["id"],)).fetchall()
    assert [f["campo"] for f in filas] == ["datos_giro"]


def test_el_cliente_no_puede_fijar_el_puntaje(cliente, crear_negocio):
    a = crear_negocio()
    _pipeline(cliente, a)
    op = _oportunidad(cliente, a, {"score": 999, "categoria": "alta", "razones": [], "enganche": 0})
    assert op["datos_giro"]["score"] == 0
    assert op["datos_giro"]["categoria"] == "baja"


def test_la_propiedad_de_una_oportunidad_debe_ser_del_negocio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    _pipeline(cliente, a)
    prop_b = _propiedad(cliente, b)
    contacto = _contacto(cliente, a)
    r = cliente.post("/api/oportunidades", json={"contacto_id": contacto["id"], "giro": "inmobiliarias",
                                                 "datos_giro": {"propiedad_id": prop_b["id"]}}, headers=a.capturista)
    assert r.status_code == 422


def test_datos_de_un_giro_con_campos_desconocidos_se_rechazan(cliente, crear_negocio):
    a = crear_negocio()
    _pipeline(cliente, a)
    contacto = _contacto(cliente, a)
    r = cliente.post("/api/oportunidades", json={"contacto_id": contacto["id"], "giro": "inmobiliarias",
                                                 "datos_giro": {"mascota": "perro"}}, headers=a.capturista)
    assert r.status_code == 422


def test_una_oportunidad_de_un_giro_sin_reglas_guarda_lo_que_llega(cliente, crear_negocio):
    a = crear_negocio()
    _pipeline(cliente, a)
    op = _oportunidad(cliente, a, {"lo_que_sea": 1}, giro="")
    assert op["datos_giro"] == {"lo_que_sea": 1}


def test_una_visita_liga_la_propiedad_y_aparece_en_la_ficha(cliente, crear_negocio):
    a = crear_negocio()
    prop = _propiedad(cliente, a)
    contacto = _contacto(cliente, a)
    r = cliente.post("/api/giros/inmobiliarias/visitas",
                     json={"propiedad_id": prop["id"], "contacto_id": contacto["id"], "texto": "Le gustó la cocina"},
                     headers=a.capturista)
    assert r.status_code == 200, r.text
    assert r.json()["visita"]["tipo"] == "visita"
    ficha = cliente.get(f"/api/contactos/{contacto['id']}", headers=a.admin).json()
    assert [x["tipo"] for x in ficha["actividades"]] == ["visita"]
    visitas = cliente.get(f"/api/giros/inmobiliarias/visitas?propiedad_id={prop['id']}", headers=a.admin).json()
    assert len(visitas["visitas"]) == 1


def test_una_visita_necesita_contacto_u_oportunidad_y_una_propiedad_propia(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    prop = _propiedad(cliente, a)
    prop_b = _propiedad(cliente, b)
    assert cliente.post("/api/giros/inmobiliarias/visitas", json={"propiedad_id": prop["id"]},
                        headers=a.capturista).status_code == 422
    contacto = _contacto(cliente, a)
    assert cliente.post("/api/giros/inmobiliarias/visitas",
                        json={"propiedad_id": prop_b["id"], "contacto_id": contacto["id"]},
                        headers=a.capturista).status_code == 422


def test_los_registros_de_un_negocio_no_se_ven_en_otro(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    prop = _propiedad(cliente, a)
    assert cliente.get(f"{BASE}/{prop['id']}", headers=b.admin).status_code == 404
    assert cliente.get(BASE, headers=b.admin).json()["registros"] == []
