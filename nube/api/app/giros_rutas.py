"""Rutas de los giros: sus registros (registros_giro) y, para inmobiliarias, las visitas a propiedades.

Las visitas son actividades del núcleo (tipo «visita») que apuntan a una propiedad del giro en su campo datos; así la
ficha del contacto o de la oportunidad las muestra junto con las demás actividades.
"""
import json
import uuid
from datetime import datetime

import psycopg
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import almacen as alm
from . import db, giros
from .nucleo import _historial, ambos_roles, solo_admin
from .rutas import _sumar_uso

router = APIRouter(prefix="/api")


class NuevoRegistro(BaseModel):
    tipo: str = Field(min_length=1, max_length=40)
    datos: dict = Field(default_factory=dict)
    estado: str | None = Field(default=None, max_length=40)
    contacto_id: uuid.UUID | None = None
    oportunidad_id: uuid.UUID | None = None


class CambioRegistro(BaseModel):
    datos: dict | None = None
    estado: str | None = Field(default=None, max_length=40)
    contacto_id: uuid.UUID | None = None
    oportunidad_id: uuid.UUID | None = None


class NuevaVisita(BaseModel):
    propiedad_id: uuid.UUID
    contacto_id: uuid.UUID | None = None
    oportunidad_id: uuid.UUID | None = None
    ocurrido: datetime | None = None
    duracion_min: int | None = Field(default=None, ge=0, le=1440)
    texto: str = Field(default="", max_length=4000)


def _giro_o_404(giro):
    if not giros.existe(giro):
        raise HTTPException(404, "Ese giro no existe.")


def _json(valor):
    return json.dumps(valor, ensure_ascii=False, sort_keys=True)


@router.get("/giros")
def listar_giros(ident=Depends(ambos_roles)):
    """Los giros disponibles y sus tipos de registro. El celular y el panel lo usan para saber qué mostrar."""
    return {"giros": [{"giro": g, "tipos": giros.tipos(g)} for g in sorted(giros.TIPOS)]}


@router.get("/giros/{giro}/registros")
def listar_registros(giro: str, tipo: str | None = None, estado: str | None = None,
                     contacto_id: uuid.UUID | None = None, oportunidad_id: uuid.UUID | None = None,
                     limite: int = Query(100, ge=1, le=500), ident=Depends(ambos_roles)):
    _giro_o_404(giro)
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT * FROM registros_giro
               WHERE giro = %s
                 AND (%s::text IS NULL OR tipo = %s::text)
                 AND (%s::text IS NULL OR estado = %s::text)
                 AND (%s::uuid IS NULL OR contacto_id = %s::uuid)
                 AND (%s::uuid IS NULL OR oportunidad_id = %s::uuid)
               ORDER BY creado DESC LIMIT %s""",
            (giro, tipo, tipo, estado, estado, contacto_id, contacto_id, oportunidad_id, oportunidad_id, limite)).fetchall()
    return {"registros": filas}


@router.post("/giros/{giro}/registros")
def crear_registro(giro: str, nuevo: NuevoRegistro, ident=Depends(ambos_roles)):
    _giro_o_404(giro)
    try:
        if giros.requiere_admin(giro, nuevo.tipo) and ident.rol != "admin":
            raise HTTPException(403, "Solo el administrador puede registrar este tipo.")
        with db.transaccion(ident.negocio_id) as con:
            datos, estado = giros.validar_registro(
                con, giro, nuevo.tipo, nuevo.datos, nuevo.estado,
                contexto={"negocio_id": ident.negocio_id, "usuario_id": uuid.UUID(ident.usuario_id)}, crear=True)
            fila = con.execute(
                """INSERT INTO registros_giro (negocio_id, giro, tipo, contacto_id, oportunidad_id, datos, estado)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (ident.negocio_id, giro, nuevo.tipo, nuevo.contacto_id, nuevo.oportunidad_id, Jsonb(datos), estado)
            ).fetchone()
    except giros.ErrorGiro as e:
        raise HTTPException(422, str(e))
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "El contacto u oportunidad no existe en tu negocio.")
    return {"registro": fila}


@router.get("/giros/{giro}/registros/{registro_id}")
def ficha_registro(giro: str, registro_id: uuid.UUID, ident=Depends(ambos_roles)):
    _giro_o_404(giro)
    with db.transaccion(ident.negocio_id) as con:
        fila = con.execute("SELECT * FROM registros_giro WHERE id = %s AND giro = %s", (registro_id, giro)).fetchone()
        if not fila:
            raise HTTPException(404, "No encontramos ese registro.")
        historial = con.execute(
            "SELECT * FROM historial WHERE entidad = 'registro_giro' AND entidad_id = %s ORDER BY cuando DESC LIMIT 100",
            (str(registro_id),)).fetchall()
    return {"registro": fila, "historial": historial}


