# BUILD.md — сборка и получение программы для Windows

## Для пользователя: где скачать готовую программу

Программа собирается автоматически на GitHub при каждом обновлении. Начиная
с v0.4.1 каждый запуск **Build Windows** создаёт два варианта:

| Артефакт | Что внутри | Когда использовать |
|---|---|---|
| `Oplata-windows-folder-vX.Y.Z` — **рекомендуется** | папка `Oplata` с `Oplata.exe`, DLL и служебными файлами + `START_HERE_RU.txt` | основной вариант; не распаковывается во временную папку при каждом запуске и обычно менее подозрителен для эвристик браузера/антивируса |
| `Oplata-windows-single-file-vX.Y.Z` | один `Oplata.exe` + `START_HERE_RU.txt` | запасной вариант, если нужен именно один файл |

### Скачивание рекомендуемого варианта

1. Откройте репозиторий на GitHub → вкладка **Actions**.
2. Выберите последний успешный (зелёный) запуск **Build Windows**.
3. Внизу страницы запуска, в блоке **Artifacts**, скачайте
   `Oplata-windows-folder-vX.Y.Z`.
4. Распакуйте скачанный ZIP в удобную папку.
5. Запускайте `Oplata\Oplata.exe`.

> ⚠️ Не вытаскивайте `Oplata.exe` из папки `Oplata` отдельно: рядом с ним
> лежат нужные DLL и файлы `_internal`. Для быстрого доступа создайте ярлык
> на `Oplata\Oplata.exe` и положите ярлык на рабочий стол.

Установка не нужна. Для запасного варианта после распаковки запускайте
`Oplata.exe`, лежащий рядом с `START_HERE_RU.txt`.

Для версий с тегом те же пакеты лежат в разделе **Releases** как
`Oplata-vX.Y.Z-windows-folder.zip` и
`Oplata-vX.Y.Z-windows-single-file.zip`, рядом — `SHA256SUMS.txt`
с контрольными суммами SHA-256.

Данные программа хранит отдельно, в
`%APPDATA%\OplataStroymarket\data.json`, поэтому замена папки программы или
EXE при обновлении данные не стирает.

## Если браузер заблокировал саму загрузку

Это отдельная ситуация от окна SmartScreen при запуске. Чаще всего причина
не «найденный вирус», а отсутствие репутации: EXE пока не подписан цифровой
подписью издателя, а у каждой новой сборки свой хэш. Репутация предыдущих
версий на новый хэш автоматически не переносится, поэтому одна версия может
скачаться спокойно, а следующая — получить предупреждение.

Сначала попробуйте рекомендуемый артефакт
`Oplata-windows-folder-vX.Y.Z`: он не является самораспаковывающимся
onefile-EXE. Если браузер всё равно остановил загрузку, разрешайте её
**только** когда вы сами нажали именно этот артефакт на странице GitHub
Actions проекта и версия/имя совпадают с ожидаемыми.

### Microsoft Edge

1. Откройте **Загрузки** (`Ctrl+J`).
2. У заблокированного файла нажмите `...`.
3. Выберите **Сохранить** (*Keep*).
4. Нажмите **Показать больше** (*Show more*).
5. Выберите **Сохранить в любом случае** (*Keep anyway*).

### Google Chrome

1. Откройте **Загрузки** (`Ctrl+J`).
2. У заблокированного файла нажмите **Ещё**.
3. Выберите **Скачать опасный файл**.

Названия пунктов зависят от языка и версии браузера. Не отключайте Защитник
Windows или SmartScreen ради одной загрузки. После скачивания можно
дополнительно проверить файл через контекстное меню:
**Сканировать с помощью Microsoft Defender**.

Если пункта «сохранить в любом случае» нет, загрузку может запрещать
политика организации — тогда нужен администратор компьютера. Полностью
убрать предупреждения для всех пользователей без цифровой подписи кода
нельзя; подпись остаётся отдельным пунктом ROADMAP.

## Если Windows предупредила при первом запуске

Это уже SmartScreen: «Windows защитила ваш компьютер». Причина та же — EXE
пока без цифровой подписи. Для файла, скачанного из GitHub Actions проекта,
нажмите **Подробнее → Выполнить в любом случае**.

## Как устроена автосборка (GitHub Actions)

