"""Экран «Поступление и оплаты» (вкладка главного окна, v0.10.0).

Запрос пользователя: на вкладке два блока — **«Поступление»** и под ним
**«Оплата»**. Заголовок блока и строка даты идут в одну строчку:

    Поступление   Дата [27.10.2026 ▾]  [Сегодня]  [←] [→]

Ниже в блоке — таблица. Строки таблицы — что именно принимаем/платим;
столбцы:

* **Сумма** — внесённая сумма за выбранную дату (у наличных и карт она
  вводится прямо в ячейку);
* **Неделя / Месяц / Год** — нарастающий итог с начала периода
  (понедельника недели, первого числа месяца, 1 января) по выбранную
  дату включительно. Это простые суммы сохранённых записей, без формул
  долгов и остатков (ADR-017).

Строка «Покупатели» (и «Поставщики» в блоке «Оплата») не редактируется
ячейкой: клик по строке открывает **всплывающее окно справа** со списком
контрагентов и полем суммы у каждого. Внизу окна сразу виден итог по
всем покупателям — он пересчитывается по мере ввода.

Куда попадают суммы (новых формул нет, см. ADR-016/017):

* «Поступление наличными» / «Поступление по картам» — записи
  ``AppData.cash_receipts`` (контрагента нет, в недельных таблицах они
  не участвуют), одна запись на день и источник;
* «Покупатели» → операция «Оплата» покупателя на дату, то есть та же
  запись, что показывает колонка «Оплата» недельной таблицы покупателей;
* «Поставщики» → операция «Оплата» поставщика на дату (как ввод в ячейку
  недельной таблицы поставщиков).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import styles
from ..dates import fmt_money_plain, parse_money, week_monday
from ..models import (
    CARD_SOURCE,
    CASH_SOURCE,
    KIND_PAYMENT,
    cash_receipt_amount,
    operation_amount,
    set_daily_cash_receipt,
    set_daily_operation,
)
from ..storage import DataStore
from ..widgets import AmountLineEdit, blue_button, make_amount_edit, make_date_edit

CASH_ROW = "cash"
CARD_ROW = "card"
CUSTOMERS_ROW = "customers"
SUPPLIERS_ROW = "suppliers"

ROW_TITLES = {
    CASH_ROW: "Поступление наличными",
    CARD_ROW: "Поступление по картам",
    CUSTOMERS_ROW: "Покупатели",
    SUPPLIERS_ROW: "Поставщики",
}

# Строки, сумму которых вводят прямо в ячейку.
DIRECT_INPUT_ROWS = (CASH_ROW, CARD_ROW)
# Строки, по клику открывающие всплывающее окно со списком контрагентов.
POPUP_ROWS = (CUSTOMERS_ROW, SUPPLIERS_ROW)

COL_NAME, COL_AMOUNT, COL_WEEK, COL_MONTH, COL_YEAR = range(5)
HEADERS = ("", "Сумма", "Неделя", "Месяц", "Год")
COLUMN_WIDTHS = {COL_AMOUNT: 150, COL_WEEK: 150, COL_MONTH: 150, COL_YEAR: 170}

ZERO = Decimal("0")


def _today() -> date:
    return date.today()


def _qdate_to_date(qdate) -> date:
    return date(qdate.year(), qdate.month(), qdate.day())


def _money_text(amount: Decimal) -> str:
    """Пусто вместо ``0,00`` — как в недельных таблицах (макет 02/03)."""

    return fmt_money_plain(amount) if amount else ""


class AmountItemDelegate(QStyledItemDelegate):
    """Редактор суммы для ячейки: без мешающих нулей (AmountLineEdit)."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        return AmountLineEdit(parent=parent)


