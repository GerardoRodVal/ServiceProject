"""Prospectos y embudo en uno: cada visita queda ligada a su contacto y su oportunidad (app/visitas_nucleo.py)."""
from .conftest import visita


def _ops(cliente, n):
    return cliente.get("/api/oportunidades", headers=n.admin).json()["oportunidades"]


def _etapas(cliente, n):
    pipeline = cliente.get("/api/pipelines", headers=n.admin).json()["pipelines"][0]
    return {e["nombre"].lower(): e["id"] for e in pipeline["etapas"]}


def test_una_visita_crea_su_contacto_y_su_oportunidad(cliente, crear_negocio):
    a = crear_negocio()
    assert cliente.post("/api/visitas", json=visita(nombre="Ana Ruiz", tel="55 1234 5678", score=72, cat="alta"),
                        headers=a.capturista).status_code == 200
    contactos = cliente.get("/api/contactos", headers=a.admin).json()["contactos"]
    assert [(c["nombre"], c["telefono"], c["origen"]) for c in contactos] == [("Ana Ruiz", "5512345678", "Visita")]
    ops = _ops(cliente, a)
    assert len(ops) == 1
    assert ops[0]["contacto_id"] == contactos[0]["id"]
    assert ops[0]["etapa_id"] == _etapas(cliente, a)["nuevo"]          # el embudo por defecto se crea solo
    assert ops[0]["datos_giro"]["score"] == 72
    assert ops[0]["datos_giro"]["categoria"] == "alta"
    assert ops[0]["datos_giro"]["propiedad"] == "modelo-encino"


def test_la_misma_persona_es_la_misma_oportunidad_y_toma_la_ultima_visita(cliente, crear_negocio):
    a = crear_negocio()
    cliente.post("/api/visitas", json=visita(tel="5512345678", score=40, creado="2026-11-01T10:00:00+00:00"), headers=a.capturista)
    cliente.post("/api/visitas", json=visita(tel="+52 (55) 1234-5678", score=80, creado="2026-11-03T10:00:00+00:00"),
                 headers=a.capturista)
    assert len(cliente.get("/api/contactos", headers=a.admin).json()["contactos"]) == 1
    ops = _ops(cliente, a)
    assert len(ops) == 1
    assert ops[0]["datos_giro"]["score"] == 80


def test_una_visita_mas_vieja_no_pisa_el_score_de_la_mas_reciente(cliente, crear_negocio):
    a = crear_negocio()
    cliente.post("/api/visitas", json=visita(score=80, creado="2026-11-03T10:00:00+00:00"), headers=a.capturista)
    cliente.post("/api/visitas", json=visita(score=20, creado="2026-10-01T10:00:00+00:00"), headers=a.capturista)
    assert _ops(cliente, a)[0]["datos_giro"]["score"] == 80


def test_sin_telefono_cada_visita_es_un_contacto(cliente, crear_negocio):
    a = crear_negocio()
    cliente.post("/api/visitas", json=visita(tel=""), headers=a.capturista)
    cliente.post("/api/visitas", json=visita(tel=""), headers=a.capturista)
    assert len(cliente.get("/api/contactos", headers=a.admin).json()["contactos"]) == 2


def test_mover_la_oportunidad_cambia_el_estado_de_sus_visitas(cliente, crear_negocio):
    a = crear_negocio()
    v = visita()
    cliente.post("/api/visitas", json=v, headers=a.capturista)
    op = _ops(cliente, a)[0]
    cliente.patch(f"/api/oportunidades/{op['id']}", json={"etapa_id": _etapas(cliente, a)["negociando"]}, headers=a.admin)
    assert cliente.get("/api/visitas", headers=a.admin).json()[0]["estado"] == "negociando"


def test_cambiar_el_estado_de_la_visita_mueve_su_oportunidad(cliente, crear_negocio):
    a = crear_negocio()
    v = visita()
    cliente.post("/api/visitas", json=v, headers=a.capturista)
    cliente.post("/api/visitas/estado", json={"id": v["id"], "estado": "contactado"}, headers=a.admin)
    assert _ops(cliente, a)[0]["etapa_id"] == _etapas(cliente, a)["contactado"]


