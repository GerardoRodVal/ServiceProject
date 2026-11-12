"""Instala y prepara el PostgreSQL de NearLeads Inmobiliarias. Lo ejecuta el instalador, con permisos de administrador:

    NearLeadsInmobiliarias.exe --instalar-bd --pg "<carpeta de la app>\\pgsql" [--puerto 5544]
    NearLeadsInmobiliarias.exe --quitar-bd   --pg "<carpeta de la app>\\pgsql"

--instalar-bd (se puede repetir: una actualización o una reparación no borra nada):
  1. Crea el clúster en <ProgramData>\\NearLeads Inmobiliarias\\pgdata con initdb (UTF-8, español de México con ICU,
     contraseñas scram-sha-256, solo escucha en localhost) si aún no existe.
  2. Da permiso a la cuenta NETWORK SERVICE sobre esa carpeta y registra el servicio de Windows
     «NearLeads Inmobiliarias - Base de datos» (inicio automático), y lo arranca.
  3. Crea el usuario de la app y la base (si faltan) y escribe conexion.ini con su contraseña.
  4. Crea las tablas (esquema.sql).
La contraseña del superusuario «postgres» se genera al azar y se guarda en admin\\postgres.txt, legible solo por
administradores, para mantenimiento (respaldos con pg_dump, por ejemplo).

--quitar-bd detiene y da de baja el servicio. Los datos se quedan en pgdata: los borra el desinstalador solo si se le pide.

Todo queda en registro\\instalacion.log.
"""
import argparse
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

import pg8000.dbapi

import bd

SERVICIO = "NearLeadsInmobiliariasBD"
SERVICIO_NOMBRE = "NearLeads Inmobiliarias - Base de datos"
PUERTO_DEFECTO = 5544
BASE = "nearleads_inmobiliarias"
USUARIO_APP = "nearleads_app"
SID_NETWORK_SERVICE = "*S-1-5-20"
SID_ADMINISTRADORES = "*S-1-5-32-544"
SID_SYSTEM = "*S-1-5-18"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErrorInstalacion(Exception):
    pass


def _ejecutar(args, comprobar=True, entrada=None):
    logging.info("$ %s", " ".join(f'"{a}"' if " " in str(a) else str(a) for a in args))
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, errors="replace", input=entrada,
                       creationflags=SIN_VENTANA, timeout=600)
    salida = (r.stdout + r.stderr).strip()
    if salida:
        logging.info(salida)
    if comprobar and r.returncode != 0:
        raise ErrorInstalacion(f"Falló: {os.path.basename(str(args[0]))} (código {r.returncode}). {salida[-400:]}")
    return r


def _rutas(pg):
    datos = bd.carpeta_datos()
    return {
        "bin": os.path.join(pg, "bin"),
        "datos": datos,
        "pgdata": os.path.join(datos, "pgdata"),
        "admin": os.path.join(datos, "admin"),
        "clave_admin": os.path.join(datos, "admin", "postgres.txt"),
    }


def _servicio_existe():
    return _ejecutar(["sc.exe", "query", SERVICIO], comprobar=False).returncode == 0


def _servicio_corriendo():
    r = _ejecutar(["sc.exe", "query", SERVICIO], comprobar=False)
    return r.returncode == 0 and "RUNNING" in r.stdout


