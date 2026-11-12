"""Tablas del giro inmobiliario en PostgreSQL: configuración, zonas, inmuebles, asesores, prospectos, citas y cargas.

Mismas funciones (y mismos nombres) que gestor-windows/datos_locales.py, para que el servidor no cambie. El primer
argumento «ruta» se conserva por compatibilidad y se ignora: la conexión sale de conexion.ini (ver bd.py).

Diferencia con el gestor: al guardar una visita, en la misma transacción se liga a su contacto y a su oportunidad del
embudo (crm_local.ligar), y al cambiar su estado su oportunidad cambia de etapa. Así Prospectos y Embudo son lo mismo.
"""
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

import bd
import crm_local
from bd import iso, jsonb, transaccion as _tx

LISTAS = ("zonas", "propiedades", "asesores")
TIPOS_ZONA = ("desarrollo", "colonia")
TIPOS_INMUEBLE = ("casa", "departamento", "terreno")
ESTATUS_INMUEBLE = ("disponible", "apartado", "vendido")

# Relaciones que se deducen de «datos»: el formulario guarda el id de la propiedad y el nombre (o id) del asesor.
ENLAZAR_PROSPECTOS = """
UPDATE prospectos SET
  inmueble_id = (SELECT i.id FROM inmuebles i WHERE i.id = prospectos.datos->>'propiedad'),
  asesor_id = coalesce(
    (SELECT a.id FROM asesores a WHERE a.id = prospectos.datos->>'asesor_id'),
    (SELECT a.id FROM asesores a WHERE lower(a.nombre) = lower(prospectos.datos->>'asesor') ORDER BY a.orden LIMIT 1))
WHERE true {donde}
"""
ENLAZAR_CITAS = """
UPDATE citas SET
  zona_id = (SELECT z.id FROM zonas z WHERE z.id = citas.datos->>'zona'),
  inmueble_id = (SELECT i.id FROM inmuebles i WHERE i.id = citas.datos->>'casa'),
  asesor_id = coalesce(
    (SELECT a.id FROM asesores a WHERE a.id = citas.datos->>'asesor_id'),
    (SELECT a.id FROM asesores a WHERE a.id = citas.datos->>'asesor'),
    (SELECT a.id FROM asesores a WHERE lower(a.nombre) = lower(citas.datos->>'asesor') ORDER BY a.orden LIMIT 1))
WHERE true {donde}
"""


def _ahora():
    return datetime.now(timezone.utc)


def _entero(valor):
    try:
        return max(0, int(float(valor or 0)))
    except (TypeError, ValueError):
        return 0


def _opcion(valor, permitidas):
    return valor if valor in permitidas else permitidas[0]


def _dict(valor):
    return valor if isinstance(valor, dict) else {}


def _lista(valor):
    return valor if isinstance(valor, list) else []


@contextmanager
def transaccion(ruta=None, escritura=False):
    with _tx(escritura) as con:
        yield con


def preparar(ruta=None):
    bd.preparar()


def _enlazar(con):
    con.execute(ENLAZAR_PROSPECTOS.format(donde=""))
    con.execute(ENLAZAR_CITAS.format(donde=""))


def _borrar_faltantes(con, tabla, ids):
    con.execute(f"DELETE FROM {tabla} WHERE NOT (id = ANY(%s::text[]))", (list(ids),))


# --- Configuración y catálogo ---

COLUMNAS_ZONA = ("id", "nombre", "tipo", "ciudad", "imagen")
COLUMNAS_INMUEBLE = ("id", "nombre", "precio", "tipo", "zona", "estatus", "recamaras", "banos", "estacionamientos",
                     "m2_construccion", "m2_terreno", "imagen")
COLUMNAS_ASESOR = ("id", "nombre", "activo", "foto", "correo", "telefono", "hash", "password_nueva", "tiene_password")


def _ficha(d, columnas):
    return jsonb({k: v for k, v in d.items() if k not in columnas})


def _con_id(lista):
    return [x for x in (lista or []) if isinstance(x, dict) and str(x.get("id") or "").strip()]


