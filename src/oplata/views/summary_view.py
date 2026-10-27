"""Экран «Сводка» (макет 01-summary.png).

Все цифры по-прежнему приходят из заглушек ``calc.py``. Печать не
добавляет формул: она оформляет ровно тот снимок значений, который уже
показан на экране после последнего ``refresh()``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from PySide6.QtCore import QMarginsF, Qt
from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __app_name__, __version__, calc, styles
from ..dates import fmt_date, fmt_money, fmt_money_plain
from ..reports import SummaryReportRow, build_summary_report_html
from ..storage import DataStore
from ..widgets import date_edit_value, kpi_card, make_date_edit, make_panel


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
        self._report_totals = calc.SummaryTotals()
        self._report_rows: list[SummaryReportRow] = []
        self._report_overdue_count = 0
        self._report_include_archived = False

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

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

        # --- 6 KPI-карточек ---
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        self.kpi_values: dict[str, QLabel] = {}
        kpi_defs = [
            ("total_debt", "ВСЕГО ЗАДОЛЖЕННОСТЬ", False),
            ("overdue", "ПРОСРОЧЕНО", False),
            ("due_today", "СРОК СЕГОДНЯ", False),
            ("due_next_7", "СРОК В БЛИЖАЙШИЕ 7 ДНЕЙ", False),
            ("income_month", "ПРИХОД ЗА МЕСЯЦ", False),
            ("paid_month", "ОПЛАЧЕНО ЗА МЕСЯЦ", True),  # на макете зелёная
        ]
        for key, title, green in kpi_defs:
            card, value_label = kpi_card(title, fmt_money(calc.ZERO), green_value=green)
            self.kpi_values[key] = value_label
            kpi_row.addWidget(card, stretch=1)
        root.addLayout(kpi_row)

        # --- таблица задолженности по поставщикам ---
        panel, panel_layout = make_panel()
        title = QLabel("Задолженность по поставщикам")
        title.setObjectName("PanelTitle")
        panel_layout.addWidget(title)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Поставщик", "Задолженность", "Просрочено", "Ближайший срок", "Сумма срока"]
        )
        header_item = self.table.horizontalHeaderItem(0)
        if header_item is not None:
            header_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.table.itemClicked.connect(self._on_item_clicked)
        panel_layout.addWidget(self.table)
        root.addWidget(panel, stretch=1)

        # --- просроченные оплаты ---
        overdue_panel, overdue_layout = make_panel()
        self.overdue_title = QLabel("🔴  Просроченные оплаты (0)")
        self.overdue_title.setObjectName("PanelTitle")
        overdue_layout.addWidget(self.overdue_title)
        self.overdue_text = QLabel("Просроченных оплат нет 🎉")
        self.overdue_text.setObjectName("Muted")
        overdue_layout.addWidget(self.overdue_text)
        root.addWidget(overdue_panel)

        self.refresh()

    def build_print_html(self, generated_at: datetime | None = None) -> str:
        """HTML печатной формы по последнему экранному снимку."""

        return build_summary_report_html(
            on_date=self._report_date,
            totals=self._report_totals,
            rows=self._report_rows,
            overdue_count=self._report_overdue_count,
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
        page_layout.setOrientation(QPageLayout.Orientation.Landscape)
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
        preview.resize(1100, 800)
        preview.paintRequested.connect(
            lambda target_printer: self.render_print_document(
                target_printer, document
            )
        )
        preview.exec()

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            supplier_id = item.data(Qt.ItemDataRole.UserRole)
            if supplier_id:
                self.on_open_supplier(str(supplier_id))

    def refresh(self) -> None:
        on_date = date_edit_value(self.date_edit)
        include_archived = self.show_archived.isChecked()

        totals = calc.summary_totals(on_date)  # заглушка: все нули
        self._report_date = on_date
        self._report_totals = totals
        self._report_include_archived = include_archived
        self._report_rows = []

        self.kpi_values["total_debt"].setText(fmt_money(totals.total_debt))
        self.kpi_values["overdue"].setText(fmt_money(totals.overdue))
        self.kpi_values["due_today"].setText(fmt_money(totals.due_today))
        self.kpi_values["due_next_7"].setText(fmt_money(totals.due_next_7_days))
        self.kpi_values["income_month"].setText(fmt_money(totals.income_month))
        self.kpi_values["paid_month"].setText(fmt_money(totals.paid_month))

        suppliers = [
            s for s in self.store.data.suppliers
            if include_archived or not s.archived
        ]
        self.table.setRowCount(len(suppliers))
        for row, supplier in enumerate(suppliers):
            debt_row = calc.supplier_debt_row(supplier.id, on_date)  # заглушка
            self._report_rows.append(
                SummaryReportRow(
                    supplier_name=supplier.name,
                    debt=debt_row.debt,
                    overdue=debt_row.overdue,
                    next_due_date=debt_row.next_due_date,
                    next_due_amount=debt_row.next_due_amount,
                )
            )

            name_item = QTableWidgetItem(supplier.name)
            name_item.setForeground(QColor(styles.LINK_BLUE))
            font = QFont()
            font.setUnderline(True)
            name_item.setFont(font)
            name_item.setData(Qt.ItemDataRole.UserRole, supplier.id)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(row, 0, name_item)

            values = [
                fmt_money_plain(debt_row.debt),
                fmt_money_plain(debt_row.overdue),
                fmt_date(debt_row.next_due_date) if debt_row.next_due_date else "—",
                (
                    fmt_money_plain(debt_row.next_due_amount)
                    if debt_row.next_due_amount is not None
                    else "—"
                ),
            ]
            for col, text in enumerate(values, start=1):
                item = QTableWidgetItem(text)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight if col != 3 else Qt.AlignmentFlag.AlignCenter
                )
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, col, item)

        overdue = calc.overdue_payments(on_date)  # заглушка: пустой список
        self._report_overdue_count = len(overdue)
        self.overdue_title.setText(f"🔴  Просроченные оплаты ({len(overdue)})")
        self.overdue_text.setText(
            "Просроченных оплат нет 🎉" if not overdue else f"Просрочено операций: {len(overdue)}"
        )
