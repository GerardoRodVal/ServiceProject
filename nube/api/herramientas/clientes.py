"""Alta y gestión de clientes (negocios) y de su primer administrador.

Es una herramienta de quien opera el servicio, no una ruta de la API: crear negocios cruza el aislamiento entre clientes,
así que usa la conexión de administrador de la base (DATABASE_ADMIN_URL) y la API pública no puede hacerlo. Cada
comando es una sola transacción: o queda todo o no queda nada.

  docker compose --profile herramientas run --rm clientes crear --nombre "Inmobiliaria Sol" --admin-correo ana@sol.mx
  docker compose --profile herramientas run --rm clientes listar
  docker compose --profile herramientas run --rm clientes admin --negocio "Inmobiliaria Sol" --correo luis@sol.mx
  docker compose --profile herramientas run --rm clientes suspender --negocio "Inmobiliaria Sol"
  docker compose --profile herramientas run --rm clientes reactivar --negocio "Inmobiliaria Sol"
  docker compose --profile herramientas run --rm clientes whatsapp-ia --negocio "Inmobiliaria Sol" --activar si

Dar de alta no crea la cuenta en el proveedor de identidad: la persona entra a la app con ese correo y, si es la primera
vez, crea su cuenta ahí. Desde entonces el administrador da de alta a sus asesores desde el gestor.
"""
import argparse
import os
import re
import sys

import psycopg
from psycopg.rows import dict_row

RE_CORREO = re.compile(r"[^@\s]{1,100}@[^@\s]{1,100}\.[^@\s]{2,20}")
RE_GIRO = re.compile(r"[a-z][a-z_]{2,39}")


class ErrorAlta(Exception):
    """Algo que quien opera debe corregir (dato inválido, duplicado, no existe). Sale sin traza."""


def _nombre(texto):
    nombre = " ".join((texto or "").split())
    if not 1 <= len(nombre) <= 120:
        raise ErrorAlta("El nombre del negocio debe tener entre 1 y 120 caracteres.")
    return nombre


def _correo(texto):
    correo = (texto or "").strip().lower()
    if len(correo) > 200 or not RE_CORREO.fullmatch(correo):
        raise ErrorAlta(f"«{texto}» no es un correo válido.")
    return correo


def _negocio(con, nombre):
    """Busca por nombre (sin distinguir mayúsculas). Como crear no permite repetirlos, hay a lo más uno."""
    fila = con.execute("SELECT * FROM negocios WHERE lower(nombre) = lower(%s)", (_nombre(nombre),)).fetchone()
    if not fila:
        raise ErrorAlta(f"No existe el negocio «{nombre}». Revisa el nombre con el comando listar.")
    return fila


def crear(con, nombre, correo_admin, nombre_admin="", giro="inmobiliarias"):
    nombre, correo = _nombre(nombre), _correo(correo_admin)
    giro = (giro or "").strip().lower()
    if not RE_GIRO.fullmatch(giro):
        raise ErrorAlta("El giro debe ser una palabra en minúsculas, por ejemplo «inmobiliarias».")
    nombre_admin = " ".join((nombre_admin or "").split())[:80] or correo.split("@")[0]
    if con.execute("SELECT 1 FROM negocios WHERE lower(nombre) = lower(%s)", (nombre,)).fetchone():
        raise ErrorAlta(f"Ya existe un negocio llamado «{nombre}».")
    dueno = con.execute("SELECT n.nombre FROM usuarios u JOIN negocios n ON n.id = u.negocio_id WHERE lower(u.correo) = %s", (correo,)).fetchone()
    if dueno:
        raise ErrorAlta(f"El correo {correo} ya está registrado en «{dueno['nombre']}»: cada correo pertenece a un solo negocio.")
    negocio = con.execute("INSERT INTO negocios (nombre, giro) VALUES (%s, %s) RETURNING id, nombre, giro, token_publico", (nombre, giro)).fetchone()
    con.execute("INSERT INTO usuarios (negocio_id, correo, nombre, rol) VALUES (%s, %s, %s, 'admin')", (negocio["id"], correo, nombre_admin))
    return {**negocio, "admin_correo": correo}


def agregar_admin(con, nombre_negocio, correo, nombre=""):
    """Agrega un administrador a un negocio que ya existe, o promueve y reactiva a quien ya es de ese negocio (por ejemplo,
    cuando el único administrador perdió el acceso). Un correo de otro negocio no se toca."""
    negocio = _negocio(con, nombre_negocio)
    correo = _correo(correo)
    actual = con.execute("SELECT id, negocio_id FROM usuarios WHERE lower(correo) = %s", (correo,)).fetchone()
    if actual and actual["negocio_id"] != negocio["id"]:
        raise ErrorAlta(f"El correo {correo} ya está registrado en otro negocio.")
    if actual:
        con.execute("UPDATE usuarios SET rol = 'admin', activo = true WHERE id = %s", (actual["id"],))
        return {"negocio": negocio["nombre"], "correo": correo, "accion": "promovido"}
    con.execute("INSERT INTO usuarios (negocio_id, correo, nombre, rol) VALUES (%s, %s, %s, 'admin')",
                (negocio["id"], correo, " ".join((nombre or "").split())[:80] or correo.split("@")[0]))
    return {"negocio": negocio["nombre"], "correo": correo, "accion": "agregado"}