def tiene_config(ruta=None):
    with transaccion() as con:
        return con.execute("SELECT 1 FROM configuracion WHERE id = 1").fetchone() is not None


def leer_config(ruta=None):
    """La configuración completa (como la usa el panel), o None si la base aún no tiene una."""
    with transaccion() as con:
        fila = con.execute("SELECT datos FROM configuracion WHERE id = 1").fetchone()
        if fila is None:
            return None
        cfg = _dict(fila["datos"])
        cfg["zonas"] = [{"id": f["id"], "nombre": f["nombre"], "tipo": f["tipo"], "ciudad": f["ciudad"],
                         "imagen": f["imagen"], **_dict(f["ficha"])}
                        for f in con.execute("SELECT * FROM zonas ORDER BY orden, id")]
        cfg["propiedades"] = [{"id": f["id"], "nombre": f["nombre"], "precio": f["precio"], "tipo": f["tipo"],
                               "zona": f["zona_id"] or "", "estatus": f["estatus"], "recamaras": f["recamaras"],
                               "banos": f["banos"], "estacionamientos": f["estacionamientos"],
                               "m2_construccion": f["m2_construccion"], "m2_terreno": f["m2_terreno"],
                               "imagen": f["imagen"], **_dict(f["ficha"])}
                              for f in con.execute("SELECT * FROM inmuebles ORDER BY orden, id")]
        cfg["asesores"] = [{"id": f["id"], "nombre": f["nombre"], "activo": bool(f["activo"]), "foto": f["foto"],
                            **_dict(f["ficha"])}
                           for f in con.execute("SELECT * FROM asesores ORDER BY orden, id")]
        return cfg


def _escribir_config(con, cfg, acceso=None):
    ahora = _ahora()
    resto = {k: v for k, v in cfg.items() if k not in LISTAS}
    con.execute("""INSERT INTO configuracion (id, datos, actualizado) VALUES (1, %s::jsonb, %s)
                   ON CONFLICT (id) DO UPDATE SET datos = excluded.datos, actualizado = excluded.actualizado""",
                (jsonb(resto), ahora))

    zonas = _con_id(cfg.get("zonas"))
    for orden, z in enumerate(zonas):
        con.execute("""INSERT INTO zonas (id, orden, nombre, tipo, ciudad, imagen, ficha, actualizado)
                       VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                       ON CONFLICT (id) DO UPDATE SET orden = excluded.orden, nombre = excluded.nombre, tipo = excluded.tipo,
                         ciudad = excluded.ciudad, imagen = excluded.imagen, ficha = excluded.ficha,
                         actualizado = excluded.actualizado""",
                    (str(z["id"]), orden, str(z.get("nombre") or ""), _opcion(z.get("tipo"), TIPOS_ZONA[::-1]),
                     str(z.get("ciudad") or ""), str(z.get("imagen") or ""), _ficha(z, COLUMNAS_ZONA), ahora))
    ids_zonas = {str(z["id"]) for z in zonas}

    inmuebles = _con_id(cfg.get("propiedades"))
    for orden, p in enumerate(inmuebles):
        zona = str(p.get("zona") or "")
        con.execute("""INSERT INTO inmuebles (id, orden, zona_id, nombre, tipo, estatus, precio, recamaras, banos,
                         estacionamientos, m2_construccion, m2_terreno, imagen, ficha, actualizado)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                       ON CONFLICT (id) DO UPDATE SET orden = excluded.orden, zona_id = excluded.zona_id,
                         nombre = excluded.nombre, tipo = excluded.tipo, estatus = excluded.estatus, precio = excluded.precio,
                         recamaras = excluded.recamaras, banos = excluded.banos, estacionamientos = excluded.estacionamientos,
                         m2_construccion = excluded.m2_construccion, m2_terreno = excluded.m2_terreno,
                         imagen = excluded.imagen, ficha = excluded.ficha, actualizado = excluded.actualizado""",
                    (str(p["id"]), orden, zona if zona in ids_zonas else None, str(p.get("nombre") or ""),
                     _opcion(p.get("tipo"), TIPOS_INMUEBLE), _opcion(p.get("estatus"), ESTATUS_INMUEBLE),
                     _entero(p.get("precio")), _entero(p.get("recamaras")), _entero(p.get("banos")),
                     _entero(p.get("estacionamientos")), _entero(p.get("m2_construccion")), _entero(p.get("m2_terreno")),
                     str(p.get("imagen") or ""), _ficha(p, COLUMNAS_INMUEBLE), ahora))
    _borrar_faltantes(con, "inmuebles", [str(p["id"]) for p in inmuebles])
    _borrar_faltantes(con, "zonas", sorted(ids_zonas))

    asesores = _con_id(cfg.get("asesores"))
    for orden, a in enumerate(asesores):
        con.execute("""INSERT INTO asesores (id, orden, nombre, activo, foto, ficha, actualizado)
                       VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                       ON CONFLICT (id) DO UPDATE SET orden = excluded.orden, nombre = excluded.nombre,
                         activo = excluded.activo, foto = excluded.foto, ficha = excluded.ficha,
                         actualizado = excluded.actualizado""",
                    (str(a["id"]), orden, str(a.get("nombre") or ""), a.get("activo") is not False,
                     str(a.get("foto") or ""), _ficha(a, COLUMNAS_ASESOR), ahora))
    _borrar_faltantes(con, "asesores", [str(a["id"]) for a in asesores])
    if acceso is not None:
        _escribir_acceso(con, acceso)
    _enlazar(con)


