"""WhatsApp con IA: webhook de Meta (verificación, firma, mensajes repetidos), respuesta de la IA, complemento,
pausa, asesor y aislamiento entre negocios. La IA y la Cloud API de Meta se sustituyen: no sale nada a internet."""
import hashlib
import hmac
import json
import uuid

import pytest

from app import whatsapp

SECRETO = "0123456789abcdef0123456789abcdef"
TOKEN_META = "EAAB" + "x" * 60
VERIFY = "verificacion-de-prueba-123"
PNID = "106540352242922"


def _cuenta(**extra):
    base = {"phone_number_id": PNID, "waba_id": "102290129340398", "version": "v23.0", "verify_token": VERIFY,
            "token": TOKEN_META, "app_secret": SECRETO, "ia_activa": True,
            "ia": {"nombre": "Sofía", "tono": "amable", "bienvenida": "¡Hola! Soy Sofía.", "instrucciones": ""}}
    base.update(extra)
    return base


def _aviso(texto="Hola, ¿qué casas tienen?", wa_id="5215512345678", wamid=None, pnid=PNID, nombre="Ana"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "524421234567", "phone_number_id": pnid},
        "contacts": [{"profile": {"name": nombre}, "wa_id": wa_id}],
        "messages": [{"from": wa_id, "id": wamid or f"wamid.{uuid.uuid4().hex}", "timestamp": "1700000000",
                      "type": "text", "text": {"body": texto}}]}}]}]}


def _firmar(cuerpo, secreto=SECRETO):
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _publicar(cliente, ruta, aviso, secreto=SECRETO):
    cuerpo = json.dumps(aviso).encode()
    return cliente.post(ruta, content=cuerpo, headers={"Content-Type": "application/json",
                                                       "X-Hub-Signature-256": _firmar(cuerpo, secreto)})


@pytest.fixture
def falsos(monkeypatch):
    """Sustituye a la IA y a Meta. Registra lo que se habría enviado."""
    estado = {"enviados": [], "pedidos": [], "respuesta": "Tenemos el Modelo Encino en $2,000,000 MXN."}

    def responder(cfg, sistema, mensajes):
        estado["pedidos"].append({"sistema": sistema, "mensajes": mensajes})
        return estado["respuesta"]

    def enviar(cfg, version, pnid, token, wa_id, texto):
        estado["enviados"].append({"pnid": pnid, "token": token, "wa_id": wa_id, "texto": texto})
        return f"wamid.salida.{uuid.uuid4().hex}"

    monkeypatch.setattr(whatsapp.ia, "responder", responder)
    monkeypatch.setattr(whatsapp, "enviar_texto", enviar)
    return estado


@pytest.fixture
def conectado(cliente, crear_negocio, admin_con):
    """Un negocio con el complemento, su cuenta de WhatsApp guardada y un catálogo publicado."""
    n = crear_negocio()
    admin_con.execute("UPDATE negocios SET whatsapp_ia = true WHERE id = %s", (n.id,))
    r = cliente.put("/api/config", headers=n.admin, json={
        "negocio": {"nombre": "Casas Sol"}, "zonas": [{"id": "z1", "nombre": "Juriquilla", "ciudad": "Querétaro"}],
        "propiedades": [{"id": "modelo-encino", "nombre": "Modelo Encino", "tipo": "Casa", "precio": 2000000,
                         "zona": "z1", "recamaras": 3}]})
    assert r.status_code == 200, r.text
    r = cliente.put("/api/whatsapp/cuenta", headers=n.admin, json=_cuenta())
    assert r.status_code == 200, r.text
    n.whatsapp = r.json()
    return n


def test_la_cuenta_nunca_devuelve_los_secretos(cliente, conectado, admin_con):
    c = conectado.whatsapp
    assert c["tiene_token"] and c["tiene_secreto"] and c["complemento"]
    assert "token" not in c and "app_secret" not in c
    assert TOKEN_META not in json.dumps(c) and SECRETO not in json.dumps(c)
    assert c["webhook"].startswith("/api/whatsapp/") and c["webhook"].endswith("/webhook")
    fila = admin_con.execute("SELECT token_cifrado, app_secret_cifrado FROM whatsapp_cuentas WHERE negocio_id = %s",
                             (conectado.id,)).fetchone()
    assert TOKEN_META.encode() not in bytes(fila["token_cifrado"])       # cifrado en la base
    assert SECRETO.encode() not in bytes(fila["app_secret_cifrado"])


