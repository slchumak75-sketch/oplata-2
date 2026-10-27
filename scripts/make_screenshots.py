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

from PySide6.QtCore import QDate, QTimer  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from oplata import styles  # noqa: E402
from oplata.app import MainWindow  # noqa: E402
from oplata.dialogs.counterparty_card import CounterpartyCardDialog  # noqa: E402
from oplata.dialogs.delivery_dialog import DeliveryDialog  # noqa: E402
from oplata.dialogs.operation_dialog import OperationDialog  # noqa: E402
from oplata.models import Counterparty, Operation  # noqa: E402
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
        window.resize(1920, 1000)  # как развёрнутое окно на Full HD у пользователя
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
            eee = next(s for s in store.data.suppliers if s.name == "eee")
            buyer = Counterparty(name="Покупатель А")
            store.data.customers.append(buyer)
            first = Operation(
                kind="Приход",
                counterparty_id=ggg.id,
                date="2026-10-26",
                due_date="2026-11-05",
                amount="88888.88",
                document="Накладная 42",
                comment="поставка за неделю",
            )
            store.data.operations.extend([
                first,
                Operation(
                    kind="Оплата",
                    counterparty_id=ggg.id,
                    date="2026-10-27",
                    amount="10000.00",
                    document="П/п 17",
                    comment="частичная оплата",
                ),
                Operation(
                    kind="Приход",
                    counterparty_id=eee.id,
                    date="2026-10-27",
                    due_date="2026-10-27",
                    amount="25000.00",
                    document="Счёт 8",
                    comment="предоплата",
                ),
                Operation(
                    kind="Приход",
                    counterparty_id=buyer.id,
                    date="2026-10-28",
                    amount="12345.67",
                    document="Реализация 3",
                ),
            ])
            window.operations_view.refresh()
            window.show_page(3)
            window.operations_view.table.selectRow(0)
            app.processEvents()
            window.grab().save(str(OUT / "operations.png"))
            print("saved operations")
            dialog = CounterpartyCardDialog(store, "supplier", selected_id=ggg.id)
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(OUT / "supplier-card.png"))
            print("saved supplier-card")
            dialog.close()
            # Окно «Поставка» — как на макете пользователя с «eee / Предоплата»
            eee.prepayment = True
            delivery = DeliveryDialog(store, eee.id)
            delivery.show()
            app.processEvents()
            delivery.grab().save(str(OUT / "delivery-dialog.png"))
            print("saved delivery-dialog")
            delivery.close()
            # Окно операции журнала: тип, дата, сумма, документ и комментарий
            operation = OperationDialog(store, initial_counterparty_id=ggg.id)
            operation.date_edit.setDate(QDate(2026, 10, 26))
            operation.amount_edit.setText("88 888,88")
            operation.document_edit.setText("Накладная 42")
            operation.comment_edit.setText("пример оформления операции")
            operation.show()
            app.processEvents()
            operation.grab().save(str(OUT / "operation-dialog.png"))
            print("saved operation-dialog")
            operation.close()
            app.quit()

        QTimer.singleShot(200, shoot)
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
