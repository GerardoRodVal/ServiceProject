"""CRM local: contactos, embudos (pipelines y etapas), oportunidades, actividades y tareas en el PostgreSQL de esta PC.

Responde a las mismas rutas y con la misma forma que la API de la nube (nube/api/app/nucleo.py), para que las pestañas
Contactos, Embudo y Tareas del panel funcionen sin cambios. El panel las pide como /api/crm/<ruta>.

Prospectos y embudo son lo mismo (como en nube/api/app/visitas_nucleo.py):
- Una oportunidad abierta por persona; sus visitas quedan como su historial.
- La persona se reconoce por su teléfono a 10 dígitos. Sin teléfono, cada visita crea su propio contacto.
- La oportunidad toma la calificación y el score de su visita más reciente; su valor, el precio del inmueble.
- La etapa de la oportunidad y el estado de sus visitas se mantienen iguales.
- Las visitas de ejemplo no se ligan.
"""
import json
import re
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from bd import a_json, jsonb, transaccion

GIRO = "inmobiliarias"
ETAPAS_DEFECTO = [("Nuevo", "abierta"), ("Contactado", "abierta"), ("Negociando", "abierta"),
                  ("Cerrado", "ganada"), ("Perdido", "perdida")]
ESTADOS = ("nuevo", "contactado", "negociando", "cerrado", "perdido")
CALIFICACION = ("presupuesto", "enganche", "credito", "plazo", "decide")
TIPOS_ACTIVIDAD = ("nota", "llamada", "mensaje", "reunion", "visita", "cita", "sistema")
ESTADOS_TAREA = ("pendiente", "hecha", "cancelada")
MAX_DATOS_GIRO = 16 * 1024
MAX_ETAPAS = 12
USUARIO = "administrador"           # quién cambia desde el panel (en esta edición solo entra el administrador)

RE_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
RE_RUTA = re.compile(rf"/api/crm/(?:(contactos|oportunidades|pipelines|actividades|tareas)(?:/({RE_UUID}))?|(visitas/ligar))")