def test_guardar_sin_token_conserva_el_guardado(cliente, conectado, falsos):
    r = cliente.put("/api/whatsapp/cuenta", headers=conectado.admin, json=_cuenta(token="", app_secret="", ia_activa=False))
    assert r.status_code == 200 and r.json()["tiene_token"] and r.json()["tiene_secreto"]
    assert r.json()["webhook"] == conectado.whatsapp["webhook"]          # la dirección no cambia al volver a guardar


def test_capturista_no_ve_la_cuenta(cliente, conectado):
    assert cliente.get("/api/whatsapp/cuenta", headers=conectado.capturista).status_code == 403


def test_verificacion_del_webhook(cliente, conectado):
    ruta = conectado.whatsapp["webhook"]
    r = cliente.get(ruta, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"})
    assert r.status_code == 200 and r.text == "1158201444"
    r = cliente.get(ruta, params={"hub.mode": "subscribe", "hub.verify_token": "otro-token-cualquiera", "hub.challenge": "1"})
    assert r.status_code == 403
    assert cliente.get("/api/whatsapp/" + "0" * 32 + "/webhook", params={"hub.mode": "subscribe"}).status_code == 404


def test_firma_invalida_no_guarda_nada(cliente, conectado, falsos):
    ruta = conectado.whatsapp["webhook"]
    r = _publicar(cliente, ruta, _aviso(), secreto="f" * 32)
    assert r.status_code == 401
    r = cliente.post(ruta, json=_aviso())                                   # sin firma
    assert r.status_code == 401
    conv = cliente.get("/api/whatsapp/conversaciones", headers=conectado.admin).json()["conversaciones"]
    assert conv == [] and falsos["enviados"] == []


def test_la_ia_contesta_con_saludo_y_catalogo(cliente, conectado, falsos, admin_con):
    r = _publicar(cliente, conectado.whatsapp["webhook"], _aviso())
    assert r.status_code == 200, r.text
    # Primer contacto: saludo configurado y luego la respuesta de la IA, al número de quien escribió.
    assert [e["texto"] for e in falsos["enviados"]] == ["¡Hola! Soy Sofía.", falsos["respuesta"]]
    assert all(e["wa_id"] == "5215512345678" and e["token"] == TOKEN_META and e["pnid"] == PNID for e in falsos["enviados"])
    sistema = falsos["pedidos"][0]["sistema"]
    assert "Modelo Encino" in sistema and "Casas Sol" in sistema and "no vuelvas a saludar" in sistema
    conv = cliente.get("/api/whatsapp/conversaciones/5215512345678", headers=conectado.admin).json()
    assert [m["autor"] for m in conv["mensajes"]] == ["cliente", "ia", "ia"]
    assert conv["contacto"]["nombre"] == "Ana" and conv["contacto"]["tel10"] == "5512345678"
    uso = admin_con.execute("SELECT whatsapp_respuestas FROM uso_mensual WHERE negocio_id = %s", (conectado.id,)).fetchone()
    assert uso["whatsapp_respuestas"] == 1                                 # se cuenta la respuesta, no el saludo
    # Segundo mensaje: ya sin saludo, con la conversación anterior como contexto.
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso("¿Y cuántas recámaras?"))
    assert len(falsos["enviados"]) == 3
    assert falsos["pedidos"][1]["mensajes"][-1] == ("user", "¿Y cuántas recámaras?")
    assert "no vuelvas a saludar" not in falsos["pedidos"][1]["sistema"]


def test_la_ia_conoce_al_prospecto_pero_no_sus_datos_internos(cliente, conectado, falsos):
    from tests.conftest import visita
    r = cliente.post("/api/visitas", headers=conectado.capturista, json=visita(nombre="Ana Ruiz", tel="55 1234 5678",
                                                                                presupuesto=2345678, notas="nota secreta"))
    assert r.status_code in (200, 201), r.text
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso())
    sistema = falsos["pedidos"][0]["sistema"]
    assert "Quien escribe: Ana Ruiz; le interesa Modelo Encino" in sistema
    assert "2345678" not in sistema and "2,345,678" not in sistema and "nota secreta" not in sistema


