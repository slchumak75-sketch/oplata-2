"""Экран «Поступление и оплаты» (вкладка главного окна, v0.11.0).

Как устроен экран (по запросам пользователя):

* слева — два блока друг под другом: **«Поступление»** и **«Оплата»**.
  Они занимают половину ширины окна (плашки «в два раза уже»);
* справа на всю высоту — панель **«Сводка»** (прежняя вкладка «Сводка»
  удалена, её содержимое живёт здесь);
* вверху каждого блока **одна строчка**: заголовок, подпись «Дата»,
  поле даты с выпадающим календарём, кнопка «Сегодня» и стрелки
  «←»/«→»;
* ниже в блоке — **таблица** со столбцами «Сумма / Неделя / Месяц /
  Год», где Неделя, Месяц и Год — нарастающий итог с начала периода
  (понедельника недели, 1-го числа месяца, 1 января) по выбранную дату
  включительно. Это агрегаты по сохранённым записям, без формул долгов
  (ADR-017).

Строки блока «Поступление»: «Поступление наличными», «Поступление по
картам» (суммы вводятся **прямо в ячейку**) и «Покупатели» (клик открывает
окно справа со списком покупателей и полями сумм; поля при повторном
открытии заполнены уже введёнными суммами за эту дату).

Строки блока «Оплата»: «Поставщики» (то же окно справа) и **прочие виды
оплат**, которые пользователь добавляет сам кнопкой **«+»**: название
вводится текстом прямо в ячейку, сумма — в ячейку «Сумма». Кнопка
**«−»** убирает выделенную строку.

Куда попадают суммы (новых формул нет, см. ADR-016/017/018):

* наличные/карты — записи ``AppData.cash_receipts`` (контрагента нет, в
  недельных таблицах не участвуют), одна запись на день и источник;
* покупатели/поставщики — операции «Оплата» контрагента на дату, то есть
  те же записи, что показывает колонка «Оплата» недельных таблиц.
  Поэтому суммы видны в обе стороны: что введено здесь — появляется в
  таблице поставщиков/покупателей, и наоборот;
* прочие виды оплат — записи ``AppData.other_payments``: своё название и
  сумма за дату, итоги считаются по названию.
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
    QSizePolicy,
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
    add_other_payment,
    cash_receipt_amount,
    delete_other_payment,
    operation_amount,
    other_payment_amount,
    other_payment_names,
    rename_other_payment,
    set_daily_cash_receipt,
    set_daily_operation,
    set_daily_other_payment,
)
from ..storage import DataStore
from ..widgets import AmountLineEdit, blue_button, make_amount_edit, make_date_edit

CASH_ROW = "cash"
CARD_ROW = "card"
CUSTOMERS_ROW = "customers"
SUPPLIERS_ROW = "suppliers"

# Префикс ключей строк «прочих видов оплат»: OTHER_PREFIX + название.
OTHER_PREFIX = "other:"
NEW_PAYMENT_NAME = "Новый вид оплаты"

ROW_TITLES = {
    CASH_ROW: "Поступление наличными",
    CARD_ROW: "Поступление по картам",
    CUSTOMERS_ROW: "Покупатели",
    SUPPLIERS_ROW: "Поставщики",
}

# Строки, сумму которых вводят прямо в ячейку.
DIRECT_INPUT_ROWS = (CASH_ROW, CARD_ROW)
# Строки, по клику открывающие окно со списком контрагентов.
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


def other_row_key(name: str) -> str:
    return f"{OTHER_PREFIX}{name}"


def other_row_name(key: str) -> str:
    return key[len(OTHER_PREFIX):] if key.startswith(OTHER_PREFIX) else ""


class AmountItemDelegate(QStyledItemDelegate):
    """Редактор суммы для ячейки: без мешающих нулей (AmountLineEdit)."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        return AmountLineEdit(parent=parent)


