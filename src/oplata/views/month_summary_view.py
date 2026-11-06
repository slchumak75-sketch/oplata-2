"""Общее окно сводки «по месяцам, кварталам и году» (v0.32.0).

Окно одно, данных два: оплат и поступлений. Всё, кроме источника
строк, совпадает — таблица, выбор года, ширины колонок, — поэтому
общий класс лежит здесь, а ``PaymentsSummaryDialog`` и
``IncomingSummaryDialog`` задают только заголовок и расчёт.

Раскладка колонок (просьба пользователя v0.32.0):

* колонка «Вид» — **узкая**, ровно по названию, без растяжки;
* все числовые колонки (12 месяцев, 4 квартала, «Всего за год»)
  делят оставшуюся ширину **поровну**;
* минимальная ширина числовой колонки — 92 px: на узком экране
  появляется горизонтальная прокрутка, а не обрезанные цифры.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
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

from .. import calc, styles
from ..dates import fmt_money_plain
from ..storage import BASE_TITLES, DataStore

_MONEY_ALIGN = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
# Числовой колонке хватает места на «1 000 000,00».
MIN_NUMBER_COLUMN = 92
# Год выбирается в окне: от условного начала ведения базы до далёкого
# будущего — чтобы год можно было и нажать, и вписать руками.
MIN_YEAR = 2000
MAX_YEAR = 2100


class MonthSummaryDialog(QDialog):
    """Окно сводки: строки — виды, столбцы — месяцы, кварталы, год."""

    # Заголовок окна, слово в подписи («Оплаты за … год») и подпись
    # первой колонки.
    TITLE = ""
    PERIOD_WORD = ""
    KIND_COLUMN_TITLE = ""

    def __init__(
        self,
        store: DataStore,
        on_date: date,
        parent: QDialog | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        # Базу показываем в заголовке: таблица считается по активной
        # базе, а их две — МИР и КОРЕЛИЧИ.
        self.base_title = BASE_TITLES.get(store.base_key, "")
        self.setWindowTitle(
            f"{self.TITLE} — {self.base_title}" if self.base_title else self.TITLE
        )
        # v0.32.0: окно раскрывается на весь экран — 18 колонок на
        # обычном окне не помещались. Размер оставлен запасным: если
        # система не применит развёрнутое состояние, окно не будет
        # мелким.
        self.resize(1400, 760)
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

        # --- таблица: вид, 12 месяцев, 4 квартала, всего ---
        self.headers = self._headers()
        self.table = QTableWidget(0, len(self.headers))
        # v0.26.5: без фокуса — без рамки на нажатой ячейке.
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.table.setStyleSheet(
            "QTableWidget{"
            f"background:#ffffff;border:1px solid {styles.BORDER};"
            "border-radius:8px;"
            f"gridline-color:{styles.TABLE_GRID}}}"
            + styles.WIDE_SCROLLBAR
        )
        self._setup_columns()
        root.addWidget(self.table, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        # Год меняет таблицу сразу.
        self.year_spin.valueChanged.connect(lambda *_: self.refresh())
        self.refresh()

    # ------------------------------------------------------------------ вид
    def _headers(self) -> list[str]:
        return (
            [self.KIND_COLUMN_TITLE]
            + list(calc.MONTH_TITLES)
            + list(calc.QUARTER_TITLES)
            + ["Всего за год"]
        )

    def _setup_columns(self) -> None:
        """Первая колонка — по названию, остальные делят ширину поровну."""

        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(MIN_NUMBER_COLUMN)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, len(self.headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)

    # ------------------------------------------------------------------ год
    def current_year(self) -> int:
        return int(self.year_spin.value())

    def set_year(self, year: int) -> None:
        self.year_spin.setValue(year)

    def go_this_year(self) -> None:
        self.set_year(date.today().year)

    # ------------------------------------------------------------- расчёты
    def summary(self) -> calc.PaymentsSummary:  # pragma: no cover - переопределяется
        raise NotImplementedError

    def refresh(self) -> None:
        summary = self.summary()
        period = f"{self.PERIOD_WORD} за {summary.year} год"
        self.period_label.setText(
            f"{period} · база {self.base_title}" if self.base_title else period
        )

        rows = list(summary.rows) + [summary.total]
        table = self.table
        table.setRowCount(len(rows))
        for row, line in enumerate(rows):
            bold = row == len(rows) - 1  # строка «Всего»
            values = (line.name,) + tuple(line.months) + tuple(line.quarters)
            values += (line.total,)
            for column, amount in enumerate(values):
                text = amount if column == 0 else _money(amount)
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                if column > 0:
                    item.setTextAlignment(_MONEY_ALIGN)
                if bold:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row, column, item)


def _money(amount: object) -> str:
    """Сумма в виде текста; ноль показываем пустым (таблица читается)."""

    return fmt_money_plain(amount) if amount else ""
