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

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import (
    __app_name__,
    __version__,
    column_widths,
    local_settings,
    styles,
)
from .access import (
    IDX_CUSTOMERS,
    IDX_DATA,
    IDX_KKM,
    IDX_RECEIPTS,
    IDX_SUPPLIERS,
    Access,
)
from .dialogs.counterparty_card import CounterpartyCardDialog
from .dialogs.delivery_dialog import DeliveryDialog
from .dialogs.operations_journal_dialog import OperationsJournalDialog
from .dialogs.period_dialog import PeriodDialog
from .dialogs.realization_dialog import RealizationDialog
from .dialogs.statement_dialog import StatementDialog
from .excelio import (
    ExcelError,
    describe_bases,
    export_bases,
    import_bases,
)
from .models import AppData, keep_payment_kinds, set_year_start_funds
from .statement import StatementError, parse_statement
from .statement_match import account_base_key
from .storage import (
    BASE_KORELICHI,
    BASE_MIR,
    BASE_TITLES,
    LOCAL_BASE_DIR_NAME,
    OTHER_BASE,
    DataStore,
    data_path_for,
    latest_local_base_file,
    local_base_dir,
    local_base_file,
    make_backup,
    set_extra_backup_dir,
)
from .views.data_view import DataView
from .views.kkm_view import KkmView
from .views.receipts_view import ReceiptsView
from .views.summary_view import SummaryView
from .views.week_view import WeekView

# Порядок должен совпадать с access.PAGE_TITLES (там же индексы
# IDX_*): в «Конфигурации» по ним ставятся галочки прав.
NAV_TITLES = (
    "Поступление и оплаты",
    "Отчеты ККМ",  # v0.42.0: реестр отчётов кассы за месяц
    "Поставщики",
    "Покупатели",
    "Конфигурация",  # v0.14.0: прежняя вкладка «Данные»
)

# v0.17.0: цвет шапки показывает, какая база открыта. v0.39.0:
# «МИР» — зелёная, «КОРЕЛИЧИ» — вишнёвая (в v0.38.0 были песочная и
# карамельная, до неё — тёмно-синяя и вишнёвая).
BASE_HEADER_COLORS = {
    BASE_MIR: styles.BASE_GREEN,
    BASE_KORELICHI: styles.BASE_MAROON,
}


# v0.87.0: через сколько бездействия закрывается «Конфигурация»
# (просьба пользователя: «5 минут бездействия»).
CONFIG_IDLE_MS = 5 * 60 * 1000

# v0.87.0: как часто смотреть папку обновлений на Диске (час).
UPDATE_CHECK_MS = 60 * 60 * 1000

# v0.47.0: окно открывается в **половину экрана по горизонтали**
# (просьба пользователя): доступная область экрана делится линией по
# горизонтали, и программа занимает **всю ширину** экрана и половину
# его высоты. На экране 1920×1080 это 1920 на ~520.
# v0.49.0: половина — **нижняя** (просьба пользователя): окно стоит у
# нижнего края экрана, а не у верхнего.
# Размер не фиксированный: считается от экрана при каждом запуске.
MIN_WINDOW_WIDTH = 1100
MIN_WINDOW_HEIGHT = 400


