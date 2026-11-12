"""El celular directo a la nube: inicio de sesión OIDC (proveedor simulado de Docker), configuración por negocio,
asesor tomado de la sesión y archivos de la app que sirve la API."""
import base64
import hashlib
import os
import secrets
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

from .conftest import visita

IDP = os.environ.get("IDP_URL", "http://idp:9000")
REDIRECT = "http://127.0.0.1:8000/"
CLIENT = "captura-pwa"
CLAVE = "ClaveDePrueba-123"


def pkce():
    verificador = secrets.token_urlsafe(48)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, desafio


def parametros(desafio, **cambios):
    p = {"client_id": CLIENT, "redirect_uri": REDIRECT, "response_type": "code", "scope": "openid profile email offline_access",
         "state": secrets.token_urlsafe(12), "code_challenge": desafio, "code_challenge_method": "S256"}
    p.update(cambios)
    return {k: v for k, v in p.items() if v is not None}


def codigo_de(correo, clave, p, idp):
    r = idp.post("/authorize", data={**p, "correo": correo, "clave": clave})
    return r


REDIRECT_APK = "com.nearustech.capturavisitas:/oauth"      # la APK de Android: la vuelta llega como un enlace de la app


def iniciar_sesion(correo, clave=CLAVE, redirect=REDIRECT):
    """El recorrido completo de la app del celular: autorizar, volver con el código y canjearlo con PKCE."""
    verificador, desafio = pkce()
    p = parametros(desafio, redirect_uri=redirect)
    with httpx.Client(base_url=IDP, follow_redirects=False) as idp:
        assert idp.get("/authorize", params=p).status_code == 200
        r = codigo_de(correo, clave, p, idp)
        assert r.status_code == 303, r.text
        assert r.headers["location"].startswith(redirect + "?"), r.headers["location"]
        vuelta = parse_qs(urlparse(r.headers["location"]).query)
        assert vuelta["state"] == [p["state"]]
        t = idp.post("/token", data={"grant_type": "authorization_code", "client_id": CLIENT, "code": vuelta["code"][0],
                                     "redirect_uri": redirect, "code_verifier": verificador})
        assert t.status_code == 200, t.text
        return t.json()


