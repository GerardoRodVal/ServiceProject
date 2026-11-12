"""Base local del gestor (SQLite): las tablas del giro inmobiliario y la configuración.

  configuracion  una sola fila (id = 1) con el JSON de ajustes: negocio, formulario, puntaje, formato de cita, IA…
  zonas          desarrollos y colonias
  inmuebles      catálogo; zona_id -> zonas
  asesores       equipo y su acceso a la app (correo, teléfono y hash de la contraseña; nunca la contraseña)
  prospectos     visitas capturadas; inmueble_id -> inmuebles, asesor_id -> asesores. El formulario completo va en «datos»
  citas          solicitudes del enlace público (origen «enlace») y citas realizadas por un asesor (origen «asesor»)
  cargas         cada archivo subido con «Cargar datos» (columnas reconocidas y descartadas)
  prospectos_espera  filas de esas cargas antes de entrar al embudo, con su revisión (correcto, falta, validar)

Cada tabla del catálogo tiene columnas para lo que se filtra, ordena o relaciona, y una columna «ficha» (JSON) con el
resto: así agregar un atributo al inmueble no exige cambiar la tabla.

Compatibilidad con PostgreSQL (Azure): los tipos pasan uno a uno (TEXT -> text, INTEGER 0/1 -> boolean,
TEXT con JSON -> jsonb, fechas ISO 8601 en TEXT -> timestamptz). Los ids son texto que genera la app (no autoincrementales)
para que una fila conserve su id al subir a la nube. El equivalente está en nube/db/borradores/011_inmobiliaria_sobre_nucleo.sql
(unificado con el núcleo del CRM; ver ARQUITECTURA_CRM_Y_GIROS.md, sección 11).

El gestor sigue entregando el catálogo al celular como config.json: ese archivo se genera desde estas tablas al guardar.
Solo usa la biblioteca estándar de Python.
"""
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

