@echo off
rem Локальная сборка Oplata.exe под Windows (то же самое делает GitHub Actions).
rem Требования: Python 3.11+ в PATH. Запуск из корня репозитория:
rem   scripts\build_windows.bat
setlocal
cd /d "%~dp0\.."

python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm oplata.spec
if errorlevel 1 (
  echo Сборка завершилась с ошибкой.
  exit /b 1
)

echo.
echo Готово: dist\Oplata.exe
echo Проверка запуска: dist\Oplata.exe --self-test
endlocal
