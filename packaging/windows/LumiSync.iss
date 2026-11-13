; LumiSync per-user Windows installer.
;
; Built by tools/build_windows.ps1 after PyInstaller produces the onedir
; bundle in dist/LumiSync. The same installer is what the in-app updater will
; download once the release workflow starts publishing it.
;
; Version can be overridden from the build script:
;   ISCC.exe /DMyAppVersion=1.0.0 packaging\windows\LumiSync.iss
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#ifndef MyAppVersionNumeric
  #define MyAppVersionNumeric MyAppVersion
#endif

#define MyAppName "LumiSync"
#define MyAppPublisher "Minlor"
#define MyAppURL "https://github.com/Minlor/LumiSync"
#define MyAppExeName "LumiSync.exe"

[Setup]
; A stable AppId keeps upgrades and uninstalls tied to the same product.
AppId={{A3B9C1D4-6E2F-4A7B-9C3D-1F5E8A2B4C6D}
AppName={#MyAppName}
; AppVersion fills the version column in Windows "Installed apps".
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
LicenseFile=..\..\LICENSE
MinVersion=10.0
ArchitecturesAllowed=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=LumiSync-Setup
SetupIconFile=..\..\lumisync\gui\resources\icons\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersionNumeric}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start {#MyAppName} when I sign in"; GroupDescription: "Startup:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce
Name: "firewall"; Description: "Allow {#MyAppName} through Windows Firewall to find lights on your network (asks for administrator approval)"; GroupDescription: "Network:"; Flags: checkedonce

[Files]
Source: "..\..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{userprograms}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Code]
// Ask an already-running LumiSync to exit cleanly before replacing files.
// The tray icon would otherwise keep the app alive and lock the bundle.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\{#MyAppExeName}')) then
  begin
    Exec(
      ExpandConstant('{app}\{#MyAppExeName}'),
      '--quit',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );
  end;
end;

// The in-app updater runs the installer silently. It passes /launch=1 so the
// upgraded app comes back after the file swap; plain silent deployments do not.
function ShouldLaunchAfterSilentInstall: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:launch|0}') = '1');
end;

const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunValue = 'LumiSync';

procedure ApplyAutostartChoice;
var
  Command: String;
begin
  // In-app updates must preserve the user's current auto-start choice.
  if ExpandConstant('{param:update|0}') = '1' then
    Exit;
  if IsTaskSelected('autostart') then
  begin
    // Sign-in launches go straight to the tray instead of opening the window.
    Command := '"' + ExpandConstant('{app}\{#MyAppExeName}') + '" --minimized';
    RegWriteStringValue(HKCU, RunKey, RunValue, Command);
  end
  else
    RegDeleteValue(HKCU, RunKey, RunValue);
end;

// LumiSync must hear unsolicited LAN traffic: LSC/Tuya presence broadcasts
// (UDP 6667) and Govee discovery replies (UDP 4002). A per-user install adds
// no firewall rule by itself, so the "firewall" task adds one with a single
// elevated netsh call. The app's own check (Devices page, Settings) reads the
// rule by this name, and uninstall removes it.
const
  FirewallRuleName = 'LumiSync - local devices';
  FirewallRuleDescription = 'Lets LumiSync hear lights on your network (Govee LAN replies, LSC/Tuya announcements).';

function Quoted(const Value: String): String;
begin
  Result := '"' + Value + '"';
end;

// Reading rules needs no elevation; netsh exits 0 when the named rule exists.
function FirewallRuleExists: Boolean;
var
  ResultCode: Integer;
begin
  Result :=
    Exec(
      ExpandConstant('{sys}\netsh.exe'),
      'advfirewall firewall show rule name=' + Quoted(FirewallRuleName),
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    ) and (ResultCode = 0);
end;

procedure AddFirewallRule;
var
  Exe, Netsh, Commands: String;
  ErrorCode: Integer;
begin
  if not WizardIsTaskSelected('firewall') then
    Exit;
  // Silent in-app updates must never raise a UAC prompt; the app itself
  // offers Allow if the rule is missing.
  if ExpandConstant('{param:update|0}') = '1' then
  begin
    Log('Firewall: skipped during an in-app update');
    Exit;
  end;
  if FirewallRuleExists then
  begin
    Log('Firewall: the inbound rule already exists');
    Exit;
  end;
  Exe := ExpandConstant('{app}\{#MyAppExeName}');
  Netsh := Quoted(ExpandConstant('{sys}\netsh.exe'));
  // Delete every inbound rule for this program first: that also clears the
  // block rules Windows adds when someone declines its own prompt. Then
  // allow it on private and domain networks, never public ones.
  Commands :=
    Netsh + ' advfirewall firewall delete rule name=all dir=in program=' + Quoted(Exe) +
    ' & ' +
    Netsh + ' advfirewall firewall add rule name=' + Quoted(FirewallRuleName) +
    ' dir=in action=allow program=' + Quoted(Exe) +
    ' enable=yes profile=private,domain protocol=any description=' + Quoted(FirewallRuleDescription);
  // [Code] instead of [Run]: declining the UAC prompt must not show an error.
  // /s makes cmd strip only the outer quotes around Commands.
  if ShellExec(
    'runas',
    ExpandConstant('{cmd}'),
    '/d /v:off /s /c ' + Quoted(Commands),
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ErrorCode
  ) then
  begin
    if FirewallRuleExists then
      Log('Firewall: added the inbound rule')
    else
      Log('Firewall: netsh ran but the inbound rule is missing');
  end
  else
    Log('Firewall: rule not added (' + SysErrorMessage(ErrorCode) + ')');
end;

// Best effort: declining the prompt leaves the rule in place.
procedure RemoveFirewallRule;
var
  ErrorCode: Integer;
begin
  if not FirewallRuleExists then
    Exit;
  if ShellExec(
    'runas',
    ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule name=' + Quoted(FirewallRuleName),
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ErrorCode
  ) then
    Log('Firewall: removed the inbound rule')
  else
    Log('Firewall: rule left in place (' + SysErrorMessage(ErrorCode) + ')');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    ApplyAutostartChoice;
    AddFirewallRule;
  end;
end;

// The tray app would otherwise keep its files locked and survive uninstall.
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\{#MyAppExeName}')) then
    Exec(
      ExpandConstant('{app}\{#MyAppExeName}'),
      '--quit',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, RunKey, RunValue);
  if CurUninstallStep = usPostUninstall then
    RemoveFirewallRule;
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: ShouldLaunchAfterSilentInstall
