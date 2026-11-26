[Setup]
AppName=Center Print Helper
AppVersion=1.0.0
AppPublisher=Center ERP
DefaultDirName={localappdata}\CenterPrintHelper
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=CenterPrintHelper-Setup
Compression=lzma
SolidCompression=yes
DisableDirPage=yes
UninstallDisplayName=Center Print Helper

[Files]
Source: "dist\CenterPrintHelper.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userstartup}\Center Print Helper"; Filename: "{app}\CenterPrintHelper.exe"

[INI]
Filename: "{userdesktop}\Center Print Helper - Setup Page.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://127.0.0.1:17653/"

[Run]
Filename: "{app}\CenterPrintHelper.exe"; Flags: nowait runhidden
Filename: "http://127.0.0.1:17653/"; Description: "Open the pairing code page"; Flags: shellexec postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/im CenterPrintHelper.exe /f"; Flags: runhidden; RunOnceId: "KillHelper"

[UninstallDelete]
Type: files; Name: "{userdesktop}\Center Print Helper - Setup Page.url"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var rc: Integer;
begin
  Exec('taskkill.exe', '/im CenterPrintHelper.exe /f', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Result := '';
end;