def cambiar_activo(con, nombre_negocio, activo):
    negocio = _negocio(con, nombre_negocio)
    con.execute("UPDATE negocios SET activo = %s WHERE id = %s", (activo, negocio["id"]))
    return {"negocio": negocio["nombre"], "activo": activo, "ya_estaba": negocio["activo"] == activo}


def cambiar_whatsapp_ia(con, nombre_negocio, activo):
    """Complemento con costo aparte: la IA que contesta el WhatsApp del negocio. Sin él los mensajes se guardan pero
    la IA no contesta."""
    negocio = _negocio(con, nombre_negocio)
    con.execute("UPDATE negocios SET whatsapp_ia = %s WHERE id = %s", (activo, negocio["id"]))
    return {"negocio": negocio["nombre"], "activo": activo, "ya_estaba": negocio["whatsapp_ia"] == activo}


def listar(con):
    return con.execute(
        """SELECT n.id, n.nombre, n.giro, n.activo, n.creado, n.whatsapp_ia,
                  (SELECT count(*) FROM usuarios u WHERE u.negocio_id = n.id AND u.activo) AS usuarios,
                  (SELECT string_agg(u.correo, ', ' ORDER BY u.creado) FROM usuarios u
                    WHERE u.negocio_id = n.id AND u.rol = 'admin' AND u.activo) AS admins,
                  (SELECT count(*) FROM registros r WHERE r.negocio_id = n.id) AS registros
           FROM negocios n ORDER BY n.creado, n.nombre""").fetchall()


def main(argv=None, admin_url=None):
    p = argparse.ArgumentParser(prog="clientes", description="Alta y gestión de clientes (negocios).")
    sub = p.add_subparsers(dest="comando", required=True)
    c = sub.add_parser("crear", help="crea un negocio y su primer administrador")
    c.add_argument("--nombre", required=True, help="nombre del negocio")
    c.add_argument("--admin-correo", required=True)
    c.add_argument("--admin-nombre", default="")
    c.add_argument("--giro", default="inmobiliarias")
    c.add_argument("--url", default="http://127.0.0.1:8000", help="dirección pública de la app, para el mensaje final")
    sub.add_parser("listar", help="muestra los negocios, sus administradores y su uso")
    a = sub.add_parser("admin", help="agrega (o reactiva) un administrador de un negocio")
    a.add_argument("--negocio", required=True)
    a.add_argument("--correo", required=True)
    a.add_argument("--nombre", default="")
    for nombre, ayuda in (("suspender", "bloquea el acceso de todo el negocio sin borrar datos"), ("reactivar", "devuelve el acceso")):
        s = sub.add_parser(nombre, help=ayuda)
        s.add_argument("--negocio", required=True)
    w = sub.add_parser("whatsapp-ia", help="activa o quita el complemento de IA para WhatsApp (costo aparte)")
    w.add_argument("--negocio", required=True)
    w.add_argument("--activar", required=True, choices=("si", "no"))
    args = p.parse_args(argv)

    admin_url = admin_url or os.environ.get("DATABASE_ADMIN_URL", "")
    if not admin_url:
        print("Falta DATABASE_ADMIN_URL (la conexión de administrador de la base).", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(admin_url, row_factory=dict_row) as con:      # el bloque confirma al salir y revierte si falla
            if args.comando == "crear":
                r = crear(con, args.nombre, args.admin_correo, args.admin_nombre, args.giro)
                print(f"Negocio creado: {r['nombre']} (id {r['id']}, giro {r['giro']})")
                print(f"Administrador:  {r['admin_correo']}")
                print(f"\nSiguiente paso: que {r['admin_correo']} entre a {args.url} con ese correo (si es su primera vez, "
                      "«Crea tu cuenta»). Después da de alta a sus asesores desde el gestor.")
            elif args.comando == "listar":
                filas = listar(con)
                if not filas:
                    print("No hay negocios.")
                for f in filas:
                    estado = "activo" if f["activo"] else "SUSPENDIDO"
                    print(f"{f['nombre']} · {f['giro']} · {estado} · {f['usuarios']} usuarios · {f['registros']} registros · "
                          f"admin: {f['admins'] or '(ninguno activo)'} · creado {f['creado']:%Y-%m-%d}"
                          + (" · IA WhatsApp" if f["whatsapp_ia"] else ""))
            elif args.comando == "admin":
                r = agregar_admin(con, args.negocio, args.correo, args.nombre)
                print(f"{r['correo']} {r['accion']} como administrador de {r['negocio']}.")
            elif args.comando == "whatsapp-ia":
                r = cambiar_whatsapp_ia(con, args.negocio, args.activar == "si")
                estado = "activo" if r["activo"] else "sin el complemento"
                print(f"IA para WhatsApp de {r['negocio']}: {'ya estaba ' if r['ya_estaba'] else ''}{estado}.")
            else:
                r = cambiar_activo(con, args.negocio, args.comando == "reactivar")
                estado = "reactivado" if r["activo"] else "suspendido"
                print(f"{r['negocio']} ya estaba {estado}." if r["ya_estaba"] else f"{r['negocio']} {estado}.")
    except ErrorAlta as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
