"""Proveedor de identidad OpenID Connect SOLO PARA DESARROLLO LOCAL. Simula a Microsoft Entra External ID para poder
probar el inicio de sesión de la app del celular (código de autorización con PKCE) sin salir de tu PC.

Como en un flujo de «registro e inicio de sesión» de Entra, cualquiera puede crear una cuenta (correo y contraseña);
quién puede usar la app lo decide después la API con su lista de usuarios dados de alta.

No es un servicio de producción: no verifica el correo, no hay límite de intentos ni consentimiento y el control de
usuarios es mínimo. En Azure este servicio no existe: lo reemplaza Entra External ID sin cambiar la app.

  uvicorn app.dev_idp:app --port 9000
"""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import threading
import time
import uuid
from urllib.parse import urlencode, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

VIDA_CODIGO = 300
ITERACIONES = 200_000
RE_CORREO = re.compile(r"[^@\s]{1,100}@[^@\s]{1,100}\.[^@\s]{2,20}")
_lock = threading.Lock()
_codigos = {}       # código de autorización -> datos (un solo uso)
_refrescos = {}     # refresh token -> datos


def _b64url(datos: bytes):
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _entero_b64(n: int):
    return _b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def _hash(clave: str, sal: bytes):
    return hashlib.pbkdf2_hmac("sha256", clave.encode(), sal, ITERACIONES).hex()


def _nuevo_usuario(nombre: str, clave: str):
    sal = secrets.token_bytes(16)
    return {"nombre": nombre, "sal": sal.hex(), "hash": _hash(clave, sal)}


