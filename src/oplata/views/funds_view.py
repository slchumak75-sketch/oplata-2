"""Журнал движения средств (v0.22.1, уточнён v0.23.1).

Запрос пользователя: «добавляем журнал движения средств, в котором
отображается остаток на счету, все поступления, все оплаты и итого
остаток». С v0.23.1 журнал показывается **за один день**: первая
строка — «Остаток на счёте» (на начало дня), затем все поступления
дня, затем все оплаты дня, последняя строка — «Итого остаток».

Остаток на начало дня берётся по правилам ``calc.funds_totals``,
поэтому вчерашний «Итого остаток» становится сегодняшним «Остатком на
счёте»: журнал и блок «Средства» «Сводки» не расходятся.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import calc, styles
from ..dates import fmt_date, fmt_money_plain
from ..storage import DataStore
from ..widgets import arrow_button, date_edit_value, make_date_edit, set_date_edit

# Журнал показывается за один день (v0.23.1).
MOVEMENT_DAYS = 1

_MONEY_ALIGN = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class FundsMovementDialog(QDialog):
    """Окно журнала: остаток, поступления, оплаты, итого остаток за день."""

    def __init__(
        self,
        store: DataStore,
        on_date: date,
        parent: QDialog | None = None,
        days: int = MOVEMENT_DAYS,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.days = days
        self.setWindowTitle("Журнал движения средств")
        # v0.23.4: окно раскрывается на всю высоту экрана (просьба
        # пользователя) — журнал за день бывает длинным.
        screen = QApplication.primaryScreen()
        height = 620
        if screen is not None:
            available = screen.availableGeometry().height()
            height = max(320, available - 24)
        self.resize(920, height)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        title = QLabel("Журнал движения средств")
        title.setStyleSheet(f"font-weight:800;font-size:{styles.fs(16)}px")
        root.addWidget(title)

        # --- строка даты: как в блоках «Поступление»/«Оплата» ---
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("Дата"))
        self.date_edit = make_date_edit(on_date)
        bar.addWidget(self.date_edit)
        today_btn = QPushButton("Сегодня")
        today_btn.clicked.connect(self.go_today)
        bar.addWidget(today_btn)
        prev_btn = arrow_button("←", "День назад", bold=True)
        prev_btn.clicked.connect(lambda: self.shift_date(-1))
        bar.addWidget(prev_btn)
        next_btn = arrow_button("→", "День вперёд", bold=True)
        next_btn.clicked.connect(lambda: self.shift_date(1))
        bar.addWidget(next_btn)
        bar.addStretch(1)
        root.addLayout(bar)

        self.period_label = QLabel("")
        self.period_label.setStyleSheet(f"color:{styles.MUTED}")
        root.addWidget(self.period_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Дата", "Наименование", "Поступление", "Оплата", "Остаток"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        # v0.23.4: вертикальный скроллинг — широкий и всегда видимый,
        # такой же, как в таблицах «Поставщики»/«Покупатели».
        self.table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.table.setStyleSheet(
            "QTableWidget{"
            f"background:#ffffff;border:1px solid {styles.BORDER};"
            "border-radius:8px;"
            f"gridline-color:{styles.TABLE_GRID}}}"
            + styles.WIDE_SCROLLBAR
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table, stretch=1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Закрыть")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        # Дата меняет журнал сразу.
        self.date_edit.dateChanged.connect(lambda *_: self.refresh())
        self.refresh()

    # ----------------------------------------------------------------- дата
    def current_date(self) -> date:
        return date_edit_value(self.date_edit)

    def set_date(self, day: date) -> None:
        set_date_edit(self.date_edit, day)

    def shift_date(self, days: int) -> None:
        self.set_date(self.current_date() + timedelta(days=days))

    def go_today(self) -> None:
        self.set_date(date.today())

    # ------------------------------------------------------------- расчёты
    def movement(self) -> calc.FundsMovement:
        return calc.funds_movement(self.store.data, self.current_date(), self.days)

    def refresh(self) -> None:
        movement = self.movement()
        day_text = (
            fmt_date(movement.start)
            if movement.start == movement.end
            else f"{fmt_date(movement.start)} – {fmt_date(movement.end)}"
        )
        self.period_label.setText(f"Движение средств за {day_text}")

        # Строки журнала: остаток, все поступления, все оплаты, итог.
        lines: list[tuple[str, str, object, object, object]] = [
            (
                fmt_date(movement.start),
                "Остаток на счёте",
                "",
                "",
                fmt_money_plain(movement.start_funds),
            )
        ]
        for entry in movement.rows:
            if entry.income:
                lines.append(
                    (
                        fmt_date(entry.day),
                        entry.name,
                        fmt_money_plain(entry.income),
                        "",
                        fmt_money_plain(entry.balance),
                    )
                )
        for entry in movement.rows:
            if entry.payment:
                lines.append(
                    (
                        fmt_date(entry.day),
                        entry.name,
                        "",
                        fmt_money_plain(entry.payment),
                        fmt_money_plain(entry.balance),
                    )
                )
        lines.append(
            (
                fmt_date(movement.end),
                "Итого остаток",
                fmt_money_plain(movement.income),
                fmt_money_plain(movement.payments),
                fmt_money_plain(movement.balance),
            )
        )

        table = self.table
        table.setRowCount(len(lines))
        table.clearSpans()
        for row, values in enumerate(lines):
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                if column >= 2:
                    item.setTextAlignment(_MONEY_ALIGN)
                if row == 0 or row == len(lines) - 1:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row, column, item)
