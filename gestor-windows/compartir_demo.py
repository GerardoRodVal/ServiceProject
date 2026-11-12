"""Comparte una copia DEMO de NearLeads (panel del gestor y app del celular) con enlaces públicos, mientras esta PC esté
encendida y con internet. Sirve para enseñarla a alguien sin que instale nada.

- Datos de ejemplo en %LOCALAPPDATA%\\NearLeads\\demo-compartida: no toca tu gestor real (puerto 8765), tu base de
  visitas, tus clientes ni tu config.json. Cada vez que se inicia, los datos de ejemplo se vuelven a crear.
- Panel: con clave y de solo vista. No se puede guardar, borrar, apagar ni cerrar procesos de esta PC.
- App del celular: funciona completa contra los datos de ejemplo, con un asesor de prueba.
- Enlaces con Cloudflare (cloudflared, túnel rápido sin cuenta, https). Cambian cada vez que se inicia la demo.
- Mientras corre, Windows no se suspende por inactividad (cerrar la tapa o apagar sí lo corta).

Uso:
    python compartir_demo.py             inicia y muestra los enlaces (Ctrl+C o cerrar la ventana para dejar de compartir)
    python compartir_demo.py --detener   detiene una demo iniciada en segundo plano
Los enlaces quedan también en %LOCALAPPDATA%\\NearLeads\\demo-compartida\\enlaces.txt
"""
import ctypes
import functools
import hmac
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(os.environ.get("LOCALAPPDATA") or BASE, "NearLeads", "demo-compartida")
# Cada inicio usa su propia carpeta (Windows a veces retiene archivos de la anterior unos segundos); las viejas se borran.
CORRIDA = os.path.join(DEMO, "corrida-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
DATOS = os.path.join(CORRIDA, "datos")
APP = os.path.join(CORRIDA, "app")
PID_PATH = os.path.join(DEMO, "proceso.pid")
ENLACES_PATH = os.path.join(DEMO, "enlaces.txt")
CLAVES_PATH = os.path.join(DEMO, "claves.json")
LOG_TUNEL = os.path.join(DEMO, "tunel-{}.log")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COOKIE = "nl_demo"
SOLO_VISTA = "Versión de demostración: es solo para ver, los cambios no se guardan."


def _proceso_python_vivo(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
    return "python" in r.stdout.lower()


def detener(silencioso=False):
    """Detiene la demo anterior (y sus túneles) si sigue en marcha."""
    try:
        with open(PID_PATH, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        pid = None
    if pid and pid != os.getpid() and _proceso_python_vivo(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        if not silencioso:
            print("Demo detenida: los enlaces ya no abren.")
    elif not silencioso:
        print("No hay una demo compartida en marcha.")
    try:
        os.remove(PID_PATH)
    except OSError:
        pass


def cloudflared():
    for ruta in (shutil.which("cloudflared"), r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                 r"C:\Program Files\cloudflared\cloudflared.exe"):
        if ruta and os.path.exists(ruta):
            return ruta
    sys.exit("No encuentro cloudflared. Instálalo con:  winget install --id Cloudflare.cloudflared -e")


def claves():
    """Clave del panel y asesor de prueba: se conservan entre inicios para no tener que volver a compartirlas."""
    try:
        with open(CLAVES_PATH, encoding="utf-8") as f:
            c = json.load(f)
        if all(isinstance(c.get(k), str) and c[k] for k in ("panel", "correo", "password")):
            return c
    except (OSError, ValueError):
        pass
    c = {"panel": secrets.token_urlsafe(12), "correo": "asesor.demo@example.com", "password": secrets.token_urlsafe(6)}
    os.makedirs(DEMO, exist_ok=True)
    with open(CLAVES_PATH, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    return c


# ---------------------------------------------------------------- copia demo del gestor

def preparar_gestor(c):
    """Importa el gestor apuntando todo (base, accesos, citas, imágenes, config y app) a la carpeta demo."""
    for nombre in os.listdir(DEMO) if os.path.isdir(DEMO) else []:
        if nombre.startswith("corrida-") or nombre in ("datos", "app"):
            shutil.rmtree(os.path.join(DEMO, nombre), ignore_errors=True)
    os.makedirs(DATOS)
    os.environ["CAPTURA_DB"] = os.path.join(DATOS, "visitas.db")
    shutil.copytree(os.path.normpath(os.path.join(BASE, "..", "captura-visitas-pwa")), APP)
    sys.path.insert(0, BASE)
    import gestor_visitas as g
    g.APP_DIR = APP
    g.CONFIG_PATH = os.path.join(APP, "config.json")
    assert os.path.dirname(g.DB_PATH) == DATOS and os.path.dirname(g.ACCESO_PATH) == DATOS, "la demo debe quedar aislada"

    cfg = g.config_defecto()
    cfg["negocio"].update({"nombre": "Inmobiliaria Demo", "tipo": "desarrollo"})
    cfg["zonas"] = [
        {"id": "el-pedregal", "nombre": "El Pedregal", "tipo": "desarrollo", "color": "#3CC4A7"},
        {"id": "lomas-del-valle", "nombre": "Lomas del Valle", "tipo": "desarrollo", "color": "#5B8CFF"},
    ]
    cfg["propiedades"] = [
        {"id": "modelo-encino", "nombre": "Modelo Encino", "precio": 1890000, "zona": "el-pedregal", "estatus": "disponible"},
        {"id": "modelo-roble", "nombre": "Modelo Roble", "precio": 2480000, "zona": "el-pedregal", "estatus": "apartado"},
        {"id": "modelo-cedro", "nombre": "Modelo Cedro", "precio": 3650000, "zona": "el-pedregal", "estatus": "vendido"},
        {"id": "casa-alamo", "nombre": "Casa Álamo", "precio": 4200000, "zona": "lomas-del-valle", "estatus": "disponible"},
        {"id": "casa-olivo", "nombre": "Casa Olivo", "precio": 5350000, "zona": "lomas-del-valle", "estatus": "disponible"},
    ]
    asesores = [("laura-gomez", "Laura Gómez"), ("carlos-ruiz", "Carlos Ruiz"), ("diana-torres", "Diana Torres")]
    cfg["asesores"] = [{"id": i, "nombre": n, "activo": True} for i, n in asesores]
    cfg["cita_publica"] = dict(g.CITA_PUBLICA_DEFECTO)
    g.guardar_config(cfg)
    # Solo Laura entra a la app con el asesor de prueba; los otros dos tienen correo pero no contraseña.
    g.guardar_acceso({
        "laura-gomez": {"correo": c["correo"], "telefono": "", "hash": g.hash_password(c["password"])},
        "carlos-ruiz": {"correo": "carlos.demo@example.com", "telefono": "", "hash": ""},
        "diana-torres": {"correo": "diana.demo@example.com", "telefono": "", "hash": ""},
    })

    manager = g.Manager()
    g.generar_ejemplos(g.cargar_config(), manager.almacen, 34)
    sembrar_citas(g, manager)
    return g, manager


def sembrar_citas(g, manager):
    hoy = datetime.now().replace(minute=0, second=0, microsecond=0)

    def iso(dias, hora, minuto=0):
        return (hoy + timedelta(days=dias)).replace(hour=hora, minute=minuto).astimezone(timezone.utc).isoformat()

    almacen = manager.almacen
    for nombre, tel, casa, zona, asesor, cuando, nota in [
        ("Mariana Soto", "4495550101", "modelo-roble", "el-pedregal", "laura-gomez", iso(1, 10, 30), "Viene con su pareja"),
        ("Jorge Pérez", "4495550102", "modelo-encino", "el-pedregal", "carlos-ruiz", iso(1, 16), ""),
        ("Sofía Navarro", "4495550103", "casa-alamo", "lomas-del-valle", "diana-torres", iso(2, 18), "Prefiere después del trabajo"),
        ("Diego Fuentes", "4495550104", "casa-olivo", "lomas-del-valle", "carlos-ruiz", iso(3, 12), ""),
    ]:
        almacen.solicitud_crear({"nombre": nombre, "tel": tel, "zona": zona, "casa": casa, "fecha": "", "horario": "", "asesor": asesor})
        sid = next(s["id"] for s in almacen.solicitudes_listar() if s["tel"] == tel)
        almacen.solicitud_actualizar(sid, {"estado": "confirmada", "confirmada_para": cuando, "nota": nota})
    for nombre, tel, casa, zona, dias, hora in [("Paola Ortega", "4495550106", "modelo-encino", "el-pedregal", 2, "11:00"),
                                                 ("Iván Morales", "4495550107", "casa-olivo", "lomas-del-valle", 4, "17:00")]:
        almacen.solicitud_crear({"nombre": nombre, "tel": tel, "zona": zona, "casa": casa, "asesor": "",
                                 "fecha": (hoy + timedelta(days=dias)).strftime("%Y-%m-%d"), "horario": hora})
    # Citas terminadas, con un trayecto corto (centro de Aguascalientes).
    for dias, hora, minutos, asesor_id, nombre in [(1, 11, 42, "laura-gomez", "Laura Gómez"), (2, 17, 35, "carlos-ruiz", "Carlos Ruiz"),
                                                   (3, 10, 55, "diana-torres", "Diana Torres"), (5, 13, 28, "laura-gomez", "Laura Gómez")]:
        inicio = (hoy - timedelta(days=dias)).replace(hour=hora).astimezone(timezone.utc)
        puntos = [{"hora": (inicio + timedelta(minutes=5 * k)).isoformat(), "lat": 21.8818 + 0.0009 * k, "lng": -102.2916 + 0.0007 * k,
                   "precision": 12} for k in range(minutos // 5)]
        g.guardar_resumen_cita(nombre, {"asesor_id": asesor_id, "inicio": inicio.isoformat(),
                                        "fin": (inicio + timedelta(minutes=minutos)).isoformat(), "duracion_seg": minutos * 60,
                                        "ubicacion_final": puntos[-1], "trayecto": puntos})


# ---------------------------------------------------------------- puerta del panel: clave y solo vista

PAGINA_CLAVE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>NearLeads · demo</title><style>
:root{color-scheme:dark}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0B1118;color:#E8EDF2;
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}form{width:min(360px,100%);display:grid;gap:12px;padding:28px;
border-radius:18px;background:#151D27;border:1px solid #243041}h1{margin:0;font-size:20px}p{margin:0;color:#93A1B2;font-size:13px}
input{padding:12px 14px;border-radius:11px;border:1px solid #243041;background:#0B1118;color:inherit;font:inherit}
button{padding:12px;border:0;border-radius:11px;background:#5B8CFF;color:#06101F;font:inherit;font-weight:800;cursor:pointer}
.error{color:#F08A80}</style></head><body><form method="get" action="/"><p>NearLeads · demostración</p><h1>Escribe la clave</h1>
__ERROR__<input name="clave" type="password" autocomplete="off" aria-label="Clave" autofocus required><button>Entrar</button></form></body></html>"""

CINTA = """<div style="position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:60;padding:7px 14px;border-radius:999px;
background:rgba(91,140,255,.16);border:1px solid rgba(91,140,255,.5);color:#C9D8FF;font:700 12px/1.2 system-ui,sans-serif;
backdrop-filter:blur(6px);pointer-events:none">Demo · solo vista: los cambios no se guardan</div></body>"""


def crear_puerta(puerto_panel, clave):
    class Puerta(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _enviar(self, codigo, cuerpo, tipo, extra=()):
            data = cuerpo.encode("utf-8") if isinstance(cuerpo, str) else cuerpo
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _autorizado(self):
            try:
                valor = SimpleCookie(self.headers.get("Cookie") or "").get(COOKIE)
            except Exception:
                return False
            return valor is not None and hmac.compare_digest(valor.value.encode(), clave.encode())

        def _json(self, codigo, cuerpo):
            self._enviar(codigo, json.dumps(cuerpo, ensure_ascii=False), "application/json; charset=utf-8")

        def do_GET(self):
            partes = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(partes.query)
            if "clave" in q:
                if hmac.compare_digest(q["clave"][0].encode(), clave.encode()):
                    return self._enviar(302, "", "text/plain", [
                        ("Location", partes.path or "/"),
                        ("Set-Cookie", f"{COOKIE}={clave}; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax")])
                return self._enviar(401, PAGINA_CLAVE.replace("__ERROR__", '<p class="error">Clave incorrecta.</p>'), "text/html; charset=utf-8")
            if not self._autorizado():
                if partes.path.startswith("/api/"):
                    return self._json(401, {"error": "Abre de nuevo el enlace de la demo."})
                return self._enviar(401, PAGINA_CLAVE.replace("__ERROR__", ""), "text/html; charset=utf-8")
            self._reenviar()

        do_HEAD = do_GET

        def _bloquear(self):
            # Todo lo que cambia algo (guardar, borrar, apagar, cerrar procesos, conectar servicios) se rechaza.
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= 10 * 1024 * 1024:
                self.rfile.read(n)
            if not self._autorizado():
                return self._json(401, {"error": "Abre de nuevo el enlace de la demo."})
            self._json(403, {"error": SOLO_VISTA, "errores": [SOLO_VISTA]})

        do_POST = do_PUT = do_PATCH = do_DELETE = _bloquear

        def _reenviar(self):
            encabezados = {k: v for k, v in self.headers.items()
                           if k.lower() not in ("host", "cookie", "connection", "accept-encoding", "x-forwarded-for")}
            encabezados["Host"] = f"127.0.0.1:{puerto_panel}"
            try:
                con = http.client.HTTPConnection("127.0.0.1", puerto_panel, timeout=60)
                con.request(self.command, self.path, headers=encabezados)
                r = con.getresponse()
                data = r.read()
                tipo = r.getheader("Content-Type") or "application/octet-stream"
                con.close()
            except OSError:
                return self._json(502, {"error": "La demo no responde."})
            if tipo.startswith("text/html") and self.path.split("?")[0] in ("/", "/index.html"):
                data = data.replace(b"</body>", CINTA.encode("utf-8"), 1)
            self._enviar(r.status, data, tipo)

    return Puerta


# ---------------------------------------------------------------- túneles de Cloudflare

def abrir_tunel(exe, puerto, nombre):
    log = LOG_TUNEL.format(nombre)
    with open(log, "w", encoding="utf-8") as salida:
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{puerto}"],
                                stdout=salida, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
    limite = time.time() + 60
    while time.time() < limite:
        if proc.poll() is not None:
            break
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", f.read())
        except OSError:
            m = None
        if m:
            return proc, m.group(0)
        time.sleep(0.5)
    proc.kill()
    sys.exit(f"Cloudflare no dio un enlace para {nombre}. Revisa tu internet; detalle en {log}")


def esperar_enlace(url, intentos=40):
    """El enlace tarda unos segundos en responder desde internet después de crearse."""
    for _ in range(intentos):
        try:
            con = http.client.HTTPSConnection(urllib.parse.urlsplit(url).hostname, timeout=8)
            con.request("HEAD", "/")
            if con.getresponse().status < 500:
                return True
        except OSError:
            pass
        time.sleep(1.5)
    return False


def main():
    if "--detener" in sys.argv:
        return detener()
    detener(silencioso=True)          # una demo a la vez: si había otra, se cierra
    exe = cloudflared()
    c = claves()
    print("Preparando la copia demo…", flush=True)
    g, manager = preparar_gestor(c)
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    g.Handler.manager = manager
    panel = ThreadingHTTPServer(("127.0.0.1", 0), g.Handler)
    lan = type("Lan", (g.LanHandler,), {"almacen": manager.almacen, "taller": manager.taller})
    app = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(lan, directory=APP))
    puerta = ThreadingHTTPServer(("127.0.0.1", 0), crear_puerta(panel.server_address[1], c["panel"]))
    manager.servidor.httpd, manager.servidor.puerto = app, app.server_address[1]   # el panel lo ve como «servidor activo»
    for s in (panel, app, puerta):
        threading.Thread(target=s.serve_forever, daemon=True).start()

    print("Abriendo los enlaces con Cloudflare…", flush=True)
    t_panel, url_panel = abrir_tunel(exe, puerta.server_address[1], "panel")
    t_app, url_app = abrir_tunel(exe, app.server_address[1], "celular")

    # Conexión y el QR del panel muestran la dirección pública de la app, y nada de esta PC.
    estado_original, url_celular = manager.estado, f"{url_app}/index.html"
    def estado():
        e = estado_original()
        e["servidor"]["url"] = url_celular
        e["almacen"] = "Copia de demostración con datos de ejemplo"
        return e
    manager.estado = estado
    manager.procesos = lambda: {"procesos": []}
    cfg = g.cargar_config()
    cfg["acceso"]["url_celular"] = url_celular
    g.guardar_config(cfg)

    # Mientras la demo corre, Windows no se suspende por inactividad.
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except (AttributeError, OSError):
        pass

    listos = esperar_enlace(url_panel) and esperar_enlace(url_app)
    texto = f"""NearLeads · demo compartida (iniciada el {datetime.now():%d/%m/%Y a las %H:%M})

Panel del gestor (solo vista, la clave ya va en el enlace):
  {url_panel}/?clave={c['panel']}

App del celular (captura de visitas; ábrela en el celular o en la PC):
  {url_app}/
  Asesor de prueba: {c['correo']}  ·  contraseña: {c['password']}

Página para que un cliente agende su cita:
  {url_app}/agendar.html

Los enlaces funcionan mientras esta PC esté encendida y con internet. Cambian cada vez que se inicia la demo.
Todo usa datos de ejemplo: tu gestor real y tus clientes no se comparten.
Para dejar de compartir: cierra la ventana de la demo, o ejecuta  python compartir_demo.py --detener
"""
    with open(ENLACES_PATH, "w", encoding="utf-8") as f:
        f.write(texto)
    print("\n" + texto, flush=True)
    if not listos:
        print("Aviso: Cloudflare aún no responde en los enlaces; si no abren, espera un minuto.", flush=True)

    try:
        while t_panel.poll() is None and t_app.poll() is None:
            time.sleep(5)
        print("Se cerró un túnel de Cloudflare: inicia la demo de nuevo para tener enlaces nuevos.", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for t in (t_panel, t_app):
            if t.poll() is None:
                t.kill()
        try:
            os.remove(PID_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
