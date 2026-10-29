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
import json
from pathlib import Path
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import styles
from ..access import (
    BASE_KORELICHI,
    BASE_MIR,
    PAGE_TITLES,
    User,
    check_password,
    load_current_user,
    load_users,
    new_user_id,
    save_current_user,
    save_users,
    set_password,
)
from ..dates import MONTHS_RU, WEEKDAYS_RU_TITLE, fmt_money_plain, parse_money
from ..models import (
    AppData,
    YearStart,
    set_year_start_customer_debt,
    set_year_start_date,
    set_year_start_funds,
    set_year_start_supplier_debt,
)
from ..storage import OTHER_BASE, DataStore, data_path_for
from ..widgets import (
    AmountLineEdit,
    CollapsiblePanel,
    blue_button,
    danger_button,
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
        on_users_saved: Callable[[User], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_calendar_saved = on_calendar_saved
        # v0.20.0: выбор текущего пользователя применяется сразу.
        self.on_users_saved = on_users_saved
        self.year = date.today().year
        self._day_buttons: dict[date, QPushButton] = {}
        # Черновик отметок: клики по календарю меняют его, а не файл
        # данных. В store он попадёт только по кнопке «Сохранить».
        self._pending_overrides: dict[str, str] = dict(store.data.day_overrides)
        # v0.18.0: без права ввода календарь и таблицы долгов только
        # для просмотра (флаг выставляет MainWindow.apply_access).
        self.read_only = False

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
        # v0.24.0: раскрытая плашка занимает всю высоту экрана — иначе
        # таблицы остатков сжимались и не использовали место. Само
        # содержимое плашки тоже должно тянуться, иначе место достаётся
        # плашке, а не таблицам внутри неё.
        self.data_panel.content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.data_panel, stretch=1)

        # --- раскрывающаяся плашка «Пользователи и права» (v0.18.0) ---
        self.users_panel = CollapsiblePanel("Пользователи и права", expanded=False)
        self._build_users_panel(self.users_panel.content_layout)
        root.addWidget(self.users_panel)

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
        # v0.23.1: «Очистить базу» — удалить все данные с резервной
        # копией и двумя подтверждениями (просьба пользователя).
        self.clear_button = danger_button("Очистить базу")
        self.clear_button.setToolTip(
            "Удалить все данные этой базы. Перед очисткой создаётся "
            "резервная копия, нужно два подтверждения."
        )
        self.clear_button.clicked.connect(self._clear_database)
        admin_buttons.addWidget(self.clear_button)
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
        # v0.24.0: пустой отступ внизу больше не забирает свободное
        # место — иначе раскрытая плашка «Данные» не дорастала до низа
        # экрана и таблицы остатков оставались низкими (просьба
        # пользователя: разворачивать на всю высоту экрана).
        root.addStretch(0)

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

        # --- таблицы: поставщики слева, покупатели справа (v0.24.0) ---
        # Раньше таблицы стояли друг под другом во всю ширину; по просьбе
        # пользователя они стали двумя вертикальными списками рядом и
        # занимают всю высоту экрана.
        tables_row = QHBoxLayout()
        tables_row.setSpacing(12)

        left_column = QVBoxLayout()
        left_column.setSpacing(6)
        left_column.addWidget(QLabel("Поставщики — дата оплаты и сумма долга"))
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
        self.supplier_debts_table.setColumnWidth(1, 140)
        self.supplier_debts_table.setColumnWidth(2, 140)
        self._stretch_debts_table(self.supplier_debts_table)
        left_column.addWidget(self.supplier_debts_table, stretch=1)
        tables_row.addLayout(left_column, stretch=1)

        right_column = QVBoxLayout()
        right_column.setSpacing(6)
        right_column.addWidget(QLabel("Покупатели — задолженность"))
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
        self._stretch_debts_table(self.customer_debts_table)
        right_column.addWidget(self.customer_debts_table, stretch=1)
        tables_row.addLayout(right_column, stretch=1)

        layout.addLayout(tables_row, stretch=1)

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

    @staticmethod
    def _stretch_debts_table(table: QTableWidget) -> None:
        """Таблица тянется по высоте и всегда с широким скроллбаром.

        v0.24.0: таблицы остатков стоят рядом (слева и справа) и
        занимают всю высоту экрана, поэтому им нужна растягиваемая
        политика и всегда видимая полоса прокрутки.
        """

        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        table.setStyleSheet(styles.WIDE_SCROLLBAR_ON_TABLE)

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

    def _make_backup(self, target_dir: Path | None = None) -> Path | None:
        """Скопировать data.json с датой и временем в имени.

        ``target_dir`` — куда положить копию (v0.23.1: при очистке базы
        копия идёт в «Загрузки\База поставщики оплата»). По умолчанию —
        рядом с самой базой, как и было.
        """

        from datetime import datetime

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder = target_dir if target_dir is not None else self.store.path.parent
        target = folder / f"data-backup-{stamp}.json"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.store.path.read_bytes())
        except OSError as error:
            self.admin_status_label.setText(f"Не удалось скопировать: {error}")
            return None
        self.admin_status_label.setText(f"Копия создана: {target.name}")
        return target

    def _reload_file(self) -> None:
        """Перечитать файл данных (для ручной правки/восстановления копии)."""

        self.store.data = self.store._load()  # noqa: SLF001 - тот же путь загрузки
        self._pending_overrides = dict(self.store.data.day_overrides)
        self.refresh()
        self._update_admin_stats("Файл перечитан")
        if self.on_calendar_saved is not None:
            self.on_calendar_saved()

    # v0.23.1: копии при очистке базы лежат в Загрузках, в отдельной
    # папке — чтобы их не уносило в облако вместе с самой базой.
    BACKUP_DIR_NAME = "База поставщики оплата"

    def _backup_dir(self) -> Path:
        """Папка для копий: «Загрузки\База поставщики оплата»."""

        from PySide6.QtCore import QStandardPaths

        downloads = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
        base = Path(downloads) if downloads else Path.home() / "Downloads"
        return base / self.BACKUP_DIR_NAME

    def _export_before_clear(self, target_dir: Path) -> Path | None:
        """Автоматическая выгрузка обеих баз в Excel перед очисткой.

        Файл ``oplata-базы-ДАТА_ВРЕМЯ.xlsx`` кладётся в ``target_dir``
        (v0.23.1 — «Загрузки\База поставщики оплата»). Возвращает путь к
        файлу или None, если выгрузить не удалось.
        """

        from ..excelio import ExcelError, export_bases

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        target = target_dir / f"oplata-базы-{stamp}.xlsx"
        bases = {self.store.base_key: self.store.data}
        other = OTHER_BASE[self.store.base_key]
        bases[other] = self._read_other_base(other)
        try:
            export_bases(bases, target)
        except ExcelError as error:
            QMessageBox.warning(
                self,
                "Очистить базу",
                f"Не удалось выгрузить базы в Excel:\n{error}\n\n"
                "Очистка отменена.",
            )
            return None
        return target

    def _read_other_base(self, base_key: str) -> AppData:
        """Прочитать вторую базу с диска (в выгрузку идут обе)."""

        path = data_path_for(base_key)
        if not path.exists():
            return AppData()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return AppData.from_dict(raw)
        except (OSError, ValueError, TypeError):
            return AppData()

    def _clear_database(self) -> None:
        """Полностью очистить базу: выгрузка в Excel + подтверждение.

        Стираются поставщики, покупатели, операции, поступления
        наличными/по картам, прочие виды оплат, отметки календаря и
        перенос остатков на 1 января. Пользователи и права лежат в
        отдельном файле (users.json) и не трогаются. Перед стиранием
        обе базы сами выгружаются в Excel и делается копия data.json
        (v0.23.1).
        """

        data = self.store.data
        folder_text = str(self._backup_dir())
        answer = QMessageBox.warning(
            self,
            "Очистить базу",
            "Будут удалены ВСЕ данные этой базы:\n"
            f"поставщиков: {len(data.suppliers)} · "
            f"покупателей: {len(data.customers)} · "
            f"операций: {len(data.operations)} · "
            f"поступлений: {len(data.cash_receipts)} · "
            f"прочих оплат: {len(data.other_payments)} · "
            f"отметок календаря: {len(data.day_overrides)}.\n\n"
            "Перед очисткой базы будут выгружены в Excel и сохранена "
            f"копия data.json в папку:\n{folder_text}\n\n"
            "Действие необратимо.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._update_admin_stats("Очистка отменена")
            return

        folder = self._backup_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.warning(
                self,
                "Очистить базу",
                f"Не удалось создать папку для копий:\n{folder}\n{error}\n\n"
                "Очистка отменена.",
            )
            self._update_admin_stats("Очистка отменена: нет папки для копий")
            return

        exported = self._export_before_clear(folder)
        if exported is None:  # выгрузить не удалось — базу не трогаем
            self._update_admin_stats("Очистка отменена: нет выгрузки в Excel")
            return
        backup = self._make_backup(folder)  # копия data.json, до стирания
        if backup is None:
            self._update_admin_stats("Очистка отменена: нет копии data.json")
            return

        data.suppliers = []
        data.customers = []
        data.operations = []
        data.cash_receipts = []
        data.other_payments = []
        data.day_overrides = {}
        data.year_start = YearStart()
        self._pending_overrides = {}
        self.store.save()
        self.refresh()
        self._update_admin_stats(
            "База очищена. Выгрузка в Excel и копия data.json — в папке "
            f"«{self.BACKUP_DIR_NAME}» в Загрузках ({exported.name})."
        )
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

    # ---------------------------------------------------------- пользователи
    def _build_users_panel(self, layout) -> None:
        """Список пользователей и их права (v0.18.0)."""

        hint = QLabel(
            "Кто работает на каждом компьютере и что этому пользователю "
            "разрешено. Отметьте в столбце «Текущий», кто работает на "
            "этом компьютере, и нажмите «Сохранить пользователей» — "
            "программа сразу перейдёт в режим этого пользователя."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # v0.20.0: столбец «Текущий» — какой пользователь работает на
        # этом компьютере (раньше программа спрашивала об этом при
        # первом запуске).
        self.users_column_count = len(PAGE_TITLES) + 4
        self.users_col_current = len(PAGE_TITLES) + 3
        self.users_table = QTableWidget(0, self.users_column_count)
        self.users_table.setHorizontalHeaderLabels(
            ["Пользователь", *PAGE_TITLES, "База", "Ввод данных", "Текущий"]
        )
        self.users_table.verticalHeader().setVisible(False)
        self.users_table.setEditTriggers(
            QAbstractItemView.EditTrigger.AllEditTriggers
        )
        self.users_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.users_table)
        # Отметка «Текущий» может быть только одна.
        self.users_table.itemChanged.connect(self._on_users_item_changed)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.users_add_btn = QPushButton("Добавить")
        self.users_add_btn.clicked.connect(self._add_user_row)
        buttons.addWidget(self.users_add_btn)
        self.users_remove_btn = QPushButton("Удалить")
        self.users_remove_btn.clicked.connect(self._remove_user_row)
        buttons.addWidget(self.users_remove_btn)
        self.users_save_btn = blue_button("Сохранить пользователей")
        self.users_save_btn.clicked.connect(self._save_users)
        buttons.addWidget(self.users_save_btn)
        self.users_password_btn = QPushButton("Сменить пароль")
        self.users_password_btn.setToolTip("Пароль на раздел «Конфигурация»")
        self.users_password_btn.clicked.connect(self._change_password)
        buttons.addWidget(self.users_password_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.users_status_label = QLabel("")
        self.users_status_label.setObjectName("Muted")
        self.users_status_label.setWordWrap(True)
        layout.addWidget(self.users_status_label)

        self._fill_users_table()

    def _fill_users_table(self) -> None:
        """Перечитать список пользователей в таблицу."""

        if not hasattr(self, "users_table"):
            return
        users = load_users()
        current_user = load_current_user()
        # Заполнение дёргает itemChanged — на время отключаем обработчик.
        self.users_table.itemChanged.disconnect(self._on_users_item_changed)
        try:
            self._fill_users_table_rows(users, current_user)
        finally:
            self.users_table.itemChanged.connect(self._on_users_item_changed)

    def _fill_users_table_rows(self, users, current_user) -> None:
        self.users_table.setRowCount(len(users))
        for row, user in enumerate(users):
            name_item = QTableWidgetItem(user.name)
            name_item.setData(Qt.ItemDataRole.UserRole, user.id)
            self.users_table.setItem(row, 0, name_item)
            for col in range(len(PAGE_TITLES)):
                item = QTableWidgetItem()
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                checked = col in user.pages
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                self.users_table.setItem(row, 1 + col, item)
            base_combo = QComboBox()
            base_combo.addItem("Обе базы", None)
            base_combo.addItem("МИР", BASE_MIR)
            base_combo.addItem("КОРЕЛИЧИ", BASE_KORELICHI)
            index = {None: 0, BASE_MIR: 1, BASE_KORELICHI: 2}.get(user.base, 0)
            base_combo.setCurrentIndex(index)
            self.users_table.setCellWidget(row, 1 + len(PAGE_TITLES), base_combo)
            edit_item = QTableWidgetItem()
            edit_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            edit_item.setCheckState(
                Qt.CheckState.Checked if user.can_edit else Qt.CheckState.Unchecked
            )
            self.users_table.setItem(row, 2 + len(PAGE_TITLES), edit_item)
            # v0.20.0: кто из пользователей работает на этом компьютере.
            current_item = QTableWidgetItem()
            current_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            is_current = current_user is not None and current_user.id == user.id
            current_item.setCheckState(
                Qt.CheckState.Checked if is_current else Qt.CheckState.Unchecked
            )
            self.users_table.setItem(row, self.users_col_current, current_item)

    def _on_users_item_changed(self, item: QTableWidgetItem) -> None:
        """Следим, чтобы «Текущий» был отмечен только у одного (v0.20.0)."""

        if item.column() != self.users_col_current:
            return
        if item.checkState() != Qt.CheckState.Checked:
            return
        self.users_table.itemChanged.disconnect(self._on_users_item_changed)
        try:
            for row in range(self.users_table.rowCount()):
                other = self.users_table.item(row, self.users_col_current)
                if other is not None and other is not item:
                    other.setCheckState(Qt.CheckState.Unchecked)
        finally:
            self.users_table.itemChanged.connect(self._on_users_item_changed)

    def _add_user_row(self) -> None:
        """Новый пользователь: только просмотр таблицы «Покупатели»."""

        row = self.users_table.rowCount()
        self.users_table.insertRow(row)
        name_item = QTableWidgetItem("Новый пользователь")
        name_item.setData(Qt.ItemDataRole.UserRole, new_user_id())
        self.users_table.setItem(row, 0, name_item)
        for col in range(len(PAGE_TITLES)):
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            # По умолчанию новая учётка видит только «Покупатели».
            checked = PAGE_TITLES[col] == "Покупатели"
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.users_table.setItem(row, 1 + col, item)
        base_combo = QComboBox()
        base_combo.addItem("Обе базы", None)
        base_combo.addItem("МИР", BASE_MIR)
        base_combo.addItem("КОРЕЛИЧИ", BASE_KORELICHI)
        self.users_table.setCellWidget(row, 1 + len(PAGE_TITLES), base_combo)
        edit_item = QTableWidgetItem()
        edit_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        edit_item.setCheckState(Qt.CheckState.Unchecked)
        self.users_table.setItem(row, 2 + len(PAGE_TITLES), edit_item)
        current_item = QTableWidgetItem()
        current_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        )
        current_item.setCheckState(Qt.CheckState.Unchecked)
        self.users_table.setItem(row, self.users_col_current, current_item)
        self.users_table.setCurrentCell(row, 0)
        self.users_status_label.setText("Добавлен пользователь — нажмите «Сохранить»")

    def _remove_user_row(self) -> None:
        row = self.users_table.currentRow()
        if row < 0:
            return
        if self.users_table.rowCount() <= 1:
            self.users_status_label.setText("Должен остаться хотя бы один пользователь")
            return
        self.users_table.removeRow(row)
        self.users_status_label.setText("Пользователь удалён — нажмите «Сохранить»")

    def _collect_users(self) -> list[User]:
        """Собрать список пользователей из таблицы."""

        users: list[User] = []
        for row in range(self.users_table.rowCount()):
            name_item = self.users_table.item(row, 0)
            name = name_item.text().strip() if name_item is not None else ""
            user_id = (
                name_item.data(Qt.ItemDataRole.UserRole)
                if name_item is not None
                else None
            )
            pages = []
            for col in range(len(PAGE_TITLES)):
                item = self.users_table.item(row, 1 + col)
                if item is not None and item.checkState() == Qt.CheckState.Checked:
                    pages.append(col)
            combo = self.users_table.cellWidget(row, 1 + len(PAGE_TITLES))
            base = combo.currentData() if combo is not None else None
            edit_item = self.users_table.item(row, 2 + len(PAGE_TITLES))
            can_edit = bool(
                edit_item is not None
                and edit_item.checkState() == Qt.CheckState.Checked
            )
            users.append(
                User(
                    id=str(user_id or new_user_id()),
                    name=name or "Пользователь",
                    pages=pages,
                    base=base,
                    can_edit=can_edit,
                    can_switch_base=base is None and can_edit,
                )
            )
        return users

    def _current_user_row(self) -> int:
        """Строка, отмеченная в столбце «Текущий» (-1 — не отмечена)."""

        for row in range(self.users_table.rowCount()):
            item = self.users_table.item(row, self.users_col_current)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                return row
        return -1

    def _save_users(self) -> None:
        users = self._collect_users()
        save_users(users)
        # v0.20.0: отмеченный пользователь становится текущим на этом
        # компьютере — главное окно сразу применяет его права.
        row = self._current_user_row()
        if row < 0 or row >= len(users):
            self.users_status_label.setText(
                "Пользователи сохранены. Отметьте в столбце «Текущий», "
                "кто работает на этом компьютере"
            )
            return
        save_current_user(users[row])
        if self.on_users_saved is not None:
            self.on_users_saved(users[row])
        self.users_status_label.setText(
            f"Пользователи сохранены. Текущий пользователь: {users[row].name}"
        )

    def _change_password(self) -> None:
        """Сменить пароль на раздел «Конфигурация» (v0.18.0)."""

        from PySide6.QtWidgets import QInputDialog, QLineEdit

        old_text, ok = QInputDialog.getText(
            self, "Смена пароля", "Текущий пароль:", QLineEdit.EchoMode.Password
        )
        if not ok or not check_password(old_text):
            self.users_status_label.setText("Текущий пароль не принят")
            return
        new_text, ok = QInputDialog.getText(
            self, "Смена пароля", "Новый пароль:", QLineEdit.EchoMode.Password
        )
        if not ok or not new_text:
            return
        set_password(new_text)
        self.users_status_label.setText("Пароль изменён")

    def set_read_only(self, read_only: bool, can_admin: bool = False) -> None:
        """Права пользователя: «только просмотр» или полный доступ (v0.18.0).

        ``can_admin`` — доступ к разделу «Администрирование». С v0.23.1
        плашку видят все, кто открыл вкладку «Конфигурация»: сама
        вкладка закрыта паролем, а кнопка «Очистить базу» должна быть
        доступна любому, кто этот пароль знает (просьба пользователя).
        Параметр оставлен для совместимости вызова.
        """

        self.read_only = read_only
        self._can_admin = can_admin  # право на раздел «Данные» (v0.18.0)
        # Отметки в календаре меняют данные — без права ввода нельзя.
        self.save_button.setVisible(not read_only)
        self.cancel_button.setVisible(not read_only)
        self.year_start_save_btn.setVisible(not read_only)
        self.supplier_debts_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if read_only
            else QAbstractItemView.EditTrigger.AllEditTriggers
        )
        self.customer_debts_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if read_only
            else QAbstractItemView.EditTrigger.AllEditTriggers
        )
        # v0.23.1: «Администрирование» видно всем, кто зашёл на вкладку
        # «Конфигурация» (она за паролем).
        self.admin_panel.setVisible(True)
        # Пользователей правит только тот, у кого есть право ввода данных.
        for btn in (
            self.users_add_btn,
            self.users_remove_btn,
            self.users_save_btn,
            self.users_password_btn,
        ):
            btn.setVisible(not read_only)
        self.users_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if read_only
            else QAbstractItemView.EditTrigger.AllEditTriggers
        )

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
        self._fill_users_table()
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
        if self.read_only:  # v0.18.0: только просмотр
            return
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
