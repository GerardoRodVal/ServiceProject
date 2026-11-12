"""Núcleo del CRM (migración 009): contactos, pipelines y etapas, oportunidades y actividades.

Es lo que sirve a cualquier giro. Un giro guarda sus propios registros (propiedades, vehículos, ventas…) y se apoya aquí
por contacto_id y oportunidad_id; los datos de contacto nunca se copian a un giro. Cada cambio de un contacto o de una
oportunidad deja su rastro en historial (campo por campo). Las actividades son un registro histórico: se agregan, no se editan.
"""
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import db, giros, visitas_nucleo
from .auth import requiere

router = APIRouter(prefix="/api")
ambos_roles = requiere("admin", "capturista")
solo_admin = requiere("admin")

TIPOS_CONTACTO = ("persona", "empresa")
TIPOS_ETAPA = ("abierta", "ganada", "perdida")
TIPOS_ACTIVIDAD = ("nota", "llamada", "mensaje", "reunion", "visita", "cita", "sistema")
MAX_DATOS_GIRO = 16 * 1024
MAX_ETAPAS = 12
SIN_EXISTENCIA = "no existe en tu negocio"


# --- Modelos de entrada -------------------------------------------------------------------------------------------------

class NuevoContacto(BaseModel):
    tipo: Literal["persona", "empresa"] = "persona"
    nombre: str = Field(min_length=1, max_length=120)
    telefono: str = Field(default="", max_length=30)
    correo: str = Field(default="", max_length=200)
    origen: str = Field(default="", max_length=60)
    responsable_id: uuid.UUID | None = None
    etiquetas: list[str] = Field(default_factory=list, max_length=20)


class CambioContacto(BaseModel):
    tipo: Literal["persona", "empresa"] | None = None
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    telefono: str | None = Field(default=None, max_length=30)
    correo: str | None = Field(default=None, max_length=200)
    origen: str | None = Field(default=None, max_length=60)
    responsable_id: uuid.UUID | None = None
    etiquetas: list[str] | None = Field(default=None, max_length=20)


class EtapaNueva(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    tipo: Literal["abierta", "ganada", "perdida"] = "abierta"


class NuevoPipeline(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    giro: str = Field(default="", max_length=40)
    por_defecto: bool = False
    etapas: list[EtapaNueva] = Field(min_length=2, max_length=MAX_ETAPAS)


class NuevaOportunidad(BaseModel):
    contacto_id: uuid.UUID
    pipeline_id: uuid.UUID | None = None
    etapa_id: uuid.UUID | None = None
    giro: str = Field(default="", max_length=40)
    valor: Decimal = Field(default=Decimal(0), ge=0, le=10**12)
    probabilidad: int | None = Field(default=None, ge=0, le=100)
    cierre_estimado: date | None = None
    responsable_id: uuid.UUID | None = None
    datos_giro: dict = Field(default_factory=dict)


class CambioOportunidad(BaseModel):
    etapa_id: uuid.UUID | None = None
    valor: Decimal | None = Field(default=None, ge=0, le=10**12)
    probabilidad: int | None = Field(default=None, ge=0, le=100)
    cierre_estimado: date | None = None
    responsable_id: uuid.UUID | None = None
    datos_giro: dict | None = None
    motivo_cierre: str | None = Field(default=None, max_length=300)


class NuevaActividad(BaseModel):
    tipo: Literal["nota", "llamada", "mensaje", "reunion", "visita", "cita", "sistema"]
    contacto_id: uuid.UUID | None = None
    oportunidad_id: uuid.UUID | None = None
    texto: str = Field(default="", max_length=4000)
    ocurrido: datetime | None = None
    duracion_min: int | None = Field(default=None, ge=0, le=100000)


# --- Ayudas ------------------------------------------------------------------------------------------------------------

def _sin_existencia(nombre):
    return HTTPException(422, f"{nombre} {SIN_EXISTENCIA}.")


def _responsable(con, responsable_id):
    if responsable_id is None:
        return None
    if not con.execute("SELECT 1 FROM usuarios WHERE id = %s", (responsable_id,)).fetchone():
        raise _sin_existencia("El responsable")
    return responsable_id


def _historial(con, negocio_id, entidad, entidad_id, antes, despues, usuario_id):
    """Una fila por campo que cambió. Se compara como texto para que 10 y «10» no cuenten como cambio."""
    for campo, nuevo in despues.items():
        viejo = antes.get(campo)
        if str(viejo) == str(nuevo):
            continue
        con.execute(
            "INSERT INTO historial (negocio_id, entidad, entidad_id, campo, antes, despues, usuario_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (negocio_id, entidad, str(entidad_id), campo,
             None if viejo is None else str(viejo), None if nuevo is None else str(nuevo), usuario_id))


def _datos_giro(datos):
    if len(json.dumps(datos, ensure_ascii=False)) > MAX_DATOS_GIRO:
        raise HTTPException(422, "Los datos del giro pasan de 16 KB.")
    return Jsonb(datos)


def _ilike(texto):
    escapado = texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escapado}%"


