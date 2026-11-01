"""Окно «Реализация» для покупателя.

По запросу пользователя окно повторяет «Поставку», но называется
«Реализация», поле даты подписано «Дата реализации», а поле «Срок
оплаты» отсутствует: у реализации покупателя срока оплаты нет.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import calc
from ..dates import parse_money
from ..models import Counterparty, add_realization
from ..storage import DataStore
from .. import styles
from ..widgets import (
    blue_button,
    date_edit_value,
    make_amount_edit,
    make_date_edit,
    set_date_edit,
)


class RealizationDialog(QDialog):
    """Ввод одной реализации для конкретного покупателя."""

    def __init__(
        self,
        store: DataStore,
        customer_id: str,
        preset_date: date | None = None,
        on_open_card: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_open_card = on_open_card
        self.customer: Counterparty | None = calc.find_counterparty(
            store.data, customer_id
        )
        self.initial_date = preset_date or date.today()
        self.saved_date: date | None = None
        self.changed = False

        # v0.27.3: в шапке — наименование покупателя (просьба
        # пользователя) — так же, как в окне «Поставка».
        title = "Реализация"
        if self.customer is not None:
            title = f"{title} — {self.customer.name}"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("Реализация")
        title.setStyleSheet(f"font-size:{styles.fs(19)}px;font-weight:800")
        top.addWidget(title)
        top.addStretch(1)
        card_btn = QPushButton("Карточка")
        card_btn.clicked.connect(self._open_card)
        top.addWidget(card_btn)
        root.addLayout(top)

        self.name_label = QLabel()
        self.name_label.setStyleSheet(f"font-size:{styles.fs(16)}px;font-weight:800")
        root.addWidget(self.name_label)

        root.addWidget(QLabel("Дата реализации *"))
        self.date_edit = make_date_edit(self.initial_date)
        root.addWidget(self.date_edit)

        root.addWidget(QLabel("Сумма с НДС *"))
        self.amount_edit = make_amount_edit()
        root.addWidget(self.amount_edit)
        amount_hint = QLabel(
            "Одна сумма уже с НДС в руб.; она появится в столбце "
            "«Реализация» на выбранную дату."
        )
        amount_hint.setObjectName("Muted")
        amount_hint.setWordWrap(True)
        root.addWidget(amount_hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        clear_btn = QPushButton("Очистить")
        clear_btn.clicked.connect(self._clear)
        buttons.addWidget(clear_btn)
        save_btn = blue_button("Реализация")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

        footer = QLabel(
            "После сохранения таблица перейдёт на неделю даты реализации."
        )
        footer.setObjectName("Muted")
        footer.setWordWrap(True)
        root.addWidget(footer)

        self._refresh_customer_info()
        self.amount_edit.setFocus()
        self.amount_edit.selectAll()

    def _refresh_customer_info(self) -> None:
        self.name_label.setText(self.customer.name if self.customer else "—")

    def _open_card(self) -> None:
        if self.on_open_card is None:
            return
        self.on_open_card()
        if self.customer is not None:
            self.customer = calc.find_counterparty(
                self.store.data, self.customer.id
            )
        if self.customer is None:
            self.reject()
            return
        self._refresh_customer_info()

    def _clear(self) -> None:
        set_date_edit(self.date_edit, self.initial_date)
        self.amount_edit.clear()
        self.amount_edit.setFocus()

    def _save(self) -> None:
        if self.customer is None:
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
                "Введите сумму реализации с НДС, например: 15 000,75",
            )
            self.amount_edit.setFocus()
            self.amount_edit.selectAll()
            return

        realization_date = date_edit_value(self.date_edit)
        add_realization(
            self.store.data,
            self.customer.id,
            realization_date.isoformat(),
            amount,
        )
        self.store.save()
        self.saved_date = realization_date
        self.changed = True
        self.accept()
