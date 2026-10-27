"""Недельная таблица — экраны «Поставщики» и «Покупатели» (макеты 02/03).

Состояние (v0.3.1, запросы пользователя 27.10.2026):
* Клик по поставщику открывает окно «Поставка» (07-delivery-dialog.png);
  двойной клик по ячейке «Приход» — то же окно с датой этой ячейки.
  Прямой ввод в ячейку «Приход» у поставщиков заменён этим окном,
  у покупателей ячейка «Приход» пока редактируется как раньше.
* «Оплата» редактируется прямо в ячейке и сохраняется как операция дня.
* «Долг» (только поставщики) считается в calc: исходная сумма поставок
  на срок оплаты; сумма остаётся для архива, оплата её пока не уменьшает
  (правила оплат пользователь даст отдельным этапом); колонки
  «на сегодня» (просрочено) и «за неделю» (задолженность недели) — там же.
  У покупателей «Долг» пока пустой (правил для них пользователь не давал).
* Таблица растягивается на всю ширину окна; шрифт сумм подгоняется под
  максимальное число ``88 888,88``; дни разделены толстыми линиями
  (08-week-day-separators.png); шрифт шапки таблицы увеличен.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from itertools import groupby

from PySide6.QtCore import QModelIndex, QTimer, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
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
    fmt_money_plain,
    month_title,
    parse_money,
    week_days,
    week_monday,
)
from ..models import KIND_INCOME, KIND_PAYMENT, Counterparty, set_daily_operation
from ..storage import DataStore
from ..widgets import danger_button, date_edit_value, make_date_edit, set_date_edit

HEADER_ROWS = 3
TOTAL_ROW = 3
FIRST_DATA_ROW = 4
COLS = 3 + 7 * 3  # имя + 2 просрочки + 7 дней * (Приход/Долг/Оплата)
SUBHEADERS = ("Приход", "Долг", "Оплата")
SUBHEADER_KINDS = ("income", "debt", "payment")
OP_KIND_BY_COLUMN = {"income": KIND_INCOME, "payment": KIND_PAYMENT}
DEBT_CELL_BG = "#f4f6fa"  # лёгкая серая заливка нередактируемых ячеек долга
DAY_SEPARATOR_COLOR = "#5b6b84"  # толстые линии между днями, как на макете 08
SEPARATOR_AFTER_COLS = {2, 5, 8, 11, 14, 17, 20}  # после «за неделю» и каждого дня, кроме Вс
MAX_AMOUNT_TEXT = "88 888,88"  # под это число подгоняется шрифт сумм (запрос пользователя)
META_ROLE = Qt.ItemDataRole.UserRole        # "id|дата|kind" редактируемых ячеек
DIALOG_ROLE = Qt.ItemDataRole.UserRole + 1  # то же для ячеек, открывающих «Поставку»
# Веса и минимумы ширин колонок для растягивания на всю ширину таблицы.
BASE_WIDTHS = [180, 96, 96] + [60] * 21
MIN_WIDTHS = [146, 80, 80] + [52] * 21
ZERO = Decimal("0")


def _money_text(amount: Decimal) -> str:
    return fmt_money_plain(amount) if amount != 0 else ""


class DaySeparatorDelegate(QStyledItemDelegate):
    """Рисует толстую вертикальную линию на границах дней (макет 08)."""

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        table = self.parent()
        span = 1
        if isinstance(table, QTableWidget):
            span = table.columnSpan(index.row(), index.column())
        right_col = index.column() + span - 1
        is_separator = right_col in SEPARATOR_AFTER_COLS
        original_right = option.rect.right()
        if is_separator:
            # Отступ справа, чтобы суммы не упирались в толстую линию.
            option.rect.setRight(original_right - 5)
        super().paint(painter, option, index)
        if not is_separator:
            return
        painter.save()
        pen = QPen(QColor(DAY_SEPARATOR_COLOR))
        pen.setWidth(3)
        painter.setPen(pen)
        x = original_right - 1
        painter.drawLine(x, option.rect.top(), x, option.rect.bottom())
        painter.restore()


class WeekView(QWidget):
    """``kind``: ``"supplier"`` | ``"customer"``."""

    def __init__(
        self,
        store: DataStore,
        kind: str,
        on_open_card: Callable[[str | None], None],
        on_data_changed: Callable[[], None] | None = None,
        on_open_delivery: Callable[[str, date | None], None] | None = None,
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
        # Только поставщики: окно «Поставка» (клик по имени / двойной по Приходу).
        self.on_open_delivery = on_open_delivery
        self.week_start = week_monday(date.today())
        self._partners: list[Counterparty] = []
        self._days: list[date] = []
        self._rebuilding = False  # True, пока таблицу меняет код, а не пользователь
        self._amount_items: list[QTableWidgetItem] = []  # для подгонки шрифта сумм

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addLayout(self._build_toolbar())

        self.table = QTableWidget()
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setItemDelegate(DaySeparatorDelegate(self.table))
        self.table.itemClicked.connect(self._on_item_clicked)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
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
            self.right_value_label = QLabel(fmt_money(ZERO))
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
        self.goto_date(date.today())

    def _go_to_date(self) -> None:
        self.goto_date(date_edit_value(self.date_edit))

    def goto_date(self, day: date) -> None:
        """Перейти на неделю, содержащую ``day`` (также после «Поставки»)."""
        self.week_start = week_monday(day)
        self.refresh()

    def _clear_amounts(self) -> None:
        """Кнопка «Очистить суммы»: удалить ВВЕДЁННЫЕ Приход/Оплата за эту неделю.

        Предположение (SPEC Q5): удаляются операции с датой внутри
        показанной недели, только после подтверждения.
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

    # ------------------------------------------------------------ клики/ввод
    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or item.row() < FIRST_DATA_ROW:
            return
        counterparty_id = item.data(Qt.ItemDataRole.UserRole)
        if not counterparty_id:
            return
        if self.kind == "supplier" and self.on_open_delivery is not None:
            # По макету 07: клик по поставщику открывает «Поставку».
            self.on_open_delivery(str(counterparty_id), None)
        else:
            self.on_open_card(str(counterparty_id))

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        meta = item.data(DIALOG_ROLE)
        if not meta or self.on_open_delivery is None:
            return
        counterparty_id, date_iso, _kind = str(meta).split("|", 2)
        self.on_open_delivery(counterparty_id, date.fromisoformat(date_iso))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Пользователь ввёл сумму в редактируемую ячейку: проверить и сохранить."""
        if self._rebuilding:
            return
        meta = item.data(META_ROLE)
        if not meta or "|" not in str(meta):
            return  # не ячейка ввода (заголовки, имя, ВСЕГО, Долг…)
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

        due_iso = ""
        if op_kind == KIND_INCOME:
            partner = calc.find_counterparty(self.store.data, counterparty_id)
            if partner is not None:
                due_iso = calc.payment_due_date(
                    date.fromisoformat(date_iso),
                    partner,
                    self.store.data.day_overrides,
                ).isoformat()
        set_daily_operation(
            self.store.data, counterparty_id, date_iso, op_kind, amount, due_iso
        )
        self.store.save()
        # Показать отформатированное значение и пересчитать Долг/итоги,
        # не перестраивая таблицу целиком.
        self._restore_cell(item, counterparty_id, date_iso, column_kind)
        self._update_calculated_cells()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def _restore_cell(
        self, item: QTableWidgetItem, counterparty_id: str, date_iso: str, column_kind: str
    ) -> None:
        """Перерисовать ячейку значением из хранилища (формат ``1 500,75``)."""
        day = date.fromisoformat(date_iso)
        text = calc.week_cell_value(
            counterparty_id,
            day,
            column_kind,
            self.store.data.operations,
            self.store.data,
        )
        self._rebuilding = True
        try:
            item.setText(text)
        finally:
            self._rebuilding = False

    # ------------------------------------------------------- расчётные ячейки
    def _due_maps(self) -> list[dict[date, Decimal]]:
        """Суммы поставок по срокам («Долг») для каждого контрагента таблицы.

        Формулы долга пользователь дал только для поставщиков;
        у покупателей карты пустые (Долг не показывается).
        """
        if self.kind != "supplier":
            return [{} for _ in self._partners]
        return [
            calc.debt_by_due_date(self.store.data, partner)
            for partner in self._partners
        ]

    @staticmethod
    def _debt_columns(
        due_map: dict[date, Decimal], days: list[date], today: date
    ) -> tuple[Decimal, Decimal]:
        """(просрочено на сегодня, задолженность за показанную неделю)."""
        overdue = sum((a for d, a in due_map.items() if d < today), ZERO)
        week = sum((a for d, a in due_map.items() if days and days[0] <= d <= days[-1]), ZERO)
        return overdue, week

    def _set_cell_text(self, row: int, col: int, text: str) -> None:
        item = self.table.item(row, col)
        if item is not None:
            item.setText(text)

    def _update_calculated_cells(self) -> None:
        """Пересчитать Долг, «на сегодня»/«за неделю», ВСЕГО и тулбар без перестройки."""
        today = date.today()
        due_maps = self._due_maps()
        partner_ids = [p.id for p in self._partners]
        operations = self.store.data.operations
        total_overdue = ZERO
        total_week = ZERO
        self._rebuilding = True
        try:
            for r, due_map in enumerate(due_maps):
                row = FIRST_DATA_ROW + r
                overdue, week = self._debt_columns(due_map, self._days, today)
                total_overdue += overdue
                total_week += week
                self._set_cell_text(row, 1, _money_text(overdue))
                self._set_cell_text(row, 2, _money_text(week))
                for i, day in enumerate(self._days):
                    self._set_cell_text(
                        row, 3 + i * 3 + 1, _money_text(due_map.get(day, ZERO))
                    )
            self._set_cell_text(TOTAL_ROW, 1, _money_text(total_overdue))
            self._set_cell_text(TOTAL_ROW, 2, _money_text(total_week))
            for i, day in enumerate(self._days):
                for j, kind_name in enumerate(SUBHEADER_KINDS):
                    if kind_name == "debt":
                        amount = sum((m.get(day, ZERO) for m in due_maps), ZERO)
                        text = _money_text(amount)
                    else:
                        text = calc.week_column_total(
                            partner_ids, day, kind_name, operations
                        )
                    self._set_cell_text(TOTAL_ROW, 3 + i * 3 + j, text)
        finally:
            self._rebuilding = False
        if self.kind == "supplier":
            self.right_value_label.setText(fmt_money(total_week))

    # --------------------------------------------------------------- данные
    def counterparties(self) -> list[Counterparty]:
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
        self._amount_items = []
        table.setRowCount(FIRST_DATA_ROW + len(partners))
        table.setColumnCount(COLS)

        for row in range(HEADER_ROWS):
            table.setRowHeight(row, 28)  # шапка выше — шрифт увеличен

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
                font.setPixelSize(size)
            item.setFont(font)
            if color:
                item.setForeground(QColor(color))
            table.setItem(row, col, item)
            return item

        def money_cell(row, col, text, **kwargs) -> QTableWidgetItem:
            """Ячейка суммы: правый край + шрифт, который потом подгонит _fit_*."""
            cell = put(row, col, text, size=11, **kwargs)
            cell.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._amount_items.append(cell)
            return cell

        pink, beige = styles.HEADER_PINK, styles.HEADER_BEIGE

        # Строка 0: месяцы (неделя может захватывать 1–2 месяца)
        put(0, 0, "", bg=styles.HEADER_MONTH)
        table.setSpan(0, 0, 1, 3)
        col = 3
        for (_year, _month), group in groupby(days, key=lambda d: (d.year, d.month)):
            group_days = list(group)
            put(0, col, month_title(group_days[0]),
                bg=styles.HEADER_MONTH, bold=True, size=13)
            table.setSpan(0, col, 1, len(group_days) * 3)
            col += len(group_days) * 3

        # Строка 1: дни недели (шрифт шапки увеличен по запросу пользователя)
        put(1, 0, "ПОСТАВЩИК" if self.kind == "supplier" else "ПОКУПАТЕЛЬ",
            bg=pink, bold=True, size=13)
        put(1, 1, "ПРОСРОЧЕН. ЗАДОЛЖ.", bg=pink, bold=True, size=12)
        table.setSpan(1, 1, 1, 2)
        for i, day in enumerate(days):
            bg = beige if day.weekday() >= 5 else pink
            c = 3 + i * 3
            put(1, c, f"{day.day}  {WEEKDAYS_RU_SHORT[day.weekday()]}",
                bg=bg, bold=True, size=13)
            table.setSpan(1, c, 1, 3)

        # Строка 2: подзаголовки
        put(2, 0, "", bg=pink)
        put(2, 1, "на сегодня", bg=pink, bold=True, size=12)
        put(2, 2, "за неделю", bg=pink, bold=True, size=12)
        for i, day in enumerate(days):
            if day == today:
                bg = styles.TODAY_YELLOW  # текущий день подсвечен жёлтым на макете
            else:
                bg = beige if day.weekday() >= 5 else "#f1f5fb"
            for j, sub in enumerate(SUBHEADERS):
                put(2, 3 + i * 3 + j, sub, bg=bg, bold=True, size=12)

        operations = self.store.data.operations
        partner_ids = [p.id for p in partners]
        due_maps = self._due_maps()
        debt_columns = [self._debt_columns(m, days, today) for m in due_maps]

        # Строка «ВСЕГО»
        total_overdue = sum((c[0] for c in debt_columns), ZERO)
        total_week = sum((c[1] for c in debt_columns), ZERO)
        put(TOTAL_ROW, 0, "ВСЕГО", bold=True, center=False)
        for col_idx, amount in ((1, total_overdue), (2, total_week)):
            cell = put(TOTAL_ROW, col_idx, _money_text(amount), bold=True, size=11)
            cell.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        for i, day in enumerate(days):
            for j, kind_name in enumerate(SUBHEADER_KINDS):
                if kind_name == "debt":
                    amount = sum((m.get(day, ZERO) for m in due_maps), ZERO)
                    value = _money_text(amount)
                else:
                    value = calc.week_column_total(
                        partner_ids, day, kind_name, operations
                    )
                money_cell(TOTAL_ROW, 3 + i * 3 + j, value, bold=True)

        # Строки контрагентов
        for r, partner in enumerate(partners):
            row = FIRST_DATA_ROW + r
            due_map = due_maps[r]
            overdue, week = debt_columns[r]
            name_item = put(row, 0, partner.name, center=False,
                            color=styles.LINK_BLUE, underline=True)
            name_item.setData(Qt.ItemDataRole.UserRole, partner.id)
            if self.kind == "supplier":
                name_item.setToolTip("Клик — окно «Поставка» для этого поставщика")
            # Просрочено «на сегодня» / задолженность «за неделю» (calc).
            for col_idx, amount in ((1, overdue), (2, week)):
                cell = put(row, col_idx, _money_text(amount), bg=DEBT_CELL_BG, size=11)
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            for i, day in enumerate(days):
                for j, kind_name in enumerate(SUBHEADER_KINDS):
                    col_idx = 3 + i * 3 + j
                    meta = f"{partner.id}|{day.isoformat()}|{kind_name}"
                    if kind_name == "debt":
                        cell = money_cell(
                            row, col_idx, _money_text(due_map.get(day, ZERO)),
                            bg=DEBT_CELL_BG,
                        )
                        cell.setToolTip(
                            "Сумма поставок со сроком оплаты в этот день. "
                            "Остаётся для архива: оплата её пока не уменьшает"
                        )
                        continue

                    value = calc.week_cell_value(
                        partner.id, day, kind_name, operations
                    )
                    supplier_income_via_dialog = (
                        self.kind == "supplier"
                        and kind_name == "income"
                        and self.on_open_delivery is not None
                    )
                    if supplier_income_via_dialog:
                        cell = money_cell(row, col_idx, value)
                        cell.setData(DIALOG_ROLE, meta)
                        cell.setToolTip(
                            "Двойной клик — окно «Поставка»: дата, сумма с НДС, "
                            "срок оплаты"
                        )
                    else:
                        cell = money_cell(row, col_idx, value, editable=True)
                        cell.setData(META_ROLE, meta)
                        cell.setToolTip(
                            f"{SUBHEADERS[j]}: введите сумму и нажмите Enter. "
                            "Пустая ячейка — удалить."
                        )

        if self.kind == "supplier":
            self.right_value_label.setText(fmt_money(total_week))

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
        """Растянуть колонки ровно на ширину таблицы и подогнать шрифт сумм.

        Ширина раздаётся пропорционально ``BASE_WIDTHS``; ниже ``MIN_WIDTHS``
        колонки не сжимаются — на узких окнах появляется прокрутка.
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
        self._fit_amount_font(widths[3])

    def _fit_amount_font(self, day_column_width: int) -> None:
        """Подобрать размер шрифта сумм так, чтобы «88 888,88» влезало целиком."""
        if not self._amount_items:
            return
        chosen = 8
        for size in range(14, 7, -1):
            font = QFont(self.table.font())
            font.setPixelSize(size)
            font.setBold(True)  # по жирной строке ВСЕГО — худший случай
            if QFontMetrics(font).horizontalAdvance(MAX_AMOUNT_TEXT) <= (
                day_column_width - 8
            ):
                chosen = size
                break
        self._rebuilding = True  # setFont тоже может дёрнуть itemChanged
        try:
            for item in self._amount_items:
                font = item.font()
                if font.pixelSize() != chosen:
                    font.setPixelSize(chosen)
                    item.setFont(font)
        except RuntimeError:
            self._amount_items = []  # таблица уже перестроена, ячейки мертвы
        finally:
            self._rebuilding = False
