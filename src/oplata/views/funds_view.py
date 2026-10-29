"""Журнал движения средств (v0.22.1).

Запрос пользователя: «добавляем журнал движения средств, в котором
отображается остаток на счету, все поступления, все оплаты и итого
остаток (за последние 30 дней)».

Журнал открывается кнопкой в шапке «Сводки» и показывает те же цифры,
что и блок «Средства», но развёрнуто: по каждой записи за 30 дней —
дата, название, сумма и остаток после неё. Остаток на начало периода
берётся по правилам ``calc.funds_totals``, поэтому вчерашний «Итого
остаток» становится сегодняшним «Остатком на счету».
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import calc, styles
from ..dates import fmt_date, fmt_money_plain
from ..storage import DataStore

# Сколько дней показывает журнал (запрос пользователя: 30).
MOVEMENT_DAYS = 30

_MONEY_ALIGN = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class FundsMovementDialog(QDialog):
    """Окно журнала: остаток, поступления, оплаты, итого остаток."""

    def __init__(
        self,
        store: DataStore,
        on_date: date,
        parent: QDialog | None = None,
        days: int = MOVEMENT_DAYS,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_date = on_date
        self.days = days
        self.setWindowTitle("Журнал движения средств")
        self.resize(920, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("Журнал движения средств")
        title.setStyleSheet(f"font-weight:800;font-size:{styles.fs(16)}px")
        root.addWidget(title)

        self.period_label = QLabel("")
        self.period_label.setStyleSheet(f"color:{styles.MUTED}")
        root.addWidget(self.period_label)

        self.start_label = QLabel("")
        self.start_label.setStyleSheet("font-weight:700")
        root.addWidget(self.start_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Дата", "Наименование", "Поступление", "Оплата", "Остаток"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, stretch=1)

        self.totals_label = QLabel("")
        self.totals_label.setStyleSheet(
            f"font-weight:800;color:{styles.BLUE}"
        )
        root.addWidget(self.totals_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self.refresh()

    # ------------------------------------------------------------- расчёты
    def movement(self) -> calc.FundsMovement:
        return calc.funds_movement(self.store.data, self.on_date, self.days)

    def refresh(self) -> None:
        movement = self.movement()
        self.period_label.setText(
            f"за {self.days} дней: "
            f"{fmt_date(movement.start)} – {fmt_date(movement.end)}"
        )
        self.start_label.setText(
            f"Остаток на счету на {fmt_date(movement.start)}: "
            f"{fmt_money_plain(movement.start_funds)}"
        )

        table = self.table
        table.setRowCount(len(movement.rows) or 1)
        if not movement.rows:
            empty = QTableWidgetItem("За этот период записей нет")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            table.setItem(0, 0, empty)
            table.setSpan(0, 0, 1, 5)
        else:
            table.clearSpans()
            for row, entry in enumerate(movement.rows):
                values = (
                    fmt_date(entry.day),
                    entry.name,
                    fmt_money_plain(entry.income) if entry.income else "",
                    fmt_money_plain(entry.payment) if entry.payment else "",
                    fmt_money_plain(entry.balance),
                )
                for column, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    if column >= 2:
                        item.setTextAlignment(_MONEY_ALIGN)
                    table.setItem(row, column, item)

        self.totals_label.setText(
            f"Итого поступление: {fmt_money_plain(movement.income)}"
            f"    Итого оплаты: {fmt_money_plain(movement.payments)}"
            f"    Итого остаток: {fmt_money_plain(movement.balance)}"
        )