def _cargar_o_crear_clave(ruta):
    """La clave se guarda en un archivo (volumen) para que reiniciar el servicio no invalide las sesiones abiertas."""
    if ruta and os.path.exists(ruta):
        with open(ruta, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if ruta:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "wb") as f:
            f.write(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return clave


def crear_app(env=None):
    env = env if env is not None else os.environ
    emisor = env.get("IDP_EMISOR", "").rstrip("/")
    if not emisor:
        raise RuntimeError("Falta IDP_EMISOR (por ejemplo http://127.0.0.1:9000).")
    audiencia = env.get("IDP_AUDIENCIA", "api://captura")
    client_id = env.get("IDP_CLIENT_ID", "captura-pwa")
    redirects = [r.strip() for r in env.get("IDP_REDIRECTS", "http://127.0.0.1:8000/,http://localhost:8000/").split(",") if r.strip()]
    vida_acceso = int(env.get("IDP_ACCESS_SEGUNDOS", "3600"))
    vida_refresco = int(env.get("IDP_REFRESH_SEGUNDOS", "86400"))
    permitir_altas = env.get("IDP_PERMITIR_ALTAS", "0") == "1"
    ruta_usuarios = env.get("IDP_USUARIOS_RUTA", "")

    usuarios = {}       # correo en minúsculas -> {"nombre", "sal", "hash"}
    for correo, d in json.loads(env.get("IDP_USUARIOS", "{}") or "{}").items():
        usuarios[correo.lower()] = _nuevo_usuario(d.get("nombre", correo.split("@")[0]), d["clave"])
    if ruta_usuarios and os.path.exists(ruta_usuarios):
        with open(ruta_usuarios, encoding="utf-8") as f:
            usuarios.update(json.load(f))

    def guardar_usuarios(solo_persistentes):
        """Solo las cuentas creadas desde la pantalla de registro sobreviven a un reinicio."""
        if not ruta_usuarios:
            return
        os.makedirs(os.path.dirname(ruta_usuarios), exist_ok=True)
        tmp = ruta_usuarios + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(solo_persistentes, f, ensure_ascii=False)
        os.replace(tmp, ruta_usuarios)

    persistentes = {}
    if ruta_usuarios and os.path.exists(ruta_usuarios):
        with open(ruta_usuarios, encoding="utf-8") as f:
            persistentes = json.load(f)

    clave = _cargar_o_crear_clave(env.get("IDP_CLAVE_RUTA", ""))
    numeros = clave.public_key().public_numbers()
    kid = _b64url(hashlib.sha256(_entero_b64(numeros.n).encode()).digest())[:16]
    jwks = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": _entero_b64(numeros.n), "e": _entero_b64(numeros.e)}]}
    origenes = sorted({f"{urlparse(r).scheme}://{urlparse(r).netloc}" for r in redirects})

    app = FastAPI(title="Proveedor OIDC local (solo desarrollo)", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=origenes, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    def _sub(correo):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{emisor}/{correo}"))

    def _firmar(datos):
        return jwt.encode(datos, clave, algorithm="RS256", headers={"kid": kid})

    def _tokens(correo, nonce=None):
        u, ahora = usuarios[correo], int(time.time())
        sub = _sub(correo)
        base = {"iss": emisor, "sub": sub, "oid": sub, "email": correo, "preferred_username": correo, "name": u["nombre"],
                "iat": ahora, "nbf": ahora}
        acceso = _firmar({**base, "aud": audiencia, "scp": "captura", "exp": ahora + vida_acceso, "jti": uuid.uuid4().hex})
        id_token = _firmar({**base, "aud": client_id, "exp": ahora + vida_acceso, **({"nonce": nonce} if nonce else {})})
        refresco = secrets.token_urlsafe(48)
        with _lock:
            _refrescos[refresco] = {"correo": correo, "expira": ahora + vida_refresco}
        return {"token_type": "Bearer", "access_token": acceso, "id_token": id_token, "expires_in": vida_acceso,
                "scope": "openid profile email", "refresh_token": refresco}

    def _verificar(correo, clave_enviada):
        u = usuarios.get(correo)
        # Se calcula siempre un hash para que el tiempo de respuesta no delate si el correo existe.
        sal = bytes.fromhex(u["sal"]) if u else secrets.token_bytes(16)
        calculado = _hash(clave_enviada, sal)
        return bool(u) and hmac.compare_digest(calculado, u["hash"])

    ESTILO = """body{font:16px system-ui,sans-serif;background:#f4f6f3;color:#16221f;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}
form{background:#fff;border:1px solid #dde3df;border-radius:16px;padding:24px;width:100%;max-width:360px;display:grid;gap:12px}
h1{font-size:20px;margin:0} .sim{font-size:12px;background:#fff4d6;border:1px solid #e6c56b;border-radius:8px;padding:6px 8px;margin:0}
label{display:grid;gap:4px;font-size:14px;font-weight:600} input[type=email],input[type=password],input[type=text]{padding:12px;border:1px solid #c5cdc8;border-radius:10px;font-size:16px}
button{padding:12px;border:0;border-radius:10px;background:#0b7a66;color:#fff;font-size:16px;font-weight:700;cursor:pointer} .err{color:#b3261e;margin:0;font-size:14px}
a{color:#0b7a66;font-size:14px;text-align:center}"""

    def _pagina(modo, parametros, error="", codigo=200):
        ocultos = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in parametros.items())
        aviso = f'<p class="err">{html.escape(error)}</p>' if error else ""
        if modo == "registro":
            destino, titulo, boton = "/registro", "Crear cuenta", "Crear cuenta y entrar"
            campos = ('<label>Nombre<input type="text" name="nombre" autocomplete="name" required autofocus></label>'
                      '<label>Correo<input type="email" name="correo" autocomplete="username" required></label>'
                      '<label>Contraseña (mínimo 8 caracteres)<input type="password" name="clave" autocomplete="new-password" required minlength="8"></label>')
            enlace = f'<a href="/authorize?{html.escape(urlencode(parametros))}">Ya tengo cuenta: iniciar sesión</a>'
        else:
            destino, titulo, boton = "/authorize", "Iniciar sesión", "Entrar"
            campos = ('<label>Correo<input type="email" name="correo" autocomplete="username" required autofocus></label>'
                      '<label>Contraseña<input type="password" name="clave" autocomplete="current-password" required></label>')
            enlace = f'<a href="/registro?{html.escape(urlencode(parametros))}">¿Primera vez? Crea tu cuenta</a>'
        cuerpo = (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                  f'<title>{titulo}</title><style>{ESTILO}</style></head><body><form method="post" action="{destino}"><h1>{titulo}</h1>'
                  f'<p class="sim">Simulación de Microsoft Entra External ID · solo desarrollo local</p>{aviso}{campos}{ocultos}'
                  f'<button type="submit">{boton}</button>{enlace}</form></body></html>')
        return HTMLResponse(cuerpo, status_code=codigo, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                                                  "Content-Security-Policy": "frame-ancestors 'none'"})

    def _validar_peticion(p):
        """Errores del cliente (no del usuario): no se redirige, se muestra el motivo."""
        if p.get("client_id") != client_id:
            raise HTTPException(400, "client_id desconocido.")
        if p.get("redirect_uri") not in redirects:
            raise HTTPException(400, "redirect_uri no registrada.")
        if p.get("response_type") != "code":
            raise HTTPException(400, "Solo se admite response_type=code.")
        if p.get("code_challenge_method") != "S256" or not p.get("code_challenge"):
            raise HTTPException(400, "Se exige PKCE con code_challenge_method=S256.")
        if not p.get("state"):
            raise HTTPException(400, "Falta state.")

    def _entregar_codigo(correo, p):
        codigo = secrets.token_urlsafe(32)
        with _lock:
            _codigos[codigo] = {"correo": correo, "client_id": p["client_id"], "redirect_uri": p["redirect_uri"],
                                "challenge": p["code_challenge"], "nonce": p["nonce"] or None, "expira": time.time() + VIDA_CODIGO}
        destino = f"{p['redirect_uri']}{'&' if '?' in p['redirect_uri'] else '?'}{urlencode({'code': codigo, 'state': p['state']})}"
        return RedirectResponse(destino, status_code=303, headers={"Cache-Control": "no-store"})

    CAMPOS = ("client_id", "redirect_uri", "response_type", "scope", "state", "code_challenge", "code_challenge_method", "nonce")
    en_pagina = lambda p: {k: v for k, v in p.items() if v}

    @app.get("/.well-known/openid-configuration")
    def descubrimiento():
        return {"issuer": emisor, "authorization_endpoint": f"{emisor}/authorize", "token_endpoint": f"{emisor}/token",
                "jwks_uri": f"{emisor}/jwks", "response_types_supported": ["code"], "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token"], "scopes_supported": ["openid", "profile", "email", "offline_access"],
                "token_endpoint_auth_methods_supported": ["none"]}

    @app.get("/jwks")
    def claves():
        return jwks

    @app.get("/authorize")
    def autorizar_formulario(request: Request):
        p = {k: request.query_params.get(k, "") for k in CAMPOS}
        _validar_peticion(p)
        return _pagina("login", en_pagina(p))

    @app.post("/authorize")
    async def autorizar_envio(request: Request):
        formulario = await request.form()
        p = {k: str(formulario.get(k, "")) for k in CAMPOS}
        _validar_peticion(p)
        correo = str(formulario.get("correo", "")).strip().lower()
        if not _verificar(correo, str(formulario.get("clave", ""))):
            return _pagina("login", en_pagina(p), "Correo o contraseña incorrectos.", 401)
        return _entregar_codigo(correo, p)

    @app.get("/registro")
    def registro_formulario(request: Request):
        p = {k: request.query_params.get(k, "") for k in CAMPOS}
        _validar_peticion(p)
        return _pagina("registro", en_pagina(p))

    @app.post("/registro")
    async def registro_envio(request: Request):
        formulario = await request.form()
        p = {k: str(formulario.get(k, "")) for k in CAMPOS}
        _validar_peticion(p)
        correo = str(formulario.get("correo", "")).strip().lower()
        nombre = str(formulario.get("nombre", "")).strip()[:80]
        clave_nueva = str(formulario.get("clave", ""))
        error = ""
        if not nombre:
            error = "Escribe tu nombre."
        elif len(correo) > 200 or not RE_CORREO.fullmatch(correo):
            error = "Escribe un correo válido."
        elif len(clave_nueva) < 8 or len(clave_nueva) > 128:
            error = "La contraseña debe tener entre 8 y 128 caracteres."
        elif correo in usuarios:
            error = "Ese correo ya tiene una cuenta. Inicia sesión."
        if error:
            return _pagina("registro", en_pagina(p), error, 400)
        nuevo = _nuevo_usuario(nombre, clave_nueva)
        with _lock:
            usuarios[correo] = nuevo
            persistentes[correo] = nuevo
            guardar_usuarios(persistentes)
        return _entregar_codigo(correo, p)

    def _error(codigo, descripcion):
        return JSONResponse({"error": codigo, "error_description": descripcion}, status_code=400, headers={"Cache-Control": "no-store"})

    @app.post("/token")
    async def token(request: Request):
        f = await request.form()
        tipo = f.get("grant_type")
        if f.get("client_id") != client_id:
            return _error("invalid_client", "client_id desconocido.")
        if tipo == "authorization_code":
            with _lock:
                datos = _codigos.pop(str(f.get("code", "")), None)       # un solo uso, también si falla lo demás
            if not datos or datos["expira"] < time.time():
                return _error("invalid_grant", "Código inválido o vencido.")
            if datos["client_id"] != client_id or datos["redirect_uri"] != f.get("redirect_uri"):
                return _error("invalid_grant", "redirect_uri no coincide.")
            verificador = str(f.get("code_verifier", ""))
            if not (43 <= len(verificador) <= 128) or not hmac.compare_digest(
                    _b64url(hashlib.sha256(verificador.encode()).digest()), datos["challenge"]):
                return _error("invalid_grant", "code_verifier no coincide (PKCE).")
            return JSONResponse(_tokens(datos["correo"], datos["nonce"]), headers={"Cache-Control": "no-store"})
        if tipo == "refresh_token":
            with _lock:
                datos = _refrescos.pop(str(f.get("refresh_token", "")), None)    # se rota en cada uso
            if not datos or datos["expira"] < time.time() or datos["correo"] not in usuarios:
                return _error("invalid_grant", "Sesión vencida: hay que iniciar sesión de nuevo.")
            return JSONResponse(_tokens(datos["correo"]), headers={"Cache-Control": "no-store"})
        return _error("unsupported_grant_type", "Solo authorization_code y refresh_token.")

    if permitir_altas:
        @app.post("/dev/usuarios")
        async def alta(request: Request):
            """Solo desarrollo y pruebas: registra o cambia un usuario (en memoria; no sobrevive a un reinicio)."""
            d = await request.json()
            correo = str(d.get("correo", "")).strip().lower()
            if not correo or "@" not in correo or not d.get("clave"):
                raise HTTPException(422, "Faltan correo y clave.")
            usuarios[correo] = _nuevo_usuario(str(d.get("nombre") or correo.split("@")[0]), str(d["clave"]))
            return {"ok": True, "sub": _sub(correo)}

    @app.get("/salud")
    def salud():
        return {"ok": True}

    return app


app = crear_app() if os.environ.get("IDP_EMISOR") else None