def escribir_config(ruta, cfg, acceso=None):
    """Guarda la configuración completa: el JSON de ajustes y las tablas zonas, inmuebles y asesores (agrega, cambia y
    quita filas para que queden igual que las listas). Con «acceso» también reemplaza correo, teléfono y hash."""
    with transaccion(escritura=True) as con:
        _escribir_config(con, cfg, acceso)


def leer_acceso(ruta=None):
    """{id del asesor: {correo, telefono, hash}}."""
    with transaccion() as con:
        return {f["id"]: {"correo": f["correo"], "telefono": f["telefono"], "hash": f["hash_password"]}
                for f in con.execute("SELECT id, correo, telefono, hash_password FROM asesores")
                if f["correo"] or f["hash_password"]}


def _escribir_acceso(con, acceso):
    # Primero se vacía y luego se llena: así dos asesores pueden intercambiar correo sin chocar con el índice único.
    con.execute("UPDATE asesores SET correo = '', telefono = '', hash_password = ''")
    for id_, a in (acceso or {}).items():
        if isinstance(a, dict):
            con.execute("UPDATE asesores SET correo = %s, telefono = %s, hash_password = %s WHERE id = %s",
                        (str(a.get("correo") or "").lower(), str(a.get("telefono") or ""), str(a.get("hash") or ""), str(id_)))


def escribir_acceso(ruta, acceso):
    with transaccion(escritura=True) as con:
        _escribir_acceso(con, acceso)


def asegurar_config(cfg):
    """Primera vez: guarda la configuración por omisión. No hace nada si la base ya tiene una. Devuelve True si la guardó."""
    with transaccion(escritura=True) as con:
        con.execute("SELECT pg_advisory_xact_lock(554402)")
        if con.execute("SELECT 1 FROM configuracion WHERE id = 1").fetchone():
            return False
        _escribir_config(con, cfg, {})
        return True


# --- Prospectos (visitas) ---

def _fila_prospecto(f):
    return {**_dict(f["datos"]), "id": f["id"], "creado": iso(f["creado"]), "recibido": iso(f["recibido"]),
            "estado": f["estado"], "demo": bool(f["demo"])}


def prospecto_guardar(ruta, visita, demo=False, estado="nuevo"):
    with transaccion(escritura=True) as con:
        con.execute("""INSERT INTO prospectos (id, recibido, creado, demo, estado, datos, nombre, tel)
                       VALUES (%s, now(), %s::timestamptz, %s, %s, %s::jsonb, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET datos = excluded.datos, recibido = excluded.recibido,
                         nombre = excluded.nombre, tel = excluded.tel""",
                    (visita["id"], visita["creado"], bool(demo), estado, jsonb(visita),
                     str(visita.get("nombre") or "")[:80], str(visita.get("tel") or "")[:30]))
        con.execute(ENLAZAR_PROSPECTOS.format(donde="AND id = %s"), (visita["id"],))
        crm_local.ligar(con, visita["id"])


