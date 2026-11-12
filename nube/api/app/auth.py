"""Autenticación intercambiable.

- local: la API emite sus propios tokens de prueba (solo en tu PC, POST /dev/token).
- oidc:  valida tokens firmados por un proveedor externo (Microsoft Entra External ID u otro OpenID Connect).

En los dos casos el resultado es el mismo: una «identidad» ya verificada, que la base de datos traduce
a usuario, negocio y rol. Cambiar de uno a otro es cambiar variables de entorno, no código.
"""
import time
from typing import Callable, NamedTuple

import jwt
from fastapi import Depends, HTTPException, Request

from . import db

AUDIENCIA_LOCAL = "captura-api"
EMISOR_LOCAL = "captura-local"


class ErrorToken(Exception):
    pass


class Identidad(NamedTuple):
    usuario_id: str
    negocio_id: str
    rol: str


class ValidadorLocal:
    def __init__(self, secreto):
        self.secreto = secreto

    def emitir(self, correo, horas=8):
        ahora = int(time.time())
        return jwt.encode({"sub": correo, "iss": EMISOR_LOCAL, "aud": AUDIENCIA_LOCAL, "iat": ahora, "exp": ahora + horas * 3600},
                          self.secreto, algorithm="HS256")

    def identidad(self, token):
        try:
            datos = jwt.decode(token, self.secreto, algorithms=["HS256"], audience=AUDIENCIA_LOCAL, issuer=EMISOR_LOCAL,
                               options={"require": ["exp", "sub", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise ErrorToken(str(e))
        return datos["sub"]


class ValidadorOIDC:
    """Valida firma (RS256), emisor, audiencia y vigencia. La clave pública se baja del JWKS del proveedor."""

    def __init__(self, emisor, audiencia, jwks_url, claim_identidad="oid", resolver_clave: Callable[[str], object] | None = None):
        self.emisor = emisor
        self.audiencia = audiencia
        self.claim = claim_identidad
        if resolver_clave is None:
            cliente = jwt.PyJWKClient(jwks_url, cache_keys=True)
            resolver_clave = lambda token: cliente.get_signing_key_from_jwt(token).key
        self._clave = resolver_clave

    def identidad(self, token):
        try:
            datos = jwt.decode(token, self._clave(token), algorithms=["RS256"], audience=self.audiencia, issuer=self.emisor,
                               options={"require": ["exp", "iss", "aud"]})
        except jwt.PyJWTError as e:
            raise ErrorToken(str(e))
        identidad = datos.get(self.claim) or datos.get("sub")
        if not identidad:
            raise ErrorToken("El token no trae identidad.")
        return str(identidad)


class ValidadorCombinado:
    """Solo en local: acepta los tokens de prueba (HS256) y los de un proveedor OIDC (RS256). Cada validador fija su
    algoritmo, así que un token no puede hacerse pasar por el del otro."""

    def __init__(self, local, oidc):
        self.local = local
        self.oidc = oidc

    def emitir(self, correo, horas=8):
        return self.local.emitir(correo, horas)

    def identidad(self, token):
        try:
            algoritmo = jwt.get_unverified_header(token).get("alg")
        except jwt.PyJWTError:
            raise ErrorToken("Token mal formado.")
        if algoritmo == "HS256":
            return self.local.identidad(token)
        if algoritmo == "RS256":
            return self.oidc.identidad(token)
        raise ErrorToken("Algoritmo no aceptado.")


def crear_validador(cfg):
    local = ValidadorLocal(cfg.jwt_dev_secreto) if cfg.auth_modo == "local" else None
    oidc = (ValidadorOIDC(cfg.oidc_emisor, cfg.oidc_audiencia, cfg.oidc_jwks_url, cfg.oidc_claim_identidad)
            if cfg.oidc_emisor else None)
    if local and oidc:
        return ValidadorCombinado(local, oidc)
    return local or oidc


def identidad_actual(request: Request) -> Identidad:
    cabecera = request.headers.get("authorization", "")
    esquema, _, token = cabecera.partition(" ")
    if esquema.lower() != "bearer" or not token:
        raise HTTPException(401, "Falta iniciar sesión.", headers={"WWW-Authenticate": "Bearer"})
    try:
        identidad = request.app.state.validador.identidad(token.strip())
    except ErrorToken:
        raise HTTPException(401, "La sesión no es válida o venció.", headers={"WWW-Authenticate": "Bearer"})
    fila = db.identificar(identidad)
    if not fila:
        raise HTTPException(403, "Tu usuario no está dado de alta o está inactivo.")
    return Identidad(str(fila["usuario_id"]), str(fila["negocio_id"]), fila["rol"])


def requiere(*roles):
    def dependencia(ident: Identidad = Depends(identidad_actual)):
        if ident.rol not in roles:
            raise HTTPException(403, "No tienes permiso para esta acción.")
        return ident
    return dependencia
