"""Окно «Операции контрагента» — журнал операций из карточки.

Кнопка «Операции и долги» в карточке поставщика/покупателя вела на
вкладку «Оплаты». С v0.9.0 эта вкладка занята блоками «Поступление» и
«Оплата», поэтому журнал стал отдельным окном с тем же содержимым:
фильтры, добавление, правка и удаление операций выбранного контрагента.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import styles
from ..models import Counterparty
from ..storage import DataStore
from ..views.operations_view import OperationsView


class OperationsJournalDialog(QDialog):
    """``kind``: ``"supplier"`` | ``"customer"``."""

    def __init__(
        self,
        store: DataStore,
        kind: str,
        counterparty_id: str | None = None,
        on_data_changed: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        assert kind in ("supplier", "customer")
        self.store = store
        self.kind = kind
        self.on_data_changed = on_data_changed
        noun = "поставщика" if kind == "supplier" else "покупателя"
        # v0.23.2: журнал поставщика ведётся за 3 месяца, журнал
        # покупателя — за 12 месяцев (просьба пользователя).
        # v0.23.9: оба журнала одной ширины — 800 px: в ней четыре
        # колонки влезают целиком, без горизонтального скроллинга.
        if kind == "supplier":
            width, months = 800, 3
        else:
            width, months = 800, 12
        person: Counterparty | None = next(
            (
                item
                for item in (
                    store.data.suppliers if kind == "supplier" else store.data.customers
                )
                if item.id == counterparty_id
            ),
            None,
        )

        self.setWindowTitle(f"Операции {noun}")
        self.setModal(False)
        self.resize(width, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # v0.27.3: в шапке окна — наименование контрагента (просьба
        # пользователя): несколько журналов легко перепутать, а имя в
        # заголовке системного окна видно хуже. Остальное по-прежнему
        # без заголовка: ни фильтров, ни кнопок (v0.23.1).
        if person is not None:
            title_label = QLabel(person.name)
            title_label.setObjectName("SectionTitle")
            title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
            title_label.setStyleSheet(
                f"font-size:{styles.fs(15)}pt;font-weight:600;"
                f"color:{styles.TEXT};padding:0 0 2px 2px;"
            )
            root.addWidget(title_label)
        self.view = OperationsView(
            store,
            on_data_changed=on_data_changed,
            parent=self,
            show_filters=False,
            show_actions=False,
            counterparty_kind=kind,
            period_months=months,
        )
        root.addWidget(self.view, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self.view.refresh_counterparty_filter()
        if counterparty_id:
            self.view.set_counterparty_filter(counterparty_id)
        else:
            self.view.refresh()
