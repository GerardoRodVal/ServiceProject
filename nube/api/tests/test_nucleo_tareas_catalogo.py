"""Tareas y catálogo del núcleo del CRM (rutas en app/nucleo.py)."""
from datetime import datetime, timedelta, timezone


def _tarea(cliente, negocio, titulo="Llamar para cita", **extra):
    r = cliente.post("/api/tareas", json={"titulo": titulo, **extra}, headers=negocio.capturista)
    assert r.status_code == 200, r.text
    return r.json()["tarea"]


def _articulo(cliente, negocio, **extra):
    cuerpo = {"tipo": "producto", "nombre": "Aceite 5W-30", "precio": 250, "existencia": 10, "minimo": 3, **extra}
    r = cliente.post("/api/catalogo", json=cuerpo, headers=negocio.admin)
    assert r.status_code == 200, r.text
    return r.json()["articulo"]


# --- Tareas ------------------------------------------------------------------------------------------------------------

def test_la_tarea_queda_a_nombre_de_quien_la_crea_por_omision(cliente, crear_negocio):
    a = crear_negocio()
    t = _tarea(cliente, a)
    assert t["responsable_id"] is not None
    assert t["estado"] == "pendiente"
    assert t["completada_en"] is None


def test_completar_una_tarea_marca_la_fecha_y_reabrirla_la_quita(cliente, crear_negocio):
    a = crear_negocio()
    t = _tarea(cliente, a)
    r = cliente.patch(f"/api/tareas/{t['id']}", json={"estado": "hecha"}, headers=a.capturista)
    assert r.json()["tarea"]["completada_en"] is not None
    r = cliente.patch(f"/api/tareas/{t['id']}", json={"estado": "pendiente"}, headers=a.capturista)
    assert r.json()["tarea"]["completada_en"] is None


def test_las_tareas_vencidas_salen_con_su_filtro(cliente, crear_negocio):
    a = crear_negocio()
    ayer = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    manana = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    vencida = _tarea(cliente, a, "Vencida", vence=ayer)
    _tarea(cliente, a, "Vigente", vence=manana)
    ids = [t["id"] for t in cliente.get("/api/tareas?vencidas=true", headers=a.admin).json()["tareas"]]
    assert ids == [vencida["id"]]


def test_las_tareas_se_filtran_por_contacto_y_estado(cliente, crear_negocio):
    a = crear_negocio()
    c = cliente.post("/api/contactos", json={"nombre": "Ana"}, headers=a.capturista).json()["contacto"]
    ligada = _tarea(cliente, a, "Con contacto", contacto_id=c["id"])
    _tarea(cliente, a, "Suelta")
    assert [t["id"] for t in cliente.get(f"/api/tareas?contacto_id={c['id']}", headers=a.admin).json()["tareas"]] == [ligada["id"]]
    assert cliente.get("/api/tareas?estado=hecha", headers=a.admin).json()["tareas"] == []


