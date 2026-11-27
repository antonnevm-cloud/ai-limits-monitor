#define AppName "AI Limits Monitor"

[Setup]
AppId={{C809A50B-4A52-467E-BF77-426185D1FEA9}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
OutputDir={#BuildOutput}
OutputBaseFilename=AI-Limits-Monitor-{#AppVersion}-windows-x64-setup
SetupIconFile={#SourcePath}\..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Uninstallable=not PortableInstall
UsePreviousAppDir=not PortableInstall
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked; Check: not PortableInstall
Name: "autostart"; Description: "{cm:AutoStartLabel}"; Flags: unchecked; Check: not PortableInstall

[CustomMessages]
russian.AutoStartLabel=Запускать монитор при входе в Windows
english.AutoStartLabel=Start the monitor when I sign in to Windows

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Check: not PortableInstall
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon; Check: not PortableInstall
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: autostart; Check: not PortableInstall

[Code]
function PortableInstall: Boolean;
begin
  Result := ExpandConstant('{param:portable|0}') = '1';
end;
