"""Giros: los registros y reglas propias de cada tipo de negocio, apoyados en el núcleo (nucleo.py).

Un giro declara sus tipos de registro (se guardan en registros_giro), los estados que admite cada tipo y cómo validar
los datos de sus oportunidades (datos_giro). El núcleo no conoce ningún giro concreto: solo llama a
validar_oportunidad(); si el giro no está registrado, los datos pasan sin validar.

Este módulo no importa al núcleo ni a las rutas: solo recibe una conexión ya fijada al negocio.
Hay tres giros: inmobiliarias, taller y venta. Cada uno usa las mismas piezas del núcleo (contactos, catálogo, archivos).
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable


class ErrorGiro(ValueError):
    """Dato que el giro no acepta. Las rutas lo convierten en 422."""


@dataclass(frozen=True)
class Tipo:
    validar: Callable                  # (con, datos) -> datos limpios
    estados: tuple                     # estados que admite el tipo
    inicial: str                       # estado por omisión
    al_crear: Callable | None = None   # (con, datos_limpios, contexto) -> efectos, como mover existencias
    editable: bool = True              # False: el registro no se corrige después (una venta cobrada)
    crear_solo_inicial: bool = False   # True: al crear solo se admite el estado inicial (una venta no nace anulada)
    solo_admin: bool = False           # True: solo el administrador puede registrar este tipo


# --- Ayudas de validación ----------------------------------------------------------------------------------------------

def _campos(datos, requeridos, opcionales):
    if not isinstance(datos, dict):
        raise ErrorGiro("Los datos deben ser un objeto.")
    desconocidos = set(datos) - set(requeridos) - set(opcionales)
    if desconocidos:
        raise ErrorGiro(f"Campos no reconocidos: {', '.join(sorted(desconocidos))}.")
    faltan = [c for c in requeridos if datos.get(c) in (None, "")]
    if faltan:
        raise ErrorGiro(f"Faltan datos: {', '.join(faltan)}.")
    # Un opcional vacío es como no mandarlo.
    return {k: v for k, v in datos.items() if not (k in opcionales and v in (None, ""))}


def _historial_fila(con, contexto, entidad, entidad_id, campo, antes, despues):
    con.execute(
        "INSERT INTO historial (negocio_id, entidad, entidad_id, campo, antes, despues, usuario_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (contexto["negocio_id"], entidad, str(entidad_id), campo,
         None if antes is None else str(antes), None if despues is None else str(despues), contexto.get("usuario_id")))


def _texto(valor, campo, largo):
    if not isinstance(valor, str) or not 1 <= len(valor.strip()) <= largo:
        raise ErrorGiro(f"{campo} debe tener entre 1 y {largo} caracteres.")
    return valor.strip()


def _numero(valor, campo, minimo, maximo):
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        raise ErrorGiro(f"{campo} debe ser un número.")
    try:
        n = float(valor)
    except ValueError:
        raise ErrorGiro(f"{campo} debe ser un número.")
    if not minimo <= n <= maximo:       # también rechaza nan
        raise ErrorGiro(f"{campo} debe estar entre {minimo:g} y {maximo:g}.")
    return n


def _entero(valor, campo, minimo, maximo):
    n = _numero(valor, campo, minimo, maximo)
    if not n.is_integer():
        raise ErrorGiro(f"{campo} debe ser un número entero.")
    return int(n)


def _opcion(valor, campo, opciones):
    if valor not in opciones:
        raise ErrorGiro(f"{campo} debe ser uno de: {', '.join(opciones)}.")
    return valor


def _id(valor, campo):
    try:
        return str(uuid.UUID(str(valor)))
    except ValueError:
        raise ErrorGiro(f"{campo} no es un identificador válido.")


# --- Inmobiliarias -----------------------------------------------------------------------------------------------------

INMOBILIARIAS = "inmobiliarias"
TIPOS_INMUEBLE = ("casa", "departamento", "terreno")
TIPOS_ZONA = ("desarrollo", "colonia")
ESTATUS = ("disponible", "apartado", "vendido")
CREDITOS = ("no", "tramite", "preaprobado")
PLAZOS = ("0", "3", "6", "x")
DECIDE = ("solo", "pareja", "familia")
DERIVADOS = ("score", "categoria", "razones")      # los calcula el servidor; lo que mande el cliente se descarta

# Pesos del puntaje de compra: los mismos que usa el gestor de Windows por omisión (config.json, sección score).
# Cuando cada negocio pueda ajustarlos, estos valores pasan a su configuración.
PESOS = {
    "presupuesto_cubre": 30, "presupuesto_cerca": 15, "cerca_precio_pct": 90,
    "credito_preaprobado": 20, "credito_tramite": 10,
    "enganche_alto": 15, "enganche_alto_pct": 20, "enganche_medio": 8, "enganche_medio_pct": 10,
    "plazo_corto": 20, "plazo_medio": 12, "plazo_largo": 4,
    "decide_solo": 15, "decide_pareja": 10, "decide_familia": 5,
    "umbral_alta": 70, "umbral_media": 40,
}


def propiedad(con, propiedad_id):
    """La propiedad (registro del giro) o None. El negocio lo fija la conexión: nunca se ve una ajena."""
    return con.execute(
        "SELECT * FROM registros_giro WHERE id = %s AND giro = %s AND tipo = 'propiedad'",
        (propiedad_id, INMOBILIARIAS)).fetchone()


def _validar_propiedad(con, datos):
    limpio = _campos(datos, requeridos=("titulo", "tipo_inmueble", "precio"), opcionales=("zona_id", "recamaras", "m2"))
    limpio["titulo"] = _texto(limpio["titulo"], "El título", 120)
    limpio["tipo_inmueble"] = _opcion(limpio["tipo_inmueble"], "El tipo de inmueble", TIPOS_INMUEBLE)
    limpio["precio"] = _numero(limpio["precio"], "El precio", 0, 1e10)
    if "zona_id" in limpio:
        zona_id = _id(limpio["zona_id"], "La zona")
        existe = con.execute("SELECT 1 FROM registros_giro WHERE id = %s AND giro = %s AND tipo = 'zona'",
                             (zona_id, INMOBILIARIAS)).fetchone()
        if not existe:
            raise ErrorGiro("La zona no existe en tu negocio.")
        limpio["zona_id"] = zona_id
    if "recamaras" in limpio:
        limpio["recamaras"] = _entero(limpio["recamaras"], "Las recámaras", 0, 50)
    if "m2" in limpio:
        limpio["m2"] = _numero(limpio["m2"], "Los metros cuadrados", 0, 1e6)
    return limpio


def _validar_zona(con, datos):
    limpio = _campos(datos, requeridos=("nombre", "tipo_zona"), opcionales=())
    limpio["nombre"] = _texto(limpio["nombre"], "El nombre", 120)
    limpio["tipo_zona"] = _opcion(limpio["tipo_zona"], "El tipo de zona", TIPOS_ZONA)
    return limpio


def _puntaje(precio, d):
    """Mismo criterio que calcular_score del gestor de Windows: cinco criterios, 100 puntos en total."""
    p = PESOS
    pres = d.get("presupuesto", 0)
    razones = []
    if precio and pres >= precio:
        razones.append(("Presupuesto cubre el precio", p["presupuesto_cubre"]))
    elif precio and pres >= precio * p["cerca_precio_pct"] / 100:
        razones.append((f"Presupuesto cerca del precio ({p['cerca_precio_pct']}% o más)", p["presupuesto_cerca"]))
    else:
        razones.append(("Presupuesto por debajo del precio", 0))
    credito = d.get("credito")
    if credito == "preaprobado":
        razones.append(("Crédito preaprobado", p["credito_preaprobado"]))
    elif credito == "tramite":
        razones.append(("Crédito en trámite", p["credito_tramite"]))
    else:
        razones.append(("Sin crédito", 0))
    eng = d.get("enganche", 0)
    if eng >= p["enganche_alto_pct"]:
        razones.append((f"Enganche de {p['enganche_alto_pct']}% o más", p["enganche_alto"]))
    elif eng >= p["enganche_medio_pct"]:
        razones.append((f"Enganche de {p['enganche_medio_pct']} a {p['enganche_alto_pct'] - 1}%", p["enganche_medio"]))
    else:
        razones.append((f"Enganche menor a {p['enganche_medio_pct']}% o sin dato", 0))
    plazo = d.get("plazo")
    razones.append({"0": ("Compra en menos de 3 meses", p["plazo_corto"]),
                    "3": ("Compra en 3 a 6 meses", p["plazo_medio"]),
                    "6": ("Compra en más de 6 meses", p["plazo_largo"])}.get(plazo, ("Plazo sin definir", 0)))
    decide = d.get("decide")
    razones.append({"solo": ("Decide solo", p["decide_solo"]),
                    "pareja": ("Decide con pareja", p["decide_pareja"]),
                    "familia": ("Decide con familia", p["decide_familia"])}.get(decide, ("Quién decide sin definir", 0)))
    score = sum(pts for _, pts in razones)
    categoria = "alta" if score >= p["umbral_alta"] else "media" if score >= p["umbral_media"] else "baja"
    return score, categoria, [{"txt": t, "pts": pts} for t, pts in razones]


def _validar_oportunidad(con, datos):
    datos = {k: v for k, v in (datos or {}).items() if k not in DERIVADOS}
    # propiedad: id del catálogo que publica el gestor (p. ej. «modelo-encino»); ultima_visita: la pone el enlace con las
    # visitas (visitas_nucleo.py). propiedad_id: una propiedad en registros_giro (hasta aplicar la migración 011).
    limpio = _campos(datos, requeridos=(), opcionales=("presupuesto", "enganche", "credito", "plazo", "decide", "propiedad_id",
                                                       "propiedad", "ultima_visita"))
    if "propiedad" in limpio:
        limpio["propiedad"] = _texto(limpio["propiedad"], "La propiedad", 60)
    if "ultima_visita" in limpio:
        u = limpio["ultima_visita"] if isinstance(limpio["ultima_visita"], dict) else {}
        limpio["ultima_visita"] = {"id": str(u.get("id") or "")[:64], "asesor": str(u.get("asesor") or "")[:60],
                                   "creado": str(u.get("creado") or "")[:40]}
    if "presupuesto" in limpio:
        limpio["presupuesto"] = _numero(limpio["presupuesto"], "El presupuesto", 0, 1e10)
    if "enganche" in limpio:
        limpio["enganche"] = _numero(limpio["enganche"], "El enganche (%)", 0, 100)
    if "credito" in limpio:
        limpio["credito"] = _opcion(limpio["credito"], "El crédito", CREDITOS)
    if "plazo" in limpio:
        limpio["plazo"] = _opcion(str(limpio["plazo"]), "El plazo", PLAZOS)
    if "decide" in limpio:
        limpio["decide"] = _opcion(limpio["decide"], "Quién decide", DECIDE)
    precio = 0.0
    if "propiedad_id" in limpio:
        limpio["propiedad_id"] = _id(limpio["propiedad_id"], "La propiedad")
        prop = propiedad(con, limpio["propiedad_id"])
        if not prop:
            raise ErrorGiro("La propiedad no existe en tu negocio.")
        precio = float(prop["datos"]["precio"])
    elif "propiedad" in limpio:
        from .visitas_nucleo import precio_propiedad      # catálogo que publica el gestor (configuraciones)
        precio = precio_propiedad(con, limpio["propiedad"])
    score, categoria, razones = _puntaje(precio, limpio)
    return {**limpio, "score": score, "categoria": categoria, "razones": razones}


# --- Taller mecánico --------------------------------------------------------------------------------------------------
# Un vehículo pertenece a un contacto (contacto_id del registro). La orden de servicio lleva la lista de servicios del
# catálogo con su estado; las fotos por etapa son archivos del núcleo ligados al registro (ver giros_rutas.py).

TALLER = "taller"
ESTADOS_ORDEN = ("recibido", "en_proceso", "terminado", "entregado")
ESTADOS_SERVICIO = ("pendiente", "en_proceso", "hecho")
ETAPAS_FOTO = ("recepcion", "avance", "entrega")
MAX_SERVICIOS = 50
MAX_FOTOS_REGISTRO = 30


def _validar_vehiculo(con, datos):
    limpio = _campos(datos, requeridos=("marca", "modelo"), opcionales=("anio", "placas", "color"))
    limpio["marca"] = _texto(limpio["marca"], "La marca", 40)
    limpio["modelo"] = _texto(limpio["modelo"], "El modelo", 60)
    if "anio" in limpio:
        limpio["anio"] = _entero(limpio["anio"], "El año", 1950, 2100)
    if "placas" in limpio:
        limpio["placas"] = _texto(limpio["placas"], "Las placas", 15).upper()
    if "color" in limpio:
        limpio["color"] = _texto(limpio["color"], "El color", 30)
    return limpio


def _servicios(con, lista):
    if not isinstance(lista, list) or not 1 <= len(lista) <= MAX_SERVICIOS:
        raise ErrorGiro(f"La orden necesita entre 1 y {MAX_SERVICIOS} servicios.")
    salida = []
    for s in lista:
        if not isinstance(s, dict) or set(s) - {"catalogo_id", "estado", "nota"} or not s.get("catalogo_id"):
            raise ErrorGiro("Cada servicio necesita catalogo_id y puede traer estado y nota.")
        art = con.execute("SELECT id, nombre FROM catalogo WHERE id = %s AND tipo = 'servicio' AND activo",
                          (_id(s["catalogo_id"], "El servicio"),)).fetchone()
        if not art:
            raise ErrorGiro("Un servicio no existe en el catálogo o está inactivo.")
        item = {"catalogo_id": str(art["id"]), "nombre": art["nombre"],
                "estado": _opcion(s.get("estado", "pendiente"), "El estado del servicio", ESTADOS_SERVICIO)}
        if s.get("nota"):
            item["nota"] = _texto(s["nota"], "La nota del servicio", 200)
        salida.append(item)
    return salida


def _validar_orden(con, datos):
    limpio = _campos(datos, requeridos=("vehiculo_id", "servicios"), opcionales=("promesa", "kilometraje", "nota"))
    vehiculo_id = _id(limpio["vehiculo_id"], "El vehículo")
    if not con.execute("SELECT 1 FROM registros_giro WHERE id = %s AND giro = %s AND tipo = 'vehiculo'",
                       (vehiculo_id, TALLER)).fetchone():
        raise ErrorGiro("El vehículo no existe en tu negocio.")
    limpio["vehiculo_id"] = vehiculo_id
    if "promesa" in limpio:
        try:
            limpio["promesa"] = date.fromisoformat(str(limpio["promesa"])).isoformat()
        except ValueError:
            raise ErrorGiro("La fecha de entrega debe tener formato AAAA-MM-DD.")
    if "kilometraje" in limpio:
        limpio["kilometraje"] = _entero(limpio["kilometraje"], "El kilometraje", 0, 2_000_000)
    if "nota" in limpio:
        limpio["nota"] = _texto(limpio["nota"], "La nota", 500)
    limpio["servicios"] = _servicios(con, limpio["servicios"])
    return limpio


# --- Venta (punto de venta) -------------------------------------------------------------------------------------------
# Los precios y el IVA salen del catálogo, nunca del cliente. El precio del catálogo incluye IVA. Una venta cobrada no se
# corrige: se registra otra. Al crearla baja la existencia de los artículos que la llevan.

VENTA = "venta"
PAGOS = ("efectivo", "tarjeta", "transferencia")
MAX_LINEAS = 100


def _dinero(d):
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _validar_venta(con, datos):
    limpio = _campos(datos, requeridos=("lineas", "pago"), opcionales=("descuento", "recibido"))
    pago = _opcion(limpio["pago"], "El pago", PAGOS)
    lineas_in = limpio["lineas"]
    if not isinstance(lineas_in, list) or not 1 <= len(lineas_in) <= MAX_LINEAS:
        raise ErrorGiro(f"La venta necesita entre 1 y {MAX_LINEAS} artículos.")
    lineas, pedido = [], {}
    subtotal = iva = Decimal(0)
    for linea in lineas_in:
        if not isinstance(linea, dict) or set(linea) - {"catalogo_id", "cantidad"} or "catalogo_id" not in linea or "cantidad" not in linea:
            raise ErrorGiro("Cada artículo necesita catalogo_id y cantidad.")
        articulo_id = _id(linea["catalogo_id"], "El artículo")
        cantidad = Decimal(str(_numero(linea["cantidad"], "La cantidad", 0.001, 1e6)))
        art = con.execute("SELECT * FROM catalogo WHERE id = %s AND activo FOR UPDATE", (articulo_id,)).fetchone()
        if not art:
            raise ErrorGiro("Un artículo no existe o está inactivo.")
        precio, tasa = Decimal(str(art["precio"])), Decimal(str(art["tasa_iva"]))
        importe = precio * cantidad
        subtotal += importe
        iva += importe * tasa / (1 + tasa)
        pedido[articulo_id] = pedido.get(articulo_id, Decimal(0)) + cantidad
        lineas.append({"catalogo_id": articulo_id, "nombre": art["nombre"], "tipo": art["tipo"],
                       "precio": float(precio), "cantidad": float(cantidad), "importe": _dinero(importe)})
    for articulo_id, cantidad in pedido.items():
        art = con.execute("SELECT nombre, existencia FROM catalogo WHERE id = %s", (articulo_id,)).fetchone()
        if art["existencia"] is not None and cantidad > art["existencia"]:
            raise ErrorGiro(f"No hay existencia suficiente de {art['nombre']} (quedan {float(art['existencia']):g}).")
    descuento = Decimal(str(_numero(limpio.get("descuento", 0), "El descuento", 0, float(subtotal))))
    total = subtotal - descuento
    iva_total = iva * total / subtotal if subtotal > 0 else Decimal(0)
    if pago == "efectivo":
        recibido = Decimal(str(_numero(limpio.get("recibido", float(total)), "Lo recibido", 0, 1e10)))
        if recibido < total:
            raise ErrorGiro("Lo recibido no alcanza para cubrir el total.")
        cambio = recibido - total
    else:
        recibido, cambio = total, Decimal(0)
    return {"lineas": lineas, "pago": pago, "subtotal": _dinero(subtotal), "descuento": _dinero(descuento),
            "total": _dinero(total), "iva": _dinero(iva_total), "recibido": _dinero(recibido),
            "cambio": _dinero(cambio), "articulos": float(sum(pedido.values()))}


def _descontar_existencia(con, datos, contexto):
    for linea in datos["lineas"]:
        art = con.execute("SELECT existencia FROM catalogo WHERE id = %s FOR UPDATE", (linea["catalogo_id"],)).fetchone()
        if art["existencia"] is None:
            continue   # sin inventario: no hay nada que bajar
        nueva = art["existencia"] - Decimal(str(linea["cantidad"]))
        if nueva < 0:
            raise ErrorGiro(f"No hay existencia suficiente de {linea['nombre']}.")
        con.execute("UPDATE catalogo SET existencia = %s, actualizado = now() WHERE id = %s", (nueva, linea["catalogo_id"]))
        _historial_fila(con, contexto, "articulo", linea["catalogo_id"], "existencia", art["existencia"], nueva)


def _validar_devolucion(con, datos):
    """Registro de devolución de una venta ANULADA. Es solo un registro: no mueve inventario ni dinero en el sistema.
    El monto y la forma de pago salen de la venta; el cliente no los escribe. Una venta tiene como máximo una devolución."""
    limpio = _campos(datos, requeridos=("venta_id",), opcionales=("motivo",))
    venta_id = _id(limpio["venta_id"], "La venta")
    venta = con.execute("SELECT * FROM registros_giro WHERE id = %s AND giro = %s AND tipo = 'venta' FOR UPDATE",
                        (venta_id, VENTA)).fetchone()
    if not venta:
        raise ErrorGiro("La venta no existe en tu negocio.")
    if venta["estado"] != "anulada":
        raise ErrorGiro("Solo se registra devolución de una venta anulada.")
    ya = con.execute("SELECT 1 FROM registros_giro WHERE giro = %s AND tipo = 'devolucion' AND datos->>'venta_id' = %s",
                     (VENTA, venta_id)).fetchone()
    if ya:
        raise ErrorGiro("Esa venta ya tiene una devolución registrada.")
    resultado = {"venta_id": venta_id, "monto": venta["datos"]["total"], "pago": venta["datos"]["pago"]}
    if "motivo" in limpio:
        resultado["motivo"] = _texto(limpio["motivo"], "El motivo", 300)
    return resultado


def requiere_admin(giro, tipo):
    definicion = TIPOS[giro].get(tipo)
    if definicion is None:
        raise ErrorGiro(f"Este giro no tiene el tipo «{tipo}».")
    return definicion.solo_admin


def anular_venta(con, registro, motivo, contexto):
    """Pasa una venta cobrada a anulada y devuelve su existencia. Devuelve los datos nuevos, con el bloque anulacion.
    La venta no se borra: conserva sus líneas y su historia. Cada devolución deja historial, como una venta."""
    if registro["giro"] != VENTA or registro["tipo"] != "venta":
        raise ErrorGiro("Esa no es una venta.")
    if registro["estado"] != "cobrada":
        raise ErrorGiro("Solo se anula una venta cobrada.")
    for linea in registro["datos"]["lineas"]:
        art = con.execute("SELECT existencia FROM catalogo WHERE id = %s FOR UPDATE", (linea["catalogo_id"],)).fetchone()
        if art is None or art["existencia"] is None:
            continue   # sin inventario: no hay nada que devolver
        nueva = art["existencia"] + Decimal(str(linea["cantidad"]))
        con.execute("UPDATE catalogo SET existencia = %s, actualizado = now() WHERE id = %s", (nueva, linea["catalogo_id"]))
        _historial_fila(con, contexto, "articulo", linea["catalogo_id"], "existencia", art["existencia"], nueva)
    _historial_fila(con, contexto, "registro_giro", registro["id"], "estado", "cobrada", "anulada")
    _historial_fila(con, contexto, "registro_giro", registro["id"], "anulacion", None, motivo)
    return {**registro["datos"],
            "anulacion": {"motivo": motivo, "usuario_id": str(contexto["usuario_id"]),
                          "cuando": datetime.now(timezone.utc).isoformat()}}


TIPOS = {
    INMOBILIARIAS: {
        "propiedad": Tipo(_validar_propiedad, ESTATUS, "disponible"),
        "zona": Tipo(_validar_zona, ("",), ""),
    },
    TALLER: {
        "vehiculo": Tipo(_validar_vehiculo, ("",), ""),
        "orden_servicio": Tipo(_validar_orden, ESTADOS_ORDEN, "recibido"),
    },
    VENTA: {
        "venta": Tipo(_validar_venta, ("cobrada", "anulada"), "cobrada", al_crear=_descontar_existencia,
                      editable=False, crear_solo_inicial=True),
        "devolucion": Tipo(_validar_devolucion, ("registrada",), "registrada",
                           editable=False, crear_solo_inicial=True, solo_admin=True),
    },
}
VALIDADOR_OPORTUNIDAD = {INMOBILIARIAS: _validar_oportunidad}


# --- Punto de entrada para el núcleo y las rutas -----------------------------------------------------------------------

def existe(giro):
    return giro in TIPOS


def tipos(giro):
    return sorted(TIPOS[giro])


def editable(giro, tipo):
    definicion = TIPOS[giro].get(tipo)
    if definicion is None:
        raise ErrorGiro(f"Este giro no tiene el tipo «{tipo}».")
    return definicion.editable


def validar_registro(con, giro, tipo, datos, estado, contexto=None, crear=False):
    """Devuelve (datos, estado) listos para guardar. Lanza ErrorGiro si el tipo, los datos o el estado no valen.
    Con crear=True corre además los efectos del tipo (p. ej. bajar existencias de una venta)."""
    definicion = TIPOS[giro].get(tipo)
    if definicion is None:
        raise ErrorGiro(f"Este giro no tiene el tipo «{tipo}».")
    limpio = definicion.validar(con, datos)
    estado = definicion.inicial if estado is None else estado
    if estado not in definicion.estados:
        raise ErrorGiro(f"El estado debe ser uno de: {', '.join(d for d in definicion.estados if d) or '(ninguno)'}.")
    if crear and definicion.crear_solo_inicial and estado != definicion.inicial:
        raise ErrorGiro(f"Un registro nuevo empieza en «{definicion.inicial}».")
    if crear and definicion.al_crear:
        definicion.al_crear(con, limpio, contexto or {})
    return limpio, estado


def validar_oportunidad(con, giro, datos):
    """Valida y completa datos_giro de una oportunidad. Un giro sin reglas registradas no valida nada."""
    validador = VALIDADOR_OPORTUNIDAD.get(giro)
    return validador(con, datos or {}) if validador else (datos or {})
