# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipes for the Windows builds.

Environment variable ``OPLATA_PACKAGE_MODE`` selects the layout:

* ``folder`` (default, recommended): PyInstaller *onedir*.  The result is
  ``dist/Oplata/Oplata.exe`` plus its DLL/_internal files.  Unlike a
  one-file build it does not unpack itself to a temporary directory at
  every start, which is less likely to resemble a dropper/packer to
  browser and antivirus heuristics.
* ``single``: the former PyInstaller *onefile* build,
  ``dist/Oplata.exe``.  It remains available as a fallback.

Before a Windows build, run ``python scripts/write_version_info.py``.
If ``build/version_info.txt`` exists on Windows, it is embedded as the
EXE version resource (product/company/description/version metadata).
"""

import os
from pathlib import Path

ROOT = Path(SPECPATH)
PACKAGE_MODE = os.environ.get("OPLATA_PACKAGE_MODE", "folder").strip().lower()
if PACKAGE_MODE not in {"folder", "single"}:
    raise SystemExit(
        "OPLATA_PACKAGE_MODE must be 'folder' or 'single', "
        f"got {PACKAGE_MODE!r}"
    )

VERSION_INFO_FILE = ROOT / "build" / "version_info.txt"
VERSION_RESOURCE = (
    str(VERSION_INFO_FILE)
    if os.name == "nt" and VERSION_INFO_FILE.is_file()
    else None
)

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    # v0.20.0: openpyxl импортируется лениво (внутри функции), поэтому
    # PyInstaller-у его называем явно — иначе выгрузка в Excel в
    # собранной программе упала бы с «No module named openpyxl».
    hiddenimports=["openpyxl"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

EXE_OPTIONS = dict(
    name="Oplata",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI без чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=VERSION_RESOURCE,
    # TODO: иконка приложения (icon="assets/oplata.ico") — см. ROADMAP.
)

if PACKAGE_MODE == "single":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        **EXE_OPTIONS,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **EXE_OPTIONS,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="Oplata",
    )
