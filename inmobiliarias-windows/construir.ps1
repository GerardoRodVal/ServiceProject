# Construye el instalador de NearLeads Inmobiliarias para Windows.
#
#   powershell -ExecutionPolicy Bypass -File construir.ps1 [-PgDir "C:\Program Files\PostgreSQL\17"] [-SinInstalador]
#
# Pasos:
#   1. Copia del gestor el panel (adaptado) y la app del celular (herramientas\extraer.py).
#   2. Arma el ejecutable con PyInstaller (carpeta, no un solo .exe: abre más rápido).
#   3. Copia de una instalación de PostgreSQL 64 bits solo lo necesario para el servidor.
#   (2 y 3 trabajan en %LOCALAPPDATA%\NearLeadsInmobiliarias-construccion, fuera de OneDrive.)
#   4. Compila instalador\inmobiliarias.iss con Inno Setup 6 -> salida\NearLeadsInmobiliarias-Instalador-<versión>.exe
#
# Requisitos: Python 3.11+ (crea .venv con pg8000, pyinstaller y pillow), PostgreSQL 17 instalado en esta PC
# (solo como fuente de los binarios) e Inno Setup 6 (ISCC.exe).
param(
    [string]$PgDir = "C:\Program Files\PostgreSQL\17",
    [switch]$SinInstalador
)
$ErrorActionPreference = "Stop"
$raiz = $PSScriptRoot
Set-Location $raiz
# Carpetas de trabajo fuera de OneDrive: mientras sincroniza, OneDrive bloquea archivos y PyInstaller no puede borrarlos.
$trabajo = Join-Path $env:LOCALAPPDATA "NearLeadsInmobiliarias-construccion"
New-Item -ItemType Directory -Force $trabajo | Out-Null

$py = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creando entorno de Python..."
    py -3 -m venv .venv
}
& $py -m pip install --quiet --disable-pip-version-check pg8000 pyinstaller pillow
if ($LASTEXITCODE) { throw "No se pudieron instalar las dependencias de Python." }

Write-Host "1/4 Panel y app del celular"
& $py herramientas\extraer.py
if ($LASTEXITCODE) { throw "Falló la extracción desde el gestor." }

Write-Host "2/4 Ejecutable"
$version = (Select-String -Path app\inmobiliarias.py -Pattern '^VERSION_APP = "(.+)"').Matches[0].Groups[1].Value
& $py -m PyInstaller --noconfirm --clean --windowed --name NearLeadsInmobiliarias `
    --distpath "$trabajo\dist" --workpath "$trabajo\pyinstaller" --specpath "$trabajo" `
    --icon "$raiz\app\pwa\icon-512.png" `
    --add-data "$raiz\app\panel.html;." `
    --add-data "$raiz\app\esquema.sql;." `
    --add-data "$raiz\app\pwa;pwa" `
    --hidden-import instalar_bd `
    --paths "$raiz\app" `
    app\inmobiliarias.py
if ($LASTEXITCODE) { throw "Falló PyInstaller." }

Write-Host "3/4 PostgreSQL ($PgDir)"
if (-not (Test-Path "$PgDir\bin\postgres.exe")) { throw "No se encontró PostgreSQL en $PgDir" }
$pg = Join-Path $trabajo "pgsql"
if (Test-Path $pg) { Remove-Item -Recurse -Force $pg }
New-Item -ItemType Directory -Force "$pg\bin", "$pg\lib", "$pg\share" | Out-Null
# Servidor y herramientas de mantenimiento (respaldo y restauración); todas las DLL de bin (OpenSSL, ICU, zlib...).
$exes = "postgres", "initdb", "pg_ctl", "psql", "pg_dump", "pg_dumpall", "pg_restore", "pg_isready", "pg_controldata", "pg_upgrade"
foreach ($e in $exes) { Copy-Item "$PgDir\bin\$e.exe" "$pg\bin\" }
Copy-Item "$PgDir\bin\*.dll" "$pg\bin\" -Exclude "wx*"     # wxWidgets es de pgAdmin/StackBuilder, no del servidor
# Runtime de Visual C++ junto a los binarios (despliegue local que permite Microsoft): un Windows recién instalado no
# lo trae y sin él postgres.exe no arranca.
foreach ($dll in "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll") {
    if (-not (Test-Path "$env:windir\System32\$dll")) { throw "Falta $dll en System32: instala el Visual C++ Redistributable x64." }
    Copy-Item "$env:windir\System32\$dll" "$pg\bin\"
}
# Extensiones y conversiones de codificación que carga el servidor (solo DLL; sin .lib de desarrollo).
Copy-Item "$PgDir\lib\*.dll" "$pg\lib\"
Copy-Item "$PgDir\share\*" "$pg\share\" -Recurse -Exclude "doc"
foreach ($l in "server_license.txt", "commandlinetools_3rd_party_licenses.txt") {
    if (Test-Path "$PgDir\$l") { Copy-Item "$PgDir\$l" "$pg\" }
}
$mb = [math]::Round((Get-ChildItem $pg -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "   PostgreSQL recortado: $mb MB"

if ($SinInstalador) { Write-Host "Listo (sin instalador)."; return }

Write-Host "4/4 Instalador"
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "No se encontró Inno Setup 6 (ISCC.exe). Instálalo desde https://jrsoftware.org/isdl.php" }
& $iscc "/DVersionApp=$version" "/DTrabajo=$trabajo" instalador\inmobiliarias.iss
if ($LASTEXITCODE) { throw "Falló Inno Setup." }
Write-Host "Instalador: $(Join-Path $raiz "salida\NearLeadsInmobiliarias-Instalador-$version.exe")"
