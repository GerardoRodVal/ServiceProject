"""Prueba de punta a punta: gestor de Windows + celular (por la red local del gestor) + API en Docker.

Arranca un gestor de prueba con una base SQLite temporal, crea un negocio de prueba en la nube, recorre el
flujo completo (carga de archivo, subida de datos, activación de la nube, captura desde el celular, caída y
vuelta de la nube, reinicio) y al terminar borra todo lo que creó.

Requisitos: el entorno de nube encendido (docker compose up -d) y Python 3.
    python nube/integracion/probar_gestor_nube.py
Toca temporalmente captura-visitas-pwa/config.json (agrega un asesor de prueba); lo restaura al terminar.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

RAIZ = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
NUBE_DIR = os.path.join(RAIZ, "nube")
GESTOR_DIR = os.path.join(RAIZ, "gestor-windows")
CONFIG_JSON = os.path.join(RAIZ, "captura-visitas-pwa", "config.json")
EXCEL = os.path.join(RAIZ, "ejemplos", "prospectos_100_leads.xlsx")
API = "http://127.0.0.1:8000"
PANEL, LAN = "http://127.0.0.1:8881", "http://127.0.0.1:8882"

sufijo = uuid.uuid4().hex[:8]
NEGOCIO = f"Integración {sufijo}"
CORREO_ADMIN = f"gestor.{sufijo}@prueba.local"
CORREO_ASESOR, CLAVE_ASESOR = f"asesora.{sufijo}@prueba.local", "ClavePrueba123"
fallas = []


def http(metodo, url, cuerpo=None, cabeceras=None):
    datos = None if cuerpo is None else json.dumps(cuerpo).encode()
    h = {"Content-Type": "application/json", **(cabeceras or {})}
    req = urllib.request.Request(url, data=datos, headers=h, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def crudo(url):
    """GET sin sesión que devuelve (código, bytes): para fotos."""
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def sql(consulta):
    r = subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", "captura_admin", "-d", "captura", "-At", "-c", consulta],
                       cwd=NUBE_DIR, capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def nube_n(donde=""):
    return int(sql(f"SELECT count(*) FROM registros WHERE negocio_id = (SELECT id FROM negocios WHERE nombre = '{NEGOCIO}') {donde}"))


def verificar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLA ") + mensaje)
    if not condicion:
        fallas.append(mensaje)


def esperar(url, intentos=40):
    for _ in range(intentos):
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def arrancar_gestor(env, log):
    p = subprocess.Popen([sys.executable, "-B", os.path.join(GESTOR_DIR, "gestor_visitas.py"), "--puerto-panel", "8881", "--sin-navegador"],
                         cwd=GESTOR_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    if not esperar(PANEL + "/api/estado"):
        raise RuntimeError("El gestor de prueba no arrancó.")
    return p


def visita_celular(id_):
    return {"id": id_, "creado": "2026-11-05T15:00:00+00:00", "asesor": "x", "giro": "inmobiliarias", "nombre": "Lead del celular",
            "tel": "5511223344", "propiedad": "modelo-encino", "presupuesto": 2500000, "enganche": 20, "credito": "preaprobado",
            "plazo": "0", "decide": "solo", "notas": "", "score": 90, "cat": "alta", "razones": [{"txt": "Prueba", "pts": 10}]}


def main():
    sql(f"WITH n AS (INSERT INTO negocios (nombre) VALUES ('{NEGOCIO}') RETURNING id) "
        f"INSERT INTO usuarios (negocio_id, correo, rol) SELECT id, '{CORREO_ADMIN}', 'admin' FROM n")
    tmp = tempfile.mkdtemp(prefix="gestor_nube_")
    env = {**os.environ, "CAPTURA_DB": os.path.join(tmp, "visitas.db"), "PYTHONDONTWRITEBYTECODE": "1"}
    with open(CONFIG_JSON, "rb") as f:
        config_original = f.read()
    # Historial de citas de esta PC (citas.jsonl) para la pantalla Citas: una con trayecto, una sin él y una línea dañada.
    with open(os.path.join(tmp, "citas.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"asesor": "Ana Local", "asesor_id": "ana", "inicio": "2026-11-01T10:00:00+00:00", "fin": "2026-11-01T10:30:00+00:00",
                            "duracion_seg": 1800, "ubicacion_final": {"lat": 19.5, "lng": -99.2, "precision": 9, "hora": "2026-11-01T10:30:00+00:00"},
                            "trayecto": [{"lat": 19.4, "lng": -99.1, "precision": 5, "hora": "2026-11-01T10:05:00+00:00"},
                                         {"lat": 19.45, "lng": -99.15, "precision": 6, "hora": "2026-11-01T10:15:00+00:00"},
                                         {"lat": 19.5, "lng": -99.2, "precision": 9, "hora": "2026-11-01T10:30:00+00:00"}]}) + "\n")
        f.write("esto no es json\n")
        f.write(json.dumps({"asesor": "Beto Local", "asesor_id": "beto", "inicio": "2026-11-02T09:00:00+00:00", "fin": "2026-11-02T09:10:00+00:00",
                            "duracion_seg": 600, "ubicacion_final": None, "trayecto": []}) + "\n")
    log = open(os.path.join(tmp, "gestor.log"), "wb")
    gestor = None
    try:
        gestor = arrancar_gestor(env, log)
        cu = {"url": API, "correo": CORREO_ADMIN}
        excel = {"nombre_archivo": "p.xlsx", "contenido": base64.b64encode(open(EXCEL, "rb").read()).decode()}

        print("A. Modo local: cargar el Excel")
        c, r = http("POST", PANEL + "/api/visitas/importar", excel)
        verificar(c == 200 and r["importadas"] == 100, "se importaron 100 prospectos en SQLite")
        c, lista = http("GET", PANEL + "/api/visitas")
        verificar(len(lista) == 100 and nube_n() == 0, "el panel ve 100 y la nube está vacía")
        http("POST", PANEL + "/api/visitas/estado", {"id": lista[0]["id"], "estado": "negociando"})
        id_negociando = lista[0]["id"]

        c, r = http("GET", PANEL + "/api/citas/panel")
        h = r.get("historial", [])
        verificar(c == 200 and r["origen"] == "local" and r["en_curso"] == [] and [x["nombre"] for x in h] == ["Beto Local", "Ana Local"],
                  "pantalla Citas (local): historial de citas.jsonl, la más reciente primero y sin la línea dañada")
        verificar([x["id"] for x in h] == ["L3", "L1"] and [x["puntos"] for x in h] == [0, 3], "los identificadores son el número de línea y cuentan los puntos")
        c, r = http("GET", PANEL + "/api/citas/trayecto?id=L1")
        verificar(c == 200 and [p["lat"] for p in r["puntos"]] == [19.4, 19.45, 19.5] and r["puntos"][0]["hora"], "trayecto local con sus tres puntos")
        verificar(http("GET", PANEL + "/api/citas/trayecto?id=L3")[1] == {"puntos": []}, "una cita sin trayecto devuelve una lista vacía")
        verificar(all(http("GET", PANEL + "/api/citas/trayecto?id=" + i)[0] == 404 for i in ("L999", "L2", "x", "L1%20OR%201", "")), "identificadores que no existen o no son válidos dan 404")

        print("B. Probar la conexión")
        c, r = http("POST", PANEL + "/api/nube/probar", cu)
        verificar(c == 200 and r["uso"]["registros_total"] == 0, "conexión correcta con la API")
        c, r = http("POST", PANEL + "/api/nube/probar", {"url": API, "correo": "nadie@prueba.local"})
        verificar(c == 502 and "no está dado de alta" in r["error"], "un correo que no existe en la nube se rechaza con un mensaje claro")
        c, r = http("POST", PANEL + "/api/nube/probar", {"url": "http://example.com", "correo": CORREO_ADMIN})
        verificar(c == 400 and "https" in r["error"], "una dirección http fuera de esta PC se rechaza")
        c, r = http("POST", PANEL + "/api/nube/probar", {"url": "http://127.0.0.1:9", "correo": CORREO_ADMIN})
        verificar(c == 502 and "No hay conexión" in r["error"], "una API apagada da «No hay conexión»")

        print("C. Subir los datos locales a la nube")
        c, r = http("POST", PANEL + "/api/nube/subir", cu)
        verificar(c == 200 and r["insertadas"] == 100 and r["total_omitidas"] == 0, "se subieron 100 prospectos")
        verificar(nube_n() == 100 and nube_n(f"AND id = '{id_negociando}' AND estado = 'negociando'") == 1, "la nube tiene 100 y conservó el estado")
        c, r = http("POST", PANEL + "/api/nube/subir", cu)
        verificar(r["insertadas"] == 0 and r["actualizadas"] == 100 and nube_n() == 100, "subir otra vez no duplica")

        print("D. Activar la nube")
        c, r = http("POST", PANEL + "/api/nube/guardar", {**cu, "activo": True})
        verificar(c == 200 and r["activa"] is True, "la nube quedó activa")
        c, lista = http("GET", PANEL + "/api/visitas")
        verificar(len(lista) == 100, "el panel ahora lee 100 prospectos de la nube")
        verificar(os.path.exists(os.path.join(tmp, "nube.json")), "la configuración quedó en nube.json (fuera del proyecto)")
        c, r = http("GET", PANEL + "/api/estado")
        verificar(r.get("nube_activa") is True, "/api/estado informa nube_activa")
        negocio_sql = f"(SELECT id FROM negocios WHERE nombre = '{NEGOCIO}')"
        verificar(sql(f"SELECT count(*) FROM configuraciones WHERE negocio_id = {negocio_sql} AND datos ? 'formulario'") == "1",
                  "al activar, la configuración del negocio se publicó para los celulares")
        verificar(sql(f"SELECT count(*) FROM configuraciones WHERE negocio_id = {negocio_sql} AND (datos ? 'acceso' OR datos ? 'asesores' OR datos ? 'ia')") == "0",
                  "la configuración publicada no lleva accesos, asesores ni IA")

        print("D2. Usuarios de la nube desde el panel")
        c, r = http("POST", PANEL + "/api/nube/usuarios", {})
        yo = next((u for u in r.get("usuarios", []) if u["correo"] == CORREO_ADMIN), None)
        verificar(c == 200 and yo is not None and yo["rol"] == "admin", "el panel lista a los usuarios (el administrador configurado)")
        nuevo = f"alta.{sufijo}@prueba.local"
        c, r = http("POST", PANEL + "/api/nube/usuario_crear", {"correo": nuevo, "nombre": "Alta Prueba", "rol": "capturista"})
        verificar(c == 200 and r["usuario"]["correo"] == nuevo, "se da de alta a un asesor")
        verificar(sql(f"SELECT count(*) FROM usuarios WHERE lower(correo) = '{nuevo}' AND rol = 'capturista' AND activo") == "1", "el usuario quedó en la base")
        c, r = http("POST", PANEL + "/api/nube/usuario_crear", {"correo": nuevo.upper()})
        verificar(c == 409 and "ya está registrado" in r["error"], "un correo repetido se rechaza con el motivo")
        c, r = http("POST", PANEL + "/api/nube/usuario_crear", {"correo": "sin-arroba"})
        verificar(c == 422, "un correo inválido se rechaza")
        c, r = http("POST", PANEL + "/api/nube/usuario_actualizar", {"id": yo["id"], "activo": False})
        verificar(c == 409 and "propio acceso" in r["error"], "el administrador no puede darse de baja a sí mismo")
        otro_id = http("POST", PANEL + "/api/nube/usuarios", {})[1]["usuarios"][-1]["id"]
        c, r = http("POST", PANEL + "/api/nube/usuario_actualizar", {"id": otro_id, "activo": False, "rol": "capturista"})
        verificar(c == 200 and r["usuario"]["activo"] is False, "se da de baja a un asesor")
        c, r = http("POST", PANEL + "/api/nube/usuario_actualizar", {"id": str(uuid.uuid4()), "activo": True})
        verificar(c == 404, "un usuario que no existe da 404")

        print("D3. Consejos y fotos")
        sys.path.insert(0, GESTOR_DIR)
        import asesor_app
        import nube as modulo_nube
        a = modulo_nube.AlmacenNube(modulo_nube.ClienteNube(API, CORREO_ADMIN), None)
        muestra = [("Ana", "alta", "nuevo", 90, "no"), ("Beto", "alta", "nuevo", 80, "tramite"), ("Carla", "media", "contactado", 50, "no"),
                   ("Dani", "alta", "contactado", 95, "no")]
        a.guardar_lote([{"visita": {"id": f"cons-{sufijo}-{i}", "creado": "2026-11-04T10:00:00+00:00", "nombre": n, "cat": cat, "score": s,
                                    "credito": cr, "asesor_id": yo["id"]}, "estado": e} for i, (n, cat, e, s, cr) in enumerate(muestra)])
        score = http("GET", PANEL + "/api/config")[1]["score"]
        esperado = asesor_app.consejos(yo["id"], [{"asesor_id": yo["id"], "nombre": n, "cat": cat, "estado": e, "score": s, "credito": cr}
                                                 for n, cat, e, s, cr in muestra], score)
        recibido = a.cliente.pedir("GET", "/api/app/consejos")["consejos"]
        verificar(recibido == esperado and recibido[0].get("urgente") is True,
                  "los consejos de la nube dicen exactamente lo mismo que los del gestor con las mismas visitas")
        sql(f"DELETE FROM registros WHERE negocio_id = {negocio_sql} AND id LIKE 'cons-%'")

        foto = b"\xff\xd8\xff\xe0" + os.urandom(200)
        c, r = http("POST", PANEL + "/api/imagen", {"datos": "data:image/jpeg;base64," + base64.b64encode(foto).decode()})
        id_foto = r["id"]
        c, cfg = http("GET", PANEL + "/api/config")
        cfg["asesores"] = []                       # el gestor de prueba no tiene los correos de los asesores reales: sin ellos no valida
        for p in cfg["propiedades"]:
            p["imagen"] = ""                       # las fotos de verdad del catálogo viven en otra carpeta; aquí solo se prueba una
        cfg["propiedades"][0]["imagen"] = id_foto
        c, r = http("POST", PANEL + "/api/config", cfg)
        verificar(c == 200 and "aviso_nube" not in r, f"guardar la configuración con una foto nueva (nube activa) no da avisos [{c} {str(r)[:300]}]")
        verificar(crudo(API + f"/api/imagen/{id_foto}") == (200, foto), "la foto quedó en la nube y se ve sin sesión (como la pide una etiqueta <img>)")
        fantasma = os.urandom(16).hex()
        cfg["propiedades"][1]["imagen"] = fantasma             # una foto cuyo archivo no está en esta PC
        c, r = http("POST", PANEL + "/api/config", cfg)
        verificar(c == 200 and "1 foto" in r.get("aviso_nube", ""), "una foto que no está en esta PC se avisa sin romper el guardado")
        cfg["propiedades"][1]["imagen"] = ""
        cfg["propiedades"][0]["imagen"] = ""
        http("POST", PANEL + "/api/config", cfg)
        verificar(crudo(API + f"/api/imagen/{id_foto}")[0] == 404, "al quitar la foto del catálogo se borra de la nube")
        verificar(not os.path.exists(os.path.join(tmp, "imagenes_catalogo", id_foto + ".jpg")), "y de esta PC (como ya hacía el gestor)")

        print("E. Operar desde el panel con la nube activa")
        otro = lista[5]["id"]
        c, _ = http("POST", PANEL + "/api/visitas/estado", {"id": otro, "estado": "cerrado"})
        verificar(c == 200 and nube_n(f"AND id = '{otro}' AND estado = 'cerrado'") == 1, "cambiar un estado se guarda en la nube")
        verificar(int(sql(f"SELECT count(*) FROM historial_estados WHERE registro_id = '{otro}'")) == 1, "queda en el historial de estados")
        c, r = http("POST", PANEL + "/api/visitas/importar", excel)
        verificar(c == 200 and r["importadas"] == 100 and nube_n("AND id LIKE 'imp-%'") == 200, "importar con la nube activa envía por lotes (100 más)")
        c, _ = http("POST", PANEL + "/api/visitas/borrar-importados", {})
        verificar(c == 200 and nube_n("AND id LIKE 'imp-%'") == 0, "«Borrar cargados» borra en la nube")
        http("POST", PANEL + "/api/visitas/ejemplo", {"accion": "cargar"})
        verificar(nube_n("AND demo") == 28, "los datos de ejemplo se guardan marcados como ejemplo")
        http("POST", PANEL + "/api/visitas/ejemplo", {"accion": "quitar"})
        verificar(nube_n("AND demo") == 0, "quitar ejemplos los borra de la nube")
        sys.path.insert(0, GESTOR_DIR)
        import nube as modulo_nube
        a = modulo_nube.AlmacenNube(modulo_nube.ClienteNube(API, CORREO_ADMIN), None)
        v_nueva = visita_celular(f"kommo-{sufijo}")
        a.guardar_visita(v_nueva, estado="nuevo")
        a.actualizar_datos(v_nueva["id"], {"kommo_id": 4821, "kommo_url": "https://miempresa.kommo.com/leads/detail/4821"})
        verificar(nube_n(f"AND id = 'kommo-{sufijo}' AND (datos->>'kommo_id')::int = 4821") == 1, "el enlace de Kommo se agrega sin tocar lo demás")

        print("E2. Citas en curso")
        c, r = http("GET", PANEL + "/api/citas")
        verificar(c == 200 and r == {}, "sin citas en la nube, /api/citas del gestor está vacío")
        a.cliente.pedir("POST", "/api/app/cita", {"accion": "iniciar"})
        a.cliente.pedir("POST", "/api/app/ubicacion", {"lat": 19.43, "lng": -99.13, "precision": 10})
        c, r = http("GET", PANEL + "/api/citas")
        verificar(c == 200 and len(r) == 1 and next(iter(r.values()))["ubicacion"]["lat"] == 19.43, "con la nube activa, /api/citas del gestor muestra las citas de la nube")
        c, r = http("GET", PANEL + "/api/citas/panel")
        verificar(c == 200 and r["origen"] == "nube" and len(r["en_curso"]) == 1 and r["en_curso"][0]["nombre"] and r["historial"] == [],
                  "pantalla Citas (nube): quién está en cita, y el historial de la nube (no el de citas.jsonl)")
        a.cliente.pedir("POST", "/api/app/cita", {"accion": "terminar"})
        verificar(sql(f"SELECT count(*) FROM citas WHERE negocio_id = {negocio_sql} AND fin IS NOT NULL") == "1", "la cita terminada quedó en la base")
        c, r = http("GET", PANEL + "/api/citas/panel")
        verificar(r["en_curso"] == [] and len(r["historial"]) == 1 and r["historial"][0]["puntos"] == 1, "la cita terminada pasa al historial de la nube")
        c, r2 = http("GET", PANEL + "/api/citas/trayecto?id=" + r["historial"][0]["id"])
        verificar(c == 200 and r2["puntos"][0]["lat"] == 19.43, "trayecto de la nube")
        verificar(http("GET", PANEL + "/api/citas/trayecto?id=" + str(uuid.uuid4()))[0] == 404 and http("GET", PANEL + "/api/citas/trayecto?id=L1")[0] == 404,
                  "en la nube, una cita que no existe (o un id local) da 404")

        print("E3. Enlace de los QR de las casas")
        c, cfg_qr = http("GET", PANEL + "/api/config")
        casa = cfg_qr["propiedades"][0]["id"]
        token_antes = sql(f"SELECT token_publico FROM negocios WHERE nombre = '{NEGOCIO}'")
        verificar(http("GET", API + f"/api/publico/{token_antes}/casa/{casa}")[0] == 200, "la casa publicada por el gestor se ve con el enlace actual")
        c, r = http("POST", PANEL + "/api/nube/enlace_rotar", {})
        token_despues = sql(f"SELECT token_publico FROM negocios WHERE nombre = '{NEGOCIO}'")
        verificar(c == 200 and r == {"ok": True} and token_despues != token_antes, "el panel cambia el enlace (y no lo devuelve)")
        verificar(http("GET", API + f"/api/publico/{token_antes}/casa/{casa}")[0] == 404, "el QR anterior deja de abrir")
        verificar(http("GET", API + f"/api/publico/{token_despues}/casa/{casa}")[0] == 200, "el enlace nuevo abre la misma casa")

        print("F. Captura desde el celular (reenviada por el gestor)")
        c, cfg = http("GET", PANEL + "/api/config")
        cfg["asesores"] = [{"id": "nuevo-1", "nombre": "Asesora Prueba", "correo": CORREO_ASESOR, "telefono": "", "activo": True,
                            "password_nueva": CLAVE_ASESOR}]
        c, r = http("POST", PANEL + "/api/config", cfg)
        verificar(c == 200, f"se dio de alta a un asesor de prueba ({r if c != 200 else 'ok'})")
        c, r = http("POST", PANEL + "/api/servidor", {"accion": "iniciar", "puerto": 8882})
        verificar(c == 200, "servidor del celular iniciado")
        c, _ = http("GET", LAN + "/app-config.json")
        verificar(c == 404, "el gestor no publica /app-config.json: la app del celular abierta desde él sigue en modo clásico")
        cap = {"X-Captura": "1"}
        c, sesion = http("POST", LAN + "/api/app/login", {"correo": CORREO_ASESOR, "password": CLAVE_ASESOR}, cap)
        verificar(c == 200 and sesion.get("token"), "el asesor inicia sesión en la app")
        auth = {**cap, "Authorization": f"Bearer {sesion.get('token')}"}
        v = visita_celular(f"cel-{sufijo}-1")
        c, _ = http("POST", LAN + "/api/visitas", v, auth)
        verificar(c == 200 and nube_n(f"AND id = '{v['id']}' AND datos->>'asesor_id' = '{sesion['id']}'") == 1, "la visita llega a la nube con el asesor de la sesión")
        c, _ = http("POST", LAN + "/api/visitas", v, auth)
        usados = int(sql(f"SELECT registros FROM uso_mensual WHERE negocio_id = (SELECT id FROM negocios WHERE nombre = '{NEGOCIO}')"))
        verificar(c == 200 and nube_n(f"AND id = '{v['id']}'") == 1 and usados == 1, "reenviarla no la duplica ni suma al uso (uso del mes = 1)")

        print("G. La nube se cae y vuelve")
        subprocess.run(["docker", "compose", "stop", "api"], cwd=NUBE_DIR, capture_output=True)
        v2 = visita_celular(f"cel-{sufijo}-2")
        c, r = http("POST", LAN + "/api/visitas", v2, auth)
        verificar(c == 503 and "nube" in r.get("error", "").lower(), "con la nube caída el celular recibe 503 (conserva la visita y reintenta)")
        c, r = http("GET", PANEL + "/api/visitas")
        verificar(c == 502 and r.get("nube") is True, "el panel recibe 502 con la marca «nube» (conserva los últimos datos)")
        subprocess.run(["docker", "compose", "start", "api"], cwd=NUBE_DIR, capture_output=True)
        verificar(esperar(API + "/salud", 60), "la API volvió")
        c, _ = http("POST", LAN + "/api/visitas", v2, auth)
        verificar(c == 200 and nube_n(f"AND id = '{v2['id']}'") == 1, "el reintento del celular llega y queda una sola vez")

        print("H. Reiniciar el gestor")
        http("POST", PANEL + "/api/servidor", {"accion": "detener"})
        gestor.terminate()
        gestor.wait(timeout=15)
        gestor = arrancar_gestor(env, log)
        c, r = http("GET", PANEL + "/api/estado")
        c2, lista = http("GET", PANEL + "/api/visitas")
        verificar(r["nube_activa"] is True and len(lista) == nube_n(), "al reiniciar sigue en la nube y lee lo mismo que hay en ella")

        print("I. Desactivar la nube")
        c, r = http("POST", PANEL + "/api/nube/guardar", {**cu, "activo": False})
        c2, lista = http("GET", PANEL + "/api/visitas")
        c, r2 = http("POST", PANEL + "/api/nube/usuarios", {})
        verificar(c == 409 and "Activa la nube" in r2["error"], "con la nube desactivada no se gestionan usuarios")
        c, r2 = http("POST", PANEL + "/api/nube/enlace_rotar", {})
        verificar(c == 409 and "Activa la nube" in r2["error"], "con la nube desactivada tampoco se cambia el enlace de los QR")
        c, _ = http("POST", PANEL + "/api/nube/enlace_rotar", None, {"Content-Type": "text/plain"})
        verificar(c == 415, "el cambio de enlace solo acepta solicitudes JSON del panel")
        verificar(r["activa"] is False and len(lista) == 100 and all(not x["id"].startswith(("cel-", "kommo-")) for x in lista), "vuelve a leer SQLite local (100) y la nube no cambia")
        # En la nube quedan: el de Kommo (1) y los dos del celular (2).
        verificar(nube_n() == 3, "la nube conserva sus 3 registros")

        print("J. Citas de esta PC con la nube desactivada")
        http("POST", PANEL + "/api/servidor", {"accion": "iniciar", "puerto": 8882})
        c, sesion = http("POST", LAN + "/api/app/login", {"correo": CORREO_ASESOR, "password": CLAVE_ASESOR}, cap)
        auth = {**cap, "Authorization": f"Bearer {sesion.get('token')}"}
        http("POST", LAN + "/api/app/cita", {"accion": "iniciar"}, auth)
        c, r = http("GET", PANEL + "/api/citas/panel")
        verificar(c == 200 and r["origen"] == "local" and [x["nombre"] for x in r["en_curso"]] == ["Asesora Prueba"], "pantalla Citas (local): quién está en cita ahora, con el nombre del asesor")
        http("POST", LAN + "/api/app/ubicacion", {"lat": 20.1, "lng": -100.2, "precision": 7}, auth)
        http("POST", LAN + "/api/app/cita", {"accion": "terminar"}, auth)
        c, r = http("GET", PANEL + "/api/citas/panel")
        nueva = r["historial"][0]
        verificar(r["en_curso"] == [] and nueva["nombre"] == "Asesora Prueba" and nueva["puntos"] == 1 and len(r["historial"]) == 3, "al terminar, la cita queda en el historial local (citas.jsonl)")
        verificar(http("GET", PANEL + "/api/citas/trayecto?id=" + nueva["id"])[1]["puntos"][0]["lng"] == -100.2, "y su trayecto se puede ver")
    finally:
        if gestor:
            try:
                http("POST", PANEL + "/api/servidor", {"accion": "detener"})
            except Exception:
                pass
            gestor.terminate()
            try:
                gestor.wait(timeout=15)
            except subprocess.TimeoutExpired:
                gestor.kill()
        log.close()
        with open(CONFIG_JSON, "wb") as f:
            f.write(config_original)
        subprocess.run(["docker", "compose", "start", "api"], cwd=NUBE_DIR, capture_output=True)
        sql(f"DELETE FROM negocios WHERE nombre = '{NEGOCIO}'")
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("TODO BIEN" if not fallas else f"{len(fallas)} FALLA(S):\n  - " + "\n  - ".join(fallas))
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