def test_un_reintento_de_meta_no_se_contesta_dos_veces(cliente, conectado, falsos):
    aviso = _aviso(wamid="wamid.repetido.1")
    _publicar(cliente, conectado.whatsapp["webhook"], aviso)
    _publicar(cliente, conectado.whatsapp["webhook"], aviso)
    conv = cliente.get("/api/whatsapp/conversaciones/5215512345678", headers=conectado.admin).json()
    assert [m["autor"] for m in conv["mensajes"]].count("cliente") == 1
    assert len(falsos["pedidos"]) == 1


def test_otro_numero_de_la_misma_app_se_ignora(cliente, conectado, falsos):
    r = _publicar(cliente, conectado.whatsapp["webhook"], _aviso(pnid="999999999999"))
    assert r.status_code == 200
    assert cliente.get("/api/whatsapp/conversaciones", headers=conectado.admin).json()["conversaciones"] == []


def test_sin_complemento_o_con_ia_apagada_solo_se_guarda(cliente, conectado, falsos, admin_con):
    admin_con.execute("UPDATE negocios SET whatsapp_ia = false WHERE id = %s", (conectado.id,))
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso(wa_id="5215500000001"))
    admin_con.execute("UPDATE negocios SET whatsapp_ia = true WHERE id = %s", (conectado.id,))
    cliente.put("/api/whatsapp/cuenta", headers=conectado.admin, json=_cuenta(token="", app_secret="", ia_activa=False))
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso(wa_id="5215500000002"))
    assert falsos["enviados"] == [] and falsos["pedidos"] == []
    conv = cliente.get("/api/whatsapp/conversaciones", headers=conectado.admin).json()["conversaciones"]
    assert {c["wa_id"] for c in conv} == {"5215500000001", "5215500000002"}


def test_pausa_y_asesor_toman_la_conversacion(cliente, conectado, falsos):
    ruta = conectado.whatsapp["webhook"]
    _publicar(cliente, ruta, _aviso())
    r = cliente.post("/api/whatsapp/conversaciones/5215512345678/pausa", headers=conectado.admin, json={"pausada": True})
    assert r.status_code == 200 and r.json()["contacto"]["ia_pausada"]
    enviados = len(falsos["enviados"])
    _publicar(cliente, ruta, _aviso("¿Sigue disponible?"))
    assert len(falsos["enviados"]) == enviados                            # pausada: la IA no contesta
    cliente.post("/api/whatsapp/conversaciones/5215512345678/pausa", headers=conectado.admin, json={"pausada": False})
    r = cliente.post("/api/whatsapp/conversaciones/5215512345678/enviar", headers=conectado.admin,
                     json={"texto": "Hola Ana, soy Laura, tu asesora."})
    assert r.status_code == 200, r.text
    assert falsos["enviados"][-1]["texto"] == "Hola Ana, soy Laura, tu asesora."
    conv = cliente.get("/api/whatsapp/conversaciones/5215512345678", headers=conectado.admin).json()
    assert conv["mensajes"][-1]["autor"] == "asesor" and conv["contacto"]["ia_pausada"]   # el asesor la tomó


def test_asesor_fuera_de_las_24_horas(cliente, conectado, falsos, admin_con):
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso())
    admin_con.execute("UPDATE whatsapp_contactos SET ultimo_entrante = now() - interval '25 hours' WHERE negocio_id = %s",
                      (conectado.id,))
    r = cliente.post("/api/whatsapp/conversaciones/5215512345678/enviar", headers=conectado.admin, json={"texto": "Hola"})
    assert r.status_code == 409 and "24 horas" in r.json()["detail"]


def test_un_negocio_no_ve_las_conversaciones_de_otro(cliente, conectado, crear_negocio, falsos):
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso())
    otro = crear_negocio()
    assert cliente.get("/api/whatsapp/conversaciones", headers=otro.admin).json()["conversaciones"] == []
    assert cliente.get("/api/whatsapp/conversaciones/5215512345678", headers=otro.admin).status_code == 404
    r = cliente.post("/api/whatsapp/conversaciones/5215512345678/enviar", headers=otro.admin, json={"texto": "hola"})
    assert r.status_code == 404
    assert cliente.get("/api/whatsapp/cuenta", headers=otro.admin).json()["existe"] is False


