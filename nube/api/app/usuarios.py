"""Alta y gestión de usuarios de un negocio, solo para su administrador.

Quién puede entrar lo decide esta tabla, no el proveedor de identidad: el proveedor (Entra) solo comprueba que la persona
es quien dice ser; la API solo la deja pasar si su correo está dado de alta aquí. Dar de alta no crea la cuenta en el
proveedor: la persona entra con ese correo y, si es la primera vez, crea su cuenta ahí.
"""
import re
import uuid
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import db
from .auth import requiere

router = APIRouter(prefix="/api/usuarios")
solo_admin = requiere("admin")
RE_CORREO = re.compile(r"[^@\s]{1,100}@[^@\s]{1,100}\.[^@\s]{2,20}")
Rol = Literal["admin", "capturista"]
COLUMNAS = "id, correo, nombre, rol, activo, creado"


class NuevoUsuario(BaseModel):
    correo: str
    nombre: str = ""
    rol: Rol = "capturista"


class CambioUsuario(BaseModel):
    nombre: str | None = None
    rol: Rol | None = None
    activo: bool | None = None


def _aviso(limite, activos):
    """Los planes aún no están definidos: con un límite configurado se avisa, pero no se impide dar de alta."""
    return "excedido" if limite and activos > limite else None


@router.get("")
def listar(request: Request, ident=Depends(solo_admin)):
    with db.transaccion(ident.negocio_id) as con:
        filas = con.execute(f"SELECT {COLUMNAS} FROM usuarios ORDER BY creado, correo").fetchall()
    activos = sum(1 for f in filas if f["activo"])
    return {"usuarios": filas, "activos": activos, "limite": request.app.state.cfg.limite_usuarios,
            "aviso": _aviso(request.app.state.cfg.limite_usuarios, activos)}


@router.post("")
def dar_de_alta(request: Request, nuevo: NuevoUsuario, ident=Depends(solo_admin)):
    correo = nuevo.correo.strip().lower()
    nombre = nuevo.nombre.strip()
    if len(correo) > 200 or not RE_CORREO.fullmatch(correo):
        raise HTTPException(422, "Escribe un correo válido.")
    if len(nombre) > 80:
        raise HTTPException(422, "El nombre pasa de 80 caracteres.")
    try:
        with db.transaccion(ident.negocio_id) as con:
            fila = con.execute(
                f"INSERT INTO usuarios (negocio_id, correo, nombre, rol) VALUES (%s, %s, %s, %s) RETURNING {COLUMNAS}",
                (ident.negocio_id, correo, nombre or correo.split("@")[0], nuevo.rol)).fetchone()
            activos = con.execute("SELECT count(*) AS n FROM usuarios WHERE activo").fetchone()["n"]
    except psycopg.errors.UniqueViolation:
        # El correo es único en todo el servicio (identifica a la persona ante el proveedor).
        raise HTTPException(409, "Ese correo ya está registrado.")
    return {"usuario": fila, "aviso": _aviso(request.app.state.cfg.limite_usuarios, activos)}


@router.patch("/{usuario_id}")
def actualizar(usuario_id: uuid.UUID, cambio: CambioUsuario, ident=Depends(solo_admin)):
    nombre = None if cambio.nombre is None else cambio.nombre.strip()
    if nombre is not None and not 1 <= len(nombre) <= 80:
        raise HTTPException(422, "El nombre debe tener entre 1 y 80 caracteres.")
    with db.transaccion(ident.negocio_id) as con:
        # Se bloquean los administradores activos (en orden fijo) para que dos cambios simultáneos no dejen al negocio sin ninguno.
        con.execute("SELECT id FROM usuarios WHERE rol = 'admin' AND activo ORDER BY id FOR UPDATE")
        actual = con.execute("SELECT id, rol, activo FROM usuarios WHERE id = %s FOR UPDATE", (usuario_id,)).fetchone()
        if not actual:
            raise HTTPException(404, "No encontramos a ese usuario.")
        rol = cambio.rol or actual["rol"]
        activo = actual["activo"] if cambio.activo is None else cambio.activo
        deja_de_ser_admin = actual["rol"] == "admin" and actual["activo"] and not (rol == "admin" and activo)
        if deja_de_ser_admin:
            if str(usuario_id) == ident.usuario_id:
                raise HTTPException(409, "No puedes quitarte tu propio acceso de administrador.")
            otros = con.execute("SELECT count(*) AS n FROM usuarios WHERE rol = 'admin' AND activo AND id <> %s", (usuario_id,)).fetchone()["n"]
            if not otros:
                raise HTTPException(409, "Debe quedar al menos un administrador activo.")
        fila = con.execute(
            f"UPDATE usuarios SET nombre = COALESCE(%s, nombre), rol = %s, activo = %s WHERE id = %s RETURNING {COLUMNAS}",
            (nombre, rol, activo, usuario_id)).fetchone()
    return {"usuario": fila}
