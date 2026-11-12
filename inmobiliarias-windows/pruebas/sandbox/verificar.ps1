# Prueba de instalación limpia en Windows Sandbox (ver pruebas\sandbox\README en prueba_sandbox.ps1).
# C:\entrada = carpeta salida (instalador, solo lectura). C:\prueba = esta carpeta (resultados).
$ErrorActionPreference = "Continue"
$res = "C:\prueba\resultado.txt"
Set-Content $res "Prueba de instalación en Windows Sandbox - $(Get-Date -Format s)" -Encoding UTF8
function Anotar($ok, $texto) { Add-Content $res ("{0} {1}" -f ($(if ($ok) { "OK   " } else { "FALLA" }), $texto)) -Encoding UTF8 }
function Nota($texto) { Add-Content $res "     $texto" -Encoding UTF8 }

$instalador = Get-ChildItem C:\entrada\NearLeadsInmobiliarias-Instalador-*.exe | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Nota "Instalador: $($instalador.Name)"
$app = "C:\Program Files\NearLeads Inmobiliarias"
$datos = "C:\ProgramData\NearLeads Inmobiliarias"
Nota "Runtime VC++ en System32 antes de instalar: $(Test-Path C:\Windows\System32\vcruntime140.dll)"

function Instalar($etiqueta) {
    $t = Get-Date
    $p = Start-Process $instalador.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=C:\prueba\inno-$etiqueta.log" -Wait -PassThru
    Anotar ($p.ExitCode -eq 0) "Instalador ($etiqueta) terminó con código $($p.ExitCode) en $([int]((Get-Date) - $t).TotalSeconds) s"
}

function Psql($sql) {
    $ini = Get-Content "$datos\conexion.ini"
    $env:PGPASSWORD = (($ini | Where-Object { $_ -match '^clave' }) -split '=', 2)[1].Trim()
    & "$app\pgsql\bin\psql.exe" -h 127.0.0.1 -p 5544 -U nearleads_app -d nearleads_inmobiliarias -tAc $sql 2>&1
}

function Api($metodo, $ruta, $cuerpo) {
    $p = @{ Method = $metodo; Uri = "http://127.0.0.1:8765$ruta"; TimeoutSec = 20 }
    if ($cuerpo) { $p.Body = [Text.Encoding]::UTF8.GetBytes(($cuerpo | ConvertTo-Json -Depth 8)); $p.ContentType = "application/json" }
    Invoke-RestMethod @p
}

# --- 1. Instalación limpia ---
Instalar "limpia"
$svc = Get-Service NearLeadsInmobiliariasBD -ErrorAction SilentlyContinue
Anotar ($svc -and $svc.Status -eq "Running") "Servicio de la base: $($svc.Status) / inicio $($svc.StartType) / '$($svc.DisplayName)'"
Anotar (Test-Path "$datos\conexion.ini") "conexion.ini creado"
Anotar (Test-Path "$datos\pgdata\PG_VERSION") "Clúster en ProgramData"
Copy-Item "$datos\registro\instalacion.log" C:\prueba\instalacion-limpia.log -ErrorAction SilentlyContinue
$tablas = Psql "select count(*) from information_schema.tables where table_schema='public'"
Anotar ([int]("$tablas".Trim()) -ge 17) "Tablas creadas: $tablas"
Nota "Collation: $(Psql "select datlocprovider::text || ' ' || coalesce(datlocale, datcollate) from pg_database where datname = current_database()")"
$regla = netsh advfirewall firewall show rule name="NearLeads Inmobiliarias (celulares en la red local)" 2>&1
Anotar ($LASTEXITCODE -eq 0) "Regla del firewall creada"
$adminAcl = (icacls "$datos\admin" 2>&1) -join " "
Anotar ($adminAcl -notmatch "Usuarios|Users") "Carpeta admin solo para administradores"

