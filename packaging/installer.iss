; Inno Setup Script for NGTeco Clock Manager
; Production Windows Installer

#ifndef MyAppVersion
#define MyAppVersion "0.14.0"
#endif

#define MyAppName "NGTeco Clock Manager"
#define MyAppPublisher "NGTeco Clock Manager Project"
#define MyAppExeName "clockmanager.exe"
#define MyAppAssocName "NGTeco Clock Manager"

[Setup]
; Unique application GUID for update detection and clean uninstall
AppId={{A5C2278C-8F4C-4BF0-96A7-29D2A3B3E52F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppVerName={#MyAppName} {#MyAppVersion}

; Per-user non-elevated install by default; allows standard users to install without UAC admin prompt.
; Users who want all-users install can specify it via dialog or run setup with administrative privileges.
DefaultDirName={localappdata}\Programs\{#MyAppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

DefaultGroupName={#MyAppName}
AllowNoIcons=yes

; The application holds this mutex while running, including when it is hidden
; in the notification area (clockmanager.windows.INSTALLER_MUTEX_NAME). Setup
; and the uninstaller refuse to replace files under a running copy.
AppMutex=NGTecoClockManagerRunning

; Installer output settings
OutputDir=..\dist\installer
OutputBaseFilename=NGTecoClockManager-Setup-{#MyAppVersion}
SetupIconFile=assets\clockmanager.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

; Modern LZMA2 solid compression
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; 64-bit target
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; A copy hidden in the notification area is easy to miss; say where to look.
SetupAppRunningError=%1 is still running. It may be hidden in the notification area next to the Windows clock.%n%nRight-click its icon there and choose Quit, then click OK to continue, or Cancel to exit.
UninstallAppRunningError=%1 is still running. It may be hidden in the notification area next to the Windows clock.%n%nRight-click its icon there and choose Quit, then click OK to continue, or Cancel to exit.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start {#MyAppName} automatically when Windows starts"; GroupDescription: "Windows Startup:"; Flags: unchecked

[Files]
; Main executable and bundled runtime files from PyInstaller distribution
Source: "..\dist\clockmanager\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\clockmanager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu shortcuts
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; Optional Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Optional automatic launch at Windows login
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NGTecoClockManager"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
; Option to launch the application immediately after installation completes
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Data preservation guarantee:
// The application database, configuration, backups, and audit logs are stored in:
//   %LOCALAPPDATA%\NGTecoClockManager
// The application binary files are installed in:
//   %LOCALAPPDATA%\Programs\NGTeco Clock Manager
//
// During uninstall or upgrade, Inno Setup cleanly removes the binaries in {app},
// but leaves the data directory completely untouched, guaranteeing zero data loss.

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Informational note: Inno Setup uninstalls files cleanly from {app}.
  end;
end;
