"""Fotos del catálogo (inmuebles).

El gestor de Windows las sube al publicar la configuración y los celulares las ven con una etiqueta <img>, que no puede
mandar credenciales: por eso la lectura es pública, pero solo por identificador (128 bits al azar: no se puede adivinar)
y solo para fotos del catálogo. Las fotos de las visitas siguen en /api/archivos, con sesión.
"""
import re
import uuid

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import almacen as alm
from . import db
from .auth import requiere

router = APIRouter(prefix="/api")
solo_admin = requiere("admin")
RE_ID = re.compile(r"[0-9a-f]{32}")
MAX_BYTES_FOTO = 4 * 1024 * 1024          # lo mismo que acepta el gestor de Windows
MAX_IDS = 500


def _uuid(id_):
    if not RE_ID.fullmatch(id_ or ""):
        raise HTTPException(404, "No encontramos esa foto.")
    return uuid.UUID(hex=id_)


@router.get("/imagen/{id_}")
def ver_foto(id_: str, request: Request):
    u = _uuid(id_)
    with db.conexion() as con:             # sin negocio en la sesión: la función de la base solo dice dónde está el archivo
        fila = con.execute("SELECT * FROM imagen_catalogo(%s)", (u,)).fetchone()
    contenido = request.app.state.almacen.leer(fila["nombre_blob"]) if fila else None
    if contenido is None:
        raise HTTPException(404, "No encontramos esa foto.")
    # El identificador cambia cuando cambia la foto, así que el contenido de una dirección nunca cambia.
    return Response(contenido, media_type=fila["tipo"],
                    headers={"Cache-Control": "public, max-age=86400, immutable", "X-Content-Type-Options": "nosniff"})


def _guardar(request, ident, u, datos, tipo):
    almacen = request.app.state.almacen
    nombre_blob = f"{ident.negocio_id}/catalogo/{u.hex}{alm.TIPOS[tipo]}"
    with db.transaccion(ident.negocio_id) as con:
        if con.execute("SELECT 1 AS ok FROM archivos WHERE negocio_id = %s AND id = %s AND proposito = 'catalogo'",
                       (ident.negocio_id, u)).fetchone():
            return {"ok": True, "existia": True}
    almacen.guardar_como(nombre_blob, datos, tipo)
    try:
        with db.transaccion(ident.negocio_id) as con:
            con.execute("INSERT INTO archivos (negocio_id, id, nombre_blob, tipo, bytes, subido_por, proposito) VALUES (%s, %s, %s, %s, %s, %s, 'catalogo')",
                        (ident.negocio_id, u, nombre_blob, tipo, len(datos), ident.usuario_id))
            con.execute(
                """INSERT INTO uso_mensual (negocio_id, periodo, archivos_bytes)
                   VALUES (%s, date_trunc('month', now() AT TIME ZONE 'UTC')::date, %s)
                   ON CONFLICT (negocio_id, periodo) DO UPDATE SET archivos_bytes = uso_mensual.archivos_bytes + excluded.archivos_bytes""",
                (ident.negocio_id, len(datos)))
    except psycopg.errors.UniqueViolation:
        almacen.borrar(nombre_blob)
        raise HTTPException(409, "Ese identificador de foto ya está en uso.")
    except Exception:
        almacen.borrar(nombre_blob)
        raise
    return {"ok": True, "existia": False}


@router.put("/imagen/{id_}")
async def subir_foto(id_: str, request: Request, ident=Depends(solo_admin)):
    """El gestor sube una foto con el mismo identificador que tiene en su catálogo. Repetirlo no hace nada."""
    u = _uuid(id_)
    if int(request.headers.get("content-length") or 0) > MAX_BYTES_FOTO:
        raise HTTPException(413, "La foto pasa de 4 MB.")
    datos = await request.body()
    if len(datos) > MAX_BYTES_FOTO:
        raise HTTPException(413, "La foto pasa de 4 MB.")
    tipo = alm.tipo_por_contenido(datos)
    if not tipo:
        raise HTTPException(415, "Solo se aceptan imágenes JPG, PNG o WebP.")
    return await run_in_threadpool(_guardar, request, ident, u, datos, tipo)


class Conjunto(BaseModel):
    ids: list[str] = Field(max_length=MAX_IDS)


@router.post("/imagenes/sincronizar")
def sincronizar(request: Request, conjunto: Conjunto, ident=Depends(solo_admin)):
    """El catálogo del gestor es la verdad: devuelve qué fotos de la lista faltan en la nube y borra las que ya no están en
    la lista (fotos que el gestor descartó). Solo toca fotos del catálogo de este negocio."""
    pedidas = set()
    for i in conjunto.ids:
        if not RE_ID.fullmatch(i):
            raise HTTPException(422, "Identificador de foto no válido.")
        pedidas.add(i)
    almacen = request.app.state.almacen
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute("SELECT id, nombre_blob FROM archivos WHERE negocio_id = %s AND proposito = 'catalogo'", (ident.negocio_id,)).fetchall()
        sobran = [f for f in filas if f["id"].hex not in pedidas]
        for f in sobran:
            con.execute("DELETE FROM archivos WHERE negocio_id = %s AND id = %s", (ident.negocio_id, f["id"]))
        tienen = {f["id"].hex for f in filas}
    for f in sobran:
        almacen.borrar(f["nombre_blob"])
    return {"faltan": sorted(pedidas - tienen), "borradas": len(sobran)}
