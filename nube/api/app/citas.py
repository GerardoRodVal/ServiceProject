"""Citas de los asesores y su ubicación.

Las rutas /api/app/* de la app del celular tienen la misma forma que las del gestor de Windows (estado, cita, ubicación,
salir), así que la app las usa igual en los dos modos. Las rutas /api/citas son para el administrador.

Privacidad: la ubicación solo se recibe con una cita en curso (la app la envía únicamente con la pantalla abierta y la
persona puede terminar la cita cuando quiera). El trayecto se borra pasados RETENCION_UBICACION_DIAS; el aviso de
privacidad del negocio debe decirle a su gente que se registra la ubicación durante la cita.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import db
from .auth import requiere

app_router = APIRouter(prefix="/api/app")
admin_router = APIRouter(prefix="/api/citas")
ambos_roles = requiere("admin", "capturista")
solo_admin = requiere("admin")

MAX_PUNTOS = 2000                 # por cita: lo mismo que acepta el gestor de Windows
ESPERA_ENTRE_PUNTOS = 5           # segundos: la app envía cada 30; más seguido se ignora
MAX_HORAS_ATRASO = 12             # una cita iniciada sin señal puede declarar su inicio real, hasta 12 h atrás


class PedidoCita(BaseModel):
    accion: Literal["iniciar", "terminar"]
    inicio: str | None = None     # solo con «iniciar»: la hora real si la app empezó la cita sin señal


class Punto(BaseModel):
    lat: Annotated[float, Field(ge=-90, le=90)]
    lng: Annotated[float, Field(ge=-180, le=180)]
    precision: Annotated[float, Field(ge=0, le=100000)] = 0


def _vista(cita):
    return {"en_cita": True, "inicio": cita["inicio"].isoformat(timespec="seconds"), "ubicacion": cita["ultima_ubicacion"]}


def _sin_cita():
    return {"en_cita": False, "inicio": None, "ubicacion": None}


def _inicio_declarado(texto):
    """La hora que declara la app, solo si es razonable (ni futura ni de hace más de 12 h); si no, se usa la del servidor."""
    if not texto:
        return None
    try:
        d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    ahora = datetime.now(timezone.utc)
    return d if ahora - timedelta(hours=MAX_HORAS_ATRASO) <= d <= ahora else None


def _purgar(con, dias):
    """Retención: borra el trayecto antiguo y las ubicaciones finales de citas viejas."""
    con.execute("DELETE FROM ubicaciones WHERE capturada < now() - make_interval(days => %s)", (dias,))
    con.execute("UPDATE citas SET ultima_ubicacion = NULL, ubicacion_final = NULL "
                "WHERE fin IS NOT NULL AND fin < now() - make_interval(days => %s) AND (ultima_ubicacion IS NOT NULL OR ubicacion_final IS NOT NULL)",
                (dias,))


def _terminar(con, ident, dias):
    """Cierra la cita en curso del usuario y devuelve su resumen, o None si no había."""
    cita = con.execute(
        """UPDATE citas SET fin = now(), ubicacion_final = ultima_ubicacion
           WHERE usuario_id = %s AND fin IS NULL RETURNING id, inicio, fin, ubicacion_final""",
        (ident.usuario_id,)).fetchone()
    if not cita:
        return None
    puntos = con.execute("SELECT count(*) AS n FROM ubicaciones WHERE negocio_id = %s AND cita_id = %s",
                         (ident.negocio_id, cita["id"])).fetchone()["n"]
    _purgar(con, dias)
    return {"asesor_id": ident.usuario_id, "inicio": cita["inicio"].isoformat(timespec="seconds"),
            "fin": cita["fin"].isoformat(timespec="seconds"),
            "duracion_seg": int((cita["fin"] - cita["inicio"]).total_seconds()),
            "ubicacion_final": cita["ubicacion_final"], "puntos": puntos}


# --- App del celular (misma forma que las rutas del gestor de Windows) ---

@app_router.get("/estado")
def estado(ident=Depends(ambos_roles)):
    with db.transaccion(ident.negocio_id) as con:
        cita = con.execute("SELECT inicio, ultima_ubicacion FROM citas WHERE usuario_id = %s AND fin IS NULL",
                           (ident.usuario_id,)).fetchone()
    return _vista(cita) if cita else _sin_cita()


@app_router.post("/cita")
def cita(request: Request, pedido: PedidoCita, ident=Depends(ambos_roles)):
    dias = request.app.state.cfg.retencion_ubicacion_dias
    with db.transaccion(ident.negocio_id) as con:
        if pedido.accion == "terminar":
            return {"en_cita": False, "resumen": _terminar(con, ident, dias)}
        # «Iniciar» no reinicia el cronómetro si la cita ya está en curso.
        con.execute(
            """INSERT INTO citas (negocio_id, usuario_id, inicio) VALUES (%s, %s, COALESCE(%s, now()))
               ON CONFLICT (usuario_id) WHERE fin IS NULL DO NOTHING""",
            (ident.negocio_id, ident.usuario_id, _inicio_declarado(pedido.inicio)))
        actual = con.execute("SELECT inicio, ultima_ubicacion FROM citas WHERE usuario_id = %s AND fin IS NULL",
                             (ident.usuario_id,)).fetchone()
    return _vista(actual)


@app_router.post("/ubicacion")
def ubicacion(punto: Punto, ident=Depends(ambos_roles)):
    """Solo se acepta con una cita en curso. Cada punto va al trayecto con un tope y un mínimo de espera."""
    hora = datetime.now(timezone.utc)
    registro = {"lat": punto.lat, "lng": punto.lng, "precision": round(punto.precision), "hora": hora.isoformat(timespec="seconds")}
    with db.transaccion(ident.negocio_id) as con:
        cita = con.execute("SELECT id, inicio FROM citas WHERE usuario_id = %s AND fin IS NULL FOR UPDATE", (ident.usuario_id,)).fetchone()
        if not cita:
            raise HTTPException(409, "No hay una cita en curso.")
        resumen = con.execute(
            "SELECT count(*) AS n, max(capturada) AS ultima FROM ubicaciones WHERE negocio_id = %s AND cita_id = %s",
            (ident.negocio_id, cita["id"])).fetchone()
        guardar = resumen["n"] < MAX_PUNTOS and (resumen["ultima"] is None or (hora - resumen["ultima"]).total_seconds() >= ESPERA_ENTRE_PUNTOS)
        if guardar:
            con.execute("INSERT INTO ubicaciones (negocio_id, cita_id, lat, lng, precision_m) VALUES (%s, %s, %s, %s, %s)",
                        (ident.negocio_id, cita["id"], punto.lat, punto.lng, registro["precision"]))
        con.execute("UPDATE citas SET ultima_ubicacion = %s WHERE negocio_id = %s AND id = %s",
                    (Jsonb(registro), ident.negocio_id, cita["id"]))
    return {"en_cita": True, "inicio": cita["inicio"].isoformat(timespec="seconds"), "ubicacion": registro}


@app_router.post("/salir")
def salir(request: Request, ident=Depends(ambos_roles)):
    """Cerrar sesión termina la cita en curso (en la nube no hay sesión de servidor que cerrar: el token vence solo)."""
    with db.transaccion(ident.negocio_id) as con:
        _terminar(con, ident, request.app.state.cfg.retencion_ubicacion_dias)
    return {"ok": True}


# --- Administrador ---

@admin_router.get("/activas")
def activas(ident=Depends(solo_admin)):
    """Asesores con una cita en curso y su última ubicación."""
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT c.usuario_id, COALESCE(NULLIF(u.nombre, ''), split_part(u.correo, '@', 1)) AS nombre, c.inicio, c.ultima_ubicacion
               FROM citas c JOIN usuarios u ON u.id = c.usuario_id
               WHERE c.fin IS NULL ORDER BY c.inicio""").fetchall()
    return {"citas": [{"usuario_id": str(f["usuario_id"]), "nombre": f["nombre"], "en_cita": True,
                       "inicio": f["inicio"].isoformat(timespec="seconds"), "ubicacion": f["ultima_ubicacion"]} for f in filas]}