class ErrorCrm(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo


def _422(mensaje):
    return ErrorCrm(422, mensaje)


# --- Validación de entrada (lo que en la nube hace pydantic) ---

def _texto(datos, campo, maximo, minimo=0, defecto=""):
    valor = datos.get(campo, defecto)
    if valor is None:
        valor = defecto
    if not isinstance(valor, str):
        raise _422(f"{campo} debe ser texto.")
    valor = valor.strip()
    if not minimo <= len(valor) <= maximo:
        raise _422(f"{campo} debe tener entre {minimo} y {maximo} caracteres." if minimo
                   else f"{campo} admite hasta {maximo} caracteres.")
    return valor


def _uuid(valor, nombre):
    if valor in (None, ""):
        return None
    try:
        return uuid.UUID(str(valor))
    except ValueError:
        raise _422(f"{nombre} no es un identificador válido.")


def _decimal(valor, nombre, maximo=10**12):
    try:
        d = Decimal(str(valor if valor not in (None, "") else 0))
    except InvalidOperation:
        raise _422(f"{nombre} debe ser un número.")
    if not d.is_finite() or d < 0 or d > maximo:
        raise _422(f"{nombre} debe estar entre 0 y {maximo}.")
    return d


def _entero(valor, nombre, minimo, maximo):
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or int(valor) != valor or not minimo <= valor <= maximo:
        raise _422(f"{nombre} debe ser un entero entre {minimo} y {maximo}.")
    return int(valor)


def _fecha_hora(valor, nombre):
    if valor in (None, ""):
        return None
    try:
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        raise _422(f"{nombre} no es una fecha válida.")
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _fecha(valor, nombre):
    if valor in (None, ""):
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        raise _422(f"{nombre} no es una fecha válida.")


def _etiquetas(valor):
    if valor is None:
        return []
    if not isinstance(valor, list) or len(valor) > 20 or not all(isinstance(x, str) and len(x) <= 40 for x in valor):
        raise _422("etiquetas debe ser una lista de hasta 20 textos cortos.")
    return [x.strip() for x in valor if x.strip()]


def _datos_giro(valor):
    if not isinstance(valor, dict):
        raise _422("datos_giro debe ser un objeto.")
    if len(json.dumps(valor, ensure_ascii=False)) > MAX_DATOS_GIRO:
        raise _422("Los datos del giro pasan de 16 KB.")
    return valor


def _ilike(texto):
    return "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _limite(q, defecto, maximo):
    try:
        n = int((q.get("limite") or [defecto])[0])
    except ValueError:
        raise _422("limite debe ser un número.")
    if not 1 <= n <= maximo:
        raise _422(f"limite debe estar entre 1 y {maximo}.")
    return n


def _q_uuid(q, campo):
    return _uuid((q.get(campo) or [None])[0], campo)


# --- Ayudas de datos ---

def _responsable(con, responsable_id):
    """Un asesor de este negocio, o nada."""
    if responsable_id in (None, ""):
        return None
    if not con.execute("SELECT 1 FROM asesores WHERE id = %s", (str(responsable_id),)).fetchone():
        raise _422("El responsable no existe en tu negocio.")
    return str(responsable_id)


def _historial(con, entidad, entidad_id, antes, despues):
    """Una fila por campo que cambió. Se compara como texto para que 10 y «10» no cuenten como cambio."""
    for campo, nuevo in despues.items():
        viejo = antes.get(campo)
        if str(viejo) == str(nuevo):
            continue
        con.execute("INSERT INTO historial (entidad, entidad_id, campo, antes, despues, usuario) VALUES (%s, %s, %s, %s, %s, %s)",
                    (entidad, str(entidad_id), campo, None if viejo is None else str(viejo),
                     None if nuevo is None else str(nuevo), USUARIO))


def _etapa_de(con, pipeline_id, etapa_id):
    fila = con.execute("SELECT pipeline_id, tipo FROM etapas WHERE id = %s", (etapa_id,)).fetchone()
    if not fila:
        raise _422("La etapa no existe en tu negocio.")
    if fila["pipeline_id"] != pipeline_id:
        raise _422("La etapa no pertenece al pipeline de la oportunidad.")
    return fila["tipo"]


def _marca_de_cierre(tipo):
    return datetime.now(timezone.utc) if tipo in ("ganada", "perdida") else None


def _actualizar(con, tabla, id_, cambios):
    columnas = list(cambios)
    valores = [jsonb(cambios[c]) if c == "datos_giro" else cambios[c] for c in columnas]
    asignar = ", ".join(f"{c} = %s::jsonb" if c == "datos_giro" else f"{c} = %s" for c in columnas)
    return con.execute(f"UPDATE {tabla} SET {asignar}, actualizado = now() WHERE id = %s RETURNING *",
                       valores + [id_]).fetchone()


# --- Contactos ---

def contactos_listar(con, q):
    texto = (q.get("q") or [""])[0].strip()
    filas = con.execute("""SELECT * FROM contactos
                           WHERE %s = '' OR nombre ILIKE %s OR telefono LIKE %s
                           ORDER BY actualizado DESC LIMIT %s""",
                        (texto, _ilike(texto), _ilike(texto), _limite(q, 50, 200))).fetchall()
    return {"contactos": filas}


def contacto_crear(con, d):
    tipo = d.get("tipo") or "persona"
    if tipo not in ("persona", "empresa"):
        raise _422("tipo debe ser persona o empresa.")
    fila = con.execute(
        """INSERT INTO contactos (tipo, nombre, telefono, correo, origen, responsable_id, etiquetas)
           VALUES (%s, %s, %s, %s, %s, %s, %s::text[]) RETURNING *""",
        (tipo, _texto(d, "nombre", 120, 1), _texto(d, "telefono", 30), _texto(d, "correo", 200),
         _texto(d, "origen", 60), _responsable(con, d.get("responsable_id")), _etiquetas(d.get("etiquetas")))).fetchone()
    return {"contacto": fila}


def contacto_ficha(con, id_):
    contacto = con.execute("SELECT * FROM contactos WHERE id = %s", (id_,)).fetchone()
    if not contacto:
        raise ErrorCrm(404, "No encontramos a ese contacto.")
    return {"contacto": contacto,
            "oportunidades": con.execute("SELECT * FROM oportunidades WHERE contacto_id = %s ORDER BY creado DESC",
                                         (id_,)).fetchall(),
            "actividades": con.execute("SELECT * FROM actividades WHERE contacto_id = %s ORDER BY ocurrido DESC LIMIT 50",
                                       (id_,)).fetchall()}


def contacto_actualizar(con, id_, d):
    actual = con.execute("SELECT * FROM contactos WHERE id = %s FOR UPDATE", (id_,)).fetchone()
    if not actual:
        raise ErrorCrm(404, "No encontramos a ese contacto.")
    cambios = {}
    for campo in ("tipo", "nombre", "telefono", "correo", "origen", "etiquetas"):
        if campo in d and d[campo] is None:
            raise _422(f"{campo} no puede quedar vacío.")
    if "tipo" in d:
        if d["tipo"] not in ("persona", "empresa"):
            raise _422("tipo debe ser persona o empresa.")
        cambios["tipo"] = d["tipo"]
    for campo, maximo, minimo in (("nombre", 120, 1), ("telefono", 30, 0), ("correo", 200, 0), ("origen", 60, 0)):
        if campo in d:
            cambios[campo] = _texto(d, campo, maximo, minimo)
    if "etiquetas" in d:
        cambios["etiquetas"] = _etiquetas(d["etiquetas"])
    if "responsable_id" in d:
        cambios["responsable_id"] = _responsable(con, d["responsable_id"])
    if not cambios:
        return {"contacto": actual}
    _historial(con, "contacto", id_, {k: actual[k] for k in cambios}, cambios)
    columnas = list(cambios)
    asignar = ", ".join(f"{c} = %s::text[]" if c == "etiquetas" else f"{c} = %s" for c in columnas)
    fila = con.execute(f"UPDATE contactos SET {asignar}, actualizado = now() WHERE id = %s RETURNING *",
                       [cambios[c] for c in columnas] + [id_]).fetchone()
    return {"contacto": fila}


# --- Pipelines y etapas ---

def _con_etapas(con, pipelines):
    por_pipeline = {}
    for e in con.execute("SELECT * FROM etapas ORDER BY pipeline_id, orden").fetchall():
        por_pipeline.setdefault(e["pipeline_id"], []).append(e)
    return [{**p, "etapas": por_pipeline.get(p["id"], [])} for p in pipelines]


def pipelines_listar(con):
    return {"pipelines": _con_etapas(con, con.execute("SELECT * FROM pipelines ORDER BY por_defecto DESC, nombre").fetchall())}


def pipeline_crear(con, d):
    nombre = _texto(d, "nombre", 80, 1)
    giro = _texto(d, "giro", 40)
    etapas = d.get("etapas")
    if not isinstance(etapas, list) or not 2 <= len(etapas) <= MAX_ETAPAS:
        raise _422(f"El pipeline necesita entre 2 y {MAX_ETAPAS} etapas.")
    limpias = []
    for e in etapas:
        if not isinstance(e, dict):
            raise _422("Cada etapa necesita nombre y tipo.")
        tipo = e.get("tipo") or "abierta"
        if tipo not in ("abierta", "ganada", "perdida"):
            raise _422("El tipo de etapa debe ser abierta, ganada o perdida.")
        limpias.append((_texto(e, "nombre", 80, 1), tipo))
    if not any(t == "abierta" for _, t in limpias):
        raise _422("El pipeline necesita al menos una etapa abierta.")
    if len({n.lower() for n, _ in limpias}) != len(limpias):
        raise _422("Las etapas no pueden repetir nombre.")
    por_defecto = d.get("por_defecto") is True
    if por_defecto:
        con.execute("UPDATE pipelines SET por_defecto = false WHERE por_defecto")
    pipeline = con.execute("INSERT INTO pipelines (nombre, giro, por_defecto) VALUES (%s, %s, %s) RETURNING *",
                           (nombre, giro, por_defecto)).fetchone()
    for orden, (n, t) in enumerate(limpias):
        con.execute("INSERT INTO etapas (pipeline_id, nombre, orden, tipo) VALUES (%s, %s, %s, %s)",
                    (pipeline["id"], n, orden, t))
    return {"pipeline": _con_etapas(con, [pipeline])[0]}


# --- Oportunidades ---

def oportunidades_listar(con, q):
    contacto_id, etapa_id = _q_uuid(q, "contacto_id"), _q_uuid(q, "etapa_id")
    filas = con.execute("""SELECT * FROM oportunidades
                           WHERE (%s::uuid IS NULL OR contacto_id = %s::uuid) AND (%s::uuid IS NULL OR etapa_id = %s::uuid)
                           ORDER BY actualizado DESC LIMIT %s""",
                        (contacto_id, contacto_id, etapa_id, etapa_id, _limite(q, 100, 500))).fetchall()
    return {"oportunidades": filas}


def oportunidad_crear(con, d):
    contacto_id = _uuid(d.get("contacto_id"), "contacto_id")
    if contacto_id is None or not con.execute("SELECT 1 FROM contactos WHERE id = %s", (contacto_id,)).fetchone():
        raise _422("El contacto no existe en tu negocio.")
    pipeline_id = _uuid(d.get("pipeline_id"), "pipeline_id")
    if pipeline_id is None:
        defecto = con.execute("SELECT id FROM pipelines WHERE por_defecto").fetchone()
        if not defecto:
            raise _422("Crea primero un pipeline o indica pipeline_id.")
        pipeline_id = defecto["id"]
    elif not con.execute("SELECT 1 FROM pipelines WHERE id = %s", (pipeline_id,)).fetchone():
        raise _422("El pipeline no existe en tu negocio.")
    etapa_id = _uuid(d.get("etapa_id"), "etapa_id")
    if etapa_id is None:
        inicial = con.execute("SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND tipo = 'abierta' ORDER BY orden LIMIT 1",
                              (pipeline_id,)).fetchone()
        if not inicial:
            raise _422("El pipeline no tiene una etapa abierta.")
        etapa_id, tipo = inicial["id"], inicial["tipo"]
    else:
        tipo = _etapa_de(con, pipeline_id, etapa_id)
    fila = con.execute(
        """INSERT INTO oportunidades (contacto_id, pipeline_id, etapa_id, giro, valor, probabilidad, cierre_estimado,
               responsable_id, datos_giro, cerrado_en)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING *""",
        (contacto_id, pipeline_id, etapa_id, _texto(d, "giro", 40), _decimal(d.get("valor"), "valor"),
         _entero(d.get("probabilidad"), "probabilidad", 0, 100), _fecha(d.get("cierre_estimado"), "cierre_estimado"),
         _responsable(con, d.get("responsable_id")), jsonb(_datos_giro(d.get("datos_giro") or {})),
         _marca_de_cierre(tipo))).fetchone()
    return {"oportunidad": fila}


def oportunidad_ficha(con, id_):
    fila = con.execute("SELECT * FROM oportunidades WHERE id = %s", (id_,)).fetchone()
    if not fila:
        raise ErrorCrm(404, "No encontramos esa oportunidad.")
    return {"oportunidad": fila,
            "actividades": con.execute("SELECT * FROM actividades WHERE oportunidad_id = %s ORDER BY ocurrido DESC LIMIT 50",
                                       (id_,)).fetchall(),
            "historial": con.execute("""SELECT * FROM historial WHERE entidad = 'oportunidad' AND entidad_id = %s
                                        ORDER BY cuando DESC LIMIT 100""", (str(id_),)).fetchall()}


def oportunidad_actualizar(con, id_, d):
    for campo in ("etapa_id", "valor", "datos_giro", "motivo_cierre"):
        if campo in d and d[campo] is None:
            raise _422(f"{campo} no puede quedar vacío.")
    actual = con.execute("SELECT * FROM oportunidades WHERE id = %s FOR UPDATE", (id_,)).fetchone()
    if not actual:
        raise ErrorCrm(404, "No encontramos esa oportunidad.")
    cambios, upd = {}, {}
    if "etapa_id" in d:
        cambios["etapa_id"] = _uuid(d["etapa_id"], "etapa_id")
        upd["cerrado_en"] = _marca_de_cierre(_etapa_de(con, actual["pipeline_id"], cambios["etapa_id"]))
    if "valor" in d:
        cambios["valor"] = _decimal(d["valor"], "valor")
    if "probabilidad" in d:
        cambios["probabilidad"] = _entero(d["probabilidad"], "probabilidad", 0, 100)
    if "cierre_estimado" in d:
        cambios["cierre_estimado"] = _fecha(d["cierre_estimado"], "cierre_estimado")
    if "responsable_id" in d:
        cambios["responsable_id"] = _responsable(con, d["responsable_id"])
    if "motivo_cierre" in d:
        cambios["motivo_cierre"] = _texto(d, "motivo_cierre", 300)
    if "datos_giro" in d:
        cambios["datos_giro"] = _datos_giro(d["datos_giro"])
    if not cambios:
        return {"oportunidad": actual}
    _historial(con, "oportunidad", id_, {k: actual[k] for k in cambios if k != "datos_giro"},
               {k: v for k, v in cambios.items() if k != "datos_giro"})
    if "datos_giro" in cambios:
        _historial(con, "oportunidad", id_, {"datos_giro": json.dumps(actual["datos_giro"], ensure_ascii=False, sort_keys=True)},
                   {"datos_giro": json.dumps(cambios["datos_giro"], ensure_ascii=False, sort_keys=True)})
    fila = _actualizar(con, "oportunidades", id_, {**cambios, **upd})
    if "etapa_id" in cambios:
        estado_de_visitas(con, id_)      # sus visitas muestran la misma etapa
    return {"oportunidad": fila}


# --- Actividades: se agregan, no se editan ---

def actividades_listar(con, q):
    contacto_id, oportunidad_id = _q_uuid(q, "contacto_id"), _q_uuid(q, "oportunidad_id")
    filas = con.execute("""SELECT * FROM actividades
                           WHERE (%s::uuid IS NULL OR contacto_id = %s::uuid) AND (%s::uuid IS NULL OR oportunidad_id = %s::uuid)
                           ORDER BY ocurrido DESC LIMIT %s""",
                        (contacto_id, contacto_id, oportunidad_id, oportunidad_id, _limite(q, 50, 200))).fetchall()
    return {"actividades": filas}


def actividad_crear(con, d):
    if d.get("tipo") not in TIPOS_ACTIVIDAD:
        raise _422("Tipo de actividad no válido.")
    contacto_id, oportunidad_id = _uuid(d.get("contacto_id"), "contacto_id"), _uuid(d.get("oportunidad_id"), "oportunidad_id")
    if contacto_id and not con.execute("SELECT 1 FROM contactos WHERE id = %s", (contacto_id,)).fetchone():
        raise _422("El contacto u oportunidad no existe en tu negocio.")
    if oportunidad_id and not con.execute("SELECT 1 FROM oportunidades WHERE id = %s", (oportunidad_id,)).fetchone():
        raise _422("El contacto u oportunidad no existe en tu negocio.")
    fila = con.execute(
        """INSERT INTO actividades (tipo, contacto_id, oportunidad_id, ocurrido, duracion_min, texto)
           VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s) RETURNING *""",
        (d["tipo"], contacto_id, oportunidad_id, _fecha_hora(d.get("ocurrido"), "ocurrido"),
         _entero(d.get("duracion_min"), "duracion_min", 0, 100000), _texto(d, "texto", 4000))).fetchone()
    return {"actividad": fila}


# --- Tareas: recordatorios con fecha ---

def tareas_listar(con, q):
    estado = (q.get("estado") or [None])[0]
    if estado is not None and estado not in ESTADOS_TAREA:
        raise _422("estado no válido.")
    responsable = (q.get("responsable_id") or [None])[0]
    contacto_id, oportunidad_id = _q_uuid(q, "contacto_id"), _q_uuid(q, "oportunidad_id")
    vencidas = (q.get("vencidas") or ["false"])[0].lower() in ("1", "true", "si", "sí")
    filas = con.execute(
        """SELECT * FROM tareas
           WHERE (%s::text IS NULL OR estado = %s::text)
             AND (%s::text IS NULL OR responsable_id = %s::text)
             AND (%s::uuid IS NULL OR contacto_id = %s::uuid)
             AND (%s::uuid IS NULL OR oportunidad_id = %s::uuid)
             AND (NOT %s OR (estado = 'pendiente' AND vence < now()))
           ORDER BY (estado = 'pendiente') DESC, vence ASC NULLS LAST, creado DESC
           LIMIT %s""",
        (estado, estado, responsable, responsable, contacto_id, contacto_id, oportunidad_id, oportunidad_id,
         vencidas, _limite(q, 100, 500))).fetchall()
    return {"tareas": filas}


def tarea_crear(con, d):
    contacto_id, oportunidad_id = _uuid(d.get("contacto_id"), "contacto_id"), _uuid(d.get("oportunidad_id"), "oportunidad_id")
    if contacto_id and not con.execute("SELECT 1 FROM contactos WHERE id = %s", (contacto_id,)).fetchone():
        raise _422("El contacto u oportunidad no existe en tu negocio.")
    if oportunidad_id and not con.execute("SELECT 1 FROM oportunidades WHERE id = %s", (oportunidad_id,)).fetchone():
        raise _422("El contacto u oportunidad no existe en tu negocio.")
    fila = con.execute(
        """INSERT INTO tareas (titulo, vence, responsable_id, contacto_id, oportunidad_id)
           VALUES (%s, %s, %s, %s, %s) RETURNING *""",
        (_texto(d, "titulo", 200, 1), _fecha_hora(d.get("vence"), "vence"), _responsable(con, d.get("responsable_id")),
         contacto_id, oportunidad_id)).fetchone()
    return {"tarea": fila}


def tarea_actualizar(con, id_, d):
    for campo in ("titulo", "estado"):
        if campo in d and d[campo] is None:
            raise _422(f"{campo} no puede quedar vacío.")
    actual = con.execute("SELECT * FROM tareas WHERE id = %s FOR UPDATE", (id_,)).fetchone()
    if not actual:
        raise ErrorCrm(404, "No encontramos esa tarea.")
    cambios = {}
    if "titulo" in d:
        cambios["titulo"] = _texto(d, "titulo", 200, 1)
    if "vence" in d:
        cambios["vence"] = _fecha_hora(d["vence"], "vence")
    if "responsable_id" in d:
        cambios["responsable_id"] = _responsable(con, d["responsable_id"])
    if "estado" in d:
        if d["estado"] not in ESTADOS_TAREA:
            raise _422("estado no válido.")
        cambios["estado"] = d["estado"]
    if not cambios:
        return {"tarea": actual}
    upd = dict(cambios)
    if "estado" in cambios:
        # Solo una tarea hecha tiene fecha de cumplimiento; reabrirla o cancelarla la quita.
        upd["completada_en"] = datetime.now(timezone.utc) if cambios["estado"] == "hecha" else None
    _historial(con, "tarea", id_, {k: actual[k] for k in cambios}, cambios)
    return {"tarea": _actualizar(con, "tareas", id_, upd)}


# --- Visitas al embudo ---

def tel10(valor):
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos[-10:] if len(digitos) >= 10 else ""


def pipeline_defecto(con):
    """El embudo por defecto; si no hay, se crea «Ventas» con las cinco etapas de siempre."""
    fila = con.execute("SELECT id FROM pipelines WHERE por_defecto").fetchone()
    if fila:
        return fila["id"]
    pid = con.execute("INSERT INTO pipelines (nombre, giro, por_defecto) VALUES ('Ventas', %s, true) RETURNING id",
                      (GIRO,)).fetchone()["id"]
    for orden, (nombre, tipo) in enumerate(ETAPAS_DEFECTO):
        con.execute("INSERT INTO etapas (pipeline_id, nombre, orden, tipo) VALUES (%s, %s, %s, %s)", (pid, nombre, orden, tipo))
    return pid


def etapa_para(con, pipeline_id, estado):
    fila = con.execute("SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND lower(nombre) = %s",
                       (pipeline_id, estado)).fetchone()
    return fila or con.execute("SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND tipo = 'abierta' ORDER BY orden LIMIT 1",
                               (pipeline_id,)).fetchone()


def datos_giro_de(visita):
    d = {k: visita[k] for k in CALIFICACION if visita.get(k) not in (None, "")}
    if visita.get("propiedad"):
        d["propiedad"] = str(visita["propiedad"])[:60]
    d["score"] = int(visita.get("score") or 0)
    d["categoria"] = visita.get("cat") or "baja"
    d["razones"] = visita.get("razones") or []
    d["ultima_visita"] = {"id": visita.get("id", ""), "asesor": str(visita.get("asesor") or "")[:60],
                          "creado": str(visita.get("creado") or "")[:40]}
    return d


def ligar(con, prospecto_id):
    """Liga una visita a su contacto y oportunidad (creándolos si hace falta). Devuelve el id de la oportunidad, o None si
    la visita no existe o es de ejemplo. Se puede repetir: una visita ya ligada solo actualiza su oportunidad."""
    r = con.execute("SELECT * FROM prospectos WHERE id = %s FOR UPDATE", (prospecto_id,)).fetchone()
    if r is None or r["demo"]:
        return None
    v = r["datos"] if isinstance(r["datos"], dict) else {}
    responsable = r["asesor_id"]

    contacto_id = r["contacto_id"]
    if contacto_id is None:
        tel = tel10(v.get("tel"))
        fila = con.execute(
            "SELECT id FROM contactos WHERE right(regexp_replace(telefono, '\\D', '', 'g'), 10) = %s ORDER BY creado LIMIT 1",
            (tel,)).fetchone() if tel else None
        if fila is None:
            fila = con.execute("""INSERT INTO contactos (nombre, telefono, origen, responsable_id)
                                  VALUES (%s, %s, 'Visita', %s) RETURNING id""",
                               ((str(v.get("nombre") or "").strip() or "Sin nombre")[:120], tel, responsable)).fetchone()
        contacto_id = fila["id"]

    oportunidad_id = r["oportunidad_id"]
    if oportunidad_id is None:
        fila = con.execute(
            """SELECT o.id FROM oportunidades o JOIN etapas e ON e.id = o.etapa_id
               WHERE o.contacto_id = %s AND o.giro IN (%s, '') AND e.tipo = 'abierta'
               ORDER BY (o.giro = %s) DESC, o.creado DESC LIMIT 1""", (contacto_id, GIRO, GIRO)).fetchone()
        if fila:
            con.execute("UPDATE oportunidades SET giro = %s WHERE id = %s AND giro = ''", (GIRO, fila["id"]))
        else:
            pipeline_id = pipeline_defecto(con)
            etapa = etapa_para(con, pipeline_id, r["estado"])
            fila = con.execute(
                """INSERT INTO oportunidades (contacto_id, pipeline_id, etapa_id, giro, responsable_id, cerrado_en)
                   VALUES (%s, %s, %s, %s, %s, CASE WHEN %s IN ('ganada', 'perdida') THEN now() END) RETURNING id""",
                (contacto_id, pipeline_id, etapa["id"], GIRO, responsable, etapa["tipo"])).fetchone()
        oportunidad_id = fila["id"]

    con.execute("UPDATE prospectos SET contacto_id = %s, oportunidad_id = %s WHERE id = %s",
                (contacto_id, oportunidad_id, prospecto_id))

    # La oportunidad toma la calificación y el score de su visita más reciente; su valor, el precio de ese inmueble
    # (solo si nadie lo escribió a mano).
    ultima = con.execute("SELECT datos, inmueble_id FROM prospectos WHERE oportunidad_id = %s ORDER BY creado DESC, recibido DESC LIMIT 1",
                         (oportunidad_id,)).fetchone()
    precio = con.execute("SELECT precio FROM inmuebles WHERE id = %s", (ultima["inmueble_id"],)).fetchone() \
        if ultima["inmueble_id"] else None
    con.execute("""UPDATE oportunidades SET datos_giro = datos_giro || %s::jsonb,
                       valor = CASE WHEN valor = 0 THEN %s ELSE valor END, actualizado = now()
                   WHERE id = %s""",
                (jsonb(datos_giro_de(ultima["datos"] if isinstance(ultima["datos"], dict) else {})),
                 precio["precio"] if precio else 0, oportunidad_id))
    estado_de_visitas(con, oportunidad_id)
    return oportunidad_id


def ligar_pendientes(con):
    ids = [f["id"] for f in con.execute(
        "SELECT id FROM prospectos WHERE oportunidad_id IS NULL AND NOT demo ORDER BY creado").fetchall()]
    for id_ in ids:
        ligar(con, id_)
    return len(ids)


def estado_de_visitas(con, oportunidad_id):
    """Tras mover una oportunidad de etapa: sus visitas toman el estado equivalente (si existe)."""
    con.execute("""UPDATE prospectos p SET estado = lower(e.nombre)
                   FROM oportunidades o JOIN etapas e ON e.id = o.etapa_id
                   WHERE o.id = %s AND p.oportunidad_id = o.id AND lower(e.nombre) = ANY(%s::text[])
                     AND p.estado <> lower(e.nombre)""", (oportunidad_id, list(ESTADOS)))


def etapa_desde_estado(con, prospecto_id, estado):
    """Tras cambiar el estado de una visita: su oportunidad pasa a la etapa con ese nombre (si existe en su embudo)."""
    fila = con.execute("""SELECT o.id, o.pipeline_id, o.etapa_id FROM prospectos p JOIN oportunidades o ON o.id = p.oportunidad_id
                          WHERE p.id = %s""", (prospecto_id,)).fetchone()
    if not fila:
        return
    etapa = con.execute("SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND lower(nombre) = %s",
                        (fila["pipeline_id"], estado)).fetchone()
    if etapa and etapa["id"] != fila["etapa_id"]:
        con.execute("""UPDATE oportunidades SET etapa_id = %s, actualizado = now(),
                           cerrado_en = CASE WHEN %s IN ('ganada', 'perdida') THEN now() END WHERE id = %s""",
                    (etapa["id"], etapa["tipo"], fila["id"]))
        estado_de_visitas(con, fila["id"])


# --- Entrada desde el servidor del panel ---

def atender(metodo, path, datos=None):
    """(código, cuerpo) para una ruta /api/crm/... del panel."""
    partes = urllib.parse.urlsplit(path)
    m = RE_RUTA.fullmatch(partes.path)
    if not m:
        return 404, {"error": "No encontrado"}
    recurso, id_, ligar_ruta = m.groups()
    q = urllib.parse.parse_qs(partes.query)
    d = datos if isinstance(datos, dict) else {}
    if metodo != "GET" and datos is not None and not isinstance(datos, dict):
        return 422, {"error": "Formato inválido."}
    try:
        with transaccion(escritura=metodo != "GET") as con:
            if ligar_ruta:
                if metodo != "POST":
                    return 405, {"error": "Método no permitido."}
                return 200, {"ok": True, "ligadas": ligar_pendientes(con)}
            id_ = uuid.UUID(id_) if id_ else None
            manejador = _RUTAS.get((recurso, metodo, id_ is not None))
            if manejador is None:
                return 405, {"error": "Método no permitido."}
            args = []
            if id_ is not None:
                args.append(id_)
            if metodo == "GET" and id_ is None and recurso != "pipelines":
                args.append(q)
            if metodo != "GET":
                args.append(d)
            return 200, a_json(manejador(con, *args))
    except ErrorCrm as e:
        return e.codigo, {"error": str(e)}


_RUTAS = {
    ("contactos", "GET", False): contactos_listar,
    ("contactos", "POST", False): contacto_crear,
    ("contactos", "GET", True): contacto_ficha,
    ("contactos", "PATCH", True): contacto_actualizar,
    ("pipelines", "GET", False): pipelines_listar,
    ("pipelines", "POST", False): pipeline_crear,
    ("oportunidades", "GET", False): oportunidades_listar,
    ("oportunidades", "POST", False): oportunidad_crear,
    ("oportunidades", "GET", True): oportunidad_ficha,
    ("oportunidades", "PATCH", True): oportunidad_actualizar,
    ("actividades", "GET", False): actividades_listar,
    ("actividades", "POST", False): actividad_crear,
    ("tareas", "GET", False): tareas_listar,
    ("tareas", "POST", False): tarea_crear,
    ("tareas", "PATCH", True): tarea_actualizar,
}
