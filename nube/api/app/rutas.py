"""Rutas de la API. Cada una corre en una transacción con el negocio del usuario ya fijado (ver db.transaccion)."""
import uuid

import psycopg
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import almacen as alm
from . import db, visitas_nucleo
from .auth import requiere
from .validacion import ESTADOS, MAX_BYTES_VISITA, limpiar_enlace, limpiar_visita

router = APIRouter(prefix="/api")
ambos_roles = requiere("admin", "capturista")
solo_admin = requiere("admin")
MAX_BYTES_LOTE = 1024 * 1024
MAX_ITEMS_LOTE = 200


class CambioEstado(BaseModel):
    id: str
    estado: str


class ItemLote(BaseModel):
    visita: dict
    estado: str = "nuevo"
    demo: bool = False
    captura: bool = False     # true = visita capturada en el celular: cuenta como uso del mes


class Lote(BaseModel):
    visitas: list[ItemLote] = Field(max_length=MAX_ITEMS_LOTE)


def _sumar_uso(con, negocio_id, registros=0, archivos_bytes=0):
    con.execute(
        """INSERT INTO uso_mensual (negocio_id, periodo, registros, archivos_bytes)
           VALUES (%s, date_trunc('month', now() AT TIME ZONE 'UTC')::date, %s, %s)
           ON CONFLICT (negocio_id, periodo) DO UPDATE
           SET registros = uso_mensual.registros + excluded.registros,
               archivos_bytes = uso_mensual.archivos_bytes + excluded.archivos_bytes""",
        (negocio_id, registros, archivos_bytes))


def _registros_del_mes(con, negocio_id):
    fila = con.execute(
        "SELECT registros FROM uso_mensual WHERE negocio_id = %s AND periodo = date_trunc('month', now() AT TIME ZONE 'UTC')::date",
        (negocio_id,)).fetchone()
    return fila["registros"] if fila else 0


def _aviso_de_uso(limite, usados):
    """Regla de los planes: se avisa al 80 % y al excederse, pero la captura nunca se bloquea."""
    if not limite:
        return None
    if usados > limite:
        return "excedido"
    return "uso_80" if usados >= limite * 0.8 else None


def _visita_a_json(f):
    return {**f["datos"], "id": f["id"], "creado": f["creado"].isoformat(), "recibido": f["recibido"].isoformat(),
            "estado": f["estado"], "demo": f["demo"]}


@router.post("/visitas")
def crear_visita(request: Request, visita: dict = Body(...), ident=Depends(ambos_roles)):
    """La app del celular envía aquí cada visita. Reenviar la misma no la duplica ni pierde su estado de seguimiento."""
    if int(request.headers.get("content-length") or 0) > MAX_BYTES_VISITA:
        raise HTTPException(413, "La visita es demasiado grande.")
    try:
        limpia = limpiar_visita(visita)
    except ValueError as e:
        raise HTTPException(422, str(e))
    with db.transaccion(ident.negocio_id) as con:
        # La visita queda a nombre de quien inició sesión, no de lo que escriba el celular.
        usuario = con.execute("SELECT nombre, correo FROM usuarios WHERE id = %s", (ident.usuario_id,)).fetchone()
        limpia["asesor"] = (usuario["nombre"] or usuario["correo"].split("@")[0])[:60]
        limpia["asesor_id"] = ident.usuario_id
        fila = con.execute(
            """INSERT INTO registros (negocio_id, id, creado, demo, estado, datos)
               VALUES (%s, %s, %s, false, 'nuevo', %s)
               ON CONFLICT (negocio_id, id) DO UPDATE SET datos = excluded.datos, recibido = now()
               RETURNING (xmax = 0) AS nuevo""",
            (ident.negocio_id, limpia["id"], limpia["creado"], Jsonb(limpia))).fetchone()
        if fila["nuevo"]:
            _sumar_uso(con, ident.negocio_id, registros=1)
        visitas_nucleo.ligar(con, ident.negocio_id, limpia["id"])     # su contacto y su oportunidad en el embudo
        usados = _registros_del_mes(con, ident.negocio_id)
    return {"ok": True, "duplicado": not fila["nuevo"],
            "aviso": _aviso_de_uso(request.app.state.cfg.limite_registros_mes, usados)}


# Lo que la app del celular necesita de la configuración de un negocio. Lo demás (accesos, IA, taller, lista de
# asesores) no sale de la nube hacia los celulares aunque el gestor lo envíe.
CLAVES_PROHIBIDAS_CONFIG = {"acceso", "ia", "taller", "conexiones", "asesores"}
MAX_BYTES_CONFIG = 512 * 1024


