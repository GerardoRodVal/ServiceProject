"""Validador de tokens de un proveedor externo (Entra External ID u otro OIDC), probado con claves propias.
No prueba el servicio real de Microsoft: eso solo se puede verificar en Azure (ver README)."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import ErrorToken, ValidadorOIDC

EMISOR = "https://login.ejemplo.test/tenant/v2.0"
AUDIENCIA = "api://captura"


@pytest.fixture(scope="module")
def clave():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(clave, **cambios):
    ahora = int(time.time())
    datos = {"iss": EMISOR, "aud": AUDIENCIA, "exp": ahora + 600, "iat": ahora, "sub": "sub-1", "oid": "oid-123"}
    datos.update(cambios)
    return jwt.encode({k: v for k, v in datos.items() if v is not None}, clave, algorithm="RS256")


def _validador(clave):
    return ValidadorOIDC(EMISOR, AUDIENCIA, "https://no-se-usa.test/jwks", "oid", resolver_clave=lambda token: clave.public_key())


def test_token_valido_devuelve_la_identidad(clave):
    assert _validador(clave).identidad(_token(clave)) == "oid-123"


def test_sin_el_claim_configurado_usa_sub(clave):
    assert _validador(clave).identidad(_token(clave, oid=None)) == "sub-1"


@pytest.mark.parametrize("cambios", [
    {"exp": int(time.time()) - 60},          # vencido
    {"aud": "api://otra"},                   # para otra API
    {"iss": "https://otro.test/v2.0"},       # emitido por otro proveedor
    {"exp": None},                           # sin vigencia
])
def test_tokens_invalidos_se_rechazan(clave, cambios):
    with pytest.raises(ErrorToken):
        _validador(clave).identidad(_token(clave, **cambios))


def test_firma_de_otra_clave_se_rechaza(clave):
    otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ErrorToken):
        _validador(clave).identidad(_token(otra))


def test_un_token_hs256_no_se_acepta_como_rs256(clave):
    falso = jwt.encode({"iss": EMISOR, "aud": AUDIENCIA, "exp": int(time.time()) + 600, "oid": "oid-123"}, "x" * 40, algorithm="HS256")
    with pytest.raises(ErrorToken):
        _validador(clave).identidad(falso)
