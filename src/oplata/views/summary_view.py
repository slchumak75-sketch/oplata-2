"""Экран «Сводка» (макет 01-summary.png).

Все цифры — заглушки ``calc.py`` (``0,00 руб.``), формул пока нет.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import calc, styles
from ..dates import fmt_date, fmt_money, fmt_money_plain
from ..storage import DataStore
from ..widgets import date_edit_value, kpi_card, make_date_edit, make_panel


class SummaryView(QWidget):
    def __init__(
        self,
        store: DataStore,
        on_open_supplier: Callable[[str | None], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_open_supplier = on_open_supplier

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # --- верхняя строка: дата, архивные, печать ---
        top = QHBoxLayout()
        date_label = QLabel("Сводка на дату")
        date_label.setStyleSheet("font-weight:700")
        top.addWidget(date_label)
        self.date_edit = make_date_edit(date.today())
        self.date_edit.dateChanged.connect(lambda *_: self.refresh())
        top.addWidget(self.date_edit)
        self.show_archived = QCheckBox("показывать архивных")
        self.show_archived.toggled.connect(lambda *_: self.refresh())
        top.addWidget(self.show_archived)
        top.addStretch(1)
        print_btn = QPushButton("🖨  Печать")
        print_btn.clicked.connect(self._print_stub)
        top.addWidget(print_btn)
        root.addLayout(top)

        # --- 6 KPI-карточек ---
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.kpi_values: dict[str, QLabel] = {}
        kpi_defs = [
            ("total_debt", "ВСЕГО ЗАДОЛЖЕННОСТЬ", False),
            ("overdue", "ПРОСРОЧЕНО", False),
            ("due_today", "СРОК СЕГОДНЯ", False),
            ("due_next_7", "СРОК В БЛИЖАЙШИЕ 7 ДНЕЙ", False),
            ("income_month", "ПРИХОД ЗА МЕСЯЦ", False),
            ("paid_month", "ОПЛАЧЕНО ЗА МЕСЯЦ", True),  # на макете зелёная
        ]
        for key, title, green in kpi_defs:
            card, value_label = kpi_card(title, fmt_money(calc.ZERO), green_value=green)
            self.kpi_values[key] = value_label
            kpi_row.addWidget(card, stretch=1)
        root.addLayout(kpi_row)

        # --- таблица задолженности по поставщикам ---
        panel, panel_layout = make_panel()
        title = QLabel("Задолженность по поставщикам")
        title.setObjectName("PanelTitle")
        panel_layout.addWidget(title)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Поставщик", "Задолженность", "Просрочено", "Ближайший срок", "Сумма срока"]
        )
        header_item = self.table.horizontalHeaderItem(0)
        if header_item is not None:
            header_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.itemClicked.connect(self._on_item_clicked)
        panel_layout.addWidget(self.table)
        root.addWidget(panel, stretch=1)

        # --- просроченные оплаты ---
        overdue_panel, overdue_layout = make_panel()
        self.overdue_title = QLabel("🔴  Просроченные оплаты (0)")
        self.overdue_title.setObjectName("PanelTitle")
        overdue_layout.addWidget(self.overdue_title)
        self.overdue_text = QLabel("Просроченных оплат нет 🎉")
        self.overdue_text.setObjectName("Muted")
        overdue_layout.addWidget(self.overdue_text)
        root.addWidget(overdue_panel)

        self.refresh()

    def _print_stub(self) -> None:
        # TODO(этап печати): реальная печать сводки (см. ROADMAP).
        QMessageBox.information(
            self,
            "Печать",
            "Печать пока не реализована — сейчас делается только оболочка "
            "программы (см. docs/ROADMAP.md).",
        )

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            supplier_id = item.data(Qt.ItemDataRole.UserRole)
            if supplier_id:
                self.on_open_supplier(str(supplier_id))

    def refresh(self) -> None:
        on_date = date_edit_value(self.date_edit)

        totals = calc.summary_totals(on_date)  # заглушка: все нули
        self.kpi_values["total_debt"].setText(fmt_money(totals.total_debt))
        self.kpi_values["overdue"].setText(fmt_money(totals.overdue))
        self.kpi_values["due_today"].setText(fmt_money(totals.due_today))
        self.kpi_values["due_next_7"].setText(fmt_money(totals.due_next_7_days))
        self.kpi_values["income_month"].setText(fmt_money(totals.income_month))
        self.kpi_values["paid_month"].setText(fmt_money(totals.paid_month))

        suppliers = [
            s for s in self.store.data.suppliers
            if self.show_archived.isChecked() or not s.archived
        ]
        self.table.setRowCount(len(suppliers))
        for row, supplier in enumerate(suppliers):
            debt_row = calc.supplier_debt_row(supplier.id, on_date)  # заглушка

            name_item = QTableWidgetItem(supplier.name)
            name_item.setForeground(QColor(styles.LINK_BLUE))
            font = QFont()
            font.setUnderline(True)
            name_item.setFont(font)
            name_item.setData(Qt.ItemDataRole.UserRole, supplier.id)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 0, name_item)

            values = [
                fmt_money_plain(debt_row.debt),
                fmt_money_plain(debt_row.overdue),
                fmt_date(debt_row.next_due_date) if debt_row.next_due_date else "—",
                (
                    fmt_money_plain(debt_row.next_due_amount)
                    if debt_row.next_due_amount is not None
                    else "—"
                ),
            ]
            for col, text in enumerate(values, start=1):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight if col != 3 else Qt.AlignmentFlag.AlignCenter
                )
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)

        overdue = calc.overdue_payments(on_date)  # заглушка: пустой список
        self.overdue_title.setText(f"🔴  Просроченные оплаты ({len(overdue)})")
        self.overdue_text.setText(
            "Просроченных оплат нет 🎉" if not overdue else f"Просрочено операций: {len(overdue)}"
        )
