"""Prueba de humo contra una instancia en marcha (panel en 127.0.0.1).

    python pruebas/prueba_humo.py [puerto_panel]

Recorre lo principal: configuración con zona, inmueble y asesor; visita del celular por la red local (con sesión del
asesor); la visita queda ligada a contacto y oportunidad; mover la etapa cambia el estado de la visita y al revés;
contactos, actividades y tareas; solicitud de cita desde la página pública; fotos en la base; config.json del celular.
Deja datos en la base: úsala contra una base de prueba.
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request

PANEL = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8765}"
fallas = []


def pedir(metodo, url, cuerpo=None, cabeceras=None):
    datos = None if cuerpo is None else json.dumps(cuerpo).encode()
    h = {"Content-Type": "application/json", **(cabeceras or {})}
    req = urllib.request.Request(url, data=datos, method=metodo, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            crudo = r.read()
            return r.status, (json.loads(crudo) if r.headers.get_content_type() == "application/json" else crudo)
    except urllib.error.HTTPError as e:
        cuerpo = e.read()
        try:
            return e.code, json.loads(cuerpo)
        except ValueError:
            return e.code, cuerpo


def revisar(nombre, condicion, detalle=""):
    print(("OK   " if condicion else "FALLA") + f" {nombre}" + (f" — {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallas.append(nombre)


c, estado = pedir("GET", PANEL + "/api/estado")
revisar("estado", c == 200 and estado["almacen"].startswith("PostgreSQL"), estado)

# Foto del catálogo: se guarda en la base.
jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()
c, foto = pedir("POST", PANEL + "/api/imagen", {"datos": "data:image/jpeg;base64," + jpeg})
revisar("subir foto", c == 200 and len(foto.get("id", "")) == 32, foto)
c, crudo = pedir("GET", PANEL + f"/api/imagen/{foto.get('id')}")
revisar("leer foto", c == 200 and crudo.startswith(b"\xff\xd8"))

c, cfg = pedir("GET", PANEL + "/api/config")
revisar("leer configuración", c == 200 and cfg["negocio"]["giro"] == "inmobiliarias", cfg)
cfg["negocio"]["nombre"] = "Inmobiliaria de prueba"
cfg["zonas"] = [{"id": "lomas", "nombre": "Lomas", "tipo": "colonia", "ciudad": "CDMX"}]
cfg["propiedades"] = [{"id": "casa-roble", "nombre": "Casa Roble", "precio": 3500000, "tipo": "casa", "zona": "lomas",
                       "estatus": "disponible", "imagen": foto["id"]}]
cfg["asesores"] = [{"id": "ana", "nombre": "Ana Ruiz", "activo": True, "correo": "ana@ejemplo.com",
                    "password_nueva": "clave-segura-123"}]
c, r = pedir("POST", PANEL + "/api/config", cfg)
revisar("guardar configuración", c == 200 and r.get("ok"), r)
c, cfg2 = pedir("GET", PANEL + "/api/config")
revisar("configuración guardada en tablas",
        cfg2["propiedades"][0]["id"] == "casa-roble" and cfg2["zonas"][0]["id"] == "lomas"
        and cfg2["asesores"][0].get("tiene_password"), cfg2.get("asesores"))

# Servidor del celular en la red local: arranca solo con la app; se detiene y se vuelve a iniciar en un puerto conocido.
c, r = pedir("GET", PANEL + "/api/estado")
revisar("el servidor del celular arrancó solo", c == 200 and r["servidor"]["activo"], r.get("servidor"))
pedir("POST", PANEL + "/api/servidor", {"accion": "detener"})
c, r = pedir("POST", PANEL + "/api/servidor", {"accion": "iniciar", "puerto": 8899})
revisar("iniciar servidor del celular", c == 200 and r["servidor"]["activo"], r)
LAN = "http://127.0.0.1:8899"
c, conf = pedir("GET", LAN + "/config.json")
revisar("config.json del celular desde la base", c == 200 and conf["propiedades"][0]["id"] == "casa-roble"
        and "hash" not in json.dumps(conf), str(conf)[:200])
c, html = pedir("GET", LAN + "/index.html")
revisar("app del celular", c == 200 and b"<html" in html[:500].lower())
c, r = pedir("GET", LAN + "/taller.html")
revisar("sin páginas del taller", c == 404)

c, sesion = pedir("POST", LAN + "/api/app/login", {"correo": "ana@ejemplo.com", "password": "clave-segura-123"},
                  {"X-Captura": "1"})
revisar("inicio de sesión del asesor", c == 200 and sesion.get("token"), sesion)
token = sesion.get("token", "")
ahora = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
visita = {"id": f"cel-{int(time.time())}", "creado": ahora, "nombre": "Laura Méndez", "tel": "55 1234 5678",
          "propiedad": "casa-roble", "presupuesto": 3600000, "enganche": 20, "credito": "preaprobado", "plazo": "0",
          "decide": "solo", "notas": "Le gustó el jardín", "score": 90, "cat": "alta", "razones": [{"txt": "Presupuesto", "pts": 30}]}
c, r = pedir("POST", LAN + "/api/visitas", visita, {"X-Captura": "1", "Authorization": f"Bearer {token}"})
revisar("visita desde el celular", c == 200 and r.get("ok"), r)

c, visitas = pedir("GET", PANEL + "/api/visitas")
v = next((x for x in visitas if x["id"] == visita["id"]), None)
revisar("visita guardada a nombre del asesor", v and v["asesor"] == "Ana Ruiz" and v["estado"] == "nuevo", v)

c, pipes = pedir("GET", PANEL + "/api/crm/pipelines")
revisar("embudo creado solo", c == 200 and len(pipes["pipelines"]) == 1 and len(pipes["pipelines"][0]["etapas"]) == 5, pipes)
etapas = {e["nombre"]: e["id"] for e in pipes["pipelines"][0]["etapas"]}
c, ops = pedir("GET", PANEL + "/api/crm/oportunidades?limite=500")
op = ops["oportunidades"][0] if ops.get("oportunidades") else {}
revisar("oportunidad ligada a la visita", op.get("valor") == 3500000 and op["datos_giro"].get("score") == 90
        and op["etapa_id"] == etapas["Nuevo"], op)
c, cts = pedir("GET", PANEL + "/api/crm/contactos?limite=200&q=laura")
ct = cts["contactos"][0] if cts.get("contactos") else {}
revisar("contacto con teléfono a 10 dígitos", ct.get("telefono") == "5512345678" and ct.get("responsable_id") == "ana", ct)

# Segunda visita de la misma persona: misma oportunidad.
visita2 = {**visita, "id": visita["id"] + "b", "score": 60, "cat": "media",
           "creado": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 60))}
pedir("POST", LAN + "/api/visitas", visita2, {"X-Captura": "1", "Authorization": f"Bearer {token}"})
c, ops = pedir("GET", PANEL + "/api/crm/oportunidades?limite=500")
revisar("una oportunidad por persona", len(ops["oportunidades"]) == 1 and ops["oportunidades"][0]["datos_giro"]["score"] == 60,
        ops)

c, r = pedir("PATCH", PANEL + f"/api/crm/oportunidades/{op['id']}", {"etapa_id": etapas["Negociando"]})
revisar("mover de etapa", c == 200 and r["oportunidad"]["etapa_id"] == etapas["Negociando"], r)
c, visitas = pedir("GET", PANEL + "/api/visitas")
revisar("la visita toma la etapa", all(x["estado"] == "negociando" for x in visitas if x["id"].startswith(visita["id"])))
c, r = pedir("POST", PANEL + "/api/visitas/estado", {"id": visita["id"], "estado": "cerrado"})
c, f = pedir("GET", PANEL + f"/api/crm/oportunidades/{op['id']}")
revisar("estado de la visita mueve la oportunidad", f["oportunidad"]["etapa_id"] == etapas["Cerrado"]
        and f["oportunidad"]["cerrado_en"], f["oportunidad"])
revisar("historial de etapa", any(h["campo"] == "etapa_id" for h in f["historial"]), f["historial"])

c, r = pedir("POST", PANEL + "/api/crm/contactos", {"nombre": "Jorge Salas", "telefono": "5598765432"})
revisar("alta de contacto", c == 200 and r["contacto"]["nombre"] == "Jorge Salas", r)
jid = r["contacto"]["id"]
c, r = pedir("POST", PANEL + "/api/crm/actividades", {"tipo": "llamada", "contacto_id": jid, "texto": "Pidió informes"})
revisar("actividad", c == 200 and r["actividad"]["tipo"] == "llamada", r)
c, r = pedir("POST", PANEL + "/api/crm/oportunidades", {"contacto_id": jid, "valor": 1200000})
revisar("oportunidad a mano", c == 200 and r["oportunidad"]["etapa_id"] == etapas["Nuevo"], r)
c, r = pedir("POST", PANEL + "/api/crm/tareas", {"titulo": "Llamar a Jorge", "vence": "2020-01-01T18:00:00.000Z",
                                                 "contacto_id": jid})
revisar("tarea", c == 200 and r["tarea"]["estado"] == "pendiente", r)
tid = r["tarea"]["id"]
c, r = pedir("GET", PANEL + "/api/crm/tareas?estado=pendiente&limite=200&vencidas=true")
revisar("tareas vencidas", c == 200 and any(t["id"] == tid for t in r["tareas"]), r)
c, r = pedir("PATCH", PANEL + f"/api/crm/tareas/{tid}", {"estado": "hecha"})
revisar("tarea hecha", c == 200 and r["tarea"]["completada_en"], r)
c, r = pedir("GET", PANEL + f"/api/crm/contactos/{jid}")
revisar("ficha del contacto", c == 200 and len(r["oportunidades"]) == 1 and len(r["actividades"]) == 1, r)
c, r = pedir("PATCH", PANEL + f"/api/crm/contactos/{jid}", {"nombre": ""})
revisar("validación de nombre vacío", c == 422, r)
c, r = pedir("POST", PANEL + "/api/crm/visitas/ligar", {})
revisar("ligar pendientes", c == 200 and r["ligadas"] == 0, r)
c, r = pedir("POST", PANEL + "/api/crm/contactos", {"nombre": "x"}, {"Content-Type": "text/plain"})
revisar("CRM exige JSON", c == 415, r)

# Página pública para agendar (red local).
c, disp = pedir("GET", LAN + "/api/cita/disponibles")
revisar("horas libres para agendar", c == 200 and disp["disponibles"], disp)
if c == 200 and disp["disponibles"]:
    dia = disp["disponibles"][0]
    c, r = pedir("POST", LAN + "/api/cita/solicitud",
                 {"nombre": "Marta Ríos", "metodo": "telefono", "contacto": "5511112222", "tel": "5511112222", "zona": "lomas",
                  "casa": "casa-roble", "fecha": dia["fecha"], "horario": dia["horas"][0]}, {"X-Captura": "1"})
    revisar("solicitud de cita", c == 200, r)
    c, r = pedir("GET", PANEL + "/api/solicitudes")
    revisar("solicitud en el panel", c == 200 and any(s.get("nombre") == "Marta Ríos" for s in r["solicitudes"]), r)

# Cita en campo con trayecto.
pedir("POST", LAN + "/api/app/cita", {"accion": "iniciar"}, {"X-Captura": "1", "Authorization": f"Bearer {token}"})
pedir("POST", LAN + "/api/app/ubicacion", {"lat": 19.4, "lng": -99.1, "precision": 10},
      {"X-Captura": "1", "Authorization": f"Bearer {token}"})
c, r = pedir("POST", LAN + "/api/app/cita", {"accion": "terminar"}, {"X-Captura": "1", "Authorization": f"Bearer {token}"})
c, r = pedir("GET", PANEL + "/api/citas/panel")
revisar("cita terminada en el historial", c == 200 and r["historial"] and r["historial"][0]["nombre"] == "Ana Ruiz", r)

c, r = pedir("POST", PANEL + "/api/visitas/ejemplo", {"accion": "cargar"})
c, ops = pedir("GET", PANEL + "/api/crm/oportunidades?limite=500")
revisar("ejemplos no llenan el embudo", len(ops["oportunidades"]) == 2, len(ops["oportunidades"]))
pedir("POST", PANEL + "/api/visitas/ejemplo", {"accion": "quitar"})
c, r = pedir("GET", PANEL + "/api/nube")
revisar("sin nube", c == 200 and r["activa"] is False)
c, r = pedir("GET", PANEL + "/api/kommo")
revisar("sin Kommo", c == 404)
pedir("POST", PANEL + "/api/servidor", {"accion": "detener"})

print(f"\n{len(fallas)} fallas" + (": " + ", ".join(fallas) if fallas else ""))
sys.exit(1 if fallas else 0)
