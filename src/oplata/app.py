"""Главное окно: тёмно-синяя шапка + 4 экрана (QStackedWidget).

Шапка: слева «▦ Оплаты / Строймаркет», далее вкладки
Поступление и оплаты | Поставщики | Покупатели | Данные. С v0.11.0
вкладки «Сводка» нет: панель «Сводка» стоит справа на вкладке
«Поступление и оплаты» и занимает половину окна по ширине. Кнопки
карточек перенесены в панели таблиц (рядом с «Задолженность за
неделю»; кнопка «Очистить суммы» убрана в v0.23.1).
Журнал операций открывается окном из карточки контрагента (кнопка
«Операции и долги»).
"""

from __future__ import annotations

import json
from pathlib import Path

from datetime import date

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
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
from .excelio import (
    ExcelError,
    default_export_name,
    describe_bases,
    export_bases,
    import_bases,
)
from .models import AppData
from .storage import (
    BASE_KORELICHI,
    BASE_MIR,
    BASE_TITLES,
    OTHER_BASE,
    DataStore,
    data_path_for,
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
            store,
            lambda sid: self.open_counterparty_card("supplier", sid),
            # v0.22.1: смена даты в «Сводке» переставляет дату блоков
            # «Поступление» и «Оплата» — дата на экране одна.
        )
        self.suppliers_view = WeekView(
            store,
            "supplier",
            lambda sid: self.open_counterparty_card("supplier", sid),
            on_data_changed=self.refresh_after_week_edit,
            on_open_delivery=self.open_delivery_dialog,
            # v0.23.1: клик по поставщику открывает его журнал операций
            # так же, как у покупателя (просьба пользователя); окно
            # «Поставка» осталось на двойном клике.
            on_open_operations=lambda sid: self.open_operations_journal(
                "supplier", sid
            ),
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
            store,
            on_calendar_saved=self.refresh_after_calendar_change,
            # v0.20.0: выбор текущего пользователя в «Конфигурации»
            # применяется сразу, без перезапуска.
            on_users_saved=self.apply_current_user,
        )

        # v0.21.0: столбец «на сегодня» считается по настоящей дате,
        # поэтому при наступлении следующего дня экраны пересчитываем
        # сами — программа может стоять открытой сутками.
        self._today = date.today()
        self._day_timer = QTimer(self)
        self._day_timer.setInterval(60_000)  # проверка раз в минуту
        self._day_timer.timeout.connect(self._on_day_timer)
        self._day_timer.start()

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

        layout.addSpacing(10)
        # v0.20.0: выгрузка и загрузка **обеих** баз (МИР и КОРЕЛИЧИ)
        # одним файлом Excel. Кнопки видит только администратор — у
        # остальных пользователей их нет в шапке.
        self.export_button = QPushButton("Выгрузить базы")
        self.export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_button.setToolTip(
            "Сохранить обе базы (МИР и КОРЕЛИЧИ) в файл Excel"
        )
        self.export_button.clicked.connect(self.export_bases_to_excel)
        layout.addWidget(self.export_button)

        self.import_button = QPushButton("Загрузить базы")
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.setToolTip(
            "Заменить данные баз (МИР и КОРЕЛИЧИ) данными из файла Excel"
        )
        self.import_button.clicked.connect(self.import_bases_from_excel)
        layout.addWidget(self.import_button)

        self._apply_base_style()

        layout.addStretch(1)
        # Кнопки «Карточка поставщика»/«Карточка покупателя» с v0.11.0
        # живут в панелях таблиц — там, где была «Очистить суммы»
        # (кнопка убрана в v0.23.1).
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
        # Кнопки базы (v0.20.0): рамка, а не заливка, — чтобы не путать
        # их с вкладками и с переключателем «МИР/КОРЕЛИЧИ».
        base_tools_style = (
            "QPushButton{background:rgba(255,255,255,0.16);color:#ffffff;"
            "border:1px solid rgba(255,255,255,0.85);border-radius:8px;"
            f"padding:6px 14px;font-weight:700;font-size:{styles.fs(11)}px}}"
            "QPushButton:hover{background:rgba(255,255,255,0.32)}"
        )
        for btn in (self.export_button, self.import_button):
            btn.setStyleSheet(base_tools_style)
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

    def _read_base(self, base_key: str) -> AppData:
        """Прочитать базу с диска (без переключения активной базы)."""

        path = data_path_for(base_key)
        if not path.exists():
            return AppData()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return AppData()
        try:
            return AppData.from_dict(raw)
        except (TypeError, ValueError):
            return AppData()

    def _write_base(self, base_key: str, data: AppData) -> None:
        """Записать базу на диск атомарно (как сохраняет DataStore)."""

        holder = DataStore(path=data_path_for(base_key), base_key=base_key)
        holder.data = data
        holder.save()

    def export_bases_to_excel(self) -> None:
        """Кнопка «Выгрузить базы»: обе базы в один файл Excel (v0.20.0)."""

        bases = {self.store.base_key: self.store.data}
        other = OTHER_BASE[self.store.base_key]
        bases[other] = self._read_base(other)

        default_path = str(Path.home() / default_export_name())
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Выгрузить базы в Excel",
            default_path,
            "Книга Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            export_bases(bases, Path(path))
        except ExcelError as exc:
            QMessageBox.warning(self, "Выгрузка баз", str(exc))
            return
        QMessageBox.information(
            self,
            "Выгрузка баз",
            "Базы МИР и КОРЕЛИЧИ выгружены в файл:\n" + path,
        )

    def import_bases_from_excel(self) -> None:
        """Кнопка «Загрузить базы»: обе базы из файла Excel (v0.20.0).

        Файл заменяет данные целиком, поэтому перед заменой спрашиваем
        подтверждение: отменить загрузку будет нечем.
        """

        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Загрузить базы из Excel",
            str(Path.home()),
            "Книга Excel (*.xlsx)",
        )
        if not path:
            return
        try:
            bases = import_bases(Path(path))
        except ExcelError as exc:
            QMessageBox.warning(self, "Загрузка баз", str(exc))
            return

        names = describe_bases(bases)
        answer = QMessageBox.question(
            self,
            "Загрузка баз",
            f"Из файла будут загружены базы: {names}.\n"
            "Нынешние данные этих баз будут заменены. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # Журналы операций показывают данные конкретной базы — закрываем,
        # чтобы в них не остались строки из прежней загрузки.
        for dialog in list(self._journal_dialogs):
            dialog.close()
        self._journal_dialogs.clear()

        try:
            for base_key, data in bases.items():
                self._write_base(base_key, data)
        except OSError as exc:
            QMessageBox.warning(
                self, "Загрузка баз", f"Не удалось сохранить данные: {exc}"
            )
            return

        # Активную базу подменяем «на месте»: объект store.data остаётся
        # тем же, чтобы экраны со старой ссылкой не показывали пустоту.
        loaded = bases.get(self.store.base_key)
        if loaded is not None:
            current = self.store.data
            current.suppliers = loaded.suppliers
            current.customers = loaded.customers
            current.operations = loaded.operations
            current.cash_receipts = loaded.cash_receipts
            current.other_payments = loaded.other_payments
            current.day_overrides = loaded.day_overrides
            current.year_start = loaded.year_start
        self.refresh_all_views()
        QMessageBox.information(
            self, "Загрузка баз", f"Базы {names} загружены из файла:\n" + path
        )

    def apply_current_user(self, user) -> None:
        """Сменить текущего пользователя и сразу применить его права.

        Вызывается из «Конфигурации» после сохранения списка
        пользователей (v0.20.0): перезапуск не нужен.
        """

        from .access import Access

        self.access = Access(user)
        self.setWindowTitle(
            f"{__app_name__}  v{__version__} · {self.access.title}"
        )
        self.apply_access()

    def _on_day_timer(self) -> None:
        """Наступил новый день — пересчитать всё, что считается «на сегодня».

        v0.21.0: столбец «на сегодня» показывает только оплаты со сроком
        сегодня, поэтому со сменой календарной даты он должен меняться
        сам, без перезапуска программы (v0.21.0, запрос пользователя).
        """

        today = date.today()
        if today == self._today:
            return
        self._today = today
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
        # v0.23.1: на любой вкладке дата начинается с сегодняшнего дня —
        # иначе после перехода на другую неделю и обратно в окошке
        # оставался прежний день (просьба пользователя).
        self._reset_page_date(index)

    def _reset_page_date(self, index: int) -> None:
        """Показать сегодняшний день на вкладке, на которую перешли."""

        today = date.today()
        if index == IDX_RECEIPTS:
            # v0.27.6: даты независимы, поэтому при входе на вкладку
            # каждую плашку ставим на сегодня отдельно.
            self.receipts_view.set_date(today)
            self.summary_view.set_date(today)
        elif index == IDX_SUPPLIERS:
            self.suppliers_view.reset_to_today()
        elif index == IDX_CUSTOMERS:
            self.customers_view.reset_to_today()
        # v0.26.0: плашка «Данные» (перенос остатков) удалена — на
        # вкладке «Конфигурация» раскрывать больше нечего.

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
        # Выгрузка и загрузка баз — только администратору (v0.20.0).
        self.export_button.setVisible(self.access.can_admin_data)
        self.import_button.setVisible(self.access.can_admin_data)
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

        # v0.27.3: открытые раньше журналы сворачиваем — иначе они
        # перекрывают друг друга и не видно, чей журнал открыт последним.
        for previous in self._journal_dialogs:
            if previous.isVisible():
                previous.showMinimized()
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
