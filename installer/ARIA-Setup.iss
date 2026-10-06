; ARIA-Setup.iss — Inno Setup 6 script for the A.R.I.A. Windows installer.
;
; Build the one-folder bundle first (on a Windows machine, from the repo root):
;     python installer\build_installer.py
; then compile this script with Inno Setup 6 -> installer\Output\ARIA-Setup-<ver>.exe
;
; Install model: per-user (no admin needed). Installs to
; %LOCALAPPDATA%\Programs\ARIA, drops a Start Menu shortcut + desktop icon,
; and clears the first-run flag so the wizard fires on first launch.

#define MyAppName "A.R.I.A."
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Alek Berger"
#define MyAppExeName "ARIA.exe"

[Setup]
AppId={{3F1A9C2E-7B4D-4E8A-9F0C-ARIAULTIMATE01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ARIA
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=ARIA-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
; Keep user data (~/ARIA) on uninstall — it holds memory, schedules, keys state.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[InstallDelete]
; First-run flag: force the wizard on first launch after a (re)install.
; The wizard is skippable ("skip" / Ctrl+C), so upgrades just re-verify keys.
Type: files; Name: "{userprofile}\ARIA\.first_run_done"

[Files]
Source: "..\dist\ARIA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
