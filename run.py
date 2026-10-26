"""Запуск приложения из исходников и вход для PyInstaller.

    python run.py            # обычный запуск
    python run.py --self-test
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from oplata.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
