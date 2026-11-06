"""Сводка оплат: помесячно, поквартально и за год (v0.31.0).

Запрос пользователя: под плашкой «Сводка» — кнопка «Сводка оплат»,
за ней — сводная таблица оплат **помесячно и за квартал**.

Считаем исходящие оплаты активной базы (МИР или КОРЕЛИЧИ — та, что
открыта переключателем в шапке):

* строка «Поставщики» — все операции вида «Оплата»;
* по строке на каждый прочий вид оплаты (как в блоке «Оплата»);
* строка «Всего» — итог по месяцу, кварталу и году.

Формулы — в ``calc.payments_summary``; здесь только показ.
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
# Год выбирается в окне: от условного начала ведения базы до далёкого
# будущего — чтобы на год можно было и нажать, и вписать руками.
MIN_YEAR = 2000
MAX_YEAR = 2100


class PaymentsSummaryDialog(QDialog):
    """Окно «Сводка оплат»: виды оплат × месяцы, кварталы, год."""

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
            f"Сводка оплат — {self.base_title}"
            if self.base_title
            else "Сводка оплат"
        )
        self.resize(1240, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("Сводка оплат")
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

        # --- таблица: вид оплаты, 12 месяцев, 4 квартала, всего ---
        headers = ["Вид оплаты"] + list(calc.MONTH_TITLES)
        headers += list(calc.QUARTER_TITLES)
        headers.append("Всего за год")
        self.table = QTableWidget(0, len(headers))
        # v0.26.5: без фокуса — без рамки на нажатой ячейке.
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setHorizontalHeaderLabels(headers)
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
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
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

    # ------------------------------------------------------------------ год
    def current_year(self) -> int:
        return int(self.year_spin.value())

    def set_year(self, year: int) -> None:
        self.year_spin.setValue(year)

    def go_this_year(self) -> None:
        self.set_year(date.today().year)

    # ------------------------------------------------------------- расчёты
    def summary(self) -> calc.PaymentsSummary:
        return calc.payments_summary(self.store.data, self.current_year())

    def refresh(self) -> None:
        summary = self.summary()
        period = f"Оплаты за {summary.year} год"
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