class AmountPopup(QFrame):
    """Всплывающее окно справа: список контрагентов с полем суммы.

    Показывает **итог по всем строкам** и пересчитывает его по мере
    ввода. ``on_save`` получает словарь ``{id: Decimal}`` только по
    непустым полям; сохранение выполняет вызывающий экран.
    """

    def __init__(
        self,
        title: str,
        entries: list[tuple[str, str]],
        on_save: Callable[[dict[str, Decimal]], None],
        parent: QWidget | None = None,
        hint: str = "",
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("Popup")
        self.setStyleSheet(
            "QFrame#Popup{background:#ffffff;border:1px solid #b9c9ef;"
            "border-radius:10px}"
            "QLabel#PopupTitle{font-weight:800;font-size:15px}"
            "QLabel#PopupTotal{font-weight:800;font-size:15px;"
            f"color:{styles.BLUE}}}"
        )
        # Закрытие по клику вне окна или по Esc удаляет его: держать
        # скрытые окна на каждый клик незачем.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._on_save = on_save
        self.edits: dict[str, AmountLineEdit] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QLabel(title)
        header.setObjectName("PopupTitle")
        root.addWidget(header)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("Muted")
            hint_label.setWordWrap(True)
            hint_label.setMaximumWidth(360)
            root.addWidget(hint_label)

        fields = QWidget()
        fields_layout = QVBoxLayout(fields)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fields_layout.setSpacing(6)
        for key, name in entries:
            fields_layout.addLayout(self._make_row(name, key))

        if len(entries) > 8:  # длинный список прокручиваем
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(fields)
            scroll.setMaximumHeight(320)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            root.addWidget(scroll)
        else:
            root.addWidget(fields)

        total_row = QHBoxLayout()
        total_row.addStretch(1)
        total_caption = QLabel("Итого по покупателям:")
        total_caption.setStyleSheet("font-weight:700")
        total_row.addWidget(total_caption)
        self.total_label = QLabel("0,00")
        self.total_label.setObjectName("PopupTotal")
        total_row.addWidget(self.total_label)
        root.addLayout(total_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        save_btn = blue_button("Сохранить")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

        self._update_total()

    # ------------------------------------------------------------------ вид
    def _make_row(self, name: str, key: str) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(10)
        label = QLabel(name)
        label.setMinimumWidth(200)
        label.setMaximumWidth(320)
        layout.addWidget(label, stretch=1)
        edit = make_amount_edit()
        edit.setPlaceholderText("0,00")
        edit.setFixedWidth(170)
        edit.returnPressed.connect(self._save)
        # Итог пересчитывается на каждый ввод, а не только по «Сохранить».
        edit.textChanged.connect(lambda *_: self._update_total())
        self.edits[key] = edit
        layout.addWidget(edit)
        return layout

    def _update_total(self) -> None:
        total = ZERO
        for edit in self.edits.values():
            text = edit.text().strip()
            if not text:
                continue
            try:
                value = parse_money(text)
            except ValueError:
                continue
            if value is not None:
                total += value
        self.total_label.setText(fmt_money_plain(total))

    def open_beside(self, widget: QWidget, row_top: int = 0) -> None:
        """Показать окно **справа** от ``widget`` на уровне строки.

        Запрос пользователя: окно покупателей всплывает справа. Если
        справа места нет (таблица на всю ширину окна), окно прижимается
        к правому краю экрана — но остаётся справа, а не под строкой.
        """

        anchor = widget.mapToGlobal(QPoint(widget.width(), row_top))
        self.adjustSize()
        self.move(QPoint(anchor.x() + 8, anchor.y()))
        self.show()
        screen = self.screen()
        if screen is not None:  # не даём окну уехать за край экрана
            free = screen.availableGeometry()
            frame = self.frameGeometry()
            x = min(frame.x(), free.right() - frame.width() - 8)
            y = min(frame.y(), free.bottom() - frame.height() - 8)
            self.move(max(free.left() + 8, x), max(free.top() + 8, y))
        first = next(iter(self.edits.values()), None)
        if first is not None:
            first.setFocus()

    # ------------------------------------------------------------- сохранение
    def collected(self) -> dict[str, Decimal]:
        """Разобрать поля. Пустые пропускаются; мусор -> ``ValueError``."""

        result: dict[str, Decimal] = {}
        for key, edit in self.edits.items():
            text = edit.text().strip()
            if not text:
                continue
            try:
                value = parse_money(text)
            except ValueError:
                raise ValueError(f"Не понимаю сумму: {text}") from None
            if value is None:
                continue
            if value <= 0:
                raise ValueError(f"Сумма должна быть больше нуля: {text}")
            result[key] = value
        return result

    def _save(self) -> None:
        try:
            values = self.collected()
        except ValueError as error:
            QMessageBox.warning(self, "Проверьте суммы", str(error))
            return
        if not values:
            QMessageBox.information(
                self, "Нечего сохранять", "Введите сумму хотя бы в одно поле."
            )
            return
        self._on_save(values)
        self.close()


class MoneyBlock(QFrame):
    """Блок «Поступление»/«Оплата»: дата в одну строку с заголовком + таблица."""

    def __init__(
        self,
        title: str,
        row_keys: list[str],
        on_open_popup: Callable[[str, QWidget], None],
        on_amount_entered: Callable[[str, str], None],
        on_date_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.row_keys = list(row_keys)
        self.on_open_popup = on_open_popup
        self.on_amount_entered = on_amount_entered
        self.on_date_changed = on_date_changed
        self._updating = False  # заполнение таблицы не считается вводом

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # --- заголовок и дата в одну строчку ---
        top = QHBoxLayout()
        top.setSpacing(10)
        header = QLabel(title)
        header.setObjectName("PanelTitle")
        top.addWidget(header)
        top.addSpacing(12)
        top.addWidget(QLabel("Дата"))
        self.date_edit = make_date_edit(_today())
        self.date_edit.dateChanged.connect(lambda *_: self._on_date_changed())
        top.addWidget(self.date_edit)

        self.today_btn = QPushButton("Сегодня")
        self.today_btn.clicked.connect(self.go_today)
        top.addWidget(self.today_btn)

        self.prev_btn = QPushButton("←")
        self.prev_btn.setFixedWidth(38)
        self.prev_btn.setToolTip("День назад")
        self.prev_btn.clicked.connect(lambda: self.shift_date(-1))
        top.addWidget(self.prev_btn)

        self.next_btn = QPushButton("→")
        self.next_btn.setFixedWidth(38)
        self.next_btn.setToolTip("День вперёд")
        self.next_btn.clicked.connect(lambda: self.shift_date(1))
        top.addWidget(self.next_btn)

        top.addStretch(1)
        root.addLayout(top)

        # --- таблица: строка + сумма + нарастающие итоги ---
        self.table = QTableWidget(len(self.row_keys), len(HEADERS))
        self.table.setHorizontalHeaderLabels(list(HEADERS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setItemDelegateForColumn(COL_AMOUNT, AmountItemDelegate(self.table))
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column, width in COLUMN_WIDTHS.items():
            self.table.setColumnWidth(column, width)
        self.table.setFixedHeight(
            self.table.horizontalHeader().height()
            + len(self.row_keys) * self.table.verticalHeader().defaultSectionSize()
            + 2
        )
        root.addWidget(self.table)

        self._build_rows()

    # ------------------------------------------------------------------ вид
    def _build_rows(self) -> None:
        # Заполнение таблицы не должно выглядеть как ввод суммы: флаг
        # гасит itemChanged на время создания ячеек.
        self._updating = True
        for row, key in enumerate(self.row_keys):
            name_item = QTableWidgetItem(ROW_TITLES[key])
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            name_item.setToolTip(self._row_tooltip(key))
            self.table.setItem(row, COL_NAME, name_item)
            for column in (
                COL_AMOUNT,
                COL_WEEK,
                COL_MONTH,
                COL_YEAR,
            ):
                item = QTableWidgetItem("")
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if key in DIRECT_INPUT_ROWS and column == COL_AMOUNT:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                if column == COL_AMOUNT:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)
        self._updating = False

    @staticmethod
    def _row_tooltip(key: str) -> str:
        if key in DIRECT_INPUT_ROWS:
            return "Сумма вводится прямо в ячейку"
        return "Клик — окно со списком и полями сумм"

    def set_amount(self, row_key: str, column: int, text: str) -> None:
        row = self.row_keys.index(row_key)
        item = self.table.item(row, column)
        if item is None:
            return
        self._updating = True
        item.setText(text)
        self._updating = False

    def amount_text(self, row_key: str, column: int = COL_AMOUNT) -> str:
        row = self.row_keys.index(row_key)
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    # ----------------------------------------------------------------- дата
    def current_date(self) -> date:
        return _qdate_to_date(self.date_edit.date())

    def _on_date_changed(self) -> None:
        if self.on_date_changed is not None:
            self.on_date_changed()

    def set_date(self, day: date) -> None:
        self.date_edit.blockSignals(True)
        self.date_edit.setDate(QDate(day.year, day.month, day.day))
        self.date_edit.blockSignals(False)
        self._on_date_changed()

    def go_today(self) -> None:
        self.set_date(_today())

    def shift_date(self, days: int) -> None:
        self.set_date(self.current_date() + timedelta(days=days))

    # ------------------------------------------------------------- действия
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != COL_AMOUNT:
            return
        key = self.row_keys[item.row()]
        if key not in DIRECT_INPUT_ROWS:
            return
        self.on_amount_entered(key, item.text())

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        key = self.row_keys[row]
        if key in POPUP_ROWS:
            self.on_open_popup(key, self.table, row)


class ReceiptsView(QWidget):
    """Блок «Поступление» и под ним блок «Оплата»."""

    def __init__(
        self,
        store: DataStore,
        on_data_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_data_changed = on_data_changed
        self._popup: AmountPopup | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        self.incoming = MoneyBlock(
            "Поступление",
            [CASH_ROW, CARD_ROW, CUSTOMERS_ROW],
            on_open_popup=self.open_popup,
            on_amount_entered=self.save_direct_amount,
            on_date_changed=self.refresh,
        )
        self.outgoing = MoneyBlock(
            "Оплата",
            [SUPPLIERS_ROW],
            on_open_popup=self.open_popup,
            on_amount_entered=self.save_direct_amount,
            on_date_changed=self.refresh,
        )
        root.addWidget(self.incoming)
        root.addWidget(self.outgoing)

        hint = QLabel(
            "«Сумма» — за выбранную дату; наличные и карты вводятся прямо "
            "в ячейку. «Неделя/Месяц/Год» — нарастающий итог с начала "
            "периода по эту дату. Клик по строке «Покупатели»/«Поставщики» "
            "открывает окно справа со списком и полями сумм."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch(1)

        self.refresh()

    # ---------------------------------------------------------------- даты
    @property
    def blocks(self) -> tuple[MoneyBlock, MoneyBlock]:
        return (self.incoming, self.outgoing)

    def set_date(self, day: date) -> None:
        """Задать дату обоим блокам (удобно для тестов и скриншотов)."""

        for block in self.blocks:
            block.set_date(day)
        self.refresh()

    # --------------------------------------------------------- ввод в ячейку
    def save_direct_amount(self, row_key: str, text: str) -> None:
        """Сохранить сумму, введённую прямо в ячейку (наличные/карты)."""

        block = self.outgoing if row_key == SUPPLIERS_ROW else self.incoming
        day_iso = block.current_date().isoformat()
        cleaned = (text or "").strip()
        if not cleaned:
            amount = None
        else:
            try:
                amount = parse_money(cleaned)
            except ValueError:
                amount = None
        source = CASH_SOURCE if row_key == CASH_ROW else CARD_SOURCE
        if amount is not None and amount < 0:
            amount = None
        set_daily_cash_receipt(self.store.data, source, day_iso, amount)
        self.store.save()
        self.refresh()
        # Недельные таблицы и «Сводку» не трогаем: у наличных и карт нет
        # контрагента, в этих таблицах они не участвуют (ADR-017).

    # -------------------------------------------------------------- выпадающее
    def open_popup(
        self, row_key: str, anchor: QWidget | None = None, row: int | None = None
    ) -> AmountPopup | None:
        """Открыть окно со списком контрагентов справа от строки."""

        block = self.outgoing if row_key == SUPPLIERS_ROW else self.incoming
        table = block.table
        if row is None:
            row = block.row_keys.index(row_key)
        row_top = table.rowViewportPosition(row)
        if anchor is None:
            anchor = table
        title = ROW_TITLES[row_key]

        if row_key == CUSTOMERS_ROW:
            people = [c for c in self.store.data.customers if not c.archived]
            empty_title = "Нет покупателей"
            empty_text = (
                "Добавьте покупателя через кнопку «Карточка покупателя»."
            )
            hint = "Сумма поступит в колонку «Оплата» покупателя за дату."
        else:
            people = [s for s in self.store.data.suppliers if not s.archived]
            empty_title = "Нет поставщиков"
            empty_text = (
                "Добавьте поставщика через кнопку «Карточка поставщика»."
            )
            hint = "Сумма появится в колонке «Оплата» поставщика за дату."

        if not people:
            QMessageBox.information(self, empty_title, empty_text)
            return None

        popup = AmountPopup(
            title,
            [(person.id, person.name) for person in people],
            self._make_saver(block, row_key),
            self,
            hint,
        )
        # Окно удаляется при закрытии (WA_DeleteOnClose) — не держим ссылку.
        popup.destroyed.connect(self._forget_popup)
        self._popup = popup
        popup.open_beside(anchor, row_top)
        return popup

    def _forget_popup(self, *_args) -> None:
        self._popup = None

    def _make_saver(self, block: MoneyBlock, row_key: str):
        def save(values: dict[str, Decimal]) -> None:
            day_iso = block.current_date().isoformat()
            for counterparty_id, amount in values.items():
                set_daily_operation(
                    self.store.data,
                    counterparty_id,
                    day_iso,
                    KIND_PAYMENT,
                    amount,
                )
            self.store.save()
            self.refresh()
            if self.on_data_changed is not None:
                self.on_data_changed()

        return save

    # ------------------------------------------------------------------ итоги
    def _cash_total(self, source: str, day: date) -> Decimal:
        day_iso = day.isoformat()
        return sum(
            (
                cash_receipt_amount(receipt)
                for receipt in self.store.data.cash_receipts
                if receipt.date == day_iso and receipt.source == source
            ),
            ZERO,
        )

    def _payment_total(self, counterparty_ids: set[str], day: date) -> Decimal:
        day_iso = day.isoformat()
        return sum(
            (
                operation_amount(op)
                for op in self.store.data.operations
                if op.kind == KIND_PAYMENT
                and op.date == day_iso
                and op.counterparty_id in counterparty_ids
            ),
            ZERO,
        )

    def _period_totals(self, key: str, day: date) -> tuple[Decimal, Decimal, Decimal]:
        """Суммы записей за периоды: неделя (с Пн), месяц, год — по дату."""

        week_start = week_monday(day)
        month_start = day.replace(day=1)
        year_start = day.replace(month=1, day=1)
        week = month = year = ZERO

        if key in (CASH_ROW, CARD_ROW):
            source = CASH_SOURCE if key == CASH_ROW else CARD_SOURCE
            for receipt in self.store.data.cash_receipts:
                if receipt.source != source or not receipt.date:
                    continue
                item_day = _parse_day(receipt.date)
                if item_day is None or item_day > day:
                    continue
                amount = cash_receipt_amount(receipt)
                if item_day >= week_start:
                    week += amount
                if item_day >= month_start:
                    month += amount
                if item_day >= year_start:
                    year += amount
            return week, month, year

        if key == CUSTOMERS_ROW:
            ids = {c.id for c in self.store.data.customers}
        else:
            ids = {s.id for s in self.store.data.suppliers}
        for op in self.store.data.operations:
            if op.kind != KIND_PAYMENT or op.counterparty_id not in ids:
                continue
            item_day = _parse_day(op.date)
            if item_day is None or item_day > day:
                continue
            amount = operation_amount(op)
            if item_day >= week_start:
                week += amount
            if item_day >= month_start:
                month += amount
            if item_day >= year_start:
                year += amount
        return week, month, year

    def refresh(self) -> None:
        incoming_day = self.incoming.current_date()
        outgoing_day = self.outgoing.current_date()
        customer_ids = {c.id for c in self.store.data.customers}
        supplier_ids = {s.id for s in self.store.data.suppliers}

        self.incoming.set_amount(
            CASH_ROW, COL_AMOUNT, _money_text(self._cash_total(CASH_SOURCE, incoming_day))
        )
        self.incoming.set_amount(
            CARD_ROW, COL_AMOUNT, _money_text(self._cash_total(CARD_SOURCE, incoming_day))
        )
        self.incoming.set_amount(
            CUSTOMERS_ROW,
            COL_AMOUNT,
            _money_text(self._payment_total(customer_ids, incoming_day)),
        )
        self.outgoing.set_amount(
            SUPPLIERS_ROW,
            COL_AMOUNT,
            _money_text(self._payment_total(supplier_ids, outgoing_day)),
        )

        for block, day in ((self.incoming, incoming_day), (self.outgoing, outgoing_day)):
            for key in block.row_keys:
                week, month, year = self._period_totals(key, day)
                block.set_amount(key, COL_WEEK, _money_text(week))
                block.set_amount(key, COL_MONTH, _money_text(month))
                block.set_amount(key, COL_YEAR, _money_text(year))


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
