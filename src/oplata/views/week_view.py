"""Недельная таблица — экраны «Поставщики» и «Покупатели» (макеты 02/03).

Один класс на оба экрана: разница только в заголовке первой колонки,
правой части тулбара и списке контрагентов.

Этап оболочки:
* навигация по неделям работает (это календарь, не формула);
* ячейки Приход/Долг/Оплата пустые и редактируемые «для вида» —
  ввод НИКУДА НЕ СОХРАНЯЕТСЯ (TODO этапа операций);
* все суммы — заглушки из ``calc.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from itertools import groupby

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import calc, styles
from ..dates import WEEKDAYS_RU_SHORT, fmt_date, fmt_money, month_title, week_days, week_monday
from ..storage import DataStore
from ..widgets import danger_button, date_edit_value, make_date_edit, set_date_edit

HEADER_ROWS = 3
TOTAL_ROW = 3
FIRST_DATA_ROW = 4
COLS = 3 + 7 * 3  # имя + 2 просрочки + 7 дней * (Приход/Долг/Оплата)
SUBHEADERS = ("Приход", "Долг", "Оплата")
SUBHEADER_KINDS = ("income", "debt", "payment")


class WeekView(QWidget):
    """``kind``: ``"supplier"`` | ``"customer"``."""

    def __init__(
        self,
        store: DataStore,
        kind: str,
        on_open_card: Callable[[str | None], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        assert kind in ("supplier", "customer")
        self.store = store
        self.kind = kind
        self.on_open_card = on_open_card
        self.week_start = week_monday(date.today())

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addLayout(self._build_toolbar())

        self.table = QTableWidget()
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self.table, stretch=1)
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        prev_btn = QPushButton("← Неделя")
        prev_btn.clicked.connect(lambda: self._shift_week(-7))
        bar.addWidget(prev_btn)

        self.week_label = QLabel()
        self.week_label.setStyleSheet("font-weight:700;font-size:14px")
        bar.addWidget(self.week_label)

        next_btn = QPushButton("Неделя →")
        next_btn.clicked.connect(lambda: self._shift_week(7))
        bar.addWidget(next_btn)

        today_btn = QPushButton("Сегодня")
        today_btn.clicked.connect(self._go_today)
        bar.addWidget(today_btn)

        bar.addWidget(QLabel("Дата"))
        self.date_edit = make_date_edit(self.week_start)
        bar.addWidget(self.date_edit)

        go_btn = QPushButton("Перейти")
        go_btn.clicked.connect(self._go_to_date)
        bar.addWidget(go_btn)

        bar.addStretch(1)

        if self.kind == "supplier":
            clear_btn = danger_button("Очистить суммы")
            clear_btn.clicked.connect(self._clear_amounts)
            bar.addWidget(clear_btn)
            debt_title = QLabel("Задолженность за неделю:")
            debt_title.setObjectName("Muted")
            bar.addWidget(debt_title)
            self.right_value_label = QLabel(fmt_money(calc.week_debt(self.week_start)))
            self.right_value_label.setStyleSheet("font-weight:800;font-size:15px")
            bar.addWidget(self.right_value_label)
        else:
            count_title = QLabel("Покупателей:")
            count_title.setObjectName("Muted")
            bar.addWidget(count_title)
            self.right_value_label = QLabel("0")
            self.right_value_label.setStyleSheet("font-weight:800;font-size:15px")
            bar.addWidget(self.right_value_label)
        return bar

    # ------------------------------------------------------------- навигация
    def _shift_week(self, days: int) -> None:
        self.week_start += timedelta(days=days)
        self.refresh()

    def _go_today(self) -> None:
        self.week_start = week_monday(date.today())
        self.refresh()

    def _go_to_date(self) -> None:
        self.week_start = week_monday(date_edit_value(self.date_edit))
        self.refresh()

    def _clear_amounts(self) -> None:
        """Кнопка «Очистить суммы»: стирает ручной ввод в ячейках таблицы."""
        for row in range(FIRST_DATA_ROW, self.table.rowCount()):
            for col in range(3, COLS):
                item = self.table.item(row, col)
                if item is not None:
                    item.setText("")

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and item.row() >= FIRST_DATA_ROW:
            counterparty_id = item.data(Qt.ItemDataRole.UserRole)
            if counterparty_id:
                self.on_open_card(str(counterparty_id))

    # --------------------------------------------------------------- данные
    def counterparties(self):
        items = (
            self.store.data.suppliers
            if self.kind == "supplier"
            else self.store.data.customers
        )
        return [c for c in items if not c.archived]

    def refresh(self) -> None:
        partners = self.counterparties()
        days = week_days(self.week_start)
        today = date.today()
        set_date_edit(self.date_edit, self.week_start)
        self.week_label.setText(
            f"Неделя: {fmt_date(days[0])} — {fmt_date(days[-1])}"
        )
        if self.kind == "customer":
            self.right_value_label.setText(str(len(partners)))
        else:
            # Заглушка из calc.py — формулы пока нет.
            self.right_value_label.setText(fmt_money(calc.week_debt(self.week_start)))
        self._rebuild_table(partners, days, today)

    # --------------------------------------------------------------- таблица
    def _rebuild_table(self, partners, days, today) -> None:
        table = self.table
        table.clear()
        table.clearSpans()
        table.setRowCount(FIRST_DATA_ROW + len(partners))
        table.setColumnCount(COLS)

        table.setColumnWidth(0, 150)
        table.setColumnWidth(1, 80)
        table.setColumnWidth(2, 80)
        for col in range(3, COLS):
            table.setColumnWidth(col, 52)  # вся неделя влезает в окно ~1440px
        for row in range(HEADER_ROWS):
            table.setRowHeight(row, 24)

        def put(row, col, text, *, bg=None, bold=False, center=True,
                editable=False, color=None, underline=False, size=None):
            item = QTableWidgetItem(text)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if editable:
                flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            if center:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if bg:
                item.setBackground(QColor(bg))
            font = QFont()
            font.setBold(bold)
            font.setUnderline(underline)
            if size:
                font.setPixelSize(size)  # подзаголовки на макете мельче основного шрифта
            item.setFont(font)
            if color:
                item.setForeground(QColor(color))
            table.setItem(row, col, item)
            return item

        pink, beige = styles.HEADER_PINK, styles.HEADER_BEIGE

        # Строка 0: месяцы (неделя может захватывать 1–2 месяца)
        put(0, 0, "", bg=styles.HEADER_MONTH)
        table.setSpan(0, 0, 1, 3)
        col = 3
        for (_year, _month), group in groupby(days, key=lambda d: (d.year, d.month)):
            group_days = list(group)
            put(0, col, month_title(group_days[0]), bg=styles.HEADER_MONTH, bold=True)
            table.setSpan(0, col, 1, len(group_days) * 3)
            col += len(group_days) * 3

        # Строка 1: дни недели
        put(1, 0, "ПОСТАВЩИК" if self.kind == "supplier" else "ПОКУПАТЕЛЬ",
            bg=pink, bold=True, size=12)
        put(1, 1, "ПРОСРОЧЕН. ЗАДОЛЖ.", bg=pink, bold=True, size=11)
        table.setSpan(1, 1, 1, 2)
        for i, day in enumerate(days):
            bg = beige if day.weekday() >= 5 else pink
            c = 3 + i * 3
            put(1, c, f"{day.day}  {WEEKDAYS_RU_SHORT[day.weekday()]}",
                bg=bg, bold=True, size=12)
            table.setSpan(1, c, 1, 3)

        # Строка 2: подзаголовки
        put(2, 0, "", bg=pink)
        put(2, 1, "на сегодня", bg=pink, bold=True, size=11)
        put(2, 2, "за неделю", bg=pink, bold=True, size=11)
        for i, day in enumerate(days):
            if day == today:
                bg = styles.TODAY_YELLOW  # текущий день подсвечен жёлтым на макете
            else:
                bg = beige if day.weekday() >= 5 else "#f1f5fb"
            for j, sub in enumerate(SUBHEADERS):
                put(2, 3 + i * 3 + j, sub, bg=bg, bold=True, size=10)

        # Строка «ВСЕГО» (суммы — заглушки, поэтому пусто/нули позже даст calc)
        put(TOTAL_ROW, 0, "ВСЕГО", bold=True, center=False)
        for c in range(1, COLS):
            put(TOTAL_ROW, c, "")

        # Строки контрагентов
        for r, partner in enumerate(partners):
            row = FIRST_DATA_ROW + r
            name_item = put(row, 0, partner.name, center=False,
                            color=styles.LINK_BLUE, underline=True)
            name_item.setData(Qt.ItemDataRole.UserRole, partner.id)
            put(row, 1, "")
            put(row, 2, "")
            for i, day in enumerate(days):
                for j, kind_name in enumerate(SUBHEADER_KINDS):
                    # Заглушка calc.week_cell_value -> "" (формул пока нет).
                    value = calc.week_cell_value(partner.id, day, kind_name)
                    put(row, 3 + i * 3 + j, value, editable=True)
