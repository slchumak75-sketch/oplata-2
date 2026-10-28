"""Журнал поступлений и журнал оплат (v0.13.0).

Открывается кликом по **названию строки** в блоке «Поступление» или
«Оплата» (кроме строк «Покупатели» и «Поставщики» — у них своё окно со
списком контрагентов). Что показывает журнал:

* в блоке **«Поступление»** — журнал **поступлений**: записи наличных
  и карт (``AppData.cash_receipts``) и прочие поступления
  (``AppData.other_payments`` с ``direction = INCOMING``);
* в блоке **«Оплата»** — журнал **оплат**: прочие виды оплат
  (``AppData.other_payments`` с ``direction = OUTGOING``).

Клик по конкретной строке показывает только её записи; журнал всего
блока — все строки этого направления. Окно немодальное: его часто
смотрят рядом с таблицами. Редактирования здесь нет: суммы меняются
прямо в ячейках блоков (ADR-017/018).
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import styles
from ..dates import fmt_date, fmt_money_plain
from ..models import (
    CARD_SOURCE,
    CASH_SOURCE,
    INCOMING,
    OUTGOING,
    CashReceipt,
    OtherPayment,
    cash_receipt_amount,
    other_payment_amount,
)
from ..storage import DataStore

DATE_COL, NAME_COL, AMOUNT_COL = range(3)


def _parse_day(value: str):
    """Дата из ISO-строки; битая строка — ``None``."""

    try:
        return date.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None

CASH_TITLE = "Поступление наличными"
CARD_TITLE = "Поступление по картам"


class PaymentsJournalDialog(QDialog):
    """Окно со списком записей строки блока и итогом по ним."""

    def __init__(
        self,
        store: DataStore,
        direction: str = INCOMING,
        row_title: str | None = None,
        row_key: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.direction = direction
        self.row_title = row_title
        self.row_key = row_key or ""

        incoming = direction == INCOMING
        self.setWindowTitle("Журнал поступлений" if incoming else "Журнал оплат")
        self.resize(620, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title_text = "Журнал поступлений" if incoming else "Журнал оплат"
        if row_title:
            title_text = f"{title_text} · {row_title}"
        title = QLabel(title_text)
        title.setStyleSheet(f"font-size:{styles.fs(17)}px;font-weight:800")
        root.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        root.addWidget(self.count_label)

        self.table = QTableWidget(0, len((DATE_COL, NAME_COL, AMOUNT_COL)))
        self.table.setHorizontalHeaderLabels(["Дата", "Строка", "Сумма"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(NAME_COL, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, stretch=1)

        total_row = QHBoxLayout()
        total_row.addStretch(1)
        caption = QLabel("Итого:")
        caption.setStyleSheet("font-weight:700")
        total_row.addWidget(caption)
        self.total_label = QLabel("0,00")
        self.total_label.setStyleSheet(
            f"font-weight:800;font-size:{styles.fs(15)}px;color:{styles.BLUE}"
        )
        total_row.addWidget(self.total_label)
        root.addLayout(total_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------------ данные
    def _entries(self) -> list[tuple[str, str, object]]:
        """Записи журнала: ``(дата, название строки, сумма)``."""

        data = self.store.data
        rows: list[tuple[str, str, object]] = []

        if self.direction == INCOMING:
            for receipt in data.cash_receipts:
                if not isinstance(receipt, CashReceipt):
                    continue
                title = (
                    CASH_TITLE
                    if getattr(receipt, "source", CASH_SOURCE) == CASH_SOURCE
                    else CARD_TITLE
                )
                rows.append((receipt.date, title, cash_receipt_amount(receipt)))

        for payment in data.other_payments:
            if not isinstance(payment, OtherPayment):
                continue
            if getattr(payment, "direction", OUTGOING) != self.direction:
                continue
            if not payment.amount or payment.amount == "0":
                continue  # строки с нулевой суммой в журнал не попадают
            rows.append((payment.date, payment.name, other_payment_amount(payment)))

        if self.row_title:
            rows = [row for row in rows if row[1] == self.row_title]

        rows.sort(key=lambda row: (row[0] or ""), reverse=True)
        return rows

    # ------------------------------------------------------------------- вид
    def refresh(self) -> None:
        rows = self._entries()
        self.table.setRowCount(len(rows))
        total = 0
        for line, (day_iso, name, amount) in enumerate(rows):
            day = _parse_day(day_iso)
            date_text = fmt_date(day) if day is not None else str(day_iso or "")
            for column, text in enumerate(
                (date_text, name, fmt_money_plain(amount))
            ):
                item = QTableWidgetItem(text)
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                if column == AMOUNT_COL:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(line, column, item)
            total += amount

        self.table.setColumnWidth(DATE_COL, 120)
        self.table.setColumnWidth(AMOUNT_COL, 140)
        self.count_label.setText(f"Записей: {len(rows)}")
        self.total_label.setText(fmt_money_plain(total))
