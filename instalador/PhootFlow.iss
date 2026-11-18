; Instalador de PhootFlow (Inno Setup 6). Lo compila el build de GitHub:
;   iscc /DMyVersion=0.8.3 /DSrcDir=..\dist\PhootFlow /DOutDir=..\dist instalador\PhootFlow.iss
; Instala por usuario (sin pedir administrador) y deja desinstalador y accesos directos.
; La app se actualiza sola: descarga el PhootFlow-Setup.exe nuevo y lo ejecuta en silencio (updater.py, modo «setup»).

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif
#ifndef SrcDir
  #define SrcDir "..\dist\PhootFlow"
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{33EBF9D9-B515-416A-B848-A93AD3278370}
AppName=PhootFlow
AppVersion={#MyVersion}
AppVerName=PhootFlow {#MyVersion}
AppPublisher=David (PhootFlow)
DefaultDirName={localappdata}\Programs\PhootFlowApp
DisableDirPage=yes
DefaultGroupName=PhootFlow
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=PhootFlow-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\PhootFlow.exe
UninstallDisplayName=PhootFlow
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; versión anterior de las librerías: se borra antes de copiar la nueva para no mezclar archivos
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\PhootFlow"; Filename: "{app}\PhootFlow.exe"
Name: "{autodesktop}\PhootFlow"; Filename: "{app}\PhootFlow.exe"; Tasks: escritorio

[Run]
Filename: "{app}\PhootFlow.exe"; Description: "Abrir PhootFlow"; Flags: nowait postinstall skipifsilent
; actualización automática (instalación silenciosa lanzada por la propia app): volver a abrirla
Filename: "{app}\PhootFlow.exe"; Parameters: "{param:ARGS|}"; Flags: nowait skipifnotsilent; Check: Relanzar

[Code]
function Relanzar: Boolean;
begin
  Result := ExpandConstant('{param:RELANZAR|0}') = '1';
end;