@router.get("/app/config")
def config_para_el_celular(ident=Depends(ambos_roles)):
    """La app del celular la pide al iniciar sesión y cada minuto: formulario, propiedades y pesos del score."""
    with db.transaccion(ident.negocio_id) as con:
        negocio = con.execute("SELECT nombre, giro, token_publico FROM negocios WHERE id = %s", (ident.negocio_id,)).fetchone()
        guardada = con.execute("SELECT datos FROM configuraciones WHERE negocio_id = %s", (ident.negocio_id,)).fetchone()
        usuario = con.execute("SELECT nombre, correo, rol FROM usuarios WHERE id = %s", (ident.usuario_id,)).fetchone()
    return {"config": guardada["datos"] if guardada else None, "negocio": negocio,
            "usuario": {"id": ident.usuario_id, "nombre": usuario["nombre"] or usuario["correo"].split("@")[0],
                        "correo": usuario["correo"], "rol": usuario["rol"]}}


@router.put("/config")
def guardar_configuracion(request: Request, config: dict = Body(...), ident=Depends(solo_admin)):
    """El gestor de Windows publica aquí la configuración de su negocio para que los celulares la tomen."""
    if int(request.headers.get("content-length") or 0) > MAX_BYTES_CONFIG:
        raise HTTPException(413, "La configuración es demasiado grande.")
    limpia = {k: v for k, v in config.items() if k not in CLAVES_PROHIBIDAS_CONFIG}
    with db.transaccion(ident.negocio_id) as con:
        con.execute(
            """INSERT INTO configuraciones (negocio_id, datos) VALUES (%s, %s)
               ON CONFLICT (negocio_id) DO UPDATE SET datos = excluded.datos, actualizado = now()""",
            (ident.negocio_id, Jsonb(limpia)))
    return {"ok": True}


@router.get("/visitas")
def listar_visitas(ident=Depends(solo_admin)):
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute("SELECT * FROM registros ORDER BY creado DESC").fetchall()
    return [_visita_a_json(f) for f in filas]


@router.post("/visitas/estado")
def cambiar_estado(cambio: CambioEstado, ident=Depends(solo_admin)):
    if cambio.estado not in ESTADOS:
        raise HTTPException(422, "Estado no válido.")
    with db.transaccion(ident.negocio_id) as con:
        previo = con.execute("SELECT estado FROM registros WHERE negocio_id = %s AND id = %s FOR UPDATE",
                             (ident.negocio_id, cambio.id)).fetchone()
        if not previo:
            raise HTTPException(404, "No encontramos ese registro.")
        if previo["estado"] != cambio.estado:
            con.execute("UPDATE registros SET estado = %s WHERE negocio_id = %s AND id = %s",
                        (cambio.estado, ident.negocio_id, cambio.id))
            con.execute(
                """INSERT INTO historial_estados (negocio_id, registro_id, estado_anterior, estado_nuevo, usuario_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (ident.negocio_id, cambio.id, previo["estado"], cambio.estado, ident.usuario_id))
            visitas_nucleo.etapa_desde_estado(con, cambio.id, cambio.estado)   # su oportunidad cambia de etapa igual
    return {"ok": True}


@router.post("/visitas/ligar")
def ligar_visitas(ident=Depends(solo_admin)):
    """Liga al embudo las visitas que aún no tienen contacto ni oportunidad (las de antes de unir prospectos y embudo)."""
    with db.transaccion(ident.negocio_id) as con:
        n = visitas_nucleo.ligar_pendientes(con, ident.negocio_id)
    return {"ok": True, "ligadas": n}


@router.post("/visitas/lote")
def guardar_lote(request: Request, lote: Lote, ident=Depends(solo_admin)):
    """Para el gestor de Windows: guarda varias visitas con su estado y marca de ejemplo. Como en el gestor local,
    una visita que ya existe actualiza sus datos pero conserva su estado de seguimiento. Solo las marcadas como
    «captura» (las del celular) cuentan como uso del mes; las cargas de archivo, Kommo y ejemplos no."""
    if int(request.headers.get("content-length") or 0) > MAX_BYTES_LOTE:
        raise HTTPException(413, "El lote es demasiado grande.")
    insertadas = actualizadas = capturas_nuevas = 0
    omitidas = []
    with db.transaccion(ident.negocio_id) as con:
        for item in lote.visitas:
            id_ = str(item.visita.get("id", ""))[:64]
            try:
                if item.estado not in ESTADOS:
                    raise ValueError("Estado no válido.")
                limpia = limpiar_visita(item.visita, interno=True)
                with con.transaction():     # un punto de guardado por visita: si la base rechaza una, las demás siguen
                    fila = con.execute(
                        """INSERT INTO registros (negocio_id, id, creado, demo, estado, datos)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (negocio_id, id) DO UPDATE SET datos = excluded.datos, recibido = now()
                           RETURNING (xmax = 0) AS nuevo""",
                        (ident.negocio_id, limpia["id"], limpia["creado"], item.demo, item.estado, Jsonb(limpia))).fetchone()
                    visitas_nucleo.ligar(con, ident.negocio_id, limpia["id"])   # las de ejemplo no se ligan
            except (ValueError, psycopg.errors.CheckViolation) as e:
                omitidas.append({"id": id_, "motivo": str(e)})
                continue
            if fila["nuevo"]:
                insertadas += 1
                if item.captura and not item.demo:
                    capturas_nuevas += 1
            else:
                actualizadas += 1
        if capturas_nuevas:
            _sumar_uso(con, ident.negocio_id, registros=capturas_nuevas)
        usados = _registros_del_mes(con, ident.negocio_id)
    return {"ok": True, "insertadas": insertadas, "actualizadas": actualizadas, "omitidas": omitidas,
            "aviso": _aviso_de_uso(request.app.state.cfg.limite_registros_mes, usados)}


@router.patch("/visitas/{visita_id}/datos")
def agregar_datos(visita_id: str, cambios: dict = Body(...), ident=Depends(solo_admin)):
    """Agrega a una visita su enlace a Kommo, origen o asesor, sin tocar su estado ni sus fechas."""
    try:
        extra = limpiar_enlace(cambios)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not extra:
        raise HTTPException(422, "No hay datos válidos que agregar.")
    with db.transaccion(ident.negocio_id) as con:
        n = con.execute("UPDATE registros SET datos = datos || %s WHERE negocio_id = %s AND id = %s",
                        (Jsonb(extra), ident.negocio_id, visita_id)).rowcount
    if not n:
        raise HTTPException(404, "No encontramos ese registro.")
    return {"ok": True}


@router.post("/visitas/borrar-ejemplos")
def borrar_ejemplos(ident=Depends(solo_admin)):
    with db.transaccion(ident.negocio_id) as con:
        borrados = con.execute("DELETE FROM registros WHERE negocio_id = %s AND demo", (ident.negocio_id,)).rowcount
    return {"ok": True, "borrados": borrados}


@router.post("/visitas/borrar-importados")
def borrar_importados(ident=Depends(solo_admin)):
    """Quita los prospectos que entraron por carga de archivo (ids «imp-…»)."""
    with db.transaccion(ident.negocio_id) as con:
        borrados = con.execute("DELETE FROM registros WHERE negocio_id = %s AND id LIKE 'imp-%%'",
                               (ident.negocio_id,)).rowcount
    return {"ok": True, "borrados": borrados}


@router.post("/archivos")
def subir_archivo(request: Request, archivo: UploadFile = File(...), registro_id: str | None = Query(None, max_length=64),
                  ident=Depends(ambos_roles)):
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
            if registro_id is not None:
                existe = con.execute("SELECT 1 AS ok FROM registros WHERE negocio_id = %s AND id = %s",
                                     (ident.negocio_id, registro_id)).fetchone()
                if not existe:
                    raise HTTPException(404, "No encontramos ese registro.")
            fila = con.execute(
                """INSERT INTO archivos (negocio_id, registro_id, nombre_blob, tipo, bytes, subido_por)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (ident.negocio_id, registro_id, nombre_blob, tipo, len(datos), ident.usuario_id)).fetchone()
            _sumar_uso(con, ident.negocio_id, archivos_bytes=len(datos))
    except Exception:
        almacen.borrar_prefijo(nombre_blob)   # no dejar un archivo huérfano si la base rechazó el alta
        raise
    return {"id": str(fila["id"]), "tipo": tipo, "bytes": len(datos)}


