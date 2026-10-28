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


def test_application_and_project_versions_are_in_sync():
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]

    assert version_info.application_version() == oplata.__version__
    assert project["version"] == oplata.__version__