def _leer_clave_admin(r):
    try:
        with open(r["clave_admin"], encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _restringir_a_administradores(ruta):
    """Solo administradores y SYSTEM: quita la herencia (los usuarios normales no leen la contraseña de postgres)."""
    _ejecutar(["icacls", ruta, "/inheritance:r", "/grant:r", f"{SID_ADMINISTRADORES}:(OI)(CI)F",
               f"{SID_SYSTEM}:(OI)(CI)F"])


def crear_cluster(r, puerto):
    if os.path.exists(os.path.join(r["pgdata"], "PG_VERSION")):
        logging.info("El clúster ya existe en %s: se conserva.", r["pgdata"])
        return False
    os.makedirs(r["admin"], exist_ok=True)
    _restringir_a_administradores(r["admin"])
    clave = secrets.token_urlsafe(24)
    with open(r["clave_admin"], "w", encoding="utf-8") as f:
        f.write(clave)
    # initdb se ejecuta con un token restringido (sin el grupo Administradores): no puede leer la carpeta admin. La
    # contraseña le llega en un archivo temporal del propio usuario, que se borra al terminar.
    fd, pwfile = tempfile.mkstemp(prefix="nearleads-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(clave)
    base_initdb = [os.path.join(r["bin"], "initdb.exe"), "-D", r["pgdata"], "-U", "postgres",
                   f"--pwfile={pwfile}", "-E", "UTF8", "--auth=scram-sha-256", "--data-checksums"]
    try:
        try:
            _ejecutar(base_initdb + ["--locale-provider=icu", "--icu-locale=es-MX", "--locale=C"])
        except ErrorInstalacion:
            # Sin ICU (o sin ese idioma) se usa el orden simple: los datos funcionan igual.
            logging.warning("initdb con ICU falló; se intenta con la configuración regional C.")
            if os.path.isdir(r["pgdata"]):
                shutil.rmtree(r["pgdata"], ignore_errors=True)
            _ejecutar(base_initdb + ["--locale=C"])
    finally:
        os.remove(pwfile)
    with open(os.path.join(r["pgdata"], "postgresql.conf"), "a", encoding="utf-8") as f:
        f.write("\n# --- NearLeads Inmobiliarias ---\n"
                f"port = {int(puerto)}\n"
                "listen_addresses = 'localhost'\n"
                "max_connections = 40\n"
                "shared_buffers = 128MB\n"
                "logging_collector = on\n"
                "log_directory = 'log'\n"
                "log_filename = 'postgresql-%a.log'\n"
                "log_truncate_on_rotation = on\n"
                "log_rotation_age = 1d\n"
                "log_rotation_size = 0\n")
    return True


def registrar_servicio(r):
    # El servicio corre como NETWORK SERVICE: necesita control total de pgdata y nada más.
    _ejecutar(["icacls", r["pgdata"], "/grant", f"{SID_NETWORK_SERVICE}:(OI)(CI)F", "/T", "/Q"])
    if not _servicio_existe():
        _ejecutar([os.path.join(r["bin"], "pg_ctl.exe"), "register", "-N", SERVICIO, "-D", r["pgdata"],
                   "-S", "auto", "-U", "NT AUTHORITY\\NetworkService", "-w"])
        _ejecutar(["sc.exe", "config", SERVICIO, "DisplayName=", SERVICIO_NOMBRE])
        _ejecutar(["sc.exe", "description", SERVICIO,
                   "Base de datos PostgreSQL local de NearLeads Inmobiliarias (prospectos, catálogo, citas y CRM)."])
        # Si se cae, Windows lo vuelve a levantar.
        _ejecutar(["sc.exe", "failure", SERVICIO, "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/30000"])
    if not _servicio_corriendo():
        _ejecutar(["sc.exe", "start", SERVICIO], comprobar=False)


def _conectar_admin(clave, puerto, base="postgres", segundos=90):
    limite = time.time() + segundos
    while True:
        try:
            return pg8000.dbapi.connect(host="127.0.0.1", port=int(puerto), user="postgres", password=clave,
                                        database=base, timeout=10)
        except (pg8000.dbapi.Error, OSError) as e:
            if time.time() > limite:
                raise ErrorInstalacion(f"El servicio de la base no respondió en el puerto {puerto}: {e}")
            time.sleep(2)


def crear_base(r, puerto):
    """Usuario y base de la app. Si ya existen y conexion.ini es válido, no se cambia nada."""
    clave_admin = _leer_clave_admin(r)
    if not clave_admin:
        raise ErrorInstalacion(f"No se encontró la contraseña del administrador de la base en {r['clave_admin']}.")
    con = _conectar_admin(clave_admin, puerto)
    con.autocommit = True
    cur = con.cursor()
    try:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (USUARIO_APP,))
        existe_rol = cur.fetchone() is not None
        conexion_valida = False
        if existe_rol and os.path.exists(bd.ruta_conexion()):
            try:
                p = bd.leer_parametros()
                conexion_valida = p["user"] == USUARIO_APP and int(p["port"]) == int(puerto) and bool(p["password"])
            except (bd.SinConfiguracion, ValueError, KeyError):
                conexion_valida = False
        if not conexion_valida:
            clave_app = secrets.token_urlsafe(24)
            # CREATE/ALTER ROLE no aceptan parámetros: la contraseña es base64 de URL (sin comillas), se arma segura.
            assert all(c.isalnum() or c in "-_" for c in clave_app)
            verbo = "ALTER" if existe_rol else "CREATE"
            cur.execute(f"{verbo} ROLE {USUARIO_APP} LOGIN PASSWORD '{clave_app}'")
            bd.escribir_parametros("127.0.0.1", puerto, BASE, USUARIO_APP, clave_app)
            logging.info("Usuario de la app %s.", "actualizado" if existe_rol else "creado")
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (BASE,))
        if cur.fetchone() is None:
            cur.execute(f"CREATE DATABASE {BASE} OWNER {USUARIO_APP} ENCODING 'UTF8' TEMPLATE template0")
            cur.execute(f"REVOKE CONNECT ON DATABASE {BASE} FROM PUBLIC")
            cur.execute(f"GRANT CONNECT ON DATABASE {BASE} TO {USUARIO_APP}")
            logging.info("Base %s creada.", BASE)
    finally:
        con.close()
    # En la base nueva el esquema public es del dueño de la base desde PostgreSQL 15: la app crea ahí sus tablas.
    bd.preparar()
    logging.info("Tablas listas.")


