"""La API también sirve la app del celular (PWA): mismo origen, así no hace falta CORS y en Azure basta con HTTPS.

Solo se sirven los archivos de la app listados aquí. config.json (el del gestor de un negocio) y las páginas del
taller o del cliente final NO se sirven: la configuración de cada negocio sale de /api/app/config con sesión.
"""
import os

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

ARCHIVOS = {
    "index.html": "text/html; charset=utf-8",
    "inicio.html": "text/html; charset=utf-8",           # página de presentación de NearLeads (también en /inicio)
    "cliente.html": "text/html; charset=utf-8",          # vista del cliente: la abre el QR de cada casa (ver publico.py)
    "agendar.html": "text/html; charset=utf-8",          # agenda en línea: el cliente pide su cita (ver solicitudes.py)
    "sw.js": "text/javascript; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json",
    "icon.svg": "image/svg+xml",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "vendor/qrcode.js": "text/javascript; charset=utf-8",
}
CABECERAS = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}


def _enviar(ruta, tipo, extra=None):
    return FileResponse(ruta, media_type=tipo, headers={**CABECERAS, **(extra or {})})


def registrar(app):
    """Se lee del entorno al crear la aplicación: PWA_DIR (carpeta con la app) y PWA_OIDC_* (cómo inicia sesión)."""
    carpeta = os.environ.get("PWA_DIR", "").strip()
    if carpeta and os.path.isdir(carpeta):
        for nombre, tipo in ARCHIVOS.items():
            ruta = os.path.join(carpeta, nombre)
            if not os.path.isfile(ruta):
                continue
            extra = {"Service-Worker-Allowed": "/"} if nombre == "sw.js" else None
            if nombre in ("cliente.html", "agendar.html"):
                # El enlace lleva el token del negocio: que no viaje en el encabezado Referer a otros sitios (fuentes, etc.).
                extra = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
            app.add_api_route(f"/{nombre}", lambda ruta=ruta, tipo=tipo, extra=extra: _enviar(ruta, tipo, extra),
                              methods=["GET"], include_in_schema=False)
            if nombre == "index.html":
                app.add_api_route("/", lambda ruta=ruta, tipo=tipo: _enviar(ruta, tipo), methods=["GET"], include_in_schema=False)
            if nombre == "inicio.html":
                app.add_api_route("/inicio", lambda ruta=ruta, tipo=tipo: _enviar(ruta, tipo), methods=["GET"], include_in_schema=False)

    @app.get("/app-config.json", include_in_schema=False)
    def app_config():
        """La app del celular lo pide al abrir: si existe, trabaja directo con la nube y sabe dónde iniciar sesión."""
        client_id = os.environ.get("PWA_OIDC_CLIENT_ID", "").strip()
        emisor = (os.environ.get("PWA_OIDC_EMISOR") or os.environ.get("OIDC_EMISOR") or "").strip()
        if not (client_id and emisor):
            raise HTTPException(404, "Este servidor no está configurado para el celular.")
        scope = os.environ.get("PWA_OIDC_SCOPE", "openid profile email offline_access").strip()
        return JSONResponse({"modo": "nube", "oidc": {"emisor": emisor.rstrip("/"), "client_id": client_id, "scope": scope}},
                            headers={"Cache-Control": "no-store"})