@router.patch("/giros/{giro}/registros/{registro_id}")
def actualizar_registro(giro: str, registro_id: uuid.UUID, cambio: CambioRegistro, ident=Depends(ambos_roles)):
    """Los datos se reemplazan completos: se validan de nuevo como un todo antes de guardar."""
    _giro_o_404(giro)
    cambios = cambio.model_dump(exclude_unset=True)
    for campo in ("datos", "estado"):
        if campo in cambios and cambios[campo] is None:
            raise HTTPException(422, f"{campo} no puede quedar vacío.")
    try:
        with db.transaccion(ident.negocio_id) as con:
            actual = con.execute("SELECT * FROM registros_giro WHERE id = %s AND giro = %s FOR UPDATE",
                                 (registro_id, giro)).fetchone()
            if not actual:
                raise HTTPException(404, "No encontramos ese registro.")
            if not giros.editable(giro, actual["tipo"]):
                raise HTTPException(422, "Este registro no se corrige. Regístralo de nuevo.")
            if not cambios:
                return {"registro": actual}
            datos, estado = giros.validar_registro(con, giro, actual["tipo"],
                                                   cambios.get("datos", actual["datos"]),
                                                   cambios.get("estado", actual["estado"]))
            # Lo que queda después del cambio, y lo que había antes. Solo se registra en historial lo que cambió de verdad.
            despues = {"datos": _json(datos), "estado": estado,
                       "contacto_id": cambios.get("contacto_id", actual["contacto_id"]),
                       "oportunidad_id": cambios.get("oportunidad_id", actual["oportunidad_id"])}
            antes = {"datos": _json(actual["datos"]), "estado": actual["estado"],
                     "contacto_id": actual["contacto_id"], "oportunidad_id": actual["oportunidad_id"]}
            _historial(con, ident.negocio_id, "registro_giro", registro_id, antes, despues, uuid.UUID(ident.usuario_id))
            fila = con.execute(
                """UPDATE registros_giro SET datos = %s, estado = %s, contacto_id = %s, oportunidad_id = %s,
                       actualizado = now()
                   WHERE id = %s AND giro = %s RETURNING *""",
                (Jsonb(datos), estado, despues["contacto_id"], despues["oportunidad_id"], registro_id, giro)).fetchone()
    except giros.ErrorGiro as e:
        raise HTTPException(422, str(e))
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "El contacto u oportunidad no existe en tu negocio.")
    return {"registro": fila}


# --- Visitas a propiedades (inmobiliarias) -----------------------------------------------------------------------------

@router.post("/giros/inmobiliarias/visitas")
def registrar_visita(nueva: NuevaVisita, ident=Depends(ambos_roles)):
    if nueva.contacto_id is None and nueva.oportunidad_id is None:
        raise HTTPException(422, "La visita necesita un contacto o una oportunidad.")
    try:
        with db.transaccion(ident.negocio_id) as con:
            if not giros.propiedad(con, nueva.propiedad_id):
                raise HTTPException(422, "La propiedad no existe en tu negocio.")
            fila = con.execute(
                """INSERT INTO actividades (negocio_id, tipo, contacto_id, oportunidad_id, responsable_id, ocurrido,
                       duracion_min, texto, datos)
                   VALUES (%s, 'visita', %s, %s, %s, COALESCE(%s, now()), %s, %s, %s) RETURNING *""",
                (ident.negocio_id, nueva.contacto_id, nueva.oportunidad_id, uuid.UUID(ident.usuario_id),
                 nueva.ocurrido, nueva.duracion_min, nueva.texto.strip(),
                 Jsonb({"giro": giros.INMOBILIARIAS, "propiedad_id": str(nueva.propiedad_id)}))).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "El contacto u oportunidad no existe en tu negocio.")
    return {"visita": fila}


