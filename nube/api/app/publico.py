"""Vista del cliente: los datos de UNA casa, sin sesión, para la página que abre el QR que le muestra el asesor.

El acceso es por enlace: token del negocio (128 bits al azar) + identificador de la casa. No se devuelve la configuración
del negocio sino una ficha con solo los campos que la página muestra (lista blanca): nada de score, asesores, accesos ni
datos internos del inmueble.
"""
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from . import db
from .auth import requiere

router = APIRouter(prefix="/api")
solo_admin = requiere("admin")
RE_TOKEN = re.compile(r"[0-9a-f]{32}")
RE_ID_CASA = re.compile(r"[a-z0-9-]{1,60}")
RE_ID_FOTO = re.compile(r"[0-9a-f]{32}")
NO_ENCONTRADA = "No encontramos la casa."


def _texto(valor, largo):
    return valor.strip()[:largo] if isinstance(valor, str) else ""


def _numero(valor):
    """Solo números de verdad (no texto ni booleanos); lo demás se muestra como 0."""
    return valor if isinstance(valor, (int, float)) and not isinstance(valor, bool) else 0


def ficha(propiedad, zonas, nombre_negocio):
    """Lo que ve el cliente de una casa. Cada campo se valida y se recorta."""
    zona = next((z for z in zonas if isinstance(z, dict) and z.get("id") == propiedad.get("zona")), None)
    imagen = propiedad.get("imagen")
    return {
        "negocio": {"nombre": _texto(nombre_negocio, 120)},
        "zona": {"nombre": _texto(zona.get("nombre"), 120)} if zona else None,
        "casa": {
            "id": propiedad["id"],
            "nombre": _texto(propiedad.get("nombre"), 120),
            "direccion": _texto(propiedad.get("direccion"), 200),
            "descripcion": _texto(propiedad.get("descripcion"), 2000),
            "precio": _numero(propiedad.get("precio")),
            "recamaras": _numero(propiedad.get("recamaras")),
            "banos": _numero(propiedad.get("banos")),
            "estacionamientos": _numero(propiedad.get("estacionamientos")),
            "m2_construccion": _numero(propiedad.get("m2_construccion")),
            "m2_terreno": _numero(propiedad.get("m2_terreno")),
            "entrega": propiedad.get("entrega") if propiedad.get("entrega") in ("inmediata", "preventa") else "",
            "estatus": propiedad.get("estatus") if propiedad.get("estatus") in ("disponible", "apartado", "vendido") else "",
            "imagen": imagen if isinstance(imagen, str) and RE_ID_FOTO.fullmatch(imagen) else "",
        },
    }


@router.get("/publico/{token}/casa/{casa_id}")
def casa_publica(token: str, casa_id: str):
    # Cualquier fallo (token, casa o formato) da el mismo mensaje: no se revela qué parte estaba mal.
    if not RE_TOKEN.fullmatch(token) or not RE_ID_CASA.fullmatch(casa_id):
        raise HTTPException(404, NO_ENCONTRADA)
    with db.conexion() as con:        # sin negocio en la sesión: la función de la base exige el token
        fila = con.execute("SELECT * FROM config_publica(%s)", (token,)).fetchone()
    if not fila:
        raise HTTPException(404, NO_ENCONTRADA)
    datos = fila["datos"] if isinstance(fila["datos"], dict) else {}
    propiedad = next((p for p in datos.get("propiedades", []) if isinstance(p, dict) and p.get("id") == casa_id), None)
    if not propiedad:
        raise HTTPException(404, NO_ENCONTRADA)
    negocio = datos.get("negocio") if isinstance(datos.get("negocio"), dict) else {}
    return JSONResponse(ficha(propiedad, datos.get("zonas", []) if isinstance(datos.get("zonas"), list) else [],
                              negocio.get("nombre") or fila["nombre"]),
                        headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})


@router.post("/publico/rotar")
def rotar_enlace(ident=Depends(solo_admin)):
    """Si un QR se filtró o se imprimió de más: genera un token nuevo y los enlaces anteriores dejan de funcionar."""
    nuevo = secrets.token_hex(16)
    with db.transaccion(ident.negocio_id) as con:
        con.execute("UPDATE negocios SET token_publico = %s WHERE id = %s", (nuevo, ident.negocio_id))
    return {"ok": True, "token_publico": nuevo}