# Listas de la configuración que viven en su propia tabla (el resto de la configuración es un solo JSON).
LISTAS = ("zonas", "propiedades", "asesores")
TIPOS_ZONA = ("desarrollo", "colonia")
TIPOS_INMUEBLE = ("casa", "departamento", "terreno")
ESTATUS_INMUEBLE = ("disponible", "apartado", "vendido")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS configuracion (
  id           INTEGER PRIMARY KEY CHECK (id = 1),
  datos        TEXT NOT NULL,
  actualizado  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zonas (
  id           TEXT PRIMARY KEY,
  orden        INTEGER NOT NULL DEFAULT 0,
  nombre       TEXT NOT NULL,
  tipo         TEXT NOT NULL DEFAULT 'colonia' CHECK (tipo IN ('desarrollo', 'colonia')),
  ciudad       TEXT NOT NULL DEFAULT '',
  imagen       TEXT NOT NULL DEFAULT '',
  ficha        TEXT NOT NULL DEFAULT '{}',
  actualizado  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inmuebles (
  id                TEXT PRIMARY KEY,
  orden             INTEGER NOT NULL DEFAULT 0,
  zona_id           TEXT REFERENCES zonas(id) ON DELETE SET NULL,
  nombre            TEXT NOT NULL,
  tipo              TEXT NOT NULL DEFAULT 'casa' CHECK (tipo IN ('casa', 'departamento', 'terreno')),
  estatus           TEXT NOT NULL DEFAULT 'disponible' CHECK (estatus IN ('disponible', 'apartado', 'vendido')),
  precio            INTEGER NOT NULL DEFAULT 0 CHECK (precio >= 0),
  recamaras         INTEGER NOT NULL DEFAULT 0,
  banos             INTEGER NOT NULL DEFAULT 0,
  estacionamientos  INTEGER NOT NULL DEFAULT 0,
  m2_construccion   INTEGER NOT NULL DEFAULT 0,
  m2_terreno        INTEGER NOT NULL DEFAULT 0,
  imagen            TEXT NOT NULL DEFAULT '',
  ficha             TEXT NOT NULL DEFAULT '{}',
  actualizado       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inmuebles_zona ON inmuebles (zona_id);

CREATE TABLE IF NOT EXISTS asesores (
  id             TEXT PRIMARY KEY,
  orden          INTEGER NOT NULL DEFAULT 0,
  nombre         TEXT NOT NULL,
  activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
  foto           TEXT NOT NULL DEFAULT '',
  correo         TEXT NOT NULL DEFAULT '',
  telefono       TEXT NOT NULL DEFAULT '',
  hash_password  TEXT NOT NULL DEFAULT '',
  ficha          TEXT NOT NULL DEFAULT '{}',
  actualizado    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS asesores_correo_uq ON asesores (lower(correo)) WHERE correo <> '';

CREATE TABLE IF NOT EXISTS prospectos (
  id           TEXT PRIMARY KEY,
  recibido     TEXT NOT NULL,
  creado       TEXT NOT NULL,
  demo         INTEGER NOT NULL DEFAULT 0,
  estado       TEXT NOT NULL DEFAULT 'nuevo',
  datos        TEXT NOT NULL,
  nombre       TEXT NOT NULL DEFAULT '',
  tel          TEXT NOT NULL DEFAULT '',
  inmueble_id  TEXT REFERENCES inmuebles(id) ON DELETE SET NULL,
  asesor_id    TEXT REFERENCES asesores(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS citas (
  id               TEXT PRIMARY KEY,
  origen           TEXT NOT NULL CHECK (origen IN ('enlace', 'asesor')),
  estado           TEXT NOT NULL DEFAULT 'pendiente'
                   CHECK (estado IN ('pendiente', 'confirmada', 'descartada', 'terminada')),
  creada           TEXT NOT NULL,
  actualizada      TEXT NOT NULL,
  nombre           TEXT NOT NULL DEFAULT '',
  tel              TEXT NOT NULL DEFAULT '',
  zona_id          TEXT REFERENCES zonas(id) ON DELETE SET NULL,
  inmueble_id      TEXT REFERENCES inmuebles(id) ON DELETE SET NULL,
  asesor_id        TEXT REFERENCES asesores(id) ON DELETE SET NULL,
  prospecto_id     TEXT REFERENCES prospectos(id) ON DELETE SET NULL,
  confirmada_para  TEXT NOT NULL DEFAULT '',
  nota             TEXT NOT NULL DEFAULT '',
  inicio           TEXT,
  fin              TEXT,
  duracion_seg     INTEGER NOT NULL DEFAULT 0,
  ubicacion_final  TEXT,
  trayecto         TEXT NOT NULL DEFAULT '[]',
  datos            TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS citas_origen_creada ON citas (origen, creada DESC);
CREATE INDEX IF NOT EXISTS citas_tel ON citas (tel);

-- «Cargar datos»: cada archivo es una carga y sus filas esperan aquí hasta que se agregan al embudo o se descartan
-- (sin punto y coma en estos comentarios: el esquema se separa por ese carácter).
-- valores: las columnas reconocidas tal como venían (el resto se descarta). revision: correcto, falta (falta un dato
-- obligatorio) o validar (un dato no se entiende o hay que confirmarlo). problemas: [{campo, tipo, texto, confirmable}].
CREATE TABLE IF NOT EXISTS cargas (
  id           TEXT PRIMARY KEY,
  archivo      TEXT NOT NULL DEFAULT '',
  cargada      TEXT NOT NULL,
  reconocidas  TEXT NOT NULL DEFAULT '[]',
  descartadas  TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS prospectos_espera (
  id           TEXT PRIMARY KEY,
  carga_id     TEXT NOT NULL REFERENCES cargas(id) ON DELETE CASCADE,
  fila         INTEGER NOT NULL,
  revision     TEXT NOT NULL DEFAULT 'validar' CHECK (revision IN ('correcto', 'falta', 'validar')),
  problemas    TEXT NOT NULL DEFAULT '[]',
  confirmado   INTEGER NOT NULL DEFAULT 0 CHECK (confirmado IN (0, 1)),
  valores      TEXT NOT NULL DEFAULT '{}',
  actualizado  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prospectos_espera_carga ON prospectos_espera (carga_id, fila);
"""

# Columnas que se agregan a la tabla «visitas» de versiones anteriores al renombrarla «prospectos».
COLUMNAS_PROSPECTO_NUEVAS = [
    ("nombre", "TEXT NOT NULL DEFAULT ''"),
    ("tel", "TEXT NOT NULL DEFAULT ''"),
    ("inmueble_id", "TEXT REFERENCES inmuebles(id) ON DELETE SET NULL"),
    ("asesor_id", "TEXT REFERENCES asesores(id) ON DELETE SET NULL"),
]
# Van después de agregar esas columnas (una tabla «visitas» renombrada aún no las tiene al crear el resto).
INDICES_PROSPECTOS = """
CREATE INDEX IF NOT EXISTS prospectos_creado ON prospectos (creado DESC);
CREATE INDEX IF NOT EXISTS prospectos_inmueble ON prospectos (inmueble_id);
CREATE INDEX IF NOT EXISTS prospectos_asesor ON prospectos (asesor_id)
"""

# Relaciones que se deducen de «datos»: el formulario guarda el id de la propiedad y el nombre (o id) del asesor.
# Si el inmueble o el asesor ya no existe, la columna queda en NULL y el dato original sigue en «datos».
ENLAZAR_PROSPECTOS = """
UPDATE prospectos SET
  inmueble_id = (SELECT i.id FROM inmuebles i WHERE i.id = json_extract(prospectos.datos, '$.propiedad')),
  asesor_id = coalesce(
    (SELECT a.id FROM asesores a WHERE a.id = json_extract(prospectos.datos, '$.asesor_id')),
    (SELECT a.id FROM asesores a WHERE lower(a.nombre) = lower(json_extract(prospectos.datos, '$.asesor'))
     ORDER BY a.orden LIMIT 1))
WHERE json_valid(prospectos.datos) {donde}
"""
ENLAZAR_CITAS = """
UPDATE citas SET
  zona_id = (SELECT z.id FROM zonas z WHERE z.id = json_extract(citas.datos, '$.zona')),
  inmueble_id = (SELECT i.id FROM inmuebles i WHERE i.id = json_extract(citas.datos, '$.casa')),
  asesor_id = coalesce(
    (SELECT a.id FROM asesores a WHERE a.id = json_extract(citas.datos, '$.asesor_id')),
    (SELECT a.id FROM asesores a WHERE a.id = json_extract(citas.datos, '$.asesor')),
    (SELECT a.id FROM asesores a WHERE lower(a.nombre) = lower(json_extract(citas.datos, '$.asesor'))
     ORDER BY a.orden LIMIT 1))
WHERE json_valid(citas.datos) {donde}
"""

_PREPARADAS = set()
_LOCK = threading.Lock()


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(valor):
    return json.dumps(valor, ensure_ascii=False)


def _leer_json(texto, defecto):
    try:
        valor = json.loads(texto) if texto else defecto
    except ValueError:
        return defecto
    return valor if isinstance(valor, type(defecto)) else defecto


def _entero(valor):
    try:
        return max(0, int(float(valor or 0)))
    except (TypeError, ValueError):
        return 0


def _opcion(valor, permitidas):
    return valor if valor in permitidas else permitidas[0]


# --- Conexión y estructura ---

def preparar(ruta):
    """Crea las tablas si faltan y actualiza una base de una versión anterior: la tabla «visitas» pasa a llamarse
    «prospectos» (con sus columnas nuevas) y las «solicitudes» pasan a la tabla «citas». Idempotente."""
    with _LOCK:
        if ruta in _PREPARADAS:
            return
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        con = sqlite3.connect(ruta, timeout=10, isolation_level=None)
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("BEGIN IMMEDIATE")
            try:
                tablas = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if "visitas" in tablas and "prospectos" not in tablas:
                    con.execute("ALTER TABLE visitas RENAME TO prospectos")
                    tablas.add("prospectos")
                columnas = {f[1] for f in con.execute("PRAGMA table_info(prospectos)")} if "prospectos" in tablas else set()
                for sentencia in ESQUEMA.split(";"):
                    if sentencia.strip():
                        con.execute(sentencia)
                if columnas:
                    faltan = [(c, t) for c, t in COLUMNAS_PROSPECTO_NUEVAS if c not in columnas]
                    for c, t in faltan:
                        con.execute(f"ALTER TABLE prospectos ADD COLUMN {c} {t}")
                    if faltan:
                        con.execute("""UPDATE prospectos SET
                                         nombre = substr(coalesce(json_extract(datos, '$.nombre'), ''), 1, 80),
                                         tel = substr(coalesce(json_extract(datos, '$.tel'), ''), 1, 30)
                                       WHERE json_valid(datos)""")
                        con.execute(ENLAZAR_PROSPECTOS.format(donde=""))
                for sentencia in INDICES_PROSPECTOS.split(";"):
                    con.execute(sentencia)
                if "solicitudes" in tablas:
                    con.execute("""INSERT OR IGNORE INTO citas
                                     (id, origen, estado, creada, actualizada, nombre, tel, confirmada_para, nota, datos)
                                   SELECT id, 'enlace', estado, creada, actualizada,
                                          CASE WHEN json_valid(datos) THEN coalesce(json_extract(datos, '$.nombre'), '') ELSE '' END,
                                          tel, confirmada_para, nota, datos
                                   FROM solicitudes""")
                    con.execute("DROP TABLE solicitudes")
                    con.execute(ENLAZAR_CITAS.format(donde=""))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()
        _PREPARADAS.add(ruta)


@contextmanager
def transaccion(ruta, escritura=False):
    """Conexión con llaves foráneas activas dentro de una transacción: o se guarda todo o nada.
    Las lecturas también van en transacción para ver una sola versión de los datos."""
    preparar(ruta)
    con = sqlite3.connect(ruta, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("BEGIN IMMEDIATE" if escritura else "BEGIN")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
    finally:
        con.close()


def _enlazar(con):
    con.execute(ENLAZAR_PROSPECTOS.format(donde=""))
    con.execute(ENLAZAR_CITAS.format(donde=""))


def _borrar_faltantes(con, tabla, ids):
    con.execute(f"DELETE FROM {tabla} WHERE id NOT IN (SELECT value FROM json_each(?))", (_json(ids),))


# --- Configuración y catálogo ---

COLUMNAS_ZONA = ("id", "nombre", "tipo", "ciudad", "imagen")
COLUMNAS_INMUEBLE = ("id", "nombre", "precio", "tipo", "zona", "estatus", "recamaras", "banos", "estacionamientos",
                     "m2_construccion", "m2_terreno", "imagen")
COLUMNAS_ASESOR = ("id", "nombre", "activo", "foto", "correo", "telefono", "hash", "password_nueva", "tiene_password")


def _ficha(d, columnas):
    return _json({k: v for k, v in d.items() if k not in columnas})


def _con_id(lista):
    return [x for x in (lista or []) if isinstance(x, dict) and str(x.get("id") or "").strip()]


def tiene_config(ruta):
    with transaccion(ruta) as con:
        return con.execute("SELECT 1 FROM configuracion WHERE id = 1").fetchone() is not None


def leer_config(ruta):
    """La configuración completa (como la usa el panel), o None si la base aún no tiene una."""
    with transaccion(ruta) as con:
        fila = con.execute("SELECT datos FROM configuracion WHERE id = 1").fetchone()
        if fila is None:
            return None
        cfg = _leer_json(fila["datos"], {})
        cfg["zonas"] = [{"id": f["id"], "nombre": f["nombre"], "tipo": f["tipo"], "ciudad": f["ciudad"],
                         "imagen": f["imagen"], **_leer_json(f["ficha"], {})}
                        for f in con.execute("SELECT * FROM zonas ORDER BY orden, id")]
        cfg["propiedades"] = [{"id": f["id"], "nombre": f["nombre"], "precio": f["precio"], "tipo": f["tipo"],
                               "zona": f["zona_id"] or "", "estatus": f["estatus"], "recamaras": f["recamaras"],
                               "banos": f["banos"], "estacionamientos": f["estacionamientos"],
                               "m2_construccion": f["m2_construccion"], "m2_terreno": f["m2_terreno"],
                               "imagen": f["imagen"], **_leer_json(f["ficha"], {})}
                              for f in con.execute("SELECT * FROM inmuebles ORDER BY orden, id")]
        cfg["asesores"] = [{"id": f["id"], "nombre": f["nombre"], "activo": bool(f["activo"]), "foto": f["foto"],
                            **_leer_json(f["ficha"], {})}
                           for f in con.execute("SELECT * FROM asesores ORDER BY orden, id")]
        return cfg


def _escribir_config(con, cfg, acceso=None):
    ahora = _ahora()
    resto = {k: v for k, v in cfg.items() if k not in LISTAS}
    con.execute("""INSERT INTO configuracion (id, datos, actualizado) VALUES (1, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET datos = excluded.datos, actualizado = excluded.actualizado""",
                (_json(resto), ahora))

    zonas = _con_id(cfg.get("zonas"))
    for orden, z in enumerate(zonas):
        con.execute("""INSERT INTO zonas (id, orden, nombre, tipo, ciudad, imagen, ficha, actualizado)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        con.execute("""INSERT INTO asesores (id, orden, nombre, activo, foto, ficha, actualizado) VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (id) DO UPDATE SET orden = excluded.orden, nombre = excluded.nombre,
                         activo = excluded.activo, foto = excluded.foto, ficha = excluded.ficha,
                         actualizado = excluded.actualizado""",
                    (str(a["id"]), orden, str(a.get("nombre") or ""), 0 if a.get("activo") is False else 1,
                     str(a.get("foto") or ""), _ficha(a, COLUMNAS_ASESOR), ahora))
    _borrar_faltantes(con, "asesores", [str(a["id"]) for a in asesores])
    if acceso is not None:
        _escribir_acceso(con, acceso)
    _enlazar(con)


def escribir_config(ruta, cfg, acceso=None):
    """Guarda la configuración completa: el JSON de ajustes y las tablas zonas, inmuebles y asesores (agrega, cambia y
    quita filas para que queden igual que las listas). Con «acceso» también reemplaza correo, teléfono y hash de los
    asesores, en la misma transacción."""
    with transaccion(ruta, escritura=True) as con:
        _escribir_config(con, cfg, acceso)


def leer_acceso(ruta):
    """{id del asesor: {correo, telefono, hash}}: lo que antes estaba en asesores_acceso.json."""
    with transaccion(ruta) as con:
        return {f["id"]: {"correo": f["correo"], "telefono": f["telefono"], "hash": f["hash_password"]}
                for f in con.execute("SELECT id, correo, telefono, hash_password FROM asesores")
                if f["correo"] or f["hash_password"]}


def _escribir_acceso(con, acceso):
    # Primero se vacía y luego se llena: así dos asesores pueden intercambiar correo sin chocar con el índice único.
    con.execute("UPDATE asesores SET correo = '', telefono = '', hash_password = ''")
    for id_, a in (acceso or {}).items():
        if isinstance(a, dict):
            con.execute("UPDATE asesores SET correo = ?, telefono = ?, hash_password = ? WHERE id = ?",
                        (str(a.get("correo") or "").lower(), str(a.get("telefono") or ""), str(a.get("hash") or ""), str(id_)))


def escribir_acceso(ruta, acceso):
    """Reemplaza correo, teléfono y hash de todos los asesores (los que no aparecen quedan sin acceso)."""
    with transaccion(ruta, escritura=True) as con:
        _escribir_acceso(con, acceso)


# --- Prospectos ---

def _fila_prospecto(f):
    return {**_leer_json(f["datos"], {}), "id": f["id"], "creado": f["creado"], "recibido": f["recibido"],
            "estado": f["estado"], "demo": bool(f["demo"])}


def prospecto_guardar(ruta, visita, demo=False, estado="nuevo"):
    with transaccion(ruta, escritura=True) as con:
        con.execute("""INSERT INTO prospectos (id, recibido, creado, demo, estado, datos, nombre, tel)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (id) DO UPDATE SET datos = excluded.datos, recibido = excluded.recibido,
                         nombre = excluded.nombre, tel = excluded.tel""",
                    (visita["id"], datetime.now(timezone.utc).isoformat(), visita["creado"], 1 if demo else 0, estado,
                     _json(visita), str(visita.get("nombre") or "")[:80], str(visita.get("tel") or "")[:30]))
        con.execute(ENLAZAR_PROSPECTOS.format(donde="AND id = ?"), (visita["id"],))


def prospectos_listar(ruta):
    with transaccion(ruta) as con:
        return [_fila_prospecto(f) for f in con.execute("SELECT * FROM prospectos ORDER BY creado DESC")]


def prospecto_estado(ruta, id_, estado):
    with transaccion(ruta, escritura=True) as con:
        con.execute("UPDATE prospectos SET estado = ? WHERE id = ?", (estado, id_))


def prospecto_agregar_datos(ruta, id_, extra):
    """Agrega campos a un prospecto (p. ej. su enlace a Kommo) sin tocar su estado ni sus fechas."""
    with transaccion(ruta, escritura=True) as con:
        f = con.execute("SELECT datos FROM prospectos WHERE id = ?", (id_,)).fetchone()
        if f:
            datos = {**_leer_json(f["datos"], {}), **extra}
            con.execute("UPDATE prospectos SET datos = ?, nombre = ?, tel = ? WHERE id = ?",
                        (_json(datos), str(datos.get("nombre") or "")[:80], str(datos.get("tel") or "")[:30], id_))
            con.execute(ENLAZAR_PROSPECTOS.format(donde="AND id = ?"), (id_,))


def prospectos_borrar(ruta, donde):
    """Borra según un filtro fijo de este módulo ('demo' o 'importados'); devuelve cuántos borró."""
    filtro = {"demo": "demo = 1", "importados": "id LIKE 'imp-%'"}[donde]
    with transaccion(ruta, escritura=True) as con:
        return con.execute(f"DELETE FROM prospectos WHERE {filtro}").rowcount


# --- Citas: solicitudes del enlace público ---

def _fila_solicitud(f):
    return {**_leer_json(f["datos"], {}), "id": f["id"], "tel": f["tel"], "creada": f["creada"], "estado": f["estado"],
            "confirmada_para": f["confirmada_para"], "nota": f["nota"]}


def solicitud_crear(ruta, datos):
    """Un teléfono tiene a lo sumo una solicitud por confirmar: si ya hay una, se actualiza."""
    ahora = _ahora()
    with transaccion(ruta, escritura=True) as con:
        pendiente = con.execute("SELECT id FROM citas WHERE origen = 'enlace' AND estado = 'pendiente' AND tel = ?",
                                (datos["tel"],)).fetchone()
        if pendiente:
            id_ = pendiente["id"]
            con.execute("UPDATE citas SET datos = ?, nombre = ?, actualizada = ? WHERE id = ?",
                        (_json(datos), str(datos.get("nombre") or ""), ahora, id_))
        else:
            id_ = f"sol-{secrets.token_hex(6)}"
            con.execute("""INSERT INTO citas (id, origen, estado, creada, actualizada, nombre, tel, datos)
                           VALUES (?, 'enlace', 'pendiente', ?, ?, ?, ?, ?)""",
                        (id_, ahora, ahora, str(datos.get("nombre") or ""), datos["tel"], _json(datos)))
        con.execute(ENLAZAR_CITAS.format(donde="AND id = ?"), (id_,))


def solicitudes_listar(ruta):
    with transaccion(ruta) as con:
        return [_fila_solicitud(f) for f in
                con.execute("SELECT * FROM citas WHERE origen = 'enlace' ORDER BY creada DESC LIMIT 500")]


def solicitud_actualizar(ruta, id_, cambios):
    """Cambia estado, fecha acordada, nota o asesor. LookupError si no existe; ValueError si reabrirla duplicaría al teléfono."""
    with transaccion(ruta, escritura=True) as con:
        actual = con.execute("SELECT * FROM citas WHERE id = ? AND origen = 'enlace'", (id_,)).fetchone()
        if actual is None:
            raise LookupError("No encontramos la solicitud.")
        nuevo = {**dict(actual), **cambios}
        if nuevo["estado"] == "pendiente" and actual["estado"] != "pendiente" and con.execute(
                "SELECT 1 FROM citas WHERE origen = 'enlace' AND tel = ? AND estado = 'pendiente' AND id != ?",
                (actual["tel"], id_)).fetchone():
            raise ValueError("Ese teléfono ya tiene otra solicitud por confirmar.")
        datos = _leer_json(actual["datos"], {})
        if "asesor" in cambios:
            datos["asesor"] = cambios["asesor"]
        con.execute("UPDATE citas SET estado = ?, confirmada_para = ?, nota = ?, datos = ?, actualizada = ? WHERE id = ?",
                    (nuevo["estado"], nuevo["confirmada_para"], nuevo["nota"], _json(datos), _ahora(), id_))
        return _fila_solicitud(con.execute("SELECT * FROM citas WHERE id = ?", (id_,)).fetchone())


def solicitud_eliminar(ruta, id_):
    """Borra la solicitud del enlace. LookupError si no existe."""
    with transaccion(ruta, escritura=True) as con:
        borradas = con.execute("DELETE FROM citas WHERE id = ? AND origen = 'enlace'", (id_,)).rowcount
    if not borradas:
        raise LookupError("No encontramos la solicitud.")


# --- Citas: realizadas por un asesor (con su trayecto) ---

def _puntos(trayecto):
    return [{"hora": p.get("hora"), "lat": p.get("lat"), "lng": p.get("lng"), "precision": p.get("precision", 0)}
            for p in (trayecto if isinstance(trayecto, list) else []) if isinstance(p, dict)]


def _insertar_terminada(con, id_, registro):
    ubicacion = registro.get("ubicacion_final")
    creada = str(registro.get("fin") or registro.get("inicio") or _ahora())
    con.execute("""INSERT OR IGNORE INTO citas (id, origen, estado, creada, actualizada, inicio, fin, duracion_seg,
                     ubicacion_final, trayecto, datos)
                   VALUES (?, 'asesor', 'terminada', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id_, creada, creada, registro.get("inicio"), registro.get("fin"), _entero(registro.get("duracion_seg")),
                 _json(ubicacion) if isinstance(ubicacion, dict) else None, _json(_puntos(registro.get("trayecto"))),
                 _json({"asesor": str(registro.get("asesor") or ""), "asesor_id": str(registro.get("asesor_id") or "")})))
    con.execute(ENLAZAR_CITAS.format(donde="AND id = ?"), (id_,))


def cita_terminada(ruta, registro):
    """Guarda una cita que un asesor acaba de terminar: asesor, inicio, fin, duración, ubicación final y trayecto."""
    with transaccion(ruta, escritura=True) as con:
        _insertar_terminada(con, f"cita-{secrets.token_hex(8)}", registro)


def citas_terminadas(ruta, limite):
    """Citas terminadas, de la más reciente a la más antigua (lo que muestra la pantalla Citas)."""
    with transaccion(ruta) as con:
        filas = con.execute("""SELECT id, asesor_id, datos, inicio, fin, duracion_seg, ubicacion_final,
                                      json_array_length(trayecto) AS puntos
                               FROM citas WHERE origen = 'asesor' ORDER BY creada DESC, rowid DESC LIMIT ?""",
                            (int(limite),)).fetchall()
    salida = []
    for f in filas:
        datos = _leer_json(f["datos"], {})
        salida.append({"id": f["id"], "usuario_id": str(datos.get("asesor_id") or f["asesor_id"] or ""),
                       "nombre": str(datos.get("asesor") or ""), "inicio": f["inicio"], "fin": f["fin"],
                       "duracion_seg": f["duracion_seg"], "ubicacion_final": _leer_json(f["ubicacion_final"], {}) or None,
                       "puntos": f["puntos"] or 0})
    return salida


def trayecto(ruta, id_):
    """Puntos del trayecto de una cita terminada, o None si no existe."""
    with transaccion(ruta) as con:
        f = con.execute("SELECT trayecto FROM citas WHERE id = ? AND origen = 'asesor'", (id_,)).fetchone()
    return None if f is None else _puntos(_leer_json(f["trayecto"], []))


# --- Cargas en espera («Cargar datos» del embudo) ---

def _fila_espera(f):
    return {"id": f["id"], "carga_id": f["carga_id"], "fila": f["fila"], "revision": f["revision"],
            "problemas": _leer_json(f["problemas"], []), "confirmado": bool(f["confirmado"]),
            "valores": _leer_json(f["valores"], {}), "actualizado": f["actualizado"],
            "archivo": f["archivo"], "cargada": f["cargada"]}


def carga_crear(ruta, archivo, reconocidas, descartadas, filas):
    """filas: [(número de fila, valores, revision, problemas)]. Devuelve el id de la carga."""
    ahora = datetime.now(timezone.utc).isoformat()     # con microsegundos: dos cargas seguidas quedan en orden
    carga_id = f"carga-{secrets.token_hex(6)}"
    with transaccion(ruta, escritura=True) as con:
        con.execute("INSERT INTO cargas (id, archivo, cargada, reconocidas, descartadas) VALUES (?, ?, ?, ?, ?)",
                    (carga_id, str(archivo)[:200], ahora, _json(reconocidas), _json(descartadas)))
        con.executemany("""INSERT INTO prospectos_espera (id, carga_id, fila, revision, problemas, valores, actualizado)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        [(f"esp-{secrets.token_hex(8)}", carga_id, int(n), revision, _json(problemas), _json(valores), ahora)
                         for n, valores, revision, problemas in filas])
    return carga_id


def cargas_listar(ruta):
    with transaccion(ruta) as con:
        return [{"id": f["id"], "archivo": f["archivo"], "cargada": f["cargada"],
                 "reconocidas": _leer_json(f["reconocidas"], []), "descartadas": _leer_json(f["descartadas"], []),
                 "filas": f["filas"]}
                for f in con.execute("""SELECT c.*, (SELECT count(*) FROM prospectos_espera e WHERE e.carga_id = c.id) AS filas
                                        FROM cargas c ORDER BY c.cargada DESC""")]


def espera_listar(ruta, ids=None):
    """Filas en espera (todas, o solo las de ids), de la carga más reciente a la más antigua y en el orden del archivo."""
    with transaccion(ruta) as con:
        filtro = "" if ids is None else "WHERE e.id IN (SELECT value FROM json_each(?))"
        return [_fila_espera(f) for f in con.execute(
            f"""SELECT e.*, c.archivo, c.cargada FROM prospectos_espera e JOIN cargas c ON c.id = e.carga_id {filtro}
                ORDER BY c.cargada DESC, e.carga_id, e.fila""", () if ids is None else (_json(list(ids)),))]


def espera_actualizar(ruta, id_, valores=None, confirmado=None):
    """Cambia los valores (al corregir una fila se pierde la confirmación) o la confirmación. LookupError si no existe."""
    with transaccion(ruta, escritura=True) as con:
        actual = con.execute("SELECT * FROM prospectos_espera WHERE id = ?", (id_,)).fetchone()
        if actual is None:
            raise LookupError("Esa fila ya no está en espera.")
        nuevos = _leer_json(actual["valores"], {}) if valores is None else valores
        conf = actual["confirmado"] if confirmado is None else (1 if confirmado else 0)
        if valores is not None and confirmado is None:
            conf = 0
        con.execute("UPDATE prospectos_espera SET valores = ?, confirmado = ?, actualizado = ? WHERE id = ?",
                    (_json(nuevos), conf, _ahora(), id_))


def espera_guardar_revision(ruta, revisiones):
    """revisiones: [(id, revision, problemas)]. Solo escribe las que cambiaron."""
    with transaccion(ruta, escritura=True) as con:
        for id_, revision, problemas in revisiones:
            con.execute("""UPDATE prospectos_espera SET revision = ?, problemas = ?
                           WHERE id = ? AND (revision != ? OR problemas != ?)""",
                        (revision, _json(problemas), id_, revision, _json(problemas)))


def espera_borrar(ruta, ids=None):
    """Quita filas de la espera (todas si ids es None) y las cargas que se quedan sin filas. Devuelve cuántas quitó."""
    with transaccion(ruta, escritura=True) as con:
        if ids is None:
            n = con.execute("DELETE FROM prospectos_espera").rowcount
        else:
            n = con.execute("DELETE FROM prospectos_espera WHERE id IN (SELECT value FROM json_each(?))",
                            (_json(list(ids)),)).rowcount
        con.execute("DELETE FROM cargas WHERE id NOT IN (SELECT carga_id FROM prospectos_espera)")
        return n


# --- Paso desde los archivos de versiones anteriores ---

def importar_archivos(ruta, cfg, acceso, citas):
    """Primera vez con tablas: guarda la configuración que estaba en config.json, el acceso de asesores_acceso.json y
    las citas de citas.jsonl (citas: lista de (número de línea, registro); el id queda «L<línea>», como antes).
    No hace nada si la base ya tiene configuración. Devuelve True si importó."""
    with transaccion(ruta, escritura=True) as con:
        if con.execute("SELECT 1 FROM configuracion WHERE id = 1").fetchone():
            return False
        _escribir_config(con, cfg, acceso)
        for n, registro in citas:
            _insertar_terminada(con, f"L{n}", registro)
        return True
