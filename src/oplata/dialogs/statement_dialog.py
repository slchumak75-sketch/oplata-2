"""Окно «Загрузить выписку» — таблица операций из PDF (v0.54.0).

Первый этап: **только показать** выписку таблицей, ничего не разносить.
Разнесение по «Поступлению и оплате» — отдельный (второй) этап, поэтому
кнопка «Продолжить» пока объясняет, что следующего шага ещё нет, и
окно не закрывает: данные не должны пропадать по неосторожности.

Столбцы: Дата · УНП · Наименование организации · Оплата · Поступление
(такой порядок просил пользователь: оплата перед поступлением).
"""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from ..statement import Statement
from ..widgets import green_button

COLUMNS = ("Дата", "УНП", "Наименование организации", "Оплата", "Поступление")
COL_DATE, COL_UNP, COL_NAME, COL_OUT, COL_IN = range(5)
# Ширины колонок: наименование тянется, остальные — фиксированные.
COLUMN_WIDTHS = ((COL_DATE, 130), (COL_UNP, 130), (COL_OUT, 150), (COL_IN, 150))


class StatementDialog(QDialog):
    """Таблица операций выписки с кнопками «Продолжить» и «Отмена»."""

    def __init__(
        self,
        statement: Statement,
        source_name: str = "",
        parent: QWidget | None = None,
        bank_unp: str = "",
    ) -> None:
        super().__init__(parent)
        self.statement = statement
        # v0.54.0: УНП обслуживающего банка — из карточки в «Конфигурации».
        # Его строки в выписке не поставщик и не покупатель.
        self.bank_unp = (bank_unp or "").strip()
        self.setWindowTitle("Выписка из банка")
        self.setModal(True)
        self.resize(1180, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("🧾  Выписка из банка")
        title.setStyleSheet(f"font-size:{styles.fs(18)}px;font-weight:800")
        root.addWidget(title)

        root.addWidget(QLabel(self._caption(source_name)))

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
        root.addWidget(self.table, stretch=1)

        self._fill_table()

        # --- кнопки ---
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)

        self.continue_btn = green_button("Продолжить")
        self.continue_btn.setToolTip(
            "Разнесение данных по «Поступлению и оплате» — второй этап"
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
        # Сверка: если обороты файла сошлись с суммой строк, это видно
        # сразу — значит файл прочитан целиком, без потерь. Комиссия
        # банка попадает и в оплату, и в поступление (v0.55.0), поэтому
        # ожидаемый оборот больше на сумму комиссий.
        commission = statement.total_commission
        matched = (
            statement.total_outgoing
            == statement.turnover_outgoing + commission
            and statement.total_incoming
            == statement.turnover_incoming + commission
        )
        if matched and commission:
            tail = (
                "Обороты сошлись с итогом файла "
                f"(с учётом комиссий банка: {fmt_money_plain(commission)})."
            )
        elif matched:
            tail = "Обороты сошлись с итогом файла."
        else:
            tail = "Внимание: итог строк не совпал с оборотами файла."
        bank_rows = sum(1 for row in statement.rows if self._is_bank(row))
        if bank_rows:
            tail += (
                f" Строк банка (не поставщик и не покупатель): "
                f"{bank_rows}."
            )
        return (
            f"{head}\n"
            f"Входящее сальдо {fmt_money_plain(statement.opening_balance)} · "
            f"Оплата {fmt_money_plain(statement.total_outgoing)} · "
            f"Поступление {fmt_money_plain(statement.total_incoming)} · "
            f"Исходящее сальдо {fmt_money_plain(statement.closing_balance)}. "
            f"{tail}"
        )

    def _is_bank(self, row) -> bool:
        """Строка принадлежит банку из карточки «Конфигурации» (v0.54.0)."""
        return bool(self.bank_unp) and row.unp == self.bank_unp

    def _fill_table(self) -> None:
        rows = self.statement.rows
        self.table.setRowCount(len(rows) + 1)  # последняя строка — «ИТОГО»
        for row, item in enumerate(rows):
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
            if item.commission:
                self.table.item(row, COL_OUT).setToolTip(
                    "Комиссия банка: удержана из суммы, поэтому "
                    f"добавлена и к поступлению ({fmt_money_plain(item.commission)})"
                )
            if self._is_bank(item):
                self._mark_bank_row(row)
        self._fill_total_row(len(rows))
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
        total = self._set_cell(row, COL_DATE, "ИТОГО")
        for column, value in (
            (COL_OUT, self.statement.total_outgoing),
            (COL_IN, self.statement.total_incoming),
        ):
            cell = self._set_cell(row, column, fmt_money_plain(value), money=True)
            font = cell.font()
            font.setBold(True)
            cell.setFont(font)
        font = total.font()
        font.setBold(True)
        font.setPointSize(bold)
        total.setFont(font)

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

    # --------------------------------------------------------------- кнопки
    def _continue(self) -> None:
        """Разнесение — следующий этап; окно остаётся открытым."""

        QMessageBox.information(
            self,
            "Продолжение",
            "Таблица выписки готова.\n\n"
            "Разнесение данных по разделу «Поступление и оплата» — "
            "второй этап, он делается отдельно.",
        )
