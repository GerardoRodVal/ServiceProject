"""Sesión del asesor en la app móvil: inicio de sesión con correo y contraseña, estado "En cita" con
ubicación, y los consejos del panel adaptados a sus visitas. Solo usa la biblioteca estándar.

Todo vive en memoria: al reiniciar el gestor, los asesores vuelven a iniciar sesión. Las ubicaciones
no se guardan en disco.
"""
import secrets
import threading
import time
from datetime import datetime, timezone

DURACION_SESION = 12 * 3600      # segundos; después el celular vuelve a pedir correo y contraseña
MAX_FALLOS = 5                   # intentos fallidos seguidos por correo antes de bloquear
BLOQUEO = 300                    # segundos de bloqueo tras MAX_FALLOS

MENSAJE_LOGIN = "Correo o contraseña incorrectos."


class Sesiones:
    def __init__(self):
        self._lock = threading.Lock()
        self._tokens = {}     # token -> {"id", "nombre", "expira"}
        self._fallos = {}     # correo -> (fallos, bloqueado_hasta)
        self._citas = {}      # id -> {"en_cita", "inicio", "ubicacion"}

    def iniciar(self, correo, password, acceso, asesores, verificar):
        """Devuelve (sesion, error). sesion = {"token", "id", "nombre"} si las credenciales son válidas.
        `asesores` es la lista pública de config.json; `acceso` tiene correo y hash por id; `verificar`
        compara la contraseña con el hash."""
        correo = str(correo or "").strip().lower()
        password = str(password or "")
        ahora = time.time()
        with self._lock:
            _, hasta = self._fallos.get(correo, (0, 0))
            if hasta > ahora:
                return None, "Demasiados intentos. Espera unos minutos y vuelve a intentar."

        asesor = next((a for a in asesores if isinstance(a, dict) and a.get("activo") is not False
                       and (acceso.get(a.get("id")) or {}).get("correo", "").lower() == correo), None)
        valido = bool(asesor) and bool(password) and verificar(password, acceso[asesor["id"]].get("hash", ""))
        if not valido:
            with self._lock:
                fallos, _ = self._fallos.get(correo, (0, 0))
                fallos += 1
                self._fallos[correo] = (0, time.time() + BLOQUEO) if fallos >= MAX_FALLOS else (fallos, 0)
            return None, MENSAJE_LOGIN

        token = secrets.token_urlsafe(32)
        with self._lock:
            self._fallos.pop(correo, None)
            self._tokens[token] = {"id": asesor["id"], "nombre": asesor["nombre"], "expira": ahora + DURACION_SESION}
        return {"token": token, "id": asesor["id"], "nombre": asesor["nombre"]}, ""

    def asesor_de(self, token):
        """Asesor (dict con id y nombre) de un token vigente, o None."""
        if not token:
            return None
        with self._lock:
            s = self._tokens.get(token)
            if not s:
                return None
            if s["expira"] < time.time():
                self._tokens.pop(token, None)
                return None
            return {"id": s["id"], "nombre": s["nombre"]}

    def cerrar(self, token):
        """Cierra la sesión. Si había una cita en curso, la termina y devuelve su resumen para guardarlo."""
        with self._lock:
            s = self._tokens.pop(token, None)
        if not s:
            return None
        return self.cita(s["id"], "terminar")

    def cita(self, asesor_id, accion):
        """accion "iniciar" activa la cita; "terminar" la cierra y devuelve su resumen para guardarlo.
        Iniciar no reinicia el cronómetro si la cita ya está en curso."""
        ahora = datetime.now(timezone.utc)
        with self._lock:
            actual = self._citas.get(asesor_id)
            if accion == "iniciar":
                if actual is None:
                    actual = {"en_cita": True, "inicio": ahora.isoformat(timespec="seconds"),
                              "ubicacion": None, "trayecto": []}
                    self._citas[asesor_id] = actual
                return self._vista(actual)
            if accion == "terminar":
                if actual is None:
                    return None
                del self._citas[asesor_id]
                inicio = datetime.fromisoformat(actual["inicio"])
                return {
                    "asesor_id": asesor_id,
                    "inicio": actual["inicio"],
                    "fin": ahora.isoformat(timespec="seconds"),
                    "duracion_seg": int((ahora - inicio).total_seconds()),
                    "ubicacion_final": actual["ubicacion"],
                    "trayecto": actual["trayecto"],
                }
            raise ValueError("Acción inválida")

    def ubicacion(self, asesor_id, lat, lng, precision):
        """Solo se acepta con cita en curso: la app envía ubicación únicamente mientras está activa."""
        try:
            lat, lng, precision = float(lat), float(lng), float(precision or 0)
        except (TypeError, ValueError):
            raise ValueError("Ubicación inválida")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180) or precision < 0:
            raise ValueError("Ubicación inválida")
        ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        punto = {"lat": lat, "lng": lng, "precision": round(precision), "hora": ahora}
        with self._lock:
            actual = self._citas.get(asesor_id)
            if actual is None:
                return None
            actual["ubicacion"] = punto
            if len(actual["trayecto"]) < 2000:
                actual["trayecto"].append(punto)
            return self._vista(actual)

    @staticmethod
    def _vista(actual):
        return {"en_cita": True, "inicio": actual["inicio"], "ubicacion": actual["ubicacion"]}

    def estado(self, asesor_id):
        with self._lock:
            actual = self._citas.get(asesor_id)
            if actual is None:
                return {"en_cita": False, "inicio": None, "ubicacion": None}
            return self._vista(actual)

    def todas(self):
        """Estado de todos los asesores con cita en curso, para el panel."""
        with self._lock:
            return {k: self._vista(v) for k, v in self._citas.items()}