def test_una_tarea_no_se_liga_a_un_contacto_ajeno(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    c_b = cliente.post("/api/contactos", json={"nombre": "Ajena"}, headers=b.capturista).json()["contacto"]
    r = cliente.post("/api/tareas", json={"titulo": "Mía", "contacto_id": c_b["id"]}, headers=a.capturista)
    assert r.status_code == 422


def test_cambiar_el_titulo_de_una_tarea_deja_historial(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    t = _tarea(cliente, a)
    cliente.patch(f"/api/tareas/{t['id']}", json={"titulo": "Llamar mañana"}, headers=a.capturista)
    filas = admin_con.execute(
        "SELECT campo, antes, despues FROM historial WHERE entidad = 'tarea' AND entidad_id = %s", (t["id"],)).fetchall()
    assert [(f["campo"], f["antes"], f["despues"]) for f in filas] == [("titulo", "Llamar para cita", "Llamar mañana")]


def test_una_tarea_ajena_no_se_ve_ni_se_cambia(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    t = _tarea(cliente, a)
    assert cliente.patch(f"/api/tareas/{t['id']}", json={"estado": "hecha"}, headers=b.admin).status_code == 404
    assert cliente.get("/api/tareas", headers=b.admin).json()["tareas"] == []


# --- Catálogo ----------------------------------------------------------------------------------------------------------

def test_solo_el_administrador_da_de_alta_articulos(cliente, crear_negocio):
    a = crear_negocio()
    cuerpo = {"tipo": "producto", "nombre": "Filtro", "precio": 100}
    assert cliente.post("/api/catalogo", json=cuerpo, headers=a.capturista).status_code == 403
    assert _articulo(cliente, a)["nombre"] == "Aceite 5W-30"


def test_las_entradas_y_salidas_no_dejan_la_existencia_negativa(cliente, crear_negocio):
    a = crear_negocio()
    art = _articulo(cliente, a, existencia=5)
    url = f"/api/catalogo/{art['id']}/existencia"
    r = cliente.post(url, json={"cambio": -2, "motivo": "Venta"}, headers=a.capturista)
    assert float(r.json()["articulo"]["existencia"]) == 3
    r = cliente.post(url, json={"cambio": -4}, headers=a.capturista)
    assert r.status_code == 422
    assert float(cliente.get("/api/catalogo", headers=a.admin).json()["catalogo"][0]["existencia"]) == 3


def test_un_articulo_sin_inventario_no_admite_movimientos(cliente, crear_negocio):
    a = crear_negocio()
    servicio = _articulo(cliente, a, tipo="servicio", nombre="Balanceo", existencia=None)
    r = cliente.post(f"/api/catalogo/{servicio['id']}/existencia", json={"cambio": 1}, headers=a.capturista)
    assert r.status_code == 422


def test_un_movimiento_de_existencia_deja_historial_con_su_motivo(cliente, crear_negocio, admin_con):
    a = crear_negocio()
    art = _articulo(cliente, a, existencia=5)
    cliente.post(f"/api/catalogo/{art['id']}/existencia", json={"cambio": 2, "motivo": "Compra proveedor"},
                 headers=a.capturista)
    campos = admin_con.execute(
        "SELECT campo, despues FROM historial WHERE entidad = 'articulo' AND entidad_id = %s ORDER BY id",
        (art["id"],)).fetchall()
    assert [f["campo"] for f in campos] == ["existencia", "motivo_existencia"]
    assert campos[1]["despues"] == "Compra proveedor"


def test_el_filtro_de_bajo_minimo_encuentra_lo_que_hay_que_reponer(cliente, crear_negocio):
    a = crear_negocio()
    _articulo(cliente, a, nombre="Abundante", existencia=10, minimo=3)
    justo = _articulo(cliente, a, nombre="Escaso", existencia=2, minimo=3)
    nombres = [x["nombre"] for x in cliente.get("/api/catalogo?bajo_minimo=true", headers=a.admin).json()["catalogo"]]
    assert nombres == [justo["nombre"]]


def test_desactivar_un_articulo_lo_oculta_sin_borrarlo(cliente, crear_negocio):
    a = crear_negocio()
    art = _articulo(cliente, a)
    cliente.patch(f"/api/catalogo/{art['id']}", json={"activo": False}, headers=a.admin)
    assert cliente.get("/api/catalogo", headers=a.admin).json()["catalogo"] == []
    assert len(cliente.get("/api/catalogo?incluir_inactivos=true", headers=a.admin).json()["catalogo"]) == 1


def test_el_catalogo_de_otro_negocio_no_se_ve_ni_se_edita(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    art = _articulo(cliente, a)
    assert cliente.get("/api/catalogo", headers=b.admin).json()["catalogo"] == []
    assert cliente.patch(f"/api/catalogo/{art['id']}", json={"precio": 1}, headers=b.admin).status_code == 404


def test_precio_negativo_o_iva_fuera_de_rango_se_rechaza(cliente, crear_negocio):
    a = crear_negocio()
    assert cliente.post("/api/catalogo", json={"tipo": "producto", "nombre": "X", "precio": -1},
                        headers=a.admin).status_code == 422
    assert cliente.post("/api/catalogo", json={"tipo": "producto", "nombre": "X", "precio": 1, "tasa_iva": 2},
                        headers=a.admin).status_code == 422
