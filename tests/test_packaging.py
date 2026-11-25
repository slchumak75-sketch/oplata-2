"""Tests for Windows packaging helpers (no Qt or PyInstaller build needed)."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

import oplata

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "write_version_info.py"
SPEC = importlib.util.spec_from_file_location("write_version_info", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
version_info = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(version_info)

# Build dotted versions from components in these tests.  This keeps the
# expected values unambiguous when this file is transported through tools
# that display or normalize version-looking strings.
VERSION_THREE_PARTS = ".".join(("0", "4", "1"))
VERSION_FOUR_PARTS = ".".join(("1", "2", "3", "4"))


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("1", (1, 0, 0, 0)),
        ("0.4", (0, 4, 0, 0)),
        (VERSION_THREE_PARTS, (0, 4, 1, 0)),
        (VERSION_FOUR_PARTS, (1, 2, 3, 4)),
    ],
)
def test_version_tuple_pads_to_four_components(version, expected):
    assert version_info.version_tuple(version) == expected


@pytest.mark.parametrize("version", ["", "0.4-beta", "0.2-dev", "65536.0"])
def test_version_tuple_rejects_unsupported_versions(version):
    with pytest.raises(ValueError):
        version_info.version_tuple(version)


def test_render_version_info_contains_windows_metadata():
    rendered = version_info.render_version_info(VERSION_THREE_PARTS)

    assert rendered.startswith("# UTF-8\n")
    assert "filevers=(0, 4, 1, 0)" in rendered
    assert "prodvers=(0, 4, 1, 0)" in rendered
    assert f"StringStruct('FileVersion', '{VERSION_THREE_PARTS}')" in rendered
    assert f"StringStruct('ProductVersion', '{VERSION_THREE_PARTS}')" in rendered
    assert "StringStruct('OriginalFilename', 'Oplata.exe')" in rendered
    assert "Оплаты — Строймаркет" in rendered


def test_google_drive_launcher_is_shipped():
    """v0.28.1: bat-файл запуска с общей папкой есть и уходит в сборку.

    Он задаёт ``OPLATA_DATA_DIR``, поэтому программа читает и пишет
    данные в общей папке Google Диска, а не в локальном каталоге.
    """

    launcher = ROOT / "packaging" / "Start-With-Google-Drive.bat"
    assert launcher.exists(), "нет bat-файла запуска с Google Диска"

    raw = launcher.read_bytes()
    assert b"\r\n" in raw, "bat-файл должен быть с переводами строк CRLF"
    # v0.30.0: файл переведён в UTF-8 (консоль переключается chcp 65001) —
    # иначе русские буквы не доезжают до PowerShell, который делает ярлык.
    text = raw.decode("utf-8")

    assert "chcp 65001" in text, "без UTF-8 в консоли ярлык получится кракозяброй"
    assert "OPLATA_DATA_DIR" in text
    assert "Oplata.exe" in text
    # путь можно задать вручную: строка закомментирована в начале файла
    assert 'rem set "OPLATA_DATA_DIR=' in text
    # папка ищется сама — по стандартным расположениям Диска
    assert "Google" in text and "USERPROFILE" in text

    # v0.30.0: bat сам создаёт ярлык «Оплаты» с иконкой из Oplata.exe —
    # рядом с программой и на рабочем столе.
    assert "call :make_shortcut" in text, "ярлык больше не создаётся при запуске"
    assert ":make_shortcut" in text
    assert "WScript.Shell" in text, "ярлык делается через COM-объект WScript.Shell"
    assert "IconLocation" in text, "у ярлыка должна быть иконка программы"
    assert "Оплаты.lnk" in text
    assert "%USERPROFILE%\\Desktop\\" in text
    # PowerShell может быть недоступен — запуск программы важнее ярлыка.
    assert "exit /b 0" in text

    # Файл копируется в оба пакета (папка и один exe).
    workflow = (
        ROOT / ".github" / "workflows" / "build-windows.yml"
    ).read_text(encoding="utf-8")
    assert workflow.count(
        'Copy-Item ".\\packaging\\Start-With-Google-Drive.bat"'
    ) == 2
    # И упомянут в START_HERE: пользователь должен о нём узнать.
    start_here = (ROOT / "packaging" / "START_HERE_RU.txt").read_text(
        encoding="utf-8"
    )
    assert "Start-With-Google-Drive.bat" in start_here


def test_application_and_project_versions_are_in_sync():
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]

    assert version_info.application_version() == oplata.__version__
    assert project["version"] == oplata.__version__


def _png_size(path):
    """Размер PNG из заголовка (без Pillow)."""
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "это не PNG"
    width = int.from_bytes(raw[16:20], "big")
    height = int.from_bytes(raw[20:24], "big")
    return width, height


def _ico_sizes(path):
    """Размеры картинок внутри ICO (без Pillow)."""
    raw = path.read_bytes()
    assert raw[:4] == b"\x00\x00\x01\x00", "это не ICO"
    count = int.from_bytes(raw[4:6], "little")
    sizes = []
    for index in range(count):
        offset = 6 + index * 16
        width = raw[offset] or 256  # 0 в байте означает 256
        height = raw[offset + 1] or 256
        sizes.append((width, height))
    return sizes


def test_app_icon_files_are_shipped():
    """v0.28.2: иконка есть и в PNG (окно), и в ICO (EXE-файл)."""

    png = ROOT / "assets" / "oplata.png"
    ico = ROOT / "assets" / "oplata.ico"
    assert png.is_file(), "нет PNG-иконки для окна программы"
    assert ico.is_file(), "нет ICO-иконки для EXE-файла"

    assert _png_size(png) == (256, 256)
    sizes = _ico_sizes(ico)
    # Мелкие размеры — для проводника, крупные — для панели задач.
    for size in ((16, 16), (32, 32), (48, 48), (256, 256)):
        assert size in sizes, f"в ICO нет размера {size}"


def test_spec_embeds_icon_into_exe():
    """v0.28.2: PyInstaller вшивает иконку в EXE и кладёт ресурсы в сборку."""

    spec = (ROOT / "oplata.spec").read_text(encoding="utf-8")

    assert 'ICON_FILE = ASSETS_DIR / "oplata.ico"' in spec
    assert "ICON_RESOURCE = str(ICON_FILE) if ICON_FILE.is_file() else None" in spec
    assert "icon=ICON_RESOURCE," in spec, "иконка не передана в EXE()"
    # assets/ уходит в сборку целиком: оттуда main.py берёт иконку
    # окна, а styles.py — картинку стрелки календаря.
    assert "datas=ASSET_DATAS," in spec
    assert 'for path in sorted(ASSETS_DIR.glob("*"))' in spec


def test_all_asset_files_are_shipped():
    """v0.28.2: ничего из assets/ не должно потеряться по дороге в сборку."""

    shipped = sorted(
        path.name for path in (ROOT / "assets").glob("*") if path.is_file()
    )

    assert shipped == ["arrow-down.png", "oplata.ico", "oplata.png"]


def test_window_icon_is_applied_at_startup():
    """v0.28.2: окно программы показывает свою иконку."""

    main_source = (ROOT / "src" / "oplata" / "main.py").read_text(encoding="utf-8")

    assert "setWindowIcon(" in main_source
    assert "from .resources import icon_path" in main_source

    from oplata.resources import icon_path

    assert icon_path().is_file(), "иконка не находится при запуске из исходников"


def test_resources_find_icon_in_frozen_bundle(monkeypatch, tmp_path):
    """v0.28.2: в сборке иконка ищется там, куда её распаковал PyInstaller."""

    from oplata import resources

    bundle_assets = tmp_path / "assets"
    bundle_assets.mkdir()
    (bundle_assets / "oplata.png").write_bytes(b"stub")

    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resources.icon_path() == bundle_assets / "oplata.png"

    monkeypatch.setattr(resources.sys, "frozen", False, raising=False)
    assert resources.icon_path() == ROOT / "assets" / "oplata.png"


def test_exit_autosave_is_wired_into_startup():
    """v0.30.0: выход из программы выгружает базы, но не в smoke-прогоне."""

    main_source = (ROOT / "src" / "oplata" / "main.py").read_text(encoding="utf-8")

    assert (
        "aboutToQuit.connect(window.autosave_bases_to_excel)" in main_source
    ), "при выходе из программы выгрузка не делается"
    assert 'window.autosave_on_exit = "--self-test" not in argv' in main_source, (
        "в smoke-прогоне (--self-test) выгрузка должна быть отключена"
    )


def test_inno_setup_installer_is_shipped():
    """v0.87.0: программа ставится установщиком, а не только ZIP-архивом.

    Просьба пользователя: «сделать установку программы через setup с
    полной её правильной распаковкой». Раньше из ZIP терялись файлы
    (Defender вырезал Oplata.exe).
    """

    iss = (ROOT / "packaging" / "Oplata.iss").read_text(encoding="utf-8")

    # Ставим без прав администратора и в профиль пользователя.
    assert "PrivilegesRequired=lowest" in iss
    assert "{localappdata}" in iss
    # Обновление ставится поверх, данные не трогаются.
    assert "UsePreviousAppDir=yes" in iss
    assert "%APPDATA%\\OplataStroymarket" in iss  # пояснение в шапке
    # Ярлыки: обычный запуск и запуск с общей базой на Диске.
    assert "Start-With-Google-Drive.bat" in iss
    assert "desktopicon" in iss
    # Версию передаёт CI, а не вписана руками.
    assert "AppVersion" in iss and "#ifndef AppVersion" in iss

    workflow = (
        ROOT / ".github" / "workflows" / "build-windows.yml"
    ).read_text(encoding="utf-8")
    # Установщик собирается в CI...
    assert "innosetup" in workflow
    assert "Oplata.iss" in workflow
    # ...и в релиз попадают установщик и файл обновления.
    assert "Oplata-*-Setup.exe" in workflow
    assert "UPDATE_ZIP" in workflow
    assert "oplata-update.json" in workflow


def test_inno_setup_gets_absolute_build_dir():
    """Inno Setup считает относительные Source от папки .iss (packaging\\).

    CI раньше передавал /DBuildDir=.\\package-folder, и ISCC искал
    "packaging\\.\\package-folder\\Oplata\\*" → «No files found»; сборка
    Windows падала. Поэтому BuildDir обязан быть абсолютным.
    """

    workflow = (
        ROOT / ".github" / "workflows" / "build-windows.yml"
    ).read_text(encoding="utf-8")

    # Переменная вычисляется через Resolve-Path (абсолютный путь)...
    assert "Resolve-Path" in workflow
    assert '"/DBuildDir=$buildDir"' in workflow
    # ...и относительный ./package-folder в аргументе ISCC больше не передаётся.
    assert '/DBuildDir=.\\package-folder' not in workflow
    # Перед сборкой проверяем, что папка сборки полная (есть Oplata.exe).
    assert "package-folder is incomplete" in workflow


def test_start_here_explains_updates_and_base_transfer():
    """v0.87.0: в инструкции есть обновление и перенос базы на Диск."""

    text = (ROOT / "packaging" / "START_HERE_RU.txt").read_text(encoding="utf-8")

    assert "ОБНОВЛЕНИЕ ПРОГРАММЫ" in text
    assert "кнопка «Обновить»" in text or "Кнопка «Обновить»" in text
    assert "Положить в тест" in text and "Выпустить" in text
    assert "ПЕРВЫЙ ЗАПУСК С ДИСКОМ" in text, "нет раздела о переносе базы"
    assert "Oplata-<версия>-Setup.exe" in text


def test_start_here_explains_password_recovery():
    """v0.88.0: в инструкции есть восстановление забытого пароля.

    Пользователь просил привязку к почте; но у инструкции на компьютере
    должен быть и ручной путь — она открывается в тот момент, когда
    программа уже не пускает.
    """

    text = (ROOT / "packaging" / "START_HERE_RU.txt").read_text(encoding="utf-8")

    assert "ЗАБЫЛИ ПАРОЛЬ" in text
    assert "Забыли пароль?" in text
    assert "пароль\n     приложения" in text or "пароль приложения" in text
    # Ручной путь никуда не делся.
    assert "users.json" in text


def test_test_runner_rejects_names_that_break_k_expression():
    """Имена тестов идут в выражение -k — опасные символы отсекаются.

    v0.88.0: параметризованный тест с адресом почты дал id
    «test_...[a@mail.ru-...]». `pytest -k` не принимает «@», разбор
    выражения падал, и весь прогон в CI умирал за 4 секунды — ещё до
    запуска тестов, без единой строчки о причине. Скрипт теперь
    проверяет имена заранее.
    """

    script = (ROOT / "scripts" / "run_tests.sh").read_text(encoding="utf-8")

    assert "bad_names" in script
    # Проверяются именно символы, ломающие -k, а не всё «не-слово»:
    # точки и дефисы в параметрах ([1-expected0]) допустимы.
    assert "grep -E '[@(),[:space:]]'" in script
    assert "Имена тестов содержат символы" in script


# ------------------------------------- закрепление версии Qt (v0.88.1)

def test_pyside_version_is_pinned_exactly():
    """Версия Qt закреплена точно, а не диапазоном.

    v0.88.0 не запускалась на компьютере пользователя: «DLL load failed
    while importing QtCore: Не найдена указанная процедура». Причина —
    диапазон «>=6.7,<6.12»: 26 октября вышла 6.11.3 (за 25 минут до
    сборки), а в ней Qt удалила символы версии QtPrivate_6_11_2, которые
    просит pyside6.abi3.dll от 6.11.2. Смесь файлов двух версий в папке
    программы даёт именно эту ошибку.
    """

    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    # Только строки зависимостей: в комментариях старый диапазон
    # упоминается намеренно — как объяснение, почему его убрали.
    deps = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    assert "PySide6-Essentials==" in text
    assert "shiboken6==" in text
    # Диапазонов быть не должно: они и привели к подмене версии.
    assert not any("PySide6-Essentials>=" in d for d in deps)
    assert not any("<6.12" in d for d in deps)


def test_pyside_and_shiboken_versions_match():
    """PySide6 и shiboken6 — одна версия: иначе Qt не загрузится."""

    import re

    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    pyside = re.search(r"PySide6-Essentials==(\S+)", text)
    shiboken = re.search(r"shiboken6==(\S+)", text)

    assert pyside and shiboken
    assert pyside.group(1) == shiboken.group(1)


def test_project_metadata_pins_same_version():
    """pyproject.toml не должен разрешать другую версию Qt."""

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "PySide6-Essentials==" in text
    assert "PySide6-Essentials>=" not in text


def test_installer_replaces_all_files_and_cleans_stale_ones():
    """Установщик обязан заменять файлы и убирать остатки прежней версии.

    Без ignoreversion Setup не заменяет файл без данных о версии, если
    размер совпадает: shiboken6.abi3.dll и QtCore.pyd как раз такие
    (проверено — у них нет version-info). Тогда рядом оказываются файлы
    двух версий Qt, и программа падает при запуске.
    """

    iss = (ROOT / "packaging" / "Oplata.iss").read_text(encoding="utf-8-sig")

    # Папка сборки ставится с ignoreversion — заменяются все файлы.
    assert "recursesubdirs createallsubdirs ignoreversion" in iss
    # Остатки прежней версии удаляются до установки...
    assert "[InstallDelete]" in iss
    assert 'Name: "{app}\\_internal"' in iss
    assert 'Name: "{app}\\PySide6"' in iss
    # ...и проверяются после установки на случай занятого файла.
    assert "[Code]" in iss
    assert "CleanStaleFiles" in iss
    assert "ssPostInstall" in iss


def test_startup_qt_failure_is_explained_in_russian():
    """Ошибка загрузки Qt показывается по-русски, с планом действий.

    Раньше пользователь видел английское окно PyInstaller с текстом
    «DLL load failed while importing QtCore» — по нему нельзя понять,
    что делать. Теперь есть подсказка про установщик и сохранность базы.
    """

    text = (ROOT / "src" / "oplata" / "main.py").read_text(encoding="utf-8")

    assert "_show_startup_error" in text
    assert "QT_LOAD_HELP" in text
    assert "не смогла загрузить Qt" in text
    # Главный совет — переустановить установщиком.
    assert "Setup.exe" in text
    # И успокоить насчёт данных.
    assert "данные при этом не пропадут" in text.lower()
    # Qt грузится в try: иначе ошибка вылетает трейсбеком.
    assert "except ImportError as error:" in text


def test_startup_error_reports_qt_file_versions():
    """При сбое Qt в сообщении есть версии и контрольные суммы файлов.

    Это единственный способ разобрать причину на расстоянии: программа
    стоит на четырёх компьютерах, и «пришлите скриншот» — весь доступный
    канал. По размерам и хэшам сразу видно, смешаны ли файлы двух сборок
    (у pyside6.abi3.dll размер одинаковый, а содержимое разное).
    """

    text = (ROOT / "src" / "oplata" / "main.py").read_text(encoding="utf-8")

    assert "_qt_files_report" in text
    assert "Qt6Core.dll" in text
    assert "pyside6.abi3.dll" in text
    assert "shiboken6.abi3.dll" in text
    # В сообщение отчёт попадает вместе с подсказками.
    assert "Файлы рядом с программой" in text


def test_qt_files_report_finds_libraries_next_to_exe(tmp_path, monkeypatch):
    """Отчёт ищет библиотеки в _internal рядом с exe (как в сборке)."""

    import sys as _sys

    from oplata import main as main_module

    internal = tmp_path / "_internal" / "PySide6"
    internal.mkdir(parents=True)
    core_bytes = b"qt-core-bytes"      # 13 байт
    pyside_bytes = b"pyside-bytes"     # 12 байт
    (internal / "Qt6Core.dll").write_bytes(core_bytes)
    (internal / "pyside6.abi3.dll").write_bytes(pyside_bytes)

    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "executable", str(tmp_path / "Oplata.exe"))

    report = main_module._qt_files_report()

    assert f"Qt6Core.dll: {len(core_bytes)} байт" in report
    assert f"pyside6.abi3.dll: {len(pyside_bytes)} байт" in report
    # Контрольная сумма в отчёте — 8 символов: по ней сравнивают файлы.
    assert len(report.split("Qt6Core.dll: ")[1].split(", ")[1].split("\n")[0]) == 8
