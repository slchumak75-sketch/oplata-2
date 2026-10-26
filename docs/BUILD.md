# BUILD.md — сборка и получение `Oplata.exe`

## Для пользователя: где скачать готовую программу

Программа собирается автоматически на GitHub при каждом обновлении:

1. Откройте репозиторий на GitHub → вкладка **Actions**.
2. Выберите последний успешный (зелёный) запуск **Build Windows**.
3. Внизу страницы запуска, блок **Artifacts** → скачайте `Oplata-windows-vX.Y.Z`.
4. Распакуйте zip → внутри `Oplata.exe` → запускайте. Установка не нужна.

Для версий с тегом (релизных) тот же zip лежит в разделе **Releases**.

> ⚠️ При первом запуске Windows может показать SmartScreen
> («Windows защитила ваш компьютер») — exe не подписан цифровой подписью.
> Нажмите «Подробнее» → «Выполнить в любом случае». Подпись — в планах (ROADMAP, этап 3).

Данные программа хранит в `%APPDATA%\OplataStroymarket\data.json`
(обновление exe данные не стирает).

## Как устроена автосборка (GitHub Actions)

Файл `.github/workflows/build-windows.yml`:

| Триггер | Что происходит |
|---|---|
| push в `main` или `arena/**`, Pull Request | тесты (Ubuntu, headless Qt) → сборка exe (windows-latest) → smoke-test `Oplata.exe --self-test` → zip в **Artifacts** |
| push тега `v*` (например `v0.2.0`) | всё выше **+ GitHub Release** с zip |
| вручную (Actions → Build Windows → Run workflow) | как push |

Сборка падает, если падают тесты или если готовый exe не запускается —
битый exe до пользователя не доедет.

## Как выпустить релиз (для разработчика/агента)

1. Поднять версию синхронно: `src/oplata/__init__.py` (`__version__`) и `pyproject.toml`.
2. В `CHANGELOG.md` перенести `[Unreleased]` в раздел новой версии с датой.
3. Коммит → пуш → дождаться зелёного Actions.
4. `git tag vX.Y.Z && git push origin vX.Y.Z` — Actions соберёт и опубликует Release.

## Локальная разработка

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt    # Linux/macOS
.venv\Scripts\pip install -r requirements-dev.txt  # Windows

.venv/bin/python run.py                 # запуск из исходников
.venv/bin/python run.py --self-test     # открыться и закрыться (код 0 = ОК)
.venv/bin/python -m pytest -q           # на Linux без монитора: QT_QPA_PLATFORM=offscreen
```

Linux-нюанс: PySide6 нужны системные библиотеки —
`sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1`
(в GitHub Actions этот шаг есть в workflow). На Windows ничего не нужно.

## Локальная сборка exe (Windows)

```bat
scripts\build_windows.bat
```

Скрипт создаст `.venv`, поставит зависимости и соберёт `dist\Oplata.exe`
(PyInstaller onefile, без консоли; рецепт — `oplata.spec`).

Вручную то же самое:

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm oplata.spec
dist\Oplata.exe --self-test
```

## Проблемы и решения

| Симптом | Причина / лечение |
|---|---|
| `ImportError: libGL.so.1` при тестах на Linux | Поставить системные пакеты из раздела выше |
| Exe запускается ~2–5 c | PyInstaller onefile распаковывается во временную папку (ADR-003). Если станет критично — перейти на onedir |
| Антивирус ругается на exe | Известно для неподписанных PyInstaller-сборок; лечится подписью кода (этап 3) |
| Данные «пропали» после обновления | Проверьте `%APPDATA%\OplataStroymarket\data.json` — exe его не трогает; путь также виден на экране «Данные» |
