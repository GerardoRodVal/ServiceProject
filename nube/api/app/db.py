"""Conexión a PostgreSQL. Cada petición corre en una transacción con el negocio fijado:
PostgreSQL aplica el aislamiento (seguridad por fila) aunque una consulta olvide filtrar."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def abrir(database_url):
    global _pool
    _pool = ConnectionPool(database_url, min_size=1, max_size=10, open=False, kwargs={"row_factory": dict_row})
    _pool.open(wait=True, timeout=30)


def cerrar():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def comprobar():
    with _pool.connection() as con:
        return con.execute("SELECT 1 AS ok").fetchone()["ok"] == 1


@contextmanager
def conexion():
    """Conexión sin negocio fijado: solo para lo que es público por diseño (ver imagen_catalogo)."""
    with _pool.connection() as con:
        yield con


@contextmanager
def transaccion(negocio_id=None):
    """Transacción con app.negocio_id fijado (is_local=true: se borra al terminar, no se filtra a otra petición)."""
    with _pool.connection() as con:
        with con.transaction():
            if negocio_id is not None:
                con.execute("SELECT set_config('app.negocio_id', %s, true)", (str(negocio_id),))
            yield con


def identificar(identidad):
    """Usuario y negocio de una identidad ya verificada (correo en local, oid en Entra), o None."""
    with _pool.connection() as con:
        return con.execute("SELECT * FROM identificar_usuario(%s)", (identidad,)).fetchone()
