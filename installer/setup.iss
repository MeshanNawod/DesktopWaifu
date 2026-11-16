; Inno Setup script: builds build\installer\DesktopWaifu-Setup.exe from the PyInstaller folder build\dist\DesktopWaifu.
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\setup.iss
#define AppName "DesktopWaifu"
#define AppVersion "1.0.0"
#define AppPublisher "MeshanNawod"
#define AppURL "https://github.com/MeshanNawod/DesktopWaifu"

[Setup]
AppId={{6B1F3E2A-9C4D-4E8B-A7F1-5D2C8E9B0A43}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\build\installer
OutputBaseFilename=DesktopWaifu-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\DesktopWaifu.exe
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
WizardSizePercent=110
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\build\dist\DesktopWaifu\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\DesktopWaifu.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DesktopWaifu.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\DesktopWaifu.exe"; Tasks: startup

[Run]
Filename: "{app}\DesktopWaifu.exe"; Description: "Say hi to her now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM DesktopWaifu.exe /F"; Flags: runhidden; RunOnceId: "StopApp"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nA cute anime companion who lives on your taskbar.%n%nYour settings and memories stay in your own AppData folder on this PC.
