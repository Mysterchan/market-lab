; Inno Setup script for the Windows installer; tools/build_desktop.py passes the defines.
; Per-user install: no administrator rights, and data stays in the user's profile.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1C2A8E-4B7D-4E21-9C3A-5D8E1F0B7A42}
AppName=Market Lab
AppVersion={#AppVersion}
AppPublisher=Market Lab contributors
DefaultDirName={localappdata}\Programs\Market Lab
DefaultGroupName=Market Lab
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconFile}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Market Lab"; Filename: "{app}\Market Lab.exe"
Name: "{userdesktop}\Market Lab"; Filename: "{app}\Market Lab.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Market Lab.exe"; Description: "Start Market Lab"; Flags: nowait postinstall skipifsilent