@router.get("/archivos/{archivo_id}")
def bajar_archivo(archivo_id: uuid.UUID, request: Request, ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        fila = con.execute("SELECT nombre_blob, tipo FROM archivos WHERE negocio_id = %s AND id = %s AND proposito = 'registro'",
                           (ident.negocio_id, archivo_id)).fetchone()
    if not fila:
        raise HTTPException(404, "No encontramos ese archivo.")
    contenido = request.app.state.almacen.leer(fila["nombre_blob"])
    if contenido is None:
        raise HTTPException(404, "No encontramos ese archivo.")
    return Response(contenido, media_type=fila["tipo"],
                    headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.get("/uso")
def uso(request: Request, ident=Depends(solo_admin)):
    cfg = request.app.state.cfg
    with db.transaccion(ident.negocio_id) as con:
        mes = con.execute(
            "SELECT registros, archivos_bytes FROM uso_mensual WHERE negocio_id = %s "
            "AND periodo = date_trunc('month', now() AT TIME ZONE 'UTC')::date", (ident.negocio_id,)).fetchone()
        total = con.execute("SELECT count(*) AS n FROM registros").fetchone()["n"]
        usuarios = con.execute("SELECT count(*) AS n FROM usuarios WHERE activo").fetchone()["n"]
    mes = mes or {"registros": 0, "archivos_bytes": 0}
    return {
        "registros_mes": mes["registros"],
        "archivos_mb_mes": round(mes["archivos_bytes"] / (1024 * 1024), 2),
        "registros_total": total,
        "usuarios_activos": usuarios,
        # null = límite por definir (ver Planes en el gestor)
        "limites": {"registros_mes": cfg.limite_registros_mes, "archivos_mb_mes": cfg.limite_archivos_mb_mes},
        "aviso_registros": _aviso_de_uso(cfg.limite_registros_mes, mes["registros"]),
    }
