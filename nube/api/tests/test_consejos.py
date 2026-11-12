"""Consejos del celular: se calculan con las visitas de quien inició sesión y con los pesos de su negocio."""
from app.consejos import construir

from .conftest import visita


def capturar(cliente, cabecera, id_, nombre, cat, score, credito="tramite"):
    # Cada persona con su propio teléfono: con el mismo, la API las trata como la misma persona (visitas_nucleo.py).
    tel = "55" + str(abs(hash(nombre)) % 10**8).zfill(8)
    r = cliente.post("/api/visitas", json=visita(id_, nombre=nombre, cat=cat, score=score, credito=credito, tel=tel),
                     headers=cabecera)
    assert r.status_code == 200, r.text


def textos(cliente, cabecera):
    r = cliente.get("/api/app/consejos", headers=cabecera)
    assert r.status_code == 200, r.text
    return r.json()["consejos"]


# --- La función pura (mismos textos que el gestor de Windows) ---

def test_sin_visitas_salen_los_seis_consejos_generales():
    lista = construir(0, "", 0, {})
    assert len(lista) == 6 and lista[0]["tag"] == "Prospecto alto" and not any(c.get("urgente") for c in lista)
    assert "desde 70 puntos" in lista[0]["texto"] and "+30" in lista[0]["texto"] and "+20" in lista[0]["texto"]


def test_singular_plural_y_orden():
    uno = construir(1, "Ana", 1, {})
    assert uno[0] == {"urgente": True, "tag": "Misión del día",
                      "texto": "Tienes 1 prospecto de prioridad alta sin contactar. Llama primero a Ana: es quien más puntos tiene."}
    assert uno[1]["tag"] == "Sube de nivel" and uno[1]["texto"].startswith("1 prospecto de prioridad media no tiene crédito.")
    varios = construir(3, "", 2, {})
    assert "Tienes 3 prospectos de prioridad alta" in varios[0]["texto"] and "Llama primero a quien más puntos tiene" in varios[0]["texto"]
    assert varios[1]["texto"].startswith("2 prospectos de prioridad media no tienen crédito.")
    assert len(varios) == 6 and len(uno) == 6                                              # nunca más de seis


def test_los_pesos_del_negocio_se_reflejan_y_los_que_faltan_usan_los_de_siempre():
    lista = construir(0, "", 0, {"umbral_alta": 80, "credito_preaprobado": 25})
    assert "desde 80 puntos" in lista[0]["texto"] and "(+25)" in lista[0]["texto"] and "(+30)" in lista[0]["texto"]
    assert "con 20% o más suma +15 puntos" in lista[1]["texto"]


# --- El endpoint ---

def test_los_consejos_usan_las_visitas_de_quien_inicio_sesion(cliente, crear_negocio):
    n = crear_negocio()
    c = n.capturista
    assert textos(cliente, c)[0]["tag"] == "Prospecto alto"                                  # sin visitas: solo generales
    capturar(cliente, c, "alta-0000001", "Ana", "alta", 90)
    capturar(cliente, c, "alta-0000002", "Beto", "alta", 80)
    capturar(cliente, c, "media-000001", "Carla", "media", 50, credito="no")
    capturar(cliente, c, "media-000002", "Dani", "media", 50, credito="preaprobado")        # con crédito: no cuenta
    lista = textos(cliente, c)
    assert lista[0]["urgente"] is True and "Tienes 2 prospectos de prioridad alta" in lista[0]["texto"]
    assert "Llama primero a Ana:" in lista[0]["texto"]                                       # el de más puntos
    assert lista[1]["texto"].startswith("1 prospecto de prioridad media no tiene crédito.")
    assert len(lista) == 6
    # Los consejos de otra persona del mismo negocio no cambian con las visitas de esta.
    assert not any(c.get("urgente") for c in textos(cliente, n.admin))


def test_un_prospecto_contactado_deja_de_ser_mision_del_dia(cliente, crear_negocio):
    n = crear_negocio()
    capturar(cliente, n.capturista, "alta-0000001", "Ana", "alta", 90)
    capturar(cliente, n.capturista, "alta-0000002", "Beto", "alta", 80)
    cliente.post("/api/visitas/estado", json={"id": "alta-0000001", "estado": "contactado"}, headers=n.admin)
    lista = textos(cliente, n.capturista)
    assert "Tienes 1 prospecto de prioridad alta" in lista[0]["texto"] and "Llama primero a Beto:" in lista[0]["texto"]
    cliente.post("/api/visitas/estado", json={"id": "alta-0000002", "estado": "negociando"}, headers=n.admin)
    assert not any(c.get("urgente") for c in textos(cliente, n.capturista))


def test_los_consejos_siguen_los_pesos_publicados_por_el_negocio(cliente, crear_negocio):
    n = crear_negocio()
    cliente.put("/api/config", json={"score": {"umbral_alta": 85, "credito_preaprobado": 22}}, headers=n.admin)
    texto = textos(cliente, n.capturista)[0]["texto"]
    assert "desde 85 puntos" in texto and "(+22)" in texto


def test_los_consejos_exigen_sesion_y_no_mezclan_negocios(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    assert cliente.get("/api/app/consejos").status_code == 401
    capturar(cliente, a.capturista, "alta-0000001", "Ana", "alta", 90)
    assert not any(c.get("urgente") for c in textos(cliente, b.capturista))
    assert not any(c.get("urgente") for c in textos(cliente, b.admin))