@admin_router.get("")
def historial(limite: int = Query(50, ge=1, le=200), ident=Depends(solo_admin)):
    """Citas terminadas, de la más reciente a la más antigua."""
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(
            """SELECT c.id, c.usuario_id, COALESCE(NULLIF(u.nombre, ''), split_part(u.correo, '@', 1)) AS nombre, c.inicio, c.fin, c.ubicacion_final,
                      (SELECT count(*) FROM ubicaciones p WHERE p.negocio_id = c.negocio_id AND p.cita_id = c.id) AS puntos
               FROM citas c JOIN usuarios u ON u.id = c.usuario_id WHERE c.fin IS NOT NULL ORDER BY c.fin DESC LIMIT %s""",
            (limite,)).fetchall()
    return {"citas": [{"id": str(f["id"]), "usuario_id": str(f["usuario_id"]), "nombre": f["nombre"],
                       "inicio": f["inicio"].isoformat(timespec="seconds"), "fin": f["fin"].isoformat(timespec="seconds"),
                       "duracion_seg": int((f["fin"] - f["inicio"]).total_seconds()), "ubicacion_final": f["ubicacion_final"],
                       "puntos": f["puntos"]} for f in filas]}


@admin_router.get("/{cita_id}/trayecto")
def trayecto(cita_id: uuid.UUID, ident=Depends(solo_admin)):
    with db.transaccion(ident.negocio_id) as con:
        existe = con.execute("SELECT 1 AS ok FROM citas WHERE negocio_id = %s AND id = %s", (ident.negocio_id, cita_id)).fetchone()
        if not existe:
            raise HTTPException(404, "No encontramos esa cita.")
        puntos = con.execute("SELECT capturada, lat, lng, precision_m FROM ubicaciones WHERE negocio_id = %s AND cita_id = %s ORDER BY capturada",
                             (ident.negocio_id, cita_id)).fetchall()
    return {"puntos": [{"hora": p["capturada"].isoformat(timespec="seconds"), "lat": p["lat"], "lng": p["lng"], "precision": p["precision_m"]} for p in puntos]}
