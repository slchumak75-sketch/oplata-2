"""Экран «Данные» — «Настройка календаря» и «Администрирование» (макет 05).

С v0.9.0 календарь живёт в раскрывающейся плашке «Настройка календаря»
(свёрнута по умолчанию: экран нужен редко, а места занимает много). Рядом
добавлена плашка «Администрирование» — путь к файлу данных, копия данных
и счётчики.

Алгоритм пользователя (v0.6.0):

1. выбрать галочку-режим «Выходные дни» и кликами расставить выходные
   (подсвечиваются красным);
2. выбрать галочку-режим «Рабочие дни» и кликами расставить рабочие
   (подсвечиваются серым) — так отмечаются законодательные переносы;
3. нажать кнопку «Сохранить».

До сохранения клики меняют только черновик внутри этого экрана
(``_pending_overrides``): файл данных и расчёт сроков не трогаются.
После «Сохранить» отметки попадают в ``AppData.day_overrides`` и
используются ``calc.payment_due_date`` для таблиц «Поставщики» и
«Покупатели». Уже сохранённый в операции срок задним числом не
переписывается (ADR-007/009); календарь влияет на новые вводы и на
операции без сохранённого срока.

По умолчанию Сб/Вс — выходные, Пн–Пт — рабочие. Если выбранный режим
совпадает с умолчанием дня, ручная отметка для него не хранится.

Внизу добавлен служебный блок с путём к файлу данных (его не было на
макете — нужен пользователю/агентам, чтобы знать, где лежат данные).
"""

from __future__ import annotations

import calendar as calendar_module
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import styles
from ..dates import MONTHS_RU, WEEKDAYS_RU_TITLE, fmt_money_plain, parse_money
from ..models import (
    set_year_start_customer_debt,
    set_year_start_date,
    set_year_start_funds,
    set_year_start_supplier_debt,
)
from ..storage import DataStore
from ..widgets import (
    AmountLineEdit,
    CollapsiblePanel,
    blue_button,
    make_amount_edit,
    make_date_edit,
)

MONTHS_PER_ROW = 5  # как на макете: Январь–Май в первой строке

# Плашка «Данные»: разовый перенос остатков на 1 января (v0.14.0).
DATE_FORMAT = "%d.%m.%Y"