def prospectos_listar(ruta=None):
    with transaccion() as con:
        return [_fila_prospecto(f) for f in con.execute("SELECT * FROM prospectos ORDER BY creado DESC")]


def prospecto_estado(ruta, id_, estado):
    with transaccion(escritura=True) as con:
        con.execute("UPDATE prospectos SET estado = %s WHERE id = %s", (estado, id_))
        crm_local.etapa_desde_estado(con, id_, estado)


def prospecto_agregar_datos(ruta, id_, extra):
    """Agrega campos a un prospecto sin tocar su estado ni sus fechas."""
    with transaccion(escritura=True) as con:
        f = con.execute("SELECT datos FROM prospectos WHERE id = %s FOR UPDATE", (id_,)).fetchone()
        if f:
            datos = {**_dict(f["datos"]), **extra}
            con.execute("UPDATE prospectos SET datos = %s::jsonb, nombre = %s, tel = %s WHERE id = %s",
                        (jsonb(datos), str(datos.get("nombre") or "")[:80], str(datos.get("tel") or "")[:30], id_))
            con.execute(ENLAZAR_PROSPECTOS.format(donde="AND id = %s"), (id_,))


def prospectos_borrar(ruta, donde):
    """Borra según un filtro fijo de este módulo ('demo' o 'importados'); devuelve cuántos borró."""
    filtro = {"demo": "demo", "importados": "id LIKE 'imp-%%'"}[donde]
    with transaccion(escritura=True) as con:
        return con.execute(f"DELETE FROM prospectos WHERE {filtro}").rowcount


# --- Citas: solicitudes del enlace público ---

def _fila_solicitud(f):
    return {**_dict(f["datos"]), "id": f["id"], "tel": f["tel"], "creada": iso(f["creada"]), "estado": f["estado"],
            "confirmada_para": f["confirmada_para"], "nota": f["nota"]}


def solicitud_crear(ruta, datos):
    """Un teléfono tiene a lo sumo una solicitud por confirmar: si ya hay una, se actualiza."""
    with transaccion(escritura=True) as con:
        pendiente = con.execute(
            "SELECT id FROM citas WHERE origen = 'enlace' AND estado = 'pendiente' AND tel = %s FOR UPDATE",
            (datos["tel"],)).fetchone()
        if pendiente:
            id_ = pendiente["id"]
            con.execute("UPDATE citas SET datos = %s::jsonb, nombre = %s, actualizada = now() WHERE id = %s",
                        (jsonb(datos), str(datos.get("nombre") or ""), id_))
        else:
            id_ = f"sol-{secrets.token_hex(6)}"
            con.execute("""INSERT INTO citas (id, origen, estado, nombre, tel, datos)
                           VALUES (%s, 'enlace', 'pendiente', %s, %s, %s::jsonb)""",
                        (id_, str(datos.get("nombre") or ""), datos["tel"], jsonb(datos)))
        con.execute(ENLAZAR_CITAS.format(donde="AND id = %s"), (id_,))


def solicitudes_listar(ruta=None):
    with transaccion() as con:
        return [_fila_solicitud(f) for f in
                con.execute("SELECT * FROM citas WHERE origen = 'enlace' ORDER BY creada DESC LIMIT 500")]


