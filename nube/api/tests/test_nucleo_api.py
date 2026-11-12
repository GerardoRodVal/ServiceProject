"""API del núcleo del CRM: contactos, pipelines, oportunidades y actividades (rutas en app/nucleo.py)."""

PIPELINE = {"nombre": "Ventas", "giro": "inmobiliarias", "por_defecto": True,
            "etapas": [{"nombre": "nuevo", "tipo": "abierta"}, {"nombre": "negociando", "tipo": "abierta"},
                       {"nombre": "cerrado", "tipo": "ganada"}, {"nombre": "perdido", "tipo": "perdida"}]}


def _pipeline(cliente, negocio, cuerpo=None):
    r = cliente.post("/api/pipelines", json=cuerpo or PIPELINE, headers=negocio.admin)
    assert r.status_code == 200, r.text
    return r.json()["pipeline"]


def _contacto(cliente, negocio, nombre="Ana Ruiz", **extra):
    r = cliente.post("/api/contactos", json={"nombre": nombre, "telefono": "5512345678", **extra},
                     headers=negocio.capturista)
    assert r.status_code == 200, r.text
    return r.json()["contacto"]


def test_crear_y_buscar_contactos_del_propio_negocio(cliente, crear_negocio):
    a = crear_negocio()
    c = _contacto(cliente, a, "Laura Méndez")
    assert c["responsable_id"] is not None          # por omisión, quien lo captura
    assert [x["id"] for x in cliente.get("/api/contactos?q=méndez", headers=a.admin).json()["contactos"]] == [c["id"]]
    assert cliente.get("/api/contactos?q=5512345678", headers=a.admin).json()["contactos"][0]["id"] == c["id"]


