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
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QDate, QRectF, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter  # noqa: E402
from PySide6.QtPrintSupport import QPrinter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from oplata import styles  # noqa: E402
from oplata.app import MainWindow  # noqa: E402
from oplata.dialogs.counterparty_card import CounterpartyCardDialog  # noqa: E402
from oplata.dialogs.delivery_dialog import DeliveryDialog  # noqa: E402
from oplata.dialogs.operation_dialog import OperationDialog  # noqa: E402
from oplata.dialogs.realization_dialog import RealizationDialog  # noqa: E402
from oplata.models import Counterparty, Operation  # noqa: E402

from oplata.storage import DataStore  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
PAGES = ["summary", "receipts", "suppliers", "customers", "data"]


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
        # На документационном снимке не нужен случайный временный путь:
        # показываем реальное расположение данных у пользователя Windows.
        window.data_view.path_label.setText(
            r"%APPDATA%\OplataStroymarket\data.json"
        )
        window.resize(1920, 1000)  # как развёрнутое окно на Full HD у пользователя
        window.show()

        def shoot(index: int = 0) -> None:
            if index < len(PAGES):
                page = PAGES[index]
                if page == "data":
                    # Плашки свёрнуты по умолчанию — для снимка раскрываем.
                    window.data_view.calendar_panel.set_expanded(True)
                    window.data_view.admin_panel.set_expanded(True)
                window.show_page(index)
                app.processEvents()
                window.grab().save(str(OUT / f"{page}.png"))
                print("saved", page)
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
                    kind="Реализация",
                    counterparty_id=buyer.id,
                    date="2026-10-28",
                    amount="12345.67",
                    document="Реализация 3",
                ),
            ])
            # Экран «Поступление и оплаты»: суммы за 27.10.2026.
            from oplata.models import add_cash_receipt

            receipts = window.receipts_view
            receipts.set_date(date(2026, 10, 27))
            add_cash_receipt(store.data, "cash", "2026-10-27", Decimal("15000"))
            add_cash_receipt(store.data, "card", "2026-10-27", Decimal("42500.50"))
            store.data.operations.append(
                Operation(
                    kind="Оплата",
                    counterparty_id=buyer.id,
                    date="2026-10-27",
                    amount="12345.67",
                    comment="Поступление от покупателя",
                )
            )
            store.data.operations.append(
                Operation(
                    kind="Оплата",
                    counterparty_id=ggg.id,
                    date="2026-10-27",
                    amount="10000.00",
                    comment="Оплата поставщику",
                )
            )
            store.save()
            receipts.refresh()
            window.show_page(1)
            app.processEvents()
            window.grab().save(str(OUT / "receipts.png"))
            print("saved receipts")

            # Выпадающее окно покупателей с полем суммы.
            popup = receipts.open_row_popup("customers")
            popup.edits[buyer.id].setText("12 345,67")
            app.processEvents()
            popup.grab().save(str(OUT / "receipts-customers-popup.png"))
            print("saved receipts-customers-popup")
            popup.close()
            window.customers_view.refresh()
            window.customers_view.goto_date(date(2026, 10, 28))
            window.show_page(3)
            app.processEvents()
            window.grab().save(str(OUT / "customers.png"))
            print("saved customers with realization")
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
            realization = RealizationDialog(
                store, buyer.id, preset_date=date(2026, 10, 28)
            )
            realization.amount_edit.setText("12 345,67")
            realization.show()
            app.processEvents()
            realization.grab().save(str(OUT / "realization-dialog.png"))
            print("saved realization-dialog")
            realization.close()
            # Окно операции: тип, дата, сумма, документ и комментарий
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

            # Печатная форма «Сводки»: первая страница A4, отрисованная
            # тем же QTextDocument и с той же настройкой принтера, что и
            # в окне предпросмотра. Дата/время фиксированы для стабильности.
            summary = window.summary_view
            summary.date_edit.setDate(QDate(2026, 10, 27))
            summary.refresh()
            document = summary.create_print_document(
                datetime(2026, 10, 27, 19, 5)
            )
            printer = summary.configure_print_printer(
                QPrinter(QPrinter.PrinterMode.HighResolution)
            )
            paper_rect = printer.paperRect(QPrinter.Unit.Point)
            page_rect = printer.pageRect(QPrinter.Unit.Point)
            document.setPageSize(page_rect.size())
            scale = 2.0
            image = QImage(
                int(paper_rect.width() * scale),
                int(paper_rect.height() * scale),
                QImage.Format.Format_ARGB32,
            )
            image.fill(QColor("white"))
            painter = QPainter(image)
            painter.scale(scale, scale)
            painter.translate(page_rect.left(), page_rect.top())
            document.drawContents(
                painter,
                QRectF(0, 0, page_rect.width(), page_rect.height()),
            )
            painter.end()
            image.save(str(OUT / "summary-print.png"))
            print("saved summary-print")

            app.quit()

        QTimer.singleShot(200, shoot)
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
