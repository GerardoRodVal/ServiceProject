"""Toda la configuración sale de variables de entorno: es lo único que cambia entre tu PC y Azure."""
import base64
import binascii
import os
from dataclasses import dataclass


def _requerida(nombre):
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"Falta la variable de entorno {nombre}.")
    return valor


@dataclass(frozen=True)
class Config:
    database_url: str
    auth_modo: str                 # "local" (desarrollo) u "oidc" (Entra External ID u otro proveedor)
    jwt_dev_secreto: str
    oidc_emisor: str
    oidc_jwks_url: str
    oidc_audiencia: str
    oidc_claim_identidad: str
    blob_connection_string: str    # Azurite en local
    blob_account_url: str          # Azure real, con identidad administrada (sin llaves)
    blob_contenedor: str
    blob_crear_contenedor: bool
    limite_registros_mes: int | None
    limite_archivos_mb_mes: int | None
    limite_usuarios: int | None
    retencion_ubicacion_dias: int
    # WhatsApp con IA (complemento). La clave cifra en la base los tokens de Meta de cada negocio; en Azure vive en
    # Key Vault. Sin ella no se pueden guardar cuentas de WhatsApp.
    whatsapp_clave: bytes | None = None
    ia_proveedor: str = ""          # "anthropic" (Claude, producción), "ollama" (modelo local) o "" (sin IA)
    ia_modelo: str = ""             # vacío = el de cada proveedor (ver app/ia.py)
    ollama_url: str = "http://host.docker.internal:11434"
    meta_graph_url: str = "https://graph.facebook.com"


def _entero_opcional(nombre):
    texto = os.environ.get(nombre, "").strip()
    return int(texto) if texto else None


def _clave_whatsapp():
    texto = os.environ.get("WHATSAPP_CLAVE", "").strip()
    if not texto:
        return None
    try:
        clave = base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))
    except (binascii.Error, ValueError):
        clave = b""
    if len(clave) != 32:
        raise RuntimeError("WHATSAPP_CLAVE debe ser 32 bytes en base64 (python -c \"import secrets,base64;"
                           "print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\").")
    return clave


def cargar():
    modo = os.environ.get("AUTH_MODO", "local").strip().lower()
    if modo not in ("local", "oidc"):
        raise RuntimeError("AUTH_MODO debe ser «local» u «oidc».")
    cfg = Config(
        database_url=_requerida("DATABASE_URL"),
        auth_modo=modo,
        jwt_dev_secreto=os.environ.get("JWT_DEV_SECRETO", ""),
        oidc_emisor=os.environ.get("OIDC_EMISOR", ""),
        oidc_jwks_url=os.environ.get("OIDC_JWKS_URL", ""),
        oidc_audiencia=os.environ.get("OIDC_AUDIENCIA", ""),
        oidc_claim_identidad=os.environ.get("OIDC_CLAIM_IDENTIDAD", "oid"),
        blob_connection_string=os.environ.get("BLOB_CONNECTION_STRING", ""),
        blob_account_url=os.environ.get("BLOB_ACCOUNT_URL", ""),
        blob_contenedor=os.environ.get("BLOB_CONTENEDOR", "archivos"),
        blob_crear_contenedor=os.environ.get("BLOB_CREAR_CONTENEDOR", "1") == "1",
        # Los límites de los planes aún no están definidos: vacíos = sin límite. Ver README.
        limite_registros_mes=_entero_opcional("LIMITE_REGISTROS_MES"),
        limite_archivos_mb_mes=_entero_opcional("LIMITE_ARCHIVOS_MB_MES"),
        limite_usuarios=_entero_opcional("LIMITE_USUARIOS"),
        # La ubicación es un dato sensible: el trayecto se borra pasado este tiempo (por omisión, 30 días).
        retencion_ubicacion_dias=max(1, _entero_opcional("RETENCION_UBICACION_DIAS") or 30),
        whatsapp_clave=_clave_whatsapp(),
        ia_proveedor=os.environ.get("IA_PROVEEDOR", "").strip().lower(),
        ia_modelo=os.environ.get("IA_MODELO", "").strip(),
        ollama_url=os.environ.get("OLLAMA_URL", "").strip() or "http://host.docker.internal:11434",
        meta_graph_url=(os.environ.get("META_GRAPH_URL", "").strip() or "https://graph.facebook.com").rstrip("/"),
    )
    if cfg.ia_proveedor not in ("", "anthropic", "ollama"):
        raise RuntimeError("IA_PROVEEDOR debe ser «anthropic», «ollama» o quedar vacío.")
    oidc = [cfg.oidc_emisor, cfg.oidc_jwks_url, cfg.oidc_audiencia]
    if modo == "local" and len(cfg.jwt_dev_secreto.encode()) < 32:
        raise RuntimeError("JWT_DEV_SECRETO debe tener al menos 32 caracteres en modo local.")
    if modo == "oidc" and not all(oidc):
        raise RuntimeError("Modo oidc: faltan OIDC_EMISOR, OIDC_JWKS_URL u OIDC_AUDIENCIA.")
    # En local se pueden aceptar a la vez los tokens de prueba y los de un proveedor OIDC (el simulado de Docker),
    # pero la configuración OIDC no puede quedar a medias.
    if modo == "local" and any(oidc) and not all(oidc):
        raise RuntimeError("Faltan variables OIDC: OIDC_EMISOR, OIDC_JWKS_URL y OIDC_AUDIENCIA van juntas.")
    if not (cfg.blob_connection_string or cfg.blob_account_url):
        raise RuntimeError("Falta BLOB_CONNECTION_STRING (local) o BLOB_ACCOUNT_URL (Azure).")
    return cfg
