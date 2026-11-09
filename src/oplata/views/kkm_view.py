"""Вкладка «Отчеты ККМ» (v0.42.0) — реестр отчётов кассы за месяц.

Правила, заданные пользователем:

* таблица **на месяц**, столбцы **Дата / Отчет / Сумма**;
* на одну дату приходится несколько строк: в базе **МИР** — две
  «Отчет ККМ» и одна «Возврат покупателя» (порядок именно такой),
  в базе **КОРЕЛИЧИ** — одна «Отчет ККМ» и одна «Возврат покупателя»
  (состав и порядок строк — ``models.KKM_DAY_KINDS``);
* «Сумма» — поле ввода; пусто или 0 удаляет запись;
* сверху — переход по месяцам и **итог за месяц** тремя цифрами:
  отчёты ККМ, возвраты и разница (отчёты минус возвраты).

Реестр самостоятельный: на расчёты денег («Поступление», касса,
долги, сводки) он не влияет — это отдельный учёт по решению
пользователя.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import calc, styles
from ..dates import fmt_date, fmt_money, fmt_money_plain, month_title, parse_money
from ..models import KKM_DAY_KINDS, KKM_KIND_TITLES, find_kkm_report, set_kkm_amount
from ..storage import DataStore
from ..widgets import AmountLineEdit, arrow_button

COL_DATE, COL_KIND, COL_AMOUNT = range(3)
HEADERS = ("Дата", "Отчет", "Сумма")
# v0.43.0: таблица стоит **тремя вертикальными рядами** по 10 дней —
# так весь месяц виден сразу, без вертикальной прокрутки. Между
# рядами — узкое вертикальное поле-разделитель (колонка без данных).
BLOCKS = 3
DAYS_PER_BLOCK = 10
COLUMNS_PER_BLOCK = len(HEADERS)
SEPARATOR_WIDTH = 8
COLUMN_COUNT = BLOCKS * COLUMNS_PER_BLOCK + (BLOCKS - 1)
SEPARATOR_COLUMNS = tuple(
    block * (COLUMNS_PER_BLOCK + 1) + COLUMNS_PER_BLOCK
    for block in range(BLOCKS - 1)
)


def _block_first_column(block: int) -> int:
    """Первая колонка вертикального ряда ``block``."""

    return block * (COLUMNS_PER_BLOCK + 1)


def _is_amount_column(column: int) -> bool:
    """Колонка «Сумма» одного из трёх рядов (v0.43.0)."""

    return column not in SEPARATOR_COLUMNS and (
        column % (COLUMNS_PER_BLOCK + 1) == COL_AMOUNT
    )
# v0.42.0: данные ячейки — «дата|номер строки»; по ним обработчик
# находит запись, которую надо сохранить.
META_ROLE = Qt.ItemDataRole.UserRole
ZERO = Decimal("0")


class _AmountDelegate(QStyledItemDelegate):
    """Редактор суммы в ячейке: без мешающих нулей (AmountLineEdit)."""

    def createEditor(self, parent, option, index):  # Qt API: имя задано Qt
        return AmountLineEdit(parent=parent)


def _money_text(amount: Decimal) -> str:
    """Сумма для ячейки: с разделителями, пусто вместо нуля."""

    return fmt_money_plain(amount) if amount else ""


class KkmView(QWidget):
    """Реестр «Отчеты ККМ»: таблица на месяц с итогом (v0.42.0)."""

    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self.store = store
        # v0.18.0: без права ввода данных таблица только для просмотра.
        self.read_only = False
        self._rebuilding = False  # True, пока таблицу меняет код, а не человек
        self._month = date.today().replace(day=1)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addLayout(self._build_toolbar())

        self.table = QTableWidget()
        self.table.setColumnCount(COLUMN_COUNT)
        # Подписи повторяются для каждого ряда; у разделителей их нет.
        self.table.setHorizontalHeaderLabels(
            [
                HEADERS[column % (COLUMNS_PER_BLOCK + 1)]
                if column not in SEPARATOR_COLUMNS
                else ""
                for column in range(COLUMN_COUNT)
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(
            f"QTableWidget{{font-size:{styles.fs(12)}px}}"
            f"{styles.WIDE_SCROLLBAR_ON_TABLE}"
        )
        # Прокрутки нет ни по вертикали, ни по горизонтали: месяц
        # делится на три ряда по 10 дней, а высота строк растягивается
        # под размер окна (v0.43.0).
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows_header = self.table.verticalHeader()
        rows_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rows_header.setMinimumSectionSize(14)
        header = self.table.horizontalHeader()
        for block in range(BLOCKS):
            first = _block_first_column(block)
            header.setSectionResizeMode(
                first + COL_DATE, QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(
                first + COL_KIND, QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(
                first + COL_AMOUNT, QHeaderView.ResizeMode.Stretch
            )
        for column in SEPARATOR_COLUMNS:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        # Разделитель — узкая полоска: шире минимума по умолчанию не
        # пускаем, иначе вместо линии получается широкая колонка.
        header.setMinimumSectionSize(SEPARATOR_WIDTH)
        for column in SEPARATOR_COLUMNS:
            self.table.setColumnWidth(column, SEPARATOR_WIDTH)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.table.setItemDelegate(_AmountDelegate(self.table))
        self.table.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.table, stretch=1)

        self.refresh()

    # ------------------------------------------------------------------- UI
    def _build_toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        self.prev_button = arrow_button("←", "Предыдущий месяц")
        self.prev_button.clicked.connect(self._prev_month)
        layout.addWidget(self.prev_button)
        self.month_label = QLabel(month_title(self._month))
        self.month_label.setStyleSheet(
            f"font-size:{styles.fs(16)}px;font-weight:800;color:{styles.TEXT};"
        )
        self.month_label.setMinimumWidth(220)
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.month_label)
        self.next_button = arrow_button("→", "Следующий месяц")
        self.next_button.clicked.connect(self._next_month)
        layout.addWidget(self.next_button)

        layout.addSpacing(24)
        # v0.42.0: итог за месяц — отчёты ККМ, возвраты и разница.
        self.reports_label = QLabel("")
        self.returns_label = QLabel("")
        self.diff_label = QLabel("")
        for label, color in (
            (self.reports_label, styles.TEXT),
            (self.returns_label, styles.RED),
            (self.diff_label, styles.TEXT),
        ):
            label.setStyleSheet(
                f"font-size:{styles.fs(14)}px;font-weight:700;color:{color};"
            )
            layout.addWidget(label)
        layout.addStretch(1)
        return layout

    # ------------------------------------------------------------- навигация
    def _prev_month(self) -> None:
        self._shift_month(-1)

    def _next_month(self) -> None:
        self._shift_month(1)

    def _shift_month(self, step: int) -> None:
        """Сдвинуть показанный месяц на ``step`` месяцев."""

        month_index = self._month.month - 1 + step
        year = self._month.year + month_index // 12
        month = month_index % 12 + 1
        self.set_month(date(year, month, 1))

    def set_month(self, day: date) -> None:
        """Показать месяц, в который попадает ``day``."""

        self._month = day.replace(day=1)
        self.refresh()

    # -------------------------------------------------------------- обновление
    def refresh(self) -> None:
        """Перестроить таблицу и пересчитать итог месяца."""

        self.month_label.setText(month_title(self._month))
        self._fill_table()
        self._update_totals()

    def _day_kinds(self) -> tuple[str, ...]:
        """Какие строки приходятся на один день в активной базе."""

        return KKM_DAY_KINDS.get(self.store.base_key, KKM_DAY_KINDS["mir"])

    def _month_days(self) -> list[date]:
        year, month = self._month.year, self._month.month
        last = calendar.monthrange(year, month)[1]
        return [date(year, month, day) for day in range(1, last + 1)]

    def _day_blocks(self, days: list[date]) -> list[list[date]]:
        """Разбить дни месяца на ``BLOCKS`` вертикальных рядов по 10 дней.

        v0.43.0: весь месяц должен быть виден сразу, поэтому дни идут не
        одним столбцом, а тремя: 1–10, 11–20, 21–31 (в последнем ряду
        остаток — 8–11 дней в зависимости от месяца).
        """

        size = len(days)
        per_block = min(DAYS_PER_BLOCK, size)
        blocks = [
            days[start : start + per_block]
            for start in range(0, size, per_block)
        ]
        # Месяц в 31 день: третий ряд забирает остаток (11-й день).
        if len(blocks) > BLOCKS:
            blocks = blocks[: BLOCKS - 1] + [
                days[(BLOCKS - 1) * per_block :]
            ]
        return blocks

    def _fill_table(self) -> None:
        days = self._month_days()
        blocks = self._day_blocks(days)
        kinds = self._day_kinds()
        today = date.today()
        rows = max((len(block) for block in blocks), default=0) * len(kinds)
        self._rebuilding = True
        try:
            self.table.setRowCount(rows)
            for block_index, block in enumerate(blocks):
                first = _block_first_column(block_index)
                for day_index, day in enumerate(block):
                    day_iso = day.isoformat()
                    today_bg = (
                        styles.WEEK_TABLE_TODAY_GREEN_BODY if day == today else None
                    )
                    for slot, kind in enumerate(kinds):
                        row = day_index * len(kinds) + slot
                        self._put(
                            row, first + COL_DATE, fmt_date(day), bg=today_bg,
                            align=Qt.AlignmentFlag.AlignCenter,
                        )
                        self._put(
                            row, first + COL_KIND, KKM_KIND_TITLES[kind],
                            bg=today_bg,
                        )
                        record = find_kkm_report(self.store.data, day_iso, slot)
                        amount = (
                            Decimal(record.amount) if record is not None else ZERO
                        )
                        self._put(
                            row, first + COL_AMOUNT, _money_text(amount),
                            bg=today_bg, editable=not self.read_only,
                            align=Qt.AlignmentFlag.AlignRight,
                        )
                        self.table.item(row, first + COL_AMOUNT).setData(
                            META_ROLE, f"{day_iso}|{slot}"
                        )
            # Разделители: узкие колонки с заливкой, без данных и ввода.
            for column in SEPARATOR_COLUMNS:
                for row in range(rows):
                    self._put(row, column, "", bg=styles.BORDER)
        finally:
            self._rebuilding = False

    def _put(
        self,
        row: int,
        column: int,
        text: str,
        *,
        bg: str | None = None,
        editable: bool = False,
        align: Qt.AlignmentFlag | None = None,
    ) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)
        if bg is not None:
            item.setBackground(QColor(bg))
        if align is not None:
            item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, column, item)
        return item

    def _update_totals(self) -> None:
        reports, returns, diff = calc.kkm_month_totals(
            self.store.data, self._month.year, self._month.month
        )
        self.reports_label.setText(f"Отчеты ККМ: {fmt_money(reports)}")
        self.returns_label.setText(f"Возвраты: {fmt_money(returns)}")
        self.diff_label.setText(f"Разница: {fmt_money(diff)}")

    # ------------------------------------------------------------------ ввод
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Сохранить введённую сумму (v0.42.0)."""

        if self._rebuilding or not _is_amount_column(item.column()):
            return
        meta = item.data(META_ROLE)
        if not isinstance(meta, str) or "|" not in meta:
            return
        day_iso, slot_text = meta.split("|", 1)
        try:
            slot = int(slot_text)
        except ValueError:
            return
        kinds = self._day_kinds()
        if slot >= len(kinds):
            return
        amount = parse_money(item.text())
        self._rebuilding = True
        try:
            set_kkm_amount(
                self.store.data, day_iso, slot, kinds[slot], amount
            )
            self.store.save()
            # В ячейку кладём ровно то, что сохранили: «1 000» станет
            # «1 000,00», а мусор — пустым местом.
            item.setText(_money_text(amount) if amount else "")
        finally:
            self._rebuilding = False
        self._update_totals()

    # ----------------------------------------------------------------- права
    def set_read_only(self, read_only: bool) -> None:
        """Права пользователя: «только просмотр» или полный доступ (v0.18.0)."""

        if read_only == self.read_only:
            return
        self.read_only = read_only
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if read_only
            else QAbstractItemView.EditTrigger.AllEditTriggers
        )
        self.refresh()