class _AmountDelegate(QStyledItemDelegate):
    """Редактор суммы в ячейке: без мешающих нулей (AmountLineEdit)."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        return AmountLineEdit(parent=parent)


def _parse_date_text(text: str) -> str:
    """Текст ячейки в ISO-дату; пусто или мусор — пустая строка."""

    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    try:
        return datetime.strptime(cleaned, DATE_FORMAT).date().isoformat()
    except ValueError:
        pass
    try:  # уже ISO
        return date.fromisoformat(cleaned).isoformat()
    except (TypeError, ValueError):
        return ""


def _date_text(day_iso: str) -> str:
    try:
        return date.fromisoformat(day_iso or "").strftime(DATE_FORMAT)
    except (TypeError, ValueError):
        return ""


class DataView(QWidget):
    def __init__(
        self,
        store: DataStore,
        on_calendar_saved: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_calendar_saved = on_calendar_saved
        self.year = date.today().year
        self._day_buttons: dict[date, QPushButton] = {}
        # Черновик отметок: клики по календарю меняют его, а не файл
        # данных. В store он попадёт только по кнопке «Сохранить».
        self._pending_overrides: dict[str, str] = dict(store.data.day_overrides)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # --- раскрывающаяся плашка «Настройка календаря» ---
        self.calendar_panel = CollapsiblePanel("Настройка календаря", expanded=False)
        panel_layout = self.calendar_panel.content_layout

        title = QLabel("📅  Выходные и рабочие дни")
        title.setObjectName("PanelTitle")
        panel_layout.addWidget(title)

        # --- режим разметки + навигация по годам ---
        top = QHBoxLayout()
        self.chk_off = QCheckBox("Выходные дни")
        self.chk_off.setChecked(True)
        self.chk_off.setToolTip("Клик по дню сделает его выходным (красным)")
        self.chk_work = QCheckBox("Рабочие дни")
        self.chk_work.setToolTip("Клик по дню сделает его рабочим (серым)")
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        mode_group.addButton(self.chk_off)
        mode_group.addButton(self.chk_work)
        top.addWidget(self.chk_off)
        top.addWidget(self.chk_work)
        top.addStretch(1)

        prev_year = QPushButton("←")
        prev_year.clicked.connect(lambda: self._shift_year(-1))
        top.addWidget(prev_year)
        self.year_label = QLabel(str(self.year))
        self.year_label.setStyleSheet(f"font-weight:800;font-size:{styles.fs(16)}px")
        top.addWidget(self.year_label)
        next_year = QPushButton("→")
        next_year.clicked.connect(lambda: self._shift_year(1))
        top.addWidget(next_year)
        panel_layout.addLayout(top)

        # --- подсказка, статус черновика и явное сохранение ---
        actions = QHBoxLayout()
        hint = QLabel(
            "Выберите режим и кликайте по дням: выходные — красные, "
            "рабочие — серые. Затем нажмите «Сохранить»."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        actions.addWidget(hint, stretch=1)

        self.status_label = QLabel("Изменений нет")
        self.status_label.setObjectName("Muted")
        actions.addWidget(self.status_label)

        self.cancel_button = QPushButton("Отменить изменения")
        self.cancel_button.clicked.connect(self.discard_changes)
        actions.addWidget(self.cancel_button)

        self.save_button = blue_button("Сохранить")
        self.save_button.clicked.connect(self.save_calendar)
        actions.addWidget(self.save_button)
        panel_layout.addLayout(actions)

        # --- сетка из 12 мини-календарей ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(420)
        panel_layout.addWidget(self.scroll, stretch=1)
        root.addWidget(self.calendar_panel, stretch=1)

        # --- раскрывающаяся плашка «Данные»: перенос остатков на 1 января ---
        self.data_panel = CollapsiblePanel("Данные", expanded=False)
        self._build_year_start_panel(self.data_panel.content_layout)
        root.addWidget(self.data_panel)

        # --- раскрывающаяся плашка «Администрирование» ---
        self.admin_panel = CollapsiblePanel("Администрирование", expanded=False)
        admin_layout = self.admin_panel.content_layout

        self.path_label = QLabel(str(self.store.path))
        self.path_label.setObjectName("Muted")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        admin_layout.addWidget(self.path_label)

        admin_buttons = QHBoxLayout()
        admin_buttons.setSpacing(8)
        self.open_folder_button = QPushButton("Открыть папку данных")
        self.open_folder_button.clicked.connect(self._open_data_folder)
        admin_buttons.addWidget(self.open_folder_button)
        self.backup_button = QPushButton("Создать резервную копию")
        self.backup_button.clicked.connect(self._make_backup)
        admin_buttons.addWidget(self.backup_button)
        self.reload_button = QPushButton("Перечитать файл")
        self.reload_button.setToolTip(
            "Заново прочитать data.json; несохранённые правки пропадут."
        )
        self.reload_button.clicked.connect(self._reload_file)
        admin_buttons.addWidget(self.reload_button)
        admin_buttons.addStretch(1)
        admin_layout.addLayout(admin_buttons)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("Muted")
        self.stats_label.setWordWrap(True)
        admin_layout.addWidget(self.stats_label)

        self.admin_status_label = QLabel("")
        self.admin_status_label.setObjectName("Muted")
        self.admin_status_label.setWordWrap(True)
        admin_layout.addWidget(self.admin_status_label)

        admin_hint = QLabel(
            "Импорт / экспорт и права пользователей — позже (см. docs/ROADMAP.md)."
        )
        admin_hint.setObjectName("Muted")
        admin_hint.setWordWrap(True)
        admin_layout.addWidget(admin_hint)
        root.addWidget(self.admin_panel)
        root.addStretch(1)  # плашки прижаты к верху, лишнее место — внизу

        self.refresh()
        self._update_save_state()

    # ------------------------------------------- перенос остатков на 1 января
    def _build_year_start_panel(self, layout: QVBoxLayout) -> None:
        """Плашка «Данные»: разовые переносимые остатки на начало года."""

        title = QLabel("📥  Данные на 1 января (перенос остатков)")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)

        hint = QLabel(
            "Разовые данные, с которых начинается год: по поставщикам — "
            "дата оплаты и сумма долга, по покупателям — сумма "
            "задолженности, отдельно — текущие средства на счету. Формул к "
            "этим цифрам нет: они сохраняются и показываются как введены."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # --- дата остатков ---
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        date_row.addWidget(QLabel("Дата остатков"))
        start_day = _parse_date_text(self.store.data.year_start.date)
        if not start_day:
            start_day = date(date.today().year, 1, 1).isoformat()
        self.year_start_date_edit = make_date_edit(
            date.fromisoformat(start_day)
        )
        date_row.addWidget(self.year_start_date_edit)
        date_row.addStretch(1)
        layout.addLayout(date_row)

        # --- таблица поставщиков: дата оплаты и сумма ---
        layout.addWidget(QLabel("Поставщики — дата оплаты и сумма долга"))
        self.supplier_debts_table = QTableWidget(0, 3)
        self.supplier_debts_table.setHorizontalHeaderLabels(
            ["Наименование", "Дата оплаты", "Сумма"]
        )
        self.supplier_debts_table.verticalHeader().setVisible(False)
        self.supplier_debts_table.setEditTriggers(
            QAbstractItemView.EditTrigger.AllEditTriggers
        )
        self.supplier_debts_table.setItemDelegateForColumn(2, _AmountDelegate())
        supplier_header = self.supplier_debts_table.horizontalHeader()
        supplier_header.setSectionResizeMode(0, supplier_header.ResizeMode.Stretch)
        self.supplier_debts_table.setColumnWidth(1, 160)
        self.supplier_debts_table.setColumnWidth(2, 160)
        layout.addWidget(self.supplier_debts_table)

        # --- таблица покупателей: задолженность ---
        layout.addWidget(QLabel("Покупатели — задолженность"))
        self.customer_debts_table = QTableWidget(0, 2)
        self.customer_debts_table.setHorizontalHeaderLabels(
            ["Наименование", "Задолженность"]
        )
        self.customer_debts_table.verticalHeader().setVisible(False)
        self.customer_debts_table.setEditTriggers(
            QAbstractItemView.EditTrigger.AllEditTriggers
        )
        self.customer_debts_table.setItemDelegateForColumn(1, _AmountDelegate())
        customer_header = self.customer_debts_table.horizontalHeader()
        customer_header.setSectionResizeMode(0, customer_header.ResizeMode.Stretch)
        self.customer_debts_table.setColumnWidth(1, 160)
        layout.addWidget(self.customer_debts_table)

        # --- текущие средства на счету ---
        funds_row = QHBoxLayout()
        funds_row.setSpacing(8)
        funds_row.addWidget(QLabel("Текущие средства на счету"))
        funds_value = parse_money(self.store.data.year_start.funds or "0")
        self.funds_edit = make_amount_edit(
            fmt_money_plain(funds_value) if funds_value else ""
        )
        self.funds_edit.setFixedWidth(200)
        funds_row.addWidget(self.funds_edit)
        funds_row.addStretch(1)
        layout.addLayout(funds_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.year_start_save_btn = blue_button("Сохранить")
        self.year_start_save_btn.clicked.connect(self._save_year_start)
        buttons.addWidget(self.year_start_save_btn)
        layout.addLayout(buttons)

        self.year_start_status_label = QLabel("")
        self.year_start_status_label.setObjectName("Muted")
        self.year_start_status_label.setWordWrap(True)
        layout.addWidget(self.year_start_status_label)

        self._fill_year_start_tables()

    def _fill_year_start_tables(self) -> None:
        data = self.store.data

        suppliers = [s for s in data.suppliers if not s.archived]
        table = self.supplier_debts_table
        table.setRowCount(len(suppliers))
        for row, supplier in enumerate(suppliers):
            entry = next(
                (
                    item
                    for item in data.year_start.supplier_debts
                    if item.counterparty_id == supplier.id
                ),
                None,
            )
            values = (
                supplier.name,
                _date_text(entry.due_date) if entry else "",
                fmt_money_plain(parse_money(entry.amount or "0"))
                if entry and parse_money(entry.amount or "0")
                else "",
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                flags = (
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                if column:  # дата и сумма вводятся прямо в ячейку
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                item.setData(Qt.ItemDataRole.UserRole, supplier.id)
                if column == 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(row, column, item)

        customers = [c for c in data.customers if not c.archived]
        table = self.customer_debts_table
        table.setRowCount(len(customers))
        for row, customer in enumerate(customers):
            entry = next(
                (
                    item
                    for item in data.year_start.customer_debts
                    if item.counterparty_id == customer.id
                ),
                None,
            )
            amount = parse_money(entry.amount or "0") if entry else None
            values = (
                customer.name,
                fmt_money_plain(amount) if amount else "",
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                flags = (
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                if column:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                item.setData(Qt.ItemDataRole.UserRole, customer.id)
                if column == 1:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(row, column, item)

    def _save_year_start(self) -> None:
        """Записать перенесённые остатки в файл данных."""

        data = self.store.data
        set_year_start_date(
            data, self.year_start_date_edit.date().toPython().isoformat()
        )

        for row in range(self.supplier_debts_table.rowCount()):
            name_item = self.supplier_debts_table.item(row, 0)
            if name_item is None:
                continue
            counterparty_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            date_item = self.supplier_debts_table.item(row, 1)
            amount_item = self.supplier_debts_table.item(row, 2)
            amount_text = amount_item.text().strip() if amount_item else ""
            amount = parse_money(amount_text) if amount_text else None
            set_year_start_supplier_debt(
                data,
                counterparty_id,
                _parse_date_text(date_item.text() if date_item else ""),
                amount,
            )

        for row in range(self.customer_debts_table.rowCount()):
            name_item = self.customer_debts_table.item(row, 0)
            if name_item is None:
                continue
            counterparty_id = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
            amount_item = self.customer_debts_table.item(row, 1)
            amount_text = amount_item.text().strip() if amount_item else ""
            amount = parse_money(amount_text) if amount_text else None
            set_year_start_customer_debt(data, counterparty_id, amount)

        funds_text = self.funds_edit.text().strip()
        funds = parse_money(funds_text) if funds_text else Decimal("0")
        set_year_start_funds(data, funds if funds else Decimal("0"))

        self.store.save()
        self._fill_year_start_tables()
        saved_date = data.year_start.date
        self.year_start_status_label.setText(
            f"Сохранено: остатки на {_date_text(saved_date) or '—'}. "
            f"Поставщиков: {len(data.year_start.supplier_debts)}, "
            f"покупателей: {len(data.year_start.customer_debts)}, "
            f"средства: {fmt_money_plain(parse_money(data.year_start.funds or '0'))}."
        )

    # ------------------------------------------------------- администрирование
    def _open_data_folder(self) -> None:
        """Открыть папку с data.json средствами системы."""

        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.path.parent)))

    def _make_backup(self) -> None:
        """Скопировать data.json рядом, с датой и временем в имени."""

        from datetime import datetime

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        target = self.store.path.with_name(f"data-backup-{stamp}.json")
        try:
            target.write_bytes(self.store.path.read_bytes())
        except OSError as error:
            self.admin_status_label.setText(f"Не удалось скопировать: {error}")
            return
        self.admin_status_label.setText(f"Копия создана: {target.name}")

    def _reload_file(self) -> None:
        """Перечитать файл данных (для ручной правки/восстановления копии)."""

        self.store.data = self.store._load()  # noqa: SLF001 - тот же путь загрузки
        self._pending_overrides = dict(self.store.data.day_overrides)
        self.refresh()
        self._update_admin_stats("Файл перечитан")
        if self.on_calendar_saved is not None:
            self.on_calendar_saved()

    def _update_admin_stats(self, message: str = "") -> None:
        data = self.store.data
        self.stats_label.setText(
            f"Поставщиков: {len(data.suppliers)} · "
            f"Покупателей: {len(data.customers)} · "
            f"Операций: {len(data.operations)} · "
            f"Поступлений наличными/по картам: {len(data.cash_receipts)} · "
            f"Отметок календаря: {len(data.day_overrides)}"
        )
        self.admin_status_label.setText(message)

    # ------------------------------------------------------------ навигация
    def _shift_year(self, delta: int) -> None:
        self.year += delta
        self.refresh()

    # ------------------------------------------------------------- календарь
    def refresh(self) -> None:
        self.year_label.setText(str(self.year))
        self._day_buttons = {}

        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(12)
        for month in range(1, 13):
            row = (month - 1) // MONTHS_PER_ROW
            col = (month - 1) % MONTHS_PER_ROW
            grid.addWidget(self._build_month(self.year, month), row, col)
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(container)
        # v0.17.0: путь к файлу и незакоммиченные правки календаря —
        # от активной базы (после переключения МИР <-> КОРЕЛИЧИ).
        self.path_label.setText(str(self.store.path))
        self._pending_overrides = dict(self.store.data.day_overrides)
        self._fill_year_start_tables()
        self._update_admin_stats()
        self._update_save_state()

    def _build_month(self, year: int, month: int) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        title = QLabel(MONTHS_RU[month - 1])
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-weight:800;font-size:{styles.fs(14)}px")
        layout.addWidget(title)

        days_grid = QGridLayout()
        days_grid.setSpacing(3)
        for col, name in enumerate(WEEKDAYS_RU_TITLE):
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("Muted")
            days_grid.addWidget(label, 0, col)

        weeks = calendar_module.monthcalendar(year, month)  # недели с Пн
        for r, week in enumerate(weeks, start=1):
            for c, day_num in enumerate(week):
                if day_num == 0:
                    continue
                day = date(year, month, day_num)
                btn = QPushButton(str(day_num))
                btn.setFixedSize(32, 26)
                btn.clicked.connect(
                    lambda _checked=False, d=day: self._on_day_clicked(d)
                )
                self._day_buttons[day] = btn
                self._paint_day(btn, day)
                days_grid.addWidget(btn, r, c)
        layout.addLayout(days_grid)
        layout.addStretch(1)
        return frame

    # ----------------------------------------------------------- отметки дней
    @property
    def has_unsaved_changes(self) -> bool:
        """Есть ли отметки, ещё не попавшие в файл данных."""

        return self._pending_overrides != self.store.data.day_overrides

    def _is_day_off(self, day: date) -> bool:
        override = self._pending_overrides.get(day.isoformat())
        if override == "off":
            return True
        if override == "work":
            return False
        return day.weekday() >= 5

    def _paint_day(self, btn: QPushButton, day: date) -> None:
        is_off = self._is_day_off(day)
        bg = styles.WEEKEND_RED if is_off else styles.WORKDAY_GREY
        fg = "#ffffff" if is_off else styles.TEXT
        border = f"2px solid {styles.BLUE}" if day == date.today() else "none"
        btn.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};border:{border};"
            f"border-radius:5px;padding:0;font-weight:600}}"
        )
        btn.setToolTip(
            f"{day:%d.%m.%Y}: {'выходной' if is_off else 'рабочий'} день"
        )

    def _on_day_clicked(self, day: date) -> None:
        mode = "off" if self.chk_off.isChecked() else "work"
        is_default_off = day.weekday() >= 5
        if (mode == "off") == is_default_off:
            self._pending_overrides.pop(day.isoformat(), None)
        else:
            self._pending_overrides[day.isoformat()] = mode

        btn = self._day_buttons.get(day)
        if btn is not None:
            self._paint_day(btn, day)
        self._update_save_state()

    def _update_save_state(self, message: str | None = None) -> None:
        if not hasattr(self, "save_button"):
            return
        dirty = self.has_unsaved_changes
        self.save_button.setEnabled(dirty)
        self.cancel_button.setEnabled(dirty)
        if message is not None:
            self.status_label.setText(message)
        elif dirty:
            self.status_label.setText("Есть несохранённые изменения")
        else:
            self.status_label.setText("Изменений нет")

    def discard_changes(self) -> None:
        """Вернуть черновик календаря к последнему сохранённому состоянию."""

        self._pending_overrides = dict(self.store.data.day_overrides)
        for day, btn in self._day_buttons.items():
            self._paint_day(btn, day)
        self._update_save_state("Изменения отменены")

    def save_calendar(self) -> bool:
        """Сохранить черновик отметок и уведомить главное окно.

        Возвращает ``False``, если сохранять было нечего.
        """

        if not self.has_unsaved_changes:
            self._update_save_state("Изменений нет")
            return False
        self.store.data.day_overrides = dict(self._pending_overrides)
        self.store.save()
        self._update_save_state("Сохранено")
        if self.on_calendar_saved is not None:
            self.on_calendar_saved()
        return True
