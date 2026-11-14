"""Плашка «Сводка» (справа на вкладке «Поступление и оплаты»).

Состоит из трёх блоков, все значения идут **построчно** (v0.14.0):

* «Средства» — остаток на счету, итого поступление, итого оплаты,
  итого остаток;
* «Задолженность поставщикам» — всего, на неделе, на сегодня,
  просрочено и оплата понедельно на три недели вперёд;
* «Месяц» — итоги реестра «Отчеты ККМ» за весь месяц (v0.43.0).

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
from ..widgets import (
    CollapsiblePanel,
    arrow_button,
    date_edit_value,
    make_date_edit,
)

# v0.71.0: стрелки в шапке «Сводки» у́же обычных (44 px вместо 52):
# плашка занимает пятую часть окна, и лишние 16 px в ней дороги.
SUMMARY_ARROW_WIDTH = 44

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
            f"font-weight:700;font-size:{styles.fs(14)}px"
        )
        top.addWidget(date_label)
        top.addStretch(1)
        top.setSpacing(4)
        root.addLayout(top)

        # v0.70.0: стрелки «←»/«→» листают дату сводки на день назад и
        # вперёд — как стрелки недель в блоках «Поступление» и «Оплата».
        # Раньше дату можно было только открыть календарём и выбрать.
        #
        # v0.71.0: стрелки и дата ушли на **отдельную строку**. В одну
        # строку они не влезали: подпись, две стрелки и поле даты
        # требуют 514 px, а плашка — 276…392 px в зависимости от экрана.
        # Раскладка сжимала виджеты ниже их минимума, и они наползали
        # друг на друга.
        date_row = QHBoxLayout()
        date_row.setSpacing(4)
        self.prev_btn = arrow_button("←", "День назад")
        self.prev_btn.setFixedWidth(SUMMARY_ARROW_WIDTH)
        self.prev_btn.clicked.connect(lambda: self._shift_date(-1))
        date_row.addWidget(self.prev_btn)
        self.date_edit = make_date_edit(date.today())
        self.date_edit.setStyleSheet(f"font-size:{styles.fs(12)}px")
        self.date_edit.dateChanged.connect(self._on_date_changed)
        date_row.addWidget(self.date_edit)
        self.next_btn = arrow_button("→", "День вперёд")
        self.next_btn.setFixedWidth(SUMMARY_ARROW_WIDTH)
        self.next_btn.clicked.connect(lambda: self._shift_date(1))
        date_row.addWidget(self.next_btn)
        date_row.addStretch(1)
        self._date_row = date_row
        root.addLayout(date_row)

        # Вторая строка шапки: только переключатель архивных (v0.33.0:
        # кнопка журнала уехала вниз, под плашку «Сводка»).
        tools = QHBoxLayout()
        self.show_archived = QCheckBox("показывать архивных")
        # По умолчанию галочка стоит (v0.23.1): архивные контрагенты
        # сразу видны в «Сводке».
        self.show_archived.setChecked(True)
        self.show_archived.toggled.connect(lambda *_: self.refresh())
        tools.addWidget(self.show_archived)
        tools.addStretch(1)
        root.addLayout(tools)

        # --- плашка со всеми строками ---
        self.panel = CollapsiblePanel("Сводка", expanded=True)
        # Заголовок «Сводка» — тем же шрифтом, что и заголовки блоков
        # «Поступление»/«Оплата» (v0.15.0); подзаголовки блоков внутри
        # мельче (см. ``_add_section``).
        self.panel.toggle.setStyleSheet(
            "QPushButton{background:transparent;border:none;text-align:left;"
            # v0.23.6: шрифт панели «Сводка» крупнее обычного на 50%.
            f"padding:2px 0;font-weight:800;font-size:{styles.fs_summary(15)}px;"
            f"color:{styles.TEXT}}}"
            "QPushButton:hover{background:transparent}"
        )
        content = self.panel.content_layout
        content.setSpacing(4)  # v0.43.0: плашка целиком на экране

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
                # v0.43.0: вместо прихода и оплат с начала месяца —
                # итоги реестра «Отчеты ККМ» за весь месяц.
                # v0.44.0: возврат отдельной строкой не показывается —
                # «Итого по Отчетам ККМ» уже за вычетом возвратов.
                ("month_kkm_total", "Итого по Отчетам ККМ"),
                ("month_sale_invoiced", "Реализация Покупателям"),
                ("month_total", "Итого за месяц"),
            ],
            nowrap=("month_sale_invoiced",),
        )
        root.addWidget(self.panel)

        # --- v0.33.0: три кнопки — внизу панели, в столбик: сводка
        # поступлений, сводка оплат, журнал движения средств. Раньше
        # кнопки сводок стояли в самом верху (v0.32.0), а журнал — в
        # шапке рядом с переключателем архивных. ---
        summaries = QVBoxLayout()
        summaries.setSpacing(4)
        # v0.43.0: кнопки стоят плотнее — плашка должна влезть целиком
        # на экран 768 точек по высоте.
        summaries.setContentsMargins(0, 2, 0, 0)
        self.incoming_summary_button = QPushButton("Сводка поступлений")
        self.incoming_summary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.incoming_summary_button.setToolTip(
            "Поступления помесячно, поквартально и за год по активной базе"
        )
        self.incoming_summary_button.clicked.connect(self._open_incoming_summary)
        summaries.addWidget(self.incoming_summary_button)

        self.payments_summary_button = QPushButton("Сводка оплат")
        self.payments_summary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.payments_summary_button.setToolTip(
            "Оплаты помесячно, поквартально и за год по активной базе"
        )
        self.payments_summary_button.clicked.connect(self._open_payments_summary)
        summaries.addWidget(self.payments_summary_button)

        self.movement_button = QPushButton("Журнал движения средств")
        self.movement_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.movement_button.setToolTip(
            "Поступления и оплаты за последние 30 дней с остатком по каждой "
            "записи"
        )
        self.movement_button.clicked.connect(self._open_funds_movement)
        summaries.addWidget(self.movement_button)
        root.addLayout(summaries)

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
        nowrap: tuple[str, ...] = (),
    ) -> None:
        """Блок строк: жирный подзаголовок и сетка «подпись — значение».

        ``nowrap`` — ключи строк, подпись которых **не переносится** на
        вторую строку (v0.44.0: «Реализация Покупателям» стоит в одну
        строчку).
        """

        caption = QLabel(title)
        caption.setStyleSheet(
            f"font-weight:800;font-size:{styles.fs_summary(12)}px;"
            f"color:{styles.TEXT}"
        )
        layout.addWidget(caption)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        # v0.43.0: между строками теснее — плашка должна влезть целиком.
        grid.setVerticalSpacing(1)
        for row, (key, text) in enumerate(rows):
            # v0.43.0: «Итого за месяц» — как «Итого остаток»: жирным.
            bold = key in {"balance", "month_total"}
            label = QLabel(text)
            label.setStyleSheet(
                f"font-size:{styles.fs_summary(12)}px;"
                f"{'font-weight:800;' if bold else ''}"
            )
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
            # v0.22.0: панель «Сводка» вдвое у́же, поэтому длинные
            # подписи («Задолженность покупателей») переносятся на
            # вторую строку, а не обрезаются. v0.44.0: подписи из
            # ``nowrap`` («Реализация Покупателям») стоят в одну строчку.
            label.setWordWrap(key not in nowrap)
            value = QLabel(fmt_money_plain(Decimal("0")))
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            value.setStyleSheet(
                f"font-size:{styles.fs_summary(12)}px;"
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

    def _open_payments_summary(self) -> None:
        """Открыть «Сводку оплат» за год, выбранный в «Сводке» (v0.31.0)."""

        from .payments_summary_view import PaymentsSummaryDialog

        dialog = PaymentsSummaryDialog(self.store, self.current_date(), self)
        dialog.exec()

    def _open_incoming_summary(self) -> None:
        """Открыть «Сводку поступлений» за год из «Сводки» (v0.32.0)."""

        from .incoming_summary_view import IncomingSummaryDialog

        dialog = IncomingSummaryDialog(self.store, self.current_date(), self)
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

    def _fit_date_row(self) -> None:
        """Спрятать стрелки, если плашка уже них (v0.71.0).

        Стрелки и поле даты требуют ~310 px. На узкой плашке (экран
        1366 — это 276 px) раскладка сжала бы виджеты ниже минимума, и
        они наползли бы друг на друга. Лучше временно убрать стрелки:
        дату всё равно можно выбрать календарём.
        """

        visible = self._arrows_fit(self.width())
        self.prev_btn.setVisible(visible)
        self.next_btn.setVisible(visible)

    def _arrows_fit(self, room: int) -> bool:
        """Помещаются ли стрелки и поле даты в ``room`` точек."""

        need = self.date_edit.minimumSizeHint().width()
        need += 2 * SUMMARY_ARROW_WIDTH + 2 * self._date_row.spacing()
        return need <= room

    def resizeEvent(self, event) -> None:  # noqa: N802 — имя задаёт Qt
        super().resizeEvent(event)
        self._fit_date_row()

    def _shift_date(self, days: int) -> None:
        """Сдвинуть дату «Сводки» на ``days`` дней (стрелки «←»/«→»)."""

        self.date_edit.setDate(self.date_edit.date().addDays(days))

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
        # v0.26.5: «Всего задолженность» берётся как в таблице
        # поставщиков — строка «Всего», столбец «Итого» (остаток долга
        # после оплат), а не полный график поставок.
        self._set_value(
            "debt_total",
            calc.supplier_debt_total_on_week(data, on_date, include_archived),
        )
        # v0.36.0: «Задолженность на неделе» — **только** долги со сроком
        # внутри недели, как колонка «Неделя» таблицы поставщиков
        # (v0.27.3 прибавляла просрочку — из-за этого цифра в «Сводке»
        # расходилась с таблицей). Просрочка — отдельная справочная
        # строка: она не входит ни в какие суммы.
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
        # v0.43.0: итоги ККМ за весь месяц выбранной даты (с 1-го по
        # последнее число) — реестр «Отчеты ККМ» активной базы.
        # v0.44.0: «Итого по Отчетам ККМ» — за вычетом возвратов.
        self._set_value("month_kkm_total", month.kkm_total)
        # v0.43.0: только выписанная реализация (зелёная метка).
        self._set_value("month_sale_invoiced", month.sale_invoiced)
        # «Итого за месяц» (v0.44.0) = ККМ за вычетом возвратов +
        # выписанная реализация покупателям.
        self._set_value("month_total", month.month_total)
