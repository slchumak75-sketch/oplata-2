"""Экран «Операции» — журнал операций с фильтрами (макет 04-operations.png).

Сюда попадают поставки из окна «Поставка» и суммы из ячеек недельных
таблиц. С v0.4.0 операцию можно добавить вручную, отредактировать
(двойной клик или кнопка «Изменить») и удалить с подтверждением.
Долги здесь не считаются: журнал показывает исходные операции, а
расчёты таблицы поставщиков делает calc.py.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..dates import fmt_date, fmt_money_plain
from ..dialogs.operation_dialog import OperationDialog
from ..models import Operation
from ..storage import DataStore
from ..widgets import danger_button, green_button, make_panel

ALL_SUPPLIERS = "— все поставщики —"
ALL_OPERATIONS = "Все операции"


def _display_date(iso: str) -> str:
    try:
        return fmt_date(date.fromisoformat(iso))
    except ValueError:
        return iso


def _display_amount(amount: str) -> str:
    try:
        return fmt_money_plain(Decimal(amount or "0"))
    except InvalidOperation:
        return amount


def _parse_filter_date(text: str) -> date | None:
    """Полностью введённая дата фильтра дд.мм.гггг; неполная/битая — None."""
    cleaned = (text or "").strip()
    if len(cleaned) != 10 or "_" in cleaned:
        return None
    try:
        return datetime.strptime(cleaned, "%d.%m.%Y").date()
    except ValueError:
        return None


def _operation_date(operation: Operation) -> date | None:
    try:
        return date.fromisoformat(operation.date)
    except (TypeError, ValueError):
        return None


class OperationsView(QWidget):
    def __init__(
        self,
        store: DataStore,
        on_data_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        # После добавления/правки/удаления главное окно обновляет таблицы
        # поставщиков/покупателей и «Сводку»; сам журнал обновляется здесь.
        self.on_data_changed = on_data_changed

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # --- фильтры ---
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.supplier_filter = QComboBox()
        self.supplier_filter.currentIndexChanged.connect(lambda *_: self.refresh())
        filters.addWidget(self.supplier_filter)

        self.kind_filter = QComboBox()
        # TODO(этап операций): список типов уточнить у пользователя.
        self.kind_filter.addItems([ALL_OPERATIONS, "Приход", "Оплата"])
        self.kind_filter.currentIndexChanged.connect(lambda *_: self.refresh())
        filters.addWidget(self.kind_filter)

        # Даты «от/до»: на макете два пустых поля дд.мм.гггг.
        # QDateEdit без значения не бывает, поэтому на этапе оболочки
        # используем текстовые поля с маской-подсказкой.
        self.date_from = QLineEdit()
        self.date_from.setPlaceholderText("дд.мм.гггг")
        self.date_from.setInputMask("00.00.0000;_")
        self.date_from.setMaximumWidth(120)
        self.date_from.setToolTip("Дата операции, от")
        self.date_from.textChanged.connect(lambda *_: self.refresh())
        filters.addWidget(self.date_from)
        self.date_to = QLineEdit()
        self.date_to.setPlaceholderText("дд.мм.гггг")
        self.date_to.setInputMask("00.00.0000;_")
        self.date_to.setMaximumWidth(120)
        self.date_to.setToolTip("Дата операции, до")
        self.date_to.textChanged.connect(lambda *_: self.refresh())
        filters.addWidget(self.date_to)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск: поставщик, документ, комментарий…")
        self.search.setMinimumWidth(260)
        self.search.setMaximumWidth(560)
        self.search.textChanged.connect(lambda *_: self.refresh())
        filters.addWidget(self.search, stretch=1)

        reset_btn = QPushButton("Сбросить")
        reset_btn.clicked.connect(self.reset_filters)
        filters.addWidget(reset_btn)

        filters.addStretch(1)
        self.found_label = QLabel("Найдено: 0")
        self.found_label.setObjectName("Muted")
        filters.addWidget(self.found_label)
        root.addLayout(filters)

        # --- журнал ---
        panel, panel_layout = make_panel()
        title_row = QHBoxLayout()
        title = QLabel("Журнал операций")
        title.setObjectName("PanelTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.add_btn = green_button("+ Добавить")
        self.add_btn.clicked.connect(self._add_operation)
        title_row.addWidget(self.add_btn)
        self.edit_btn = QPushButton("Изменить")
        self.edit_btn.clicked.connect(lambda: self._edit_selected_operation())
        title_row.addWidget(self.edit_btn)
        self.delete_btn = danger_button("Удалить")
        self.delete_btn.clicked.connect(self._delete_selected_operation)
        title_row.addWidget(self.delete_btn)
        panel_layout.addLayout(title_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Тип", "Поставщик", "Дата", "Сумма", "Документ", "Комментарий"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._update_action_buttons)
        self.table.itemDoubleClicked.connect(
            lambda item: self._edit_selected_operation()
        )
        header = self.table.horizontalHeader()
        # Фиксированные колонки не должны ужиматься до «Накладная …»,
        # свободную ширину делят контрагент и комментарий.
        for column, width in ((0, 110), (2, 110), (3, 140), (4, 240)):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        panel_layout.addWidget(self.table)
        root.addWidget(panel, stretch=1)

        self.refresh_supplier_filter()
        self.refresh()

    # --------------------------------------------------------------- фильтры
    def refresh_supplier_filter(self) -> None:
        current_id = self.supplier_filter.currentData()
        self.supplier_filter.blockSignals(True)
        self.supplier_filter.clear()
        self.supplier_filter.addItem(ALL_SUPPLIERS, "")
        for supplier in self.store.data.suppliers:
            self.supplier_filter.addItem(supplier.name, supplier.id)
        index = self.supplier_filter.findData(current_id or "")
        self.supplier_filter.setCurrentIndex(max(index, 0))
        self.supplier_filter.blockSignals(False)

    def set_supplier_filter(self, supplier_id: str) -> None:
        index = self.supplier_filter.findData(supplier_id)
        if index >= 0:
            self.supplier_filter.setCurrentIndex(index)

    def reset_filters(self) -> None:
        self.supplier_filter.setCurrentIndex(0)
        self.kind_filter.setCurrentIndex(0)
        self.date_from.clear()
        self.date_to.clear()
        self.search.clear()
        self.refresh()

    # --------------------------------------------------------- действия CRUD
    def selected_operation_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        operation_id = item.data(Qt.ItemDataRole.UserRole)
        return str(operation_id) if operation_id else None

    def _find_operation(self, operation_id: str | None) -> Operation | None:
        if not operation_id:
            return None
        for operation in self.store.data.operations:
            if operation.id == operation_id:
                return operation
        return None

    def _update_action_buttons(self) -> None:
        has_selection = self._find_operation(self.selected_operation_id()) is not None
        self.edit_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)

    def _select_operation_row(self, operation_id: str | None) -> None:
        if not operation_id:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == operation_id:
                self.table.selectRow(row)
                self.table.setCurrentCell(row, 0)
                return

    def _after_operation_changed(self, operation_id: str | None = None) -> None:
        self.refresh()
        self._select_operation_row(operation_id)
        self._update_action_buttons()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def _add_operation(self) -> None:
        initial_id = self.supplier_filter.currentData() or None
        dialog = OperationDialog(
            self.store, initial_counterparty_id=str(initial_id or ""), parent=self
        )
        dialog.exec()
        if dialog.changed:
            self._after_operation_changed(dialog.saved_operation_id)

    def _edit_selected_operation(self) -> None:
        operation = self._find_operation(self.selected_operation_id())
        if operation is None:
            return
        dialog = OperationDialog(self.store, operation=operation, parent=self)
        dialog.exec()
        if dialog.changed:
            self._after_operation_changed(operation.id)

    def delete_operation(self, operation_id: str) -> bool:
        """Удалить операцию по id и обновить экраны.

        Подтверждение у пользователя спрашивает вызывающий код.
        """
        before = len(self.store.data.operations)
        self.store.data.operations = [
            op for op in self.store.data.operations if op.id != operation_id
        ]
        if len(self.store.data.operations) == before:
            return False
        self.store.save()
        self._after_operation_changed(None)
        return True

    def _delete_selected_operation(self) -> None:
        operation = self._find_operation(self.selected_operation_id())
        if operation is None:
            return
        counterparty_names = {
            **{s.id: s.name for s in self.store.data.suppliers},
            **{c.id: c.name for c in self.store.data.customers},
        }
        details = (
            f"{operation.kind} · {counterparty_names.get(operation.counterparty_id, '—')} · "
            f"{_display_date(operation.date)} · {_display_amount(operation.amount)}"
        )
        answer = QMessageBox.question(
            self,
            "Удаление операции",
            f"Удалить операцию?\n\n{details}\n\n"
            "Действие нельзя отменить. Если это поставка, её сумма исчезнет "
            "из «Прихода» дня поставки и из архивного «Долга» на срок оплаты.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_operation(operation.id)

    # ----------------------------------------------------------------- вид
    def _filtered_operations(self):
        supplier_id = self.supplier_filter.currentData() or ""
        kind = self.kind_filter.currentText()
        query = self.search.text().strip().lower()
        date_from = _parse_filter_date(self.date_from.text())
        date_to = _parse_filter_date(self.date_to.text())
        supplier_names = {
            **{s.id: s.name for s in self.store.data.suppliers},
            **{c.id: c.name for c in self.store.data.customers},
        }  # имена и поставщиков, и покупателей (ввод есть в обеих таблицах)
        result = []
        for op in self.store.data.operations:
            if supplier_id and op.counterparty_id != supplier_id:
                continue
            if kind != ALL_OPERATIONS and op.kind != kind:
                continue
            operation_day = _operation_date(op)
            if date_from is not None and (
                operation_day is None or operation_day < date_from
            ):
                continue
            if date_to is not None and (
                operation_day is None or operation_day > date_to
            ):
                continue
            haystack = " ".join(
                [supplier_names.get(op.counterparty_id, ""), op.document, op.comment]
            ).lower()
            if query and query not in haystack:
                continue
            result.append(op)
        return result

    def refresh(self) -> None:
        operations = self._filtered_operations()
        self.found_label.setText(f"Найдено: {len(operations)}")
        supplier_names = {
            **{s.id: s.name for s in self.store.data.suppliers},
            **{c.id: c.name for c in self.store.data.customers},
        }  # имена и поставщиков, и покупателей (ввод есть в обеих таблицах)

        if not operations:
            self.table.clearSpans()
            self.table.setRowCount(1)
            item = QTableWidgetItem("Ничего не найдено.")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 6)
            self._update_action_buttons()
            return

        self.table.clearSpans()
        self.table.setRowCount(len(operations))
        for row, op in enumerate(operations):
            values = [
                op.kind,
                supplier_names.get(op.counterparty_id, ""),
                _display_date(op.date),
                _display_amount(op.amount),
                op.document,
                op.comment,
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, op.id)
                if col == 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)
        self._update_action_buttons()