def solicitud_actualizar(ruta, id_, cambios):
    """Cambia estado, fecha acordada, nota o asesor. LookupError si no existe; ValueError si reabrirla duplicaría al teléfono."""
    with transaccion(escritura=True) as con:
        actual = con.execute("SELECT * FROM citas WHERE id = %s AND origen = 'enlace' FOR UPDATE", (id_,)).fetchone()
        if actual is None:
            raise LookupError("No encontramos la solicitud.")
        nuevo = {**actual, **cambios}
        if nuevo["estado"] == "pendiente" and actual["estado"] != "pendiente" and con.execute(
                "SELECT 1 FROM citas WHERE origen = 'enlace' AND tel = %s AND estado = 'pendiente' AND id <> %s",
                (actual["tel"], id_)).fetchone():
            raise ValueError("Ese teléfono ya tiene otra solicitud por confirmar.")
        datos = _dict(actual["datos"])
        if "asesor" in cambios:
            datos["asesor"] = cambios["asesor"]
        con.execute("""UPDATE citas SET estado = %s, confirmada_para = %s, nota = %s, datos = %s::jsonb, actualizada = now()
                       WHERE id = %s""",
                    (nuevo["estado"], nuevo["confirmada_para"], nuevo["nota"], jsonb(datos), id_))
        con.execute(ENLAZAR_CITAS.format(donde="AND id = %s"), (id_,))
        return _fila_solicitud(con.execute("SELECT * FROM citas WHERE id = %s", (id_,)).fetchone())


def solicitud_eliminar(ruta, id_):
    with transaccion(escritura=True) as con:
        borradas = con.execute("DELETE FROM citas WHERE id = %s AND origen = 'enlace'", (id_,)).rowcount
    if not borradas:
        raise LookupError("No encontramos la solicitud.")


# --- Citas: realizadas por un asesor (con su trayecto) ---

def _puntos(trayecto):
    return [{"hora": p.get("hora"), "lat": p.get("lat"), "lng": p.get("lng"), "precision": p.get("precision", 0)}
            for p in _lista(trayecto) if isinstance(p, dict)]


def _insertar_terminada(con, id_, registro):
    ubicacion = registro.get("ubicacion_final")
    creada = registro.get("fin") or registro.get("inicio") or _ahora().isoformat()
    con.execute("""INSERT INTO citas (id, origen, estado, creada, actualizada, inicio, fin, duracion_seg,
                     ubicacion_final, trayecto, datos)
                   VALUES (%s, 'asesor', 'terminada', %s::timestamptz, %s::timestamptz, %s::timestamptz, %s::timestamptz,
                           %s, %s::jsonb, %s::jsonb, %s::jsonb)
                   ON CONFLICT (id) DO NOTHING""",
                (id_, creada, creada, registro.get("inicio"), registro.get("fin"), _entero(registro.get("duracion_seg")),
                 jsonb(ubicacion) if isinstance(ubicacion, dict) else None, jsonb(_puntos(registro.get("trayecto"))),
                 jsonb({"asesor": str(registro.get("asesor") or ""), "asesor_id": str(registro.get("asesor_id") or "")})))
    con.execute(ENLAZAR_CITAS.format(donde="AND id = %s"), (id_,))


def cita_terminada(ruta, registro):
    with transaccion(escritura=True) as con:
        _insertar_terminada(con, f"cita-{secrets.token_hex(8)}", registro)


def citas_terminadas(ruta, limite):
    with transaccion() as con:
        filas = con.execute("""SELECT id, asesor_id, datos, inicio, fin, duracion_seg, ubicacion_final,
                                      jsonb_array_length(trayecto) AS puntos
                               FROM citas WHERE origen = 'asesor' ORDER BY creada DESC, id DESC LIMIT %s""",
                            (int(limite),)).fetchall()
    salida = []
    for f in filas:
        datos = _dict(f["datos"])
        salida.append({"id": f["id"], "usuario_id": str(datos.get("asesor_id") or f["asesor_id"] or ""),
                       "nombre": str(datos.get("asesor") or ""), "inicio": iso(f["inicio"]), "fin": iso(f["fin"]),
                       "duracion_seg": f["duracion_seg"], "ubicacion_final": _dict(f["ubicacion_final"]) or None,
                       "puntos": f["puntos"] or 0})
    return salida


def trayecto(ruta, id_):
    with transaccion() as con:
        f = con.execute("SELECT trayecto FROM citas WHERE id = %s AND origen = 'asesor'", (id_,)).fetchone()
    return None if f is None else _puntos(f["trayecto"])


# --- Cargas en espera («Cargar datos» del embudo) ---

