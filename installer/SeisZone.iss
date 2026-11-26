; SeismoVerse installer (Inno Setup 6). Until 1.9.0 the program was named SeisZone (SeisZone.exe);
; this setup updates such an installation in place (same AppId and folder), removes the old
; program file and shortcuts, and closes either program before installing.
; Build: python installer\build.py   (PyInstaller -> build\dist\SeismoVerse\, then this script)
;    or: ISCC.exe installer\SeisZone.iss  after PyInstaller has built build\dist\SeismoVerse\
; Silent: SeismoVerseSetup.exe /VERYSILENT /SUPPRESSMSGBOXES [/MERGETASKS="desktopicon"]
;   (/MERGETASKS keeps the tasks that are on by default; /TASKS= would switch them off)
;
; Administrator rights are needed only to write to Program Files, to add the one Windows
; Firewall rule and (task "stopoldservice") to stop and disable the old Gecko SeedLink service,
; which holds the Gecko port 58772 and its own SeedLink port 18010 (SeismoVerse's SeedLink server
; uses 18000). That service is not removed: it
; can be switched on again in Windows Services. SeismoVerse itself always runs as the normal user.
; Recordings, exports, settings and logs live in the user's own folders and are never touched
; by setup or uninstall (they keep the folder name SeisZone).

#ifndef AppVer
  #define AppVer "2.3.0"
#endif
#define AppName "SeismoVerse"
#define Exe "SeismoVerse.exe"
#define OldName "SeisZone"
#define OldExe "SeisZone.exe"
#define OldService "GeckoSeedLink"

