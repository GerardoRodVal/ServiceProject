"""Copia del gestor de captura lo que usa la edición Inmobiliarias: el panel (adaptado) y la app del celular.

    python herramientas/extraer.py

Genera app/panel.html y app/pwa/. La app del celular se copia sin las páginas del taller (taller.html, seguimiento.html)
ni config.json: en esta edición la configuración se arma desde la base en cada petición.

El panel del gestor (gestor-windows/panel.html) sirve a todos los giros y puede conectarse a la nube. Esta edición:
- abre directo en el giro Inmobiliarias (sin la cuadrícula de giros ni «Todos los giros»);
- quita las pestañas de venta, taller y Meta AI;
- oculta la nube, WhatsApp, Kommo, la base de datos simulada, el plan de la cuenta y el destino externo de visitas;
- dice que los datos viven en el PostgreSQL de esta PC;
- pone «Salir» en la barra superior (el menú lateral no se ve dentro de un giro).

Cada reemplazo exige encontrar su texto exactamente una vez: si el panel del gestor cambió, el script se detiene y dice
cuál falta, en lugar de generar un panel a medias.
"""
import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGEN = os.path.join(RAIZ, "..", "gestor-windows", "panel.html")
DESTINO = os.path.join(RAIZ, "app", "panel.html")
PWA_ORIGEN = os.path.join(RAIZ, "..", "captura-visitas-pwa")
PWA_DESTINO = os.path.join(RAIZ, "app", "pwa")
PWA_FUERA = {"taller.html", "seguimiento.html", "taller.webmanifest", "config.json"}

ESTILO = """<style id="edicion-inmobiliarias">
/* Edición NearLeads Inmobiliarias (independiente, sin nube): piezas del gestor que aquí no aplican. */
#atras, #v-giros, #nubeCard, #metaCard, #planCuenta, #bdSimulada, #sincKommo, #matarDev, #cerrarSesion, #activoWrap,
.crm-sin-nube, .field:has(> #syncUrl), #probar, #probarRes,
#tabs [data-v="metaai"], #v-metaai, #mtVerChat { display: none !important; }
</style>
"""

REEMPLAZOS = [
    ("<title>Gestor nearustech</title>", "<title>NearLeads Inmobiliarias</title>"),
    ("</head>", ESTILO + "</head>"),
    ("let vista = 'giros';", "let vista = 'panel';"),
    ('      <button type="button" data-v="ventas" data-giro="venta">Ventas</button>\n', ""),
    ('      <button type="button" data-v="productos" data-giro="venta">Productos</button>\n', ""),
    ('      <button type="button" data-v="vender" data-giro="venta">Vender</button>\n', ""),
    ('      <button type="button" data-v="metaai" data-giro="inmobiliarias">Meta AI</button>\n', ""),
    ('      <button type="button" data-v="vehiculos" data-giro="autos" hidden>Vehículos</button>\n', ""),
    ('<button class="btn small ghost" id="atras" type="button">← Todos los giros</button>',
     '<button class="btn small ghost" id="atras" type="button">← Todos los giros</button>\n'
     '      <button class="btn small ghost" id="salirApp" type="button" title="Cierra NearLeads Inmobiliarias en esta PC">Salir</button>'),
    ('<button class="salir" id="salir" type="button">Salir del gestor</button>',
     '<button class="salir" id="salir" type="button">Salir</button>'),
    ("Hoy las visitas se guardan en una base SQLite en esta PC, fuera de la carpeta del proyecto. Si indicas una URL, el "
     "celular enviará las visitas allá y esta PC dejará de recibirlas.",
     "Las visitas, el catálogo, las citas y el CRM se guardan en la base de datos PostgreSQL que se instaló en esta PC "
     "(servicio «NearLeads Inmobiliarias - Base de datos»)."),
    ("<small>Destino de las visitas, nube, base de datos simulada y plan de la cuenta</small>",
     "<small>Dónde se guardan las visitas</small>"),
    ('      <div class="card">\n        <p class="eyebrow">Base de datos (simulada)</p>',
     '      <div class="card" id="bdSimulada">\n        <p class="eyebrow">Base de datos (simulada)</p>'),
    ("if (vista === 'config') { cargarIA(); renderConfig(); renderConexiones(); cargarNube().then(() => renderNube()); cargarMeta(); }",
     "if (vista === 'config') { cargarIA(); renderConfig(); renderConexiones(); }"),
    ("  await refrescarEstado();\n  await cargarNube();\n", "  await refrescarEstado();\n"),
    ("'El gestor abierto es de una versión anterior. Ciérralo con «Salir del gestor» y vuelve a abrirlo desde el acceso directo.'",
     "'La aplicación abierta es de una versión anterior. Ciérrala con «Salir» y vuelve a abrirla desde el acceso directo.'"),
    ("<h2>Gestor cerrado</h2>", "<h2>NearLeads Inmobiliarias cerrado</h2>"),
    ("Se enciende solo al abrir el gestor y se apaga al cerrarlo.", "Se enciende solo al abrir la aplicación y se apaga al cerrarla."),
    ("se apagará al cerrar el gestor.", "se apagará al cerrar la aplicación."),
    # Meta AI (WhatsApp con IA) no va en esta edición: fuera de la tabla de permisos y del texto de ayuda.
    ("  metaai: { administrador: 'Todo', asesor: 'Ver conversaciones' },\n", ""),
    ("Conecta el celular y la nube (sección Conexión), configura el chatbot y su modelo de IA local, conecta tu WhatsApp "
     "para Meta AI, revisa el plan de la cuenta y ajusta las preferencias del gestor, como las sugerencias y este tutorial.",
     "Conecta el celular (sección Conexión), configura el chatbot y su modelo de IA local y ajusta las preferencias, "
     "como las sugerencias y este tutorial."),
    ("cargar();\n</script>",
     "$('#salirApp').addEventListener('click', () => $('#salir').click());\ncargar();\n</script>"),
]


def main():
    with open(ORIGEN, encoding="utf-8") as f:
        html = f.read()
    for viejo, nuevo in REEMPLAZOS:
        n = html.count(viejo)
        if n != 1:
            sys.exit(f"El panel del gestor cambió: se esperaba encontrar una vez (hay {n}):\n{viejo[:200]}")
        html = html.replace(viejo, nuevo)
    with open(DESTINO, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"Panel generado: {DESTINO} ({len(html.encode('utf-8')) // 1024} KB)")

    # Se copia encima (OneDrive a veces no deja borrar la carpeta) y luego se quita lo que ya no está en el origen.
    shutil.copytree(PWA_ORIGEN, PWA_DESTINO, dirs_exist_ok=True,
                    ignore=lambda carpeta, nombres: [n for n in nombres if n in PWA_FUERA])
    for carpeta, _, archivos in os.walk(PWA_DESTINO):
        for a in archivos:
            destino = os.path.join(carpeta, a)
            origen = os.path.join(PWA_ORIGEN, os.path.relpath(destino, PWA_DESTINO))
            if a in PWA_FUERA or not os.path.exists(origen):
                os.remove(destino)
    print(f"App del celular copiada: {PWA_DESTINO} ({len(os.listdir(PWA_DESTINO))} elementos)")


if __name__ == "__main__":
    main()