def _fila_espera(f):
    return {"id": f["id"], "carga_id": f["carga_id"], "fila": f["fila"], "revision": f["revision"],
            "problemas": _lista(f["problemas"]), "confirmado": bool(f["confirmado"]),
            "valores": _dict(f["valores"]), "actualizado": iso(f["actualizado"]),
            "archivo": f["archivo"], "cargada": iso(f["cargada"])}


def carga_crear(ruta, archivo, reconocidas, descartadas, filas):
    """filas: [(número de fila, valores, revision, problemas)]. Devuelve el id de la carga."""
    ahora = _ahora()
    carga_id = f"carga-{secrets.token_hex(6)}"
    with transaccion(escritura=True) as con:
        con.execute("""INSERT INTO cargas (id, archivo, cargada, reconocidas, descartadas)
                       VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)""",
                    (carga_id, str(archivo)[:200], ahora, jsonb(reconocidas), jsonb(descartadas)))
        for n, valores, revision, problemas in filas:
            con.execute("""INSERT INTO prospectos_espera (id, carga_id, fila, revision, problemas, valores, actualizado)
                           VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
                        (f"esp-{secrets.token_hex(8)}", carga_id, int(n), revision, jsonb(problemas), jsonb(valores), ahora))
    return carga_id


def cargas_listar(ruta=None):
    with transaccion() as con:
        return [{"id": f["id"], "archivo": f["archivo"], "cargada": iso(f["cargada"]),
                 "reconocidas": _lista(f["reconocidas"]), "descartadas": _lista(f["descartadas"]), "filas": f["filas"]}
                for f in con.execute("""SELECT c.*, (SELECT count(*) FROM prospectos_espera e WHERE e.carga_id = c.id) AS filas
                                        FROM cargas c ORDER BY c.cargada DESC""")]


def espera_listar(ruta=None, ids=None):
    """Filas en espera (todas, o solo las de ids), de la carga más reciente a la más antigua y en el orden del archivo."""
    with transaccion() as con:
        filtro = "" if ids is None else "WHERE e.id = ANY(%s::text[])"
        return [_fila_espera(f) for f in con.execute(
            f"""SELECT e.*, c.archivo, c.cargada FROM prospectos_espera e JOIN cargas c ON c.id = e.carga_id {filtro}
                ORDER BY c.cargada DESC, e.carga_id, e.fila""", () if ids is None else (list(ids),))]


def espera_actualizar(ruta, id_, valores=None, confirmado=None):
    """Cambia los valores (al corregir una fila se pierde la confirmación) o la confirmación. LookupError si no existe."""
    with transaccion(escritura=True) as con:
        actual = con.execute("SELECT * FROM prospectos_espera WHERE id = %s FOR UPDATE", (id_,)).fetchone()
        if actual is None:
            raise LookupError("Esa fila ya no está en espera.")
        nuevos = _dict(actual["valores"]) if valores is None else valores
        conf = bool(actual["confirmado"]) if confirmado is None else bool(confirmado)
        if valores is not None and confirmado is None:
            conf = False
        con.execute("UPDATE prospectos_espera SET valores = %s::jsonb, confirmado = %s, actualizado = now() WHERE id = %s",
                    (jsonb(nuevos), conf, id_))


def espera_guardar_revision(ruta, revisiones):
    """revisiones: [(id, revision, problemas)]. Solo escribe las que cambiaron."""
    with transaccion(escritura=True) as con:
        for id_, revision, problemas in revisiones:
            con.execute("""UPDATE prospectos_espera SET revision = %s, problemas = %s::jsonb
                           WHERE id = %s AND (revision <> %s OR problemas <> %s::jsonb)""",
                        (revision, jsonb(problemas), id_, revision, jsonb(problemas)))


def espera_borrar(ruta=None, ids=None):
    """Quita filas de la espera (todas si ids es None) y las cargas que se quedan sin filas. Devuelve cuántas quitó."""
    with transaccion(escritura=True) as con:
        if ids is None:
            n = con.execute("DELETE FROM prospectos_espera").rowcount
        else:
            n = con.execute("DELETE FROM prospectos_espera WHERE id = ANY(%s::text[])", (list(ids),)).rowcount
        con.execute("DELETE FROM cargas WHERE id NOT IN (SELECT carga_id FROM prospectos_espera)")
        return n