[Setup]
AppId={{37295E38-5357-4E08-9810-1EEB96AED4F2}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=SeismoVerse
VersionInfoVersion={#AppVer}
VersionInfoCompany=SeismoVerse
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} - Setup
; an earlier SeisZone installation is updated in its own folder (Inno uses the previous folder)
DefaultDirName={autopf}\SeismoVerse
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\build\dist
OutputBaseFilename=SeismoVerseSetup
SetupIconFile=..\assets\seiszone.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#Exe}
; a running SeismoVerse (or the old SeisZone) is closed by [Code] below (with a message), not by the Restart Manager
CloseApplications=no
SetupLogging=yes
; the only per-user entry is the clean-up of the "Start with Windows" value (see [Registry])
UsedUserAreasWarning=no

[Messages]
WelcomeLabel2=This installs [name/ver] on your computer.%n%nSeismoVerse shows, records and shares live seismic data from Guralp Fortimus, Kelunji Gecko and other SeedLink instruments.%n%nSetup also adds one Windows Firewall rule so that a Kelunji Gecko (port 58772) and other programs (SeismoVerse SeedLink server) can connect to this PC.
ConfirmUninstall=Remove %1 from this computer?%n%nA running SeismoVerse is closed (recordings stop). Your recordings, exports, settings and log files are NOT deleted. They stay in Documents\SeismoVerse, AppData\Roaming\SeismoVerse and AppData\Local\SeismoVerse (on a PC updated from an earlier version: in the folders it used before).
UninstalledAll=%1 was removed from your computer.%n%nYour recordings, exports, settings and log files were kept.

[Tasks]
Name: desktopicon; Description: "Create a desktop icon"; GroupDescription: "Shortcuts:"; Flags: unchecked
; on by default, offered only when the old Gecko SeedLink service is installed on this PC
Name: stopoldservice; Description: "Stop and disable the old Gecko SeedLink service, so your Gecko recorder connects to SeismoVerse (frees ports 58772 and 18010; the SeismoVerse SeedLink server uses port 18000)"; GroupDescription: "Old Gecko SeedLink service:"; Check: OldServiceInstalled

[InstallDelete]
; program library of an older version: replaced as a whole so no stale module is left behind
Type: filesandordirs; Name: "{app}\_internal"
; the program file and shortcuts of the old name (SeisZone 1.9.0 and older)
Type: files; Name: "{app}\{#OldExe}"
Type: files; Name: "{autoprograms}\{#OldName}.lnk"
Type: files; Name: "{autodesktop}\{#OldName}.lnk"

[Files]
Source: "..\build\dist\SeismoVerse\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#Exe}"; WorkingDir: "{app}"; Comment: "SeismoVerse - live seismic data"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#Exe}"; WorkingDir: "{app}"; Comment: "SeismoVerse - live seismic data"; Tasks: desktopicon

[Registry]
; "Start with Windows" is switched on and off inside SeismoVerse (Settings); the value keeps the
; old name "SeisZone" (ui/app.py RUN_VALUE). Only remove the value at
; uninstall so Windows does not try to start a program that is no longer there.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "SeisZone"; Flags: uninsdeletevalue dontcreatekey

[Run]
; one inbound rule for the program covers every port it listens on: the Gecko port (58772) and
; the SeismoVerse SeedLink server (18000), also after the user changes them in Settings
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#OldName}"""; Flags: runhidden waituntilterminated; StatusMsg: "Adding the Windows Firewall rule..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#AppName}"""; Flags: runhidden waituntilterminated; StatusMsg: "Adding the Windows Firewall rule..."
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#AppName}"" dir=in action=allow program=""{app}\{#Exe}"" enable=yes"; Flags: runhidden waituntilterminated; StatusMsg: "Adding the Windows Firewall rule..."
; runasoriginaluser: SeismoVerse must run with the normal user's rights, not the installer's
Filename: "{app}\{#Exe}"; Description: "Launch SeismoVerse"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#AppName}"""; Flags: runhidden waituntilterminated; RunOnceId: "DeleteFirewallRule"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#OldName}"""; Flags: runhidden waituntilterminated; RunOnceId: "DeleteOldFirewallRule"

[Code]
{ True when a process of this program file exists (any user). find.exe exits 0 when it is listed. }
function ExeRunning(const Name: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
                 '/C tasklist /FI "IMAGENAME eq ' + Name + '" /NH | find /I "' + Name + '" >NUL',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

{ True when SeismoVerse.exe or the old SeisZone.exe runs. }
function SeisZoneRunning: Boolean;
begin
  Result := ExeRunning('{#Exe}') or ExeRunning('{#OldExe}');
end;

{ Ask SeismoVerse (and the old SeisZone) to close (a plain taskkill: it takes it as "end now" and stops its links,
  listeners and recorder cleanly), give it up to 10 s, then end it for sure. Active recordings
  are kept and continue when it starts again. }
procedure CloseSeisZone;
var
  Code, I: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#Exe} /IM {#OldExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  for I := 1 to 20 do
  begin
    if not SeisZoneRunning then Exit;
    Sleep(500);
  end;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#Exe} /IM {#OldExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(1000);
end;

{ True when the old Gecko SeedLink service is installed (its key under Services exists). }
function OldServiceInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\{#OldService}');
end;

{ Stop the old service and keep it off. A clean stop is not a failure, so its "restart on
  failure" actions do not start it again; it stays installed and can be switched on again in
  Windows Services. SeismoVerse opens the freed ports by itself within a few seconds. }
procedure StopOldService;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop {#OldService}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Log(Format('sc stop {#OldService}: exit code %d', [Code]));
  Exec(ExpandConstant('{sys}\sc.exe'), 'config {#OldService} start= disabled', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Log(Format('sc config {#OldService} start= disabled: exit code %d', [Code]));
end;

var
  ClosedBySetup: Boolean;

{ SeismoVerse is closed only once the user has committed (Install clicked / removal confirmed):
  cancelling the wizard earlier leaves it running. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not SeisZoneRunning then Exit;
  if SuppressibleMsgBox('{#AppName} is running and will be closed.' + #13#10#13#10 +
                        'Recordings that are running continue automatically when {#AppName} starts again.',
                        mbInformation, MB_OKCANCEL, IDOK) <> IDOK then
  begin
    Result := '{#AppName} must be closed to install this version. It keeps running; click Cancel to leave Setup.';
    Exit;
  end;
  CloseSeisZone;
  ClosedBySetup := True;
end;

{ The old Gecko SeedLink service is stopped first (task "stopoldservice"), so SeismoVerse finds
  the Gecko and SeedLink ports free. A silent update brings SeismoVerse back (as the normal user,
  in the tray); an interactive one offers "Launch SeismoVerse" on its last page. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('stopoldservice') then
    StopOldService;
  { /SHOWAPP=1 (an update started from SeismoVerse's own update window): open it in its window }
  if (CurStep = ssPostInstall) and ClosedBySetup and WizardSilent then
  begin
    if ExpandConstant('{param:showapp|0}') = '1' then
      ExecAsOriginalUser(ExpandConstant('{app}\{#Exe}'), '', '', SW_SHOWNORMAL, ewNoWait, Code)
    else
      ExecAsOriginalUser(ExpandConstant('{app}\{#Exe}'), '--minimized', '', SW_SHOWNORMAL, ewNoWait, Code);
  end;
end;

{ After "Remove SeismoVerse from this computer?" was answered Yes (the question says it closes). }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usUninstall) and SeisZoneRunning then
    CloseSeisZone;
end;
