"""Общее окно сводки «по месяцам, кварталам и году» (v0.32.0).

Окно одно, данных два: оплат и поступлений. Всё, кроме источника
строк, совпадает — таблица, выбор года, ширины колонок, — поэтому
общий класс лежит здесь, а ``PaymentsSummaryDialog`` и
``IncomingSummaryDialog`` задают только заголовок и расчёт.

Раскладка колонок (просьба пользователя v0.32.0):

* колонка «Вид» — **узкая**, ровно по названию, без растяжки;
* в «Сводке оплат» вторая колонка — **«НДС»** (ставка вида,
  v0.34.0), тоже узкая, по содержимому;
* все числовые колонки (12 месяцев, 4 квартала, «Год») делят
  оставшуюся ширину **поровну**; v0.34.0: последняя колонка
  называется «Год», раньше — «Всего за год»; v0.77.0: в «Сводке
  оплат» они на 30 % шире (``WIDE_NUMBER_COLUMNS``), таблица
  прокручивается по горизонтали, а первые колонки закреплены
  (``FROZEN_COLUMNS``);
* минимальная ширина числовой колонки — 92 px: на узком экране
  появляется горизонтальная прокрутка, а не обрезанные цифры.

v0.34.0: в «Сводке оплат» рядом с «Текущий год» стоит кнопка
**«Без НДС»** — переключатель показа сумм за минусом НДС: зелёная
кнопка — оплаты с НДС (как сейчас), красная — суммы без НДС
(240 при ставке 20 % показываются как 200; ставка 0 или пустая —
сумма не меняется).
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QEvent, Qt
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
from ..dates import fmt_money_plain, fmt_vat_rate
from ..storage import BASE_TITLES, DataStore
from ..widgets import FrozenColumns

_MONEY_ALIGN = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
# Числовой колонке хватает места на «1 000 000,00».
MIN_NUMBER_COLUMN = 92
# v0.77.0: месяцы, кварталы и «Год» в «Сводке оплат» на 30 % шире
# своей растянутой доли — в прежнюю ширину «1 000 000,00» влезал, но
# читался впритык. Таблица становится шире окна и прокручивается по
# горизонтали; первые колонки закреплены, поэтому вид оплаты виден
# всегда (FrozenColumns).
NUMBER_WIDTH_SCALE = 1.3
# Год выбирается в окне: от условного начала ведения базы до далёкого
# будущего — чтобы год можно было и нажать, и вписать руками.
MIN_YEAR = 2000
MAX_YEAR = 2100
# v0.34.0: последняя колонка — итог за год; раньше она называлась
# «Всего за год», пользователь просил сократить до «Год».
YEAR_COLUMN_TITLE = "Год"
# v0.76.0: колонка «Примечание» — подпись вида оплаты из его карточки
# (как в блоке «Оплата»). Показываем её рядом с названием, чтобы вид
# было проще узнать, когда названия похожи.
NOTE_COLUMN_TITLE = "Примечание"
# Кнопка-переключатель «Без НДС»: зелёная — суммы с НДС (как ввели),
# красная — суммы за минусом НДС.
_VAT_BUTTON_BASE = (
    "color:#fff;border:none;border-radius:8px;padding:7px 16px;font-weight:700"
)
_VAT_WITH = f"QPushButton{{background:{styles.GREEN};{_VAT_BUTTON_BASE}}}"
_VAT_WITHOUT = f"QPushButton{{background:{styles.RED};{_VAT_BUTTON_BASE}}}"


class MonthSummaryDialog(QDialog):
    """Окно сводки: строки — виды, столбцы — месяцы, кварталы, год."""

    # Заголовок окна, слово в подписи («Оплаты за … год») и подпись
    # первой колонки.
    TITLE = ""
    PERIOD_WORD = ""
    KIND_COLUMN_TITLE = ""
    # v0.34.0: колонка «НДС» и кнопка «Без НДС» — только в «Сводке
    # оплат»: у поступлений ставки НДС нет.
    SHOW_VAT = False
    # v0.76.0: колонка «Примечание» — только в «Сводке оплат»: у
    # поступлений своих видов нет, а значит, и примечаний.
    SHOW_NOTE = False
    # v0.77.0: числовые колонки шире своей доли на NUMBER_WIDTH_SCALE
    # — только в «Сводке оплат» (просьба пользователя).
    WIDE_NUMBER_COLUMNS = False
    # v0.77.0: сколько левых колонок закреплено (не уезжают при
    # горизонтальной прокрутке). Ноль — без закрепления.
    FROZEN_COLUMNS = 0

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
        if self.SHOW_VAT:
            # v0.34.0: «Без НДС» — переключатель: зелёная (с НДС,
            # как ввели) / красная (суммы за минусом НДС).
            self.without_vat_button = QPushButton("Без НДС")
            self.without_vat_button.setCheckable(True)
            self.without_vat_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.without_vat_button.setToolTip(
                "Зелёная — оплаты с НДС; красная — суммы за минусом НДС"
            )
            self.without_vat_button.toggled.connect(self._apply_vat_style)
            self.without_vat_button.toggled.connect(lambda *_: self.refresh())
            self._apply_vat_style()
            bar.addWidget(self.without_vat_button)
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
            f"background:#fffdf8;border:1px solid {styles.BORDER};"
            "border-radius:8px;"
            f"gridline-color:{styles.TABLE_GRID}}}"
            + styles.WIDE_SCROLLBAR
        )
        self._setup_columns()
        root.addWidget(self.table, stretch=1)
        # v0.77.0: закреплённые левые колонки. Накладку надо
        # переставлять всякий раз, как меняется размер таблицы, — иначе
        # она останется на старом месте при разворачивании окна.
        self.frozen_columns: FrozenColumns | None = None
        if self.FROZEN_COLUMNS:
            self.table.installEventFilter(self)
            self.frozen_columns = FrozenColumns(
                self.table, min(self.FROZEN_COLUMNS, self.text_columns)
            )

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
        # v0.34.0: в «Сводке оплат» вторая колонка — «НДС»; итоговая
        # колонка называется «Год».
        return (
            [self.KIND_COLUMN_TITLE]
            + (["НДС"] if self.SHOW_VAT else [])
            + ([NOTE_COLUMN_TITLE] if self.SHOW_NOTE else [])
            + list(calc.MONTH_TITLES)
            + list(calc.QUARTER_TITLES)
            + [YEAR_COLUMN_TITLE]
        )

    @property
    def text_columns(self) -> int:
        """Сколько первых колонок текстовые: вид, НДС, примечание."""

        return 1 + int(self.SHOW_VAT) + int(self.SHOW_NOTE)

    def _setup_columns(self) -> None:
        """Первая колонка — по названию, остальные делят ширину поровну.

        v0.34.0: в «Сводке оплат» колонка «НДС» — тоже по содержимому
        (ставка короткая), числовые колонки делят остаток поровну.
        """

        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(MIN_NUMBER_COLUMN)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        # Текстовые колонки — название, ставка НДС и примечание — по
        # содержимому; числовые делят остаток поровну.
        text_columns = 1 + int(self.SHOW_VAT) + int(self.SHOW_NOTE)
        for column in range(1, text_columns):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        for column in range(text_columns, len(self.headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)

    # ------------------------------------------------------------------ НДС
    @property
    def without_vat(self) -> bool:
        """Показывать суммы за минусом НДС (v0.34.0)."""

        return bool(
            self.SHOW_VAT and getattr(self, "without_vat_button", None)
            and self.without_vat_button.isChecked()
        )

    def _apply_vat_style(self) -> None:
        """Зелёная кнопка — с НДС, красная — без НДС (v0.34.0)."""

        button = getattr(self, "without_vat_button", None)
        if button is None:
            return
        button.setStyleSheet(
            _VAT_WITHOUT if button.isChecked() else _VAT_WITH
        )

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

        # v0.34.0: «Без НДС» — показываем суммы за минусом НДС по
        # ставке вида (240 при 20 % -> 200; ставка 0 — без изменений).
        lines = [
            calc.row_without_vat(line) if self.without_vat else line
            for line in summary.rows
        ]
        # Строка «Всего» — сумма показанных строк: если из части сумм
        # НДС вычтен, итог считается по тем же цифрам, что в таблице.
        totals = [calc.ZERO] * 12
        for line in lines:
            for index, amount in enumerate(line.months):
                totals[index] += amount
        rows = list(lines) + [calc.payments_row(calc.TOTAL_ROW_TITLE, totals)]
        table = self.table
        table.setRowCount(len(rows))
        # Первые колонки — название, (в «Сводке оплат») ставка НДС и
        # примечание вида (v0.76.0).
        text_columns = self.text_columns
        for row, line in enumerate(rows):
            bold = row == len(rows) - 1  # строка «Всего»
            values: list[object] = [line.name]
            if self.SHOW_VAT:
                values.append(fmt_vat_rate(line.vat_rate))
            if self.SHOW_NOTE:
                values.append(getattr(line, "note", "") or "")
            values += list(line.months) + list(line.quarters) + [line.total]
            for column, amount in enumerate(values):
                text = amount if column < text_columns else _money(amount)
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                if column >= text_columns:
                    item.setTextAlignment(_MONEY_ALIGN)
                if bold:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row, column, item)
        # Ширины и закреплённые колонки пересчитываем после каждого
        # заполнения: строк становится то больше, то меньше.
        self._apply_number_widths()
        if self.frozen_columns is not None:
            self.frozen_columns.relayout()


    def _apply_number_widths(self) -> None:
        """Числовые колонки — на 30 % шире своей доли (v0.77.0).

        Доля считается от того места, которое осталось после
        текстовых колонок: сколько пришлось бы на колонку при
        растяжке, столько и берём, но с надбавкой. Таблица от этого
        выходит за край окна — появляется горизонтальная прокрутка.
        """

        if not self.WIDE_NUMBER_COLUMNS:
            return
        table = self.table
        header = table.horizontalHeader()
        numeric = list(range(self.text_columns, len(self.headers)))
        if not numeric:
            return
        taken = sum(
            table.columnWidth(column) for column in range(self.text_columns)
        )
        free = table.viewport().width() - taken
        if free <= 0:
            return  # окно ещё не отрисовано — применим следующим разом
        # Надбавка считается от той ширины, которую колонка реально
        # занимает сейчас, — а это не всегда доля: на узком экране
        # колонка упирается в минимум (MIN_NUMBER_COLUMN), и прирост
        # надо отсчитывать от него, иначе колонка не изменится.
        current = max(free // len(numeric), MIN_NUMBER_COLUMN)
        width = max(int(round(current * NUMBER_WIDTH_SCALE)), MIN_NUMBER_COLUMN)
        # Без этого последняя колонка («Год») сама растягивается до
        # края: QTableView по умолчанию держит stretchLastSection
        # включённым, и таблица никогда не выходит за край окна, —
        # а значит, прибавка к ширине осталась бы только на бумаге.
        header.setStretchLastSection(False)
        for column in numeric:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(column, width)

    def eventFilter(self, watched, event):  # noqa: N802 - имя из Qt
        """Переставить закреплённые колонки при изменении таблицы."""

        if (
            watched is self.table
            and event.type() == QEvent.Type.Resize
            and self.frozen_columns is not None
        ):
            self._apply_number_widths()
            self.frozen_columns.relayout()
        return super().eventFilter(watched, event)


def _money(amount: object) -> str:
    """Сумма в виде текста; ноль показываем пустым (таблица читается)."""

    return fmt_money_plain(amount) if amount else ""
