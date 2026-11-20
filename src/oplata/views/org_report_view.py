"""Окно «Отчет» — «Отчет по организации» (v0.85.0).

Запрос пользователя: кнопка «Отчет» внизу плашки «Сводка»; таблица — по
образцу файла «ОТЧЕТ ПО ОРГАНИЗАЦИИ мир 2.xlsx» (жёлтым в файле
выделены затраты, справочно указан НДС 0/20/25).

Считает ``report.organization_report``, здесь — только показ и ввод.
Четыре строки приходят из бухгалтерии и вводятся прямо в клетку
(двойной клик или Enter на клетке): «НДС Реализация (Накладные)»,
«Товары реализация без НДС», «Входящий НДС», «Себестоимость реализация
без НДС». Введённое сразу сохраняется в данные и пересчитывает таблицу.
Выгрузки и печати нет — по просьбе пользователя это только окно.

Столбцы — как в файле: название, справочная ставка НДС, четыре квартала
и «Итого». Строки затрат — жёлтые, как в файле.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import report as report_calc
from .. import styles
from ..dates import fmt_money_plain, fmt_vat_rate, parse_money
from ..storage import BASE_TITLES, DataStore

# Годы, между которыми можно листать (как в «Сводке оплат»).
MIN_YEAR = 2000
MAX_YEAR = 2100

# Общие названия столбцов.
TITLE_COLUMN = "Наименование"
VAT_COLUMN = "НДС"
QUARTER_COLUMNS = ("1кв", "2кв", "3кв", "4кв")
TOTAL_COLUMN = "Итого"

# Жёлтая заливка строк затрат — как в файле «ОТЧЕТ ПО ОРГАНИЗАЦИИ».
EXPENSE_BG = QColor("#fff6d5")
# Клетка, которую можно заполнить руками, чуть светлее остальных.
MANUAL_BG = QColor("#f4f9ff")

_MONEY_ALIGN = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class OrganizationReportDialog(QDialog):
    """Окно «Отчет»: итоги по организации по кварталам."""

    TITLE = "Отчет"

    def __init__(
        self,
        store: DataStore,
        on_date: date,
        parent: QDialog | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_date = on_date
        # Базу показываем в заголовке: цифры считаются по активной базе.
        self.base_title = BASE_TITLES.get(store.base_key, "")
        self.setWindowTitle(
            f"{self.TITLE} — {self.base_title}" if self.base_title else self.TITLE
        )
        self.resize(1100, 780)
        self.setWindowState(Qt.WindowState.WindowMaximized)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel(self.TITLE)
        title.setStyleSheet(f"font-weight:800;font-size:{styles.fs(16)}px")
        root.addWidget(title)

        # --- выбор года ---
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("Год"))
        self.year_spin = QSpinBox()
        self.year_spin.setRange(MIN_YEAR, MAX_YEAR)
        self.year_spin.setValue(on_date.year)
        self.year_spin.setFixedWidth(110)
        bar.addWidget(self.year_spin)
        this_year = QPushButton("Текущий год")
        this_year.clicked.connect(self.go_this_year)
        bar.addWidget(this_year)
        bar.addStretch(1)
        root.addLayout(bar)

        self.period_label = QLabel("")
        self.period_label.setStyleSheet(f"color:{styles.MUTED}")
        root.addWidget(self.period_label)

        # --- таблица: строка, НДС, четыре квартала, «Итого» ---
        self.headers = (
            [TITLE_COLUMN, VAT_COLUMN] + list(QUARTER_COLUMNS) + [TOTAL_COLUMN]
        )
        self.table = QTableWidget(0, len(self.headers))
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.setStyleSheet(
            "QTableWidget{"
            f"background:{styles.PANEL_BG};border:1px solid {styles.BORDER};"
            "border-radius:8px;"
            f"gridline-color:{styles.TABLE_GRID}}}"
            + styles.WIDE_SCROLLBAR
        )
        self._setup_columns()
        root.addWidget(self.table, stretch=1)

        hint = QLabel(
            "Строки «НДС Реализация (Накладные)», «Товары реализация без "
            "НДС», «Входящий НДС» и «Себестоимость реализация без НДС» "
            "заполняются из бухгалтерии — двойной клик по клетке."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{styles.MUTED};font-size:{styles.fs(10)}px")
        root.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        # Пустой текст в клетке — «не заполнено», а не ошибка ввода:
        # проверяем только при уходе из клетки.
        self.table.itemChanged.connect(self._on_item_changed)
        self.year_spin.valueChanged.connect(lambda *_: self.refresh())
        self.refresh()

    # ------------------------------------------------------------------ вид
    def _setup_columns(self) -> None:
        """Название и НДС — по содержимому, суммы делят остаток."""

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 60)
        for column in range(2, len(self.headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)

    # ------------------------------------------------------------------ год
    def current_year(self) -> int:
        return int(self.year_spin.value())

    def set_year(self, year: int) -> None:
        self.year_spin.setValue(year)

    def go_this_year(self) -> None:
        self.set_year(date.today().year)

    # ------------------------------------------------------------- расчёты
    def report(self) -> report_calc.OrganizationReportData:
        """Таблица за выбранный год (считает ``report``)."""

        return report_calc.organization_report(self.store.data, self.current_year())

    def refresh(self) -> None:
        self._updating = getattr(self, "_updating", False)
        data = self.report()
        period = f"Отчет за {data.year} год"
        self.period_label.setText(
            f"{period} · база {self.base_title}" if self.base_title else period
        )

        self._updating = True
        table = self.table
        table.setRowCount(len(data.rows))
        for row, line in enumerate(data.rows):
            self._fill_row(row, line)
        self._updating = False

    def _fill_row(self, row: int, line: report_calc.ReportRow) -> None:
        """Заполнить строку таблицы значениями строки отчёта."""

        table = self.table
        title = QTableWidgetItem(line.title)
        title.setFlags(Qt.ItemFlag.NoItemFlags)
        if line.expense:
            title.setBackground(EXPENSE_BG)
        if line.bold:
            title.setFont(_bold(title.font()))
        table.setItem(row, 0, title)

        rate = QTableWidgetItem(fmt_vat_rate(line.vat_rate))
        rate.setFlags(Qt.ItemFlag.NoItemFlags)
        rate.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if line.expense:
            rate.setBackground(EXPENSE_BG)
        if line.bold:
            rate.setFont(_bold(rate.font()))
        table.setItem(row, 1, rate)

        values = list(line.quarters) + [line.total]
        for index, amount in enumerate(values):
            self._fill_amount(row, 2 + index, line, index, amount)

    def _fill_amount(
        self,
        row: int,
        column: int,
        line: report_calc.ReportRow,
        index: int,
        amount,
    ) -> None:
        """Клетка суммы: у ручных строк — правится, у остальных — нет."""

        editable = line.manual and index < 4
        # Ручную строку показываем так, как её ввели (пусто = пусто):
        # иначе пустая клетка выглядела бы заполненной нулём.
        if editable:
            text = _manual_text(
                self.store.data.org_report.quarter(
                    self.current_year(), line.key, index
                )
            )
        else:
            text = fmt_money_plain(amount)
        item = QTableWidgetItem(text)
        if editable:
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
            )
        else:
            item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setTextAlignment(_MONEY_ALIGN)
        if line.expense:
            item.setBackground(EXPENSE_BG)
        elif editable:
            item.setBackground(MANUAL_BG)
        if line.bold:
            item.setFont(_bold(item.font()))
        self.table.setItem(row, column, item)

    # ------------------------------------------------------------------ ввод
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Пользователь поправил клетку ручной строки — запомнить."""

        self._updating = getattr(self, "_updating", False)
        if self._updating:
            return
        row = item.row()
        column = item.column()
        if column < 2 or column > 5:
            return
        line = self.report().rows[row]
        if not line.manual:
            return
        text = item.text().strip()
        if text:
            try:
                parse_money(text)
            except ValueError:
                # Мусор в клетке: возвращаем прежнее значение, не портя
                # данные (сумма не должна пропасть из-за опечатки).
                self._updating = True
                self.refresh()
                return
        self.store.data.org_report.set_quarter(
            self.current_year(), line.key, column - 2, text
        )
        self.store.save()
        self.refresh()


def _bold(font):
    font.setBold(True)
    return font


def _manual_text(text: str) -> str:
    """Ручное значение в том же виде, что и остальные суммы."""

    if not str(text or "").strip():
        return ""
    try:
        amount = parse_money(text)
    except ValueError:
        return ""
    return fmt_money_plain(amount) if amount is not None else ""


__all__ = ["OrganizationReportDialog"]