def _etapa_de(con, pipeline_id, etapa_id):
    """La etapa debe existir y pertenecer al pipeline de la oportunidad. Devuelve su tipo."""
    fila = con.execute("SELECT pipeline_id, tipo FROM etapas WHERE id = %s", (etapa_id,)).fetchone()
    if not fila:
        raise _sin_existencia("La etapa")
    if fila["pipeline_id"] != pipeline_id:
        raise HTTPException(422, "La etapa no pertenece al pipeline de la oportunidad.")
    return fila["tipo"]


def _marca_de_cierre(tipo):
    return datetime.now(timezone.utc) if tipo in ("ganada", "perdida") else None


# --- Contactos ---------------------------------------------------------------------------------------------------------

@router.get("/contactos")
def listar_contactos(q: str = "", limite: int = Query(50, ge=1, le=200), ident=Depends(ambos_roles)):
    q = q.strip()
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT * FROM contactos
               WHERE %s = '' OR nombre ILIKE %s OR telefono LIKE %s
               ORDER BY actualizado DESC LIMIT %s""",
            (q, _ilike(q), _ilike(q), limite)).fetchall()
    return {"contactos": filas}


@router.post("/contactos")
def crear_contacto(nuevo: NuevoContacto, ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        responsable = _responsable(con, nuevo.responsable_id or uuid.UUID(ident.usuario_id))
        fila = con.execute(
            """INSERT INTO contactos (negocio_id, tipo, nombre, telefono, correo, origen, responsable_id, etiquetas)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (ident.negocio_id, nuevo.tipo, nuevo.nombre.strip(), nuevo.telefono.strip(), nuevo.correo.strip(),
             nuevo.origen.strip(), responsable, nuevo.etiquetas)).fetchone()
    return {"contacto": fila}