# Mismos textos que la sección "Consejos" del panel (gestor-windows/panel.html, consejos()).
def consejos(asesor_id, visitas, score):
    s = {k: score.get(k, v) for k, v in {
        "umbral_alta": 70, "presupuesto_cubre": 30, "credito_preaprobado": 20, "enganche_alto": 15,
        "enganche_alto_pct": 20, "plazo_corto": 20, "decide_solo": 15}.items()}
    mias = [v for v in visitas if v.get("asesor_id") == asesor_id]
    lista = []
    altas = [v for v in mias if v.get("cat") == "alta" and v.get("estado") == "nuevo"]
    if altas:
        mejor = max(altas, key=lambda v: v.get("score", 0))
        n = len(altas)
        plural = "prospecto de prioridad alta" if n == 1 else "prospectos de prioridad alta"
        lista.append({"urgente": True, "tag": "Misión del día",
                      "texto": f"Tienes {n} {plural} sin contactar. Llama primero a {mejor.get('nombre') or 'quien más puntos tiene'}: es quien más puntos tiene."})
    medias = sum(1 for v in mias if v.get("cat") == "media" and v.get("credito") == "no")
    if medias:
        plural = "prospecto de prioridad media no tiene" if medias == 1 else "prospectos de prioridad media no tienen"
        lista.append({"tag": "Sube de nivel",
                      "texto": f"{medias} {plural} crédito. Ayúdales a precalificar: un crédito preaprobado suma +{s['credito_preaprobado']} puntos y puede subirlos a prioridad alta."})
    lista += [
        {"tag": "Prospecto alto", "texto": f"Un prospecto es de prioridad alta desde {s['umbral_alta']} puntos. Lo que más suma: presupuesto que cubre el precio (+{s['presupuesto_cubre']}) y crédito preaprobado (+{s['credito_preaprobado']})."},
        {"tag": "Prospecto alto", "texto": f"Pregunta cuánto tiene para el enganche: con {s['enganche_alto_pct']}% o más suma +{s['enganche_alto']} puntos y el cierre es más rápido."},
        {"tag": "Prospecto alto", "texto": f"Pregunta para cuándo necesita mudarse. Si es en menos de 3 meses suma +{s['plazo_corto']} puntos: es alguien listo para decidir."},
        {"tag": "Cómo vender", "texto": "Responde rápido: un prospecto que recibe llamada el mismo día de su visita recuerda mejor la propiedad y agenda con más facilidad."},
        {"tag": "Cómo vender", "texto": "Termina cada visita con un siguiente paso con fecha: segunda visita, envío de cotización o cita con el banco."},
        {"tag": "Cómo vender", "texto": "Anota las objeciones en «Notas» durante la visita: con ellas preparas la siguiente llamada y respondes justo lo que le preocupa."},
    ]
    return lista[:6]