def test_desconectar_borra_credenciales_y_conserva_conversaciones(cliente, conectado, falsos):
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso())
    r = cliente.delete("/api/whatsapp/cuenta", headers=conectado.admin)
    assert r.status_code == 200
    c = r.json()
    assert not c["tiene_token"] and not c["tiene_secreto"] and not c["ia_activa"] and c["phone_number_id"] == ""
    assert len(cliente.get("/api/whatsapp/conversaciones", headers=conectado.admin).json()["conversaciones"]) == 1
    # Sin clave secreta ya no se acepta ningún aviso.
    assert _publicar(cliente, conectado.whatsapp["webhook"], _aviso()).status_code == 401


def test_datos_invalidos(cliente, conectado):
    for malo in ({"phone_number_id": "abc"}, {"version": "23"}, {"verify_token": "corto"}, {"token": "con espacios no"},
                 {"app_secret": "no-hex!"}):
        r = cliente.put("/api/whatsapp/cuenta", headers=conectado.admin, json=_cuenta(**malo))
        assert r.status_code in (400, 422), (malo, r.text)


def test_turnos_para_la_ia():
    from app import ia
    t = ia.turnos([("assistant", "saludo"), ("user", "hola"), ("user", "¿precio?"), ("assistant", "2 millones"),
                   ("user", "gracias")])
    assert t == [{"role": "user", "content": "hola\n¿precio?"}, {"role": "assistant", "content": "2 millones"},
                 {"role": "user", "content": "gracias"}]


def test_destinatario_mexico():
    assert whatsapp.destinatario("5215512345678") == "525512345678"     # celular de México: sin el «1»
    assert whatsapp.destinatario("525512345678") == "525512345678"
    assert whatsapp.destinatario("14155550123") == "14155550123"         # otros países, igual


# --- Conversaciones de prueba: siguen el flujo real, pero nada sale a Meta ---

def test_prueba_sigue_el_flujo_real_sin_salir_a_meta(cliente, crear_negocio, admin_con, falsos):
    from tests.conftest import visita
    n = crear_negocio()
    admin_con.execute("UPDATE negocios SET whatsapp_ia = true WHERE id = %s", (n.id,))
    cliente.put("/api/config", headers=n.admin, json={"propiedades": [{"id": "modelo-encino", "nombre": "Modelo Encino"}]})
    # Sin número conectado ni token: la prueba funciona igual.
    r = cliente.put("/api/whatsapp/cuenta", headers=n.admin, json=_cuenta(phone_number_id="", token="", app_secret=""))
    assert r.status_code == 200 and not r.json()["tiene_token"]
    cliente.post("/api/visitas", headers=n.capturista, json=visita(nombre="Ana Ruiz", tel="55 1234 5678"))
    r = cliente.post("/api/whatsapp/prueba", headers=n.admin, json={"tel10": "5512345678", "nombre": "Ana Ruiz", "texto": "Hola"})
    assert r.status_code == 200 and r.json() == {"wa_id": "05512345678", "contestara": True, "motivo": ""}
    assert falsos["enviados"] == []                                       # nada sale por WhatsApp
    assert "Quien escribe: Ana Ruiz; le interesa Modelo Encino" in falsos["pedidos"][0]["sistema"]
    conv = cliente.get("/api/whatsapp/conversaciones/05512345678", headers=n.admin).json()
    assert [m["autor"] for m in conv["mensajes"]] == ["cliente", "ia", "ia"] and conv["contacto"]["prueba"]
    uso = admin_con.execute("SELECT whatsapp_respuestas FROM uso_mensual WHERE negocio_id = %s", (n.id,)).fetchone()
    assert not uso or uso["whatsapp_respuestas"] == 0                     # las pruebas no se cobran
    # El asesor puede tomarla, también sin salir a Meta.
    r = cliente.post("/api/whatsapp/conversaciones/05512345678/enviar", headers=n.admin, json={"texto": "Soy Laura"})
    assert r.status_code == 200 and falsos["enviados"] == []
    r = cliente.post("/api/whatsapp/prueba", headers=n.admin, json={"tel10": "5512345678", "nombre": "Ana Ruiz", "texto": "¿Sigue?"})
    assert r.json()["contestara"] is False and "pausada" in r.json()["motivo"]


