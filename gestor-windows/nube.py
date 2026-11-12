"""Conexión del gestor con la API en la nube (o con su simulación local en Docker, carpeta nube/).

Con la nube activa, los prospectos se guardan y se leen en la API; lo demás (taller, ventas) sigue en SQLite.
El celular sigue enviando al gestor como siempre y el gestor reenvía a la API: si la nube no responde, el gestor
contesta con error y el celular conserva la visita para reintentar. Solo biblioteca estándar.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

TIEMPO_ESPERA = 15            # segundos por petición
TIEMPO_ESPERA_FOTO = 60       # subir una foto de hasta 4 MB puede tardar más
VIGENCIA_TOKEN = 7 * 3600     # el token de prueba dura 8 h en la API; se renueva antes
TAMANO_LOTE = 100
# copia_local: guardar en esta PC lo último que se leyó del CRM (contactos, embudo, tareas) para consultarlo si la nube
# no responde. Va encendida salvo que el administrador la apague en Configuración.
CONFIG_DEFECTO = {"activo": False, "url": "", "correo": "", "copia_local": True}
CLAVES_NO_PUBLICAS = {"acceso", "ia", "taller", "conexiones", "asesores"}


class ErrorNube(Exception):
    def __init__(self, mensaje, codigo=None):
        super().__init__(mensaje)
        self.codigo = codigo


def validar_url(valor):
    """https en general; http solo hacia esta misma PC (la simulación local), para no mandar datos sin cifrar por la red."""
    v = str(valor or "").strip().rstrip("/")
    p = urlparse(v)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.path not in ("", "/") or p.query or p.fragment:
        raise ValueError("Escribe la dirección de la API, por ejemplo https://mi-api.azurewebsites.net")
    if p.scheme == "http" and p.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Una dirección fuera de esta PC debe empezar con https://")
    return v


def validar_correo(valor):
    v = str(valor or "").strip()
    if not 3 <= len(v) <= 200 or "@" not in v or " " in v:
        raise ValueError("Escribe el correo del usuario administrador del negocio en la nube.")
    return v


class ClienteNube:
    def __init__(self, url, correo):
        self.url = validar_url(url)
        self.correo = validar_correo(correo)
        self._token = None
        self._vence = 0.0
        self._lock = threading.Lock()

    def _obtener_token(self):
        with self._lock:
            if self._token and time.time() < self._vence:
                return self._token
            r = self._enviar("POST", "/dev/token", {"correo": self.correo}, con_token=False)
            self._token, self._vence = r["token"], time.time() + VIGENCIA_TOKEN
            return self._token

    def _enviar(self, metodo, ruta, cuerpo=None, con_token=True, crudo=None, tipo="application/octet-stream"):
        """cuerpo: JSON. crudo: bytes tal cual (por ejemplo una foto), con su tipo."""
        if crudo is not None:
            datos = crudo
        else:
            datos = None if cuerpo is None else json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        cabeceras = {"Accept": "application/json"}
        if datos is not None:
            cabeceras["Content-Type"] = tipo if crudo is not None else "application/json"
        if con_token:
            cabeceras["Authorization"] = f"Bearer {self._obtener_token()}"
        req = urllib.request.Request(self.url + ruta, data=datos, headers=cabeceras, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=TIEMPO_ESPERA_FOTO if crudo is not None else TIEMPO_ESPERA) as res:
                return json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise ErrorNube(self._mensaje_http(e, ruta), e.code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ErrorNube(f"No hay conexión con la nube ({getattr(e, 'reason', e)}).")
        except ValueError:
            raise ErrorNube("La nube respondió algo que no se entiende.")

    @staticmethod
    def _mensaje_http(e, ruta):
        try:
            detalle = json.loads(e.read()).get("detail")
        except (ValueError, AttributeError):
            detalle = None
        # En una nube real el endpoint de prueba no existe (404 genérico); en la local, el 404 trae el motivo.
        if ruta == "/dev/token" and e.code in (404, 405) and detalle in (None, "Not Found", "Method Not Allowed"):
            return ("La nube no acepta inicio de sesión de prueba. Para una nube real falta conectar el inicio de sesión "
                    "(Entra External ID); ver nube/README.md.")
        if e.code == 403:
            return "El usuario no está dado de alta en la nube o no es administrador."
        if isinstance(detalle, str) and detalle:
            return detalle
        if isinstance(detalle, list) and detalle and isinstance(detalle[0], dict):      # error de validación de la API
            return f"Revisa los datos: {detalle[0].get('msg', 'valor no válido')}."
        return f"La nube respondió con el código {e.code}."

    def pedir(self, metodo, ruta, cuerpo=None, crudo=None, tipo="application/octet-stream"):
        """Con el token vencido o rechazado se pide uno nuevo y se reintenta una vez."""
        try:
            return self._enviar(metodo, ruta, cuerpo, crudo=crudo, tipo=tipo)
        except ErrorNube as e:
            if e.codigo != 401:
                raise
            with self._lock:
                self._token = None
            return self._enviar(metodo, ruta, cuerpo, crudo=crudo, tipo=tipo)

    def probar(self):
        """Comprueba la base, el usuario y el permiso de administrador. Devuelve el resumen de uso."""
        self._enviar("GET", "/salud", con_token=False)
        return self.pedir("GET", "/api/uso")


class AlmacenNube:
    """Mismos métodos de visitas que el Almacen local (SQLite), pero contra la API."""

    def __init__(self, cliente, local):
        self.cliente = cliente
        self.local = local            # para taller y ventas, que siguen en SQLite

    @property
    def ruta(self):
        return self.local.ruta

    def _ejecutar(self, *args, **kwargs):
        return self.local._ejecutar(*args, **kwargs)

    def guardar_lote(self, items):
        """items: [{"visita": {...}, "estado": "nuevo", "demo": False, "captura": False}]. Devuelve el resumen de la API."""
        total = {"insertadas": 0, "actualizadas": 0, "omitidas": []}
        for i in range(0, len(items), TAMANO_LOTE):
            r = self.cliente.pedir("POST", "/api/visitas/lote", {"visitas": items[i:i + TAMANO_LOTE]})
            total["insertadas"] += r["insertadas"]
            total["actualizadas"] += r["actualizadas"]
            total["omitidas"] += r["omitidas"]
        return total

    def guardar_visita(self, visita, demo=False, estado="nuevo", captura=False):
        r = self.guardar_lote([{"visita": visita, "estado": estado, "demo": demo, "captura": captura}])
        if r["omitidas"]:
            raise ErrorNube(f"La nube rechazó la visita: {r['omitidas'][0]['motivo']}", 422)

    def registrar_captura(self, visita):
        """Visita recién capturada en el celular: cuenta como uso del mes."""
        self.guardar_visita(visita, captura=True)

    def listar(self):
        return self.cliente.pedir("GET", "/api/visitas")

    def cambiar_estado(self, id_, estado):
        self.cliente.pedir("POST", "/api/visitas/estado", {"id": id_, "estado": estado})

    def actualizar_datos(self, id_, extra):
        self.cliente.pedir("PATCH", f"/api/visitas/{quote(str(id_), safe='')}/datos", extra)

    def borrar_ejemplos(self):
        self.cliente.pedir("POST", "/api/visitas/borrar-ejemplos", {})

    def borrar_importados(self):
        return self.cliente.pedir("POST", "/api/visitas/borrar-importados", {})["borrados"]

    def citas_activas(self):
        """Asesores con una cita en curso: [{usuario_id, nombre, inicio, ubicacion}]."""
        return self.cliente.pedir("GET", "/api/citas/activas")["citas"]

    def citas_en_curso(self):
        """Lo mismo con la forma que usa el gestor: {id del asesor: {en_cita, inicio, ubicacion}}."""
        return {c["usuario_id"]: {"en_cita": True, "inicio": c["inicio"], "ubicacion": c["ubicacion"], "nombre": c["nombre"]}
                for c in self.citas_activas()}

    def citas_historial(self, limite=200):
        return self.cliente.pedir("GET", f"/api/citas?limite={int(limite)}")["citas"]

    def citas_trayecto(self, id_):
        return self.cliente.pedir("GET", f"/api/citas/{quote(str(id_), safe='')}/trayecto")["puntos"]

    # Usuarios del negocio en la nube (solo los administra su administrador)
    def listar_usuarios(self):
        return self.cliente.pedir("GET", "/api/usuarios")

    def crear_usuario(self, correo, nombre, rol):
        return self.cliente.pedir("POST", "/api/usuarios", {"correo": correo, "nombre": nombre, "rol": rol})

    def actualizar_usuario(self, id_, cambios):
        return self.cliente.pedir("PATCH", f"/api/usuarios/{quote(str(id_), safe='')}", cambios)

    def rotar_enlace_publico(self):
        """Genera un enlace nuevo para los QR de las casas: los QR que ya se imprimieron o se enviaron dejan de abrir.
        Los celulares reciben el nuevo con su configuración."""
        self.cliente.pedir("POST", "/api/publico/rotar", {})

    # Agenda en línea: solicitudes de cita que los clientes mandan desde el enlace o el QR del asesor.
    def solicitud_crear(self, datos):
        self.cliente.pedir("POST", "/api/solicitudes", datos)

    def solicitudes_listar(self):
        return self.cliente.pedir("GET", "/api/solicitudes")["solicitudes"]

    def solicitud_actualizar(self, id_, cambios):
        return self.cliente.pedir("PATCH", f"/api/solicitudes/{quote(str(id_), safe='')}", cambios)

    def solicitud_eliminar(self, id_):
        self.cliente.pedir("DELETE", f"/api/solicitudes/{quote(str(id_), safe='')}")

    def token_publico(self):
        """Token del enlace público del negocio (el mismo de los QR de las casas)."""
        return self.cliente.pedir("GET", "/api/app/config")["negocio"]["token_publico"]

    # Fotos del catálogo: el gestor las guarda en su disco y aquí las deja también en la nube para los celulares.
    def imagenes_sincronizar(self, ids):
        """Devuelve {faltan: [...], borradas: n}: qué fotos de la lista no están en la nube; las que sobran se borran allá."""
        return self.cliente.pedir("POST", "/api/imagenes/sincronizar", {"ids": sorted(ids)})

    def subir_imagen(self, id_, datos):
        self.cliente.pedir("PUT", f"/api/imagen/{quote(str(id_), safe='')}", crudo=datos, tipo="image/jpeg")

    # WhatsApp con IA: la cuenta de Meta del negocio y sus conversaciones viven en la nube (ahí llega el webhook).
    def whatsapp_cuenta(self):
        return self.cliente.pedir("GET", "/api/whatsapp/cuenta")

    def whatsapp_guardar(self, datos):
        return self.cliente.pedir("PUT", "/api/whatsapp/cuenta", datos)

    def whatsapp_desconectar(self):
        return self.cliente.pedir("DELETE", "/api/whatsapp/cuenta")

    def whatsapp_conversaciones(self):
        return self.cliente.pedir("GET", "/api/whatsapp/conversaciones")["conversaciones"]

    def whatsapp_conversacion(self, wa_id):
        return self.cliente.pedir("GET", f"/api/whatsapp/conversaciones/{quote(str(wa_id), safe='')}")

    def whatsapp_pausa(self, wa_id, pausada):
        return self.cliente.pedir("POST", f"/api/whatsapp/conversaciones/{quote(str(wa_id), safe='')}/pausa",
                                  {"pausada": bool(pausada)})

    def whatsapp_enviar(self, wa_id, texto):
        return self.cliente.pedir("POST", f"/api/whatsapp/conversaciones/{quote(str(wa_id), safe='')}/enviar",
                                  {"texto": texto})

    def whatsapp_prueba(self, datos):
        """Mensaje como si lo mandara un cliente: sigue el flujo real en la nube, pero nada sale por WhatsApp."""
        return self.cliente.pedir("POST", "/api/whatsapp/prueba", datos)

    def whatsapp_borrar_prueba(self, wa_id):
        return self.cliente.pedir("DELETE", f"/api/whatsapp/conversaciones/{quote(str(wa_id), safe='')}")

    def whatsapp_ia_estado(self):
        return self.cliente.pedir("GET", "/api/whatsapp/ia")

    def publicar_config(self, config):
        """Publica la configuración del negocio (formulario, propiedades, score) para que los celulares la tomen.
        Los accesos, la IA, el taller y la lista de asesores no se envían."""
        publica = {k: v for k, v in config.items() if k not in CLAVES_NO_PUBLICAS}
        self.cliente.pedir("PUT", "/api/config", publica)


class AlmacenConmutable:
    """Apunta al almacén local o al de la nube. Los servidores guardan esta referencia, así que activar o
    desactivar la nube no obliga a reiniciar nada."""

    def __init__(self, local):
        self.local = local
        self.nube = None

    def usar_nube(self, cliente):
        self.nube = AlmacenNube(cliente, self.local)

    def usar_local(self):
        self.nube = None

    @property
    def en_nube(self):
        return self.nube is not None

    def __getattr__(self, nombre):          # solo se llama cuando el atributo no existe aquí
        return getattr(self.nube or self.local, nombre)
