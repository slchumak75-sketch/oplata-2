@echo off
chcp 866 >nul
rem ===========================================================================
rem  Запуск программы "Оплаты" с общей папкой Google Диска
rem
rem  Все четыре компьютера смотрят в одну папку на Диске: администратор
rem  меняет данные, остальные видят изменения (подробности - README,
rem  раздел "Синхронизация между компьютерами (Google Диск)").
rem
rem  Папка ищется автоматически. Если она не нашлась, укажите путь
rem  вручную - раскомментируйте строку ниже (уберите "rem" в начале).
rem ===========================================================================

rem set "OPLATA_DATA_DIR=C:\Users\Имя\Google Диск\Oplata"

if not defined OPLATA_DATA_DIR call :find_drive

if not defined OPLATA_DATA_DIR (
  echo.
  echo   Не удалось найти папку Google Диска.
  echo   Укажите путь вручную: откройте этот файл в Блокноте и уберите
  echo   "rem" в начале строки с OPLATA_DATA_DIR вверху файла.
  echo.
  pause
  exit /b 1
)

if not exist "%OPLATA_DATA_DIR%" mkdir "%OPLATA_DATA_DIR%"
echo   Данные: %OPLATA_DATA_DIR%

if exist "%~dp0Oplata\Oplata.exe" (
  start "" "%~dp0Oplata\Oplata.exe"
) else if exist "%~dp0Oplata.exe" (
  start "" "%~dp0Oplata.exe"
) else (
  echo.
  echo   Рядом с этим файлом не найден Oplata.exe.
  echo   Положите файл в ту же папку, где лежит программа.
  echo.
  pause
  exit /b 1
)
exit /b 0

rem ---------------------------------------------------------------------------
:find_drive
rem  Стандартные расположения папки Google Диска. Первая же существующая
rem  и становится общей папкой данных (в ней создаётся подпапка Oplata).
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
