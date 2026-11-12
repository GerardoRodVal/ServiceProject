# Compila la APK de "Captura de visita" con las herramientas del SDK de Android (sin Gradle).
#
# Requisitos: JDK 17 (JAVA_HOME o en el PATH) y Android SDK con platforms;android-35 y build-tools;35.0.0.
# Uso (desde esta carpeta):
#   powershell -ExecutionPolicy Bypass -File .\build-apk.ps1
#
# Si existe %USERPROFILE%\.android-keys\captura-visitas.properties, firma para publicación.
# Si no existe, firma con la llave de depuración de Android (sirve para probar, no para el cliente).

param(
    [string]$Sdk = "$env:LOCALAPPDATA\Android\Sdk",
    [string]$BuildTools = "35.0.0",
    [int]$Plataforma = 35,
    [string]$Version = "1.1.0",
    [int]$CodigoVersion = 2
)

$ErrorActionPreference = 'Stop'
$proyecto = $PSScriptRoot
$pwa = Join-Path $proyecto '..\captura-visitas-pwa'
$salida = Join-Path $proyecto 'build\manual'
$dist = Join-Path $proyecto 'dist'
$bt = Join-Path $Sdk "build-tools\$BuildTools"
$androidJar = Join-Path $Sdk "platforms\android-$Plataforma\android.jar"
$aapt2 = Join-Path $bt 'aapt2.exe'
$d8 = Join-Path $bt 'd8.bat'
$apksigner = Join-Path $bt 'apksigner.bat'
$zipalign = Join-Path $bt 'zipalign.exe'

# Descarga desde Google la plataforma y las herramientas de compilación (versiones por defecto del proyecto).
function Instalar-ComponentesSdk {
    $tmp = Join-Path $env:TEMP 'captura-visitas-sdk'
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $paquetes = @(
        @{ Url = 'https://dl.google.com/android/repository/platform-35_r02.zip'; Zip = 'plataforma.zip'
           Origen = 'android-35'; Destino = Join-Path $Sdk 'platforms\android-35' },
        @{ Url = 'https://dl.google.com/android/repository/build-tools_r35_windows.zip'; Zip = 'build-tools.zip'
           Origen = 'android-15'; Destino = Join-Path $Sdk 'build-tools\35.0.0' }
    )
    foreach ($p in $paquetes) {
        Write-Host "Descargando $($p.Zip)..."
        $archivo = Join-Path $tmp $p.Zip
        Invoke-WebRequest -Uri $p.Url -OutFile $archivo -UseBasicParsing
        $extraido = Join-Path $tmp ($p.Zip + '.d')
        if (Test-Path $extraido) { Remove-Item $extraido -Recurse -Force }
        Expand-Archive -Path $archivo -DestinationPath $extraido -Force
        if (Test-Path $p.Destino) { Remove-Item $p.Destino -Recurse -Force }
        New-Item -ItemType Directory -Force (Split-Path $p.Destino) | Out-Null
        Move-Item (Join-Path $extraido $p.Origen) $p.Destino
    }
    # Aceptación de la licencia android-sdk-license (la misma que aceptas con sdkmanager --licenses).
    New-Item -ItemType Directory -Force (Join-Path $Sdk 'licenses') | Out-Null
    Set-Content -Path (Join-Path $Sdk 'licenses\android-sdk-license') -Value "`n24333f8a63b6825ea9c5514f83c2829b004d1fee" -NoNewline -Encoding ascii
}

$faltan = @($aapt2, $d8, $apksigner, $zipalign, $androidJar) | Where-Object { -not (Test-Path $_) }
if ($faltan) {
    Write-Host "Faltan componentes del Android SDK en $Sdk :" -ForegroundColor Yellow
    $faltan | ForEach-Object { Write-Host "  - $_" }
    $respuesta = Read-Host 'Descargarlos ahora desde Google (unos 130 MB)? Al hacerlo aceptas la licencia del Android SDK. (s/N)'
    if ($respuesta -notmatch '^[sSyY]') { throw 'Sin el SDK no se puede compilar. Vuelve a ejecutar y responde s.' }
    Instalar-ComponentesSdk
    $faltan = @($aapt2, $d8, $apksigner, $zipalign, $androidJar) | Where-Object { -not (Test-Path $_) }
    if ($faltan) { throw "Siguen faltando: $($faltan -join ', ')" }
}

# JDK: javac, jar y keytool. Si no hay JAVA_HOME, busca un JDK 17 en las rutas habituales.
if (-not $env:JAVA_HOME) {
    $jdk = Get-ChildItem -Path 'C:\Program Files\Microsoft', 'C:\Program Files\Eclipse Adoptium' -Directory -Filter 'jdk-17*' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($jdk) { $env:JAVA_HOME = $jdk.FullName }
}
if (-not $env:JAVA_HOME) {
    throw 'No encuentro un JDK 17. Instálalo con: winget install -e --id Microsoft.OpenJDK.17   (luego abre una terminal nueva)'
}
$jdkBin = Join-Path $env:JAVA_HOME 'bin'
$javac = Join-Path $jdkBin 'javac.exe'
$jar = Join-Path $jdkBin 'jar.exe'
$keytool = Join-Path $jdkBin 'keytool.exe'

