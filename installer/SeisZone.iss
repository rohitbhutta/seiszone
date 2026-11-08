; SeisZone installer (Inno Setup 6).
; Build: python installer\build.py   (PyInstaller -> build\dist\SeisZone\, then this script)
;    or: ISCC.exe installer\SeisZone.iss  after PyInstaller has built build\dist\SeisZone\
; Silent: SeisZoneSetup.exe /VERYSILENT /SUPPRESSMSGBOXES [/TASKS="desktopicon"]
;
; Administrator rights are needed only to write to Program Files and to add the one Windows
; Firewall rule. SeisZone itself always runs as the normal user. Recordings, exports, settings
; and logs live in the user's own folders and are never touched by setup or uninstall.

#ifndef AppVer
  #define AppVer "1.0.0"
#endif
#define AppName "SeisZone"
#define Exe "SeisZone.exe"

[Setup]
AppId={{37295E38-5357-4E08-9810-1EEB96AED4F2}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=SeisZone
VersionInfoVersion={#AppVer}
VersionInfoCompany=SeisZone
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} - Setup
DefaultDirName={autopf}\SeisZone
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\build\dist
OutputBaseFilename=SeisZoneSetup
SetupIconFile=..\assets\seiszone.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#Exe}
; a running SeisZone is closed by [Code] below (with a message), not by the Restart Manager
CloseApplications=no
SetupLogging=yes
; the only per-user entry is the clean-up of the "Start with Windows" value (see [Registry])
UsedUserAreasWarning=no

[Messages]
WelcomeLabel2=This installs [name/ver] on your computer.%n%nSeisZone shows, records and shares live seismic data from Guralp Fortimus, Kelunji Gecko and other SeedLink instruments.%n%nSetup also adds one Windows Firewall rule so that a Kelunji Gecko (port 58772) and other programs (SeisZone SeedLink server) can connect to this PC.
ConfirmUninstall=Remove %1 from this computer?%n%nYour recordings, exports, settings and log files are NOT deleted. They stay in Documents\SeisZone, AppData\Roaming\SeisZone and AppData\Local\SeisZone.
UninstalledAll=%1 was removed from your computer.%n%nYour recordings, exports, settings and log files were kept.

[Tasks]
Name: desktopicon; Description: "Create a desktop icon"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; program library of an older version: replaced as a whole so no stale module is left behind
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\build\dist\SeisZone\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#Exe}"; WorkingDir: "{app}"; Comment: "SeisZone - live seismic data"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#Exe}"; WorkingDir: "{app}"; Comment: "SeisZone - live seismic data"; Tasks: desktopicon

[Registry]
; "Start with Windows" is switched on and off inside SeisZone (Settings). Only remove the value at
; uninstall so Windows does not try to start a program that is no longer there.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "SeisZone"; Flags: uninsdeletevalue dontcreatekey

[Run]
; one inbound rule for the program covers every port it listens on: the Gecko port (58772) and
; the SeisZone SeedLink server (18010), also after the user changes them in Settings
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SeisZone"""; Flags: runhidden waituntilterminated; StatusMsg: "Adding the Windows Firewall rule..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SeisZone"" dir=in action=allow program=""{app}\{#Exe}"" enable=yes"; Flags: runhidden waituntilterminated; StatusMsg: "Adding the Windows Firewall rule..."
; runasoriginaluser: SeisZone must run with the normal user's rights, not the installer's
Filename: "{app}\{#Exe}"; Description: "Launch SeisZone"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SeisZone"""; Flags: runhidden waituntilterminated; RunOnceId: "DeleteFirewallRule"

[Code]
{ True when a SeisZone.exe process exists (any user). find.exe exits 0 when the name is listed. }
function SeisZoneRunning: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
                 '/C tasklist /FI "IMAGENAME eq {#Exe}" /NH | find /I "{#Exe}" >NUL',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

{ Ask SeisZone to close (a plain taskkill: SeisZone takes it as "end now" and stops its links,
  listeners and recorder cleanly), give it up to 10 s, then end it for sure. Active recordings
  are kept by SeisZone and continue when it starts again. }
procedure CloseSeisZone;
var
  Code, I: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#Exe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  for I := 1 to 20 do
  begin
    if not SeisZoneRunning then Exit;
    Sleep(500);
  end;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#Exe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
end;

{ False = the user cancelled: SeisZone keeps running and nothing is changed }
function AskAndClose(Detail: String): Boolean;
begin
  Result := True;
  if not SeisZoneRunning then Exit;
  if SuppressibleMsgBox('SeisZone is running and will be closed.' + #13#10#13#10 + Detail,
                        mbInformation, MB_OKCANCEL, IDOK) <> IDOK then
  begin
    Result := False;
    Exit;
  end;
  CloseSeisZone;
end;

function InitializeSetup: Boolean;
begin
  Result := AskAndClose('Recordings that are running continue automatically when SeisZone starts again.');
end;

function InitializeUninstall: Boolean;
begin
  Result := AskAndClose('Recordings stop now. All files already recorded, your settings and the logs are kept.');
end;
