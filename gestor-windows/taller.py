"""Taller mecánico: vehículos, checklist de servicios, fotos y seguimiento por QR.

Cada vehículo tiene dos enlaces secretos, uno por tipo de acceso:
- token_qr: va en el QR pegado al auto. Solo permite leer el avance (lo puede ver cualquiera que escanee).
- token_taller: lo guarda el celular del mecánico. Permite cambiar servicios y subir fotos.
Los datos viven en la misma base SQLite que las visitas; las fotos, en una carpeta junto a ella.
Usa solo la biblioteca estándar de Python.
"""
import base64
import binascii
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone

VEHICULO_TIPOS = ("auto", "moto")
TAREA_ESTADOS = ("pendiente", "en_proceso", "hecho")
ETAPAS_FOTO = ("recepcion", "avance", "entrega")
MAX_TAREAS = 40
MAX_FOTOS = 30
MAX_EVENTOS = 150
MAX_FOTO_BYTES = 3 * 1024 * 1024
DATOS_CLAVES = ("vehiculo", "cliente", "recepcion", "tareas", "fotos", "eventos")

ETIQUETA_TAREA = {"pendiente": "pendiente", "en_proceso": "en proceso", "hecho": "terminado"}
ETIQUETA_ETAPA = {"recepcion": "recepción", "avance": "avance", "entrega": "entrega"}
TEXTO_ESTADO = {
    "recibido": "Vehículo recibido",
    "en_proceso": "Servicio en proceso",
    "listo": "Todos los servicios terminados: listo para entregar",
}


class ErrorTaller(Exception):
    def __init__(self, mensaje, codigo=400):
        super().__init__(mensaje)
        self.codigo = codigo


def ahora():
    return datetime.now(timezone.utc).isoformat()


def _texto(valor, largo):
    return " ".join(str(valor if valor is not None else "").split())[:largo]


def _entero(valor, minimo, maximo, etiqueta):
    if valor in (None, ""):
        return None
    try:
        n = int(float(valor))
    except (TypeError, ValueError):
        raise ErrorTaller(f"{etiqueta} debe ser un número.")
    if not minimo <= n <= maximo:
        raise ErrorTaller(f"{etiqueta} está fuera de rango.")
    return n


def _fecha(valor):
    texto = _texto(valor, 10)
    if not texto:
        return ""
    try:
        datetime.strptime(texto, "%Y-%m-%d")
    except ValueError:
        raise ErrorTaller("La fecha de entrega no es válida.")
    return texto


def sanear_vehiculo(entrada):
    """Solo pasan campos conocidos, con límite de largo. Nada del celular se guarda tal cual."""
    v = entrada.get("vehiculo") if isinstance(entrada.get("vehiculo"), dict) else {}
    c = entrada.get("cliente") if isinstance(entrada.get("cliente"), dict) else {}
    r = entrada.get("recepcion") if isinstance(entrada.get("recepcion"), dict) else {}
    tipo = v.get("tipo", "auto")
    if tipo not in VEHICULO_TIPOS:
        raise ErrorTaller("El vehículo debe ser auto o moto.")
    datos = {
        "vehiculo": {
            "tipo": tipo,
            "marca": _texto(v.get("marca"), 40),
            "modelo": _texto(v.get("modelo"), 40),
            "anio": _entero(v.get("anio"), 1950, 2100, "El año"),
            "color": _texto(v.get("color"), 30),
            "placa": _texto(v.get("placa"), 12).upper(),
            "vin": _texto(v.get("vin"), 17).upper(),
            "kilometraje": _entero(v.get("kilometraje"), 0, 2_000_000, "El kilometraje"),
        },
        "cliente": {"nombre": _texto(c.get("nombre"), 80), "telefono": _texto(c.get("telefono"), 30)},
        "recepcion": {
            "combustible": _texto(r.get("combustible"), 12),
            "notas": _texto(r.get("notas"), 600),
            "promesa": _fecha(r.get("promesa")),
        },
    }
    if not datos["vehiculo"]["marca"] or not datos["vehiculo"]["modelo"]:
        raise ErrorTaller("Escribe la marca y el modelo del vehículo.")
    if not datos["cliente"]["nombre"]:
        raise ErrorTaller("Escribe el nombre del cliente.")
    return datos


