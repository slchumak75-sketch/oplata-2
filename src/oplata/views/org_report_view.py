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

from .. import column_widths
from .. import report as report_calc
from .. import styles
from ..dates import fmt_money_plain, fmt_vat_rate, parse_money
from ..storage import BASE_TITLES, DataStore
from ..widgets import install_column_resizer

# Годы, между которыми можно листать (как в «Сводке оплат»).
MIN_YEAR = 2000
MAX_YEAR = 2100

# Названия столбцов (v0.86.0: «Примечание» — вторым, как просил
# пользователь: «Добавить в отчёт вторым столбец примечание (из оплаты)»).
TITLE_COLUMN = "Наименование"
NOTE_COLUMN = "Примечание"
VAT_COLUMN = "НДС"
QUARTER_COLUMNS = ("1кв", "2кв", "3кв", "4кв")
TOTAL_COLUMN = "Итого"

# Порядок столбцов в таблице: имя, примечание, НДС, четыре квартала.
COLUMN_TITLES = (
    [TITLE_COLUMN, NOTE_COLUMN, VAT_COLUMN]
    + list(QUARTER_COLUMNS)
    + [TOTAL_COLUMN]
)
COL_TITLE, COL_NOTE, COL_VAT = 0, 1, 2
COL_FIRST_QUARTER = 3          # 1кв; дальше 2кв, 3кв, 4кв
COL_TOTAL = 7

# Голубоватая подсветка строк, которые заполняются руками (v0.86.0).
# Просьба пользователя: «строки по которым вводятся данные (4 штуки с
# голубоватым оттенком) сделать голубоватую подсветку по всем
# горизонтальным ячейкам». Жёлтой заливки затрат больше нет.
MANUAL_BG = QColor("#e8f1f8")

