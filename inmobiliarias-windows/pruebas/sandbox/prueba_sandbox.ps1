# Prueba el instalador en Windows Sandbox (una máquina desechable): instala en silencio, revisa servicio, tablas,
# firewall y la app; desinstala, reinstala comprobando que los datos se conservan, e instala encima. Al final apaga el
# Sandbox y los resultados quedan en pruebas\sandbox\resultado.txt.
#
#   powershell -ExecutionPolicy Bypass -File pruebas\sandbox\prueba_sandbox.ps1
#
# Requiere la característica «Windows Sandbox» activada y haber corrido construir.ps1.
$ErrorActionPreference = "Stop"
$aqui = $PSScriptRoot
$salida = Resolve-Path (Join-Path $aqui "..\..\salida")
Remove-Item "$aqui\resultado.txt", "$aqui\*.log" -ErrorAction SilentlyContinue
$wsb = @"
<Configuration>
  <MappedFolders>
    <MappedFolder><HostFolder>$salida</HostFolder><SandboxFolder>C:\entrada</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$aqui</HostFolder><SandboxFolder>C:\prueba</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -ExecutionPolicy Bypass -File C:\prueba\verificar.ps1</Command></LogonCommand>
  <MemoryInMB>4096</MemoryInMB>
</Configuration>
"@
$archivo = Join-Path $env:TEMP "nearleads-prueba.wsb"
Set-Content $archivo $wsb -Encoding UTF8
Start-Process $archivo
Write-Host "Sandbox iniciado. Resultados en $aqui\resultado.txt"
