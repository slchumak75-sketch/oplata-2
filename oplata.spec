# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: один файл Oplata.exe (windowed, без консоли).

Сборка:  pyinstaller --noconfirm oplata.spec
Результат: dist/Oplata.exe
Подробности и компромисс onefile vs onedir — docs/DECISIONS.md (ADR-003).
"""

from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
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
    # TODO: иконка приложения (icon="assets/oplata.ico") — см. ROADMAP.
)