@router.get("/giros/inmobiliarias/visitas")
def listar_visitas(propiedad_id: uuid.UUID | None = None, limite: int = Query(100, ge=1, le=500),
                   ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT * FROM actividades
               WHERE tipo = 'visita' AND datos->>'giro' = 'inmobiliarias'
                 AND (%s::text IS NULL OR datos->>'propiedad_id' = %s::text)
               ORDER BY ocurrido DESC LIMIT %s""",
            (str(propiedad_id) if propiedad_id else None, str(propiedad_id) if propiedad_id else None, limite)).fetchall()
    return {"visitas": filas}


# --- Fotos de un registro del giro (taller: recepción, avance, entrega) ----------------------------------------------

@router.post("/giros/{giro}/registros/{registro_id}/fotos")
def subir_foto(giro: str, registro_id: uuid.UUID, request: Request, etapa: str = Query(..., max_length=20),
               archivo: UploadFile = File(...), ident=Depends(ambos_roles)):
    _giro_o_404(giro)
    if etapa not in giros.ETAPAS_FOTO:
        raise HTTPException(422, f"La etapa debe ser una de: {', '.join(giros.ETAPAS_FOTO)}.")
    datos = archivo.file.read(alm.MAX_BYTES + 1)
    if len(datos) > alm.MAX_BYTES:
        raise HTTPException(413, "El archivo pasa de 5 MB.")
    tipo = alm.tipo_por_contenido(datos)
    if not tipo:
        raise HTTPException(415, "Solo se aceptan imágenes JPG, PNG o WebP.")
    almacen = request.app.state.almacen
    nombre_blob = almacen.guardar(ident.negocio_id, datos, tipo)
    try:
        with db.transaccion(ident.negocio_id) as con:
            if not con.execute("SELECT 1 FROM registros_giro WHERE id = %s AND giro = %s", (registro_id, giro)).fetchone():
                raise HTTPException(404, "No encontramos ese registro.")
            cuantas = con.execute("SELECT count(*) AS n FROM archivos WHERE registro_giro_id = %s", (registro_id,)).fetchone()["n"]
            if cuantas >= giros.MAX_FOTOS_REGISTRO:
                raise HTTPException(422, f"Este registro ya tiene {giros.MAX_FOTOS_REGISTRO} fotos.")
            fila = con.execute(
                """INSERT INTO archivos (negocio_id, registro_giro_id, etiqueta, nombre_blob, tipo, bytes, subido_por)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (ident.negocio_id, registro_id, etapa, nombre_blob, tipo, len(datos), ident.usuario_id)).fetchone()
            _sumar_uso(con, ident.negocio_id, archivos_bytes=len(datos))
    except Exception:
        almacen.borrar(nombre_blob)   # no dejar una foto huérfana si la base la rechazó
        raise
    return {"id": str(fila["id"]), "etapa": etapa, "tipo": tipo, "bytes": len(datos)}


@router.get("/giros/{giro}/registros/{registro_id}/fotos")
def listar_fotos(giro: str, registro_id: uuid.UUID, ident=Depends(ambos_roles)):
    _giro_o_404(giro)
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            "SELECT id, etiqueta AS etapa, tipo, bytes, subido FROM archivos WHERE registro_giro_id = %s ORDER BY subido",
            (registro_id,)).fetchall()
    return {"fotos": filas}


# --- Anulación de ventas (solo administrador) ----------------------------------------------------------------------------

class AnulacionVenta(BaseModel):
    motivo: str = Field(min_length=1, max_length=300)


@router.post("/giros/venta/registros/{registro_id}/anular")
def anular_venta_ruta(registro_id: uuid.UUID, anulacion: AnulacionVenta, ident=Depends(solo_admin)):
    """La venta pasa a anulada y su existencia vuelve al inventario. Un motivo es obligatorio y una venta se anula una vez."""
    motivo = anulacion.motivo.strip()
    if len(motivo) < 5:
        raise HTTPException(422, "El motivo debe tener al menos 5 caracteres.")
    with db.transaccion(ident.negocio_id) as con:
        actual = con.execute("SELECT * FROM registros_giro WHERE id = %s AND giro = 'venta' FOR UPDATE",
                             (registro_id,)).fetchone()
        if not actual:
            raise HTTPException(404, "No encontramos esa venta.")
        try:
            nuevos_datos = giros.anular_venta(
                con, actual, motivo, {"negocio_id": ident.negocio_id, "usuario_id": uuid.UUID(ident.usuario_id)})
        except giros.ErrorGiro as e:
            raise HTTPException(422, str(e))
        fila = con.execute(
            "UPDATE registros_giro SET datos = %s, estado = 'anulada', actualizado = now() WHERE id = %s RETURNING *",
            (Jsonb(nuevos_datos), registro_id)).fetchone()
    return {"registro": fila}