class AmountPopup(QFrame):
    """Окно справа: список контрагентов с полем суммы у каждого.

    Поля при открытии **уже заполнены** сохранёнными суммами за дату
    (иначе повторное открытие выглядит как «суммы пропали»). Внизу окна
    виден итог по всем покупателям — он пересчитывается по мере ввода.
    ``on_save`` получает словарь ``{id: Decimal}``: непустые значения
    записываются, пустые поля означают «убрать сумму этого дня».
    """

    def __init__(
        self,
        title: str,
        entries: list[tuple[str, str]],
        on_save: Callable[[dict[str, Decimal]], None],
        parent: QWidget | None = None,
        hint: str = "",
        values: dict[str, Decimal] | None = None,
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
        # Были ли уже сохранённые суммы: если да, то пустые поля — это
        # «убрать суммы», а не «нечего сохранять».
        self._had_values = any(
            value for value in (values or {}).values() if value
        )

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
            fields_layout.addLayout(
                self._make_row(name, key, (values or {}).get(key))
            )

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
    def _make_row(
        self, name: str, key: str, value: Decimal | None = None
    ) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(10)
        label = QLabel(name)
        label.setMinimumWidth(200)
        label.setMaximumWidth(320)
        layout.addWidget(label, stretch=1)
        # make_amount_edit сам убирает нулевое значение: «0,00» не мешает.
        edit = make_amount_edit(_money_text(value or ZERO))
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
        справа места нет (таблица занимает половину окна, справа —
        «Сводка»), окно прижимается к правому краю экрана, но остаётся
        справа, а не под строкой.
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
            first.selectAll()

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
        # Пустые поля означают «убрать сумму»: сохраняем и их, но нулями.
        result: dict[str, Decimal] = {key: ZERO for key in self.edits}
        for key, value in values.items():
            result[key] = value
        if not values and not self._had_values:
            QMessageBox.information(
                self, "Нечего сохранять", "Введите сумму хотя бы в одно поле."
            )
            return
        self._on_save(result)
        self.close()


class MoneyBlock(QFrame):
    """Блок «Поступление»/«Оплата»: дата в одну строку с заголовком + таблица."""

    def __init__(
        self,
        title: str,
        row_keys: list[str],
        on_open_popup: Callable[[str, QWidget, int], None],
        on_amount_entered: Callable[[str, str], None],
        on_date_changed: Callable[[], None] | None = None,
        on_name_edited: Callable[[str, str], None] | None = None,
        on_add_row: Callable[[], None] | None = None,
        on_remove_row: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.row_keys = list(row_keys)
        self.on_open_popup = on_open_popup
        self.on_amount_entered = on_amount_entered
        self.on_date_changed = on_date_changed
        self.on_name_edited = on_name_edited
        self.on_add_row = on_add_row
        self.on_remove_row = on_remove_row
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
        self.table = QTableWidget(0, len(HEADERS))
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
        self.table.setAlternatingRowColors(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table)

        # --- кнопки «+» / «−» для строк, которые добавляет пользователь ---
        if on_add_row is not None or on_remove_row is not None:
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            if on_add_row is not None:
                self.add_btn = QPushButton("+")
                self.add_btn.setFixedWidth(38)
                self.add_btn.setToolTip("Добавить свой вид оплаты")
                self.add_btn.clicked.connect(on_add_row)
                buttons.addWidget(self.add_btn)
            if on_remove_row is not None:
                self.remove_btn = QPushButton("−")
                self.remove_btn.setFixedWidth(38)
                self.remove_btn.setToolTip("Убрать выделенный вид оплаты")
                self.remove_btn.clicked.connect(self._remove_selected)
                buttons.addWidget(self.remove_btn)
            root.addLayout(buttons)

        self.set_rows(self.row_keys)

    # ------------------------------------------------------------------ вид
    def set_rows(self, row_keys: list[str]) -> None:
        """Перестроить строки таблицы (состав строк меняется кнопкой «+»)."""

        self.row_keys = list(row_keys)
        self._updating = True
        self.table.setRowCount(len(self.row_keys))
        for row, key in enumerate(self.row_keys):
            name_item = QTableWidgetItem(self._row_title(key))
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if key.startswith(OTHER_PREFIX):
                flags |= Qt.ItemFlag.ItemIsEditable  # название вводится текстом
            name_item.setFlags(flags)
            name_item.setToolTip(self._row_tooltip(key))
            self.table.setItem(row, COL_NAME, name_item)
            for column in (COL_AMOUNT, COL_WEEK, COL_MONTH, COL_YEAR):
                item = QTableWidgetItem("")
                item_flags = (
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                editable = key in DIRECT_INPUT_ROWS or key.startswith(OTHER_PREFIX)
                if editable and column == COL_AMOUNT:
                    item_flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(item_flags)
                if column == COL_AMOUNT:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)
        self._updating = False
        self._fit_height()

    def _fit_height(self) -> None:
        rows = max(len(self.row_keys), 1)
        self.table.setFixedHeight(
            self.table.horizontalHeader().height()
            + rows * self.table.verticalHeader().defaultSectionSize()
            + 2
        )
        for column, width in COLUMN_WIDTHS.items():
            self.table.setColumnWidth(column, width)

    @staticmethod
    def _row_title(key: str) -> str:
        return ROW_TITLES.get(key, other_row_name(key))

    @staticmethod
    def _row_tooltip(key: str) -> str:
        if key.startswith(OTHER_PREFIX):
            return "Название и сумма вводятся прямо в ячейки"
        if key in DIRECT_INPUT_ROWS:
            return "Сумма вводится прямо в ячейку"
        return "Клик — окно со списком и полями сумм"

    def set_amount(self, row_key: str, column: int, text: str) -> None:
        if row_key not in self.row_keys:
            return
        row = self.row_keys.index(row_key)
        item = self.table.item(row, column)
        if item is None:
            return
        self._updating = True
        item.setText(text)
        self._updating = False

    def amount_text(self, row_key: str, column: int = COL_AMOUNT) -> str:
        if row_key not in self.row_keys:
            return ""
        item = self.table.item(self.row_keys.index(row_key), column)
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
        if self._updating:
            return
        key = self.row_keys[item.row()]
        if item.column() == COL_NAME:
            if key.startswith(OTHER_PREFIX) and self.on_name_edited is not None:
                self.on_name_edited(key, item.text())
            return
        if item.column() != COL_AMOUNT:
            return
        if key in POPUP_ROWS:
            return
        self.on_amount_entered(key, item.text())

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        key = self.row_keys[row]
        if key in POPUP_ROWS:
            self.on_open_popup(key, self.table, row)

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.row_keys):
            return
        key = self.row_keys[row]
        if not key.startswith(OTHER_PREFIX):
            QMessageBox.information(
                self,
                "Строку не удалить",
                "«Поставщики» — постоянная строка. Удалить можно только "
                "добавленные виды оплат.",
            )
            return
        if self.on_remove_row is not None:
            self.on_remove_row(key)


class ReceiptsView(QWidget):
    """Слева блоки «Поступление» и «Оплата», справа — «Сводка»."""

    def __init__(
        self,
        store: DataStore,
        on_data_changed: Callable[[], None] | None = None,
        summary_widget: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_data_changed = on_data_changed
        self.summary_widget = summary_widget
        self._popup: AmountPopup | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # --- левая колонка: блоки друг под другом, в половину ширины ---
        left = QWidget()
        self.left = left
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        self.incoming = MoneyBlock(
            "Поступление",
            [CASH_ROW, CARD_ROW, CUSTOMERS_ROW],
            on_open_popup=self.open_popup,
            on_amount_entered=self.save_direct_amount,
            on_date_changed=self.refresh,
        )
        self.outgoing = MoneyBlock(
            "Оплата",
            self._outgoing_rows(),
            on_open_popup=self.open_popup,
            on_amount_entered=self.save_direct_amount,
            on_date_changed=self.refresh,
            on_name_edited=self.rename_other_row,
            on_add_row=self.add_other_row,
            on_remove_row=self.remove_other_row,
        )
        left_layout.addWidget(self.incoming)
        left_layout.addWidget(self.outgoing)

        hint = QLabel(
            "«Сумма» — за выбранную дату; наличные, карты и свои виды оплат "
            "вводятся прямо в ячейку, название вида — текстом в первой "
            "колонке. «Неделя/Месяц/Год» — нарастающий итог с начала "
            "периода по эту дату. Клик по строке «Покупатели»/«Поставщики» "
            "открывает окно справа со списком и полями сумм; суммы из "
            "недельных таблиц видны здесь, и наоборот."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        left_layout.addWidget(hint)
        left_layout.addStretch(1)
        self.left_layout = left_layout
        root.addWidget(left, stretch=1)

        # --- справа на всю высоту — «Сводка» ---
        if summary_widget is not None:
            # Ignored по горизонтали: сводка не должна отжимать блоки
            # влево — обе половины получают ровно по 50% ширины.
            summary_widget.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
            )
            root.addWidget(summary_widget, stretch=1)

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

    # ------------------------------------------------------- прочие виды оплат
    def _outgoing_rows(self) -> list[str]:
        return [SUPPLIERS_ROW] + [
            other_row_key(name) for name in other_payment_names(self.store.data)
        ]

    def refresh_outgoing_rows(self) -> None:
        self.outgoing.set_rows(self._outgoing_rows())

    def add_other_row(self) -> None:
        """Кнопка «+»: новая строка вида оплаты с вводом названия."""

        used = set(other_payment_names(self.store.data))
        name = NEW_PAYMENT_NAME
        index = 2
        while name in used:
            name = f"{NEW_PAYMENT_NAME} {index}"
            index += 1
        add_other_payment(
            self.store.data, name, self.outgoing.current_date().isoformat()
        )
        self.store.save()
        self.refresh_outgoing_rows()
        self.refresh()
        # Сразу даём переименовать строку: курсор в ячейку названия.
        row = self.outgoing.row_keys.index(other_row_key(name))
        self.outgoing.table.setCurrentCell(row, COL_NAME)
        self.outgoing.table.editItem(self.outgoing.table.item(row, COL_NAME))

    def remove_other_row(self, row_key: str) -> None:
        """Кнопка «−»: убрать вид оплаты вместе с его суммами."""

        name = other_row_name(row_key)
        answer = QMessageBox.question(
            self,
            "Удалить вид оплаты",
            f"Убрать «{name}» из блока «Оплата»?\n\n"
            "Все введённые по этой строке суммы будут удалены.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_other_payment(self.store.data, name)
        self.store.save()
        self.refresh_outgoing_rows()
        self.refresh()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def rename_other_row(self, row_key: str, new_name: str) -> None:
        """Новое название вида оплаты, введённое прямо в ячейку."""

        old_name = other_row_name(row_key)
        cleaned = (new_name or "").strip()
        if not cleaned or cleaned == old_name:
            self.refresh_outgoing_rows()
            self.refresh()
            return
        if cleaned in other_payment_names(self.store.data):
            QMessageBox.information(
                self,
                "Такое название уже есть",
                f"Вид оплаты «{cleaned}» уже есть в списке.",
            )
            self.refresh_outgoing_rows()
            self.refresh()
            return
        rename_other_payment(self.store.data, old_name, cleaned)
        self.store.save()
        self.refresh_outgoing_rows()
        self.refresh()

    # --------------------------------------------------------- ввод в ячейку
    def save_direct_amount(self, row_key: str, text: str) -> None:
        """Сохранить сумму, введённую прямо в ячейку."""

        block = self.incoming
        if row_key == SUPPLIERS_ROW:
            block = self.outgoing  # строка поставщиков не вводится ячейкой
        if row_key.startswith(OTHER_PREFIX):
            block = self.outgoing
        day_iso = block.current_date().isoformat()
        cleaned = (text or "").strip()
        amount: Decimal | None = None
        if cleaned:
            try:
                amount = parse_money(cleaned)
            except ValueError:
                amount = None
        if amount is not None and amount < 0:
            amount = None

        if row_key == CASH_ROW or row_key == CARD_ROW:
            source = CASH_SOURCE if row_key == CASH_ROW else CARD_SOURCE
            set_daily_cash_receipt(self.store.data, source, day_iso, amount)
        elif row_key.startswith(OTHER_PREFIX):
            set_daily_other_payment(
                self.store.data, other_row_name(row_key), day_iso, amount
            )
        else:
            return
        self.store.save()
        self.refresh()
        # Недельные таблицы и «Сводку» не трогаем: у наличных, карт и
        # прочих видов оплат нет контрагента, в таблицах они не участвуют
        # (ADR-017/018).

    # -------------------------------------------------------------- выпадающее
    def open_popup(
        self, row_key: str, anchor: QWidget | None = None, row: int | None = None
    ) -> AmountPopup | None:
        """Открыть окно со списком контрагентов справа от строки."""

        block = self.outgoing if row_key == SUPPLIERS_ROW else self.incoming
        table = block.table
        if row is None:
            row = block.row_keys.index(row_key) if row_key in block.row_keys else 0
        row_top = table.rowViewportPosition(row)
        if anchor is None:
            anchor = table
        title = ROW_TITLES[row_key]
        day_iso = block.current_date().isoformat()

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

        # Поля заполняем уже сохранёнными суммами: при повторном
        # открытии окна введённое не должно «пропадать».
        values = {
            person.id: self.daily_payment_amount(person.id, day_iso)
            for person in people
        }
        popup = AmountPopup(
            title,
            [(person.id, person.name) for person in people],
            self._make_saver(block, row_key, [person.id for person in people]),
            self,
            hint,
            values,
        )
        # Окно удаляется при закрытии (WA_DeleteOnClose) — не держим ссылку.
        popup.destroyed.connect(self._forget_popup)
        self._popup = popup
        popup.open_beside(anchor, row_top)
        return popup

    def _forget_popup(self, *_args) -> None:
        self._popup = None

    def _make_saver(self, block: MoneyBlock, row_key: str, ids: list[str]):
        def save(values: dict[str, Decimal]) -> None:
            day_iso = block.current_date().isoformat()
            for counterparty_id in ids:
                amount = values.get(counterparty_id) or ZERO
                set_daily_operation(
                    self.store.data,
                    counterparty_id,
                    day_iso,
                    KIND_PAYMENT,
                    amount if amount else None,
                )
            self.store.save()
            self.refresh()
            if self.on_data_changed is not None:
                self.on_data_changed()

        return save

    # ------------------------------------------------------------------ итоги
    def daily_payment_amount(self, counterparty_id: str, day_iso: str) -> Decimal:
        return sum(
            (
                operation_amount(op)
                for op in self.store.data.operations
                if op.kind == KIND_PAYMENT
                and op.counterparty_id == counterparty_id
                and op.date == day_iso
            ),
            ZERO,
        )

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

    def _other_total(self, name: str, day: date) -> Decimal:
        day_iso = day.isoformat()
        return sum(
            (
                other_payment_amount(item)
                for item in self.store.data.other_payments
                if item.name == name and item.date == day_iso
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
            items = (
                (receipt.date, cash_receipt_amount(receipt))
                for receipt in self.store.data.cash_receipts
                if receipt.source == source
            )
        elif key.startswith(OTHER_PREFIX):
            name = other_row_name(key)
            items = (
                (item.date, other_payment_amount(item))
                for item in self.store.data.other_payments
                if item.name == name
            )
        elif key == CUSTOMERS_ROW:
            ids = {c.id for c in self.store.data.customers}
            items = (
                (op.date, operation_amount(op))
                for op in self.store.data.operations
                if op.kind == KIND_PAYMENT and op.counterparty_id in ids
            )
        else:
            ids = {s.id for s in self.store.data.suppliers}
            items = (
                (op.date, operation_amount(op))
                for op in self.store.data.operations
                if op.kind == KIND_PAYMENT and op.counterparty_id in ids
            )

        for item_date, amount in items:
            item_day = _parse_day(item_date)
            if item_day is None or item_day > day:
                continue
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

        for key in self.outgoing.row_keys:
            if key == SUPPLIERS_ROW:
                value = self._payment_total(supplier_ids, outgoing_day)
            else:
                value = self._other_total(other_row_name(key), outgoing_day)
            self.outgoing.set_amount(key, COL_AMOUNT, _money_text(value))

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
