"""Prospectos y embudo en uno: liga cada visita (registros, lo que captura el celular) con su contacto y su oportunidad.

Decisiones (ARQUITECTURA_CRM_Y_GIROS.md, sección 14):
- Una oportunidad abierta por persona en el giro inmobiliarias; sus visitas quedan como su historial.
- La persona se reconoce por su teléfono a 10 dígitos. Sin teléfono, cada visita crea su propio contacto.
- La oportunidad toma los datos de calificación y el score de su visita más reciente.
- La etapa de la oportunidad y el estado de sus visitas se mantienen iguales (el gestor y el celular siguen leyendo el estado).
- Las visitas de ejemplo no se ligan: no deben llenar el CRM con datos de prueba.

Todo corre dentro de la transacción de quien llama, con el negocio ya fijado.
"""
import re
import uuid

from psycopg.types.json import Jsonb

GIRO = "inmobiliarias"
ETAPAS_DEFECTO = [("Nuevo", "abierta"), ("Contactado", "abierta"), ("Negociando", "abierta"),
                  ("Cerrado", "ganada"), ("Perdido", "perdida")]
ESTADOS = ("nuevo", "contactado", "negociando", "cerrado", "perdido")
CALIFICACION = ("presupuesto", "enganche", "credito", "plazo", "decide")


def tel10(valor):
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos[-10:] if len(digitos) >= 10 else ""


def pipeline_defecto(con, negocio_id):
    """El embudo por defecto del negocio; si no tiene, se crea «Ventas» con las cinco etapas de siempre."""
    fila = con.execute("SELECT id FROM pipelines WHERE por_defecto").fetchone()
    if fila:
        return fila["id"]
    pid = con.execute("INSERT INTO pipelines (negocio_id, nombre, giro, por_defecto) VALUES (%s, 'Ventas', %s, true) RETURNING id",
                      (negocio_id, GIRO)).fetchone()["id"]
    for orden, (nombre, tipo) in enumerate(ETAPAS_DEFECTO):
        con.execute("INSERT INTO etapas (negocio_id, pipeline_id, nombre, orden, tipo) VALUES (%s, %s, %s, %s, %s)",
                    (negocio_id, pid, nombre, orden, tipo))
    return pid


def etapa_para(con, pipeline_id, estado):
    """La etapa con el mismo nombre que el estado de la visita; si no existe, la primera etapa abierta."""
    fila = con.execute("SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND lower(nombre) = %s", (pipeline_id, estado)).fetchone()
    return fila or con.execute("SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND tipo = 'abierta' ORDER BY orden LIMIT 1",
                               (pipeline_id,)).fetchone()


def precio_propiedad(con, propiedad):
    """Precio de una propiedad del catálogo que publica el gestor (configuraciones), o 0."""
    if not propiedad:
        return 0
    fila = con.execute(
        """SELECT p->>'precio' AS precio FROM configuraciones c, jsonb_array_elements(coalesce(c.datos->'propiedades', '[]'::jsonb)) p
           WHERE p->>'id' = %s LIMIT 1""", (str(propiedad),)).fetchone()
    try:
        return float(fila["precio"]) if fila and fila["precio"] else 0
    except ValueError:
        return 0


def datos_giro_de(visita):
    """Lo que la oportunidad toma de su visita más reciente: calificación, propiedad de interés y score."""
    d = {k: visita[k] for k in CALIFICACION if visita.get(k) not in (None, "")}
    if visita.get("propiedad"):
        d["propiedad"] = str(visita["propiedad"])[:60]
    d["score"] = int(visita.get("score") or 0)
    d["categoria"] = visita.get("cat") or "baja"
    d["razones"] = visita.get("razones") or []
    d["ultima_visita"] = {"id": visita.get("id", ""), "asesor": str(visita.get("asesor") or "")[:60],
                          "creado": str(visita.get("creado") or "")[:40]}
    return d


def _usuario_o_nada(con, valor):
    try:
        uid = uuid.UUID(str(valor))
    except ValueError:
        return None
    return uid if con.execute("SELECT 1 FROM usuarios WHERE id = %s", (uid,)).fetchone() else None