def sanear_tareas(lista):
    if not isinstance(lista, list):
        raise ErrorTaller("Los servicios no tienen un formato válido.")
    tareas = []
    for t in lista[:MAX_TAREAS]:
        nombre = _texto(t.get("nombre") if isinstance(t, dict) else t, 80)
        if nombre:
            tareas.append({"id": "t" + secrets.token_hex(4), "nombre": nombre, "estado": "pendiente",
                           "nota": "", "actualizado": None, "por": ""})
    if not tareas:
        raise ErrorTaller("Agrega al menos un servicio a realizar.")
    return tareas


def decodificar_jpeg(dato):
    """Acepta un JPEG en base64 (con o sin prefijo data:). Rechaza cualquier otro formato."""
    texto = str(dato or "")
    if texto.startswith("data:") and "," in texto:
        texto = texto.split(",", 1)[1]
    try:
        binario = base64.b64decode(texto, validate=True)
    except (binascii.Error, ValueError):
        raise ErrorTaller("La foto no se pudo leer.")
    if not 0 < len(binario) <= MAX_FOTO_BYTES:
        raise ErrorTaller("La foto es demasiado grande.")
    if not binario.startswith(b"\xff\xd8\xff"):
        raise ErrorTaller("La foto debe ser JPEG.")
    return binario


def estado_calculado(r):
    tareas = r["tareas"]
    if tareas and all(t["estado"] == "hecho" for t in tareas):
        return "listo"
    if any(t["estado"] != "pendiente" for t in tareas):
        return "en_proceso"
    return "recibido"


def progreso(r):
    tareas = r["tareas"]
    hechas = sum(1 for t in tareas if t["estado"] == "hecho")
    total = len(tareas)
    return {"hechas": hechas, "total": total, "porcentaje": round(100 * hechas / total) if total else 0}


def _evento(r, texto, quien):
    r["eventos"].append({"cuando": ahora(), "quien": _texto(quien, 60) or "Taller", "texto": texto})
    del r["eventos"][:-MAX_EVENTOS]


def _recalcular(r, quien):
    if r["estado"] == "entregado":
        return
    nuevo = estado_calculado(r)
    if nuevo != r["estado"]:
        r["estado"] = nuevo
        _evento(r, TEXTO_ESTADO[nuevo], quien)


def _desde_fila(f):
    return {"id": f["id"], "token_qr": f["token_qr"], "token_taller": f["token_taller"], "creado": f["creado"],
            "actualizado": f["actualizado"], "estado": f["estado"], **json.loads(f["datos"])}


def resumen(r):
    """Lo que el panel necesita para la lista. No incluye datos de contacto del cliente."""
    return {"id": r["id"], "estado": r["estado"], "creado": r["creado"], "actualizado": r["actualizado"],
            "vehiculo": r["vehiculo"], "cliente": {"nombre": r["cliente"]["nombre"]},
            "promesa": r["recepcion"]["promesa"], "progreso": progreso(r), "fotos": len(r["fotos"]),
            "token_qr": r["token_qr"], "token_taller": r["token_taller"]}


def vista_seguimiento(r, negocio):
    """Lo que ve el cliente al escanear el QR: sin teléfono ni notas internas de recepción."""
    v = r["vehiculo"]
    return {
        "taller": negocio,
        "estado": r["estado"],
        "actualizado": r["actualizado"],
        "vehiculo": {k: v[k] for k in ("tipo", "marca", "modelo", "anio", "color", "placa")},
        "promesa": r["recepcion"]["promesa"],
        "progreso": progreso(r),
        "tareas": [{k: t[k] for k in ("id", "nombre", "estado", "nota", "actualizado")} for t in r["tareas"]],
        "fotos": [{k: f[k] for k in ("id", "etapa", "nota", "creado")} for f in r["fotos"]],
        "eventos": r["eventos"][-40:],
    }