def cabecera(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def usuario(crear_negocio, admin_con):
    """Un negocio con un capturista dado de alta en la base y en el proveedor de identidad simulado."""
    n = crear_negocio()
    correo = f"asesora.{uuid.uuid4().hex[:8]}@prueba.local"
    admin_con.execute("INSERT INTO usuarios (negocio_id, correo, nombre, rol) VALUES (%s, %s, 'Laura Gómez', 'capturista')", (n.id, correo))
    r = httpx.post(f"{IDP}/dev/usuarios", json={"correo": correo, "clave": CLAVE, "nombre": "Laura Gómez"})
    assert r.status_code == 200, r.text
    n.correo_celular = correo
    return n


# --- Inicio de sesión ---

def test_el_celular_inicia_sesion_y_la_api_acepta_su_token(cliente, usuario):
    t = iniciar_sesion(usuario.correo_celular)
    assert t["token_type"] == "Bearer" and t["refresh_token"] and t["id_token"]
    r = cliente.get("/api/app/config", headers=cabecera(t["access_token"]))
    assert r.status_code == 200, r.text
    assert r.json()["usuario"]["correo"] == usuario.correo_celular and r.json()["usuario"]["rol"] == "capturista"


def test_la_apk_inicia_sesion_con_su_enlace_de_retorno(cliente, usuario):
    """En la APK el inicio de sesión se hace en el navegador del sistema y vuelve por com.nearustech.capturavisitas:/oauth."""
    t = iniciar_sesion(usuario.correo_celular, redirect=REDIRECT_APK)
    assert cliente.get("/api/app/config", headers=cabecera(t["access_token"])).status_code == 200
    # El código canjeado con otro redirect_uri (el de la web) no vale: el proveedor lo ata al enlace con que se pidió.
    verificador, desafio = pkce()
    p = parametros(desafio, redirect_uri=REDIRECT_APK)
    with httpx.Client(base_url=IDP, follow_redirects=False) as idp:
        codigo = parse_qs(urlparse(codigo_de(usuario.correo_celular, CLAVE, p, idp).headers["location"]).query)["code"][0]
        r = idp.post("/token", data={"grant_type": "authorization_code", "client_id": CLIENT, "code": codigo,
                                     "redirect_uri": REDIRECT, "code_verifier": verificador})
        assert r.status_code == 400


def test_la_visita_queda_a_nombre_de_quien_inicio_sesion(cliente, usuario):
    t = iniciar_sesion(usuario.correo_celular)
    v = visita(asesor="Otra Persona")                    # lo que escriba el celular no cuenta
    assert cliente.post("/api/visitas", json=v, headers=cabecera(t["access_token"])).status_code == 200
    guardada = cliente.get("/api/visitas", headers=usuario.admin).json()[0]
    assert guardada["asesor"] == "Laura Gómez"
    assert guardada["asesor_id"]


def test_renovar_la_sesion_con_el_refresh_token_y_rotarlo(cliente, usuario):
    t = iniciar_sesion(usuario.correo_celular)
    with httpx.Client(base_url=IDP) as idp:
        r = idp.post("/token", data={"grant_type": "refresh_token", "client_id": CLIENT, "refresh_token": t["refresh_token"]})
        assert r.status_code == 200, r.text
        nuevo = r.json()
        assert nuevo["refresh_token"] != t["refresh_token"]
        assert cliente.get("/api/app/config", headers=cabecera(nuevo["access_token"])).status_code == 200
        reuso = idp.post("/token", data={"grant_type": "refresh_token", "client_id": CLIENT, "refresh_token": t["refresh_token"]})
        assert reuso.status_code == 400 and reuso.json()["error"] == "invalid_grant"      # el anterior ya no sirve


def test_el_proveedor_exige_pkce_y_un_solo_uso_del_codigo():
    verificador, desafio = pkce()
    p = parametros(desafio)
    with httpx.Client(base_url=IDP, follow_redirects=False) as idp:
        # Contraseña incorrecta: no hay código.
        malo = codigo_de("quien@prueba.local", "mala", p, idp)
        assert malo.status_code == 401 and "location" not in malo.headers
        httpx.post(f"{IDP}/dev/usuarios", json={"correo": "pkce@prueba.local", "clave": CLAVE})
        codigo = parse_qs(urlparse(codigo_de("pkce@prueba.local", CLAVE, p, idp).headers["location"]).query)["code"][0]
        canje = {"grant_type": "authorization_code", "client_id": CLIENT, "code": codigo, "redirect_uri": REDIRECT}
        # Un verificador que no corresponde al desafío se rechaza... y el código se quema.
        assert idp.post("/token", data={**canje, "code_verifier": secrets.token_urlsafe(48)}).json()["error"] == "invalid_grant"
        assert idp.post("/token", data={**canje, "code_verifier": verificador}).status_code == 400
        # Un código que ya se canjeó no se puede canjear otra vez.
        codigo2 = parse_qs(urlparse(codigo_de("pkce@prueba.local", CLAVE, p, idp).headers["location"]).query)["code"][0]
        assert idp.post("/token", data={**canje, "code": codigo2, "code_verifier": verificador}).status_code == 200
        assert idp.post("/token", data={**canje, "code": codigo2, "code_verifier": verificador}).status_code == 400


@pytest.mark.parametrize("cambios", [
    {"redirect_uri": "https://evil.example/"},      # destino no registrado
    {"client_id": "otro-cliente"},
    {"code_challenge": None},                       # sin PKCE
    {"code_challenge_method": "plain"},
    {"state": None},
    {"response_type": "token"},                     # flujo implícito
])
def test_el_proveedor_rechaza_peticiones_mal_formadas_sin_redirigir(cambios):
    _, desafio = pkce()
    r = httpx.get(f"{IDP}/authorize", params=parametros(desafio, **cambios), follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers


def test_crear_cuenta_en_el_proveedor_no_basta_para_entrar_a_un_negocio(cliente, crear_negocio):
    """Primer ingreso de alguien dado de alta: crea su cuenta en el proveedor; la API la deja pasar solo si el
    administrador ya la registró."""
    n = crear_negocio()
    c = f"primera.{uuid.uuid4().hex[:8]}@prueba.local"
    verificador, desafio = pkce()
    p = parametros(desafio)
    with httpx.Client(base_url=IDP, follow_redirects=False) as idp:
        assert "/registro?" in idp.get("/authorize", params=p).text                       # el inicio de sesión ofrece crear cuenta
        assert idp.get("/registro", params=p).status_code == 200
        datos = {**p, "nombre": "Primera Vez", "correo": c}
        assert idp.post("/registro", data={**datos, "clave": "corta"}).status_code == 400            # contraseña floja
        assert idp.post("/registro", data={**datos, "correo": "no-es-correo", "clave": CLAVE}).status_code == 400
        r = idp.post("/registro", data={**datos, "clave": CLAVE})
        assert r.status_code == 303, r.text                                                  # cuenta creada y sesión iniciada
        codigo = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        t = idp.post("/token", data={"grant_type": "authorization_code", "client_id": CLIENT, "code": codigo,
                                     "redirect_uri": REDIRECT, "code_verifier": verificador}).json()
        assert idp.post("/registro", data={**datos, "clave": CLAVE}).status_code == 400            # ya tiene cuenta
        assert "ya tiene una cuenta" in idp.post("/registro", data={**datos, "clave": CLAVE}).text
        assert codigo_de(c, CLAVE, p, idp).status_code == 303                                # y puede iniciar sesión normalmente
    acceso = cabecera(t["access_token"])
    assert cliente.get("/api/app/config", headers=acceso).status_code == 403               # autenticada, pero sin alta en ningún negocio
    assert cliente.post("/api/usuarios", json={"correo": c, "nombre": "Primera Vez"}, headers=n.admin).status_code == 200
    r = cliente.get("/api/app/config", headers=acceso)
    assert r.status_code == 200 and r.json()["usuario"]["nombre"] == "Primera Vez"          # ahora sí, sin volver a iniciar sesión


# --- Lo que la API acepta y rechaza ---

def test_el_id_token_no_sirve_como_token_de_acceso(cliente, usuario):
    t = iniciar_sesion(usuario.correo_celular)
    assert cliente.get("/api/app/config", headers=cabecera(t["id_token"])).status_code == 401      # su audiencia es la app, no la API


def test_un_usuario_del_proveedor_que_no_esta_en_la_base_no_entra(cliente):
    httpx.post(f"{IDP}/dev/usuarios", json={"correo": "intruso@prueba.local", "clave": CLAVE})
    t = iniciar_sesion("intruso@prueba.local")
    assert cliente.get("/api/app/config", headers=cabecera(t["access_token"])).status_code == 403


def test_tokens_sin_firma_o_con_firma_ajena_se_rechazan(cliente, usuario):
    sin_firma = jwt.encode({"email": usuario.correo_celular, "iss": "http://127.0.0.1:9000", "aud": "api://captura"}, None, algorithm="none")
    assert cliente.get("/api/app/config", headers=cabecera(sin_firma)).status_code == 401
    t = iniciar_sesion(usuario.correo_celular)
    manipulado = t["access_token"][:-4] + "AAAA"
    assert cliente.get("/api/app/config", headers=cabecera(manipulado)).status_code == 401


# --- Configuración por negocio ---

def test_el_administrador_publica_la_configuracion_y_el_celular_la_recibe(cliente, usuario):
    config = {"negocio": {"nombre": "Mi inmobiliaria"}, "propiedades": [{"id": "p1", "nombre": "Casa", "precio": 100}],
              "formulario": {"campos": []}, "acceso": {"url_celular": "http://192.168.1.5:8080"}, "ia": {"modelo": "x"},
              "taller": {}, "conexiones": [], "asesores": [{"nombre": "Alguien"}]}
    assert cliente.put("/api/config", json=config, headers=usuario.admin).status_code == 200
    r = cliente.get("/api/app/config", headers=usuario.capturista).json()
    assert r["config"]["negocio"]["nombre"] == "Mi inmobiliaria" and r["config"]["propiedades"][0]["id"] == "p1"
    assert not {"acceso", "ia", "taller", "conexiones", "asesores"} & set(r["config"])      # no salen hacia los celulares
    assert r["negocio"]["nombre"].startswith("Prueba")


def test_la_configuracion_es_solo_para_el_administrador_y_aislada_por_negocio(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    assert cliente.put("/api/config", json={"negocio": {"nombre": "De A"}}, headers=a.capturista).status_code == 403
    cliente.put("/api/config", json={"negocio": {"nombre": "De A"}}, headers=a.admin)
    assert cliente.get("/api/app/config", headers=b.capturista).json()["config"] is None            # B no ve la de A
    assert cliente.put("/api/config", json={"relleno": "x" * (600 * 1024)}, headers=a.admin).status_code == 413


# --- Archivos de la app del celular ---

def test_la_api_sirve_la_app_y_solo_los_archivos_permitidos(cliente):
    for ruta, tipo in (("/", "text/html"), ("/index.html", "text/html"), ("/inicio", "text/html"), ("/inicio.html", "text/html"),
                       ("/cliente.html", "text/html"), ("/sw.js", "javascript"),
                       ("/manifest.webmanifest", "manifest+json"), ("/vendor/qrcode.js", "javascript")):
        r = cliente.get(ruta)
        assert r.status_code == 200 and tipo in r.headers["content-type"], ruta
        assert r.headers["cache-control"] == "no-cache" and r.headers["x-content-type-options"] == "nosniff"
    assert cliente.get("/sw.js").headers["service-worker-allowed"] == "/"
    assert "Captura de visita" in cliente.get("/").text
    assert "<title>NearLeads</title>" in cliente.get("/inicio").text
    for ruta in ("/config.json", "/taller.html", "/seguimiento.html", "/../captura-visitas-pwa/config.json",
                 "/vendor/../config.json", "/%2e%2e/config.json"):
        assert cliente.get(ruta).status_code == 404, ruta


def test_app_config_dice_donde_iniciar_sesion(cliente):
    r = cliente.get("/app-config.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"modo": "nube", "oidc": {"emisor": "http://127.0.0.1:9000", "client_id": "captura-pwa",
                                                 "scope": "openid profile email offline_access"}}