_MONEY_ALIGN = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class OrganizationReportDialog(QDialog):
    """Окно «Отчет»: итоги по организации по кварталам."""

    TITLE = "Отчет"

    def __init__(
        self,
        store: DataStore,
        on_date: date,
        parent: QDialog | None = None,
        *,
        read_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_date = on_date
        self.read_only = read_only
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
        self.headers = list(COLUMN_TITLES)
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

        hint = QLabel(self._access_hint())
        self.hint_label = hint
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
        """Столбцы: у всех — «тянутся мышью», ширины настраивает пользователь.

        v0.86.0: ширины меняются тем же способом, что в остальных
        таблицах: «Конфигурация» → «Редактировать поля» (тянуть мышью)
        → «Сохранить поля». Поэтому режим у всех столбцов —
        ``Interactive``: в режиме «по содержимому» выставленная ширина
        не держится, и сохранённые ширины не применялись бы.
        """

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self._saved_widths = column_widths.load(self._widths_key, len(self.headers))
        self._apply_default_widths()
        # Тянуть границы столбцов мышью прямо в теле таблицы — тот же
        # механизм, что в недельных таблицах и блоках оплат.
        self.resizer = install_column_resizer(self.table, self, min_width=48)
        self.set_columns_editable(False)

    def _apply_default_widths(self) -> None:
        """Ширины по умолчанию: сохранённые, иначе расчётные.

        Первые три столбца — по содержимому (название, примечание,
        НДС), остальные делят оставшееся место поровну.
        """

        table = self.table
        count = table.columnCount()
        widths = self._saved_widths
        if widths and len(widths) == count:
            for column, width in enumerate(widths):
                table.setColumnWidth(column, width)
            return
        # Расчётные: сначала по содержимому, потом числовые — поровну.
        for column in (COL_TITLE, COL_NOTE, COL_VAT):
            table.resizeColumnToContents(column)
        table.setColumnWidth(COL_VAT, max(table.columnWidth(COL_VAT), 60))
        taken = sum(table.columnWidth(column) for column in range(COL_FIRST_QUARTER))
        free = max(table.viewport().width() - taken, 0)
        numeric = count - COL_FIRST_QUARTER
        share = free // numeric if numeric else 100
        for column in range(COL_FIRST_QUARTER, count):
            table.setColumnWidth(column, max(share, 80))

    def showEvent(self, event):  # noqa: N802 - имя из Qt
        """Разложить столбцы, когда окно получило настоящий размер."""

        super().showEvent(event)
        if not self._saved_widths:
            self._apply_default_widths()

    # ------------------------------------------------------------ ширины
    @property
    def _widths_key(self) -> str:
        """Ключ таблицы в файле сохранённых ширин."""

        return "org_report"

    def reload_widths_from_file(self) -> None:
        """Подхватить общие ширины или сброс, не записывая файл обратно."""
        self._saved_widths = column_widths.load(self._widths_key, len(self.headers))
        self._apply_default_widths()

    def current_widths(self) -> list[int] | None:
        """Текущие ширины столбцов (для «Сохранить поля»)."""

        table = getattr(self, "table", None)
        if table is None:
            return None
        return [table.columnWidth(column) for column in range(table.columnCount())]

    def set_columns_editable(self, enabled: bool) -> None:
        """Разрешить или запретить тянуть границы столбцов мышью."""

        resizer = getattr(self, "resizer", None)
        if resizer is not None:
            resizer.set_enabled(enabled)

    def save_widths(self) -> bool:
        """Записать ширины столбцов в общий файл настроек."""

        widths = self.current_widths()
        if widths is None:
            return False
        column_widths.save(self._widths_key, self.table.viewport().width(), widths)
        self._saved_widths = list(widths)
        return True

    def reset_widths(self) -> None:
        """Вернуть расчётные ширины и убрать сохранённые."""

        column_widths.drop(self._widths_key)
        self._saved_widths = None
        for column in range(self.table.columnCount()):
            self.table.setColumnWidth(column, 100)
        self._setup_columns()

    # ------------------------------------------------------------------ год
    def current_year(self) -> int:
        return int(self.year_spin.value())

    def set_year(self, year: int) -> None:
        self.year_spin.setValue(year)

    def go_this_year(self) -> None:
        self.set_year(date.today().year)

    def _access_hint(self) -> str:
        if self.read_only:
            return "Режим просмотра: изменение значений в отчёте недоступно."
        return (
            "Строки «НДС Реализация (Накладные)», «Товары реализация без "
            "НДС», «Входящий НДС» и «Себестоимость реализация без НДС» "
            "заполняются из бухгалтерии — двойной клик по клетке."
        )

    def set_read_only(self, read_only: bool) -> None:
        """Сразу применить права, в том числе к уже открытому отчёту."""
        if self.read_only != read_only:
            self.read_only = read_only
            self.hint_label.setText(self._access_hint())
            self.refresh()

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
        """Заполнить строку таблицы значениями строки отчёта.

        v0.86.0: вторым столбцом — «Примечание» из карточки вида оплаты.
        У четырёх ручных строк голубым подсвечивается **вся строка**
        (просьба пользователя), жёлтой заливки затрат больше нет.
        """

        table = self.table
        # Фон всей строки: голубой у строк, которые заполняются руками.
        background = MANUAL_BG if line.manual else None

        title = QTableWidgetItem(line.title)
        title.setFlags(Qt.ItemFlag.NoItemFlags)
        if background is not None:
            title.setBackground(background)
        if line.bold:
            title.setFont(_bold(title.font()))
        table.setItem(row, COL_TITLE, title)

        note = QTableWidgetItem(line.note)
        note.setFlags(Qt.ItemFlag.NoItemFlags)
        if background is not None:
            note.setBackground(background)
        if line.bold:
            note.setFont(_bold(note.font()))
        table.setItem(row, COL_NOTE, note)

        rate = QTableWidgetItem(fmt_vat_rate(line.vat_rate))
        rate.setFlags(Qt.ItemFlag.NoItemFlags)
        rate.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if background is not None:
            rate.setBackground(background)
        if line.bold:
            rate.setFont(_bold(rate.font()))
        table.setItem(row, COL_VAT, rate)

        values = list(line.quarters) + [line.total]
        for index, amount in enumerate(values):
            self._fill_amount(row, COL_FIRST_QUARTER + index, line, index, amount)

    def _fill_amount(
        self,
        row: int,
        column: int,
        line: report_calc.ReportRow,
        index: int,
        amount,
    ) -> None:
        """Клетка суммы: у ручных строк — правится, у остальных — нет.

        Править можно четыре строки бухгалтерии и строки затрат (v0.86.0):
        введённое число заменяет сумму из «Оплаты» только внутри
        «Отчета». Колонка «Итого» не правится никогда — она сумма.
        """

        year = self.current_year()
        is_quarter = index < 4
        entered = ""
        if is_quarter:
            entered = self.store.data.org_report.quarter(year, line.key, index)
        editable = not self.read_only and is_quarter and (line.manual or line.expense)

        if line.manual:
            # Ручную строку показываем так, как её ввели (пусто = пусто).
            text = _manual_text(entered)
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
        if line.manual:
            item.setBackground(MANUAL_BG)
        if line.bold:
            item.setFont(_bold(item.font()))
        if line.expense and is_quarter:
            self._annotate_expense(item, line, index, entered)
        self.table.setItem(row, column, item)

    def _annotate_expense(
        self,
        item: QTableWidgetItem,
        line: report_calc.ReportRow,
        index: int,
        entered: str,
    ) -> None:
        """Подсказка к клетке затрат: откуда сумма (v0.86.0).

        Если квартал введён руками — говорим, сколько было в «Оплате»:
        правка живёт только в «Отчете», и разницу надо видеть.
        """

        original = (
            line.from_payments[index] if len(line.from_payments) > index
            else line.quarters[index]
        )
        from_payments = fmt_money_plain(original)
        if str(entered or "").strip():
            item.setToolTip(
                "Введено вручную в «Отчете».\n"
                f"В «Оплате» за этот квартал: {from_payments or '0,00'} руб."
            )
        elif self.read_only:
            item.setToolTip("Сумма из блока «Оплата». Доступен только просмотр.")
        else:
            item.setToolTip(
                "Сумма из блока «Оплата». Можно ввести своё значение — "
                "оно изменит только «Отчет»."
            )

    # ------------------------------------------------------------------ ввод
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Пользователь поправил клетку — запомнить правку.

        v0.86.0: правятся и четыре строки бухгалтерии, и строки затрат.
        Затраты — только внутри «Отчета»: введённое число заменяет сумму
        из «Оплаты», а сами оплаты не меняются (правило пользователя).
        """

        self._updating = getattr(self, "_updating", False)
        if self._updating:
            return
        if self.read_only:
            # На случай программного изменения ячейки или смены роли,
            # пока открыт редактор: никаких записей без права ввода.
            self.refresh()
            return
        column = item.column()
        if not COL_FIRST_QUARTER <= column < COL_TOTAL:
            return
        rows = self.report().rows
        if not 0 <= item.row() < len(rows):
            return
        line = rows[item.row()]
        if not (line.manual or line.expense):
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
            self.current_year(), line.key, column - COL_FIRST_QUARTER, text
        )
        self.store.save()
        # Пересчитываем таблицу: правка затрат меняет «Себестоимость
        # затрат без НДС», прибыль и налоги — но только здесь.
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