def vista_taller(r, negocio):
    """Lo que ve el celular del mecánico: todo, incluido el cliente y el enlace del QR."""
    return {**vista_seguimiento(r, negocio), "id": r["id"], "token_qr": r["token_qr"],
            "vehiculo": r["vehiculo"], "cliente": r["cliente"], "recepcion": r["recepcion"]}


class AlmacenTaller:
    """Vehículos del taller. Comparte la base SQLite de las visitas y guarda las fotos en carpeta aparte."""

    def __init__(self, almacen, carpeta_fotos):
        self.almacen = almacen
        self.carpeta = carpeta_fotos
        self.lock = threading.Lock()
        almacen._ejecutar("""CREATE TABLE IF NOT EXISTS taller_vehiculos (
            id TEXT PRIMARY KEY, token_qr TEXT UNIQUE NOT NULL, token_taller TEXT UNIQUE NOT NULL,
            creado TEXT NOT NULL, actualizado TEXT NOT NULL, estado TEXT NOT NULL, datos TEXT NOT NULL)""")

    def _buscar(self, campo, valor):
        if campo not in ("id", "token_qr", "token_taller"):
            raise ValueError("Campo de búsqueda no permitido")
        filas = self.almacen._ejecutar(f"SELECT * FROM taller_vehiculos WHERE {campo} = ?", (valor,), consulta=True)
        if not filas:
            raise ErrorTaller("No encontramos ese vehículo. Revisa el código o el enlace.", 404)
        return _desde_fila(filas[0])

    def _guardar(self, r):
        r["actualizado"] = ahora()
        datos = {k: r[k] for k in DATOS_CLAVES}
        self.almacen._ejecutar("UPDATE taller_vehiculos SET estado = ?, actualizado = ?, datos = ? WHERE id = ?",
                               (r["estado"], r["actualizado"], json.dumps(datos, ensure_ascii=False), r["id"]))

    def _por_token(self, campo, token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", str(token or "")):
            raise ErrorTaller("No encontramos ese vehículo. Revisa el código o el enlace.", 404)
        return self._buscar(campo, token)

    def por_token_qr(self, token):
        return self._por_token("token_qr", token)

    def por_token_taller(self, token):
        return self._por_token("token_taller", token)

    def por_id(self, id_):
        if not re.fullmatch(r"v[0-9a-f]{12}", str(id_ or "")):
            raise ErrorTaller("Vehículo no encontrado.", 404)
        return self._buscar("id", id_)

    def listar(self):
        filas = self.almacen._ejecutar("SELECT * FROM taller_vehiculos ORDER BY creado DESC", consulta=True)
        return [resumen(_desde_fila(f)) for f in filas]

    def crear(self, entrada, quien):
        if not isinstance(entrada, dict):
            raise ErrorTaller("Los datos no tienen un formato válido.")
        datos = sanear_vehiculo(entrada)
        datos["tareas"] = sanear_tareas(entrada.get("tareas"))
        momento = ahora()
        r = {"id": "v" + secrets.token_hex(6), "token_qr": secrets.token_urlsafe(16),
             "token_taller": secrets.token_urlsafe(16), "creado": momento, "actualizado": momento,
             "estado": "recibido", **datos, "fotos": [], "eventos": []}
        _evento(r, "Vehículo registrado en el taller", quien)
        with self.lock:
            self.almacen._ejecutar(
                "INSERT INTO taller_vehiculos (id, token_qr, token_taller, creado, actualizado, estado, datos) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["id"], r["token_qr"], r["token_taller"], momento, momento, r["estado"],
                 json.dumps({k: r[k] for k in DATOS_CLAVES}, ensure_ascii=False)))
        return r

    def actualizar_tarea(self, token, tarea_id, cambios, quien):
        """tarea_id None agrega un servicio nuevo. Con «eliminar» quita uno pendiente; si no, cambia estado o nota."""
        with self.lock:
            r = self.por_token_taller(token)
            if r["estado"] == "entregado":
                raise ErrorTaller("Este vehículo ya fue entregado.", 409)
            if tarea_id is None:
                nombre = _texto(cambios.get("nombre"), 80)
                if not nombre:
                    raise ErrorTaller("Escribe el nombre del servicio.")
                if len(r["tareas"]) >= MAX_TAREAS:
                    raise ErrorTaller(f"Un vehículo admite hasta {MAX_TAREAS} servicios.")
                r["tareas"].append({"id": "t" + secrets.token_hex(4), "nombre": nombre, "estado": "pendiente",
                                    "nota": "", "actualizado": None, "por": ""})
                _evento(r, f"Servicio agregado: {nombre}", quien)
            else:
                tarea = next((t for t in r["tareas"] if t["id"] == tarea_id), None)
                if tarea is None:
                    raise ErrorTaller("Ese servicio ya no está en la lista.", 404)
                if cambios.get("eliminar"):
                    if tarea["estado"] != "pendiente":
                        raise ErrorTaller("Solo se pueden quitar servicios pendientes.", 409)
                    r["tareas"].remove(tarea)
                    _evento(r, f"Servicio quitado: {tarea['nombre']}", quien)
                else:
                    if "estado" in cambios and cambios["estado"] != tarea["estado"]:
                        if cambios["estado"] not in TAREA_ESTADOS:
                            raise ErrorTaller("Estado de servicio no válido.")
                        tarea["estado"] = cambios["estado"]
                        _evento(r, f"{tarea['nombre']}: {ETIQUETA_TAREA[tarea['estado']]}", quien)
                    if "nota" in cambios:
                        nota = _texto(cambios["nota"], 300)
                        if nota != tarea["nota"]:
                            tarea["nota"] = nota
                            _evento(r, f"Nota en {tarea['nombre']}: {nota or 'borrada'}", quien)
                    tarea["actualizado"] = ahora()
                    tarea["por"] = _texto(quien, 60)
            _recalcular(r, quien)
            self._guardar(r)
            return r

    def agregar_foto(self, token, etapa, nota, quien, imagen):
        with self.lock:
            r = self.por_token_taller(token)
            if r["estado"] == "entregado":
                raise ErrorTaller("Este vehículo ya fue entregado.", 409)
            if len(r["fotos"]) >= MAX_FOTOS:
                raise ErrorTaller(f"Un vehículo admite hasta {MAX_FOTOS} fotos.")
            binario = decodificar_jpeg(imagen)
            etapa = etapa if etapa in ETAPAS_FOTO else "avance"
            foto = {"id": secrets.token_hex(6), "etapa": etapa, "nota": _texto(nota, 120), "creado": ahora(),
                    "por": _texto(quien, 60)}
            carpeta = os.path.join(self.carpeta, r["id"])
            os.makedirs(carpeta, exist_ok=True)
            with open(os.path.join(carpeta, foto["id"] + ".jpg"), "wb") as f:
                f.write(binario)
            r["fotos"].append(foto)
            _evento(r, f"Foto de {ETIQUETA_ETAPA[etapa]} agregada", quien)
            self._guardar(r)
            return foto

    def foto_binaria(self, r, foto_id):
        if not re.fullmatch(r"[0-9a-f]{12}", str(foto_id or "")) or not any(f["id"] == foto_id for f in r["fotos"]):
            raise ErrorTaller("Foto no encontrada.", 404)
        with open(os.path.join(self.carpeta, r["id"], foto_id + ".jpg"), "rb") as f:
            return f.read()

    def cambiar_estado(self, id_, accion):
        """Desde el panel: entregar el vehículo, o reabrirlo (vuelve al estado que dicen sus servicios)."""
        with self.lock:
            r = self.por_id(id_)
            if accion == "entregar":
                if r["estado"] != "entregado":
                    r["estado"] = "entregado"
                    _evento(r, "Vehículo entregado al cliente", "Panel")
            elif accion == "reabrir":
                if r["estado"] == "entregado":
                    r["estado"] = estado_calculado(r)
                    _evento(r, "Vehículo reabierto", "Panel")
            else:
                raise ErrorTaller("Acción no válida.")
            self._guardar(r)
            return r