# Firma
$propsFirma = Join-Path $env:USERPROFILE '.android-keys\captura-visitas.properties'
if (Test-Path $propsFirma) {
    $cfg = @{}
    Get-Content $propsFirma | ForEach-Object {
        if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2] }
    }
    $almacen = $cfg['storeFile']
    $alias = $cfg['keyAlias']
    $env:CAPTURA_KS_PASS = $cfg['storePassword']
    $env:CAPTURA_KEY_PASS = $cfg['keyPassword']
    $sufijo = ''
} else {
    $almacen = Join-Path $env:USERPROFILE '.android\debug.keystore'
    $alias = 'androiddebugkey'
    $env:CAPTURA_KS_PASS = 'android'
    $env:CAPTURA_KEY_PASS = 'android'
    $sufijo = '-depuracion'
    if (-not (Test-Path $almacen)) {
        New-Item -ItemType Directory -Force (Split-Path $almacen) | Out-Null
        & $keytool -genkeypair -keystore $almacen -storepass android -keypass android -alias $alias `
            -keyalg RSA -keysize 2048 -validity 10000 -dname 'CN=Android Debug,O=Android,C=US' | Out-Null
    }
    Write-Host 'Aviso: no hay llave de publicación; se firma con la llave de depuración.' -ForegroundColor Yellow
}

# 1. Limpiar y preparar la copia de la pantalla de captura (index.html y el lector de QR que usa)
if (Test-Path $salida) { Remove-Item $salida -Recurse -Force }
New-Item -ItemType Directory -Force "$salida\assets", "$salida\classes", "$salida\dex" | Out-Null
Copy-Item (Join-Path $pwa 'index.html') (Join-Path $salida 'assets\index.html')
# Va en la raíz de assets (sin subcarpeta): aapt2 en Windows guarda las subcarpetas con "\" y Android no las encuentra.
Copy-Item (Join-Path $pwa 'vendor\qrcode.js') (Join-Path $salida 'assets\qrcode.js')

# 2. Recursos y manifest. Sin Gradle, aapt2 necesita el atributo package (Gradle lo toma de build.gradle.kts).
$manifiestoFuente = Get-Content (Join-Path $proyecto 'app\src\main\AndroidManifest.xml') -Raw -Encoding UTF8
$manifiesto = Join-Path $salida 'AndroidManifest.xml'
$manifiestoFuente.Replace('<manifest xmlns:android="http://schemas.android.com/apk/res/android">',
    '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.nearustech.capturavisitas">') |
    Set-Content -Path $manifiesto -Encoding UTF8

& $aapt2 compile --dir (Join-Path $proyecto 'app\src\main\res') -o (Join-Path $salida 'res.zip')
if ($LASTEXITCODE -ne 0) { throw 'aapt2 compile falló' }
& $aapt2 link -o (Join-Path $salida 'base.apk') -I $androidJar `
    --manifest $manifiesto `
    -A (Join-Path $salida 'assets') `
    --min-sdk-version 24 --target-sdk-version $Plataforma `
    --version-code $CodigoVersion --version-name $Version `
    (Join-Path $salida 'res.zip')
if ($LASTEXITCODE -ne 0) { throw 'aapt2 link falló' }

# 3. Código Java -> bytecode
$fuentes = Get-ChildItem (Join-Path $proyecto 'app\src\main\java') -Recurse -Filter *.java | ForEach-Object { $_.FullName }
# --release 8 da la API de Java 8 (lambdas incluidas); las clases de Android vienen de android.jar en el classpath.
& $javac -encoding UTF-8 --release 8 -classpath $androidJar -d (Join-Path $salida 'classes') $fuentes
if ($LASTEXITCODE -ne 0) { throw 'javac falló' }

# 4. Bytecode -> dex y se añade a la APK
$clases = Get-ChildItem (Join-Path $salida 'classes') -Recurse -Filter *.class | ForEach-Object { $_.FullName }
& $d8 --release --min-api 24 --lib $androidJar --output (Join-Path $salida 'dex') $clases
if ($LASTEXITCODE -ne 0) { throw 'd8 falló' }
Copy-Item (Join-Path $salida 'dex\classes.dex') (Join-Path $salida 'classes.dex')
Push-Location $salida
& $jar uf base.apk classes.dex
Pop-Location
if ($LASTEXITCODE -ne 0) { throw 'jar falló al añadir classes.dex' }

# 5. Alinear y firmar
& $zipalign -f -p 4 (Join-Path $salida 'base.apk') (Join-Path $salida 'alineada.apk')
if ($LASTEXITCODE -ne 0) { throw 'zipalign falló' }
New-Item -ItemType Directory -Force $dist | Out-Null
$final = Join-Path $dist "CapturaVisitas-$Version$sufijo.apk"
& $apksigner sign --ks $almacen --ks-key-alias $alias --ks-pass env:CAPTURA_KS_PASS --key-pass env:CAPTURA_KEY_PASS `
    --out $final (Join-Path $salida 'alineada.apk')
if ($LASTEXITCODE -ne 0) { throw 'apksigner falló' }
& $apksigner verify --print-certs $final
if ($LASTEXITCODE -ne 0) { throw 'La firma no verifica' }

Remove-Item Env:CAPTURA_KS_PASS, Env:CAPTURA_KEY_PASS -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "APK lista: $final" -ForegroundColor Green
& $aapt2 dump badging $final | Select-String -Pattern '^package:|^application-label-es|^application-label:|^sdkVersion|^targetSdkVersion|uses-permission' | ForEach-Object { $_.Line }
