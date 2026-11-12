# NearLeads Inmobiliarias (edición independiente para Windows)

Aplicación de escritorio para inmobiliarias y desarrolladoras, extraída del gestor de captura (`gestor-windows/`).
Se instala con un solo `.exe`, trae su propia base PostgreSQL y no necesita la nube.

## Qué incluye

- **Panel** (en el navegador, solo desde la PC donde está instalado): Panel, Inmuebles y zonas, Contactos, Embudo,
  Tareas, Citas, Registro (formulario del celular), Asesores y Configuración (incluida la IA local con Ollama, opcional).
- **App del celular** por Wi-Fi: en Configuración → Conexión se enciende el servidor; los asesores abren la dirección
  que aparece, inician sesión y capturan visitas. También la página pública para agendar citas.
- **Inicio de sesión en el panel** con el correo y la contraseña de cada asesor (los mismos del celular). El administrador
  ve y cambia todo; el asesor trabaja Panel, Desarrollos, Contactos, Embudo, Tareas y Citas, pero no cambia
  configuración, catálogo, formulario ni equipo; el capturador solo usa el celular. Recién instalada, mientras no haya un
  administrador con contraseña, el panel abre sin sesión para darlo de alta. Cuentas de la demo: `pruebas/ACCESOS_DEMO.md`.
- **Servidor del celular** encendido solo mientras la app está abierta (puerto guardado u 8080, o el siguiente libre).
- **CRM local**: cada visita del celular se liga sola a su contacto (por teléfono a 10 dígitos) y a su oportunidad del
  embudo; mover la etapa cambia el estado de la visita y al revés.

No incluye taller, venta, Kommo, WhatsApp con IA ni la conexión con la nube.

## Cómo se instala (lo que hace el instalador)

`salida\NearLeadsInmobiliarias-Instalador-<versión>.exe` (pide permisos de administrador):

1. Copia la app y PostgreSQL 17 a `C:\Program Files\NearLeads Inmobiliarias` (`pgsql\` dentro).
2. Crea la base en `C:\ProgramData\NearLeads Inmobiliarias\pgdata` (UTF-8, español de México, contraseñas
   scram-sha-256, solo escucha en `localhost`, puerto **5544**).
3. Registra el servicio de Windows **«NearLeads Inmobiliarias - Base de datos»** (`NearLeadsInmobiliariasBD`), inicio
   automático y reinicio si falla.
4. Crea el usuario `nearleads_app`, la base `nearleads_inmobiliarias` y todas las tablas (`app/esquema.sql`).
   La conexión queda en `C:\ProgramData\NearLeads Inmobiliarias\conexion.ini`.
5. Abre la app en el Firewall de Windows solo para redes **privadas** (los celulares se conectan por Wi-Fi).
6. Crea los accesos directos.

La contraseña del superusuario `postgres` se genera al azar y queda en
`C:\ProgramData\NearLeads Inmobiliarias\admin\postgres.txt` (solo administradores), para mantenimiento.
El registro de la instalación está en `...\registro\instalacion.log`; el de la app, en `...\registro\app-<usuario>.log`.

**Actualizar:** se ejecuta el instalador nuevo encima; detiene el servicio, reemplaza archivos, conserva los datos y
aplica las tablas nuevas. **Desinstalar:** quita el servicio y la regla del firewall y pregunta si borrar los datos
(por omisión se conservan y se recuperan al reinstalar).

### Respaldo de la base

```bat
set PGPASSWORD=<contraseña de admin\postgres.txt>
"C:\Program Files\NearLeads Inmobiliarias\pgsql\bin\pg_dump.exe" -h 127.0.0.1 -p 5544 -U postgres -Fc -f respaldo.dump nearleads_inmobiliarias
```

## Estructura

| Ruta | Qué es |
|---|---|
| `app/inmobiliarias.py` | Servidor del panel (127.0.0.1:8765) y del celular (red local). Viene de `gestor_visitas.py` sin taller, venta, Kommo, Meta ni nube. |
| `app/datos_pg.py` | Tablas del giro en PostgreSQL; mismas funciones que `gestor-windows/datos_locales.py`. |
| `app/crm_local.py` | Contactos, embudos, oportunidades, actividades y tareas; mismas rutas y respuestas que `nube/api/app/nucleo.py`, más la liga de visitas (`visitas_nucleo.py`). |
| `app/bd.py` | Conexión (pg8000, sin DLL de cliente), reserva de conexiones y creación de tablas al arrancar. |
| `app/esquema.sql` | Todas las tablas. Idempotente; los cambios futuros van al final como pasos nuevos. |
| `app/instalar_bd.py` | Lo que corre el instalador: `--instalar-bd` / `--quitar-bd`. |
| `app/panel.html`, `app/pwa/` | **Generados** por `herramientas/extraer.py` desde el gestor. No se editan a mano. |
| `herramientas/extraer.py` | Copia y adapta el panel y la app del celular. Se detiene si el panel del gestor cambió donde se adapta. |
| `instalador/inmobiliarias.iss` | Script de Inno Setup 6. |
| `construir.ps1` | Construye todo: extracción, PyInstaller, PostgreSQL recortado e instalador. |
| `pruebas/prueba_humo.py` | 38 comprobaciones contra una instancia en marcha (usar con una base de prueba). |
| `pruebas/sandbox/` | Prueba de instalación limpia, desinstalación y reinstalación en Windows Sandbox. |

## Construir

Requisitos: Python 3.11+, PostgreSQL 17 instalado en la PC de construcción (solo como fuente de binarios), el
Visual C++ Redistributable x64 e Inno Setup 6.

```powershell
powershell -ExecutionPolicy Bypass -File construir.ps1
```

El instalador sale en `salida\`. Para probarlo en una máquina desechable:

```powershell
powershell -ExecutionPolicy Bypass -File pruebas\sandbox\prueba_sandbox.ps1
```

## Desarrollo sin instalar

Con un PostgreSQL de prueba (base y usuario creados a mano):

```powershell
$env:INMOB_PG_HOST="127.0.0.1"; $env:INMOB_PG_PUERTO="5599"; $env:INMOB_PG_BASE="nearleads_inmobiliarias"
$env:INMOB_PG_USUARIO="nearleads_app"; $env:INMOB_PG_CLAVE="..."; $env:INMOB_DATOS="$env:TEMP\nearleads-datos"
.venv\Scripts\python.exe app\inmobiliarias.py --puerto-panel 8865
python pruebas\prueba_humo.py 8865
```

## Limitaciones conocidas

- Una PC por instalación: el panel solo abre en la PC donde está instalado (los celulares solo capturan).
- El puerto de la base es fijo (5544); si otro programa lo usa, el servicio no arranca (se ve en `instalacion.log`).
- Las fotos del catálogo se guardan dentro de la base (tabla `imagenes`), así que el respaldo de la base las incluye.
- El instalador no está firmado digitalmente: Windows SmartScreen mostrará un aviso hasta firmarlo.
