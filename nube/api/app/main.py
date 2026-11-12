import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import auth, citas, config, consejos, db, giros_rutas, imagenes, nucleo, publico, rutas, solicitudes, usuarios, web, whatsapp
from .almacen import Almacen


@asynccontextmanager
async def ciclo(app):
    cfg = config.cargar()
    app.state.cfg = cfg
    app.state.validador = auth.crear_validador(cfg)
    app.state.almacen = Almacen(cfg)
    db.abrir(cfg.database_url)
    yield
    db.cerrar()


class PedidoToken(BaseModel):
    correo: str


def crear_app():
    local = os.environ.get("AUTH_MODO", "local").strip().lower() == "local"
    # La documentación interactiva solo existe en desarrollo local.
    app = FastAPI(title="Captura de visitas · API", lifespan=ciclo,
                  docs_url="/docs" if local else None, redoc_url=None, openapi_url="/openapi.json" if local else None)
    app.include_router(rutas.router)
    app.include_router(usuarios.router)
    app.include_router(citas.app_router)
    app.include_router(citas.admin_router)
    app.include_router(consejos.router)
    app.include_router(imagenes.router)
    app.include_router(publico.router)
    app.include_router(solicitudes.router)
    app.include_router(nucleo.router)
    app.include_router(giros_rutas.router)
    app.include_router(whatsapp.router)
    web.registrar(app)

    @app.get("/salud")
    def salud():
        try:
            db.comprobar()
        except Exception:
            raise HTTPException(503, "La base de datos no responde.")
        return {"ok": True}

    if local:
        @app.post("/dev/token")
        def token_de_prueba(pedido: PedidoToken):
            """Solo en desarrollo local: emite un token para un usuario que ya existe. En Azure este endpoint no existe."""
            if not db.identificar(pedido.correo):
                raise HTTPException(404, "Ese correo no está dado de alta.")
            return {"token": app.state.validador.emitir(pedido.correo)}

    return app


app = crear_app()
