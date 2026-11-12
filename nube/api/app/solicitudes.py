"""Agenda en línea: el cliente pide una cita desde el enlace o el QR que reparte el asesor, sin sesión.

La página pública (agendar.html?n=<token>&a=<asesor>) lee el formato, el catálogo y los horarios libres con
GET /api/publico/{token}/cita y manda la solicitud con POST al mismo camino. Todo es obligatorio: nombre, teléfono, la
propiedad, el día y la hora; solo se ofrecen los espacios libres según el horario de atención del negocio, y al cliente
nunca se le muestran las citas de otros (solo deja de ver el espacio ocupado). El administrador la confirma por llamada o mensaje (rutas /api/solicitudes);
la visita presencial después se registra en la app y aparece en Prospectos.

Solo se publica lo que la página necesita (lista blanca): título, mensaje, qué datos pide y los nombres de zonas y casas no
vendidas, y los días y horas libres. Nada de precios internos, score, asesores, citas de otros ni accesos.
"""
import re
import threading
import time
import uuid
from datetime import date, datetime, time as hora_del_dia, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import db
from .auth import requiere

router = APIRouter(prefix="/api")
solo_admin = requiere("admin")

RE_TOKEN = re.compile(r"[0-9a-f]{32}")
RE_ID = re.compile(r"[a-z0-9-]{1,60}")
NO_DISPONIBLE = "Esta agenda no está disponible."
RE_HORA = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
RE_ZONA_HORARIA = re.compile(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2}")
DURACIONES = (30, 45, 60, 90, 120)
AVISO_MINUTOS = 60          # no se ofrece un espacio que empiece en menos de una hora
DIAS_MAX = 120
LIMITE_ENVIOS, VENTANA_ENVIOS = 5, 600      # por dirección y negocio: hasta 5 solicitudes cada 10 minutos
CABECERAS = {"Cache-Control": "no-cache", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"}
DEFECTO = {
    "titulo": "Agenda tu visita",
    "mensaje": "Déjanos tus datos, la propiedad que te interesa y el horario que prefieres. Un asesor te llamará o te "
               "escribirá por WhatsApp para confirmar tu visita.",
    # Horario de atención: días de la semana (0 = lunes), primera y última hora en que puede empezar una cita, duración
    # de cada cita, cuántas caben al mismo tiempo y cuántos días hacia adelante se ofrecen.
    "dias": [0, 1, 2, 3, 4, 5], "hora_inicio": "10:00", "hora_fin": "19:00", "duracion": 60, "capacidad": 1,
    "dias_adelante": 14, "zona_horaria": "America/Mexico_City",
}
_envios: dict = {}
_envios_lock = threading.Lock()


def _texto(valor, largo):
    return " ".join(valor.split())[:largo] if isinstance(valor, str) else ""


def tel10(valor):
    """Teléfono normalizado a sus últimos 10 dígitos (México); vacío si no alcanza."""
    digitos = re.sub(r"\D", "", valor if isinstance(valor, str) else str(valor or ""))
    return digitos[-10:] if len(digitos) >= 10 else ""


METODOS_CONTACTO = ("telefono", "whatsapp", "correo", "redes")
RE_CORREO = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
RE_USUARIO_REDES = re.compile(r"[A-Za-z0-9._]{2,40}")


def clave_contacto(metodo, valor):
    """(clave, valor limpio) del método que eligió el cliente. La clave identifica a la persona para no duplicar su
    solicitud por confirmar: el teléfono de 10 dígitos, el correo o el usuario de redes."""
    texto = _texto(valor, 120)
    if metodo in ("telefono", "whatsapp"):
        tel = tel10(texto)
        if not tel:
            raise ValueError("Escribe un teléfono de 10 dígitos.")
        return tel, tel
    if metodo == "correo":
        correo = texto.lower()
        if not RE_CORREO.fullmatch(correo):
            raise ValueError("Escribe un correo válido.")
        return correo, correo
    if metodo == "redes":
        usuario = texto.lstrip("@").strip()
        if not RE_USUARIO_REDES.fullmatch(usuario):
            raise ValueError("Escribe tu usuario de Instagram o Facebook.")
        return "@" + usuario.lower(), usuario
    raise ValueError("Elige cómo quieres que te contactemos.")


def _clave_de(datos):
    """Clave del envío, para no contar su propia solicitud al mostrar los horarios libres."""
    try:
        return clave_contacto(datos.get("metodo") or "telefono", datos.get("contacto", datos.get("tel")))[0]
    except ValueError:
        return ""


def _entero(valor, minimo, maximo, defecto):
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and minimo <= valor <= maximo else defecto


def _minutos(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def formato(config):
    """Formato de la página que eligió el negocio en el gestor (config.cita_publica), con sus valores por omisión."""
    cp = config.get("cita_publica") if isinstance(config.get("cita_publica"), dict) else {}
    mensaje = cp.get("mensaje").strip()[:600] if isinstance(cp.get("mensaje"), str) else ""
    dias = sorted({d for d in cp["dias"] if _entero(d, 0, 6, None) is not None}) if isinstance(cp.get("dias"), list) else []
    inicio = cp["hora_inicio"] if isinstance(cp.get("hora_inicio"), str) and RE_HORA.fullmatch(cp["hora_inicio"]) else ""
    fin = cp["hora_fin"] if isinstance(cp.get("hora_fin"), str) and RE_HORA.fullmatch(cp["hora_fin"]) else ""
    if not inicio or not fin or _minutos(fin) < _minutos(inicio):
        inicio, fin = DEFECTO["hora_inicio"], DEFECTO["hora_fin"]
    zona = cp["zona_horaria"] if isinstance(cp.get("zona_horaria"), str) and RE_ZONA_HORARIA.fullmatch(cp["zona_horaria"]) else ""
    return {"activa": cp.get("activa", True) is not False,
            "titulo": _texto(cp.get("titulo"), 80) or DEFECTO["titulo"],
            "mensaje": mensaje or DEFECTO["mensaje"],
            "dias": dias or list(DEFECTO["dias"]),
            "hora_inicio": inicio, "hora_fin": fin,
            "duracion": cp.get("duracion") if cp.get("duracion") in DURACIONES else DEFECTO["duracion"],
            "capacidad": _entero(cp.get("capacidad"), 1, 20, DEFECTO["capacidad"]),
            "dias_adelante": _entero(cp.get("dias_adelante"), 1, 60, DEFECTO["dias_adelante"]),
            "zona_horaria": zona or DEFECTO["zona_horaria"]}


def horas_del_dia(f):
    """Horas en que puede empezar una cita: de la primera a la última hora de atención (inclusive), cada «duración»."""
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(_minutos(f["hora_inicio"]), _minutos(f["hora_fin"]) + 1, f["duracion"])]


def disponibles(f, ocupadas, ahora):
    """Días con al menos un espacio libre y sus horas libres, en la hora local del negocio.

    ocupadas: inicios (hora local, «AAAA-MM-DDTHH:MM») de las citas confirmadas y de las solicitudes que ya pidieron
    hora; ahora: la hora local actual (sin zona). Un espacio está libre si se empalman con él menos citas que la capacidad.
    """
    inicios = []
    for texto in ocupadas:
        try:
            inicios.append(datetime.fromisoformat(texto))
        except (TypeError, ValueError):
            pass
    duracion = timedelta(minutes=f["duracion"])
    desde = ahora + timedelta(minutes=AVISO_MINUTOS)
    horas = horas_del_dia(f)
    dias = []
    for i in range(f["dias_adelante"] + 1):
        dia = ahora.date() + timedelta(days=i)
        if dia.weekday() not in f["dias"]:
            continue
        libres = []
        for h in horas:
            ini = datetime.combine(dia, hora_del_dia(int(h[:2]), int(h[3:])))
            if ini < desde:
                continue
            if sum(1 for o in inicios if o < ini + duracion and ini < o + duracion) < f["capacidad"]:
                libres.append(h)
        if libres:
            dias.append({"fecha": dia.isoformat(), "horas": libres})
    return dias


def _catalogo(config):
    zonas = config.get("zonas") if isinstance(config.get("zonas"), list) else []
    casas = config.get("propiedades") if isinstance(config.get("propiedades"), list) else []
    zonas = [z for z in zonas if isinstance(z, dict) and isinstance(z.get("id"), str)]
    casas = [p for p in casas if isinstance(p, dict) and isinstance(p.get("id"), str) and p.get("estatus") != "vendido"]
    return zonas, casas


def pagina(config, nombre_negocio):
    """Lo que ve el cliente en la página para agendar."""
    f = formato(config)
    zonas, casas = _catalogo(config)
    ids = {z["id"] for z in zonas}
    negocio = config.get("negocio") if isinstance(config.get("negocio"), dict) else {}

    def casa(p):
        return {"id": p["id"], "nombre": _texto(p.get("nombre"), 120)}

    return {
        "negocio": {"nombre": _texto(negocio.get("nombre") or nombre_negocio, 120),
                    "tipo": negocio.get("tipo") if negocio.get("tipo") in ("intermediacion", "desarrollo") else "intermediacion"},
        "titulo": f["titulo"], "mensaje": f["mensaje"], "duracion": f["duracion"],
        "zonas": [{"id": z["id"], "nombre": _texto(z.get("nombre"), 120),
                   "casas": [casa(p) for p in casas if p.get("zona") == z["id"]]} for z in zonas],
        "sueltas": [casa(p) for p in casas if p.get("zona") not in ids],
    }


class Ocupado(ValueError):
    """El espacio que eligió el cliente se ocupó mientras llenaba la página."""


def limpiar(datos, config, hoy=None, libres=None):
    """Valida lo que manda la página contra el catálogo del negocio. Devuelve (tel, datos) o lanza ValueError con un
    mensaje que se le puede mostrar al cliente. Con «libres» (lo que devuelve disponibles) exige además que el día y la
    hora sigan libres; sin él (el gestor reenvía lo que ya validó en su red local) solo revisa que tengan forma válida."""
    if not isinstance(datos, dict):
        raise ValueError("Formato inválido.")
    f = formato(config)
    nombre = _texto(datos.get("nombre"), 80)
    if len(nombre) < 2:
        raise ValueError("Escribe tu nombre.")
    metodo = datos.get("metodo") or "telefono"
    tel, contacto = clave_contacto(metodo, datos.get("contacto", datos.get("tel")))
    zonas, casas = _catalogo(config)
    zona = datos.get("zona") if isinstance(datos.get("zona"), str) else ""
    casa = datos.get("casa") if isinstance(datos.get("casa"), str) else ""
    if casa:
        propiedad = next((p for p in casas if p["id"] == casa), None)
        if not propiedad:
            raise ValueError("Elige una propiedad de la lista.")
        zona = zona or (propiedad.get("zona") if isinstance(propiedad.get("zona"), str) else "")
    if zona and not any(z["id"] == zona for z in zonas):
        raise ValueError("Elige una zona de la lista.")
    if casas and not casa:
        raise ValueError("Elige la propiedad que te interesa.")
    if not zona and not casa and zonas:
        raise ValueError("Elige lo que te interesa.")
    fecha = datos.get("fecha") if isinstance(datos.get("fecha"), str) else ""
    if not fecha:
        raise ValueError("Elige el día de tu visita.")
    try:
        dia = date.fromisoformat(fecha)
    except ValueError:
        raise ValueError("La fecha no es válida.")
    hoy = hoy or datetime.now(timezone.utc).date()
    if not hoy - timedelta(days=1) <= dia <= hoy + timedelta(days=DIAS_MAX):   # un día de holgura por la zona horaria
        raise ValueError("Elige una fecha entre hoy y los próximos 4 meses.")
    horario = datos.get("horario") if isinstance(datos.get("horario"), str) else ""
    if not RE_HORA.fullmatch(horario):
        raise ValueError("Elige la hora de tu visita.")
    if libres is not None and horario not in next((d["horas"] for d in libres if d["fecha"] == fecha), ()):
        raise Ocupado("Ese horario ya no está disponible. Elige otro, por favor.")
    asesor = datos.get("asesor") if isinstance(datos.get("asesor"), str) and RE_ID.fullmatch(datos.get("asesor") or "") else ""
    return tel, {"nombre": nombre, "tel": tel, "metodo": metodo, "contacto": contacto, "zona": zona, "casa": casa,
                 "fecha": fecha, "horario": horario, "asesor": asesor}


def _permitido(clave):
    """Freno contra envíos en ráfaga desde una misma dirección (memoria del proceso; basta para una página de citas)."""
    ahora = time.monotonic()
    with _envios_lock:
        if len(_envios) > 5000:
            _envios.clear()
        recientes = [t for t in _envios.get(clave, []) if ahora - t < VENTANA_ENVIOS]
        permitido = len(recientes) < LIMITE_ENVIOS
        if permitido:
            recientes.append(ahora)
        _envios[clave] = recientes
        return permitido


def _config_publica(token):
    """(nombre del negocio, configuración) del token si el negocio está activo y la agenda encendida; si no, None."""
    if not RE_TOKEN.fullmatch(token):
        return None
    with db.conexion() as con:
        fila = con.execute("SELECT * FROM config_publica(%s)", (token,)).fetchone()
    if not fila:
        return None
    datos = fila["datos"] if isinstance(fila["datos"], dict) else {}
    return (fila["nombre"], datos) if formato(datos)["activa"] else None


def _libres(token, config, excluir_tel=""):
    """Horarios libres del negocio del token, con su horario de atención y las horas ya ocupadas (sin datos de nadie)."""
    f = formato(config)
    with db.conexion() as con:
        filas = con.execute("SELECT * FROM agenda_ocupada_publica(%s, %s, %s)",
                            (token, f["zona_horaria"], excluir_tel)).fetchall()
    ahora = next((datetime.fromisoformat(r["cuando"]) for r in filas if r["tipo"] == "ahora"), None)
    if ahora is None:
        return []
    return disponibles(f, [r["cuando"] for r in filas if r["tipo"] != "ahora"], ahora)


@router.get("/publico/{token}/cita")
def agenda_publica(token: str):
    encontrado = _config_publica(token)
    if not encontrado:
        raise HTTPException(404, NO_DISPONIBLE)
    return JSONResponse({**pagina(encontrado[1], encontrado[0]), "disponibles": _libres(token, encontrado[1])},
                        headers=CABECERAS)


@router.post("/publico/{token}/cita")
def pedir_cita(token: str, request: Request, datos: dict = Body(...)):
    encontrado = _config_publica(token)
    if not encontrado:
        raise HTTPException(404, NO_DISPONIBLE)
    if datos.get("sitio"):                # campo trampa invisible: solo un robot lo llena; se le responde igual
        return JSONResponse({"ok": True}, headers=CABECERAS)
    try:
        tel, limpio = limpiar(datos, encontrado[1], libres=_libres(token, encontrado[1], _clave_de(datos)))
    except Ocupado as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    # Solo cuentan las solicitudes válidas: un error de captura no le gasta intentos al cliente.
    if not _permitido((request.client.host if request.client else "", token)):
        raise HTTPException(429, "Recibimos varias solicitudes seguidas. Intenta de nuevo en unos minutos.")
    with db.transaccion() as con:         # sin negocio en la sesión: la función exige el token
        nuevo = con.execute("SELECT crear_solicitud_publica(%s, %s, %s) AS id", (token, tel, Jsonb(limpio))).fetchone()["id"]
    if not nuevo:
        raise HTTPException(404, NO_DISPONIBLE)
    return JSONResponse({"ok": True}, headers=CABECERAS)


def _vista(fila):
    datos = fila["datos"] if isinstance(fila["datos"], dict) else {}
    return {**datos, "id": str(fila["id"]), "tel": fila["tel"], "creada": fila["creada"].isoformat(timespec="seconds"),
            "estado": fila["estado"], "nota": fila["nota"],
            "confirmada_para": fila["confirmada_para"].isoformat(timespec="minutes") if fila["confirmada_para"] else ""}


@router.get("/solicitudes")
def listar_solicitudes(ident=Depends(solo_admin)):
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute("SELECT * FROM solicitudes_cita ORDER BY creada DESC LIMIT 500").fetchall()
    return {"solicitudes": [_vista(f) for f in filas]}


@router.post("/solicitudes")
def crear_solicitud(datos: dict = Body(...), ident=Depends(solo_admin)):
    """El gestor de Windows reenvía aquí las solicitudes que llegan por su red local mientras la nube está activa."""
    with db.transaccion(ident.negocio_id) as con:
        fila = con.execute("SELECT datos FROM configuraciones WHERE negocio_id = %s", (ident.negocio_id,)).fetchone()
        config = fila["datos"] if fila and isinstance(fila["datos"], dict) else {}
        try:
            tel, limpio = limpiar(datos, config)
        except ValueError as e:
            raise HTTPException(422, str(e))
        actualizada = con.execute("UPDATE solicitudes_cita SET datos = %s, actualizada = now() "
                                  "WHERE tel = %s AND estado = 'pendiente' RETURNING id", (Jsonb(limpio), tel)).fetchone()
        if not actualizada:
            con.execute("INSERT INTO solicitudes_cita (negocio_id, tel, datos) VALUES (%s, %s, %s)",
                        (ident.negocio_id, tel, Jsonb(limpio)))
    return {"ok": True}


class CambioSolicitud(BaseModel):
    estado: Literal["pendiente", "confirmada", "descartada"] | None = None
    confirmada_para: str | None = Field(None, max_length=40)      # ISO 8601; vacío la borra
    nota: str | None = Field(None, max_length=300)
    asesor: str | None = Field(None, max_length=60)               # id del asesor que la atiende; vacío lo quita


@router.patch("/solicitudes/{sol_id}")
def actualizar_solicitud(sol_id: uuid.UUID, cambio: CambioSolicitud, ident=Depends(solo_admin)):
    sets, valores = [], []
    if cambio.estado:
        sets.append("estado = %s")
        valores.append(cambio.estado)
    if cambio.confirmada_para is not None:
        if cambio.confirmada_para == "":
            sets.append("confirmada_para = NULL")
        else:
            try:
                cuando = datetime.fromisoformat(cambio.confirmada_para.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(422, "La fecha de la cita no es válida.")
            sets.append("confirmada_para = %s")
            valores.append(cuando if cuando.tzinfo else cuando.replace(tzinfo=timezone.utc))
    if cambio.nota is not None:
        sets.append("nota = %s")
        valores.append(cambio.nota.strip())
    if cambio.asesor is not None:
        if cambio.asesor and not RE_ID.fullmatch(cambio.asesor):
            raise HTTPException(422, "El asesor no es válido.")
        sets.append("datos = datos || jsonb_build_object('asesor', %s::text)")
        valores.append(cambio.asesor)
    if not sets:
        raise HTTPException(422, "No hay nada que cambiar.")
    try:
        with db.transaccion(ident.negocio_id) as con:
            fila = con.execute(f"UPDATE solicitudes_cita SET {', '.join(sets)}, actualizada = now() WHERE id = %s RETURNING *",
                               (*valores, sol_id)).fetchone()
    except Exception as e:      # reabrir una solicitud cuando ese teléfono ya tiene otra por confirmar
        if "solicitudes_cita_una_pendiente" in str(e):
            raise HTTPException(409, "Ese teléfono ya tiene otra solicitud por confirmar.")
        raise
    if not fila:
        raise HTTPException(404, "No encontramos la solicitud.")
    return _vista(fila)


@router.delete("/solicitudes/{sol_id}")
def eliminar_solicitud(sol_id: uuid.UUID, ident=Depends(solo_admin)):
    """Borra la solicitud de verdad. El gestor la pide con doble confirmación; descartarla sigue siendo lo normal."""
    with db.transaccion(ident.negocio_id) as con:
        fila = con.execute("DELETE FROM solicitudes_cita WHERE id = %s RETURNING id", (sol_id,)).fetchone()
    if not fila:
        raise HTTPException(404, "No encontramos la solicitud.")
    return {"ok": True}
