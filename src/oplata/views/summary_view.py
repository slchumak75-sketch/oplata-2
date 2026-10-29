"""Плашка «Сводка» (справа на вкладке «Поступление и оплаты»).

Состоит из трёх блоков, все значения идут **построчно** (v0.14.0):

* «Средства» — остаток на счету, итого поступление, итого оплаты,
  итого остаток;
* «Задолженность поставщикам» — всего, на неделе, на сегодня,
  просрочено и оплата понедельно на три недели вперёд;
* «Месяц» — приход и оплаты с начала месяца.

Цифры считает ``calc`` простыми суммами по сохранённым данным; долг
берётся архивным графиком сроков (оплата долг не уменьшает —
требование v0.3.1). Печать не добавляет формул: она оформляет ровно
тот снимок значений, который показан на экране после ``refresh()``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import calc, styles
from ..dates import fmt_money_plain
from ..storage import DataStore
from ..widgets import CollapsiblePanel, date_edit_value, make_date_edit

# Сколько недель вперёд показывать в блоке «Оплата понедельно»
# (v0.15.0: четыре недели — следующая, ещё две и четвёртая).
PLAN_WEEKS = 4


class SummaryView(QWidget):
    def __init__(
        self,
        store: DataStore,
        on_open_supplier: Callable[[str | None], None],
        parent: QWidget | None = None,
        on_date_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_open_supplier = on_open_supplier
        # v0.22.1: дата сводки и даты блоков «Поступление»/«Оплата»
        # идут вместе — смена даты здесь переставляет и их.
        self.on_date_changed = on_date_changed

        # Подписи и значения строк: ключ -> (подпись, значение).
        self._captions: dict[str, QLabel] = {}
        self._values: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # --- шапка в две строки (v0.22.1): панель узкая, поэтому
        # дата стоит отдельной строкой, а переключатели — под ней ---
        top = QHBoxLayout()
        # v0.23.1: шрифт шапки «Сводка на дату» — в полтора раза крупнее
        # основного (запрос пользователя).
        date_label = QLabel("Сводка на дату")
        date_label.setStyleSheet(
            f"font-weight:700;font-size:{styles.fs(20)}px"
        )
        top.addWidget(date_label)
        self.date_edit = make_date_edit(date.today())
        self.date_edit.setStyleSheet(f"font-size:{styles.fs(20)}px")
        self.date_edit.dateChanged.connect(self._on_date_changed)
        top.addWidget(self.date_edit)
        top.addStretch(1)
        root.addLayout(top)

        # Вторая строка шапки: архивные и журнал движения средств.
        tools = QHBoxLayout()
        self.show_archived = QCheckBox("показывать архивных")
        # По умолчанию галочка стоит (v0.23.1): архивные контрагенты
        # сразу видны в «Сводке».
        self.show_archived.setChecked(True)
        self.show_archived.toggled.connect(lambda *_: self.refresh())
        tools.addWidget(self.show_archived)
        tools.addStretch(1)
        self.movement_button = QPushButton("Журнал движения средств")
        self.movement_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.movement_button.setToolTip(
            "Поступления и оплаты за последние 30 дней с остатком по каждой "
            "записи"
        )
        self.movement_button.clicked.connect(self._open_funds_movement)
        tools.addWidget(self.movement_button)
        root.addLayout(tools)

        # --- плашка со всеми строками ---
        self.panel = CollapsiblePanel("Сводка", expanded=True)
        # Заголовок «Сводка» — тем же шрифтом, что и заголовки блоков
        # «Поступление»/«Оплата» (v0.15.0); подзаголовки блоков внутри
        # мельче (см. ``_add_section``).
        self.panel.toggle.setStyleSheet(
            "QPushButton{background:transparent;border:none;text-align:left;"
            f"padding:2px 0;font-weight:800;font-size:{styles.fs(15)}px;"
            f"color:{styles.TEXT}}}"
            "QPushButton:hover{background:transparent}"
        )
        content = self.panel.content_layout
        content.setSpacing(8)

        self._add_section(
            content,
            "СРЕДСТВА",
            [
                ("funds", "Остаток на счёте"),
                ("income", "Поступление за день"),
                ("payments", "Оплаты за день"),
                ("balance", "Итого остаток"),
            ],
        )
        self._separator(content)

        self._add_section(
            content,
            "ЗАДОЛЖЕННОСТЬ ПОСТАВЩИКАМ",
            [
                ("debt_total", "Всего задолженность"),
                ("debt_week", "Задолженность на неделе"),
                # Строки «Задолженность на сегодня» больше нет
                # (v0.22.1): в таблице поставщиков вместо столбца
                # «на сегодня» стоит столбец «Итого» (общая текущая
                # задолженность), отдельная строка не нужна.
                ("debt_late", "Просроченный платеж"),
            ]
            + [
                (f"plan_{index}", f"Оплата неделя {index}")
                for index in range(1, PLAN_WEEKS + 1)
            ],
        )
        self._separator(content)

        self._add_section(
            content,
            "ЗАДОЛЖЕННОСТЬ ПОКУПАТЕЛЕЙ",
            [
                ("cust_total", "Задолженность покупателей"),
            ],
        )
        self._separator(content)

        self._add_section(
            content,
            "МЕСЯЦ",
            [
                ("month_income", "Приход с начала месяца"),
                ("month_payments", "Оплаты с начала месяца"),
            ],
        )
        root.addWidget(self.panel)
        root.addStretch(1)

        self.refresh()

    # ------------------------------------------------------------------ вид
    def _separator(self, layout: QVBoxLayout) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("Muted")
        line.setStyleSheet(f"color:{styles.BORDER}")
        layout.addWidget(line)

    def _add_section(
        self,
        layout: QVBoxLayout,
        title: str,
        rows: list[tuple[str, str]],
    ) -> None:
        """Блок строк: жирный подзаголовок и сетка «подпись — значение»."""

        caption = QLabel(title)
        caption.setStyleSheet(
            f"font-weight:800;font-size:{styles.fs(12)}px;color:{styles.TEXT}"
        )
        layout.addWidget(caption)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        for row, (key, text) in enumerate(rows):
            bold = key in {"balance"}
            label = QLabel(text)
            label.setStyleSheet(
                f"font-size:{styles.fs(12)}px;"
                f"{'font-weight:800;' if bold else ''}"
            )
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            # v0.22.0: панель «Сводка» вдвое у́же, поэтому длинные
            # подписи («Задолженность покупателей») переносятся на
            # вторую строку, а не обрезаются.
            label.setWordWrap(True)
            value = QLabel(fmt_money_plain(Decimal("0")))
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            value.setStyleSheet(
                f"font-size:{styles.fs(12)}px;"
                f"{'font-weight:800;' if bold else ''}"
            )
            self._captions[key] = label
            self._values[key] = value
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
        grid.setColumnStretch(0, 1)
        layout.addLayout(grid)

    def value_text(self, key: str) -> str:
        """Показанное значение строки (удобно для тестов)."""

        return self._values[key].text()

    def caption_text(self, key: str) -> str:
        return self._captions[key].text()

    # --------------------------------------------------------------- журнал
    def _open_funds_movement(self) -> None:
        """Открыть журнал движения средств за 30 дней (v0.22.1)."""

        from .funds_view import FundsMovementDialog

        dialog = FundsMovementDialog(self.store, self.current_date(), self)
        dialog.exec()

    # ----------------------------------------------------------------- дата
    def current_date(self) -> date:
        return date_edit_value(self.date_edit)

    def set_date(self, day: date) -> None:
        """Задать дату сводки (без повторного оповещения блоков)."""

        self.date_edit.blockSignals(True)
        self.date_edit.setDate(QDate(day.year, day.month, day.day))
        self.date_edit.blockSignals(False)
        self.refresh()

    def _on_date_changed(self) -> None:
        self.refresh()
        if self.on_date_changed is not None:
            self.on_date_changed()

    # -------------------------------------------------------------- расчёты
    def _set_value(self, key: str, amount: Decimal) -> None:
        self._values[key].setText(fmt_money_plain(amount))

    def refresh(self) -> None:
        on_date = date_edit_value(self.date_edit)
        include_archived = self.show_archived.isChecked()
        data = self.store.data

        # v0.22.1: блок «Средства» считается на выбранную дату: остаток
        # на начало дня + поступления дня − оплаты дня.
        funds = calc.funds_totals(data, on_date)
        debt = calc.debt_totals(data, on_date, include_archived)
        plan = calc.weekly_payment_plan(
            data, on_date, PLAN_WEEKS, include_archived
        )
        customers = calc.customer_debt_totals(data, on_date, include_archived)
        _ = customers.week  # «за неделю» в блоке не показывается
        month = calc.month_totals(data, on_date)

        self._set_value("funds", funds.funds)
        self._set_value("income", funds.income)
        self._set_value("payments", funds.payments)
        self._set_value("balance", funds.balance)
        self._set_value("debt_total", debt.total)
        self._set_value("debt_week", debt.week)
        self._set_value("debt_late", debt.late)
        for index, week in enumerate(plan, start=1):
            key = f"plan_{index}"
            self._values[key].setText(fmt_money_plain(week.amount))
            self._captions[key].setText(
                f"Оплата {week.monday.strftime('%d.%m')}"
                f" – {week.sunday.strftime('%d.%m')}"
            )
        self._set_value("cust_total", customers.total)
        self._set_value("month_income", month.income)
        self._set_value("month_payments", month.payments)
