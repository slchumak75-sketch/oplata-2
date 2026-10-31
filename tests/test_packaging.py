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
    text = raw.decode("cp866")  # русский текст для cmd.exe — в кодировке 866

    assert "OPLATA_DATA_DIR" in text
    assert "Oplata.exe" in text
    # путь можно задать вручную: строка закомментирована в начале файла
    assert 'rem set "OPLATA_DATA_DIR=' in text
    # папка ищется сама — по стандартным расположениям Диска
    assert "Google" in text and "USERPROFILE" in text

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