# --- 2. La app ---
$proc = Start-Process "$app\NearLeadsInmobiliarias.exe" -ArgumentList "--sin-navegador" -PassThru
$estado = $null
for ($i = 0; $i -lt 40 -and -not $estado; $i++) { try { $estado = Api GET "/api/estado" } catch { Start-Sleep 1 } }
Anotar ($estado -ne $null) "La app abre el panel: $($estado.almacen)"
Anotar ($estado.servidor.activo) "El servidor del celular arranca solo: $($estado.servidor.url)"
$html = $null
try { $html = Invoke-WebRequest "http://127.0.0.1:$($estado.servidor.puerto)/index.html" -UseBasicParsing -TimeoutSec 10 } catch {}
Anotar ($html -and $html.StatusCode -eq 200) "La app del celular responde sin tocar nada (puerto $($estado.servidor.puerto))"
$tabs = (Invoke-WebRequest "http://127.0.0.1:8765/" -UseBasicParsing -TimeoutSec 10).Content
Anotar ($tabs -match '#tabs \[data-v="metaai"\]' -and $tabs -notmatch '<button type="button" data-v="metaai"') "Meta AI oculto"
try {
    $c = Api POST "/api/crm/contactos" @{ nombre = "Cliente de prueba Sandbox"; telefono = "5512345678" }
    Anotar ($c.contacto.id) "Alta de contacto en el CRM local"
    $cfg = Api GET "/api/config"
    $cfg.negocio.nombre = "Inmobiliaria Sandbox"
    $cfg.propiedades = @(@{ id = "casa-1"; nombre = "Casa 1"; precio = 2500000; tipo = "casa"; estatus = "disponible" })
    $r = Api POST "/api/config" $cfg
    Anotar ($r.ok) "Guardar configuración con un inmueble"
    $s = Api POST "/api/servidor" @{ accion = "iniciar"; puerto = 8080 }
    $conf = Invoke-RestMethod "http://127.0.0.1:8080/config.json" -TimeoutSec 10
    Anotar ($conf.negocio.nombre -eq "Inmobiliaria Sandbox" -and $conf.propiedades.Count -eq 1) "Servidor del celular y config.json"
    $html = Invoke-WebRequest "http://127.0.0.1:8765/" -UseBasicParsing -TimeoutSec 10
    Anotar ($html.Content -match "NearLeads Inmobiliarias") "Panel servido"
    Api POST "/api/salir" @{} | Out-Null
} catch { Anotar $false "Error usando la app: $_" }
Start-Sleep 3
Anotar (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) "La app se cierra con Salir"
Copy-Item "$datos\registro\app-*.log" C:\prueba\ -ErrorAction SilentlyContinue

# --- 3. Reinicio del servicio (como al encender la PC) ---
Restart-Service NearLeadsInmobiliariasBD
Anotar ((Get-Service NearLeadsInmobiliariasBD).Status -eq "Running") "El servicio reinicia"

# --- 4. Desinstalar (sin borrar datos) y volver a instalar ---
$p = Start-Process "$app\unins000.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
Start-Sleep 3
Anotar (-not (Get-Service NearLeadsInmobiliariasBD -ErrorAction SilentlyContinue)) "Desinstalar quita el servicio (código $($p.ExitCode))"
netsh advfirewall firewall show rule name="NearLeads Inmobiliarias (celulares en la red local)" | Out-Null
Anotar ($LASTEXITCODE -ne 0) "Desinstalar quita la regla del firewall"
Anotar (Test-Path "$datos\pgdata\PG_VERSION") "Los datos se conservan al desinstalar"

Instalar "reinstalacion"
$n = Psql "select count(*) from contactos where nombre = 'Cliente de prueba Sandbox'"
Anotar ("$n".Trim() -eq "1") "Al reinstalar, los datos siguen ahí (contactos: $n)"
Copy-Item "$datos\registro\instalacion.log" C:\prueba\instalacion-final.log -ErrorAction SilentlyContinue

# --- 5. Actualizar encima (instalador sobre una instalación activa) ---
Instalar "encima"
Anotar ((Get-Service NearLeadsInmobiliariasBD).Status -eq "Running") "Servicio activo tras instalar encima"

Add-Content $res "FIN" -Encoding UTF8
Stop-Computer -Force
