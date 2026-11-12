; Instalador de NearLeads Inmobiliarias (Inno Setup 6). Se compila con construir.ps1, que antes arma la app y
; PostgreSQL recortado en la carpeta de construcción (fuera de OneDrive) y la pasa como /DTrabajo.
;
; Qué hace al instalar (con permisos de administrador):
;   - Copia la app y PostgreSQL a Archivos de programa\NearLeads Inmobiliarias.
;   - Detiene el servicio de la base si ya existía (actualización) para poder reemplazar sus archivos.
;   - Ejecuta «NearLeadsInmobiliarias.exe --instalar-bd»: crea el clúster en ProgramData, registra el servicio
;     «NearLeads Inmobiliarias - Base de datos», crea el usuario y la base de la app y sus tablas (ver app\instalar_bd.py).
;   - Abre en el Firewall de Windows la app para redes privadas (los celulares se conectan por Wi-Fi).
; Al desinstalar: da de baja el servicio y la regla del firewall; pregunta si también se borran los datos.

#ifndef Trabajo
  #error Compila con construir.ps1 (pasa /DTrabajo con la carpeta de construcción)
#endif
#ifndef VersionApp
  #define VersionApp "1.0.0"
#endif
#define Nombre "NearLeads Inmobiliarias"
#define Exe "NearLeadsInmobiliarias.exe"
#define Servicio "NearLeadsInmobiliariasBD"
#define PuertoBD "5544"
#define ReglaFirewall "NearLeads Inmobiliarias (celulares en la red local)"

[Setup]
AppId={{6E0B7C2A-4F5D-4C3B-9B7E-2A1D8F5C9E41}
AppName={#Nombre}
AppVersion={#VersionApp}
AppPublisher=NEARUSTECH
DefaultDirName={autopf}\{#Nombre}
DefaultGroupName={#Nombre}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
OutputDir=..\salida
OutputBaseFilename=NearLeadsInmobiliarias-Instalador-{#VersionApp}
Compression=lzma2/ultra64
SolidCompression=yes
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Nombre}
SetupLogging=yes
CloseApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Dirs]
; Datos de la instalación. Los usuarios de la PC pueden escribir el registro de la app; pgdata y admin los
; protege el propio instalador de la base (solo administradores y el servicio).
Name: "{commonappdata}\{#Nombre}"
Name: "{commonappdata}\{#Nombre}\registro"; Permissions: users-modify

[Files]
Source: "{#Trabajo}\dist\NearLeadsInmobiliarias\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Trabajo}\pgsql\*"; DestDir: "{app}\pgsql"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#Nombre}"; Filename: "{app}\{#Exe}"
Name: "{group}\Desinstalar {#Nombre}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#Nombre}"; Filename: "{app}\{#Exe}"; Tasks: escritorio

[Run]
Filename: "{app}\{#Exe}"; Description: "Abrir {#Nombre}"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#Exe} /F"; Flags: runhidden; RunOnceId: "CerrarApp"
Filename: "{app}\{#Exe}"; Parameters: "--quitar-bd --pg ""{app}\pgsql"""; Flags: runhidden waituntilterminated; RunOnceId: "QuitarBD"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#ReglaFirewall}"""; Flags: runhidden; RunOnceId: "QuitarFirewall"

[Code]
function Ejecutar(const Archivo, Parametros: String): Integer;
var
  Codigo: Integer;
begin
  if not Exec(Archivo, Parametros, '', SW_HIDE, ewWaitUntilTerminated, Codigo) then
    Codigo := -1;
  Result := Codigo;
end;

// Antes de copiar: cerrar la app y detener el servicio de la base (sus DLL están en uso si ya estaba instalada).
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I: Integer;
begin
  Result := '';
  Ejecutar(ExpandConstant('{sys}\taskkill.exe'), '/IM {#Exe} /F');
  if Ejecutar(ExpandConstant('{sys}\sc.exe'), 'query {#Servicio}') = 0 then
  begin
    Ejecutar(ExpandConstant('{sys}\sc.exe'), 'stop {#Servicio}');
    for I := 1 to 30 do
    begin
      if Ejecutar(ExpandConstant('{cmd}'), '/C sc.exe query {#Servicio} | find "STOPPED"') = 0 then
        Break;
      Sleep(1000);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Codigo: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Instalando y preparando la base de datos (puede tardar un minuto)...';
    Codigo := Ejecutar(ExpandConstant('{app}\{#Exe}'),
      ExpandConstant('--instalar-bd --pg "{app}\pgsql" --puerto {#PuertoBD}'));
    if Codigo <> 0 then
      SuppressibleMsgBox('No se pudo preparar la base de datos (código ' + IntToStr(Codigo) + ').' + #13#10 +
        'Revisa el registro en ' + ExpandConstant('{commonappdata}\{#Nombre}\registro\instalacion.log') + #13#10 +
        'y vuelve a ejecutar el instalador para reintentar.', mbError, MB_OK, IDOK);
    WizardForm.StatusLabel.Caption := 'Configurando el Firewall de Windows...';
    Ejecutar(ExpandConstant('{sys}\netsh.exe'),
      'advfirewall firewall delete rule name="{#ReglaFirewall}"');
    Ejecutar(ExpandConstant('{sys}\netsh.exe'),
      ExpandConstant('advfirewall firewall add rule name="{#ReglaFirewall}" dir=in action=allow ' +
        'program="{app}\{#Exe}" profile=private enable=yes'));
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if SuppressibleMsgBox('¿Borrar también los datos de NearLeads Inmobiliarias?' + #13#10 + #13#10 +
      'Se perderán la base de datos (prospectos, inmuebles, citas, contactos y tareas) y la configuración. ' +
      'Esto no se puede deshacer.' + #13#10 + #13#10 +
      'Elige «No» para conservarlos y recuperarlos si vuelves a instalar.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      DelTree(ExpandConstant('{commonappdata}\{#Nombre}'), True, True, True);
  end;
end;