def ligar(con, negocio_id, registro_id):
    """Liga una visita a su contacto y oportunidad (creándolos si hace falta). Devuelve el id de la oportunidad, o None
    si la visita no existe o es de ejemplo. Se puede repetir: una visita ya ligada solo actualiza su oportunidad."""
    r = con.execute("SELECT * FROM registros WHERE id = %s FOR UPDATE", (registro_id,)).fetchone()
    if r is None or r["demo"]:
        return None
    v = r["datos"]
    responsable = _usuario_o_nada(con, v.get("asesor_id"))

    contacto_id = r["contacto_id"]
    if contacto_id is None:
        tel = tel10(v.get("tel"))
        fila = con.execute(
            "SELECT id FROM contactos WHERE right(regexp_replace(telefono, '\\D', '', 'g'), 10) = %s ORDER BY creado LIMIT 1",
            (tel,)).fetchone() if tel else None
        if fila is None:
            fila = con.execute(
                """INSERT INTO contactos (negocio_id, nombre, telefono, origen, responsable_id)
                   VALUES (%s, %s, %s, 'Visita', %s) RETURNING id""",
                (negocio_id, (str(v.get("nombre") or "").strip() or "Sin nombre")[:120], tel, responsable)).fetchone()
        contacto_id = fila["id"]

    oportunidad_id = r["oportunidad_id"]
    if oportunidad_id is None:
        # Una abierta de este giro, o una creada a mano sin giro (se le asigna este): no se abre otra para la misma persona.
        fila = con.execute(
            """SELECT o.id FROM oportunidades o JOIN etapas e ON e.id = o.etapa_id
               WHERE o.contacto_id = %s AND o.giro IN (%s, '') AND e.tipo = 'abierta'
               ORDER BY (o.giro = %s) DESC, o.creado DESC LIMIT 1""",
            (contacto_id, GIRO, GIRO)).fetchone()
        if fila:
            con.execute("UPDATE oportunidades SET giro = %s WHERE id = %s AND giro = ''", (GIRO, fila["id"]))
        if fila is None:
            pipeline_id = pipeline_defecto(con, negocio_id)
            etapa = etapa_para(con, pipeline_id, r["estado"])
            fila = con.execute(
                """INSERT INTO oportunidades (negocio_id, contacto_id, pipeline_id, etapa_id, giro, responsable_id, cerrado_en)
                   VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s IN ('ganada', 'perdida') THEN now() END) RETURNING id""",
                (negocio_id, contacto_id, pipeline_id, etapa["id"], GIRO, responsable, etapa["tipo"])).fetchone()
        oportunidad_id = fila["id"]

    con.execute("UPDATE registros SET contacto_id = %s, oportunidad_id = %s WHERE id = %s",
                (contacto_id, oportunidad_id, registro_id))

    # La oportunidad toma la calificación y el score de su visita más reciente; su valor, el precio de esa propiedad
    # (solo si nadie lo escribió a mano).
    ultima = con.execute("SELECT datos FROM registros WHERE oportunidad_id = %s ORDER BY creado DESC LIMIT 1",
                         (oportunidad_id,)).fetchone()["datos"]
    con.execute(
        """UPDATE oportunidades SET datos_giro = datos_giro || %s,
               valor = CASE WHEN valor = 0 THEN %s ELSE valor END, actualizado = now()
           WHERE id = %s""",
        (Jsonb(datos_giro_de(ultima)), precio_propiedad(con, ultima.get("propiedad")), oportunidad_id))

    estado_de_visitas(con, oportunidad_id)      # sus visitas muestran la etapa de la oportunidad
    return oportunidad_id


def ligar_pendientes(con, negocio_id):
    """Liga todas las visitas que aún no tienen oportunidad (las de antes de esta función). Se puede repetir."""
    ids = [f["id"] for f in con.execute(
        "SELECT id FROM registros WHERE oportunidad_id IS NULL AND NOT demo ORDER BY creado").fetchall()]
    for id_ in ids:
        ligar(con, negocio_id, id_)
    return len(ids)


def estado_de_visitas(con, oportunidad_id):
    """Tras mover una oportunidad de etapa: sus visitas toman el estado equivalente (si existe)."""
    con.execute(
        """UPDATE registros r SET estado = lower(e.nombre)
           FROM oportunidades o JOIN etapas e ON e.id = o.etapa_id
           WHERE o.id = %s AND r.oportunidad_id = o.id AND lower(e.nombre) = ANY(%s) AND r.estado <> lower(e.nombre)""",
        (oportunidad_id, list(ESTADOS)))


def etapa_desde_estado(con, registro_id, estado):
    """Tras cambiar el estado de una visita: su oportunidad pasa a la etapa con ese nombre (si existe en su embudo)."""
    fila = con.execute(
        """SELECT o.id, o.pipeline_id, o.etapa_id FROM registros r JOIN oportunidades o ON o.id = r.oportunidad_id
           WHERE r.id = %s""", (registro_id,)).fetchone()
    if not fila:
        return
    etapa = con.execute("SELECT id, tipo FROM etapas WHERE pipeline_id = %s AND lower(nombre) = %s",
                        (fila["pipeline_id"], estado)).fetchone()
    if etapa and etapa["id"] != fila["etapa_id"]:
        con.execute(
            """UPDATE oportunidades SET etapa_id = %s, actualizado = now(),
                   cerrado_en = CASE WHEN %s IN ('ganada', 'perdida') THEN now() END WHERE id = %s""",
            (etapa["id"], etapa["tipo"], fila["id"]))
        estado_de_visitas(con, fila["id"])
