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
from datetime import date, datetime
from decimal import Decimal

from PySide6.QtCore import QMarginsF, Qt
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
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

from .. import __app_name__, __version__, calc, styles
from ..dates import fmt_date, fmt_money_plain
from ..reports import SummaryLine, SummarySection, build_summary_sections_html
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
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_open_supplier = on_open_supplier

        # Снимок последних показанных значений для печатной формы.
        # Заполняется в refresh(); печать ничего не пересчитывает сама.
        self._report_date = date.today()
        self._report_sections: list[SummarySection] = []
        self._report_include_archived = False

        # Подписи и значения строк: ключ -> (подпись, значение).
        self._captions: dict[str, QLabel] = {}
        self._values: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # --- верхняя строка: дата, архивные, печать ---
        top = QHBoxLayout()
        date_label = QLabel("Сводка на дату")
        date_label.setStyleSheet("font-weight:700")
        top.addWidget(date_label)
        self.date_edit = make_date_edit(date.today())
        self.date_edit.dateChanged.connect(lambda *_: self.refresh())
        top.addWidget(self.date_edit)
        self.show_archived = QCheckBox("показывать архивных")
        self.show_archived.toggled.connect(lambda *_: self.refresh())
        top.addWidget(self.show_archived)
        top.addStretch(1)
        self.print_button = QPushButton("🖨  Печать")
        self.print_button.setToolTip("Открыть предпросмотр печати сводки")
        self.print_button.clicked.connect(self._open_print_preview)
        top.addWidget(self.print_button)
        root.addLayout(top)

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
                ("funds", "Остаток на счету"),
                ("income", "Итого поступление"),
                ("payments", "Итого оплаты"),
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
                # Строки «Задолженность на сегодня» больше нет (v0.15.0):
                # «Просрочено» берётся из столбца «на сегодня» таблицы
                # поставщиков, отдельная строка не нужна.
                # v0.16.0: «Просрочено» переименовано в «Задолженность на
                # сегодня», а под настоящий просроченный платёж добавлена
                # отдельная строка ниже.
                ("debt_overdue", "Задолженность на сегодня"),
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

    # -------------------------------------------------------------- расчёты
    def _set_value(self, key: str, amount: Decimal) -> None:
        self._values[key].setText(fmt_money_plain(amount))

    def refresh(self) -> None:
        on_date = date_edit_value(self.date_edit)
        include_archived = self.show_archived.isChecked()
        data = self.store.data

        funds = calc.funds_totals(data)
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
        self._set_value("debt_overdue", debt.overdue)
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

        # Снимок для печати: те же строки, что видны на экране.
        self._report_date = on_date
        self._report_include_archived = include_archived
        self._report_sections = [
            SummarySection(
                title="Средства",
                lines=[
                    SummaryLine("Остаток на счету", fmt_money_plain(funds.funds)),
                    SummaryLine("Итого поступление", fmt_money_plain(funds.income)),
                    SummaryLine("Итого оплаты", fmt_money_plain(funds.payments)),
                    SummaryLine("Итого остаток", fmt_money_plain(funds.balance)),
                ],
            ),
            SummarySection(
                title="Задолженность поставщикам",
                lines=[
                    SummaryLine(
                        "Всего задолженность", fmt_money_plain(debt.total)
                    ),
                    SummaryLine(
                        "Задолженность на неделе", fmt_money_plain(debt.week)
                    ),
                    SummaryLine(
                        "Задолженность на сегодня", fmt_money_plain(debt.overdue)
                    ),
                    SummaryLine(
                        "Просроченный платеж", fmt_money_plain(debt.late)
                    ),
                ]
                + [
                    SummaryLine(
                        f"Оплата {week.monday.strftime('%d.%m')}"
                        f" – {week.sunday.strftime('%d.%m')}",
                        fmt_money_plain(week.amount),
                    )
                    for week in plan
                ],
            ),
            SummarySection(
                title="Задолженность покупателей",
                lines=[
                    SummaryLine(
                        "Задолженность покупателей",
                        fmt_money_plain(customers.total),
                    ),
                ],
            ),
            SummarySection(
                title="Месяц",
                lines=[
                    SummaryLine(
                        "Приход с начала месяца", fmt_money_plain(month.income)
                    ),
                    SummaryLine(
                        "Оплаты с начала месяца", fmt_money_plain(month.payments)
                    ),
                ],
            ),
        ]

    # ---------------------------------------------------------------- печать
    def build_print_html(self, generated_at: datetime | None = None) -> str:
        """HTML печатной формы по последнему экранному снимку."""

        return build_summary_sections_html(
            on_date=self._report_date,
            sections=self._report_sections,
            include_archived=self._report_include_archived,
            generated_at=generated_at,
            app_name=__app_name__,
            app_version=__version__,
        )

    def create_print_document(
        self, generated_at: datetime | None = None
    ) -> QTextDocument:
        """Создать ``QTextDocument`` для предпросмотра/печати/PDF."""

        document = QTextDocument()
        font = QFont(self.font())
        font.setPointSize(10)
        document.setDefaultFont(font)
        document.setDocumentMargin(0)
        document.setHtml(self.build_print_html(generated_at))
        return document

    @staticmethod
    def render_print_document(printer: QPrinter, document: QTextDocument) -> None:
        """Настроить размер страницы документа и отправить его на печать."""

        document.setPageSize(printer.pageRect(QPrinter.Unit.Point).size())
        document.print_(printer)

    def configure_print_printer(self, printer: QPrinter) -> QPrinter:
        """Единая настройка страницы для предпросмотра, PDF и скриншотов."""

        page_layout = printer.pageLayout()
        page_layout.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        page_layout.setOrientation(QPageLayout.Orientation.Portrait)
        page_layout.setUnits(QPageLayout.Unit.Millimeter)
        page_layout.setMargins(QMarginsF(12, 12, 12, 12))
        printer.setPageLayout(page_layout)
        printer.setDocName(f"Сводка на {fmt_date(self._report_date)}")
        return printer

    def _open_print_preview(self) -> None:
        self.refresh()
        document = self.create_print_document()

        printer = self.configure_print_printer(
            QPrinter(QPrinter.PrinterMode.HighResolution)
        )

        preview = QPrintPreviewDialog(printer, self)
        preview.setWindowTitle("Предпросмотр печати — Сводка")
        preview.resize(900, 1000)
        preview.paintRequested.connect(
            lambda target_printer: self.render_print_document(
                target_printer, document
            )
        )
        preview.exec()
