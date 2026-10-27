"""Окно «Поставка» (макет docs/mockups/07-delivery-dialog.png).

Открывается кликом по поставщику в неделенной таблице «Поставщики»
(или двойным кликом по ячейке «Приход» — тогда дата подставлена из ячейки).

Правила (от пользователя, 27.10.2026):
* Срок оплаты = дата поставки + отсрочка; при предоплате или реализации
  отсрочка не применяется;
* если срок выпадает на выходной по календарю вкладки «Данные» — сдвиг
  на ближайший следующий рабочий день;
* после сохранения таблица переходит на неделю срока оплаты (делает
  главное окно по ``saved_due_date``).

Кнопки: «Карточка» — карточка поставщика; «Закрыть»; «Очистить» —
сброс полей; «Приход» — сохранить поставку и закрыть.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import calc
from ..dates import fmt_date, parse_money
from ..models import Counterparty, add_delivery
from ..storage import DataStore
from ..widgets import blue_button, date_edit_value, make_date_edit, set_date_edit

FOOTER_TEXT = (
    "Срок оплаты = дата поставки + отсрочка; при предоплате или реализации "
    "отсрочка не применяется. Если дата выпадает на выходной по календарю "
    "вкладки «Данные», программа сдвигает её на ближайший следующий рабочий "
    "день. После сохранения таблица сама перейдёт на неделю срока оплаты."
)


def conditions_text(counterparty: Counterparty) -> str:
    """Строка «Условия: …» под именем поставщика."""
    modes = []
    if counterparty.prepayment:
        modes.append("Предоплата")
    if counterparty.realization:
        modes.append("Реализация")
    if modes:
        return ", ".join(modes)
    if counterparty.deferment_days > 0:
        return f"Отсрочка: {counterparty.deferment_days} дн."
    return "Отсрочка: 0 дн. (срок = дата поставки)"


def due_reason_text(
    counterparty: Counterparty, raw_due: date, due: date
) -> str:
    """Подпись под сроком оплаты: почему именно эта дата."""
    if counterparty.prepayment and counterparty.realization:
        reason = "Предоплата и реализация: отсрочка не применяется."
    elif counterparty.prepayment:
        reason = "Предоплата: отсрочка не применяется."
    elif counterparty.realization:
        reason = "Реализация: отсрочка не применяется."
    elif counterparty.deferment_days > 0:
        reason = f"Дата поставки + {counterparty.deferment_days} дн. отсрочки."
    else:
        reason = "Отсрочка 0: срок оплаты = дата поставки."
    if due != raw_due:
        reason += " Дата выпала на выходной — сдвинута на ближайший рабочий день."
    return reason


class DeliveryDialog(QDialog):
    """Ввод одной поставки для конкретного поставщика."""

    def __init__(
        self,
        store: DataStore,
        supplier_id: str,
        preset_date: date | None = None,
        on_open_card: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_open_card = on_open_card
        self.supplier: Counterparty | None = calc.find_counterparty(
            store.data, supplier_id
        )
        self.initial_date = preset_date or date.today()
        self.saved_due_date: date | None = None
        self.changed = False

        self.setWindowTitle("Поставка")
        self.setModal(True)
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Поставка")
        title.setStyleSheet("font-size:19px;font-weight:800")
        top.addWidget(title)
        top.addStretch(1)
        card_btn = QPushButton("Карточка")
        card_btn.clicked.connect(self._open_card)
        top.addWidget(card_btn)
        root.addLayout(top)

        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-size:16px;font-weight:800")
        root.addWidget(self.name_label)
        self.conditions_label = QLabel()
        self.conditions_label.setObjectName("Muted")
        root.addWidget(self.conditions_label)

        root.addWidget(QLabel("Дата поставки *"))
        self.date_edit = make_date_edit(self.initial_date)
        self.date_edit.dateChanged.connect(lambda *_: self._refresh_due())
        root.addWidget(self.date_edit)

        root.addWidget(QLabel("Сумма с НДС *"))
        self.amount_edit = QLineEdit("0,00")
        self.amount_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.amount_edit)
        amount_hint = QLabel(
            "Одна сумма уже с НДС в руб.; на срок оплаты она появится в «Долге»."
        )
        amount_hint.setObjectName("Muted")
        root.addWidget(amount_hint)

        due_panel = QFrame()
        due_panel.setObjectName("Panel")
        due_layout = QVBoxLayout(due_panel)
        due_layout.setContentsMargins(14, 10, 14, 10)
        due_layout.setSpacing(3)
        due_title = QLabel("СРОК ОПЛАТЫ")
        due_title.setObjectName("KpiTitle")
        due_layout.addWidget(due_title)
        self.due_label = QLabel()
        self.due_label.setStyleSheet("font-size:19px;font-weight:800")
        due_layout.addWidget(self.due_label)
        self.due_reason_label = QLabel()
        self.due_reason_label.setObjectName("Muted")
        self.due_reason_label.setWordWrap(True)
        due_layout.addWidget(self.due_reason_label)
        root.addWidget(due_panel)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        clear_btn = QPushButton("Очистить")
        clear_btn.clicked.connect(self._clear)
        buttons.addWidget(clear_btn)
        save_btn = blue_button("Приход")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

        footer = QLabel(FOOTER_TEXT)
        footer.setObjectName("Muted")
        footer.setWordWrap(True)
        root.addWidget(footer)

        self._refresh_supplier_info()
        self.amount_edit.setFocus()
        self.amount_edit.selectAll()

    # ------------------------------------------------------------------ вид
    def _refresh_supplier_info(self) -> None:
        if self.supplier is None:
            self.name_label.setText("—")
            self.conditions_label.setText("Поставщик не найден")
            return
        self.name_label.setText(self.supplier.name)
        self.conditions_label.setText(f"Условия: {conditions_text(self.supplier)}")
        self._refresh_due()

    def _current_due(self) -> tuple[date, date] | None:
        """(срок до переноса с выходного, итоговый срок)."""
        if self.supplier is None:
            return None
        delivery = date_edit_value(self.date_edit)
        raw = calc.raw_due_date(delivery, self.supplier)
        due = calc.payment_due_date(
            delivery, self.supplier, self.store.data.day_overrides
        )
        return raw, due

    def _refresh_due(self) -> None:
        current = self._current_due()
        if current is None or self.supplier is None:
            self.due_label.setText("—")
            self.due_reason_label.setText("")
            return
        raw, due = current
        self.due_label.setText(fmt_date(due))
        self.due_reason_label.setText(due_reason_text(self.supplier, raw, due))

    # --------------------------------------------------------------- кнопки
    def _open_card(self) -> None:
        if self.on_open_card is None:
            return
        self.on_open_card()
        # Карточка могла изменить условия (или вообще удалить поставщика).
        if self.supplier is not None:
            self.supplier = calc.find_counterparty(
                self.store.data, self.supplier.id
            )
        if self.supplier is None:
            self.reject()
            return
        self._refresh_supplier_info()

    def _clear(self) -> None:
        set_date_edit(self.date_edit, self.initial_date)
        self.amount_edit.setText("0,00")
        self.amount_edit.setFocus()
        self.amount_edit.selectAll()
        self._refresh_due()

    def _save(self) -> None:
        if self.supplier is None:
            self.reject()
            return
        try:
            amount = parse_money(self.amount_edit.text())
        except ValueError:
            amount = None
        if amount is None or amount <= 0:
            QMessageBox.warning(
                self,
                "Укажите сумму",
                "Введите сумму поставки с НДС, например: 15 000,75",
            )
            self.amount_edit.setFocus()
            self.amount_edit.selectAll()
            return
        current = self._current_due()
        assert current is not None
        _raw, due = current
        delivery = date_edit_value(self.date_edit)
        add_delivery(
            self.store.data,
            self.supplier.id,
            delivery.isoformat(),
            due.isoformat(),
            amount,
        )
        self.store.save()
        self.saved_due_date = due
        self.changed = True
        self.accept()
