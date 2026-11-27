#define AppName "AI Limits Monitor"
#define AppExe AppName + ".exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
AppId={{C809A50B-4A52-467E-BF77-426185D1FEA9}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=onetone
AppPublisherURL=https://github.com/antonnevm-cloud/ai-limits-monitor
AppSupportURL=https://github.com/antonnevm-cloud/ai-limits-monitor
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=onetone
VersionInfoProductName={#AppName}
VersionInfoCopyright=Copyright 2026 onetone
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
; Windows 11 на ARM64 запускает сборку x64 через эмуляцию.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#BuildOutput}
OutputBaseFilename=AI-Limits-Monitor-{#AppVersion}-windows-x64-setup
SetupIconFile={#SourcePath}\..\assets\icon.ico
LicenseFile={#SourcePath}\..\LICENSE
; Уведомление о Qt под LGPL v3 и остальных компонентах сборки.
InfoAfterFile={#SourcePath}\licenses\README.md
UninstallDisplayIcon={app}\{#AppExe}
Uninstallable=not PortableInstall
UsePreviousAppDir=not PortableInstall
; Виджет закрывается штатно через Restart Manager, зависший процесс завершается принудительно.
; Перезапуск после обновления выполняет раздел [Run].
CloseApplications=force
RestartApplications=no
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

[InstallDelete]
; Файлы прежней версии, которых нет в новой, не должны оставаться рядом с программой.
Type: filesandordirs; Name: "{app}\_internal"
; Обычная установка в папку переносимой копии хранит данные в профиле пользователя.
Type: files; Name: "{app}\portable.txt"; Check: not PortableInstall

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Check: not PortableInstall
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; Check: not PortableInstall

[Registry]
; Тот же параметр включает и выключает переключатель автозапуска в программе.
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"" --autostart"; Tasks: autostart; Check: not PortableInstall

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent; Check: not PortableInstall
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: RestartAfterSilentUpdate

[UninstallRun]
; Убирает команды строки состояния монитора из настроек Claude Code и Antigravity, пока программа на месте.
Filename: "{app}\{#AppExe}"; Parameters: "--uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveIntegrations"

[Code]
var
  WidgetWasRunning: Boolean;

function PortableInstall: Boolean;
begin
  Result := ExpandConstant('{param:portable|0}') = '1';
end;

function InFolder(Path, Folder: String): Boolean;
begin
  Result := Pos(AddBackslash(Lowercase(Folder)), Lowercase(Path)) = 1;
end;

function IsServiceMode(Command: String): Boolean;
begin
  Command := Lowercase(Command);
  Result := (Pos('--backend', Command) > 0) or (Pos('-statusline', Command) > 0) or
            (Pos('--smoke-test', Command) > 0) or (Pos('--version', Command) > 0) or
            (Pos('--uninstall', Command) > 0);
end;

{ Завершает процессы программы из каталога Folder. Виджет без окна Restart Manager закрыть не может:
  отдельный сборщик и обработчики строки состояния завершаются здесь. Виджеты закрываются только при
  удалении, при обновлении их штатно закрывает Restart Manager. }
procedure StopProgram(Folder: String; Widgets: Boolean);
var
  Locator, Service, Items, Item: Variant;
  I: Integer;
  Path, Command: String;
begin
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Items := Service.ExecQuery('SELECT ProcessId, ExecutablePath, CommandLine FROM Win32_Process ' +
                               'WHERE Name = ''{#AppExe}''');
    for I := 0 to Items.Count - 1 do
    begin
      Item := Items.ItemIndex(I);
      Path := '';
      if not VarIsNull(Item.ExecutablePath) then
        Path := Item.ExecutablePath;
      if (Path <> '') and InFolder(Path, Folder) then
      begin
        Command := '';
        if not VarIsNull(Item.CommandLine) then
          Command := Item.CommandLine;
        if not IsServiceMode(Command) then
          WidgetWasRunning := True;
        if Widgets or IsServiceMode(Command) then
        begin
          Log('Завершение процесса ' + IntToStr(Item.ProcessId) + ': ' + Command);
          Item.Terminate();
        end;
      end;
    end;
  except
    Log('Не удалось проверить процессы программы: ' + GetExceptionMessage);
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  { Без своей папки переносимая копия легла бы поверх обычной установки, не обновив запись о ней. }
  if PortableInstall and (ExpandConstant('{param:dir|}') = '') then
  begin
    SuppressibleMsgBox('Для переносимой установки укажи папку параметром /DIR="полный путь".',
                       mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

function RestartAfterSilentUpdate: Boolean;
begin
  Result := WizardSilent and WidgetWasRunning and not PortableInstall;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopProgram(ExpandConstant('{app}'), False);
  Sleep(500);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Shortcut: String;
begin
  if (CurStep = ssPostInstall) and PortableInstall then
    { Признак переносимой копии: настройки и история хранятся в папке data рядом с программой. }
    SaveStringToFile(ExpandConstant('{app}\portable.txt'),
                     'AI Limits Monitor portable copy: settings and history are kept in the data folder next to the program.' + #13#10,
                     False);
  if (CurStep = ssPostInstall) and not PortableInstall then
  begin
    { Прежние версии включали автозапуск ярлыком в папке автозагрузки. Выбор пользователя сохраняется. }
    Shortcut := ExpandConstant('{userstartup}\{#AppName}.lnk');
    if FileExists(Shortcut) then
    begin
      DeleteFile(Shortcut);
      RegWriteStringValue(HKCU, '{#RunKey}', '{#AppName}',
                          '"' + ExpandConstant('{app}\{#AppExe}') + '" --autostart');
    end;
  end;
end;

function InitializeUninstall: Boolean;
begin
  StopProgram(ExpandConstant('{app}'), True);
  Sleep(500);
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if RegQueryStringValue(HKCU, '{#RunKey}', '{#AppName}', Value) and
       (Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Value)) > 0) then
      RegDeleteValue(HKCU, '{#RunKey}', '{#AppName}');
    DeleteFile(ExpandConstant('{userstartup}\{#AppName}.lnk'));
  end;
end;