def test_una_oportunidad_cerrada_no_recibe_visitas_nuevas(cliente, crear_negocio):
    a = crear_negocio()
    cliente.post("/api/visitas", json=visita(), headers=a.capturista)
    op = _ops(cliente, a)[0]
    cliente.patch(f"/api/oportunidades/{op['id']}", json={"etapa_id": _etapas(cliente, a)["cerrado"]}, headers=a.admin)
    cliente.post("/api/visitas", json=visita(), headers=a.capturista)       # misma persona, otro trato
    assert len(_ops(cliente, a)) == 2
    assert len(cliente.get("/api/contactos", headers=a.admin).json()["contactos"]) == 1


def test_una_visita_usa_la_oportunidad_abierta_creada_a_mano(cliente, crear_negocio):
    a = crear_negocio()
    cliente.post("/api/visitas", json=visita(tel="5599990000", nombre="Otra"), headers=a.capturista)   # crea el embudo
    c = cliente.post("/api/contactos", json={"nombre": "Ana", "telefono": "55 1234 5678"}, headers=a.admin).json()["contacto"]
    manual = cliente.post("/api/oportunidades", json={"contacto_id": c["id"], "valor": 2500000}, headers=a.admin).json()["oportunidad"]
    cliente.post("/api/visitas", json=visita(tel="5512345678", score=77), headers=a.capturista)
    ops = [o for o in _ops(cliente, a) if o["contacto_id"] == c["id"]]
    assert [o["id"] for o in ops] == [manual["id"]]
    assert ops[0]["giro"] == "inmobiliarias" and ops[0]["datos_giro"]["score"] == 77
    assert float(ops[0]["valor"]) == 2500000       # el valor escrito a mano no se pisa


def test_el_lote_liga_las_cargas_pero_no_los_ejemplos(cliente, crear_negocio):
    a = crear_negocio()
    lote = {"visitas": [{"visita": visita("imp-carga001", tel="5511111111")},
                        {"visita": visita("ej-ejemplo01", tel="5522222222"), "demo": True}]}
    assert cliente.post("/api/visitas/lote", json=lote, headers=a.admin).json()["insertadas"] == 2
    telefonos = [c["telefono"] for c in cliente.get("/api/contactos", headers=a.admin).json()["contactos"]]
    assert telefonos == ["5511111111"]


def test_ligar_las_visitas_de_antes_se_puede_repetir(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    for i in range(3):
        admin_con.execute(
            "INSERT INTO registros (negocio_id, id, creado, datos) VALUES (%s, %s, now(), %s::jsonb)",
            (a.id, f"viejo-{i:04d}", f'{{"id": "viejo-{i:04d}", "nombre": "Persona {i}", "tel": "551000000{i}", "score": 30}}'))
    assert cliente.post("/api/visitas/ligar", headers=a.admin).json()["ligadas"] == 3
    assert cliente.post("/api/visitas/ligar", headers=a.admin).json()["ligadas"] == 0
    assert len(_ops(cliente, a)) == 3
    assert cliente.post("/api/visitas/ligar", headers=a.capturista).status_code == 403


def test_el_mismo_telefono_en_otro_negocio_no_se_liga(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    cliente.post("/api/visitas", json=visita(tel="5512345678"), headers=a.capturista)
    cliente.post("/api/visitas", json=visita(tel="5512345678"), headers=b.capturista)
    assert len(cliente.get("/api/contactos", headers=a.admin).json()["contactos"]) == 1
    assert len(cliente.get("/api/contactos", headers=b.admin).json()["contactos"]) == 1


def test_una_oportunidad_ligada_se_puede_editar(cliente, crear_negocio):
    a = crear_negocio()
    cliente.post("/api/visitas", json=visita(), headers=a.capturista)
    op = _ops(cliente, a)[0]
    r = cliente.patch(f"/api/oportunidades/{op['id']}", json={"datos_giro": {**op["datos_giro"], "presupuesto": 5000000}},
                      headers=a.admin)
    assert r.status_code == 200, r.text
    assert r.json()["oportunidad"]["datos_giro"]["ultima_visita"]["id"] == op["datos_giro"]["ultima_visita"]["id"]