def test_un_contacto_ajeno_no_se_ve_ni_se_edita(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    c = _contacto(cliente, a)
    assert cliente.get(f"/api/contactos/{c['id']}", headers=b.admin).status_code == 404
    assert cliente.patch(f"/api/contactos/{c['id']}", json={"nombre": "Otro"}, headers=b.admin).status_code == 404
    assert cliente.get("/api/contactos", headers=b.admin).json()["contactos"] == []


def test_editar_contacto_deja_historial_por_campo(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    c = _contacto(cliente, a)
    r = cliente.patch(f"/api/contactos/{c['id']}", json={"telefono": "5599990000", "correo": "ana@x.com"},
                      headers=a.capturista)
    assert r.status_code == 200
    filas = admin_con.execute(
        "SELECT campo, antes, despues FROM historial WHERE entidad = 'contacto' AND entidad_id = %s ORDER BY campo",
        (c["id"],)).fetchall()
    assert [(f["campo"], f["antes"], f["despues"]) for f in filas] == [
        ("correo", "", "ana@x.com"), ("telefono", "5512345678", "5599990000")]


def test_no_se_puede_dejar_vacio_un_campo_obligatorio(cliente, crear_negocio):
    a = crear_negocio()
    c = _contacto(cliente, a)
    assert cliente.patch(f"/api/contactos/{c['id']}", json={"nombre": None}, headers=a.admin).status_code == 422
    assert cliente.patch(f"/api/contactos/{c['id']}", json={"nombre": "   "}, headers=a.admin).status_code == 422


def test_solo_el_administrador_crea_pipelines(cliente, crear_negocio):
    a = crear_negocio()
    assert cliente.post("/api/pipelines", json=PIPELINE, headers=a.capturista).status_code == 403
    p = _pipeline(cliente, a)
    assert [e["nombre"] for e in p["etapas"]] == ["nuevo", "negociando", "cerrado", "perdido"]


def test_solo_un_pipeline_por_defecto_y_se_pasa_al_nuevo(cliente, crear_negocio):
    a = crear_negocio()
    _pipeline(cliente, a)
    _pipeline(cliente, a, {**PIPELINE, "nombre": "Renta", "por_defecto": True})
    defectos = [p for p in cliente.get("/api/pipelines", headers=a.admin).json()["pipelines"] if p["por_defecto"]]
    assert [p["nombre"] for p in defectos] == ["Renta"]


def test_un_pipeline_necesita_una_etapa_abierta(cliente, crear_negocio):
    a = crear_negocio()
    cerrado = {**PIPELINE, "etapas": [{"nombre": "ganado", "tipo": "ganada"}, {"nombre": "perdido", "tipo": "perdida"}]}
    assert cliente.post("/api/pipelines", json=cerrado, headers=a.admin).status_code == 422


def test_oportunidad_usa_el_pipeline_por_defecto_y_su_etapa_inicial(cliente, crear_negocio):
    a = crear_negocio()
    p = _pipeline(cliente, a)
    c = _contacto(cliente, a)
    r = cliente.post("/api/oportunidades", json={"contacto_id": c["id"], "valor": 2500000, "giro": "inmobiliarias",
                                                 "datos_giro": {"presupuesto": 3000000}}, headers=a.capturista)
    assert r.status_code == 200, r.text
    op = r.json()["oportunidad"]
    assert op["pipeline_id"] == p["id"]
    assert op["etapa_id"] == p["etapas"][0]["id"]
    assert op["cerrado_en"] is None
    # El giro inmobiliarias valida sus datos y agrega el puntaje (ver test_giros.py).
    assert op["datos_giro"]["presupuesto"] == 3000000
    assert "score" in op["datos_giro"]


def test_oportunidad_necesita_contacto_y_pipeline_del_mismo_negocio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    _pipeline(cliente, a)
    contacto_b = _contacto(cliente, b)
    r = cliente.post("/api/oportunidades", json={"contacto_id": contacto_b["id"]}, headers=a.capturista)
    assert r.status_code == 422
    p_b = _pipeline(cliente, b)
    c_a = _contacto(cliente, a)
    r = cliente.post("/api/oportunidades", json={"contacto_id": c_a["id"], "pipeline_id": p_b["id"]}, headers=a.capturista)
    assert r.status_code == 422


def test_la_etapa_debe_ser_del_pipeline_de_la_oportunidad(cliente, crear_negocio):
    a = crear_negocio()
    p1 = _pipeline(cliente, a)
    p2 = _pipeline(cliente, a, {**PIPELINE, "nombre": "Renta", "por_defecto": False})
    c = _contacto(cliente, a)
    r = cliente.post("/api/oportunidades", json={"contacto_id": c["id"], "pipeline_id": p1["id"],
                                                 "etapa_id": p2["etapas"][1]["id"]}, headers=a.capturista)
    assert r.status_code == 422


def test_cerrar_una_oportunidad_marca_la_fecha_y_reabrirla_la_quita(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    p = _pipeline(cliente, a)
    c = _contacto(cliente, a)
    op = cliente.post("/api/oportunidades", json={"contacto_id": c["id"]}, headers=a.capturista).json()["oportunidad"]
    ganada, inicial = p["etapas"][2]["id"], p["etapas"][0]["id"]

    r = cliente.patch(f"/api/oportunidades/{op['id']}", json={"etapa_id": ganada}, headers=a.capturista)
    assert r.status_code == 200 and r.json()["oportunidad"]["cerrado_en"] is not None

    r = cliente.patch(f"/api/oportunidades/{op['id']}", json={"etapa_id": inicial}, headers=a.capturista)
    assert r.json()["oportunidad"]["cerrado_en"] is None

    ficha = cliente.get(f"/api/oportunidades/{op['id']}", headers=a.admin).json()
    cambios_de_etapa = [h for h in ficha["historial"] if h["campo"] == "etapa_id"]
    assert len(cambios_de_etapa) == 2
    assert all(h["usuario_id"] is not None for h in cambios_de_etapa)


def test_oportunidad_con_valor_negativo_o_datos_de_giro_enormes_se_rechaza(cliente, crear_negocio):
    a = crear_negocio()
    _pipeline(cliente, a)
    c = _contacto(cliente, a)
    assert cliente.post("/api/oportunidades", json={"contacto_id": c["id"], "valor": -1}, headers=a.admin).status_code == 422
    grande = {"texto": "x" * 20000}
    assert cliente.post("/api/oportunidades", json={"contacto_id": c["id"], "datos_giro": grande},
                        headers=a.admin).status_code == 422


def test_la_ficha_del_contacto_trae_sus_oportunidades_y_actividades(cliente, crear_negocio):
    a = crear_negocio()
    _pipeline(cliente, a)
    c = _contacto(cliente, a)
    cliente.post("/api/oportunidades", json={"contacto_id": c["id"]}, headers=a.capturista)
    cliente.post("/api/actividades", json={"tipo": "llamada", "contacto_id": c["id"], "texto": "Pidió cita"},
                 headers=a.capturista)
    ficha = cliente.get(f"/api/contactos/{c['id']}", headers=a.admin).json()
    assert len(ficha["oportunidades"]) == 1
    assert [x["texto"] for x in ficha["actividades"]] == ["Pidió cita"]


def test_registrar_actividad_y_no_ligarla_a_lo_ajeno(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    c_b = _contacto(cliente, b)
    r = cliente.post("/api/actividades", json={"tipo": "nota", "contacto_id": c_b["id"], "texto": "Hola"},
                     headers=a.capturista)
    assert r.status_code == 422
    c_a = _contacto(cliente, a)
    r = cliente.post("/api/actividades", json={"tipo": "nota", "contacto_id": c_a["id"], "texto": "Hola"},
                     headers=a.capturista)
    assert r.status_code == 200
    assert r.json()["actividad"]["responsable_id"] is not None


def test_el_responsable_debe_ser_de_tu_negocio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    id_b = cliente.get("/api/usuarios", headers=b.admin).json()["usuarios"][0]["id"]
    r = cliente.post("/api/contactos", json={"nombre": "Pedro", "responsable_id": id_b}, headers=a.capturista)
    assert r.status_code == 422
