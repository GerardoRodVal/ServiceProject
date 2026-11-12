# Contexto de construcción: la carpeta del proyecto (service_project), no esta carpeta; ver docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY nube/api/requirements.txt nube/api/requirements-dev.txt ./
# DEV=1 agrega pytest y httpx (pruebas). La imagen de producción se construye con DEV=0.
ARG DEV=0
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$DEV" = "1" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

COPY nube/api/app ./app
COPY nube/api/herramientas ./herramientas
COPY nube/api/tests ./tests

# La app del celular: solo los archivos que la API sirve (ver app/web.py). config.json y las páginas del taller
# o del cliente final no entran a la imagen.
COPY captura-visitas-pwa/index.html captura-visitas-pwa/inicio.html captura-visitas-pwa/cliente.html captura-visitas-pwa/sw.js captura-visitas-pwa/manifest.webmanifest \
     captura-visitas-pwa/icon.svg captura-visitas-pwa/icon-192.png captura-visitas-pwa/icon-512.png \
     captura-visitas-pwa/apple-touch-icon.png ./pwa/
COPY captura-visitas-pwa/vendor/qrcode.js ./pwa/vendor/qrcode.js
ENV PWA_DIR=/srv/pwa

# /idp: donde el proveedor OIDC de desarrollo guarda su clave de firma (volumen); debe ser escribible por el usuario app.
RUN useradd --system --uid 10001 app && mkdir /idp && chown -R app /srv /idp
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
