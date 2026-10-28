; Установщик Windows для «Оплаты — Строймаркет» (Inno Setup 6).
;
; Сборка (на Windows):
;     ISCC.exe /DMyAppVersion=0.16.0 packaging\windows\installer.iss
;
; Результат: installer\Oplata-<версия>-Setup.exe
;
; Версия передаётся ключом /D, чтобы не дублировать её в двух местах:
; источник истины — src/oplata/__init__.py (читает scripts/write_version_info.py
; и GitHub Actions).
;
; Перед сборкой установщика должна быть готова folder-сборка PyInstaller:
;     pyinstaller --noconfirm --clean oplata.spec   (OPLATA_PACKAGE_MODE=folder)
; В CI она копируется в package-folder\Oplata (папка dist к этому моменту
; уже удалена), поэтому файлы берутся оттуда; при ручной сборке можно
; указывать dist\Oplata, если папка ещё на месте.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Оплаты — Строймаркет"
#define MyAppExeName "Oplata.exe"
#define MyAppPublisher "Строймаркет"
#define MyAppURL "https://github.com/slchumak75-sketch/oplata-2"
; Один и тот же AppId нельзя менять между версиями: иначе новая версия
; встанет рядом со старой, а не обновит её.
#define MyAppId "{{7B4C1E9A-5D23-4F81-9A6C-2E8D0F31A5B7}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName}: учёт поставок, долгов и оплат
DefaultDirName={userpf}\Oplata
DefaultGroupName={#MyAppName}
; По умолчанию ставим без прав администратора (в папку пользователя).
; При запуске от администратора будет предложено поставить для всех.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\installer
OutputBaseFilename=Oplata-{#MyAppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Данные программы (data.json) лежат в %APPDATA%\Oplata и установкой
; не затрагиваются: при обновлении и удалении они сохраняются.
UninstallDisplayName={#MyAppName} {#MyAppVersion}
DisableDirPage=auto
DisableProgramGroupPage=auto
ArchitecturesInstallIn64BitMode=x64
WizardResizable=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Вся folder-сборка PyInstaller: EXE, DLL и служебные файлы _internal.
Source: "..\..\package-folder\Oplata\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\START_HERE_RU.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
