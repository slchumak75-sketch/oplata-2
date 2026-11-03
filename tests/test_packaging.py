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