Файл `.github/workflows/build-windows.yml`:

| Триггер | Что происходит |
|---|---|
| push в `main` или `arena/**`, Pull Request | тесты (Ubuntu, headless Qt) → генерация Windows-метаданных → сборка folder и single-file на `windows-latest` → smoke-test обоих EXE → проверка VersionInfo → SHA-256 → два артефакта |
| push тега `v*` (например `v0.4.1`) | всё выше **+ GitHub Release** с двумя ZIP и `SHA256SUMS.txt` |
| вручную (Actions → Build Windows → Run workflow) | как push |

Artifacts загружаются из каталогов `package-folder/` и `package-single/`
напрямую. GitHub сам создаёт один скачиваемый ZIP для каждого артефакта;
прежней вложенности «ZIP внутри ZIP» больше нет.

Сборка падает, если падают тесты, если хотя бы один готовый EXE не
запускается в `--self-test` или если в EXE отсутствуют ожидаемые
метаданные версии.

## Windows-метаданные EXE

Перед PyInstaller workflow запускает:

```bat
python scripts\write_version_info.py
```

Скрипт читает версию из `src/oplata/__init__.py` и создаёт
`build\version_info.txt` в формате PyInstaller `VSVersionInfo`. В EXE
попадают `CompanyName`, `ProductName`, `FileDescription`, `FileVersion`,
`ProductVersion` и `OriginalFilename`. Это не заменяет цифровую подпись,
но файл перестаёт быть полностью «безымянным» для Windows и пользователя.

## Как выпустить релиз (для разработчика/агента)

1. Поднять версию синхронно: `src/oplata/__init__.py` (`__version__`) и
   `pyproject.toml`. Синхронность проверяет `tests/test_packaging.py`.
2. В `CHANGELOG.md` перенести `[Unreleased]` в раздел новой версии с датой.
3. Коммит → пуш → дождаться зелёного Actions.
4. `git tag vX.Y.Z && git push origin vX.Y.Z` — Actions соберёт и
   опубликует Release с folder/single ZIP и `SHA256SUMS.txt`.

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

Рекомендуемый вариант папкой:

```bat
scripts\build_windows.bat
rem или явно:
scripts\build_windows.bat folder
```

Результат: `dist\Oplata\Oplata.exe` (не перемещать EXE отдельно от папки).

Запасной один файл:

```bat
scripts\build_windows.bat single
```

Результат: `dist\Oplata.exe`.

Скрипт создаст `.venv`, поставит зависимости, сгенерирует
`build\version_info.txt` и вызовет PyInstaller с `--clean`.

Вручную то же самое:

```bat
pip install -r requirements.txt pyinstaller
python scripts\write_version_info.py
set OPLATA_PACKAGE_MODE=folder
pyinstaller --noconfirm --clean oplata.spec
dist\Oplata\Oplata.exe --self-test
```

Для single-варианта замените `folder` на `single`; результат будет
`dist\Oplata.exe`.

## Проблемы и решения

| Симптом | Причина / лечение |
|---|---|
| Браузер пишет, что загрузка заблокирована | Сначала скачайте `Oplata-windows-folder-v…`; если всё равно заблокировал — см. раздел «Если браузер заблокировал саму загрузку». Причина обычно — новый хэш неподписанного EXE без репутации |
| «Windows защитила ваш компьютер» при запуске | SmartScreen для неподписанного EXE: «Подробнее» → «Выполнить в любом случае» только для файла из GitHub Actions проекта |
| Folder-вариант не запускается после перемещения EXE | EXE вытащили из папки `Oplata` без DLL/_internal. Верните всю папку целиком и запускайте `Oplata\Oplata.exe`; на рабочий стол кладите ярлык, а не сам EXE |
| Single-file запускается ~2–5 c | Onefile распаковывается во временную папку при старте. Используйте folder-вариант |
| Антивирус ругается на exe | Известно для неподписанных PyInstaller-сборок. Folder-вариант и метаданные снижают риск эвристических срабатываний, но окончательное решение — подпись кода |
| `ImportError: libGL.so.1` при тестах на Linux | Поставить системные пакеты из раздела выше |
| Данные «пропали» после обновления | Проверьте `%APPDATA%\OplataStroymarket\data.json` — программа его не трогает; путь также виден на экране «Данные» |