def instalar(pg, puerto):
    r = _rutas(pg)
    if not os.path.exists(os.path.join(r["bin"], "postgres.exe")):
        raise ErrorInstalacion(f"No se encontró PostgreSQL en {r['bin']}.")
    os.makedirs(r["datos"], exist_ok=True)
    # Los usuarios de la PC leen conexion.ini; solo los administradores escriben en la carpeta.
    nuevo = crear_cluster(r, puerto)
    registrar_servicio(r)
    crear_base(r, puerto)
    logging.info("Instalación de la base terminada (%s).", "nueva" if nuevo else "existente")


def quitar(pg):
    r = _rutas(pg)
    if _servicio_existe():
        if _servicio_corriendo():
            _ejecutar(["sc.exe", "stop", SERVICIO], comprobar=False)
            for _ in range(30):
                if not _servicio_corriendo():
                    break
                time.sleep(1)
        pg_ctl = os.path.join(r["bin"], "pg_ctl.exe")
        if os.path.exists(pg_ctl):
            _ejecutar([pg_ctl, "unregister", "-N", SERVICIO], comprobar=False)
        if _servicio_existe():
            _ejecutar(["sc.exe", "delete", SERVICIO], comprobar=False)
    logging.info("Servicio dado de baja. Los datos se conservan en %s.", r["pgdata"])


def main(argv):
    p = argparse.ArgumentParser(prog="NearLeadsInmobiliarias")
    p.add_argument("--instalar-bd", action="store_true")
    p.add_argument("--quitar-bd", action="store_true")
    p.add_argument("--pg", required=True, help="carpeta de PostgreSQL (con bin\\postgres.exe)")
    p.add_argument("--puerto", type=int, default=PUERTO_DEFECTO)
    a = p.parse_args(argv)
    carpeta_registro = os.path.join(bd.carpeta_datos(), "registro")
    os.makedirs(carpeta_registro, exist_ok=True)
    logging.basicConfig(filename=os.path.join(carpeta_registro, "instalacion.log"), level=logging.INFO,
                        encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s", force=True)
    if sys.stdout is not None:      # el ejecutable sin consola no tiene salida estándar
        logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    try:
        if a.quitar_bd:
            quitar(a.pg)
        else:
            instalar(a.pg, a.puerto)
        return 0
    except Exception as e:
        logging.exception("La instalación de la base falló")
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
