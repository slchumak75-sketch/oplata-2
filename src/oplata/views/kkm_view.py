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
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(list(HEADERS))
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet(
            f"QTableWidget{{font-size:{styles.fs(12)}px}}"
            f"{styles.WIDE_SCROLLBAR_ON_TABLE}"
        )
        # Горизонтальной прокрутки нет: дата и вид — по содержимому,
        # а колонка «Сумма» занимает всё оставшееся место.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_KIND, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_AMOUNT, QHeaderView.ResizeMode.Stretch)
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

    def _fill_table(self) -> None:
        days = self._month_days()
        kinds = self._day_kinds()
        today = date.today()
        self._rebuilding = True
        try:
            self.table.setRowCount(len(days) * len(kinds))
            row = 0
            for day in days:
                day_iso = day.isoformat()
                today_bg = (
                    styles.WEEK_TABLE_TODAY_GREEN_BODY if day == today else None
                )
                for slot, kind in enumerate(kinds):
                    self._put(
                        row, COL_DATE, fmt_date(day), bg=today_bg,
                        align=Qt.AlignmentFlag.AlignCenter,
                    )
                    self._put(
                        row, COL_KIND, KKM_KIND_TITLES[kind], bg=today_bg,
                    )
                    record = find_kkm_report(self.store.data, day_iso, slot)
                    amount = Decimal(record.amount) if record is not None else ZERO
                    self._put(
                        row, COL_AMOUNT, _money_text(amount), bg=today_bg,
                        editable=not self.read_only,
                        align=Qt.AlignmentFlag.AlignRight,
                    )
                    self.table.item(row, COL_AMOUNT).setData(
                        META_ROLE, f"{day_iso}|{slot}"
                    )
                    row += 1
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

        if self._rebuilding or item.column() != COL_AMOUNT:
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
