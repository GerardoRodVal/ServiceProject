"""Venta genérica: catálogo de productos, existencias y ventas (abarrotes, tiendas, refacciones, cualquier producto).

Los datos viven en la misma base SQLite que las visitas y el taller. Al cobrar, el precio y la existencia se
leen del catálogo: el panel nunca decide el precio de una venta. El IVA de cada producto se desglosa del precio
final (el precio incluye IVA). Usa solo la biblioteca estándar de Python.
"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

PAGOS = ("efectivo", "tarjeta", "transferencia")
TASAS_IVA = (0.0, 0.16)
MAX_LINEAS = 60
MAX_CANTIDAD = 100000
MAX_MONTO = 10 ** 9


class ErrorVenta(Exception):
    def __init__(self, mensaje, codigo=400):
        super().__init__(mensaje)
        self.codigo = codigo


def _texto(valor, largo):
    return " ".join(str(valor if valor is not None else "").split())[:largo]


def _numero(valor, minimo, maximo, etiqueta, decimales=2):
    try:
        n = float(valor if valor not in (None, "") else 0)
    except (TypeError, ValueError):
        raise ErrorVenta(f"{etiqueta} debe ser un número.")
    if not minimo <= n <= maximo:
        raise ErrorVenta(f"{etiqueta} está fuera de rango.")
    return round(n, decimales)


def _cantidad(valor, etiqueta="La cantidad"):
    n = _numero(valor, 0.001, MAX_CANTIDAD, etiqueta, 3)
    return n


def sanear_producto(entrada):
    if not isinstance(entrada, dict):
        raise ErrorVenta("Producto no válido.")
    nombre = _texto(entrada.get("nombre"), 80)
    if not nombre:
        raise ErrorVenta("Cada producto necesita nombre.")
    precio = _numero(entrada.get("precio"), 0, MAX_MONTO, "El precio")
    if precio <= 0:
        raise ErrorVenta(f"«{nombre}» necesita un precio mayor que cero.")
    tasa = entrada.get("tasa_iva", 0.16)
    try:
        tasa = float(tasa)
    except (TypeError, ValueError):
        raise ErrorVenta("El IVA no es válido.")
    if tasa not in TASAS_IVA:
        raise ErrorVenta("El IVA debe ser 0% o 16%.")
    return {
        "nombre": nombre,
        "codigo": _texto(entrada.get("codigo"), 40),
        "categoria": _texto(entrada.get("categoria"), 40),
        "unidad": _texto(entrada.get("unidad") or "pieza", 20),
        "precio": precio,
        "costo": _numero(entrada.get("costo"), 0, MAX_MONTO, "El costo"),
        "tasa_iva": tasa,
        "existencia": _numero(entrada.get("existencia"), 0, MAX_CANTIDAD, "La existencia", 3),
        "minimo": _numero(entrada.get("minimo"), 0, MAX_CANTIDAD, "El mínimo", 3),
        "activo": 1 if entrada.get("activo", True) else 0,
    }


def _fmt(n):
    return f"{n:g}"


class AlmacenVenta:
    """Productos y ventas. Comparte la base SQLite de las visitas."""

    def __init__(self, almacen):
        self.ruta = almacen.ruta
        self.lock = threading.Lock()
        with self._db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS productos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, codigo TEXT NOT NULL DEFAULT '',
                    categoria TEXT NOT NULL DEFAULT '', unidad TEXT NOT NULL DEFAULT 'pieza',
                    precio REAL NOT NULL, costo REAL NOT NULL DEFAULT 0, tasa_iva REAL NOT NULL DEFAULT 0.16,
                    existencia REAL NOT NULL DEFAULT 0, minimo REAL NOT NULL DEFAULT 0,
                    activo INTEGER NOT NULL DEFAULT 1, actualizado TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS ventas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, creado TEXT NOT NULL, articulos REAL NOT NULL,
                    subtotal REAL NOT NULL, descuento REAL NOT NULL, total REAL NOT NULL, iva REAL NOT NULL,
                    pago TEXT NOT NULL, recibido REAL NOT NULL, cambio REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS venta_lineas (
                    venta_id INTEGER NOT NULL, producto_id INTEGER, nombre TEXT NOT NULL, unidad TEXT NOT NULL,
                    precio REAL NOT NULL, cantidad REAL NOT NULL, importe REAL NOT NULL, iva REAL NOT NULL);
            """)

    @contextmanager
    def _db(self):
        # Autocommit: cada transacción se abre y se cierra explícitamente (BEGIN / COMMIT).
        conn = sqlite3.connect(self.ruta, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # --- Productos ---

    def _producto(self, f):
        precio, costo = f["precio"], f["costo"]
        return {
            "id": f["id"], "nombre": f["nombre"], "codigo": f["codigo"], "categoria": f["categoria"],
            "unidad": f["unidad"], "precio": precio, "costo": costo, "tasa_iva": f["tasa_iva"],
            "existencia": f["existencia"], "minimo": f["minimo"], "activo": bool(f["activo"]),
            "margen": round((precio - costo) / precio * 100, 1) if precio else 0,
            "bajo_minimo": f["minimo"] > 0 and f["existencia"] <= f["minimo"],
        }

    def listar_productos(self):
        with self._db() as conn:
            filas = conn.execute("SELECT * FROM productos ORDER BY nombre COLLATE NOCASE").fetchall()
        return [self._producto(f) for f in filas]

    def guardar_producto(self, entrada, id_=None):
        datos = sanear_producto(entrada)
        ahora = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.lock, self._db() as conn:
            if datos["codigo"]:
                repetido = conn.execute("SELECT id FROM productos WHERE codigo = ? AND activo = 1 AND id IS NOT ?",
                                        (datos["codigo"], id_)).fetchone()
                if repetido:
                    raise ErrorVenta("Ya hay un producto activo con ese código.")
            if id_ is None:
                cur = conn.execute(
                    """INSERT INTO productos (nombre, codigo, categoria, unidad, precio, costo, tasa_iva,
                       existencia, minimo, activo, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (datos["nombre"], datos["codigo"], datos["categoria"], datos["unidad"], datos["precio"],
                     datos["costo"], datos["tasa_iva"], datos["existencia"], datos["minimo"], datos["activo"], ahora))
                id_ = cur.lastrowid
            else:
                if not conn.execute("SELECT id FROM productos WHERE id = ?", (id_,)).fetchone():
                    raise ErrorVenta("Producto no encontrado.", 404)
                conn.execute(
                    """UPDATE productos SET nombre=?, codigo=?, categoria=?, unidad=?, precio=?, costo=?, tasa_iva=?,
                       existencia=?, minimo=?, activo=?, actualizado=? WHERE id=?""",
                    (datos["nombre"], datos["codigo"], datos["categoria"], datos["unidad"], datos["precio"],
                     datos["costo"], datos["tasa_iva"], datos["existencia"], datos["minimo"], datos["activo"],
                     ahora, id_))
        return self._producto(self._fila_producto(id_))

    def _fila_producto(self, id_):
        with self._db() as conn:
            f = conn.execute("SELECT * FROM productos WHERE id = ?", (id_,)).fetchone()
        if not f:
            raise ErrorVenta("Producto no encontrado.", 404)
        return f

    # --- Ventas ---

    def vender(self, entrada):
        if not isinstance(entrada, dict):
            raise ErrorVenta("Venta no válida.")
        lineas_in = entrada.get("lineas")
        if not isinstance(lineas_in, list) or not lineas_in:
            raise ErrorVenta("El ticket está vacío.")
        if len(lineas_in) > MAX_LINEAS:
            raise ErrorVenta(f"Un ticket admite hasta {MAX_LINEAS} productos.")
        cantidades = {}
        for linea in lineas_in:
            if not isinstance(linea, dict) or not isinstance(linea.get("producto_id"), int) \
                    or isinstance(linea.get("producto_id"), bool):
                raise ErrorVenta("Producto no válido en el ticket.")
            pid = linea["producto_id"]
            cantidades[pid] = round(cantidades.get(pid, 0) + _cantidad(linea.get("cantidad")), 3)
        pago = entrada.get("pago", "efectivo")
        if pago not in PAGOS:
            raise ErrorVenta("Forma de pago no válida.")
        descuento = _numero(entrada.get("descuento"), 0, MAX_MONTO, "El descuento")

        with self.lock, self._db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                lineas = []
                for pid, cantidad in cantidades.items():
                    f = conn.execute("SELECT * FROM productos WHERE id = ?", (pid,)).fetchone()
                    if not f or not f["activo"]:
                        raise ErrorVenta("Uno de los productos ya no está disponible. Quítalo del ticket.")
                    if f["existencia"] < cantidad:
                        raise ErrorVenta(f"No hay suficiente «{f['nombre']}». Existencia: {_fmt(f['existencia'])} {f['unidad']}.")
                    lineas.append({"producto_id": pid, "nombre": f["nombre"], "unidad": f["unidad"],
                                   "precio": f["precio"], "cantidad": cantidad, "tasa": f["tasa_iva"],
                                   "importe": round(f["precio"] * cantidad, 2)})
                subtotal = round(sum(x["importe"] for x in lineas), 2)
                if descuento > subtotal:
                    raise ErrorVenta("El descuento no puede ser mayor que el subtotal.")
                total = round(subtotal - descuento, 2)
                if total <= 0:
                    raise ErrorVenta("El total debe ser mayor que cero.")
                # Reparte el total entre líneas (con el descuento) y saca el IVA incluido de cada una.
                restante, factor = total, total / subtotal
                for k, x in enumerate(lineas):
                    neto = restante if k == len(lineas) - 1 else round(x["importe"] * factor, 2)
                    restante = round(restante - neto, 2)
                    x["neto"] = neto
                    x["iva"] = round(neto * x["tasa"] / (1 + x["tasa"]), 2)
                iva = round(sum(x["iva"] for x in lineas), 2)
                if pago == "efectivo":
                    recibido = _numero(entrada.get("recibido"), 0, MAX_MONTO, "El efectivo recibido")
                    if recibido < total:
                        raise ErrorVenta("El efectivo recibido no alcanza para el total.")
                    cambio = round(recibido - total, 2)
                else:
                    recibido, cambio = total, 0.0
                articulos = round(sum(x["cantidad"] for x in lineas), 3)
                creado = datetime.now().astimezone().isoformat(timespec="seconds")
                cur = conn.execute(
                    """INSERT INTO ventas (creado, articulos, subtotal, descuento, total, iva, pago, recibido, cambio)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (creado, articulos, subtotal, descuento, total, iva, pago, recibido, cambio))
                venta_id = cur.lastrowid
                for x in lineas:
                    conn.execute(
                        """INSERT INTO venta_lineas (venta_id, producto_id, nombre, unidad, precio, cantidad, importe, iva)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (venta_id, x["producto_id"], x["nombre"], x["unidad"], x["precio"], x["cantidad"],
                         x["neto"], x["iva"]))
                    conn.execute("UPDATE productos SET existencia = existencia - ?, actualizado = ? WHERE id = ?",
                                 (x["cantidad"], creado, x["producto_id"]))
                conn.execute("COMMIT")
            except ErrorVenta:
                conn.execute("ROLLBACK")
                raise
            except sqlite3.Error:
                conn.execute("ROLLBACK")
                raise ErrorVenta("No se pudo registrar la venta. Intenta de nuevo.", 500)
        return self.detalle(venta_id)

    def _venta(self, f, lineas):
        return {
            "id": f["id"], "folio": f"V{f['id']:06d}", "creado": f["creado"], "articulos": f["articulos"],
            "subtotal": f["subtotal"], "descuento": f["descuento"], "total": f["total"], "iva": f["iva"],
            "pago": f["pago"], "recibido": f["recibido"], "cambio": f["cambio"],
            "lineas": [{"nombre": l["nombre"], "unidad": l["unidad"], "precio": l["precio"],
                        "cantidad": l["cantidad"], "importe": l["importe"], "iva": l["iva"]} for l in lineas],
        }

    def detalle(self, venta_id):
        with self._db() as conn:
            f = conn.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
            if not f:
                raise ErrorVenta("Venta no encontrada.", 404)
            lineas = conn.execute("SELECT * FROM venta_lineas WHERE venta_id = ? ORDER BY rowid", (venta_id,)).fetchall()
        return self._venta(f, lineas)

    def ventas_recientes(self, limite=200):
        with self._db() as conn:
            filas = conn.execute("SELECT * FROM ventas ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
            ids = [f["id"] for f in filas]
            lineas = {}
            if ids:
                marcas = ",".join("?" * len(ids))
                for l in conn.execute(f"SELECT * FROM venta_lineas WHERE venta_id IN ({marcas}) ORDER BY rowid", ids):
                    lineas.setdefault(l["venta_id"], []).append(l)
        return [self._venta(f, lineas.get(f["id"], [])) for f in filas]

    def resumen(self):
        hoy = datetime.now().astimezone().date().isoformat()
        with self._db() as conn:
            n, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM ventas WHERE creado LIKE ?",
                                    (hoy + "%",)).fetchone()
        productos = self.listar_productos()
        return {
            "hoy": {"ventas": n, "total": round(total, 2), "ticket_promedio": round(total / n, 2) if n else 0},
            "bajo_minimo": sum(1 for p in productos if p["activo"] and p["bajo_minimo"]),
            "agotados": sum(1 for p in productos if p["activo"] and p["existencia"] <= 0),
        }
