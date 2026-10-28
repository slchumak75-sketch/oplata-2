"""Карточка поставщика / карточка покупателя (макет 06-supplier-card.png).

Макет есть только для поставщика. Для покупателя по запросу
пользователя убран блок условий поставщика: отсрочка и режимы
«Предоплата»/«Реализация» в карточке покупателя не показываются.

Поведение кнопок на этапе оболочки:
* «Сохранить» — сохранить выбранного (или создать, если выбран «— Новый —»)
  и закрыть окно;
* «+ Добавить» — создать нового из заполненных полей, остаться в окне;
* «Удалить» — удалить выбранного (с подтверждением);
* «Операции и долги» — закрыть карточку и открыть окно журнала
  операций этого контрагента (с v0.9.0 вкладка занята блоками
  «Поступление» и «Оплата»);
* «Закрыть» — просто закрыть.

Правило с макета: при отмеченном «Предоплата» или «Реализация» поле
«Отсрочка платежа» неактивно (это поведение интерфейса, не формула).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import Counterparty
from ..storage import DataStore
from ..widgets import blue_button, danger_button, green_button

NEW_ITEM_LABEL = "— Новый —"


class CounterpartyCardDialog(QDialog):
    """``kind``: ``"supplier"`` | ``"customer"``."""

    def __init__(
        self,
        store: DataStore,
        kind: str,
        selected_id: str | None = None,
        on_open_operations: Callable[[str, str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        assert kind in ("supplier", "customer")
        self.store = store
        self.kind = kind
        self.on_open_operations = on_open_operations
        self.changed = False  # были ли сохранения (главное окно перечитает списки)

        is_supplier = kind == "supplier"
        self._noun = "поставщика" if is_supplier else "покупателя"
        self._noun_title = "Поставщик" if is_supplier else "Покупатель"
        icon = "🏭" if is_supplier else "🛒"
        self.setWindowTitle(f"Карточка {self._noun}")
        self.setModal(True)
        self.setMinimumWidth(760)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel(f"{icon}  Карточка {self._noun}")
        title.setStyleSheet("font-size:18px;font-weight:800")
        root.addWidget(title)

        root.addWidget(QLabel(self._noun_title))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(lambda *_: self._load_selected())
        root.addWidget(self.combo)

        root.addWidget(QLabel(f"Наименование {self._noun} *"))
        self.name_edit = QLineEdit()
        root.addWidget(self.name_edit)

        # Условия оплаты есть только у поставщика. Для покупателя этот
        # блок не создаём вовсе (по запросу пользователя — лишнее).
        self.deferment_spin: QSpinBox | None = None
        self.chk_prepayment: QCheckBox | None = None
        self.chk_realization: QCheckBox | None = None
        if is_supplier:
            # Отсрочка (слева) + режим оплаты (справа) — как на макете.
            middle = QHBoxLayout()
            middle.setSpacing(24)

            left = QVBoxLayout()
            left.addWidget(QLabel("Отсрочка платежа, дней"))
            self.deferment_spin = QSpinBox()
            self.deferment_spin.setRange(0, 3650)
            self.deferment_spin.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )  # на макете поле растянуто на свою половину окна
            left.addWidget(self.deferment_spin)
            deferment_hint = QLabel(
                "Срок оплаты = дата поставки + отсрочка. 0 — срок вручную."
            )
            deferment_hint.setObjectName("Muted")
            deferment_hint.setWordWrap(True)
            left.addWidget(deferment_hint)
            left.addStretch(1)
            middle.addLayout(left, stretch=1)

            right = QVBoxLayout()
            right.addWidget(QLabel("Режим оплаты"))
            self.chk_prepayment = QCheckBox("Предоплата")
            self.chk_realization = QCheckBox("Реализация")
            self.chk_prepayment.toggled.connect(
                lambda *_: self._update_deferment_enabled()
            )
            self.chk_realization.toggled.connect(
                lambda *_: self._update_deferment_enabled()
            )
            right.addWidget(self.chk_prepayment)
            right.addWidget(self.chk_realization)
            mode_hint = QLabel("При выбранном режиме отсрочка неактивна.")
            mode_hint.setObjectName("Muted")
            right.addWidget(mode_hint)
            right.addStretch(1)
            middle.addLayout(right, stretch=1)
            root.addLayout(middle)

        self.chk_archived = QCheckBox(
            "В архиве (скрывать из списков, историю сохранить)"
        )
        root.addWidget(self.chk_archived)

        # --- кнопки ---
        buttons = QHBoxLayout()
        self.delete_btn = danger_button("Удалить")
        self.delete_btn.clicked.connect(self._delete)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)

        operations_btn = QPushButton("Операции и долги")
        operations_btn.clicked.connect(self._open_operations)
        buttons.addWidget(operations_btn)

        add_btn = green_button("+ Добавить")
        add_btn.clicked.connect(self._add)
        buttons.addWidget(add_btn)

        save_btn = blue_button("Сохранить")
        save_btn.clicked.connect(self._save_and_close)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

        self._reload_combo(selected_id)

    # --------------------------------------------------------------- данные
    def _items(self) -> list[Counterparty]:
        return (
            self.store.data.suppliers
            if self.kind == "supplier"
            else self.store.data.customers
        )

    def _current(self) -> Counterparty | None:
        current_id = self.combo.currentData()
        for item in self._items():
            if item.id == current_id:
                return item
        return None

    def _reload_combo(self, select_id: str | None = None) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(NEW_ITEM_LABEL, "")
        for item in self._items():
            self.combo.addItem(item.name, item.id)
        index = self.combo.findData(select_id or "")
        self.combo.setCurrentIndex(max(index, 0))
        self.combo.blockSignals(False)
        self._load_selected()

    def _load_selected(self) -> None:
        item = self._current()
        if item is None:
            self.name_edit.clear()
            if self.deferment_spin is not None:
                self.deferment_spin.setValue(0)
            if self.chk_prepayment is not None:
                self.chk_prepayment.setChecked(False)
            if self.chk_realization is not None:
                self.chk_realization.setChecked(False)
            self.chk_archived.setChecked(False)
        else:
            self.name_edit.setText(item.name)
            if self.deferment_spin is not None:
                self.deferment_spin.setValue(item.deferment_days)
            if self.chk_prepayment is not None:
                self.chk_prepayment.setChecked(item.prepayment)
            if self.chk_realization is not None:
                self.chk_realization.setChecked(item.realization)
            self.chk_archived.setChecked(item.archived)
        self.delete_btn.setEnabled(item is not None)
        self._update_deferment_enabled()

    def _update_deferment_enabled(self) -> None:
        if (
            self.deferment_spin is None
            or self.chk_prepayment is None
            or self.chk_realization is None
        ):
            return
        mode_selected = (
            self.chk_prepayment.isChecked() or self.chk_realization.isChecked()
        )
        self.deferment_spin.setEnabled(not mode_selected)

    def _apply_fields(self, item: Counterparty) -> None:
        item.name = self.name_edit.text().strip()
        # У покупателя этих полей в окне нет: существующие значения модели
        # не затираем, новые покупатели получают значения по умолчанию.
        if self.deferment_spin is not None:
            item.deferment_days = self.deferment_spin.value()
        if self.chk_prepayment is not None:
            item.prepayment = self.chk_prepayment.isChecked()
        if self.chk_realization is not None:
            item.realization = self.chk_realization.isChecked()
        item.archived = self.chk_archived.isChecked()

    def _validate_name(self) -> bool:
        if not self.name_edit.text().strip():
            QMessageBox.warning(
                self, "Заполните обязательное поле",
                f"Укажите наименование {self._noun}.",
            )
            self.name_edit.setFocus()
            return False
        return True

    #                                                                кнопки
    def _add(self) -> None:
        if not self._validate_name():
            return
        item = Counterparty(name="")
        self._apply_fields(item)
        self._items().append(item)
        self.store.save()
        self.changed = True
        self._reload_combo(item.id)

    def _save_and_close(self) -> None:
        if not self._validate_name():
            return
        item = self._current()
        if item is None:
            item = Counterparty(name="")
            self._items().append(item)
        self._apply_fields(item)
        self.store.save()
        self.changed = True
        self.accept()

    def _delete(self) -> None:
        item = self._current()
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить {self._noun} «{item.name}»?\n\n"
            "Если нужно просто скрыть из списков, используйте «В архиве».",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._items().remove(item)
        self.store.save()
        self.changed = True
        self._reload_combo(None)

    def _open_operations(self) -> None:
        item = self._current()
        self.accept()
        if self.on_open_operations is not None:
            self.on_open_operations(self.kind, item.id if item else "")
