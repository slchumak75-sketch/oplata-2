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

[InstallDelete]
; v0.88.1: прежняя версия могла оставить файлы, которых в новой сборке
; уже нет, — и они ломают запуск. Самая дорогая ошибка этого вида:
; «DLL load failed ... Не найдена указанная процедура» — pyside6.abi3.dll
; от одной версии Qt рядом с Qt6Core.dll от другой (в 6.11.3 Qt удалила
; символы QtPrivate_6_11_2). Поэтому папку программы чистим целиком,
; КРОМЕ данных пользователя: они лежат отдельно (в %APPDATA% или на
; Google Диске), в {app} их не бывает. Выгрузки Excel лежат в подпапке
; «Оплата база» — её не трогаем.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\PySide6"
Type: files; Name: "{app}\Oplata.exe"
Type: files; Name: "{app}\*.pyd"
Type: files; Name: "{app}\*.dll"

[Files]
; Папка сборки целиком: Oplata.exe, _internal с DLL, assets.
; ignoreversion — ставить файлы ВСЕГДА, даже если версия совпадает:
; shiboken6.abi3.dll и QtCore.pyd не несут данных о версии, и без этого
; флага Setup мог оставить старый файл рядом с новым (см. v0.88.1).
Source: "{#BuildDir}\Oplata\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion
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

[Code]
{ v0.88.1: страховка от «Не найдена указанная процедура».

  Если в папке программы остались DLL от прежней версии (например,
  файл был занят запущенной программой в момент [InstallDelete]),
  Qt загрузит несовместимую библиотеку и упадёт с той же ошибкой
  «Не найдена указанная процедура». Поэтому после установки убираем
  то, чего в сборке быть не должно: папку PySide6 в корне программы
  (осталась от распаковки ZIP старше v0.87.0) и .dll в корне — все
  библиотеки живут в {app}\_internal. Молча: пользователю тут
  решать нечего. }

procedure CleanStaleFiles();
var
  FindRec: TFindRec;
  Count: Integer;
begin
  Count := 0;
  { Остатки от прежних версий Qt: папка PySide6 в корне программы
    (до v0.87.0 программа ставилась распаковкой ZIP). }
  if DirExists(ExpandConstant('{app}\PySide6')) then
  begin
    DelTree(ExpandConstant('{app}\PySide6'), True, True, True);
    Count := Count + 1;
  end;
  { Лишние .dll/.pyd в корне программы: их в сборке быть не должно —
    все библиотеки живут в _internal. }
  if FindFirst(ExpandConstant('{app}\*.dll'), FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') and
           ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0) then
        begin
          DeleteFile(ExpandConstant('{app}\' + FindRec.Name));
          Count := Count + 1;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  Log(Format('v0.88.1: удалено посторонних файлов: %d', [Count]));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    CleanStaleFiles();
end;

[UninstallDelete]
; Данные пользователя НЕ удаляем: они лежат в %APPDATA%\OplataStroymarket
; или на Google Диске, а не в папке программы.
Type: filesandordirs; Name: "{app}\_internal"
