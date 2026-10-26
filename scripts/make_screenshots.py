"""Переснять скриншоты оболочки в docs/screenshots/ (для документации).

Использование (Linux/CI, без монитора):
    QT_QPA_PLATFORM=offscreen python scripts/make_screenshots.py

На Windows просто:  python scripts\\make_screenshots.py
(окно мелькнёт на экране). Данные берутся временные (демо-сид), реальный
файл данных пользователя не трогается.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from oplata import styles  # noqa: E402
from oplata.app import MainWindow  # noqa: E402
from oplata.dialogs.counterparty_card import CounterpartyCardDialog  # noqa: E402
from oplata.storage import DataStore  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
PAGES = ["summary", "suppliers", "customers", "operations", "data"]


def main() -> int:
    app = QApplication(sys.argv[:1])

    # В Linux-окружениях без шрифтов помогает DejaVu; на Windows не нужен.
    sheet = styles.APP_STYLESHEET
    dejavu = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if dejavu.exists():
        QFontDatabase.addApplicationFont(str(dejavu))
        QFontDatabase.addApplicationFont(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        )
        app.setFont(QFont("DejaVu Sans", 10))
        sheet = sheet.replace('"Segoe UI", "Arial", sans-serif', '"DejaVu Sans"')
    app.setStyleSheet(sheet)

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = DataStore(Path(tmp) / "data.json")
        window = MainWindow(store)
        window.resize(1440, 860)
        window.show()

        def shoot(index: int = 0) -> None:
            if index < len(PAGES):
                window.show_page(index)
                app.processEvents()
                window.grab().save(str(OUT / f"{PAGES[index]}.png"))
                print("saved", PAGES[index])
                QTimer.singleShot(150, lambda: shoot(index + 1))
                return
            ggg = next(s for s in store.data.suppliers if s.name == "ггг")
            dialog = CounterpartyCardDialog(store, "supplier", selected_id=ggg.id)
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(OUT / "supplier-card.png"))
            print("saved supplier-card")
            app.quit()

        QTimer.singleShot(200, shoot)
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
