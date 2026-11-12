"""Conexión con el PostgreSQL local de NearLeads Inmobiliarias.

La conexión se lee de conexion.ini en la carpeta de datos (la escribe el instalador, ver instalar_bd.py):

    [postgresql]
    host = 127.0.0.1
    puerto = 5544
    base = nearleads_inmobiliarias
    usuario = nearleads_app
    clave = ...

Para desarrollo se puede apuntar a otra base con las variables INMOB_PG_HOST, INMOB_PG_PUERTO, INMOB_PG_BASE,
INMOB_PG_USUARIO e INMOB_PG_CLAVE (tienen prioridad sobre el archivo).

Usa pg8000 (Python puro, sin DLL de cliente). Parámetros con %s. Cada transacción toma una conexión de una reserva
pequeña; jsonb llega ya convertido a dict/list, timestamptz a datetime y uuid a UUID: a_json() los deja listos para el panel.
"""
import configparser
import json
import os
import queue
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import pg8000.dbapi

NOMBRE_APP = "NearLeads Inmobiliarias"


def carpeta_datos():
    """Datos de la instalación (conexion.ini, fotos del catálogo, registro). Fuera de Archivos de programa porque ahí
    no se puede escribir sin permisos de administrador."""
    if os.environ.get("INMOB_DATOS"):
        return os.environ["INMOB_DATOS"]
    base = os.environ.get("PROGRAMDATA") or os.path.expanduser("~")
    return os.path.join(base, "NearLeads Inmobiliarias")


def ruta_conexion():
    return os.path.join(carpeta_datos(), "conexion.ini")


class SinConfiguracion(RuntimeError):
    pass


def leer_parametros():
    p = configparser.ConfigParser()
    p.read(ruta_conexion(), encoding="utf-8")
    s = p["postgresql"] if p.has_section("postgresql") else {}
    datos = {
        "host": os.environ.get("INMOB_PG_HOST") or s.get("host", "127.0.0.1"),
        "port": int(os.environ.get("INMOB_PG_PUERTO") or s.get("puerto", "5544")),
        "database": os.environ.get("INMOB_PG_BASE") or s.get("base", "nearleads_inmobiliarias"),
        "user": os.environ.get("INMOB_PG_USUARIO") or s.get("usuario", ""),
        "password": os.environ.get("INMOB_PG_CLAVE") or s.get("clave", ""),
    }
    if not datos["user"]:
        raise SinConfiguracion(f"No se encontró la conexión a la base en {ruta_conexion()}. "
                               "Vuelve a ejecutar el instalador para crearla.")
    return datos


def escribir_parametros(host, puerto, base, usuario, clave):
    os.makedirs(carpeta_datos(), exist_ok=True)
    p = configparser.ConfigParser()
    p["postgresql"] = {"host": host, "puerto": str(puerto), "base": base, "usuario": usuario, "clave": clave}
    tmp = ruta_conexion() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        p.write(f)
    os.replace(tmp, ruta_conexion())


# --- Reserva de conexiones ---

_reserva = queue.LifoQueue(maxsize=8)
_LOCK = threading.Lock()
_esquema_listo = False


def _conectar():
    con = pg8000.dbapi.connect(timeout=15, application_name="nearleads-inmobiliarias", **leer_parametros())
    con.autocommit = False
    return con


def _tomar():
    try:
        return _reserva.get_nowait()
    except queue.Empty:
        return _conectar()


def _devolver(con):
    try:
        _reserva.put_nowait(con)
    except queue.Full:
        con.close()


class Cursor:
    """Cursor con filas como dict y una interfaz parecida a la de sqlite3/psycopg: execute(...).fetchone()."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        params = tuple(params)
        # Sin parámetros pg8000 manda el texto tal cual: «%%» (el % literal cuando sí hay parámetros) se deja en «%».
        self._cur.execute(sql if params else sql.replace("%%", "%"), params)
        return self

    def _nombres(self):
        return [d[0] for d in (self._cur.description or [])]

    def fetchone(self):
        fila = self._cur.fetchone() if self._cur.description else None
        return None if fila is None else dict(zip(self._nombres(), fila))

    def fetchall(self):
        if not self._cur.description:
            return []
        nombres = self._nombres()
        return [dict(zip(nombres, f)) for f in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount


class Conexion:
    def __init__(self, con):
        self._con = con

    def execute(self, sql, params=()):
        return Cursor(self._con.cursor()).execute(sql, params)


@contextmanager
def transaccion(escritura=False):
    """O se guarda todo o nada. Las lecturas también van en transacción para ver una sola versión de los datos."""
    preparar()
    con = _tomar()
    sana = True
    try:
        try:
            yield Conexion(con)
        except BaseException:
            try:
                con.rollback()
            except Exception:
                sana = False
            raise
        con.commit()
    except (pg8000.dbapi.InterfaceError, OSError):
        sana = False
        raise
    finally:
        if sana:
            _devolver(con)
        else:
            try:
                con.close()
            except Exception:
                pass


def _ruta_recurso(nombre):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nombre)


def preparar():
    """Crea las tablas que falten (esquema.sql es idempotente). Una vez por proceso."""
    global _esquema_listo
    if _esquema_listo:
        return
    with _LOCK:
        if _esquema_listo:
            return
        with open(_ruta_recurso("esquema.sql"), encoding="utf-8") as f:
            sql = f.read()
        con = _conectar()
        try:
            con.autocommit = True
            cur = con.cursor()
            # Un bloqueo de sesión evita que dos procesos creen las tablas al mismo tiempo.
            cur.execute("SELECT pg_advisory_lock(554401)")
            try:
                cur.execute("BEGIN")
                for sentencia in _sentencias(sql):
                    cur.execute(sentencia)
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.execute("SELECT pg_advisory_unlock(554401)")
        finally:
            con.close()
        _esquema_listo = True


def _sentencias(sql):
    """Separa por «;» al final de línea, sin comentarios. El esquema no usa funciones con cuerpo."""
    limpio = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in limpio.split(";") if s.strip()]


def probar():
    """Versión del servidor; lanza una excepción si no hay conexión."""
    con = _conectar()
    try:
        cur = con.cursor()
        cur.execute("SHOW server_version")
        return cur.fetchone()[0]
    finally:
        con.close()


# --- Conversión para JSON ---

def iso(valor):
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc).isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return valor


def a_json(valor):
    """Fila o lista de filas con tipos de PostgreSQL -> tipos de JSON (fechas ISO, uuid y Decimal como texto o número)."""
    if isinstance(valor, dict):
        return {k: a_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [a_json(v) for v in valor]
    if isinstance(valor, (datetime, date)):
        return iso(valor)
    if isinstance(valor, uuid.UUID):
        return str(valor)
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def jsonb(valor):
    """Para pasar un dict o una lista como parámetro: usar con %s::jsonb."""
    return json.dumps(valor, ensure_ascii=False)
