"""Comportamiento de la API: duplicados, permisos, estados, archivos y uso."""
from .conftest import PNG, visita


def test_reenviar_una_visita_no_la_duplica_ni_pierde_su_estado(cliente, crear_negocio):
    n = crear_negocio()
    v = visita()
    assert cliente.post("/api/visitas", json=v, headers=n.capturista).json()["duplicado"] is False
    cliente.post("/api/visitas/estado", json={"id": v["id"], "estado": "negociando"}, headers=n.admin)
    v["notas"] = "Reintento con una nota nueva"
    r = cliente.post("/api/visitas", json=v, headers=n.capturista)
    assert r.json()["duplicado"] is True
    lista = cliente.get("/api/visitas", headers=n.admin).json()
    assert len(lista) == 1
    assert lista[0]["estado"] == "negociando" and lista[0]["notas"] == "Reintento con una nota nueva"
    assert cliente.get("/api/uso", headers=n.admin).json()["registros_mes"] == 1


def test_el_capturista_captura_pero_no_lee_ni_cambia_estados(cliente, crear_negocio):
    n = crear_negocio()
    v = visita()
    assert cliente.post("/api/visitas", json=v, headers=n.capturista).status_code == 200
    assert cliente.get("/api/visitas", headers=n.capturista).status_code == 403
    assert cliente.post("/api/visitas/estado", json={"id": v["id"], "estado": "cerrado"}, headers=n.capturista).status_code == 403
    assert cliente.get("/api/uso", headers=n.capturista).status_code == 403


def test_sin_sesion_o_con_token_malo_no_entra(cliente, crear_negocio):
    n = crear_negocio()
    assert cliente.get("/api/visitas").status_code == 401
    assert cliente.get("/api/visitas", headers={"Authorization": "Bearer basura"}).status_code == 401
    assert cliente.post("/dev/token", json={"correo": "nadie@nada.com"}).status_code == 404
    token_ajeno = n.admin["Authorization"][:-3] + "abc"        # firma alterada
    assert cliente.get("/api/visitas", headers={"Authorization": token_ajeno}).status_code == 401


def test_un_usuario_inactivo_no_entra(cliente, crear_negocio, admin_con):
    n = crear_negocio()
    assert cliente.get("/api/visitas", headers=n.admin).status_code == 200
    admin_con.execute("UPDATE usuarios SET activo = false WHERE negocio_id = %s", (n.id,))
    assert cliente.get("/api/visitas", headers=n.admin).status_code == 403


def test_el_cambio_de_estado_queda_en_el_historial(cliente, crear_negocio, admin_con):
    n = crear_negocio()
    v = visita()
    cliente.post("/api/visitas", json=v, headers=n.capturista)
    assert cliente.post("/api/visitas/estado", json={"id": v["id"], "estado": "contactado"}, headers=n.admin).status_code == 200
    assert cliente.post("/api/visitas/estado", json={"id": v["id"], "estado": "contactado"}, headers=n.admin).status_code == 200  # sin cambio
    assert cliente.post("/api/visitas/estado", json={"id": v["id"], "estado": "inventado"}, headers=n.admin).status_code == 422
    filas = admin_con.execute("SELECT estado_anterior, estado_nuevo FROM historial_estados WHERE negocio_id = %s", (n.id,)).fetchall()
    assert filas == [{"estado_anterior": "nuevo", "estado_nuevo": "contactado"}]


def test_visitas_invalidas_se_rechazan(cliente, crear_negocio):
    n = crear_negocio()
    assert cliente.post("/api/visitas", json=visita("corto"), headers=n.capturista).status_code == 422
    assert cliente.post("/api/visitas", json=visita(presupuesto="mucho"), headers=n.capturista).status_code == 422
    assert cliente.post("/api/visitas", json={"id": "v-123456", "nombre": "x" * 5000, "relleno": "y" * 70000}, headers=n.capturista).status_code == 413


def test_la_visita_se_limpia_antes_de_guardarse(cliente, crear_negocio):
    n = crear_negocio()
    v = visita(nombre="N" * 500, campo_raro="no debe guardarse", extras={"valido": "sí", "Mal Nombre": "no", "x" * 50: "no"})
    cliente.post("/api/visitas", json=v, headers=n.capturista)
    guardada = cliente.get("/api/visitas", headers=n.admin).json()[0]
    assert len(guardada["nombre"]) == 80
    assert "campo_raro" not in guardada
    assert guardada["extras"] == {"valido": "sí"}


def test_archivos_solo_imagenes_y_con_tope_de_tamano(cliente, crear_negocio):
    n = crear_negocio()
    assert cliente.post("/api/archivos", files={"archivo": ("a.png", b"esto no es una imagen", "image/png")}, headers=n.capturista).status_code == 415
    assert cliente.post("/api/archivos", files={"archivo": ("a.png", PNG + b"\x00" * (5 * 1024 * 1024), "image/png")}, headers=n.capturista).status_code == 413
    ok = cliente.post("/api/archivos", files={"archivo": ("a.png", PNG, "text/plain")}, headers=n.capturista)   # el tipo se detecta por el contenido
    assert ok.status_code == 200 and ok.json()["tipo"] == "image/png"
    descarga = cliente.get(f"/api/archivos/{ok.json()['id']}", headers=n.admin)
    assert descarga.headers["content-type"] == "image/png" and descarga.headers["x-content-type-options"] == "nosniff"


def test_el_uso_cuenta_registros_y_archivos(cliente, crear_negocio):
    n = crear_negocio()
    for _ in range(3):
        cliente.post("/api/visitas", json=visita(), headers=n.capturista)
    cliente.post("/api/archivos", files={"archivo": ("a.png", PNG, "image/png")}, headers=n.capturista)
    uso = cliente.get("/api/uso", headers=n.admin).json()
    assert uso["registros_mes"] == 3 and uso["registros_total"] == 3 and uso["usuarios_activos"] == 2
    assert uso["archivos_mb_mes"] == round(len(PNG) / 1024 / 1024, 2)
    assert uso["limites"] == {"registros_mes": None, "archivos_mb_mes": None}   # límites por definir


def test_al_excederse_el_limite_se_avisa_pero_no_se_bloquea(cliente, crear_negocio):
    n = crear_negocio()
    cfg = cliente.app.state.cfg
    cliente.app.state.cfg = type(cfg)(**{**cfg.__dict__, "limite_registros_mes": 5})
    try:
        avisos = [cliente.post("/api/visitas", json=visita(), headers=n.capturista).json()["aviso"] for _ in range(7)]
    finally:
        cliente.app.state.cfg = cfg
    assert avisos == [None, None, None, "uso_80", "uso_80", "excedido", "excedido"]
    assert len(cliente.get("/api/visitas", headers=n.admin).json()) == 7   # ninguna se perdió
