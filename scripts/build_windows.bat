@echo off
rem Локальная сборка Oplata.exe под Windows (то же самое делает GitHub Actions).
rem Требования: Python 3.11+ в PATH. Запуск из корня репозитория:
rem   scripts\build_windows.bat          -> рекомендуемый вариант папкой
rem   scripts\build_windows.bat folder   -> dist\Oplata\Oplata.exe (+ DLL рядом)
rem   scripts\build_windows.bat single   -> запасной один dist\Oplata.exe
setlocal
cd /d "%~dp0\.."

set "OPLATA_PACKAGE_MODE=%~1"
if "%OPLATA_PACKAGE_MODE%"=="" set "OPLATA_PACKAGE_MODE=folder"
if /I "%OPLATA_PACKAGE_MODE%"=="folder" goto mode_ok
if /I "%OPLATA_PACKAGE_MODE%"=="single" goto mode_ok
echo Неизвестный режим сборки: %OPLATA_PACKAGE_MODE%
echo Использование: scripts\build_windows.bat [folder^|single]
exit /b 2

:mode_ok
python -m venv .venv
if errorlevel 1 (
  echo Не удалось создать виртуальное окружение .venv.
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 (
  echo Не удалось обновить pip.
  exit /b 1
)
pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Не удалось установить зависимости.
  exit /b 1
)

rem Windows-метаданные EXE: название продукта, описание и версия.
python scripts\write_version_info.py
if errorlevel 1 (
  echo Не удалось сгенерировать build\version_info.txt.
  exit /b 1
)

pyinstaller --noconfirm --clean oplata.spec
if errorlevel 1 (
  echo Сборка завершилась с ошибкой.
  exit /b 1
)

echo.
if /I "%OPLATA_PACKAGE_MODE%"=="single" (
  echo Готово: dist\Oplata.exe
  echo Проверка запуска: dist\Oplata.exe --self-test
) else (
  echo Готово: dist\Oplata\Oplata.exe
  echo Не перемещайте Oplata.exe отдельно от остальных файлов папки dist\Oplata.
  echo Проверка запуска: dist\Oplata\Oplata.exe --self-test
)
endlocal