def test_prueba_explica_por_que_no_contesta(cliente, crear_negocio):
    n = crear_negocio()
    r = cliente.post("/api/whatsapp/prueba", headers=n.admin, json={"tel10": "5512345678", "texto": "Hola"})
    assert r.status_code == 200 and not r.json()["contestara"] and "complemento" in r.json()["motivo"]
    assert cliente.post("/api/whatsapp/prueba", headers=n.admin, json={"tel10": "55", "texto": "Hola"}).status_code == 400
    assert cliente.post("/api/whatsapp/prueba", headers=n.capturista, json={"tel10": "5512345678", "texto": "Hola"}).status_code == 403


def test_el_webhook_no_acepta_numeros_de_prueba(cliente, conectado, falsos):
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso(wa_id="05512345678"))
    assert cliente.get("/api/whatsapp/conversaciones", headers=conectado.admin).json()["conversaciones"] == []


# --- Reiniciar (simulador): borrar la conversación de prueba, catálogo con desarrollos, estado de la IA ---

def test_reiniciar_borra_solo_conversaciones_de_prueba(cliente, conectado, falsos):
    cliente.post("/api/whatsapp/prueba", headers=conectado.admin, json={"tel10": "5512345678", "nombre": "Ana", "texto": "Hola"})
    _publicar(cliente, conectado.whatsapp["webhook"], _aviso())                     # una conversación real
    r = cliente.delete("/api/whatsapp/conversaciones/05512345678", headers=conectado.admin)
    assert r.status_code == 200 and r.json()["borrada"]
    assert cliente.get("/api/whatsapp/conversaciones/05512345678", headers=conectado.admin).status_code == 404
    assert cliente.delete("/api/whatsapp/conversaciones/5215512345678", headers=conectado.admin).status_code == 409
    assert cliente.get("/api/whatsapp/conversaciones/5215512345678", headers=conectado.admin).status_code == 200
    # Tras reiniciar, la IA vuelve a saludar: empieza de cero.
    cliente.post("/api/whatsapp/prueba", headers=conectado.admin, json={"tel10": "5512345678", "nombre": "Ana", "texto": "Hola"})
    conv = cliente.get("/api/whatsapp/conversaciones/05512345678", headers=conectado.admin).json()
    assert [m["autor"] for m in conv["mensajes"]] == ["cliente", "ia", "ia"]


def test_reiniciar_no_cruza_negocios(cliente, conectado, crear_negocio, falsos):
    cliente.post("/api/whatsapp/prueba", headers=conectado.admin, json={"tel10": "5512345678", "texto": "Hola"})
    otro = crear_negocio()
    assert cliente.delete("/api/whatsapp/conversaciones/05512345678", headers=otro.admin).json()["borrada"] is False
    assert cliente.get("/api/whatsapp/conversaciones/05512345678", headers=conectado.admin).status_code == 200


def test_catalogo_incluye_desarrollos_y_direccion():
    texto = whatsapp.catalogo({
        "zonas": [{"id": "z1", "nombre": "El Pedregal", "tipo": "desarrollo", "ciudad": "Aguascalientes",
                   "ubicacion": "Av. Siempre Viva 123", "descripcion": "Privada con alberca"}],
        "propiedades": [{"id": "p1", "nombre": "Casa Roble", "zona": "z1", "precio": 3500000, "direccion": "Calle 5 #10",
                         "descripcion": "Esquina", "presupuesto_cliente": 1, "notas_internas": "no"}]})
    assert "- El Pedregal: desarrollo en Aguascalientes; ubicación: Av. Siempre Viva 123; Privada con alberca." in texto
    assert "zona El Pedregal" in texto and "dirección: Calle 5 #10" in texto and "Esquina" in texto
    assert "notas_internas" not in texto and "no." not in texto.split("Esquina")[1]


def test_estado_de_la_ia_del_servidor(cliente, conectado):
    r = cliente.get("/api/whatsapp/ia", headers=conectado.admin)
    assert r.status_code == 200 and set(r.json()) == {"proveedor", "modelo", "listo", "detalle"}
    assert cliente.get("/api/whatsapp/ia", headers=conectado.capturista).status_code == 403
    assert "modelo" in cliente.get("/api/whatsapp/cuenta", headers=conectado.admin).json()["servidor"]
