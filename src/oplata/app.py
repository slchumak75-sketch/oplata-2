"""Главное окно: тёмно-синяя шапка + 4 экрана (QStackedWidget).

Шапка: слева «▦ Оплаты / Строймаркет», далее вкладки
Поступление и оплаты | Поставщики | Покупатели | Данные. С v0.11.0
вкладки «Сводка» нет: панель «Сводка» стоит справа на вкладке
«Поступление и оплаты» и занимает половину окна по ширине. Кнопки
карточек перенесены в панели таблиц (слева от «Очистить суммы»).
Журнал операций открывается окном из карточки контрагента (кнопка
«Операции и долги»).
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
from .access import IDX_CUSTOMERS, Access
from .dialogs.counterparty_card import CounterpartyCardDialog
from .dialogs.delivery_dialog import DeliveryDialog
from .dialogs.operations_journal_dialog import OperationsJournalDialog
from .dialogs.realization_dialog import RealizationDialog
from .storage import (
    BASE_KORELICHI,
    BASE_MIR,
    BASE_TITLES,
    OTHER_BASE,
    DataStore,
)
from .views.data_view import DataView
from .views.receipts_view import ReceiptsView
from .views.summary_view import SummaryView
from .views.week_view import WeekView

NAV_TITLES = (
    "Поступление и оплаты",
    "Поставщики",
    "Покупатели",
    "Конфигурация",  # v0.14.0: прежняя вкладка «Данные»
)
IDX_RECEIPTS, IDX_SUPPLIERS, IDX_CUSTOMERS, IDX_DATA = range(4)

# v0.17.0: цвет шапки показывает, какая база открыта — синяя «МИР»,
# вишнёвая «КОРЕЛИЧИ».
BASE_HEADER_COLORS = {BASE_MIR: styles.NAVY, BASE_KORELICHI: styles.MAROON}


class MainWindow(QMainWindow):
    def __init__(self, store: DataStore, access: Access | None = None) -> None:
        super().__init__()
        self.store = store
        # v0.18.0: права выбранного на этом компьютере пользователя.
        self.access = access or Access()
        # «Конфигурация» открывается по паролю: флаг живёт, пока
        # пользователь не ушёл с этой вкладки.
        self._config_unlocked = False
        # Открытые окна журналов операций держим, чтобы они не закрывались
        # сборщиком мусора (окна немодальные).
        self._journal_dialogs: list[OperationsJournalDialog] = []
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
            on_open_realization=self.open_realization_dialog,
            # v0.13.0: клик по покупателю показывает его журнал операций.
            on_open_operations=lambda cid: self.open_operations_journal(
                "customer", cid
            ),
        )
        # «Сводка» больше не отдельная вкладка: она стоит справа на
        # вкладке «Поступление и оплаты» (запрос пользователя v0.11.0).
        self.receipts_view = ReceiptsView(
            store,
            on_data_changed=self.refresh_after_operation_change,
            summary_widget=self.summary_view,
        )
        self.data_view = DataView(
            store, on_calendar_saved=self.refresh_after_calendar_change
        )

        # «Сводка» пересчитывается сразу после ввода: любое сохранение
        # данных обновляет её (v0.15.0).
        self.store.on_saved.append(self.summary_view.refresh)

        self.stack = QStackedWidget()
        for view in (
            self.receipts_view,
            self.suppliers_view,
            self.customers_view,
            self.data_view,
        ):
            self.stack.addWidget(view)

        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self.stack, stretch=1)
        self.apply_access()
        # Стартовый экран — «Поступление и оплаты» (если права разрешают).
        self.show_page(
            IDX_RECEIPTS if IDX_RECEIPTS in self.access.pages else self.access.pages[0]
        )

    # ------------------------------------------------------------------ шапка
    def _build_header(self) -> QFrame:
        header = QFrame()
        self.header = header
        header.setFixedHeight(70)  # v0.17.0: место под крупную кнопку базы
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)

        logo = QVBoxLayout()
        logo.setSpacing(0)
        logo_title = QLabel("▦  Оплаты")
        logo_title.setStyleSheet(
            f"color:#ffffff;font-size:{styles.fs(19)}px;font-weight:800"
        )
        logo_sub = QLabel("Строймаркет")
        logo_sub.setStyleSheet(
            f"color:#c9d6f2;font-size:{styles.fs(11)}px"
        )
        logo.addWidget(logo_title)
        logo.addWidget(logo_sub)
        layout.addLayout(logo)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, title in enumerate(NAV_TITLES):
            btn = QPushButton(title)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, i=index: self.show_page(i))
            self.nav_group.addButton(btn)
            self.nav_buttons.append(btn)
            layout.addWidget(btn)

        layout.addSpacing(28)
        # v0.17.0: переключатель базы — на свободном поле справа от
        # вкладок. Шрифт вдвое крупнее, чем у вкладок («Конфигурация»);
        # нажатие меняет базу МИР <-> КОРЕЛИЧИ вместе с цветом шапки.
        self.base_button = QPushButton(BASE_TITLES[self.store.base_key])
        self.base_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.base_button.clicked.connect(self.toggle_base)
        layout.addWidget(self.base_button)
        self._apply_base_style()

        layout.addStretch(1)
        # Кнопки «Карточка поставщика»/«Карточка покупателя» с v0.11.0
        # живут в панелях таблиц — там же, где «Очистить суммы».
        return header

    def _apply_base_style(self) -> None:
        """Перекрасить шапку под активную базу (МИР — синяя, КОРЕЛИЧИ — вишнёвая)."""
        header_bg = BASE_HEADER_COLORS[self.store.base_key]
        nav_font = styles.fs(14)
        self.header.setStyleSheet(f"QFrame{{background:{header_bg}}}")
        nav_style = (
            "QPushButton{background:transparent;color:#ffffff;border:none;"
            "border-radius:8px;padding:7px 16px;font-weight:700;"
            f"font-size:{nav_font}px}}"
            "QPushButton:hover{background:rgba(255,255,255,0.14)}"
            f"QPushButton:checked{{background:#ffffff;color:{header_bg}}}"
        )
        for btn in self.nav_buttons:
            btn.setStyleSheet(nav_style)
        self.base_button.setStyleSheet(
            "QPushButton{background:#ffffff;"
            f"color:{header_bg};"
            "border:2px solid #ffffff;border-radius:10px;padding:0px 20px;"
            "font-weight:800;"
            f"font-size:{nav_font * 2}px}}"  # вдвое крупнее вкладок
            "QPushButton:hover{background:#f0f3fa}"
        )

    def toggle_base(self) -> None:
        """Переключить базу: МИР <-> КОРЕЛИЧИ (v0.17.0)."""
        # Журналы операций показывают данные конкретной базы — закрываем,
        # чтобы в окне не остались строки из другой базы.
        for dialog in list(self._journal_dialogs):
            dialog.close()
        self._journal_dialogs.clear()
        self.store.switch_base(OTHER_BASE[self.store.base_key])
        self.base_button.setText(BASE_TITLES[self.store.base_key])
        self._apply_base_style()
        self.refresh_all_views()

    def refresh_all_views(self) -> None:
        """Перерисовать все экраны после смены базы (v0.17.0)."""
        # Состав строк «Поступление»/«Оплата» в каждой базе свой
        # (добавленные «прочие»), поэтому сначала перечитываем строки.
        self.receipts_view.refresh_rows()
        self.receipts_view.refresh()
        self.suppliers_view.refresh()
        self.customers_view.refresh()
        self.summary_view.refresh()
        self.data_view.refresh()

    # -------------------------------------------------------------- навигация
    def show_page(self, index: int) -> None:
        """Показать вкладку с учётом прав и пароля на «Конфигурацию»."""

        if index == IDX_DATA and not self._config_unlocked:
            if not self._ask_config_password():
                return  # пароль не принят — остаёмся на прежней вкладке
        if index != IDX_DATA:
            self._config_unlocked = False  # выход из «Конфигурации» = снова по паролю
        if index not in self.access.pages:
            return
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def _ask_config_password(self) -> bool:
        """Пароль на вкладку «Конфигурация» (v0.18.0).

        Пока пароль не задан, просим придумать его; дальше проверяем
        введённый. Возвращаем True, если вход разрешён.
        """

        from PySide6.QtWidgets import QInputDialog, QLineEdit

        from .access import check_password, has_password, set_password

        if not has_password():
            title = "Первый вход в «Конфигурацию»"
            label = "Придумайте пароль на раздел «Конфигурация»:"
            while True:
                text, ok = QInputDialog.getText(
                    self, title, label, QLineEdit.EchoMode.Password
                )
                if not ok:
                    return False
                if not text:
                    continue
                confirm, ok2 = QInputDialog.getText(
                    self, title, "Повторите пароль:", QLineEdit.EchoMode.Password
                )
                if not ok2:
                    return False
                if confirm != text:
                    continue
                set_password(text)
                self._config_unlocked = True
                return True

        text, ok = QInputDialog.getText(
            self,
            "Конфигурация",
            "Пароль на раздел «Конфигурация»:",
            QLineEdit.EchoMode.Password,
        )
        if ok and text and check_password(text):
            self._config_unlocked = True
            return True
        if ok:
            self._config_unlocked = False
        return False

    def apply_access(self) -> None:
        """Применить права пользователя: вкладки, база, ввод (v0.18.0)."""

        # Видны только разрешённые вкладки.
        for index, btn in enumerate(self.nav_buttons):
            btn.setVisible(index in self.access.pages)
        # Переключатель базы — только тем, кому доступны обе базы.
        self.base_button.setVisible(self.access.can_switch_base)
        # Пользователь «посажен» на свою базу: МИР или КОРЕЛИЧИ.
        fixed = self.access.fixed_base
        if fixed and self.store.base_key != fixed:
            self.store.switch_base(fixed)
            self.base_button.setText(BASE_TITLES[self.store.base_key])
            self._apply_base_style()
            self.refresh_all_views()
        # Ввод данных — только тем, у кого есть право ввода.
        read_only = not self.access.can_edit
        for view in (self.receipts_view, self.suppliers_view, self.customers_view):
            view.set_read_only(read_only)
        self.data_view.set_read_only(read_only, can_admin=self.access.can_admin_data)
        # Текущая вкладка могла стать недоступной — показываем первую из разрешённых.
        if self.stack.currentIndex() not in self.access.pages:
            first = self.access.pages[0]
            self.stack.setCurrentIndex(first)
            self.nav_buttons[first].setChecked(True)

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
            self.receipts_view.refresh()
            self.summary_view.refresh()
            if dialog.saved_due_date is not None:
                self.suppliers_view.goto_date(dialog.saved_due_date)
            else:
                self.suppliers_view.refresh()

    # ------------------------------------------------------------ реализация
    def open_realization_dialog(self, customer_id: str, preset_date=None) -> None:
        """Окно «Реализация» по клику по имени покупателя."""
        dialog = RealizationDialog(
            self.store,
            customer_id,
            preset_date=preset_date,
            on_open_card=lambda: self.open_counterparty_card(
                "customer", customer_id
            ),
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.receipts_view.refresh()
            self.summary_view.refresh()
            if dialog.saved_date is not None:
                self.customers_view.goto_date(dialog.saved_date)
            else:
                self.customers_view.refresh()

    # -------------------------------------------------------------- карточки
    def open_counterparty_card(self, kind: str, selected_id: str | None) -> None:
        dialog = CounterpartyCardDialog(
            self.store,
            kind,
            selected_id=selected_id,
            on_open_operations=self.open_operations_journal,
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.refresh_all()

    def open_operations_journal(self, kind: str, counterparty_id: str) -> None:
        """Окно «Операции контрагента» по кнопке «Операции и долги»."""

        dialog = OperationsJournalDialog(
            self.store,
            kind,
            counterparty_id=counterparty_id,
            on_data_changed=self.refresh_after_operation_change,
            parent=self,
        )
        dialog.show()  # немодально: журнал часто смотрят рядом с таблицами
        self._journal_dialogs.append(dialog)
        dialog.finished.connect(
            lambda *_: self._forget_journal_dialog(dialog)
        )

    def refresh_after_week_edit(self) -> None:
        """После ввода суммы в недельной таблице обновить зависимые экраны.

        Саму недельную таблицу не трогаем — она уже показала введённое
        и пересчитала свою строку «ВСЕГО».
        """
        self.receipts_view.refresh()
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
        self.receipts_view.refresh()

    def _forget_journal_dialog(self, dialog: OperationsJournalDialog) -> None:
        if dialog in self._journal_dialogs:
            self._journal_dialogs.remove(dialog)

    def refresh_all(self) -> None:
        """Перечитать все экраны после изменения списков контрагентов."""
        self.summary_view.refresh()
        self.suppliers_view.refresh()
        self.customers_view.refresh()
        self.receipts_view.refresh_outgoing_rows()
        self.receipts_view.refresh()
        # DataView от контрагентов не зависит — не трогаем, чтобы не сбрасывать год.
