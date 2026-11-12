"""Puerta del webhook de WhatsApp para probar con un número real contra la nube LOCAL.

Meta necesita una dirección pública con HTTPS. Un túnel (cloudflared) la da, pero si el túnel apunta directo a la API
local (:8000) queda en internet TODA la API de desarrollo, incluido /dev/token, que emite sesiones sin contraseña.
Esta puerta escucha solo en 127.0.0.1 y deja pasar únicamente el webhook de WhatsApp; todo lo demás responde 404.

Uso (dos ventanas):
    python nube/integracion/puerta_webhook.py                 # puerta en 127.0.0.1:8002 -> API en 127.0.0.1:8000
    cloudflared tunnel --url http://127.0.0.1:8002            # da una dirección https://….trycloudflare.com

La dirección del túnel va en el gestor: Configuración → Tu WhatsApp con IA → sección 3 → «Dirección pública».
Solo usa la biblioteca estándar de Python.
"""
import argparse
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUTA = re.compile(r"/api/whatsapp/[0-9a-f]{32}/webhook")
MAX_CUERPO = 512 * 1024
PASAN = ("Content-Type", "X-Hub-Signature-256")


class Puerta(BaseHTTPRequestHandler):
    destino = "http://127.0.0.1:8000"
    server_version = "puerta-webhook"
    sys_version = ""

    def _reenviar(self, metodo):
        ruta = self.path.split("?", 1)[0]
        if not RUTA.fullmatch(ruta):
            return self._responder(404, b'{"detail":"No encontrado."}', "application/json")
        largo = int(self.headers.get("Content-Length") or 0)
        if largo > MAX_CUERPO:
            return self._responder(413, b'{"detail":"Aviso demasiado grande."}', "application/json")
        cuerpo = self.rfile.read(largo) if metodo == "POST" else None
        cabeceras = {k: self.headers[k] for k in PASAN if self.headers.get(k)}
        req = urllib.request.Request(self.destino + self.path, data=cuerpo, headers=cabeceras, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return self._responder(r.status, r.read(), r.headers.get("Content-Type", "text/plain"))
        except urllib.error.HTTPError as e:
            return self._responder(e.code, e.read(), e.headers.get("Content-Type", "text/plain"))
        except OSError:
            return self._responder(502, b'{"detail":"La nube local no responde."}', "application/json")

    def _responder(self, codigo, cuerpo, tipo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)
        # Una línea por aviso, sin contenido de los mensajes.
        print(f"{datetime.now():%H:%M:%S} {self.command} {self.path.split('?', 1)[0][:60]} -> {codigo}", flush=True)

    def do_GET(self):
        self._reenviar("GET")

    def do_POST(self):
        self._reenviar("POST")

    def do_PUT(self):
        self._responder(405, b"", "text/plain")

    do_DELETE = do_PATCH = do_PUT

    def log_message(self, *args):
        pass


def main(argv=None):
    p = argparse.ArgumentParser(description="Deja pasar solo el webhook de WhatsApp hacia la nube local.")
    p.add_argument("--puerto", type=int, default=8002)
    p.add_argument("--destino", default="http://127.0.0.1:8000", help="dirección de la API de la nube local")
    args = p.parse_args(argv)
    Puerta.destino = args.destino.rstrip("/")
    servidor = ThreadingHTTPServer(("127.0.0.1", args.puerto), Puerta)
    print(f"Puerta del webhook en http://127.0.0.1:{args.puerto} -> {Puerta.destino} (solo /api/whatsapp/<token>/webhook)")
    print(f"Ahora, en otra ventana: cloudflared tunnel --url http://127.0.0.1:{args.puerto}", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