@router.get("/contactos/{contacto_id}")
def ficha_contacto(contacto_id: uuid.UUID, ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        contacto = con.execute("SELECT * FROM contactos WHERE id = %s", (contacto_id,)).fetchone()
        if not contacto:
            raise HTTPException(404, "No encontramos a ese contacto.")
        oportunidades = con.execute(
            "SELECT * FROM oportunidades WHERE contacto_id = %s ORDER BY creado DESC", (contacto_id,)).fetchall()
        actividades = con.execute(
            "SELECT * FROM actividades WHERE contacto_id = %s ORDER BY ocurrido DESC LIMIT 50", (contacto_id,)).fetchall()
    return {"contacto": contacto, "oportunidades": oportunidades, "actividades": actividades}


@router.patch("/contactos/{contacto_id}")
def actualizar_contacto(contacto_id: uuid.UUID, cambio: CambioContacto, ident=Depends(ambos_roles)):
    cambios = cambio.model_dump(exclude_unset=True)
    # Todo salvo el responsable es obligatorio en la base: mandarlo vacío no es un cambio válido.
    for campo in ("tipo", "nombre", "telefono", "correo", "origen", "etiquetas"):
        if campo in cambios and cambios[campo] is None:
            raise HTTPException(422, f"{campo} no puede quedar vacío.")
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()
        if not cambios["nombre"]:
            raise HTTPException(422, "El nombre no puede quedar vacío.")
    with db.transaccion(ident.negocio_id) as con:
        actual = con.execute("SELECT * FROM contactos WHERE id = %s FOR UPDATE", (contacto_id,)).fetchone()
        if not actual:
            raise HTTPException(404, "No encontramos a ese contacto.")
        if "responsable_id" in cambios:
            cambios["responsable_id"] = _responsable(con, cambios["responsable_id"])
        if not cambios:
            return {"contacto": actual}
        _historial(con, ident.negocio_id, "contacto", contacto_id,
                   {k: actual[k] for k in cambios}, cambios, uuid.UUID(ident.usuario_id))
        columnas = list(cambios)
        fila = con.execute(
            f"UPDATE contactos SET {', '.join(f'{c} = %s' for c in columnas)}, actualizado = now() "
            "WHERE id = %s RETURNING *",
            [cambios[c] for c in columnas] + [contacto_id]).fetchone()
    return {"contacto": fila}


# --- Pipelines y etapas ------------------------------------------------------------------------------------------------

def _con_etapas(con, pipelines):
    etapas = con.execute("SELECT * FROM etapas ORDER BY pipeline_id, orden").fetchall()
    por_pipeline = {}
    for e in etapas:
        por_pipeline.setdefault(e["pipeline_id"], []).append(e)
    return [{**p, "etapas": por_pipeline.get(p["id"], [])} for p in pipelines]


@router.get("/pipelines")
def listar_pipelines(ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        pipelines = con.execute("SELECT * FROM pipelines ORDER BY por_defecto DESC, nombre").fetchall()
        return {"pipelines": _con_etapas(con, pipelines)}


@router.post("/pipelines")
def crear_pipeline(nuevo: NuevoPipeline, ident=Depends(solo_admin)):
    if not any(e.tipo == "abierta" for e in nuevo.etapas):
        raise HTTPException(422, "El pipeline necesita al menos una etapa abierta.")
    if len({e.nombre.strip().lower() for e in nuevo.etapas}) != len(nuevo.etapas):
        raise HTTPException(422, "Las etapas no pueden repetir nombre.")
    with db.transaccion(ident.negocio_id) as con:
        if nuevo.por_defecto:
            # Antes de insertar: solo puede haber un pipeline por defecto por negocio.
            con.execute("UPDATE pipelines SET por_defecto = false WHERE por_defecto")
        pipeline = con.execute(
            "INSERT INTO pipelines (negocio_id, nombre, giro, por_defecto) VALUES (%s, %s, %s, %s) RETURNING *",
            (ident.negocio_id, nuevo.nombre.strip(), nuevo.giro.strip(), nuevo.por_defecto)).fetchone()
        for orden, etapa in enumerate(nuevo.etapas):
            con.execute(
                "INSERT INTO etapas (negocio_id, pipeline_id, nombre, orden, tipo) VALUES (%s, %s, %s, %s, %s)",
                (ident.negocio_id, pipeline["id"], etapa.nombre.strip(), orden, etapa.tipo))
        return {"pipeline": _con_etapas(con, [pipeline])[0]}


# --- Oportunidades -----------------------------------------------------------------------------------------------------

@router.get("/oportunidades")
def listar_oportunidades(contacto_id: uuid.UUID | None = None, etapa_id: uuid.UUID | None = None,
                         limite: int = Query(100, ge=1, le=500), ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT * FROM oportunidades
               WHERE (%s::uuid IS NULL OR contacto_id = %s::uuid) AND (%s::uuid IS NULL OR etapa_id = %s::uuid)
               ORDER BY actualizado DESC LIMIT %s""",
            (contacto_id, contacto_id, etapa_id, etapa_id, limite)).fetchall()
    return {"oportunidades": filas}


@router.post("/oportunidades")
def crear_oportunidad(nueva: NuevaOportunidad, ident=Depends(ambos_roles)):
    try:
        with db.transaccion(ident.negocio_id) as con:
            if not con.execute("SELECT 1 FROM contactos WHERE id = %s", (nueva.contacto_id,)).fetchone():
                raise _sin_existencia("El contacto")
            if nueva.pipeline_id is None:
                defecto = con.execute("SELECT id FROM pipelines WHERE por_defecto").fetchone()
                if not defecto:
                    raise HTTPException(422, "Crea primero un pipeline o indica pipeline_id.")
                pipeline_id = defecto["id"]
            else:
                if not con.execute("SELECT 1 FROM pipelines WHERE id = %s", (nueva.pipeline_id,)).fetchone():
                    raise _sin_existencia("El pipeline")
                pipeline_id = nueva.pipeline_id
            if nueva.etapa_id is None:
                inicial = con.execute(
                    "SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND tipo = 'abierta' ORDER BY orden LIMIT 1",
                    (pipeline_id,)).fetchone()
                if not inicial:
                    raise HTTPException(422, "El pipeline no tiene una etapa abierta.")
                etapa_id, tipo = inicial["id"], inicial["tipo"]
            else:
                etapa_id = nueva.etapa_id
                tipo = _etapa_de(con, pipeline_id, etapa_id)
            datos = giros.validar_oportunidad(con, nueva.giro.strip(), nueva.datos_giro)
            fila = con.execute(
                """INSERT INTO oportunidades (negocio_id, contacto_id, pipeline_id, etapa_id, giro, valor, probabilidad,
                       cierre_estimado, responsable_id, datos_giro, cerrado_en)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
                (ident.negocio_id, nueva.contacto_id, pipeline_id, etapa_id, nueva.giro.strip(), nueva.valor,
                 nueva.probabilidad, nueva.cierre_estimado,
                 _responsable(con, nueva.responsable_id or uuid.UUID(ident.usuario_id)),
                 _datos_giro(datos), _marca_de_cierre(tipo))).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "Algún dato ligado no existe en tu negocio.")
    except giros.ErrorGiro as e:
        raise HTTPException(422, str(e))
    return {"oportunidad": fila}


@router.get("/oportunidades/{oportunidad_id}")
def ficha_oportunidad(oportunidad_id: uuid.UUID, ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        fila = con.execute("SELECT * FROM oportunidades WHERE id = %s", (oportunidad_id,)).fetchone()
        if not fila:
            raise HTTPException(404, "No encontramos esa oportunidad.")
        actividades = con.execute(
            "SELECT * FROM actividades WHERE oportunidad_id = %s ORDER BY ocurrido DESC LIMIT 50", (oportunidad_id,)).fetchall()
        historial = con.execute(
            "SELECT * FROM historial WHERE entidad = 'oportunidad' AND entidad_id = %s ORDER BY cuando DESC LIMIT 100",
            (str(oportunidad_id),)).fetchall()
    return {"oportunidad": fila, "actividades": actividades, "historial": historial}


@router.patch("/oportunidades/{oportunidad_id}")
def actualizar_oportunidad(oportunidad_id: uuid.UUID, cambio: CambioOportunidad, ident=Depends(ambos_roles)):
    cambios = cambio.model_dump(exclude_unset=True)
    no_nulos = ("etapa_id", "valor", "datos_giro", "motivo_cierre")
    for campo in no_nulos:
        if campo in cambios and cambios[campo] is None:
            raise HTTPException(422, f"{campo} no puede quedar vacío.")
    with db.transaccion(ident.negocio_id) as con:
        actual = con.execute("SELECT * FROM oportunidades WHERE id = %s FOR UPDATE", (oportunidad_id,)).fetchone()
        if not actual:
            raise HTTPException(404, "No encontramos esa oportunidad.")
        upd = dict(cambios)
        if "etapa_id" in cambios:
            tipo = _etapa_de(con, actual["pipeline_id"], cambios["etapa_id"])
            upd["cerrado_en"] = _marca_de_cierre(tipo)   # reabrir la oportunidad la vuelve a dejar sin cerrar
        if "responsable_id" in cambios:
            upd["responsable_id"] = _responsable(con, cambios["responsable_id"])
        if "datos_giro" in cambios:
            try:
                cambios["datos_giro"] = giros.validar_oportunidad(con, actual["giro"], cambios["datos_giro"])
            except giros.ErrorGiro as e:
                raise HTTPException(422, str(e))
            upd["datos_giro"] = _datos_giro(cambios["datos_giro"])
        if not cambios:
            return {"oportunidad": actual}
        # Solo lo que el usuario cambió queda en el historial (la fecha de cierre es consecuencia de la etapa).
        historial_antes = {k: actual[k] for k in cambios}
        _historial(con, ident.negocio_id, "oportunidad", oportunidad_id, historial_antes,
                   {k: v for k, v in cambios.items() if k != "datos_giro"}, uuid.UUID(ident.usuario_id))
        if "datos_giro" in cambios:
            _historial(con, ident.negocio_id, "oportunidad", oportunidad_id,
                       {"datos_giro": json.dumps(actual["datos_giro"], ensure_ascii=False, sort_keys=True)},
                       {"datos_giro": json.dumps(cambios["datos_giro"], ensure_ascii=False, sort_keys=True)},
                       uuid.UUID(ident.usuario_id))
        columnas = list(upd)
        fila = con.execute(
            f"UPDATE oportunidades SET {', '.join(f'{c} = %s' for c in columnas)}, actualizado = now() "
            "WHERE id = %s RETURNING *",
            [upd[c] for c in columnas] + [oportunidad_id]).fetchone()
        if "etapa_id" in cambios:
            visitas_nucleo.estado_de_visitas(con, oportunidad_id)   # sus visitas muestran la misma etapa
    return {"oportunidad": fila}


# --- Actividades -------------------------------------------------------------------------------------------------------

@router.get("/actividades")
def listar_actividades(contacto_id: uuid.UUID | None = None, oportunidad_id: uuid.UUID | None = None,
                       limite: int = Query(50, ge=1, le=200), ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT * FROM actividades
               WHERE (%s::uuid IS NULL OR contacto_id = %s::uuid) AND (%s::uuid IS NULL OR oportunidad_id = %s::uuid)
               ORDER BY ocurrido DESC LIMIT %s""",
            (contacto_id, contacto_id, oportunidad_id, oportunidad_id, limite)).fetchall()
    return {"actividades": filas}


@router.post("/actividades")
def registrar_actividad(nueva: NuevaActividad, ident=Depends(ambos_roles)):
    try:
        with db.transaccion(ident.negocio_id) as con:
            fila = con.execute(
                """INSERT INTO actividades (negocio_id, tipo, contacto_id, oportunidad_id, responsable_id, ocurrido,
                       duracion_min, texto)
                   VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s) RETURNING *""",
                (ident.negocio_id, nueva.tipo, nueva.contacto_id, nueva.oportunidad_id, uuid.UUID(ident.usuario_id),
                 nueva.ocurrido, nueva.duracion_min, nueva.texto.strip())).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "El contacto u oportunidad no existe en tu negocio.")
    return {"actividad": fila}


# --- Tareas: recordatorios con fecha. No son las líneas de servicio del taller (eso es del giro). ----------------------

class NuevaTarea(BaseModel):
    titulo: str = Field(min_length=1, max_length=200)
    vence: datetime | None = None
    responsable_id: uuid.UUID | None = None
    contacto_id: uuid.UUID | None = None
    oportunidad_id: uuid.UUID | None = None


class CambioTarea(BaseModel):
    titulo: str | None = Field(default=None, min_length=1, max_length=200)
    vence: datetime | None = None
    responsable_id: uuid.UUID | None = None
    estado: Literal["pendiente", "hecha", "cancelada"] | None = None


@router.get("/tareas")
def listar_tareas(estado: Literal["pendiente", "hecha", "cancelada"] | None = None,
                  responsable_id: uuid.UUID | None = None, contacto_id: uuid.UUID | None = None,
                  oportunidad_id: uuid.UUID | None = None, vencidas: bool = False,
                  limite: int = Query(100, ge=1, le=500), ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT * FROM tareas
               WHERE (%s::text IS NULL OR estado = %s::text)
                 AND (%s::uuid IS NULL OR responsable_id = %s::uuid)
                 AND (%s::uuid IS NULL OR contacto_id = %s::uuid)
                 AND (%s::uuid IS NULL OR oportunidad_id = %s::uuid)
                 AND (NOT %s OR (estado = 'pendiente' AND vence < now()))
               ORDER BY (estado = 'pendiente') DESC, vence ASC NULLS LAST, creado DESC
               LIMIT %s""",
            (estado, estado, responsable_id, responsable_id, contacto_id, contacto_id,
             oportunidad_id, oportunidad_id, vencidas, limite)).fetchall()
    return {"tareas": filas}


@router.post("/tareas")
def crear_tarea(nueva: NuevaTarea, ident=Depends(ambos_roles)):
    titulo = nueva.titulo.strip()
    if not titulo:
        raise HTTPException(422, "El título no puede quedar vacío.")
    try:
        with db.transaccion(ident.negocio_id) as con:
            fila = con.execute(
                """INSERT INTO tareas (negocio_id, titulo, vence, responsable_id, contacto_id, oportunidad_id)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (ident.negocio_id, titulo, nueva.vence,
                 _responsable(con, nueva.responsable_id or uuid.UUID(ident.usuario_id)),
                 nueva.contacto_id, nueva.oportunidad_id)).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "El contacto u oportunidad no existe en tu negocio.")
    return {"tarea": fila}


@router.patch("/tareas/{tarea_id}")
def actualizar_tarea(tarea_id: uuid.UUID, cambio: CambioTarea, ident=Depends(ambos_roles)):
    cambios = cambio.model_dump(exclude_unset=True)
    for campo in ("titulo", "estado"):
        if campo in cambios and cambios[campo] is None:
            raise HTTPException(422, f"{campo} no puede quedar vacío.")
    if "titulo" in cambios:
        cambios["titulo"] = cambios["titulo"].strip()
        if not cambios["titulo"]:
            raise HTTPException(422, "El título no puede quedar vacío.")
    with db.transaccion(ident.negocio_id) as con:
        actual = con.execute("SELECT * FROM tareas WHERE id = %s FOR UPDATE", (tarea_id,)).fetchone()
        if not actual:
            raise HTTPException(404, "No encontramos esa tarea.")
        if not cambios:
            return {"tarea": actual}
        upd = dict(cambios)
        if "responsable_id" in cambios:
            upd["responsable_id"] = _responsable(con, cambios["responsable_id"])
        if "estado" in cambios:
            # Solo una tarea hecha tiene fecha de cumplimiento; reabrirla o cancelarla la quita.
            upd["completada_en"] = datetime.now(timezone.utc) if cambios["estado"] == "hecha" else None
        _historial(con, ident.negocio_id, "tarea", tarea_id, {k: actual[k] for k in cambios}, cambios,
                   uuid.UUID(ident.usuario_id))
        columnas = list(upd)
        fila = con.execute(
            f"UPDATE tareas SET {', '.join(f'{c} = %s' for c in columnas)}, actualizado = now() "
            "WHERE id = %s RETURNING *",
            [upd[c] for c in columnas] + [tarea_id]).fetchone()
    return {"tarea": fila}


# --- Catálogo: productos y servicios compartidos por los giros. Existencia opcional (null = sin inventario). -------------

LIMITE_MONTO = Decimal(10**9)


class NuevoArticulo(BaseModel):
    tipo: Literal["producto", "servicio"]
    nombre: str = Field(min_length=1, max_length=120)
    codigo: str = Field(default="", max_length=60)
    categoria: str = Field(default="", max_length=60)
    unidad: str = Field(default="pieza", max_length=20)
    precio: Decimal = Field(ge=0, le=LIMITE_MONTO)
    costo: Decimal = Field(default=Decimal(0), ge=0, le=LIMITE_MONTO)
    tasa_iva: Decimal = Field(default=Decimal("0.16"), ge=0, le=1)
    existencia: Decimal | None = Field(default=None, ge=0, le=LIMITE_MONTO)
    minimo: Decimal = Field(default=Decimal(0), ge=0, le=LIMITE_MONTO)


class CambioArticulo(BaseModel):
    tipo: Literal["producto", "servicio"] | None = None
    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    codigo: str | None = Field(default=None, max_length=60)
    categoria: str | None = Field(default=None, max_length=60)
    unidad: str | None = Field(default=None, max_length=20)
    precio: Decimal | None = Field(default=None, ge=0, le=LIMITE_MONTO)
    costo: Decimal | None = Field(default=None, ge=0, le=LIMITE_MONTO)
    tasa_iva: Decimal | None = Field(default=None, ge=0, le=1)
    existencia: Decimal | None = Field(default=None, ge=0, le=LIMITE_MONTO)
    minimo: Decimal | None = Field(default=None, ge=0, le=LIMITE_MONTO)
    activo: bool | None = None


class MovimientoExistencia(BaseModel):
    cambio: Decimal = Field(ge=-LIMITE_MONTO, le=LIMITE_MONTO)   # positivo = entrada, negativo = salida
    motivo: str = Field(default="", max_length=120)


@router.get("/catalogo")
def listar_catalogo(tipo: Literal["producto", "servicio"] | None = None, q: str = "",
                    incluir_inactivos: bool = False, bajo_minimo: bool = False,
                    limite: int = Query(100, ge=1, le=500), ident=Depends(ambos_roles)):
    q = q.strip()
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT * FROM catalogo
               WHERE (%s::text IS NULL OR tipo = %s::text)
                 AND (%s = '' OR nombre ILIKE %s OR codigo ILIKE %s)
                 AND (%s OR activo)
                 AND (NOT %s OR (existencia IS NOT NULL AND existencia <= minimo))
               ORDER BY activo DESC, nombre
               LIMIT %s""",
            (tipo, tipo, q, _ilike(q), _ilike(q), incluir_inactivos, bajo_minimo, limite)).fetchall()
    return {"catalogo": filas}


@router.post("/catalogo")
def crear_articulo(nuevo: NuevoArticulo, ident=Depends(solo_admin)):
    with db.transaccion(ident.negocio_id) as con:
        fila = con.execute(
            """INSERT INTO catalogo (negocio_id, tipo, nombre, codigo, categoria, unidad, precio, costo, tasa_iva,
                   existencia, minimo)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (ident.negocio_id, nuevo.tipo, nuevo.nombre.strip(), nuevo.codigo.strip(), nuevo.categoria.strip(),
             nuevo.unidad.strip() or "pieza", nuevo.precio, nuevo.costo, nuevo.tasa_iva, nuevo.existencia,
             nuevo.minimo)).fetchone()
    return {"articulo": fila}


@router.patch("/catalogo/{articulo_id}")
def actualizar_articulo(articulo_id: uuid.UUID, cambio: CambioArticulo, ident=Depends(solo_admin)):
    cambios = cambio.model_dump(exclude_unset=True)
    no_nulos = ("tipo", "nombre", "codigo", "categoria", "unidad", "precio", "costo", "tasa_iva", "minimo", "activo")
    for campo in no_nulos:
        if campo in cambios and cambios[campo] is None:
            raise HTTPException(422, f"{campo} no puede quedar vacío.")
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()
        if not cambios["nombre"]:
            raise HTTPException(422, "El nombre no puede quedar vacío.")
    with db.transaccion(ident.negocio_id) as con:
        actual = con.execute("SELECT * FROM catalogo WHERE id = %s FOR UPDATE", (articulo_id,)).fetchone()
        if not actual:
            raise HTTPException(404, "No encontramos ese artículo.")
        if not cambios:
            return {"articulo": actual}
        _historial(con, ident.negocio_id, "articulo", articulo_id, {k: actual[k] for k in cambios}, cambios,
                   uuid.UUID(ident.usuario_id))
        columnas = list(cambios)
        fila = con.execute(
            f"UPDATE catalogo SET {', '.join(f'{c} = %s' for c in columnas)}, actualizado = now() "
            "WHERE id = %s RETURNING *",
            [cambios[c] for c in columnas] + [articulo_id]).fetchone()
    return {"articulo": fila}


@router.post("/catalogo/{articulo_id}/existencia")
def mover_existencia(articulo_id: uuid.UUID, movimiento: MovimientoExistencia, ident=Depends(ambos_roles)):
    """Entradas y salidas de inventario. La existencia nunca queda negativa y cada movimiento deja historial."""
    if movimiento.cambio == 0:
        raise HTTPException(422, "El movimiento no puede ser cero.")
    with db.transaccion(ident.negocio_id) as con:
        actual = con.execute("SELECT * FROM catalogo WHERE id = %s FOR UPDATE", (articulo_id,)).fetchone()
        if not actual:
            raise HTTPException(404, "No encontramos ese artículo.")
        if actual["existencia"] is None:
            raise HTTPException(422, "Este artículo no lleva inventario.")
        nueva = actual["existencia"] + movimiento.cambio
        if nueva < 0:
            raise HTTPException(422, f"No hay existencia suficiente (quedan {actual['existencia']}).")
        _historial(con, ident.negocio_id, "articulo", articulo_id, {"existencia": actual["existencia"]},
                   {"existencia": nueva}, uuid.UUID(ident.usuario_id))
        if movimiento.motivo.strip():
            _historial(con, ident.negocio_id, "articulo", articulo_id, {"motivo_existencia": None},
                       {"motivo_existencia": movimiento.motivo.strip()}, uuid.UUID(ident.usuario_id))
        fila = con.execute("UPDATE catalogo SET existencia = %s, actualizado = now() WHERE id = %s RETURNING *",
                           (nueva, articulo_id)).fetchone()
    return {"articulo": fila}
