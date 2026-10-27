"""Главное окно: тёмно-синяя шапка + 5 экранов (QStackedWidget).

Шапка по макетам: слева «▦ Оплаты / Строймаркет», далее вкладки
Сводка | Поставщики | Покупатели | Операции | Данные, справа кнопки
«Карточка поставщика» и «Карточка покупателя».
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __app_name__, __version__, styles
from .dialogs.counterparty_card import CounterpartyCardDialog
from .dialogs.delivery_dialog import DeliveryDialog
from .storage import DataStore
from .views.data_view import DataView
from .views.operations_view import OperationsView
from .views.summary_view import SummaryView
from .views.week_view import WeekView

NAV_TITLES = ("Сводка", "Поставщики", "Покупатели", "Операции", "Данные")
IDX_SUMMARY, IDX_SUPPLIERS, IDX_CUSTOMERS, IDX_OPERATIONS, IDX_DATA = range(5)


class MainWindow(QMainWindow):
    def __init__(self, store: DataStore) -> None:
        super().__init__()
        self.store = store
        self.setWindowTitle(f"{__app_name__}  v{__version__}")
        self.resize(1440, 860)
        self.setMinimumSize(1100, 640)

        root = QWidget()
        root.setObjectName("PageRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        # --- экраны (создаём до шапки: кнопкам шапки нужны колбэки) ---
        self.summary_view = SummaryView(
            store, lambda sid: self.open_counterparty_card("supplier", sid)
        )
        self.suppliers_view = WeekView(
            store,
            "supplier",
            lambda sid: self.open_counterparty_card("supplier", sid),
            on_data_changed=self.refresh_after_week_edit,
            on_open_delivery=self.open_delivery_dialog,
        )
        self.customers_view = WeekView(
            store,
            "customer",
            lambda cid: self.open_counterparty_card("customer", cid),
            on_data_changed=self.refresh_after_week_edit,
        )
        self.operations_view = OperationsView(
            store, on_data_changed=self.refresh_after_operation_change
        )
        self.data_view = DataView(
            store, on_calendar_saved=self.refresh_after_calendar_change
        )

        self.stack = QStackedWidget()
        for view in (
            self.summary_view,
            self.suppliers_view,
            self.customers_view,
            self.operations_view,
            self.data_view,
        ):
            self.stack.addWidget(view)

        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self.stack, stretch=1)
        self.show_page(IDX_SUMMARY)  # стартовый экран — «Сводка»

    # ------------------------------------------------------------------ шапка
    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setFixedHeight(64)
        header.setStyleSheet(f"QFrame{{background:{styles.NAVY}}}")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)

        logo = QVBoxLayout()
        logo.setSpacing(0)
        logo_title = QLabel("▦  Оплаты")
        logo_title.setStyleSheet("color:#ffffff;font-size:19px;font-weight:800")
        logo_sub = QLabel("Строймаркет")
        logo_sub.setStyleSheet("color:#c9d6f2;font-size:11px")
        logo.addWidget(logo_title)
        logo.addWidget(logo_sub)
        layout.addLayout(logo)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        nav_style = (
            "QPushButton{background:transparent;color:#ffffff;border:none;"
            "border-radius:8px;padding:7px 16px;font-weight:700;font-size:14px}"
            "QPushButton:hover{background:rgba(255,255,255,0.14)}"
            f"QPushButton:checked{{background:#ffffff;color:{styles.NAVY}}}"
        )
        for index, title in enumerate(NAV_TITLES):
            btn = QPushButton(title)
            btn.setCheckable(True)
            btn.setStyleSheet(nav_style)
            btn.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            self.nav_group.addButton(btn)
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch(1)

        card_style = (
            f"QPushButton{{background:#ffffff;color:{styles.TEXT};border:none;"
            "border-radius:8px;padding:8px 16px;font-weight:700}"
            "QPushButton:hover{background:#e8eefb}"
        )
        supplier_card_btn = QPushButton("🏭  Карточка поставщика")
        supplier_card_btn.setStyleSheet(card_style)
        supplier_card_btn.clicked.connect(
            lambda: self.open_counterparty_card("supplier", None)
        )
        layout.addWidget(supplier_card_btn)

        customer_card_btn = QPushButton("🛒  Карточка покупателя")
        customer_card_btn.setStyleSheet(card_style)
        customer_card_btn.clicked.connect(
            lambda: self.open_counterparty_card("customer", None)
        )
        layout.addWidget(customer_card_btn)
        return header

    # -------------------------------------------------------------- навигация
    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    # -------------------------------------------------------------- поставка
    def open_delivery_dialog(self, supplier_id: str, preset_date=None) -> None:
        """Окно «Поставка» по клику по имени поставщика.

        После сохранения (макеты/правила пользователя): таблица сама
        переходит на неделю срока оплаты, чтобы было видно «Долг».
        """
        dialog = DeliveryDialog(
            self.store,
            supplier_id,
            preset_date=preset_date,
            on_open_card=lambda: self.open_counterparty_card(
                "supplier", supplier_id
            ),
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.operations_view.refresh()
            self.summary_view.refresh()
            if dialog.saved_due_date is not None:
                self.suppliers_view.goto_date(dialog.saved_due_date)
            else:
                self.suppliers_view.refresh()

    # -------------------------------------------------------------- карточки
    def open_counterparty_card(self, kind: str, selected_id: str | None) -> None:
        dialog = CounterpartyCardDialog(
            self.store,
            kind,
            selected_id=selected_id,
            on_open_operations=self._go_to_operations,
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.refresh_all()

    def _go_to_operations(self, kind: str, counterparty_id: str) -> None:
        self.show_page(IDX_OPERATIONS)
        if kind == "supplier" and counterparty_id:
            self.operations_view.set_supplier_filter(counterparty_id)

    def refresh_after_week_edit(self) -> None:
        """После ввода суммы в недельной таблице обновить зависимые экраны.

        Саму недельную таблицу не трогаем — она уже показала введённое
        и пересчитала свою строку «ВСЕГО».
        """
        self.operations_view.refresh()
        self.summary_view.refresh()

    def refresh_after_operation_change(self) -> None:
        """После правки операции в журнале обновить недельные таблицы.

        Сам журнал уже перечитал себя перед вызовом этого метода.
        """
        self.suppliers_view.refresh()
        self.customers_view.refresh()
        self.summary_view.refresh()

    def refresh_after_calendar_change(self) -> None:
        """После «Сохранить» в календаре перечитать обе недельные таблицы."""

        self.suppliers_view.refresh()
        self.customers_view.refresh()
        self.summary_view.refresh()

    def refresh_all(self) -> None:
        """Перечитать все экраны после изменения списков контрагентов."""
        self.summary_view.refresh()
        self.suppliers_view.refresh()
        self.customers_view.refresh()
        self.operations_view.refresh_supplier_filter()
        self.operations_view.refresh()
        # DataView от контрагентов не зависит — не трогаем, чтобы не сбрасывать год.
