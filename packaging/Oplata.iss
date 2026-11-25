; Oplata.iss — установщик программы «Оплаты — Строймаркет» (v0.87.0).
;
; Зачем он нужен пользователю: раньше программа распространялась архивом
; ZIP, и при распаковке терялись файлы (в частности, Oplata.exe вырезал
; Defender). Установщик ставит программу целиком, сразу создаёт ярлыки и
; умеет обновлять её поверх старой версии.
;
; Требования из переписки с пользователем:
;   * установка на разных компьютерах в разных местах;
;   * права администратора НЕ нужны — ставим в профиль пользователя;
;   * обновление ставится поверх, данные (data.json в %APPDATA% или на
;     Google Диске) не трогаются;
;   * ярлык «Оплаты» запускает Oplata.exe; отдельный ярлык — запуск с
;     общей базой на Google Диске (Start-With-Google-Drive.bat).
;
; Собирается в CI: ISCC.exe /DAppVersion=0.87.0 Oplata.iss

#define AppName "Оплаты — Строймаркет"
#define AppExe "Oplata.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BuildDir
  #define BuildDir "..\package-folder"
#endif

[Setup]
AppId={{8F3A1C12-6B7E-4C71-9E44-0D37B1A2C901}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Строймаркет
DefaultDirName={localappdata}\Programs\Oplata
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Права администратора не нужны: ставим в профиль пользователя.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\installer
OutputBaseFilename=Oplata-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Программа 64-битная (PyInstaller собирает x64).
ArchitecturesInstallIn64BitMode=x64compatible
; Обновление ставится поверх: файлы заменяются, данные не трогаем.
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\{#AppExe}
DisableDirPage=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; \
    GroupDescription: "Ярлыки:"

[Files]
; Папка сборки целиком: Oplata.exe, _internal с DLL, assets.
Source: "{#BuildDir}\Oplata\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
Source: "{#BuildDir}\START_HERE_RU.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildDir}\Start-With-Google-Drive.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Оплаты — общая база на Google Диске"; \
    Filename: "{app}\Start-With-Google-Drive.bat"; \
    IconFilename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Оплаты"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; После установки сразу запускаем программу — в том числе после
; обновления (её закрыл установщик по команде из окна «Обновить»).
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Данные пользователя НЕ удаляем: они лежат в %APPDATA%\OplataStroymarket
; или на Google Диске, а не в папке программы.
Type: filesandordirs; Name: "{app}\_internal"
