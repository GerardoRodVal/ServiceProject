"""Rutas que usa el gestor de Windows: guardar por lotes, agregar el enlace de Kommo y borrar ejemplos."""
from .conftest import visita


def _item(v, estado="nuevo", demo=False, captura=False):
    return {"visita": v, "estado": estado, "demo": demo, "captura": captura}


def test_el_lote_guarda_estado_y_marca_de_ejemplo(cliente, crear_negocio):
    n = crear_negocio()
    v1, v2 = visita("imp-aaaa0001"), visita("demo-bbbb001")
    r = cliente.post("/api/visitas/lote", headers=n.admin,
                     json={"visitas": [_item(v1, "negociando"), _item(v2, "nuevo", demo=True)]})
    assert r.status_code == 200, r.text
    assert (r.json()["insertadas"], r.json()["actualizadas"], r.json()["omitidas"]) == (2, 0, [])
    por_id = {x["id"]: x for x in cliente.get("/api/visitas", headers=n.admin).json()}
    assert por_id["imp-aaaa0001"]["estado"] == "negociando" and por_id["imp-aaaa0001"]["demo"] is False
    assert por_id["demo-bbbb001"]["demo"] is True


def test_reenviar_un_lote_actualiza_datos_pero_conserva_el_estado(cliente, crear_negocio):
    n = crear_negocio()
    v = visita("imp-aaaa0001", nombre="Antes")
    cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": [_item(v, "negociando")]})
    v["nombre"] = "Después"
    r = cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": [_item(v, "nuevo")]})
    assert (r.json()["insertadas"], r.json()["actualizadas"]) == (0, 1)
    guardada = cliente.get("/api/visitas", headers=n.admin).json()[0]
    assert guardada["nombre"] == "Después" and guardada["estado"] == "negociando"


def test_solo_las_capturas_cuentan_como_uso(cliente, crear_negocio):
    n = crear_negocio()
    lote = [_item(visita("cel-aaaaaa1"), captura=True), _item(visita("imp-aaaa0001")),
            _item(visita("demo-bbbb001"), demo=True, captura=True)]
    cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": lote})
    assert cliente.get("/api/uso", headers=n.admin).json()["registros_mes"] == 1
    cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": [_item(visita("cel-aaaaaa1"), captura=True)]})
    assert cliente.get("/api/uso", headers=n.admin).json()["registros_mes"] == 1     # reenviar no suma


def test_las_visitas_malas_se_omiten_y_las_demas_entran(cliente, crear_negocio):
    n = crear_negocio()
    lote = [_item(visita("bueno-0001")), _item(visita("x")), _item(visita("bueno-0002"), estado="inventado"),
            _item(visita("bueno-0003", presupuesto="mucho")), _item(visita("bueno-0004"))]
    r = cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": lote}).json()
    assert r["insertadas"] == 2
    assert sorted(o["id"] for o in r["omitidas"]) == ["bueno-0002", "bueno-0003", "x"]
    assert sorted(x["id"] for x in cliente.get("/api/visitas", headers=n.admin).json()) == ["bueno-0001", "bueno-0004"]


def test_el_lote_tiene_tope_y_es_solo_para_el_administrador(cliente, crear_negocio):
    n = crear_negocio()
    demasiadas = [_item(visita(f"lote-{i:05d}")) for i in range(201)]
    assert cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": demasiadas}).status_code == 422
    assert cliente.post("/api/visitas/lote", headers=n.capturista, json={"visitas": [_item(visita())]}).status_code == 403
    assert cliente.post("/api/visitas/lote", json={"visitas": []}).status_code == 401


def test_el_lote_acepta_campos_internos_y_el_celular_no(cliente, crear_negocio):
    n = crear_negocio()
    interno = {"asesor_id": "a1b2c3", "origen": "kommo", "kommo_id": 4821, "kommo_url": "https://miempresa.kommo.com/leads/detail/4821"}
    cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": [_item(visita("kommo-4821", **interno))]})
    cliente.post("/api/visitas", headers=n.capturista, json=visita("cel-bbbbbb1", **interno))
    por_id = {x["id"]: x for x in cliente.get("/api/visitas", headers=n.admin).json()}
    assert {k: por_id["kommo-4821"][k] for k in interno} == interno
    # El celular no puede escribir el origen ni el enlace a Kommo; el asesor lo pone la API desde la sesión, no lo que mande.
    assert not any(k in por_id["cel-bbbbbb1"] for k in ("origen", "kommo_id", "kommo_url"))
    assert por_id["cel-bbbbbb1"]["asesor_id"] != "a1b2c3"


def test_campos_internos_invalidos_se_omiten(cliente, crear_negocio):
    n = crear_negocio()
    malos = [visita("malo-000001", kommo_url="https://evil.com/x"), visita("malo-000002", kommo_url="javascript:alert(1)"),
             visita("malo-000003", kommo_id="abc"), visita("malo-000004", asesor_id="con espacios")]
    r = cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": [_item(v) for v in malos]}).json()
    assert r["insertadas"] == 0 and len(r["omitidas"]) == 4


def test_agregar_el_enlace_de_kommo_no_toca_estado_ni_fecha(cliente, crear_negocio):
    n = crear_negocio()
    v = visita("cel-cccccc1")
    cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": [_item(v, "negociando")]})
    antes = cliente.get("/api/visitas", headers=n.admin).json()[0]
    enlace = {"kommo_id": 77, "kommo_url": "https://miempresa.kommo.com/leads/detail/77"}
    assert cliente.patch(f"/api/visitas/{v['id']}/datos", headers=n.admin, json=enlace).status_code == 200
    despues = cliente.get("/api/visitas", headers=n.admin).json()[0]
    assert despues["kommo_id"] == 77 and despues["nombre"] == antes["nombre"]
    assert (despues["estado"], despues["creado"]) == (antes["estado"], antes["creado"])
    assert cliente.patch(f"/api/visitas/{v['id']}/datos", headers=n.admin, json={"kommo_url": "https://evil.com/"}).status_code == 422
    assert cliente.patch(f"/api/visitas/{v['id']}/datos", headers=n.admin, json={"nada": 1}).status_code == 422
    assert cliente.patch("/api/visitas/no-existe-1/datos", headers=n.admin, json=enlace).status_code == 404


def test_no_se_puede_agregar_datos_a_un_registro_ajeno(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    cliente.post("/api/visitas/lote", headers=a.admin, json={"visitas": [_item(visita("cel-dddddd1"))]})
    enlace = {"kommo_id": 1}
    assert cliente.patch("/api/visitas/cel-dddddd1/datos", headers=b.admin, json=enlace).status_code == 404
    assert "kommo_id" not in cliente.get("/api/visitas", headers=a.admin).json()[0]


def test_borrar_ejemplos_solo_quita_los_de_ejemplo_del_propio_negocio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    for n in (a, b):
        cliente.post("/api/visitas/lote", headers=n.admin, json={"visitas": [_item(visita("demo-eeeeee1"), demo=True), _item(visita("real-eeeeee1"))]})
    assert cliente.post("/api/visitas/borrar-ejemplos", headers=a.admin).json()["borrados"] == 1
    assert [x["id"] for x in cliente.get("/api/visitas", headers=a.admin).json()] == ["real-eeeeee1"]
    assert len(cliente.get("/api/visitas", headers=b.admin).json()) == 2
