"""Экран «Операции» — журнал операций с фильтрами (макет 04-operations.png).

Этап оболочки: операций пока нет (вводить их негде — это следующий этап),
поэтому журнал показывает «Ничего не найдено.», а фильтры уже работают
по списку операций из хранилища (он пуст). Суммы не считаются.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..storage import DataStore
from ..widgets import make_panel

ALL_SUPPLIERS = "— все поставщики —"
ALL_OPERATIONS = "Все операции"


class OperationsView(QWidget):
    def __init__(self, store: DataStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store

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
        filters.addWidget(self.date_from)
        self.date_to = QLineEdit()
        self.date_to.setPlaceholderText("дд.мм.гггг")
        self.date_to.setInputMask("00.00.0000;_")
        self.date_to.setMaximumWidth(120)
        filters.addWidget(self.date_to)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск: поставщик, документ, комментарий…")
        self.search.setMinimumWidth(260)
        self.search.textChanged.connect(lambda *_: self.refresh())
        filters.addWidget(self.search)

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
        title = QLabel("Журнал операций")
        title.setObjectName("PanelTitle")
        panel_layout.addWidget(title)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Тип", "Поставщик", "Дата", "Сумма", "Документ", "Комментарий"]
        )
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
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

    # ----------------------------------------------------------------- вид
    def _filtered_operations(self):
        supplier_id = self.supplier_filter.currentData() or ""
        kind = self.kind_filter.currentText()
        query = self.search.text().strip().lower()
        supplier_names = {s.id: s.name for s in self.store.data.suppliers}
        result = []
        for op in self.store.data.operations:  # на этапе оболочки список пуст
            if supplier_id and op.counterparty_id != supplier_id:
                continue
            if kind != ALL_OPERATIONS and op.kind != kind:
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
        supplier_names = {s.id: s.name for s in self.store.data.suppliers}

        if not operations:
            self.table.setRowCount(1)
            item = QTableWidgetItem("Ничего не найдено.")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 6)
            return

        self.table.clearSpans()
        self.table.setRowCount(len(operations))
        for row, op in enumerate(operations):
            values = [
                op.kind,
                supplier_names.get(op.counterparty_id, ""),
                op.date,
                op.amount,
                op.document,
                op.comment,
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)
