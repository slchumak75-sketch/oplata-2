"""Недельная таблица — экраны «Поставщики» и «Покупатели» (макеты 02/03).

Один класс на оба экрана: разница только в заголовке первой колонки,
правой части тулбара и списке контрагентов.

Состояние (v0.2.0):
* навигация по неделям работает (это календарь, не формула);
* ячейки **Приход** и **Оплата** редактируемые: введённая сумма сразу
  сохраняется как операция дня (см. ``models.set_daily_operation``),
  видна в «Журнале операций» и переживает перезапуск программы;
  пустая ячейка или 0 — операция дня удаляется;
* колонка **Долг** НЕ редактируется — это будущая формула (заглушка);
* строка «ВСЕГО» суммирует только введённые Приход/Оплата;
* таблица растягивается на всю ширину окна (``_fit_columns_to_width``),
  а окно программы открывается развёрнутым (см. ``main.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from itertools import groupby

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import calc, styles
from ..dates import (
    WEEKDAYS_RU_SHORT,
    fmt_date,
    fmt_money,
    month_title,
    parse_money,
    week_days,
    week_monday,
)
from ..models import KIND_INCOME, KIND_PAYMENT, set_daily_operation
from ..storage import DataStore
from ..widgets import danger_button, date_edit_value, make_date_edit, set_date_edit

HEADER_ROWS = 3
TOTAL_ROW = 3
FIRST_DATA_ROW = 4
COLS = 3 + 7 * 3  # имя + 2 просрочки + 7 дней * (Приход/Долг/Оплата)
SUBHEADERS = ("Приход", "Долг", "Оплата")
SUBHEADER_KINDS = ("income", "debt", "payment")
EDITABLE_KINDS = ("income", "payment")  # "debt" — будущая формула, ввод запрещён
OP_KIND_BY_COLUMN = {"income": KIND_INCOME, "payment": KIND_PAYMENT}
DEBT_CELL_BG = "#f4f6fa"  # лёгкая серая заливка нередактируемого «Долга»
# Веса и минимумы ширин колонок для растягивания на всю ширину таблицы.
BASE_WIDTHS = [170, 92, 92] + [56] * 21
MIN_WIDTHS = [140, 78, 78] + [50] * 21


class WeekView(QWidget):
    """``kind``: ``"supplier"`` | ``"customer"``."""

    def __init__(
        self,
        store: DataStore,
        kind: str,
        on_open_card: Callable[[str | None], None],
        on_data_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        assert kind in ("supplier", "customer")
        self.store = store
        self.kind = kind
        self.on_open_card = on_open_card
        # Вызывается после сохранения ввода, чтобы главное окно обновило
        # «Операции»/«Сводку» (сама эта таблица при этом не перестраивается).
        self.on_data_changed = on_data_changed
        self.week_start = week_monday(date.today())
        self._partners: list = []       # контрагенты текущей таблицы (для «ВСЕГО»)
        self._days: list[date] = []     # дни текущей недели
        self._rebuilding = False        # True, пока таблицу строит код, а не пользователь

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addLayout(self._build_toolbar())

        self.table = QTableWidget()
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.itemClicked.connect(self._on_item_clicked)
        self.table.itemChanged.connect(self._on_item_changed)
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
        """Кнопка «Очистить суммы»: удалить ВВЕДЁННЫЕ Приход/Оплата за эту неделю.

        Предположение (SPEC Q5): раньше ввод не сохранялся и кнопка просто
        стирала текст ячеек; теперь ввод — сохранённые операции, поэтому
        удаление только с подтверждением и только за показанную неделю.
        """
        answer = QMessageBox.question(
            self,
            "Очистить суммы",
            "Удалить все введённые суммы «Приход» и «Оплата» за неделю "
            f"{fmt_date(self._days[0])} — {fmt_date(self._days[-1])}?\n\n"
            "Операции будут удалены безвозвратно.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        day_isos = {d.isoformat() for d in self._days}
        partner_ids = {p.id for p in self._partners}
        self.store.data.operations = [
            op
            for op in self.store.data.operations
            if not (
                op.counterparty_id in partner_ids
                and op.date in day_isos
                and op.kind in OP_KIND_BY_COLUMN.values()
            )
        ]
        self.store.save()
        self.refresh()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and item.row() >= FIRST_DATA_ROW:
            counterparty_id = item.data(Qt.ItemDataRole.UserRole)
            if counterparty_id:
                self.on_open_card(str(counterparty_id))

    # --------------------------------------------------------- ввод в ячейки
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Пользователь ввёл сумму в Приход/Оплата: проверить и сохранить."""
        if self._rebuilding:
            return
        meta = item.data(Qt.ItemDataRole.UserRole)
        if not meta or "|" not in str(meta):
            return  # не ячейка ввода (заголовки, имя, ВСЕГО…)
        counterparty_id, date_iso, column_kind = str(meta).split("|", 2)
        op_kind = OP_KIND_BY_COLUMN.get(column_kind)
        if op_kind is None:
            return

        try:
            amount = parse_money(item.text())
        except ValueError:
            amount = None
            invalid = True
        else:
            invalid = amount is not None and amount < 0
        if invalid:
            QMessageBox.warning(
                self,
                "Некорректная сумма",
                "Введите сумму цифрами, например: 1500 или 1 500,75\n"
                "Чтобы удалить сумму — очистите ячейку.",
            )
            self._restore_cell(item, counterparty_id, date_iso, column_kind)
            return

        set_daily_operation(
            self.store.data, counterparty_id, date_iso, op_kind, amount
        )
        self.store.save()
        # Показать отформатированное значение и обновить «ВСЕГО» по колонке,
        # не перестраивая таблицу (не теряем позицию/фокус пользователя).
        self._restore_cell(item, counterparty_id, date_iso, column_kind)
        self._update_totals_row()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def _restore_cell(
        self, item: QTableWidgetItem, counterparty_id: str, date_iso: str, column_kind: str
    ) -> None:
        """Перерисовать ячейку значением из хранилища (формат ``1 500,75``)."""
        day = date.fromisoformat(date_iso)
        text = calc.week_cell_value(
            counterparty_id, day, column_kind, self.store.data.operations
        )
        self._rebuilding = True
        try:
            item.setText(text)
        finally:
            self._rebuilding = False

    def _update_totals_row(self) -> None:
        """Строка «ВСЕГО»: суммы введённых Приход/Оплата по дням (Долг пуст)."""
        partner_ids = [p.id for p in self._partners]
        self._rebuilding = True
        try:
            for i, day in enumerate(self._days):
                for j, kind_name in enumerate(SUBHEADER_KINDS):
                    item = self.table.item(TOTAL_ROW, 3 + i * 3 + j)
                    if item is not None:
                        item.setText(
                            calc.week_column_total(
                                partner_ids, day, kind_name, self.store.data.operations
                            )
                        )
        finally:
            self._rebuilding = False

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
        self._partners = partners
        self._days = days
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
        # Программная перестройка не должна срабатывать как «ввод пользователя».
        self._rebuilding = True
        try:
            self._rebuild_table_inner(partners, days, today)
        finally:
            self._rebuilding = False
        self._fit_columns_to_width()

    def _rebuild_table_inner(self, partners, days, today) -> None:
        table = self.table
        table.clear()
        table.clearSpans()
        table.setRowCount(FIRST_DATA_ROW + len(partners))
        table.setColumnCount(COLS)

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

        # Строка «ВСЕГО»: суммы введённых Приход/Оплата (Долг — пока пусто).
        operations = self.store.data.operations
        partner_ids = [p.id for p in partners]
        put(TOTAL_ROW, 0, "ВСЕГО", bold=True, center=False)
        put(TOTAL_ROW, 1, "")
        put(TOTAL_ROW, 2, "")
        for i, day in enumerate(days):
            for j, kind_name in enumerate(SUBHEADER_KINDS):
                value = calc.week_column_total(partner_ids, day, kind_name, operations)
                total_cell = put(
                    TOTAL_ROW, 3 + i * 3 + j, value, bold=True, size=11
                )
                total_cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )

        # Строки контрагентов
        for r, partner in enumerate(partners):
            row = FIRST_DATA_ROW + r
            name_item = put(row, 0, partner.name, center=False,
                            color=styles.LINK_BLUE, underline=True)
            name_item.setData(Qt.ItemDataRole.UserRole, partner.id)
            # Просроченная задолженность (на сегодня / за неделю) — будущие формулы.
            put(row, 1, "", bg=DEBT_CELL_BG)
            put(row, 2, "", bg=DEBT_CELL_BG)
            for i, day in enumerate(days):
                for j, kind_name in enumerate(SUBHEADER_KINDS):
                    value = calc.week_cell_value(
                        partner.id, day, kind_name, operations
                    )
                    if kind_name in EDITABLE_KINDS:
                        cell = put(
                            row, 3 + i * 3 + j, value, editable=True, size=11
                        )
                        cell.setData(
                            Qt.ItemDataRole.UserRole,
                            f"{partner.id}|{day.isoformat()}|{kind_name}",
                        )
                        cell.setToolTip(
                            f"{SUBHEADERS[j]}: введите сумму и нажмите Enter. "
                            "Пустая ячейка — удалить."
                        )
                    else:
                        cell = put(
                            row, 3 + i * 3 + j, value, bg=DEBT_CELL_BG, size=11
                        )
                        cell.setToolTip(
                            "Долг будет рассчитываться по формулам — "
                            "следующий этап (ввод здесь запрещён)."
                        )
                    # Суммы — по правому краю и шрифтом поменьше, чтобы
                    # «15 000,00» влезало в узкую дневную колонку целиком.
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )

    # ---------------------------------------------------- ширина на весь экран
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # Отложенно: viewport таблицы получает финальный размер чуть позже
        # самого resizeEvent (особенно при первом показе вкладки).
        QTimer.singleShot(0, self._fit_columns_to_width)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_columns_to_width)

    def _fit_columns_to_width(self) -> None:
        """Растянуть колонки ровно на ширину таблицы (запрос пользователя).

        Ширина раздаётся пропорционально ``BASE_WIDTHS``; ниже
        ``MIN_WIDTHS`` колонки не сжимаются — на узких окнах вместо этого
        появляется горизонтальная прокрутка, чтобы «Приход/Оплата»
        не обрезались.
        """
        table = getattr(self, "table", None)
        if table is None or table.columnCount() != COLS:
            return
        available = table.viewport().width()
        if available <= 0:
            widths = list(BASE_WIDTHS)
        elif available <= sum(MIN_WIDTHS):
            widths = list(MIN_WIDTHS)
        else:
            total_base = sum(BASE_WIDTHS)
            widths = [
                max(min_w, available * base // total_base)
                for base, min_w in zip(BASE_WIDTHS, MIN_WIDTHS)
            ]
            # Подогнать сумму в точности под available: лишние пиксели —
            # по одному колонкам дней, недостаток — забрать у самых широких.
            diff = available - sum(widths)
            col = 3
            while diff > 0:
                widths[col] += 1
                diff -= 1
                col = 3 + (col - 2) % (COLS - 3)
            while diff < 0:
                widest = max(
                    range(COLS), key=lambda c: widths[c] - MIN_WIDTHS[c]
                )
                if widths[widest] <= MIN_WIDTHS[widest]:
                    break
                widths[widest] -= 1
                diff += 1
        for col, width in enumerate(widths):
            table.setColumnWidth(col, width)
