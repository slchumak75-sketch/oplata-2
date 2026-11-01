"""Пути к ресурсам программы (иконка).

Работает и при запуске из исходников, и из собранного PyInstaller-ом
пакета:

* из исходников ресурсы лежат в каталоге ``assets/`` рядом с ``src/``;
* в сборке PyInstaller распаковывает их во временный каталог
  ``sys._MEIPASS`` (onefile) или в ``_internal`` рядом с exe (onedir) —
  в обеих случаях путь начинается с ``sys._MEIPASS``.

Иконка нужна в двух местах: в самом EXE-файле (её вшивает PyInstaller,
см. ``oplata.spec``) и в заголовке окна (ставит ``main.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

ASSETS_DIR_NAME = "assets"
ICON_FILE_NAME = "oplata.png"
ICON_FILE_ICO = "oplata.ico"


def assets_dir() -> Path:
    """Каталог ресурсов программы."""
    if getattr(sys, "frozen", False):  # pragma: no cover — только в сборке
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / ASSETS_DIR_NAME
    return Path(__file__).resolve().parent.parent.parent / ASSETS_DIR_NAME


def icon_path() -> Path:
    """PNG-иконка окна программы."""
    return assets_dir() / ICON_FILE_NAME