def half_screen_geometry(free: QRect | None = None) -> QRect:
    """Прямоугольник окна: **вся ширина экрана и половина высоты**.

    ``free`` — доступная область экрана (без панели задач); её удобно
    передать в проверках, где свой размер экрана. Окно занимает
    **нижнюю** половину доступной области (v0.49.0, просьба
    пользователя): низ стоит у нижнего края, там же, где панель задач.
    """

    if free is None:
        screen = QGuiApplication.primaryScreen()
        free = (
            screen.availableGeometry()
            if screen is not None
            else QRect(0, 0, 1600, 860)
        )
    width = max(free.width(), MIN_WINDOW_WIDTH)
    height = max(free.height() // 2, MIN_WINDOW_HEIGHT)
    # v0.49.0: нижняя половина — окно прижато к нижнему краю доступной
    # области. На очень низком экране (половина меньше MIN_WINDOW_HEIGHT)
    # окно не выходит за верхний край: top не уходит выше free.top().
    top = max(free.top(), free.bottom() + 1 - height)
    return QRect(free.left(), top, width, height)


def backup_text(backups: list[Path]) -> str:
    """Строчка про снятые копии для итогового сообщения (v0.79.0).

    Копий бывает две (рядом с базой и на компьютере), поэтому путь не
    один; пустой список — строчки нет вовсе.
    """

    if not backups:
        return ""
    lead = "Копия прежней базы" if len(backups) == 1 else "Копии прежней базы"
    return f"\n\n{lead}:\n" + "\n".join(str(path) for path in backups)


class MainWindow(QMainWindow):
    def __init__(self, store: DataStore, access: Access | None = None) -> None:
        super().__init__()
        self.store = store
        # v0.18.0: права выбранного на этом компьютере пользователя.
        self.access = access or Access()
        # «Конфигурация» открывается по паролю: флаг живёт, пока
        # пользователь не ушёл с этой вкладки.
        self._config_unlocked = False
        # v0.40.0: путь последней удачной выгрузки — по нему «Очистить
        # базу» понимает, что копия действительно сохранена.
        self._last_export_path: str | None = None
        # Открытые окна журналов операций держим, чтобы они не закрывались
        # сборщиком мусора (окна немодальные).
        self._journal_dialogs: list[OperationsJournalDialog] = []
        # v0.30.0: при выходе обе базы сами выгружаются в «Оплата база».
        # Флаг выключает автовыгрузку в smoke-прогоне собранного exe
        # (--self-test), а ``_exit_export_done`` не даёт выгрузить дважды:
        # и по закрытию окна, и по aboutToQuit.
        self.autosave_on_exit = True
        self._exit_export_done = False
        self.setWindowTitle(f"{__app_name__}  v{__version__}")
        # v0.47.0: окно занимает **половину экрана по горизонтали** —
        # всю ширину экрана и половину высоты (``half_screen_geometry``).
        # Раньше размер был фиксированным: 1600×860 (v0.40.0: 1440 ->
        # 1600 — в шапке три кнопки базы вместо двух, им нужно место).
        # Высоту окна разрешаем доводить до 400 точек: в половине
        # экрана она ~520, и прежний минимум 640 мешал её поставить.
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setGeometry(half_screen_geometry())

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
        # v0.42.0: реестр «Отчеты ККМ» — своя таблица на месяц.
        self.kkm_view = KkmView(store)
        self.data_view = DataView(
            store,
            on_calendar_saved=self.refresh_after_calendar_change,
            # v0.20.0: выбор текущего пользователя в «Конфигурации»
            # применяется сразу, без перезапуска.
            on_users_saved=self.apply_current_user,
            # v0.75.0: ширины колонок, настроенные мышью.
            on_save_fields=self._save_column_widths,
            on_reset_fields=self._reset_column_widths,
            on_edit_fields=self._edit_column_widths,
            # v0.87.0: окно обновления открывается и из «Конфигурации»
            # (там же — кнопки раздачи, только на тестовом компьютере).
            on_open_updates=self.open_update_dialog,
            on_test_computer_changed=self._on_test_computer_changed,
        )
        # v0.79.0: второй каталог копий — на самом компьютере (Загрузки).
        # Копия рядом с базой уезжает в облако вместе с ней: испортится
        # общая папка — пропадут обе, а локальная останется. Путь ищется
        # средствами Qt, поэтому его даёт экран «Конфигурация».
        set_extra_backup_dir(self.data_view.backup_dir())

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
            self.kkm_view,
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

        # v0.28.0: данные лежат в общей папке (ADR-074). Этот компьютер
        # может быть только просмотром, поэтому изменения, внесённые
        # на компьютере администратора, подтягиваются без перезапуска.
        self.sync_interval_ms = 10_000
        # v0.87.0: подпись — по всем файлам папки базы, а не по одному.
        self._data_signature = self._shared_signature()
        self.store.on_saved.append(self._remember_signature)
        # v0.87.0: проверка обновлений на Диске — раз в час, тихо.
        self._update_hint_shown = False
        self._start_update_timer()
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(self.sync_interval_ms)
        self._sync_timer.timeout.connect(self.pull_external_changes)
        self._sync_timer.start()

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
            f"color:{styles.HEADER_TEXT};font-size:{styles.fs(19)}px;"""
            f"font-weight:800"
        )
        logo_sub = QLabel("Строймаркет")
        logo_sub.setStyleSheet(
            f"color:{styles.HEADER_TEXT_MUTED};font-size:{styles.fs(11)}px"
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
            # v0.75.0: вкладка «Конфигурация» переехала в меню «+».
            # Кнопку всё равно создаём (по номеру страницы её ищет
            # apply_access и подсветка текущей вкладки), но в шапку не
            # кладём и держим скрытой.
            if index == IDX_DATA:
                btn.setVisible(False)
            else:
                layout.addWidget(btn)

        # v0.40.0: в шапке три кнопки базы вместо двух — отступы справа
        # от вкладок теснее, иначе длинные подписи обрезались бы.
        layout.addSpacing(14)
        # v0.17.0: переключатель базы — на свободном поле справа от
        # вкладок. Шрифт вдвое крупнее, чем у вкладок («Конфигурация»);
        # нажатие меняет базу МИР <-> КОРЕЛИЧИ вместе с цветом шапки.
        self.base_button = QPushButton(BASE_TITLES[self.store.base_key])
        self.base_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.base_button.clicked.connect(self.toggle_base)
        layout.addWidget(self.base_button)

        layout.addStretch(1)
        # v0.75.0: кнопки действий уходят в правый угол шапки, а не
        # стоят сразу за вкладками: так они не спорят с длинными
        # подписями вкладок за место.
        # v0.54.0: «Загрузить выписку» — прочитать PDF-выписку банка и
        # показать её таблицей (первый этап; разнесение — второй).
        self.statement_button = QPushButton("Загрузить выписку")
        self.statement_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.statement_button.setToolTip(
            "Прочитать выписку банка из PDF и показать операции таблицей"
        )
        self.statement_button.clicked.connect(self._on_load_statement_clicked)
        layout.addWidget(self.statement_button)

        layout.addSpacing(6)
        # v0.87.0: «Обновить» — видна всем и без пароля: установить
        # обновление на свой компьютер может любой сотрудник (просьба
        # пользователя: «кнопку обновить надо на верхней плашке без
        # пароля чтобы все могли самостоятельно обновляться»). Кнопок
        # раздачи здесь нет — они только в «Конфигурации» за паролем.
        self.update_button = QPushButton("Обновить")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.setToolTip(
            "Обновить программу файлом обновления (данные не меняются)"
        )
        self.update_button.clicked.connect(self.open_update_dialog)
        layout.addWidget(self.update_button)

        layout.addSpacing(6)
        # v0.75.0: меню со всеми действиями над базой. Раньше это были
        # три отдельные кнопки («Выгрузить базы», «Загрузить базы»,
        # «Очистить базу»); в меню они занимают одну строку.
        # v0.81.0: на кнопке нет ничего — ни подписи «База», ни птички
        # «вниз» (просьба пользователя). Подпись дублировала
        # переключатель базы («МИР»/«КОРЕЛИЧИ»), стоящий в той же шапке,
        # и занимала место. Что за кнопка, говорит подсказка. Ширину
        # задаём прямо: без подписи кнопка сжалась бы до узкой полоски,
        # в которую не попасть мышью.
        self.base_menu_button = QPushButton("")
        self.base_menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.base_menu_button.setToolTip("Действия с базой данных")
        self.base_menu_button.setFixedWidth(38)
        base_menu = QMenu(self.base_menu_button)
        # v0.76.0: «Конфигурация» лежит в этом же меню (в v0.75.0 для
        # неё была отдельная кнопка «+» — две кнопки с меню в углу
        # шапки путали). Значит, меню видно **всем**, а не только
        # администратору, как было в v0.75.0: иначе не-администратор
        # потерял бы вход в «Конфигурацию», открывающийся по паролю
        # (правило v0.50.0). Пункты самой базы прячем по правам.
        self._admin_base_actions = [
            base_menu.addAction("Выгрузить базу", self._on_export_clicked),
            base_menu.addAction("Загрузить базу", self._on_import_clicked),
        ]
        base_menu.addSeparator()
        self._admin_base_actions += [
            base_menu.addAction("Очистить базу", self._on_clear_base_clicked),
            base_menu.addAction("Очистить таблицы…", self._on_clear_tables_clicked),
        ]
        base_menu.addSeparator()
        base_menu.addAction("Конфигурация", lambda: self.show_page(IDX_DATA))
        self.base_menu_button.setMenu(base_menu)
        layout.addWidget(self.base_menu_button)

        self._apply_base_style()
        # Кнопки «Карточка поставщика»/«Карточка покупателя» с v0.11.0
        # живут в панелях таблиц — там, где была «Очистить суммы»
        # (кнопка убрана в v0.23.1).
        return header

    def _apply_base_style(self) -> None:
        """Перекрасить шапку под активную базу (v0.39.0: зелёная/вишнёвая)."""
        header_bg = BASE_HEADER_COLORS[self.store.base_key]
        nav_font = styles.fs(14)
        self.header.setStyleSheet(f"QFrame{{background:{header_bg}}}")
        # v0.39.0: шапка снова тёмная, поэтому на ней светлый текст,
        # а выбранная вкладка — светлая «пилюля» с тёмным текстом.
        nav_style = (
            "QPushButton{background:transparent;"
            f"color:{styles.HEADER_TEXT};border:none;"
            "border-radius:8px;padding:7px 10px;font-weight:700;"
            f"font-size:{nav_font}px}}"
            f"QPushButton:hover{{background:{styles.HEADER_HOVER}}}"
            f"QPushButton:checked{{background:{styles.HEADER_CHECKED_BG};"
            f"color:{header_bg}}}"
        )
        for btn in self.nav_buttons:
            btn.setStyleSheet(nav_style)
        # Кнопки базы (v0.20.0): рамка, а не заливка, — чтобы не путать
        # их с вкладками и с переключателем «МИР/КОРЕЛИЧИ».
        # v0.40.0: шрифт кнопок — такой же, как у вкладок («Поставщики»,
        # «Покупатели»); раньше он был мельче (fs(11)).
        base_tools_style = (
            f"QPushButton{{background:{styles.HEADER_TOOL_BG};"
            f"color:{styles.HEADER_TEXT};"
            f"border:1px solid {styles.HEADER_TOOL_BORDER};"
            "border-radius:8px;"
            f"padding:6px 6px;font-weight:700;font-size:{nav_font}px}}"
            f"QPushButton:hover{{background:{styles.HEADER_TOOL_HOVER}}}"
        )
        # v0.75.0: в правом углу шапки тот же стиль получают
        # «Загрузить выписку» и меню «База».
        for btn in (
            self.statement_button,
            self.base_menu_button,
            self.update_button,
        ):
            btn.setStyleSheet(base_tools_style)
        self.base_button.setStyleSheet(
            f"QPushButton{{background:{styles.HEADER_CHECKED_BG};"
            f"color:{header_bg};"
            f"border:2px solid {styles.HEADER_CHECKED_BG};border-radius:10px;"
            "padding:0px 10px;"
            "font-weight:800;"
            f"font-size:{nav_font * 2}px}}"  # вдвое крупнее вкладок
            f"QPushButton:hover{{background:{styles.HEADER_HOVER}}}"
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

    # ------------------------------------------- кнопки шапки (v0.40.0)
    def _on_export_clicked(self) -> None:
        """«Выгрузить базы» по паролю (v0.40.0)."""

        if not self._ask_password("Выгрузка баз", "Пароль администратора:"):
            return
        self.export_bases_to_excel()

    def _on_import_clicked(self) -> None:
        """«Загрузить базы» по паролю (v0.40.0)."""

        if not self._ask_password("Загрузка баз", "Пароль администратора:"):
            return
        self.import_bases_from_excel()

    def _reload_current_base(self) -> None:
        """Перечитать файл активной базы и перерисовать экраны (v0.79.0)."""

        self.store.data = self.store._load()  # noqa: SLF001 - тот же путь
        self.refresh_all_views()

    def _apply_base_data(self, loaded: AppData) -> None:
        """Перенести загруженные данные в активную базу «на месте» (v0.79.0).

        Объект ``store.data`` остаётся тем же, чтобы экраны со старой
        ссылкой не показывали пустоту.
        """

        current = self.store.data
        for name in (
            "suppliers",
            "customers",
            "operations",
            "cash_receipts",
            "other_payments",
            "day_overrides",
            "kkm_reports",
            "year_start",
            "bank",
            "imported_statement_rows",
            "imported_statement_writes",
            "routing_rules",
        ):
            setattr(current, name, getattr(loaded, name))

    def _on_load_statement_clicked(self) -> None:
        """«Загрузить выписку»: PDF-файл -> таблица и разнесение (v0.56.0).

        По «Наименованию счета» из шапки узнаём, чья это выписка
        («ООО МирСтройМаркет» — МИР, «ООО Мирсититорг» — КОРЕЛИЧИ).
        Если открыта другая база — предупреждаем и предлагаем
        переключиться: разносить выписку МИР в базу КОРЕЛИЧИ нельзя.
        """

        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Выписка из банка (PDF)",
            "",
            "Выписка PDF (*.pdf)",
        )
        if not path:
            return
        try:
            statement = parse_statement(Path(path))
        except StatementError as exc:
            QMessageBox.warning(self, "Загрузка выписки", str(exc))
            return
        if not self._check_statement_base(statement):
            return
        dialog = StatementDialog(
            statement,
            Path(path).name,
            self,
            bank_unp=self.store.data.bank.unp,
            store=self.store,
            on_imported=self._after_statement_import,
        )
        dialog.exec()

    def _check_statement_base(self, statement) -> bool:
        """Выписка этой базы? Нет — предложить переключить (v0.56.0)."""

        wanted = account_base_key(statement.account_name)
        if wanted is None or wanted == self.store.base_key:
            return True
        answer = QMessageBox.question(
            self,
            "Загрузка выписки",
            f"Выписка счёта «{statement.account_name}» относится к базе "
            f"«{BASE_TITLES[wanted]}», а открыта «"
            f"{BASE_TITLES[self.store.base_key]}».\n\n"
            "Переключить базу и загрузить выписку в неё?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.toggle_base()
        return self.store.base_key == wanted

    def _after_statement_import(self, result) -> None:
        """После разнесения: сохранить и обновить таблицы (v0.56.0)."""

        self.store.save()
        self.refresh_all_views()
        if result.already:
            QMessageBox.information(
                self,
                "Выписка",
                "Уже разносили раньше, пропущено строк: "
                f"{len(result.already)}.",
            )

    def _on_clear_tables_clicked(self) -> None:
        """«Очистить таблицы»: убрать цифры за выбранный период (v0.75.0).

        От «Очистить базу» отличается тем, что стирает не всё, а только
        записи в выбранных границах: операции недельных таблиц,
        поступления кассы и суммы прочих видов оплат. Справочники,
        рабочий календарь, пользователи и долги переноса на 1 января не
        трогаются — это не dated данные.
        """

        if not self._ask_password("Очистка таблиц", "Пароль администратора:"):
            return
        data = self.store.data
        if not (data.operations or data.cash_receipts or data.other_payments):
            QMessageBox.information(
                self,
                "Очистка таблиц",
                f"В базе «{BASE_TITLES[self.store.base_key]}» нет ни одной "
                "суммы — очищать нечего.",
            )
            return

        dialog = PeriodDialog(
            self,
            title="Очистить таблицы",
            hint=(
                f"Суммы базы «{BASE_TITLES[self.store.base_key]}» за "
                "выбранные даты будут удалены. Обе даты включительно. "
                "Поставщики, покупатели, календарь и права остаются."
            ),
        )
        if not dialog.exec():
            return
        day_from, day_to = dialog.period()
        counts = self._count_in_period(day_from, day_to)
        total = sum(counts.values())
        if total == 0:
            QMessageBox.information(
                self,
                "Очистка таблиц",
                f"За период с {day_from:%d.%m.%Y} по {day_to:%d.%m.%Y} "
                "сумм нет — очищать нечего.",
            )
            return

        if not self._confirm_yes_no(
            "Очистка таблиц",
            f"Удалить суммы с {day_from:%d.%m.%Y} по {day_to:%d.%m.%Y}?",
            "Будет удалено: операций — "
            f"{counts['operations']}, поступлений кассы — "
            f"{counts['cash']}, сумм прочих видов оплат — "
            f"{counts['other']}.\n\n"
            "Виды оплат остаются: у них обнуляются только суммы "
            "— примечание, ставка НДС и УНП вида не пропадут. "
            "Отменить удаление будет нельзя, но перед очисткой "
            "программа сама снимет копию файла базы.",
        ):
            return
        backups = make_backup(self.store.base_key, "перед очисткой")
        self._clear_period(day_from, day_to)
        QMessageBox.information(
            self,
            "Очистка таблиц",
            f"Удалено за период с {day_from:%d.%m.%Y} по "
            f"{day_to:%d.%m.%Y}: операций — {counts['operations']}, "
            f"поступлений кассы — {counts['cash']}, сумм прочих видов "
            f"оплат — {counts['other']}. Виды оплат остались."
            + backup_text(backups),
        )

    def _confirm_yes_no(self, title: str, question: str, details: str) -> bool:
        """Подтверждение «Да / Нет» (v0.75.0) для необратимых действий."""

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(question)
        box.setInformativeText(details)
        yes = box.addButton("Да", QMessageBox.ButtonRole.YesRole)
        box.addButton("Нет", QMessageBox.ButtonRole.NoRole)
        box.setDefaultButton(yes)
        box.exec()
        return box.clickedButton() is yes

    def _count_in_period(self, day_from, day_to) -> dict[str, int]:
        """Сколько записей попало в период (для подтверждения)."""

        data = self.store.data
        return {
            "operations": sum(
                1 for item in data.operations if self._in_period(item.date, day_from, day_to)
            ),
            "cash": sum(
                1 for item in data.cash_receipts
                if self._in_period(item.date, day_from, day_to)
            ),
            "other": sum(
                1 for item in data.other_payments
                if self._in_period(item.date, day_from, day_to)
            ),
        }

    @staticmethod
    def _in_period(iso_day: str, day_from, day_to) -> bool:
        """Дата записи (ISO-строка) внутри периода включительно."""

        if not iso_day or len(iso_day) < 10:
            return False
        return day_from.isoformat() <= iso_day[:10] <= day_to.isoformat()

    def _clear_period(self, day_from, day_to) -> None:
        """Удалить суммы за период (v0.75.0)."""

        data = self.store.data
        data.operations = [
            item for item in data.operations
            if not self._in_period(item.date, day_from, day_to)
        ]
        data.cash_receipts = [
            item for item in data.cash_receipts
            if not self._in_period(item.date, day_from, day_to)
        ]
        # v0.79.0: запись вида оплаты не удаляем — обнуляем сумму.
        # Запись — это и карточка вида (ставка НДС, УНП, примечание,
        # отмеченные даты): удали все записи вида за период, и
        # настройки, набранные руками, пропадут (что и случилось,
        # когда период накрывал все записи вида).
        for item in data.other_payments:
            if self._in_period(item.date, day_from, day_to):
                item.amount = "0"
        self.store.save()
        self.refresh_all_views()

    # -------------------------------------------------- ширины колонок v0.75.0
    def _resizable_views(self) -> list:
        """Таблицы, ширины которых настраивает пользователь."""

        views = [self.suppliers_view, self.customers_view]
        receipts = getattr(self, "receipts_view", None)
        if receipts is not None:
            # Блоки «Поступление» и «Оплата».
            for name in ("incoming", "outgoing"):
                block = getattr(receipts, name, None)
                if block is not None and hasattr(block, "save_widths"):
                    views.append(block)
        # v0.86.0: открытое окно «Отчет» — тоже настраиваемая таблица.
        # Оно немодальное, поэтому в «Конфигурацию» можно уйти, не
        # закрывая его (просьба пользователя: «сделать возможным
        # изменять ширину столбцов с учётом уже существующих
        # возможностей в конфигурации»).
        summary = getattr(self, "summary_view", None)
        report = getattr(summary, "report_dialog", None) if summary else None
        if report is not None and report.isVisible():
            views.append(report)
        return [view for view in views if hasattr(view, "save_widths")]

    def _edit_column_widths(self, enabled: bool) -> str:
        """«Редактировать поля»: разрешить или запретить протяжку (v0.76.0)."""

        for view in self._resizable_views():
            view.set_columns_editable(enabled)
        if not enabled:
            return "Правка полей закрыта: мышь снова только вводит данные."
        return (
            "Правка полей включена: тяните границы столбцов мышью в "
            "«Поставщиках», «Покупателях», «Поступлении», «Оплате» "
            "и в открытом окне «Отчет», затем нажмите «Сохранить поля»."
        )

    def _save_column_widths(self) -> str:
        """«Сохранить поля»: записать ширины колонок в файл настроек."""

        saved = [view for view in self._resizable_views() if view.save_widths()]
        if not saved:
            return "Сохранять нечего: таблицы ещё не открывались."
        # Сохранили — правку закрываем (кнопку снимает DataView).
        for view in self._resizable_views():
            view.set_columns_editable(False)
        return (
            f"Ширины колонок сохранены ({len(saved)} таблиц). Путь: "
            f"{column_widths.path()}"
        )

    def _reset_column_widths(self) -> str:
        """«Сбросить поля»: вернуть расчётные ширины колонок."""

        for view in self._resizable_views():
            view.reset_widths()
        return "Ширины колонок сброшены: как в первый раз."

    def _on_clear_base_clicked(self) -> None:
        """«Очистить базу»: удалить операции и суммы (v0.40.0).

        Справочники (поставщики и покупатели), рабочий календарь,
        пользователи и права **не** трогаются. Перед очисткой
        предлагаем выгрузить базу в Excel: отменить очистку будет
        нечем.
        """

        if not self._ask_password("Очистка базы", "Пароль администратора:"):
            return
        data = self.store.data
        operations = len(data.operations)
        cash = len(data.cash_receipts)
        others = len(data.other_payments)
        debts = len(data.year_start.supplier_debts) + len(
            data.year_start.customer_debts
        )
        if operations + cash + others + debts == 0:
            QMessageBox.information(
                self,
                "Очистка базы",
                f"В базе «{BASE_TITLES[self.store.base_key]}» нет операций "
                "и сумм — очищать нечего.",
            )
            return

        # v0.75.0: подтверждение «Да / Нет», как у остальных пунктов
        # меню «База». Прежний вариант с тремя кнопками ушёл: выгрузка
        # теперь отдельным пунктом меню, а не частью этого вопроса.
        if not self._confirm_yes_no(
            "Очистка базы",
            f"Удалить операции и суммы базы "
            f"«{BASE_TITLES[self.store.base_key]}»?",
            f"Будет удалено: операций — {operations}, "
            f"поступлений кассы — {cash}, "
            f"сумм прочих видов оплат — {others}, "
            f"долгов переноса — {debts}.\n\n"
            "Поставщики, покупатели, виды оплат, рабочий календарь, "
            "пользователи и права остаются; у видов оплат обнуляются "
            "только суммы. Отменить очистку будет нельзя, но перед "
            "очисткой программа сама снимет копию файла базы.",
        ):
            return
        # v0.79.0: копия снимается до очистки — вернуться будет к чему
        # даже если отмена нужна через неделю. Выгрузка в Excel такой
        # страховкой не служит: она не несёт примечания и правила
        # разноса (см. excelio).
        backups = make_backup(self.store.base_key, "перед очисткой")
        self._clear_current_base(backups)

    def _clear_current_base(self, backups: list[Path] | None = None) -> None:
        """Удалить операции и суммы текущей базы (v0.40.0).

        v0.79.0: ``backups`` — пути копий, снятых до очистки: о них
        говорим в итоговом сообщении, чтобы человек знал, где лежит
        прежняя база.
        """

        data = self.store.data
        data.operations.clear()
        data.cash_receipts.clear()
        # v0.78.0: виды оплат не удаляем — это справочник, как
        # поставщики и покупатели. У вида есть карточка (ставка НДС,
        # УНП, примечание, отмеченные даты), и живёт она в первой
        # записи вида: сотри записи — пропадут и настройки. Поэтому
        # оставляем по одной записи на вид, с пустой суммой.
        data.other_payments = keep_payment_kinds(data.other_payments)
        # Перенос на 1 января — это тоже суммы, их убираем; дату
        # остатков и справочники не трогаем.
        data.year_start.supplier_debts.clear()
        data.year_start.customer_debts.clear()
        set_year_start_funds(data, None)
        self.store.save()
        self.refresh_all_views()
        QMessageBox.information(
            self,
            "Очистка базы",
            f"База «{BASE_TITLES[self.store.base_key]}» очищена: "
            "операции, поступления кассы и долги переноса удалены, "
            "суммы прочих видов оплат обнулены. Поставщики, "
            "покупатели и сами виды оплат (ставка НДС, УНП, "
            "примечание) остались."
            + backup_text(backups or []),
        )

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

    def _collect_bases(self) -> dict[str, AppData]:
        """Обе базы (МИР и КОРЕЛИЧИ) для выгрузки (v0.30.0)."""

        bases = {self.store.base_key: self.store.data}
        other = OTHER_BASE[self.store.base_key]
        bases[other] = self._read_base(other)
        return bases

    def export_bases_to_excel(self) -> None:
        """Кнопка «Выгрузить базы»: обе базы в один файл Excel (v0.20.0).

        v0.30.0: диалог открывается в папке «Оплата база» рядом с
        программой и подставляет имя «Оплата <сегодня>.xlsx» (ADR-082).
        """

        self._last_export_path = None
        bases = self._collect_bases()

        # Папку создаём заранее: если её не было, диалог всё равно
        # откроется в нужном месте. Не создалась (например, программа в
        # Program Files без прав на запись) — не беда, путь останется
        # прежним, пользователь выберет другой каталог сам.
        try:
            local_base_dir().mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        default_path = str(local_base_file())
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
        self._last_export_path = path
        QMessageBox.information(
            self,
            "Выгрузка баз",
            "Базы МИР и КОРЕЛИЧИ выгружены в файл:\n" + path,
        )

    def autosave_bases_to_excel(self) -> Path | None:
        """Молча выгрузить обе базы в «Оплата база» (v0.30.0).

        Вызывается при закрытии окна и по ``aboutToQuit``: у пользователя
        всегда лежит свежая копия обеих баз рядом с программой, даже если
        он ни разу не нажимал «Выгрузить базы». Ошибку (например, файл
        уже открыт в Excel) показываем предупреждением, но выход не
        блокируем: закрыть программу важнее.

        Возвращает путь к файлу или ``None``, если выгрузки не было.
        """

        # Один раз за выход: и по закрытию окна, и по aboutToQuit (Qt
        # шлёт оба сигнала), поэтому второй вызов ничего не делает.
        if self._exit_export_done or not self.autosave_on_exit:
            return None
        self._exit_export_done = True

        bases = self._collect_bases()
        path = local_base_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            export_bases(bases, path)
        except (ExcelError, OSError) as exc:
            QMessageBox.warning(
                self,
                "Выгрузка баз",
                "Не удалось сохранить выгрузку в папку "
                f"«{LOCAL_BASE_DIR_NAME}»:\n{exc}\n\n"
                "Программа закрывается. При желании выгрузите базы "
                "кнопкой «Выгрузить базы» вручную.",
            )
            return None
        return path

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Закрытие окна: молча выгружаем обе базы (v0.30.0, ADR-082)."""

        self.autosave_bases_to_excel()
        super().closeEvent(event)

    def _import_start_path(self) -> str:
        """Откуда открывать диалог загрузки выгрузки (v0.30.0).

        Папка «Оплата база» и самый свежий файл в ней. Если ни папки, ни
        файла нет, Qt открыла бы несуществующий каталог — в этом случае
        отдаём домашний каталог, как было раньше.
        """

        candidate = latest_local_base_file() or local_base_dir()
        try:
            exists = Path(candidate).exists()
        except OSError:
            exists = False
        return str(candidate if exists else Path.home())

    def import_bases_from_excel(self) -> None:
        """Кнопка «Загрузить базы»: обе базы из файла Excel (v0.20.0).

        Файл заменяет данные целиком, поэтому перед заменой спрашиваем
        подтверждение: отменить загрузку будет нечем.

        v0.30.0: диалог открывается в папке «Оплата база» и сразу
        подставляет самую свежую выгрузку (ADR-082) — вручную искать
        файл по всему компьютеру больше не нужно.
        """

        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Загрузить базы из Excel",
            self._import_start_path(),
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

        # v0.79.0: загрузка затирает базу целиком — сначала копия.
        # Она и есть настоящая страховка: вернуть примечания и правила
        # разноса из файла выгрузки нельзя, только из такой копии.
        # Путей несколько: копия каждой базы пишется и рядом с
        # базой, и на компьютер.
        backups = [
            *make_backup(BASE_MIR, "перед загрузкой"),
            *make_backup(BASE_KORELICHI, "перед загрузкой"),
        ]

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
            self._apply_base_data(loaded)
        self.refresh_all_views()
        QMessageBox.information(
            self,
            "Загрузка баз",
            f"Базы {names} загружены из файла:\n" + path
            + ("\n\nКопии прежних баз:\n"
               + "\n".join(str(item) for item in backups)
               if backups else ""),
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

    # ------------------------------------------- проверка обновлений (час)
    def _start_update_timer(self) -> None:
        """Раз в час смотреть папку обновлений на Диске (v0.87.0).

        Просьба пользователя: «обновления можно раз в час». Читается
        только маленький манифест из папки — сама установка идёт лишь
        по нажатию «Установить».
        """

        from PySide6.QtCore import QTimer

        self._update_timer = QTimer(self)
        self._update_timer.setInterval(UPDATE_CHECK_MS)
        self._update_timer.timeout.connect(self.check_updates_silently)
        self._update_timer.start()
        QTimer.singleShot(20_000, self.check_updates_silently)

    def check_updates_silently(self) -> None:
        """Проверить «Выпуск» на Диске и подсветить кнопку «Обновить»."""

        from . import updates

        folder = Path(self.store.path).parent
        try:
            info = updates.find_update(
                folder,
                __version__,
                allow_test=local_settings.is_test_computer(),
            )
        except OSError:
            return
        available = info is not None
        if getattr(self, "update_button", None) is not None:
            self.update_button.setToolTip(
                f"Доступно обновление: {updates.describe(info)}"
                if available
                else "Обновить программу файлом обновления (данные не меняются)"
            )
            # Заметная рамка, когда есть что ставить. Само ничего не
            # устанавливается: только с подтверждения человека.
            self.update_button.setStyleSheet(
                "QPushButton{border:2px solid #ffffff;font-weight:800;}"
                if available
                else ""
            )
            if available and not hasattr(self, "_update_hint_shown"):
                self._update_hint_shown = True
                self.statusBar().showMessage(
                    f"Доступно обновление {info.version} — кнопка «Обновить»",
                    15000,
                )

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

    def _remember_signature(self) -> None:
        """Запомнить состояние файлов после своей записи (v0.28.0/v0.87.0)."""

        self._data_signature = self._shared_signature()

    def pull_external_changes(self) -> bool:
        """Подтянуть изменения, внесённые на другом компьютере.

        v0.28.0: данные лежат в общей папке — файл мог обновить
        администратор. Возвращает ``True``, если данные перечитаны.

        v0.87.0: синхронизируется **всё**, что лежит в папке базы, а не
        только активная таблица (просьба пользователя: «Синхронизация
        данных через облако должна быть по всем параметрам ... Абсолютно
        всё»). Раньше, кроме файла активной базы, ничего не проверялось:
        второй компьютер продолжал показывать старый список
        пользователей, старые ширины столбцов и не видел изменений в
        *другой* базе (МИР, пока открыта КОРЕЛИЧИ).
        """

        if not self._shared_data_dir():
            # Локальная база: подтягивать неоткуда, не тратим время.
            return False
        signature = self._shared_signature()
        if signature == self._data_signature:
            return False
        if QApplication.activeModalWidget() is not None:
            return False
        if not self.isActiveWindow():
            return False

        self.store.reload()
        # Пользователи и права — общий users.json (v0.87.0).
        self._reload_users()
        # Ширины столбцов — общий column-widths.json (v0.87.0).
        self._reload_column_widths()
        # Другая база тоже могла измениться: её подхватят при
        # переключении, но «Сводка» и счётчики должны знать свежие
        # данные сразу.
        self._data_signature = self._shared_signature()
        self.refresh_all_views()
        return True

    def _shared_data_dir(self) -> bool:
        """Данные действительно лежат в общей папке (v0.87.0)."""

        from .storage import data_dir, local_data_dir

        try:
            return data_dir().resolve() != local_data_dir().resolve()
        except OSError:
            return False

    def _shared_signature(self) -> tuple:
        """Подпись **всех** файлов папки базы (v0.87.0).

        Берём размер и время каждого файла данных и настроек: по ним
        видно любое изменение с другого компьютера — правку тарифов,
        список пользователей, ширины столбцов, вторую базу.
        """

        from .storage import BASE_FILES, data_dir

        names = list(BASE_FILES.values()) + [
            "users.json",
            "column-widths.json",
            "state.json",
        ]
        marks: list[tuple] = []
        folder = data_dir()
        for name in names:
            try:
                stat = (folder / name).stat()
            except OSError:
                continue
            marks.append((name, stat.st_mtime_ns, stat.st_size))
        return tuple(marks)

    def _reload_users(self) -> None:
        """Перечитать общий список пользователей и права (v0.87.0)."""

        from .access import Access, load_current_user

        current = load_current_user()
        # Текущего пользователя мог удалить администратор на другом
        # компьютере — тогда прав по роли больше нет, берём первого.
        self.access = Access(current)
        self.setWindowTitle(
            f"{__app_name__}  v{__version__} · {self.access.title}"
        )
        self.apply_access()

    def _reload_column_widths(self) -> None:
        """Применить ширины столбцов, сохранённые на другом компьютере."""

        from . import column_widths

        for view in self._resizable_views():
            widths = getattr(view, "_saved_widths", None)
            try:
                stored = column_widths.load(
                    view._widths_key, getattr(view, "_column_count", 0)
                )
            except (AttributeError, TypeError):
                continue
            if stored and stored != widths:
                view._saved_widths = stored
                apply = getattr(view, "_apply_saved_widths", None)
                if callable(apply):
                    apply()

    def refresh_all_views(self) -> None:
        """Перерисовать все экраны после смены базы (v0.17.0)."""
        # Состав строк «Поступление»/«Оплата» в каждой базе свой
        # (добавленные «прочие»), поэтому сначала перечитываем строки.
        self.receipts_view.refresh_rows()
        self.receipts_view.refresh()
        # v0.42.0: у «Отчетов ККМ» своя таблица на месяц; в другой базе
        # на один день приходится другое число строк (3 у МИР, 2 у
        # КОРЕЛИЧИ), поэтому перестраиваем её вместе с остальными.
        self.kkm_view.refresh()
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
        # v0.50.0: «Конфигурация» открывается по паролю любому
        # пользователю, даже если его роль этой вкладки не دارد
        # (просьба пользователя: кнопка видна на плашке всегда).
        if index not in self.access.pages and not (
            index == IDX_DATA and self._config_unlocked
        ):
            return
        # v0.51.0: права внутри «Конфигурации» зависят от пароля: принят
        # — работаем полностью, нет (или ушли с вкладки) — по роли.
        self._apply_config_rights()
        self.stack.setCurrentIndex(index)
        # v0.87.0: «Конфигурация» сама закроется после 5 минут
        # бездействия — пароль не останется висеть открытым.
        if index == IDX_DATA and self._config_unlocked:
            self._start_config_timer()
        elif getattr(self, "_config_timer", None) is not None:
            self._config_timer.stop()
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
        elif index == IDX_KKM:
            # v0.42.0: реестр открывается на текущем месяце, как
            # недельные таблицы — на текущей неделе.
            self.kkm_view.set_month(today)
        elif index == IDX_SUPPLIERS:
            self.suppliers_view.reset_to_today()
        elif index == IDX_CUSTOMERS:
            self.customers_view.reset_to_today()
        # v0.26.0: плашка «Данные» (перенос остатков) удалена — на
        # вкладке «Конфигурация» раскрывать больше нечего.

    def _apply_config_rights(self) -> None:
        """Права на вкладке «Конфигурация» (v0.51.0).

        Пароль на раздел принят — внутри вкладки работаем **с полными
        правами**, независимо от роли. Иначе у пользователя без права
        ввода (Руководитель, Пользователь МИР / КОРЕЛИЧИ) раздел
        открывался, но кнопки «Сохранить пользователей», «Добавить»,
        «Удалить», «Сменить пароль» и «Сохранить» календаря в нём
        пропадали (замечание пользователя).

        Остальные вкладки это не затрагивает: право ввода данных там
        по-прежнему даёт только роль.
        """

        if self._config_unlocked:
            self.data_view.set_read_only(False, can_admin=True)
        else:
            self.data_view.set_read_only(
                not self.access.can_edit,
                can_admin=self.access.can_admin_data,
            )

    # --------------------------------------------------- обновления v0.87.0
    def open_update_dialog(self) -> None:
        """Кнопка «Обновить» и плашка «Обновления» (v0.87.0).

        Окно одно на программу и **немодальное**: пока оно открыто,
        обновление данных с Диска приостановлено, а после установки
        программа закрывается и запускается заново установщиком.
        """

        from .views.update_view import UpdateDialog

        dialog = getattr(self, "update_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return
        self.update_dialog = UpdateDialog(self.store, self.access, self)
        self.update_dialog.show()

    def _on_test_computer_changed(self) -> None:
        """Галочка «Тестовый компьютер» изменилась (v0.87.0)."""

        label = "да" if local_settings.is_test_computer() else "нет"
        self.data_view.update_status_label.setText(
            f"Тестовый компьютер: {label}. "
            + (
                "Кнопки раздачи появятся в окне обновления."
                if local_settings.is_test_computer()
                else "Раздача обновлений с этого компьютера закрыта."
            )
        )

    def _start_config_timer(self) -> None:
        """Таймер автоснятия пароля с «Конфигурации» (v0.87.0).

        Просьба пользователя: «5 минут бездействия». Считается только
        пока открыта «Конфигурация»: ушли на другую вкладку — пароль
        снимается и без таймера (v0.18.0).
        """

        from PySide6.QtCore import QTimer

        if getattr(self, "_config_timer", None) is not None:
            return
        self._config_timer = QTimer(self)
        self._config_timer.setInterval(CONFIG_IDLE_MS)
        self._config_timer.setSingleShot(True)
        self._config_timer.timeout.connect(self._lock_config_by_idle)
        # Любое действие в окне — знак, что человек здесь.
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._config_timer.start()

    def _lock_config_by_idle(self) -> None:
        """Время бездействия вышло: закрыть «Конфигурацию» от чужих глаз."""

        if not self._config_unlocked:
            return
        # Окно ввода не вырываем из рук: дождёмся следующей проверки.
        if self._dialog_is_open():
            self._config_timer.start()
            return
        self._config_unlocked = False
        self._apply_config_rights()
        self.data_view.refresh_update_panel()
        if self.stack.currentIndex() == IDX_DATA:
            self.show_page(self.access.pages[0])
        self.nav_buttons[self.access.pages[0]].setChecked(True)
        self.statusBar().showMessage(
            "«Конфигурация» закрыта: 5 минут бездействия.", 8000
        ) if self.statusBar() is not None else None

    def _dialog_is_open(self) -> bool:
        """Открыт ли сейчас какой-нибудь диалог ввода (v0.87.0)."""

        from PySide6.QtWidgets import QApplication, QDialog

        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible() and widget.isModal():
                return True
        return False

    def eventFilter(self, obj, event):  # noqa: N802 - имя из Qt
        """Любое действие пользователя продлевает открытую «Конфигурацию»."""

        from PySide6.QtCore import QEvent

        if getattr(self, "_config_timer", None) is not None:
            if event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.KeyPress,
                QEvent.Type.Wheel,
                QEvent.Type.MouseMove,
            ):
                if self._config_unlocked:
                    self._config_timer.start()
        return super().eventFilter(obj, event)

    def _ask_config_password(self) -> bool:
        """Пароль на вкладку «Конфигурация» (v0.18.0)."""

        return self._ask_password(
            "Конфигурация",
            "Пароль на раздел «Конфигурация»:",
            unlock_config=True,
        )

    def _ask_password(
        self, title: str, label: str, *, unlock_config: bool = False
    ) -> bool:
        """Пароль администратора (v0.18.0; общий для всех — v0.40.0).

        Пока пароль не задан, просим придумать его; дальше проверяем
        введённый. Возвращаем True, если вход разрешён. Флаг
        ``unlock_config`` открывает вкладку «Конфигурацию» — для
        кнопок шапки он не нужен.
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
                if unlock_config:
                    self._config_unlocked = True
                return True

        text, ok = QInputDialog.getText(
            self,
            title,
            label,
            QLineEdit.EchoMode.Password,
        )
        if ok and text and check_password(text):
            if unlock_config:
                self._config_unlocked = True
            return True
        if ok and unlock_config:
            self._config_unlocked = False
        return False

    def apply_access(self) -> None:
        """Применить права пользователя: вкладки, база, ввод (v0.18.0)."""

        # Видны только разрешённые вкладки.
        # v0.50.0: «Конфигурация» — исключение: кнопка видна **всегда**,
        # независимо от прав (просьба пользователя). Сама вкладка
        # по-прежнему открывается по паролю, поэтому права она не
        # расширяет: что можно править внутри, решают права роли.
        for index, btn in enumerate(self.nav_buttons):
            # v0.75.0: «Конфигурация» больше не вкладка — кнопка в
            # шапке для неё не показывается никогда.
            btn.setVisible(
                index != IDX_DATA and index in self.access.pages
            )
        # Переключатель базы — только тем, кому доступны обе базы.
        self.base_button.setVisible(self.access.can_switch_base)
        # v0.76.0: меню «База» видно всем — в нём же «Конфигурация».
        # Прячем только пункты самой базы: выгрузку и загрузку
        # (v0.20.0) и обе очистки (v0.40.0, v0.75.0).
        for action in self._admin_base_actions:
            action.setVisible(self.access.can_admin_data)
        # Пользователь «посажен» на свою базу: МИР или КОРЕЛИЧИ.
        fixed = self.access.fixed_base
        if fixed and self.store.base_key != fixed:
            self.store.switch_base(fixed)
            self.base_button.setText(BASE_TITLES[self.store.base_key])
            self._apply_base_style()
            self.refresh_all_views()
        # Ввод данных — только тем, у кого есть право ввода.
        read_only = not self.access.can_edit
        for view in (
            self.receipts_view,
            self.kkm_view,
            self.suppliers_view,
            self.customers_view,
        ):
            view.set_read_only(read_only)
        # v0.51.0: внутри «Конфигурации» права даёт пароль на раздел,
        # поэтому права этой вкладки считаем отдельно.
        self._apply_config_rights()
        # Текущая вкладка могла стать недоступной — показываем первую из
        # разрешённых. v0.50.0: с открытой по паролю «Конфигурацией»
        # этого не делаем, иначе смена пользователя выкидывала бы с неё.
        if self.stack.currentIndex() not in self.access.pages and not (
            self.stack.currentIndex() == IDX_DATA and self._config_unlocked
        ):
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
