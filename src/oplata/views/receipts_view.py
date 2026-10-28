"""Экран «Поступление и оплаты» (вкладка главного окна, v0.9.0).

Запрос пользователя: на вкладке очистить прежний журнал и сделать два
горизонтальных блока — «Поступление» и «Оплата».

Верх блока — строка даты по горизонтали:

* поле даты с выпадающим календарём (видимая стрелка справа);
* кнопка «Сегодня»;
* стрелки «←» и «→» — переход по датам на день назад/вперёд.

Ниже в блоке — вертикальный список строк. В блоке «Поступление» это
«Внесение наличными», «Поступление по картам» и «Покупатели»; в блоке
«Оплата» — «Поставщики». Клик левой кнопкой мыши по строке открывает
выпадающее окно под строкой: у «Покупателей»/«Поставщиков» — список
контрагентов с полем суммы у каждого, у наличных/карт — одно поле суммы.

Куда попадают введённые суммы (новых формул нет, см. ADR-016):

* «Покупатели» → операция «Оплата» покупателя на выбранную дату, то есть
  та же самая запись, что показывает колонка «Оплата» недельной таблицы
  покупателей;
* «Поставщики» → операция «Оплата» поставщика на дату (как ввод в ячейку
  недельной таблицы поставщиков);
* «Внесение наличными» / «Поступление по картам» → отдельная запись
  ``AppData.cash_receipts``: у неё нет контрагента, поэтому в недельных
  таблицах она не участвует, а показывается итогом дня здесь.

Справа в строке всегда виден итог по этой дате — просто сумма сохранённых
записей дня, без расчётов долгов и остатков.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import styles
from ..dates import fmt_date, fmt_money_plain, parse_money
from ..models import (
    CARD_SOURCE,
    CASH_SOURCE,
    KIND_PAYMENT,
    add_cash_receipt,
    add_customer_receipt,
    add_supplier_payment,
    cash_receipt_amount,
    operation_amount,
)
from ..storage import DataStore
from ..widgets import blue_button, make_amount_edit, make_date_edit

CASH_ROW = "cash"
CARD_ROW = "card"
CUSTOMERS_ROW = "customers"
SUPPLIERS_ROW = "suppliers"

ROW_TITLES = {
    CASH_ROW: "Внесение наличными",
    CARD_ROW: "Поступление по картам",
    CUSTOMERS_ROW: "Покупатели",
    SUPPLIERS_ROW: "Поставщики",
}

ROW_STYLE = (
    "QFrame#MoneyRow{background:#f4f7fd;border:1px solid #dbe3f4;"
    "border-radius:10px}"
    "QFrame#MoneyRow:hover{background:#e8eefb;border:1px solid #b9c9ef}"
)


def _today() -> date:
    return date.today()


def _qdate_to_date(qdate) -> date:
    return date(qdate.year(), qdate.month(), qdate.day())


class ClickableRow(QFrame):
    """Строка блока: клик левой кнопкой мыши открывает выпадающее окно."""

    def __init__(
        self, title: str, on_clicked: Callable[[], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MoneyRow")
        self.setStyleSheet(ROW_STYLE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(52)
        self._on_clicked = on_clicked

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight:700;font-size:15px")
        layout.addWidget(self.title_label, stretch=1)

        self.amount_label = QLabel("0,00")
        self.amount_label.setStyleSheet(
            f"font-weight:800;font-size:15px;color:{styles.BLUE}"
        )
        layout.addWidget(self.amount_label)

        arrow = QLabel("▾")
        arrow.setStyleSheet(f"color:{styles.BLUE};font-weight:800")
        layout.addWidget(arrow)

    def set_amount_text(self, text: str) -> None:
        self.amount_label.setText(text)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self._on_clicked()
            return
        super().mousePressEvent(event)


class AmountPopup(QFrame):
    """Выпадающее окно: список контрагентов с полем суммы у каждого.

    Если ``entries`` пуст — показывается одно поле суммы (наличные/карты).
    ``on_save`` получает словарь ``{ключ: Decimal}`` только по непустым
    полям; сохранение выполняет вызывающий экран.
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
        )
        # Закрытие по клику вне окна или по Esc удаляет его: держать
        # скрытые окна на каждый клик незачем.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._on_save = on_save
        self.edits: dict[str, object] = {}

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

        if not entries:
            fields_layout.addLayout(self._make_row("Сумма", ""))
        else:
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

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        save_btn = blue_button("Сохранить")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

    # ------------------------------------------------------------------ вид
    def _make_row(self, name: str, key: str) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(10)
        label = QLabel(name)
        label.setMinimumWidth(210)
        label.setMaximumWidth(320)
        layout.addWidget(label, stretch=1)
        edit = make_amount_edit()
        edit.setPlaceholderText("0,00")
        edit.setFixedWidth(170)
        edit.returnPressed.connect(self._save)
        self.edits[key] = edit
        layout.addWidget(edit)
        return layout

    def open_under(self, widget: QWidget) -> None:
        """Показать окно под строкой ``widget`` (как выпадающий список)."""

        point = widget.mapToGlobal(QPoint(0, widget.height()))
        self.adjustSize()
        self.move(point)
        self.show()
        screen = self.screen()
        if screen is not None:  # не даём окну уехать за край экрана
            free = screen.availableGeometry()
            frame = self.frameGeometry()
            if frame.bottom() > free.bottom():
                self.move(
                    self.x(), max(free.top(), point.y() - frame.height() - widget.height())
                )
            frame = self.frameGeometry()
            if frame.right() > free.right():
                self.move(max(free.left(), free.right() - frame.width()), self.y())
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
    """Блок «Поступление»/«Оплата»: строка даты + вертикальные строки."""

    def __init__(
        self,
        title: str,
        row_keys: list[str],
        on_row_clicked: Callable[[str], None],
        on_date_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.on_date_changed = on_date_changed
        self.rows: dict[str, ClickableRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        header = QLabel(title)
        header.setObjectName("PanelTitle")
        root.addWidget(header)

        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        date_row.addWidget(QLabel("Дата"))
        self.date_edit = make_date_edit(_today())
        self.date_edit.dateChanged.connect(lambda *_: self._on_date_changed())
        date_row.addWidget(self.date_edit)

        self.today_btn = QPushButton("Сегодня")
        self.today_btn.clicked.connect(self.go_today)
        date_row.addWidget(self.today_btn)

        self.prev_btn = QPushButton("←")
        self.prev_btn.setFixedWidth(38)
        self.prev_btn.setToolTip("День назад")
        self.prev_btn.clicked.connect(lambda: self.shift_date(-1))
        date_row.addWidget(self.prev_btn)

        self.next_btn = QPushButton("→")
        self.next_btn.setFixedWidth(38)
        self.next_btn.setToolTip("День вперёд")
        self.next_btn.clicked.connect(lambda: self.shift_date(1))
        date_row.addWidget(self.next_btn)

        date_row.addStretch(1)
        self.date_label = QLabel("")
        self.date_label.setObjectName("Muted")
        date_row.addWidget(self.date_label)
        root.addLayout(date_row)

        for key in row_keys:
            row = ClickableRow(ROW_TITLES[key], lambda k=key: on_row_clicked(k))
            self.rows[key] = row
            root.addWidget(row)

        root.addStretch(1)
        self._update_date_label()

    # ------------------------------------------------------------------ дата
    def current_date(self) -> date:
        return _qdate_to_date(self.date_edit.date())

    def _on_date_changed(self) -> None:
        self._update_date_label()
        if self.on_date_changed is not None:
            self.on_date_changed()

    def _update_date_label(self) -> None:
        self.date_label.setText(fmt_date(self.current_date()))

    def set_date(self, day: date) -> None:
        self.date_edit.blockSignals(True)
        self.date_edit.setDate(QDate(day.year, day.month, day.day))
        self.date_edit.blockSignals(False)
        self._update_date_label()
        if self.on_date_changed is not None:
            self.on_date_changed()

    def go_today(self) -> None:
        self.set_date(_today())

    def shift_date(self, days: int) -> None:
        self.set_date(self.current_date() + timedelta(days=days))


class ReceiptsView(QWidget):
    """Два горизонтальных блока: «Поступление» и «Оплата»."""

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

        blocks = QHBoxLayout()
        blocks.setSpacing(12)
        self.incoming = MoneyBlock(
            "Поступление",
            [CASH_ROW, CARD_ROW, CUSTOMERS_ROW],
            self.open_row_popup,
            on_date_changed=self.refresh,
        )
        self.outgoing = MoneyBlock(
            "Оплата",
            [SUPPLIERS_ROW],
            self.open_row_popup,
            on_date_changed=self.refresh,
        )
        blocks.addWidget(self.incoming, stretch=1)
        blocks.addWidget(self.outgoing, stretch=1)
        root.addLayout(blocks)

        hint = QLabel(
            "Клик по строке открывает окно со списком и полем суммы. "
            "Даты блоков независимы: у каждого своя строка даты."
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

    # -------------------------------------------------------------- выпадающее
    def open_row_popup(self, row_key: str) -> AmountPopup | None:
        """Открыть выпадающее окно строки ``row_key`` под этой строкой."""

        block = self.outgoing if row_key == SUPPLIERS_ROW else self.incoming
        row = block.rows[row_key]
        title = ROW_TITLES[row_key]

        if row_key in (CASH_ROW, CARD_ROW):
            entries: list[tuple[str, str]] = []
            hint = "Сумма за " + fmt_date(block.current_date()) + "."
        elif row_key == CUSTOMERS_ROW:
            people = [c for c in self.store.data.customers if not c.archived]
            if not people:
                QMessageBox.information(
                    self,
                    "Нет покупателей",
                    "Добавьте покупателя через кнопку «Карточка покупателя».",
                )
                return None
            entries = [(c.id, c.name) for c in people]
            hint = "Сумма поступит в колонку «Оплата» покупателя за дату."
        else:
            people = [s for s in self.store.data.suppliers if not s.archived]
            if not people:
                QMessageBox.information(
                    self,
                    "Нет поставщиков",
                    "Добавьте поставщика через кнопку «Карточка поставщика».",
                )
                return None
            entries = [(s.id, s.name) for s in people]
            hint = "Сумма появится в колонке «Оплата» поставщика за дату."

        popup = AmountPopup(title, entries, self._make_saver(block, row_key), self, hint)
        # Окно удаляется при закрытии (WA_DeleteOnClose) — не держим ссылку.
        popup.destroyed.connect(self._forget_popup)
        self._popup = popup
        popup.open_under(row)
        return popup

    def _forget_popup(self, *_args) -> None:
        self._popup = None

    def _make_saver(self, block: MoneyBlock, row_key: str):
        def save(values: dict[str, Decimal]) -> None:
            day_iso = block.current_date().isoformat()
            for key, amount in values.items():
                if row_key == CASH_ROW:
                    add_cash_receipt(
                        self.store.data, CASH_SOURCE, day_iso, amount
                    )
                elif row_key == CARD_ROW:
                    add_cash_receipt(
                        self.store.data, CARD_SOURCE, day_iso, amount
                    )
                elif row_key == CUSTOMERS_ROW:
                    add_customer_receipt(self.store.data, key, day_iso, amount)
                else:
                    add_supplier_payment(self.store.data, key, day_iso, amount)
            self.store.save()
            self.refresh()
            if self.on_data_changed is not None:
                self.on_data_changed()

        return save

    # ------------------------------------------------------------------ итоги
    def _cash_total(self, source: str, day_iso: str) -> Decimal:
        return sum(
            (
                cash_receipt_amount(receipt)
                for receipt in self.store.data.cash_receipts
                if receipt.date == day_iso and receipt.source == source
            ),
            Decimal("0"),
        )

    def _payment_total(self, counterparty_ids: set[str], day_iso: str) -> Decimal:
        return sum(
            (
                operation_amount(op)
                for op in self.store.data.operations
                if op.kind == KIND_PAYMENT
                and op.date == day_iso
                and op.counterparty_id in counterparty_ids
            ),
            Decimal("0"),
        )

    def refresh(self) -> None:
        incoming_day = self.incoming.current_date().isoformat()
        outgoing_day = self.outgoing.current_date().isoformat()
        customer_ids = {c.id for c in self.store.data.customers}
        supplier_ids = {s.id for s in self.store.data.suppliers}

        self.incoming.rows[CASH_ROW].set_amount_text(
            fmt_money_plain(self._cash_total(CASH_SOURCE, incoming_day))
        )
        self.incoming.rows[CARD_ROW].set_amount_text(
            fmt_money_plain(self._cash_total(CARD_SOURCE, incoming_day))
        )
        self.incoming.rows[CUSTOMERS_ROW].set_amount_text(
            fmt_money_plain(self._payment_total(customer_ids, incoming_day))
        )
        self.outgoing.rows[SUPPLIERS_ROW].set_amount_text(
            fmt_money_plain(self._payment_total(supplier_ids, outgoing_day))
        )
