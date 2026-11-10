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
from PySide6.QtGui import QColor, QPen
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
# v0.47.0: строка таблицы не сжимается ниже этой высоты. Полный месяц —
# это 30 строк (10 дней × 3 строки у МИР); в окне в половину экрана
# (~520 точек) под таблицу остаётся около 370, и строки упирались бы в
# 14 точек, а последние дни месяца просто пропадали: прокрутки в
# таблице не было вовсе. Теперь строки остаются читаемыми, а месяц
# прокручивается по вертикали — но только когда не влезает целиком.
MIN_ROW_HEIGHT = 24
# v0.45.0: поле между вертикальными рядами — 16 точек (в v0.44.0 было
# 80, оказалось слишком много). Таблица **не растягивается** на всю
# ширину экрана: справа остаётся свободное место.
SEPARATOR_WIDTH = 16
# v0.47.0: «Сумма» **вдвое у́же, чем была**: берём её ширину из v0.45.0
# (доля ряда, делённая на 1,5 — при окне 1920 это 236 точек) и делим
# пополам — получается доля, делённая на три (при 1920 — 118 точек).
# Пол ``AMOUNT_MIN_WIDTH`` (96 точек — в эту ширину влезает
# «88 888,88») оставлен страховкой: если окно узкое, точная половина
# выходит меньше, и сумма обрезалась бы многоточием. На экране 1920
# пол не мешает: 118 > 96.
AMOUNT_WIDTH_RATIO = 1 / 3
AMOUNT_MIN_WIDTH = 96
# v0.44.0: зебра для читаемости — **один день — один цвет**: все строки
# дня (у МИР их три, у КОРЕЛИЧИ две) залиты одинаково, соседние дни
# отличаются. v0.45.0: контраст был слишком сильным — оба тона стали
# светлее (почти белый и очень светлый песок).
ZEBRA_EVEN = styles.PANEL_BG      # почти белый
ZEBRA_ODD = styles.PAGE_BG        # очень светлый песок
# v0.45.0: у каждого вертикального ряда своя обводка (цвет границы
# программы); поля между рядами не закрашиваются.
BLOCK_FRAME = styles.BORDER
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
    """Редактор суммы в ячейке: без мешающих нулей (AmountLineEdit).

    v0.45.0: тот же делегат рисует **обводку вертикального ряда**: у
    каждого из трёх рядов дней своя рамка, а поля между рядами
    остаются пустыми (не закрашиваются).
    """

    def createEditor(self, parent, option, index):  # Qt API: имя задано Qt
        return AmountLineEdit(parent=parent)

    def paint(self, painter, option, index):  # Qt API: имя задано Qt
        super().paint(painter, option, index)
        column = index.column()
        if column in SEPARATOR_COLUMNS:
            return
        block = column // (COLUMNS_PER_BLOCK + 1)
        first = _block_first_column(block)
        rows = index.model().rowCount()
        pen = QPen(QColor(BLOCK_FRAME))
        pen.setWidth(1)
        painter.setPen(pen)
        rect = option.rect
        left = rect.left() + 0.5
        right = rect.right() - 0.5
        top = rect.top() + 0.5
        bottom = rect.bottom() - 0.5
        # Внешние стороны рамки — у крайних колонок и крайних строк.
        if column == first:
            painter.drawLine(left, top, left, bottom)
        if column == first + COLUMNS_PER_BLOCK - 1:
            painter.drawLine(right, top, right, bottom)
        if index.row() == 0:
            painter.drawLine(left, top, right, top)
        if index.row() == rows - 1:
            painter.drawLine(left, bottom, right, bottom)


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
        # v0.47.0: была ли видна вертикальная полоса прокрутки при
        # прошлом расчёте ширин (см. ``_fit_columns``).
        self._vscroll_seen = False
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
        # По горизонтали прокрутки нет: месяц делится на три ряда по 10
        # дней, а ширины колонок считает ``_fit_columns`` (v0.43.0).
        # v0.47.0: по вертикали прокрутка **появляется, когда месяц не
        # влезает целиком** (окно в половину экрана): строки больше не
        # сжимаются ниже ``MIN_ROW_HEIGHT``, и до конца месяца можно
        # докрутить. В полном экране месяц влезает, и прокрутки нет —
        # как и было раньше.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rows_header = self.table.verticalHeader()
        rows_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        rows_header.setMinimumSectionSize(MIN_ROW_HEIGHT)
        header = self.table.horizontalHeader()
        for block in range(BLOCKS):
            first = _block_first_column(block)
            header.setSectionResizeMode(
                first + COL_DATE, QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(
                first + COL_KIND, QHeaderView.ResizeMode.ResizeToContents
            )
            # «Сумма» не растягивается: ширину считает ``_fit_columns``.
            header.setSectionResizeMode(
                first + COL_AMOUNT, QHeaderView.ResizeMode.Fixed
            )
        for column in SEPARATOR_COLUMNS:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(8)
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
        # v0.44.0: третья цифра называется «Итого по Отчетам ККМ»
        # (это отчеты за вычетом возвратов).
        self.reports_label = QLabel("")
        self.returns_label = QLabel("")
        self.total_label = QLabel("")
        for label, color in (
            (self.reports_label, styles.TEXT),
            (self.returns_label, styles.RED),
            (self.total_label, styles.TEXT),
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
                    # v0.44.0: зебра по дням — у всех строк одного дня
                    # одинаковый цвет (светлый/светло-песочный);
                    # сегодняшний день остаётся светло-зелёным.
                    if day == today:
                        day_bg = styles.WEEK_TABLE_TODAY_GREEN_BODY
                    else:
                        day_bg = ZEBRA_ODD if day.day % 2 else ZEBRA_EVEN
                    for slot, kind in enumerate(kinds):
                        row = day_index * len(kinds) + slot
                        self._put(
                            row, first + COL_DATE, fmt_date(day), bg=day_bg,
                            align=Qt.AlignmentFlag.AlignCenter,
                        )
                        self._put(
                            row, first + COL_KIND, KKM_KIND_TITLES[kind],
                            bg=day_bg,
                        )
                        record = find_kkm_report(self.store.data, day_iso, slot)
                        amount = (
                            Decimal(record.amount) if record is not None else ZERO
                        )
                        self._put(
                            row, first + COL_AMOUNT, _money_text(amount),
                            bg=day_bg, editable=not self.read_only,
                            align=Qt.AlignmentFlag.AlignRight,
                        )
                        self.table.item(row, first + COL_AMOUNT).setData(
                            META_ROLE, f"{day_iso}|{slot}"
                        )
            # v0.45.0: поля между рядами **не закрашиваются** — это
            # пустые колонки без данных и без ввода; ряды разделяет
            # обводка, которую рисует делегат.
            for column in SEPARATOR_COLUMNS:
                for row in range(rows):
                    self._put(row, column, "")
        finally:
            self._rebuilding = False
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Разложить колонки по ширине окна (v0.44.0, переделано v0.45.0).

        «Дата» и «Отчет» — по содержимому; «Сумма» занимает **1/3**
        своей доли — вдвое меньше, чем в v0.45.0 (там была 1/1,5);
        поле между рядами — ровно ``SEPARATOR_WIDTH``.
        v0.45.0: таблица **не растягивается** на всю ширину экрана —
        свободное место остаётся справа, поля между рядами не растут.
        """

        self.table.resizeColumnsToContents()
        viewport = self.table.viewport().width()
        if viewport <= 0:
            return
        # v0.47.0: вертикальная полоса прокрутки (она появляется в окне
        # в половину экрана) занимает часть ширины. Если её видимость
        # изменилась, ширины считаются заново — по новому ``viewport``.
        scrollbar_seen = self.table.verticalScrollBar().isVisible()
        if scrollbar_seen != self._vscroll_seen:
            self._vscroll_seen = scrollbar_seen
            self._fit_columns()
            return
        fixed = 0
        for block in range(BLOCKS):
            first = _block_first_column(block)
            fixed += self.table.columnWidth(first + COL_DATE)
            fixed += self.table.columnWidth(first + COL_KIND)
        gaps = len(SEPARATOR_COLUMNS)
        gap = SEPARATOR_WIDTH
        # Доля одного ряда без полей; «Сумма» — треть доли (вдвое у́же v0.45.0).
        share = max(viewport - fixed, 0) / BLOCKS
        amount = max(int(share * AMOUNT_WIDTH_RATIO), AMOUNT_MIN_WIDTH)
        if fixed + amount * BLOCKS + gap * gaps > viewport:
            # Окно узкое: сначала сжимаем поле, потом «Сумму».
            gap = max(4, (viewport - fixed - amount * BLOCKS) // gaps)
        if fixed + amount * BLOCKS + gap * gaps > viewport:
            amount = max(60, (viewport - fixed - 4 * gaps) // BLOCKS)
            gap = max(4, (viewport - fixed - amount * BLOCKS) // gaps)
        for block in range(BLOCKS):
            self.table.setColumnWidth(
                _block_first_column(block) + COL_AMOUNT, amount
            )
        for column in SEPARATOR_COLUMNS:
            self.table.setColumnWidth(column, gap)

    def resizeEvent(self, event) -> None:  # Qt API: имя задано Qt
        super().resizeEvent(event)
        # Ширины колонок считаются от ширины окна, поэтому пересчитываем
        # их при каждом изменении размера.
        self._fit_columns()

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
        self.total_label.setText(f"Итого по Отчетам ККМ: {fmt_money(diff)}")

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
