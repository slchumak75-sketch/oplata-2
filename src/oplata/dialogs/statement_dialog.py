"""Окно «Загрузить выписку» — таблица операций из PDF (v0.54.0).

Столбцы: Дата · УНП · Наименование организации · Оплата · Поступление ·
**Куда** · Уже в базе · Примечание (порядок «оплата перед поступлением»
и столбец «Куда» сразу после сумм — по просьбе пользователя).

v0.63.0: **разнос делается прямо в этой таблице**. Раньше кнопка
«Разнести» открывала второе окно со своим списком «Куда» — приходилось
смотреть в два места. Теперь назначение выбирается здесь же, а
второй таблицы нет.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import styles
from ..dates import fmt_money_plain
from ..models import OUTGOING, add_other_payment, other_payment_names
from ..statement import Statement, reconcile
from ..statement_match import (
    OTHER_INCOME_ROW_TITLE,
    base_amount,
    classify,
    classify_commission,
    choose_option_data,
    find_by_unp,
    find_payment_by_unp,
    find_stale_realizations,
    fmt_amount,
    free_payment_name,
    iso_date,
    plan_key,
    plan_totals,
    purpose_note,
    remove_operations,
    row_is_blocked,
    TARGET_CARD,
    TARGET_CASH,
    TARGET_CHOOSE,
    TARGET_CUSTOMERS,
    TARGET_NEW_PAYMENT,
    TARGET_OTHER_INCOME,
    TARGET_OTHER_PAYMENT,
    TARGET_SUPPLIERS,
    Match,
    distribute,
)
from ..widgets import green_button

COLUMNS = (
    "Дата",
    "УНП",
    "Наименование организации",
    "Оплата",
    "Поступление",
    # v0.63.0: «Куда» — сразу после сумм, как просил пользователь:
    # назначение видно и правится в этой же таблице.
    "Куда",
    "Уже в базе",
    # v0.56.0: «Примечание» — куда строка попадёт при разнесении
    # (пояснение пользователя: база наполняется постепенно).
    "Примечание",
)
(
    COL_DATE,
    COL_UNP,
    COL_NAME,
    COL_OUT,
    COL_IN,
    COL_TARGET,
    COL_BASE,
    COL_NOTE,
) = range(8)
# Ширины колонок: наименование и примечание тянутся, остальные — нет.
# Окно занимает весь экран, поэтому у сумм и списка «Куда» есть запас.
COLUMN_WIDTHS = (
    (COL_DATE, 110),
    (COL_UNP, 110),
    (COL_OUT, 130),
    (COL_IN, 130),
    (COL_TARGET, 300),
    (COL_BASE, 160),
)

# v0.68.0: ширина раскрывающегося списка «Куда». Пункт подписан как
# «Наименование · УНП · Примечание», и в ширину колонки такая строка не
# влезает — список обрезал её, а по обрезанному началу карточки не
# различить. Сам список поэтому шире колонки.
TARGET_POPUP_WIDTH = 460

# Окно не меньше этих размеров, даже если экран узкий (v0.63.0).
MIN_WINDOW_WIDTH = 1180
MIN_WINDOW_HEIGHT = 520

# Пункт списка «Куда» для строки, которой не хватило вида оплаты.
NEW_PAYMENT_CHOICE = "Новый вид оплаты…"

# Что можно выбрать в колонке «Куда»: подпись → ключ строки блока.
TARGET_CHOICES = (
    ("Поступление по картам", TARGET_CARD),
    ("Поступление наличными", TARGET_CASH),
    ("Покупатели", TARGET_CUSTOMERS),
    ("Поставщики", TARGET_SUPPLIERS),
    ("Прочие поступления", TARGET_OTHER_INCOME),
)


class StatementDialog(QDialog):
    """Таблица операций выписки с кнопками «Продолжить» и «Отмена»."""

    def __init__(
        self,
        statement: Statement,
        source_name: str = "",
        parent: QWidget | None = None,
        bank_unp: str = "",
        store=None,
        on_imported=None,
    ) -> None:
        super().__init__(parent)
        self.statement = statement
        # v0.56.0: хранилище — по нему считается «Примечание» и делается
        # разнесение; без него окно остаётся таблицей для просмотра.
        self.store = store
        self.on_imported = on_imported
        # v0.54.0: УНП обслуживающего банка — из карточки в «Конфигурации».
        # Его строки в выписке не поставщик и не покупатель.
        self.bank_unp = (bank_unp or "").strip()
        # v0.63.0: разнос делается здесь же, поэтому окно держит и
        # назначения строк, и признак переразноса.
        self.redo = False
        self.result_distribution = None
        # Страховка от повторного входа: список «Куда» может дёрнуть
        # создание карточки, пока карточка уже открыта.
        self._creating_payment = False
        # Примечания считаются заново: с момента открытия файла в базе
        # могли появиться поставщики и покупатели.
        self.matches: list[Match] = []
        self.commissions: list[Match] = []
        self.planned: dict[tuple, Decimal] = {}
        self.visible: list[int] = []
        # v0.66.0: сверка строк с итогами файла. Считается один раз и
        # одинаково для подписи и для таблицы: расхождение должно быть
        # видно в обоих местах и не расходиться само с собой.
        self.reconciliation = reconcile(statement)
        if self.store is not None:
            self._recalculate()
        self.setWindowTitle("Выписка из банка")
        self.setModal(True)
        # v0.63.0: таблица занимает **всю ширину экрана** (просьба
        # пользователя): в выписке стало больше колонок, и наименование
        # с примечанием должны читаться целиком. По высоте — почти весь
        # экран, чтобы операций было видно больше.
        screen = QApplication.primaryScreen()
        free = screen.availableGeometry() if screen is not None else None
        if free is not None:
            self.resize(
                max(free.width() - 24, MIN_WINDOW_WIDTH),
                max(free.height() - 60, MIN_WINDOW_HEIGHT),
            )
        else:
            self.resize(1320, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("🧾  Выписка из банка")
        title.setStyleSheet(f"font-size:{styles.fs(18)}px;font-weight:800")
        root.addWidget(title)

        # v0.64.0: подпись — rich text: расхождение с оборотами файла
        # выделяем красным, иначе его легко не заметить.
        caption = QLabel(self._caption(source_name))
        caption.setTextFormat(Qt.TextFormat.RichText)
        caption.setWordWrap(True)
        root.addWidget(caption)

        self.table = QTableWidget()
        # v0.55.0: строки узкие — по шрифту, без «воздуха» (просьба
        # пользователя): выписка бывает длинная, и в экран должно
        # влезать как можно больше операций.
        # Шрифт ставим самому виджету, а не только стилем: тогда
        # QFontMetrics даёт верную высоту строки.
        table_font = self.table.font()
        table_font.setPixelSize(styles.fs(11))
        self.table.setFont(table_font)
        self.table.setStyleSheet(
            f"QTableWidget{{font-size:{styles.fs(11)}px;\n"
            "gridline-color:" + styles.TABLE_GRID + ";}\n"
            "QTableWidget::item{padding:1px 5px;}\n"
            "QHeaderView::section{padding:3px 5px;}"
        )
        # Перенос слов выключен: иначе длинное наименование организации
        # занимало две строки и таблица «раздувалась». Высота строки —
        # одна строка текста по шрифту плюс 4 px.
        self.table.setWordWrap(False)
        row_height = QFontMetrics(self.table.font()).height() + 4
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self.table.verticalHeader().setMinimumSectionSize(row_height)
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )  # таблица только для просмотра
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )  # горизонтального скролла в таблицах нет (правило проекта)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(70)
        for column, width in COLUMN_WIDTHS:
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Stretch)
        if self.store is None:
            # Без базы таблица остаётся просто просмотром файла: колонки
            # разноса в ней не нужны.
            self.table.setColumnHidden(COL_TARGET, True)
            self.table.setColumnHidden(COL_BASE, True)
        root.addWidget(self.table, stretch=1)

        self._fill_table()

        # --- кнопки ---
        buttons = QHBoxLayout()
        if self.store is not None:
            # Примечания считаются при открытии файла: когда в базе
            # появился новый контрагент, достаточно нажать «Обновить».
            refresh_btn = QPushButton("Обновить")
            refresh_btn.setToolTip(
                "Пересчитать примечания: поставщиков и покупателей "
                "добавляют постепенно"
            )
            refresh_btn.clicked.connect(self._refresh_notes)
            buttons.addWidget(refresh_btn)
        buttons.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        self.continue_btn = green_button("Разнести")
        self.continue_btn.setToolTip(
            "Разнести выписку по «Поступлению и оплате»: сначала "
            "показываем, куда попадёт каждая строка"
        )
        self.continue_btn.clicked.connect(self._continue)
        buttons.addWidget(self.continue_btn)
        root.addLayout(buttons)

    # --------------------------------------------------------------- данные
    def _caption(self, source_name: str) -> str:
        """Строка под заголовком: реквизиты файла и сверка сальдо."""

        statement = self.statement
        parts = []
        if source_name:
            parts.append(source_name)
        if statement.statement_date:
            parts.append(f"за {statement.statement_date}")
        if statement.account:
            parts.append(f"счёт {statement.account}")
        head = " · ".join(parts)
        commission = statement.total_commission
        check = self.reconciliation
        # v0.66.0: итоги файла показываем **всегда**. Иначе «не совпало»
        # заставляет сравнивать числа в уме: свои итоги против
        # напечатанных в файле.
        file_bits = [
            f"оплата {fmt_money_plain(statement.turnover_outgoing)}",
            f"поступление {fmt_money_plain(statement.turnover_incoming)}",
        ]
        if statement.operations_outgoing is not None:
            file_bits.append(
                f"операций {statement.operations_outgoing} / "
                f"{statement.operations_incoming}"
            )
        if commission:
            file_bits.append(f"комиссия банка {fmt_money_plain(commission)}")
        lines = [
            head,
            f"Входящее сальдо {fmt_money_plain(statement.opening_balance)} · "
            f"Оплата {fmt_money_plain(statement.total_outgoing)} · "
            f"Поступление {fmt_money_plain(statement.total_incoming)} · "
            f"Исходящее сальдо {fmt_money_plain(statement.closing_balance)}.",
            "Обороты файла: " + "; ".join(file_bits) + ".",
        ]
        if check.ok:
            lines.append(
                "Итог строк сошёлся с файлом"
                + (
                    " (комиссия банка добавлена и к оплате, "
                    "и к поступлению)."
                    if commission
                    else "."
                )
            )
        else:
            # v0.66.0: красным и **с цифрами** — файл прочитан с
            # расхождением, и разносить такие суммы нельзя. Именно так
            # выглядела выписка, где код платежа в бюджет приняли за
            # разряды суммы (300,00 прочиталось как 101 300,00).
            detail = "<br>".join(f"• {text}" for text in check.problems)
            lines.append(
                f'<span style="color:{styles.RED}">Внимание: итог строк '
                f"не совпал с файлом.<br>{detail}</span>"
            )
        bank_rows = sum(1 for row in statement.rows if self._is_bank(row))
        if bank_rows:
            lines.append(
                f"Строк банка (не поставщик и не покупатель): {bank_rows}."
            )
        # v0.64.0: подпись показывается как rich text (там бывает
        # красное предупреждение), поэтому переносы — через <br>.
        return "<br>".join(line for line in lines if line)

    def _note_text(self, row) -> str:
        """Примечание: куда строка попадёт при разнесении (v0.56.0).

        Считается **в момент показа**: базу поставщиков и покупателей
        пользователь наполняет постепенно, поэтому при повторном
        открытии файла примечания могут стать точнее.
        """

        if self.store is None:
            return ""
        return classify(row, self.store.data, self.bank_unp).note

    def _refresh_notes(self) -> None:
        """Пересчитать всё по нынешней базе (кнопка «Обновить»).

        v0.63.0: кроме примечаний пересчитываются списки «Куда» (в базе
        могли появиться новые контрагенты и виды оплаты) и колонка
        «Уже в базе».
        """

        if self.store is None:
            return
        self._recalculate()
        self._fill_table()

    def _is_bank(self, row) -> bool:
        """Строка принадлежит банку из карточки «Конфигурации» (v0.54.0)."""
        return bool(self.bank_unp) and row.unp == self.bank_unp

    def _fill_table(self) -> None:
        rows = self.statement.rows
        self.visible = [
            index
            for index, match in enumerate(self.matches)
            if match.target
        ]
        # v0.66.0: при расхождении с файлом под итогом появляется строка
        # «ПО ФАЙЛУ» — разницу тогда видно прямо в таблице, а не только
        # в подписи под заголовком.
        extra = 1 if self.reconciliation.ok else 2
        self.table.setRowCount(len(rows) + extra)  # последняя — «ИТОГО»
        for row, item in enumerate(rows):
            row_index = row  # в этой таблице строка = строка выписки
            match = self.matches[row_index] if self.store is not None else None
            self._set_cell(row, COL_DATE, item.date)
            self._set_cell(row, COL_UNP, item.unp)
            self._set_cell(row, COL_NAME, item.name)
            # v0.55.0: комиссия банка добавляется к поступлению и
            # показывается в оплате — её платим мы.
            self._set_cell(
                row, COL_OUT, self._amount(item.outgoing_shown), money=True
            )
            self._set_cell(
                row, COL_IN, self._amount(item.incoming_shown), money=True
            )
            # v0.63.0: «Куда» и «Уже в базе» — прямо в этой таблице,
            # второе окно для разноса больше не открывается.
            if match is not None:
                self._base_cell(row, item, match)
                self._target_cell(row, row_index, match)
            commission = (
                self.commissions[row_index]
                if self.store is not None
                else Match("", "")
            )
            text = match.note if match is not None else self._note_text(item)
            if commission.note:
                text = f"{text} · {commission.note}"
            if (
                self.store is not None
                and self._is_imported(row_index)
                and not self.redo
            ):
                text = f"{text} (уже разносили)"
            note = self._set_cell(row, COL_NOTE, text)
            if match is not None and row_is_blocked(match, commission):
                note.setForeground(QColor(styles.RED))
            if item.commission:
                self.table.item(row, COL_OUT).setToolTip(
                    "Комиссия банка: удержана из суммы, поэтому "
                    f"добавлена и к поступлению ({fmt_money_plain(item.commission)})"
                )
            if self._is_bank(item):
                self._mark_bank_row(row)
        self._fill_total_row(len(rows))
        if not self.reconciliation.ok:
            self._fill_file_row(len(rows) + 1)
        # Высоту строк не пересчитываем: она задана одна на все
        # (defaultSectionSize) — иначе Qt добавит запас, и таблица
        # выписки перестанет быть компактной.

    def _mark_bank_row(self, row: int) -> None:
        """Серым фоном и подсказкой отметить операции самого банка."""

        for column in range(len(COLUMNS)):
            cell = self.table.item(row, column)
            if cell is None:
                continue
            cell.setBackground(QColor(styles.WORKDAY_GREY))
            cell.setToolTip(
                "Операция банка, который ведёт счёт: не поставщик и не "
                "покупатель (карточка банка — в «Конфигурации»)"
            )

    def _fill_total_row(self, row: int) -> None:
        bold = styles.fs(12)
        check = self.reconciliation
        total = self._set_cell(row, COL_DATE, "ИТОГО")
        for column, value, expected in (
            (COL_OUT, self.statement.total_outgoing, check.expected_outgoing),
            (COL_IN, self.statement.total_incoming, check.expected_incoming),
        ):
            cell = self._set_cell(row, column, fmt_money_plain(value), money=True)
            font = cell.font()
            font.setBold(True)
            cell.setFont(font)
            # v0.66.0: ячейка, которая разошлась с файлом, — красная.
            if not check.ok and value != expected:
                cell.setForeground(QColor(styles.RED))
                cell.setToolTip(
                    "По файлу должно быть "
                    f"{fmt_money_plain(expected)} — разница "
                    f"{fmt_money_plain(value - expected)}"
                )
        font = total.font()
        font.setBold(True)
        font.setPointSize(bold)
        total.setFont(font)

    def _fill_file_row(self, row: int) -> None:
        """Строка «ПО ФАЙЛУ» — итоги, напечатанные в самой выписке.

        v0.66.0: появляется только при расхождении. Показывает, какое
        число ждал файл, — прямо под посчитанным итогом, чтобы разницу
        не приходилось искать в подписи. Комиссия банка добавлена к
        обеим суммам: в файле она удержана из суммы, а в строках
        показана сверху (v0.55.0), иначе сравнивать нельзя.
        """

        check = self.reconciliation
        total = self._set_cell(row, COL_DATE, "ПО ФАЙЛУ")
        for column, expected, value in (
            (COL_OUT, check.expected_outgoing, self.statement.total_outgoing),
            (COL_IN, check.expected_incoming, self.statement.total_incoming),
        ):
            cell = self._set_cell(
                row, column, fmt_money_plain(expected), money=True
            )
            font = cell.font()
            font.setBold(True)
            cell.setFont(font)
            if value != expected:
                cell.setForeground(QColor(styles.RED))
        font = total.font()
        font.setBold(True)
        total.setFont(font)
        total.setToolTip(
            "Итоги, напечатанные в файле (обороты плюс комиссия банка)"
        )

    def _set_cell(
        self, row: int, column: int, text: str, *, money: bool = False
    ) -> QTableWidgetItem:
        cell = QTableWidgetItem(text)
        if money:
            cell.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self.table.setItem(row, column, cell)
        return cell

    @staticmethod
    def _amount(value: Decimal) -> str:
        """Пусто вместо «0,00»: у операции бывает только одна сумма."""
        return fmt_money_plain(value) if value else ""

    # ------------------------------------------------- колонка «Куда»
    def _recalculate(self) -> None:
        """Пересчитать назначения строк по нынешней базе (v0.63.0).

        Базу поставщиков, покупателей и видов оплаты пользователь
        наполняет постепенно, поэтому после создания карточки всё
        считается заново.
        """

        self.matches = [
            classify(row, self.store.data, self.store.data.bank.unp)
            for row in self.statement.rows
        ]
        # Комиссия банка по строке — отдельная запись в оплатах.
        self.commissions = [
            classify_commission(row, self.store.data, self.store.data.bank.unp)
            for row in self.statement.rows
        ]
        self._refresh_planned()

    def _base_cell(self, index: int, row, match: Match) -> None:
        """«Уже в базе»: что записано за день и что станет (v0.57.0).

        Запись дня в базе одна, поэтому прежняя сумма **заменяется** —
        об этом пишем и в ячейке, и в подсказке.
        """

        date_iso = iso_date(row)
        current = base_amount(self.store.data, match, date_iso)
        result = self.planned.get(plan_key(row, match), Decimal("0"))
        if match.target in (TARGET_NEW_PAYMENT, TARGET_CHOOSE):
            # v0.61.0: пока получатель (или вид оплаты) не выбран,
            # писать некуда.
            text = "—"
        elif current == result:
            text = fmt_money_plain(current)
        else:
            text = f"{fmt_money_plain(current)} → {fmt_money_plain(result)}"
        cell = self._set_cell(index, COL_BASE, text)
        cell.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        if (
            match.target not in (TARGET_NEW_PAYMENT, TARGET_CHOOSE)
            and current
            and current != result
        ):
            # Расхождение: день в базе уже заполнен другой суммой.
            cell.setForeground(QColor(styles.RED))
        cell.setToolTip(
            f"За {date_iso} в базе: {fmt_money_plain(current)}.\n"
            f"После разноса станет: {fmt_money_plain(result)}\n"
            "Запись дня одна, прежняя сумма заменяется."
        )

    def _refresh_planned(self) -> None:
        """Пересчитать суммы, которые запишет разнос."""

        if self.store is None:
            return
        self.planned = plan_totals(
            self.statement, self.store.data, self.matches, self.commissions
        )

    def _refresh_cells(self) -> None:
        """Обновить колонки «Уже в базе» и «Примечание» (v0.63.0)."""

        for index, row_index in enumerate(self.visible):
            row = self.statement.rows[row_index]
            match = self.matches[row_index]
            self.table.takeItem(index, COL_BASE)
            self._base_cell(index, row, match)
            commission = self.commissions[row_index]
            text = match.note
            if commission.note:
                text = f"{text} · {commission.note}"
            cell = self.table.item(index, COL_NOTE)
            if cell is not None:
                cell.setText(text)

    def _is_imported(self, row_index: int) -> bool:
        """Строку уже разносили раньше? (v0.59.0)"""

        seen = set(getattr(self.store.data, "imported_statement_rows", []))
        return self.statement.rows[row_index].import_key() in seen

    def _target_cell(self, row: int, row_index: int, match: Match) -> None:
        """Список «Куда»: куда попадёт строка (v0.63.0)."""

        combo = QComboBox()
        combo.addItem("— не разносить —", "")
        if match.target == TARGET_CHOOSE:
            # v0.61.0: УНП подходит несколько карточек — показываем их
            # списком «Наименование · УНП · Примечание» и ждём выбора.
            # Первым стоит «выберите», чтобы программа не угадывала.
            combo.insertItem(
                1, f"— выберите ({len(match.options)}) —", TARGET_CHOOSE
            )
            for option in match.options:
                target, counterparty_id, payment_name, label = option
                combo.addItem(
                    label,
                    choose_option_data(
                        target, counterparty_id, payment_name
                    ),
                )
        for label, key in TARGET_CHOICES:
            combo.addItem(label, key)
        # Свой вид оплаты и «нужен новый вид» показываем подписью.
        if match.target == TARGET_OTHER_PAYMENT:
            combo.insertItem(1, match.payment_name, match.target)
        elif match.target == TARGET_NEW_PAYMENT:
            combo.insertItem(1, NEW_PAYMENT_CHOICE, match.target)
        index = combo.findData(match.target)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(
            lambda _value, r=row, i=row_index: self._target_changed(r, i)
        )
        # v0.63.0: список стоит в той же строке, что и данные, поэтому
        # он компактный — без «воздуха» по высоте.
        height = self.table.verticalHeader().defaultSectionSize()
        combo.setFixedHeight(height)
        # v0.68.0: раскрывающийся список шире самой колонки — иначе
        # подпись «Наименование · УНП · Примечание» обрезается.
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        combo.view().setMinimumWidth(TARGET_POPUP_WIDTH)
        combo.setStyleSheet(
            "QComboBox{padding:0px 4px;margin:0px;"
            f"font-size:{styles.fs(11)}px;}}"
        )
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(combo, stretch=1)
        # v0.57.0: рядом со списком — кнопка создания вида оплаты. Она
        # видна только тем строкам, которым вида не хватает.
        button = QPushButton("＋")
        button.setFixedWidth(26)
        button.setFixedHeight(height)
        button.setToolTip(
            "Создать вид оплаты: УНП и наименование возьмём из строки "
            "выписки"
        )
        button.clicked.connect(
            lambda _checked=False, r=row, i=row_index: self._create_payment(
                r, i
            )
        )
        layout.addWidget(button)
        self.table.setCellWidget(row, COL_TARGET, holder)
        self._sync_target_button(row, row_index)

    def _target_changed(self, row: int, row_index: int) -> None:
        """Ручная правка назначения: примечание подстраиваем под выбор."""

        combo = self._target_combo(row)
        target = combo.currentData() if combo is not None else ""
        row_data = self.statement.rows[row_index]
        match = self.matches[row_index]
        if self._apply_chosen_option(row, row_index, target):
            return
        match.options = []
        match.target = target
        match.counterparty_id = ""
        match.payment_name = ""
        name = row_data.name or row_data.unp
        unp = row_data.unp
        if target == TARGET_CARD:
            match.note = "Поступление по картам"
        elif target == TARGET_CASH:
            match.note = "Поступление наличными"
        elif target == TARGET_CUSTOMERS:
            customer = find_by_unp(self.store.data.customers, unp)
            match.counterparty_id = customer.id if customer else ""
            match.note = (
                f"Покупатель: {customer.name}"
                if customer
                else f"Покупатель с УНП {unp} не найден"
            )
        elif target == TARGET_SUPPLIERS:
            supplier = find_by_unp(self.store.data.suppliers, unp)
            match.counterparty_id = supplier.id if supplier else ""
            match.note = (
                f"Поставщик: {supplier.name}"
                if supplier
                else f"Поставщик с УНП {unp} не найден"
            )
        elif target == TARGET_OTHER_INCOME:
            match.note = OTHER_INCOME_ROW_TITLE
        elif target == TARGET_OTHER_PAYMENT:
            payment = find_payment_by_unp(self.store.data, unp)
            match.payment_name = payment.name if payment else ""
            match.note = (
                f"Вид оплаты: {payment.name}"
                if payment
                else f"Вид оплаты с УНП {unp} не найден"
            )
        else:
            match.note = f"Пропустить строку ({name})"
        self._set_note_text(row, match)
        self._sync_target_button(row, row_index)
        if combo is not None and combo.currentText() == NEW_PAYMENT_CHOICE:
            # Выбрали «Новый вид оплаты…» — сразу открываем карточку.
            self._create_payment(row, row_index)
            return
        self._refresh_planned()
        self._refresh_cells()

    def _apply_chosen_option(self, row: int, row_index: int, data) -> bool:
        """Выбор кандидата из списка (v0.61.0).

        Возвращает ``True``, если пункт был кандидатом (или «выберите»)
        и строка обработана: дальше общую обработку не продолжаем.
        """

        if not isinstance(data, str) or not (
            data == TARGET_CHOOSE or data.startswith("choose:")
        ):
            return False
        match = self.matches[row_index]
        row_data = self.statement.rows[row_index]
        name = row_data.name or row_data.unp
        if data == TARGET_CHOOSE:      # «— выберите (N) —»
            match.target = TARGET_CHOOSE
            match.counterparty_id = ""
            match.payment_name = ""
            match.note = (
                f"Уточните: УНП {row_data.unp or '—'} подходит "
                f"{len(match.options)} — выберите в колонке «Куда»"
            )
            self._set_note_text(row, match)
            self._sync_target_button(row, row_index)
            self._refresh_planned()
            self._refresh_cells()
            return True
        _prefix, target, counterparty_id, payment_name = data.split(":", 3)
        match.target = target
        match.counterparty_id = counterparty_id
        match.payment_name = payment_name
        match.options = []
        if payment_name:
            match.note = f"Вид оплаты: {payment_name}"
        else:
            person = self._person_by_id(counterparty_id)
            who = (
                "Покупатель"
                if match.target == TARGET_CUSTOMERS
                else "Поставщик"
            )
            match.note = (
                f"{who}: {person.name if person else name}"
                if person
                else f"{who} с УНП {row_data.unp} не найден"
            )
        self._set_note_text(row, match)
        self._sync_target_button(row, row_index)
        self._refresh_planned()
        self._refresh_cells()
        return True

    def _person_by_id(self, counterparty_id: str):
        for item in list(self.store.data.customers) + list(
            self.store.data.suppliers
        ):
            if item.id == counterparty_id:
                return item
        return None

    def _set_note_text(self, row: int, match: Match) -> None:
        cell = self.table.item(row, COL_NOTE)
        if cell is None:
            return
        commission = self.commissions[row]
        text = match.note
        if commission.note:
            text = f"{text} · {commission.note}"
        cell.setText(text)
        # Пустая кисть возвращает цвет по умолчанию: строка больше не
        # «неизвестная».
        blocked = row_is_blocked(match, commission)
        cell.setForeground(QColor(styles.RED) if blocked else QBrush())

    def _sync_target_button(self, row: int, row_index: int) -> None:
        """Показать кнопку «＋» строкам, которым не хватает вида оплаты."""

        holder = self.table.cellWidget(row, COL_TARGET)
        if holder is None:
            return
        button = holder.findChild(QPushButton)
        if button is None:
            return
        if self.matches[row_index].target == TARGET_CHOOSE:
            # v0.61.0: карточки с таким УНП уже есть — надо выбрать,
            # а не создавать новый вид оплаты.
            button.hide()
            return
        match = self.matches[row_index]
        commission = self.commissions[row_index]
        needed = (
            match.target == TARGET_NEW_PAYMENT
            or commission.target == TARGET_NEW_PAYMENT
            or (
                match.target == TARGET_OTHER_PAYMENT
                and not match.payment_name
            )
        )
        button.setVisible(needed)

    def _target_combo(self, row: int) -> QComboBox | None:
        widget = self.table.cellWidget(row, COL_TARGET)
        if widget is None:
            return None
        if isinstance(widget, QComboBox):
            return widget
        return widget.findChild(QComboBox)

    def _set_target_text(self, row: int, text: str, data: str) -> None:
        """Выбрать в списке «Куда» пункт с нужным значением."""

        combo = self._target_combo(row)
        if combo is None:
            return
        # Сигналы гасим на всё время правки: вставка пункта сама по себе
        # меняет текущий индекс и иначе дёрнула бы _target_changed.
        combo.blockSignals(True)
        try:
            index = combo.findData(data)
            if index < 0:
                combo.insertItem(1, text, data)
                index = combo.findData(data)
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    # ------------------------------------------- карточка вида оплаты
    def _create_payment(self, row: int, row_index: int) -> bool:
        """Создать вид оплаты для строки прямо из таблицы (v0.63.0)."""

        if self._creating_payment:
            return False
        self._creating_payment = True
        try:
            return self._create_payment_card(row, row_index)
        finally:
            self._creating_payment = False

    def _create_payment_card(self, row: int, row_index: int) -> bool:
        """Сама карточка: поля, сохранение, обновление строки."""

        from ..views.receipts_view import OtherPaymentCardDialog

        row_data = self.statement.rows[row_index]
        used = set(other_payment_names(self.store.data, OUTGOING))
        day = date.fromisoformat(iso_date(row_data))
        # Имя из выписки: «ООО "ОМА"» → то же, но без лишних пробелов.
        # v0.63.0: если вид с таким именем уже есть (одна организация
        # платит по нескольким назначениям — подоходный налог, страховые
        # взносы), предлагаем свободное имя с номером, иначе карточку
        # нельзя сохранить и второй вид не завести.
        suggested = free_payment_name(used, row_data.name or row_data.unp)
        # v0.62.0: УНП в строке мог не прочитаться (старая выписка, где
        # «УНП» и цифры попали на разные строки). Тогда предлагаем УНП
        # банка из «Конфигурации»: карточка без УНП строку не разнесёт.
        unp = row_data.unp or self.store.data.bank.unp
        dialog = OtherPaymentCardDialog(
            OUTGOING,
            used,
            day,
            self,
            name=suggested,
            unp=unp,
            # v0.63.0: примечание — смысл назначения платежа, чтобы виды
            # с одинаковым УНП различались не только номером.
            note=purpose_note(row_data.purpose),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        name = dialog.name_value
        add_other_payment(
            self.store.data,
            name,
            day.isoformat(),
            OUTGOING,
            dates=list(dialog.marked_dates),
            vat_rate=dialog.vat_value,
            unp=dialog.unp_value,
            # v0.63.0: примечание из назначения платежа — им виды одной
            # организации (один УНП) и различаются.
            note=dialog.note_value,
        )
        self.store.save()
        match = self.matches[row_index]
        if match.target == TARGET_NEW_PAYMENT:
            match.target = TARGET_OTHER_PAYMENT
        match.payment_name = name
        match.counterparty_id = ""
        match.note = f"Вид оплаты: {name}"
        self._set_target_text(row, name, TARGET_OTHER_PAYMENT)
        # Комиссия банка по строке теперь тоже знает, куда писать.
        self.commissions[row_index] = classify_commission(
            self.statement.rows[row_index],
            self.store.data,
            self.store.data.bank.unp,
        )
        self._set_note_text(row, match)
        self._sync_target_button(row, row_index)
        self._refresh_planned()
        self._refresh_cells()
        return True

    # --------------------------------------------------------------- кнопки
    def _continue(self) -> None:
        """Разнести выписку по базе прямо из этой таблицы (v0.63.0).

        Раньше кнопка открывала второе окно со своим списком «Куда»:
        приходилось смотреть в два места. Теперь назначение выбрано
        здесь же — осталось записать.
        """

        if self.store is None:
            QMessageBox.information(
                self,
                "Разнесение",
                "Разнесение будет доступно из раздела "
                "«Поступление и оплата».",
            )
            return
        redo = self._ask_about_repeat()
        if redo is None:  # пользователь отказался
            return
        self.redo = redo
        if not self._distribute():
            return
        result = self.result_distribution
        if self.on_imported is not None and result is not None:
            self.on_imported(result)
        self._report_distribution(result)
        if result is not None and result.rolled_unwritten:
            # v0.65.0: прежнюю запись сняли, новой нет. Окно не
            # закрываем: получателя надо выбрать здесь же, иначе сумма
            # пропадёт. Кнопка «Обновить» пересчитает таблицу.
            self._refresh_notes()
            return
        self.accept()

    def _distribute(self) -> bool:
        """Записать строки в базу; ``False`` — пользователь отменил."""

        # v0.65.0: «нет карточки» и «надо выбрать получателя» — разные
        # вещи, поэтому и говорим о них раздельно.
        waiting = [
            row
            for row, match, commission in zip(
                self.statement.rows, self.matches, self.commissions
            )
            if row_is_blocked(match, commission)
            and match.target == TARGET_CHOOSE
        ]
        unresolved = [
            row
            for row, match, commission in zip(
                self.statement.rows, self.matches, self.commissions
            )
            if row_is_blocked(match, commission)
            and match.target != TARGET_CHOOSE
        ]
        if waiting:
            names = "\n".join(
                f"• {row.name or row.unp} (УНП {row.unp})"
                for row in waiting[:6]
            )
            answer = QMessageBox.question(
                self,
                "Надо выбрать получателя",
                "Этим строкам подходит несколько карточек с одним УНП — "
                "программа не угадывает, куда писать:\n"
                f"{names}\n\n"
                "Выберите получателя в колонке «Куда» (каждая карточка "
                "подписана примечанием). Разнести остальные строки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        if unresolved:
            names = "\n".join(
                f"• {row.name or row.unp} (УНП {row.unp})"
                for row in unresolved[:6]
            )
            answer = QMessageBox.question(
                self,
                "Не хватает данных",
                "Для этих строк в базе нет контрагента или вида оплаты "
                f"с таким УНП:\n{names}\n\n"
                "Создайте карточки в «Конфигурации» и нажмите «Обновить» "
                "в окне выписки.\n\n"
                "Разнести остальные строки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        # v0.65.0: в разнос уходят **все** строки с назначением, даже
        # те, что пока записать нельзя (ждут выбора получателя): иначе
        # разнос их не увидит и не снимет прежнюю запись этой строки,
        # а она уже неверна (например, сумма прочиталась иначе).
        # distribute сам решит, что писать, а что пропустить.
        kept = [
            (row, match, commission)
            for row, match, commission in zip(
                self.statement.rows, self.matches, self.commissions
            )
            if match.target
        ]
        statement = replace(self.statement, rows=[row for row, _a, _b in kept])
        matches = [match for _row, match, _commission in kept]
        commissions = [commission for _row, _match, commission in kept]
        result = distribute(
            statement, self.store.data, matches, commissions, redo=self.redo
        )
        # v0.63.0: строки, которые разнос пропустил (не выбран получатель
        # или нет карточки), показываем в отчёте. Их нет в переданном
        # разнос списке, поэтому из самого результата они пропали бы, и
        # молча потерянная строка выглядела бы как «разнос не работает».
        result.unresolved = list(unresolved)
        result.needs_choice = list(waiting)
        # v0.59.0: при переразносе убираем «реализации», которые
        # создавали старые версии (0.56.0–0.57.0) вместо оплаты.
        if self.redo:
            self._drop_stale_realizations(result, statement, matches)
        self.result_distribution = result
        return True

    def _drop_stale_realizations(
        self, result, statement, matches: list[Match]
    ) -> None:
        """Снять ошибочные «реализации» прошлых версий (v0.59.0)."""

        stale = find_stale_realizations(
            self.store.data, statement, matches
        )
        if not stale:
            return
        names = "\n".join(
            "• "
            + f"{operation.date} — {fmt_amount(operation.amount)} "
            + f"({operation.kind})"
            for operation in stale[:8]
        )
        answer = QMessageBox.question(
            self,
            "Лишние «реализации»",
            "Эти операции «Реализация» создал прошлый разнос выписки: "
            "те же дата, покупатель и сумма, а теперь строка пишется "
            "оплатой (строки самой выписки при этом не удаляются):"
            f"\n{names}\n\nУбрать их?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for operation in stale:
            result.removed.append(
                f"{operation.date} — "
                f"{fmt_amount(operation.amount)} ({operation.kind})"
            )
        remove_operations(self.store.data, stale)

    def _ask_about_repeat(self) -> bool | None:
        """Спросить про уже разнесённый файл (v0.59.0).

        Возвращает ``True`` — переразнести целиком, ``False`` — только
        новые строки, ``None`` — отмена. Раньше в этой ситуации
        программа молча ничего не делала, и это выглядело как
        «разнос не работает».
        """

        seen = set(getattr(self.store.data, "imported_statement_rows", []))
        keys = [row.import_key() for row in self.statement.rows]
        old_count = sum(1 for key in keys if key in seen)
        if not old_count:
            return False
        new_count = len(keys) - old_count
        box = QMessageBox(self)
        box.setWindowTitle("Выписка уже разносилась")
        box.setText(
            f"Из {len(keys)} строк этой выписки {old_count} уже "
            "разносились раньше"
            + (f", новых — {new_count}.\n\n" if new_count else ".\n\n")
            + "«Переразнести» снимет прежние записи этих строк и запишет "
            "их заново по нынешним правилам: например, поступление "
            "покупателя станет оплатой, а не реализацией.\nСуммы, "
            "которые после разноса правили вручную, будут перезаписаны "
            "— в отчёте будет видно, какие именно и на что."
        )
        box.addButton("Переразнести", QMessageBox.ButtonRole.AcceptRole)
        if new_count:
            box.addButton(
                "Только новые", QMessageBox.ButtonRole.DestructiveRole
            )
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None:
            return None
        if clicked.text() == "Переразнести":
            return True
        if clicked.text() == "Только новые":
            return False
        return None

    def _report_distribution(self, result) -> None:
        """Сообщить итог разноса — всегда, даже когда писать нечего."""

        if result is None:
            return
        parts: list[str] = []
        # v0.66.0: расхождение с файлом — первым делом. Отчёт —
        # последнее, что видит человек перед тем, как суммы попадут в
        # базу: пропустить мимо глаз подпись под заголовком легко.
        if not self.reconciliation.ok:
            parts.append(
                "Внимание: итог строк не совпал с итогами файла — "
                "разнесены могут быть неверные суммы:"
            )
            parts.extend(f"• {text}" for text in self.reconciliation.problems)
            parts.append("")
        if result.applied:
            parts.append(f"Записано: {len(result.applied)}.")
            parts.extend(result.applied[:12])
        if result.replaced:
            parts.append("")
            parts.append(f"Снято прежних записей: {len(result.replaced)}.")
            parts.extend(result.replaced[:12])
        if result.removed:
            parts.append("")
            parts.append(
                f"Убрано лишних «реализаций»: {len(result.removed)}."
            )
            parts.extend(result.removed[:12])
        if result.overwritten:
            # v0.67.0: переразнос пишет поверх ручной правки — но
            # человек должен видеть, какие его цифры затёрты.
            parts.append("")
            parts.append(
                "Дни, которые правили вручную, перезаписаны: "
                f"{len(result.overwritten)}."
            )
            parts.extend(result.overwritten[:12])
        if result.restated:
            # v0.65.0: файл прочитан по-новому — прежние суммы этих
            # строк сняты, записаны нынешние.
            parts.append("")
            parts.append(
                "Строки разносились с другими суммами (файл прочитан "
                f"заново): {len(result.restated)}. Прежние записи сняты."
            )
            parts.extend(
                f"• {row.name or row.unp} — {fmt_amount(row.amount)}"
                for row in result.restated[:8]
            )
        if result.needs_choice:
            # v0.65.0: «надо выбрать получателя» — не то же, что «нет
            # карточки»: здесь карточек несколько, и выбирает человек.
            parts.append("")
            parts.append(
                "Надо выбрать получателя в колонке «Куда»: "
                f"{len(result.needs_choice)}."
            )
            parts.extend(
                f"• {row.name or row.unp} (УНП {row.unp}) — "
                f"{fmt_amount(row.amount)}"
                for row in result.needs_choice[:8]
            )
        if result.rolled_unwritten:
            parts.append("")
            parts.append(
                "Внимание: у этих строк снята прежняя запись, а новая "
                "пока не записана — выберите получателя и разнесите "
                "снова:"
            )
            parts.extend(
                f"• {row.name or row.unp} — {fmt_amount(row.amount)}"
                for row in result.rolled_unwritten[:8]
            )
        if result.unresolved:
            # v0.63.0: что не разнеслось — видно всегда, а не только
            # когда не записано ничего.
            parts.append("")
            parts.append(
                "Не разнесено (нет контрагента или вида оплаты с таким "
                f"УНП): {len(result.unresolved)}."
            )
            parts.extend(
                f"• {row.name or row.unp} (УНП {row.unp})"
                for row in result.unresolved[:8]
            )
        if result.warnings:
            parts.append("")
            parts.extend(result.warnings)
        if not parts:
            if result.already:
                parts.append(
                    "Новых данных нет: все строки этой выписки уже "
                    f"разносились ({len(result.already)})."
                )
            elif result.unresolved:
                parts.append(
                    "Ничего не записано: строкам не хватает контрагентов "
                    f"или видов оплаты ({len(result.unresolved)})."
                )
            else:
                parts.append("Разносить нечего.")
        if not self._needs_report(result):
            # v0.68.0: всё разнеслось — окно не нужно. Раньше отчёт
            # вставал после каждого разноса, и его приходилось
            # закрывать, чтобы вернуться к работе.
            return
        QMessageBox.information(
            self, "Разнесение выписки", "\n".join(parts)
        )

    def _needs_report(self, result) -> bool:
        """Надо ли показывать отчёт после разноса (v0.68.0).

        Показываем, только когда есть на что посмотреть: что-то не
        разнеслось, ждёт выбора получателя, прежняя запись снята без
        новой, итог разошёлся с файлом или ручную правку пришлось
        перезаписать. Если всё сошлось — молчим: человек и так видит
        результат в таблице.
        """

        if result is None:
            return False
        return bool(
            result.unresolved
            or result.needs_choice
            or result.rolled_unwritten
            or result.warnings
            or result.overwritten
            or result.removed
            or not self.reconciliation.ok
            # v0.68.0: ничего не записано — молчать нельзя, иначе
            # непонятно, сработал разнос или нет.
            or not result.applied
        )
