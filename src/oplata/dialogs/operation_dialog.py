"""Окно добавления/редактирования операции из «Журнала операций».

Назначение: исправлять уже введённые поставки/оплаты и вручную добавлять
операцию с документом и комментарием, которых нет при вводе из недельной
таблицы и окна «Поставка».

Срок оплаты для «Прихода»:
* у существующей поставки сохраняется тот срок, который был рассчитан при
  вводе, если пользователь правит только сумму/документ/комментарий;
* если изменены тип, дата операции или контрагент, срок пересчитывается
  по действующей формуле calc.payment_due_date и это видно в окне до
  сохранения;
* у «Оплаты» срока нет.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
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
from ..dates import fmt_date, fmt_money_plain, parse_money
from ..models import KIND_INCOME, KIND_PAYMENT, Operation
from ..storage import DataStore
from ..widgets import blue_button, date_edit_value, make_date_edit
from .delivery_dialog import due_reason_text

OPERATION_KINDS = (KIND_INCOME, KIND_PAYMENT)
NO_COUNTERPARTY = "— выберите контрагента —"


def _parse_iso_date(text: str, fallback: date) -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return fallback


class OperationDialog(QDialog):
    """Создать операцию (``operation=None``) или отредактировать существующую."""

    def __init__(
        self,
        store: DataStore,
        operation: Operation | None = None,
        initial_counterparty_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.operation = operation
        self.changed = False
        self.saved_operation_id: str | None = operation.id if operation else None

        self._initial_kind = operation.kind if operation else KIND_INCOME
        self._initial_counterparty_id = (
            operation.counterparty_id if operation else (initial_counterparty_id or "")
        )
        self._initial_date = (
            _parse_iso_date(operation.date, date.today())
            if operation
            else date.today()
        )
        self._initial_due = self._read_initial_due()

        is_new = operation is None
        self.setWindowTitle("Новая операция" if is_new else "Редактирование операции")
        self.setModal(True)
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        title = QLabel("Новая операция" if is_new else "Редактирование операции")
        title.setStyleSheet("font-size:19px;font-weight:800")
        root.addWidget(title)

        root.addWidget(QLabel("Тип операции *"))
        self.kind_combo = QComboBox()
        kinds = list(OPERATION_KINDS)
        if operation and operation.kind and operation.kind not in kinds:
            kinds.append(operation.kind)  # не потерять старый нестандартный тип
        self.kind_combo.addItems(kinds)
        self.kind_combo.setCurrentText(self._initial_kind or KIND_INCOME)
        self.kind_combo.currentTextChanged.connect(lambda *_: self._refresh_due())
        root.addWidget(self.kind_combo)

        root.addWidget(QLabel("Поставщик / покупатель *"))
        self.counterparty_combo = QComboBox()
        self.counterparty_combo.addItem(NO_COUNTERPARTY, "")
        for supplier in store.data.suppliers:
            self.counterparty_combo.addItem(f"Поставщик: {supplier.name}", supplier.id)
        for customer in store.data.customers:
            self.counterparty_combo.addItem(f"Покупатель: {customer.name}", customer.id)
        initial_index = self.counterparty_combo.findData(
            self._initial_counterparty_id or ""
        )
        self.counterparty_combo.setCurrentIndex(max(initial_index, 0))
        self.counterparty_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_due()
        )
        root.addWidget(self.counterparty_combo)

        root.addWidget(QLabel("Дата операции *"))
        self.date_edit = make_date_edit(self._initial_date)
        self.date_edit.dateChanged.connect(lambda *_: self._refresh_due())
        root.addWidget(self.date_edit)

        root.addWidget(QLabel("Сумма *"))
        self.amount_edit = QLineEdit(self._initial_amount_text())
        self.amount_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        root.addWidget(self.amount_edit)

        root.addWidget(QLabel("Документ"))
        self.document_edit = QLineEdit(operation.document if operation else "")
        root.addWidget(self.document_edit)

        root.addWidget(QLabel("Комментарий"))
        self.comment_edit = QLineEdit(operation.comment if operation else "")
        root.addWidget(self.comment_edit)

        self.due_panel = QFrame()
        self.due_panel.setObjectName("Panel")
        due_layout = QVBoxLayout(self.due_panel)
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
        root.addWidget(self.due_panel)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Отмена")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        save_btn = blue_button("Добавить" if is_new else "Сохранить")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

        self._refresh_due()
        self.amount_edit.setFocus()
        self.amount_edit.selectAll()

    # ------------------------------------------------------------- начальные
    def _read_initial_due(self) -> date | None:
        if self.operation is None:
            return None
        counterparty = calc.find_counterparty(
            self.store.data, self.operation.counterparty_id
        )
        if counterparty is not None:
            return calc.operation_due_date(
                self.operation, counterparty, self.store.data.day_overrides
            )
        if self.operation.due_date:
            return _parse_iso_date(self.operation.due_date, self._initial_date)
        return None

    def _initial_amount_text(self) -> str:
        if self.operation is None:
            return "0,00"
        try:
            return fmt_money_plain(Decimal(self.operation.amount or "0"))
        except InvalidOperation:
            return self.operation.amount

    # ------------------------------------------------------------- срок оплаты
    def _selected_counterparty(self):
        counterparty_id = self.counterparty_combo.currentData() or ""
        return calc.find_counterparty(self.store.data, str(counterparty_id))

    def _due_inputs_changed(self) -> bool:
        if self.operation is None:
            return True
        return (
            self.kind_combo.currentText() != self._initial_kind
            or str(self.counterparty_combo.currentData() or "")
            != self._initial_counterparty_id
            or date_edit_value(self.date_edit) != self._initial_date
        )

    def _due_for_save(self) -> date | None:
        if self.kind_combo.currentText() != KIND_INCOME:
            return None
        if not self._due_inputs_changed() and self._initial_due is not None:
            return self._initial_due
        counterparty = self._selected_counterparty()
        if counterparty is None:
            return self._initial_due
        return calc.payment_due_date(
            date_edit_value(self.date_edit),
            counterparty,
            self.store.data.day_overrides,
        )

    def _refresh_due(self) -> None:
        is_income = self.kind_combo.currentText() == KIND_INCOME
        self.due_panel.setVisible(is_income)
        if not is_income:
            return
        due = self._due_for_save()
        self.due_label.setText(fmt_date(due) if due else "—")
        counterparty = self._selected_counterparty()
        if self.operation is not None and not self._due_inputs_changed():
            self.due_reason_label.setText(
                "Срок сохранён при вводе. При изменении типа, даты или "
                "контрагента он будет пересчитан по отсрочке."
            )
        elif counterparty is None or due is None:
            self.due_reason_label.setText("Сначала выберите контрагента.")
        else:
            raw_due = calc.raw_due_date(
                date_edit_value(self.date_edit), counterparty
            )
            self.due_reason_label.setText(
                due_reason_text(counterparty, raw_due, due)
            )

    # ----------------------------------------------------------------- save
    def _save(self) -> None:
        counterparty_id = str(self.counterparty_combo.currentData() or "")
        if not counterparty_id:
            QMessageBox.warning(
                self, "Укажите контрагента", "Выберите поставщика или покупателя."
            )
            self.counterparty_combo.setFocus()
            return

        try:
            amount = parse_money(self.amount_edit.text())
        except ValueError:
            amount = None
        if amount is None or amount <= 0:
            QMessageBox.warning(
                self,
                "Укажите сумму",
                "Введите сумму цифрами, например: 15 000,75",
            )
            self.amount_edit.setFocus()
            self.amount_edit.selectAll()
            return

        kind = self.kind_combo.currentText()
        due = self._due_for_save() if kind == KIND_INCOME else None
        values = {
            "kind": kind,
            "counterparty_id": counterparty_id,
            "date": date_edit_value(self.date_edit).isoformat(),
            "amount": f"{amount:.2f}",
            "document": self.document_edit.text().strip(),
            "comment": self.comment_edit.text().strip(),
            "due_date": due.isoformat() if due else "",
        }
        if self.operation is None:
            operation = Operation(**values)
            self.store.data.operations.append(operation)
            self.operation = operation
        else:
            operation = self.operation
            for field, value in values.items():
                setattr(operation, field, value)

        self.store.save()
        self.saved_operation_id = operation.id
        self.changed = True
        self.accept()
