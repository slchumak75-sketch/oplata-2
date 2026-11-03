@echo off
chcp 65001 >nul
rem ===========================================================================
rem  Ярлык «Оплаты» и запуск программы с общей базой на Google Диске
rem
rem  Программа берёт данные из общей папки Google Диска: администратор
rem  пишет, остальные компьютеры смотрят. Подробности — README, раздел
rem  «Синхронизация данных между компьютерами (Google Диск)».
rem
rem  Если папка Диска лежит не там, где мы её ищем, укажите путь сами:
rem  откройте этот файл в Блокноте и уберите «rem» в начале строки с
rem  OPLATA_DATA_DIR ниже.
rem
rem  Файл в кодировке UTF-8 (поэтому первая команда — chcp 65001):
rem  русские буквы доезжают и до окна консоли, и до PowerShell,
rem  который делает ярлык «Оплаты».
rem ===========================================================================

rem set "OPLATA_DATA_DIR=C:\Users\Имя\Google Диск\Oplata"

if not defined OPLATA_DATA_DIR call :find_drive

if not defined OPLATA_DATA_DIR (
  echo.
  echo   Не удалось найти папку Google Диска.
  echo   Укажите путь вручную: уберите "rem" в начале строки с
  echo   OPLATA_DATA_DIR в начале этого файла и впишите свою папку.
  echo.
  pause
  exit /b 1
)

if not exist "%OPLATA_DATA_DIR%" mkdir "%OPLATA_DATA_DIR%"
echo   База: %OPLATA_DATA_DIR%

rem Ярлык «Оплаты» делаем до запуска программы: если PowerShell
rem недоступен или запрещён, шаг молча пропускается.
call :make_shortcut

if exist "%~dp0Oplata\Oplata.exe" (
  start "" "%~dp0Oplata\Oplata.exe"
) else if exist "%~dp0Oplata.exe" (
  start "" "%~dp0Oplata.exe"
) else (
  echo.
  echo   Рядом с этим файлом нет Oplata.exe.
  echo   Положите этот файл в одну папку с программой.
  echo.
  pause
  exit /b 1
)
exit /b 0

rem ---------------------------------------------------------------------------
:find_drive
rem  Ищем папку Google Диска по обычным расположениям. Внутри неё
rem  программа создаёт подпапку Oplata — там лежат файлы базы.
rem ---------------------------------------------------------------------------
for %%P in (
  "%USERPROFILE%\Google Диск"
  "%USERPROFILE%\Google Drive"
  "%USERPROFILE%\GoogleDrive"
  "%USERPROFILE%\Мой диск"
  "%USERPROFILE%\My Drive"
  "G:\Мой диск"
  "G:\My Drive"
) do (
  if exist %%~P (
    set "OPLATA_DATA_DIR=%%~P\Oplata"
    exit /b 0
  )
)
exit /b 0

rem ---------------------------------------------------------------------------
:make_shortcut
rem  Ярлык «Оплаты» — на этот же bat-файл, иконку берём из Oplata.exe
rem  (она вшита в саму программу). Кладём ярлык рядом с программой и на
rem  рабочий стол. Уже существующий ярлык не трогаем: не переписываем
rem  то, что пользователь мог сам перетащить или переименовать.
rem ---------------------------------------------------------------------------
set "LNK_NAME=Оплаты.lnk"
set "LNK_HERE=%~dp0%LNK_NAME%"
set "LNK_DESKTOP=%USERPROFILE%\Desktop\%LNK_NAME%"

set "EXE_PATH=%~dp0Oplata\Oplata.exe"
if not exist "%EXE_PATH%" set "EXE_PATH=%~dp0Oplata.exe"
if not exist "%EXE_PATH%" exit /b 0

where powershell >nul 2>nul
if errorlevel 1 exit /b 0

if exist "%LNK_HERE%" if exist "%LNK_DESKTOP%" exit /b 0

set "LNK_TARGET=%~f0"
set "LNK_WORKDIR=%~dp0"
set "LNK_DESC=Оплаты — Строймаркет"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$shell = New-Object -ComObject WScript.Shell; foreach ($path in @($env:LNK_HERE, $env:LNK_DESKTOP)) { if (-not $path) { continue }; if (Test-Path -LiteralPath $path) { continue }; $folder = Split-Path -LiteralPath $path -Parent; if (-not (Test-Path -LiteralPath $folder)) { continue }; try { $link = $shell.CreateShortcut($path); $link.TargetPath = $env:LNK_TARGET; $link.WorkingDirectory = $env:LNK_WORKDIR; $link.IconLocation = $env:EXE_PATH + ',0'; $link.Description = $env:LNK_DESC; $link.WindowStyle = 1; $link.Save() } catch { } }"
exit /b 0
