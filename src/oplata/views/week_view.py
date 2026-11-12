"""Недельная таблица — экраны «Поставщики» и «Покупатели» (макеты 02/03).

Состояние (v0.8.0, запросы пользователя 27.10.2026):
* Клик по поставщику открывает окно «Поставка» для отдельной поставки
  с видимым сроком оплаты; клик по покупателю — окно «Реализация» без
  срока оплаты.
* У поставщиков прямо в ячейках редактируются «Приход» и «Оплата»;
  у покупателей — «Реализация» и «Оплата». Срок через
  ``calc.payment_due_date`` считается только для поставщицкого
  «Прихода»; у «Реализации» ``due_date`` всегда пуст.
* Дневной «Долг» есть только у поставщиков и считается в calc: исходная
  сумма поставок на срок оплаты; сумма остаётся для архива, оплата её
  пока не уменьшает. У покупателей дневного столбца «Долг» нет.
* Таблица растягивается на всю ширину окна; шрифт сумм подгоняется под
  максимальное число ``88 888,88``; дни разделены толстыми линиями.
  Выходные/праздники розовые; у текущей даты салатовая только верхняя
  ячейка даты, остальные ячейки дня не красятся.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from itertools import groupby

from PySide6.QtCore import QModelIndex, QPoint, QTimer, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import calc, styles
from ..dates import (
    WEEKDAYS_RU_SHORT,
    fmt_date,
    fmt_money,
    fmt_money_plain,
    month_title,
    parse_money,
    week_days,
    week_monday,
)
from ..models import (
    KIND_INCOME,
    KIND_PAYMENT,
    KIND_SALE,
    Counterparty,
    Operation,
    alphabetical_key,
    operation_amount,
    set_daily_operation,
)
from ..storage import DataStore
from ..widgets import (
    AmountLineEdit,
    date_edit_value,
    make_date_edit,
    set_date_edit,
    RowHoverMixin,
    arrow_button,
    install_row_hover,
)

# v0.55.0: названия контрагентов — мельче основного шрифта таблицы,
# итоговые колонки — у́же (просьба пользователя). Ширины подобраны по
# метрикам: подпись «Просрочено» при fs(11) занимает ~104 px.
def alphabetical_key(item) -> str:
    """Ключ сортировки по алфавиту (v0.55.0).

    Без учёта регистра; «ё» считаем как «е», иначе «ёлка» уехала бы в
    конец списка — в русском алфавите буква стоит после «е».
    """

    return getattr(item, "name", str(item)).casefold().replace("ё", "е")


NAME_FONT_SIZE = styles.fs(10)   # названия контрагентов
LEAD_FONT_SIZE = styles.fs(10)   # итоговые колонки: «Итого»/«Всего» …
NAME_WIDTH_BASE = 188
NAME_WIDTH_MIN = 150
LEAD_WIDTH_BASE = 110
LEAD_WIDTH_MIN = 100

HEADER_ROWS = 3
TOTAL_ROW = 3
FIRST_DATA_ROW = 4
# Поставщики: 7 дней * (Приход/Долг/Оплата). Покупатели (v0.8.0):
# 7 дней * (Реализация/Оплата), дневного столбца «Долг» у них нет.
SUPPLIER_SUBHEADERS = ("Приход", "Долг", "Оплата")
SUPPLIER_SUBHEADER_KINDS = ("income", "debt", "payment")
# v0.29.0: между «Реализацией» и «Оплатой» — узкая колонка метки
# «выписано»: красная — в работе, зелёная — выписано. Клик по ней
# переключает цвет (реверсивное поле); текста в колонке нет.
CUSTOMER_SUBHEADERS = ("Реализация", "", "Оплата")
CUSTOMER_SUBHEADER_KINDS = ("sale", "mark", "payment")
# Имена ниже оставлены для тестов/совместимости и описывают поставщика.
COLS = 3 + 7 * len(SUPPLIER_SUBHEADERS)
SUBHEADERS = SUPPLIER_SUBHEADERS
SUBHEADER_KINDS = SUPPLIER_SUBHEADER_KINDS
OP_KIND_BY_COLUMN = {
    "income": KIND_INCOME,
    "sale": KIND_SALE,
    "payment": KIND_PAYMENT,
}
DAY_SEPARATOR_COLOR = "#9a8873"  # толстые линии между днями, как на макете 08
SEPARATOR_AFTER_COLS = {2, 5, 8, 11, 14, 17, 20}  # поставщики, после каждого дня кроме Вс
MAX_AMOUNT_TEXT = "88 888,88"  # под это число подгоняется шрифт сумм (запрос пользователя)
META_ROLE = Qt.ItemDataRole.UserRole        # "id|дата|kind" редактируемых ячеек
# v0.29.0: "id|дата" узкой колонки метки «выписано».
MARK_ROLE = Qt.ItemDataRole.UserRole + 1
# Веса и минимумы ширин колонок поставщика для растягивания на всю ширину.
BASE_WIDTHS = [180, 96, 96] + [60] * 21
MIN_WIDTHS = [146, 80, 80] + [52] * 21
ZERO = Decimal("0")


def _money_text(amount: Decimal) -> str:
    return fmt_money_plain(amount) if amount != 0 else ""


class DaySeparatorDelegate(RowHoverMixin, QStyledItemDelegate):
    """Рисует толстую вертикальную линию на границах дней (макет 08).

    Заодно создаёт редактор суммы для редактируемых ячеек: нулевое
    значение очищается при фокусе, ненулевое выделяется целиком.
    """

    def __init__(self, separator_after_cols=None, parent=None) -> None:
        super().__init__(parent)
        self.separator_after_cols = set(
            SEPARATOR_AFTER_COLS if separator_after_cols is None else separator_after_cols
        )

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        if index.data(META_ROLE):
            return AmountLineEdit(parent=parent)
        return super().createEditor(parent, option, index)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        table = self.parent()
        span = 1
        if isinstance(table, QTableWidget):
            span = table.columnSpan(index.row(), index.column())
        right_col = index.column() + span - 1
        is_separator = right_col in self.separator_after_cols
        original_right = option.rect.right()
        if is_separator:
            # Отступ справа, чтобы суммы не упирались в толстую линию.
            option.rect.setRight(original_right - 5)
        super().paint(painter, option, index)
        if not is_separator:
            return
        painter.save()
        pen = QPen(QColor(DAY_SEPARATOR_COLOR))
        pen.setWidth(3)
        painter.setPen(pen)
        x = original_right - 1
        painter.drawLine(x, option.rect.top(), x, option.rect.bottom())
        painter.restore()


# Сколько дней показывает недельная таблица: понедельник–суббота, без
# воскресенья (v0.18.0, запрос пользователя).
TABLE_DAYS = 6


class WeekView(QWidget):
    """``kind``: ``"supplier"`` | ``"customer"``."""

    def __init__(
        self,
        store: DataStore,
        kind: str,
        on_open_card: Callable[[str | None], None],
        on_data_changed: Callable[[], None] | None = None,
        on_open_delivery: Callable[[str, date | None], None] | None = None,
        on_open_realization: Callable[[str, date | None], None] | None = None,
        on_open_operations: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        assert kind in ("supplier", "customer")
        self.store = store
        self.kind = kind
        self.on_open_card = on_open_card
        # Вызывается после сохранения ввода, чтобы главное окно обновило
        # поступлений и «Сводку» (сама эта таблица не перестраивается).
        self.on_data_changed = on_data_changed
        # Поставщик: окно «Поставка»; покупатель: окно «Реализация».
        # Суммы в ячейках при этом по-прежнему вводятся напрямую.
        self.on_open_delivery = on_open_delivery
        self.on_open_realization = on_open_realization
        self.on_open_operations = on_open_operations
        if kind == "supplier":
            self.subheaders = SUPPLIER_SUBHEADERS
            self.subheader_kinds = SUPPLIER_SUBHEADER_KINDS
        else:
            self.subheaders = CUSTOMER_SUBHEADERS
            self.subheader_kinds = CUSTOMER_SUBHEADER_KINDS
        self.columns_per_day = len(self.subheaders)
        # v0.29.0: номер узкой колонки метки внутри дня (только у
        # покупателей); у поставщиков её нет.
        self.mark_index = (
            self.subheader_kinds.index("mark")
            if "mark" in self.subheader_kinds
            else None
        )
        # v0.16.0: у поставщиков три итоговых столбца — «Итого»
        # (вся задолженность), «Просрочено» и «Неделя».
        # v0.29.0: у покупателей тоже три — «Всего», «Выписано»
        # (реализация с зелёной меткой минус оплаты) и «В работе»
        # (реализация с красной меткой). Раньше была одна «ЗАДОЛЖ.».
        self.lead_columns = 3
        # v0.27.3: у поставщиков справа от названия — узкая колонка
        # условий: «П» — предоплата, «Р» — реализация, иначе срок
        # отсрочки в днях (просьба пользователя). Итоговые колонки
        # при этом сдвигаются вправо на одну; у покупателей колонки нет.
        self.lead_offset = 1 if kind == "supplier" else 0
        # v0.18.0: в таблице шесть дней — без воскресенья. Седьмая
        # колонка съедала место, и сумма «88 888,88» не влезала.
        # Расчёты при этом по-прежнему идут по полной неделе (пн–вс).
        self.table_days = TABLE_DAYS
        self.column_count = (
            1
            + self.lead_offset
            + self.lead_columns
            + self.table_days * self.columns_per_day
        )
        lead_last = self.lead_offset + self.lead_columns
        self.separator_after_cols = {lead_last} | {
            lead_last + (day_index + 1) * self.columns_per_day
            for day_index in range(self.table_days - 1)
        }
        # Ширины выросли вместе со шрифтом (+30%, v0.12.0): колонкам дней
        # нужно место под «88 888,88», иначе подгонка снизит шрифт сумм
        # до нечитаемого. Если окно узкое — появится горизонтальный скролл.
        # v0.18.0: колонкам дней отдаём больше места (78 -> 92), чтобы
        # «88 888,88» влезало при возможно большем шрифте: воскресенье
        # убрали, и освободившееся место делим между днями.
        # v0.28.3: итоговым колонкам добавлено место — подпись
        # «Просрочено» целиком занимает ~112 px (жирный шрифт
        # fs(12) = 16 px), а при 90 px слово обрезалось.
        # v0.29.0: узкой колонке метки — своя ширина, она не должна
        # участвовать в подгонке шрифта сумм (иначе шрифт считался бы
        # по 30 px и числа стали бы нечитаемыми).
        day_base: list[int] = []
        day_min: list[int] = []
        for _ in range(self.table_days):
            for subcolumn in range(self.columns_per_day):
                if subcolumn == self.mark_index:
                    day_base.append(34)
                    day_min.append(30)
                else:
                    day_base.append(92)
                    day_min.append(72)
        # v0.55.0: итоговые колонки у́же, а шрифт названий мельче
        # (просьба пользователя) — длинные наименования поставщиков и
        # покупателей перестали съедать место у дней недели.
        self.base_widths = (
            [NAME_WIDTH_BASE]
            + ([46] if self.lead_offset else [])
            + [LEAD_WIDTH_BASE] * self.lead_columns
            + day_base
        )
        self.min_widths = (
            [NAME_WIDTH_MIN]
            + ([40] if self.lead_offset else [])
            + [LEAD_WIDTH_MIN] * self.lead_columns
            + day_min
        )
        self.week_start = week_monday(date.today())
        self._partners: list[Counterparty] = []
        self._days: list[date] = []
        self._rebuilding = False  # True, пока таблицу меняет код, а не пользователь
        # v0.18.0: без права ввода данных таблица только для просмотра.
        self.read_only = False
        self._amount_items: list[QTableWidgetItem] = []  # для подгонки шрифта сумм

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addLayout(self._build_toolbar())

        self.table = QTableWidget()
        # v0.16.0: единый шрифт для всей недельной таблицы — шапка,
        # названия и ячейки одного размера (был разнобой 13/14/16 px).
        # Суммы в днях всё равно подгоняет _fit_amount_font по ширине
        # колонки, чтобы «88 888,88» влезало целиком.
        # v0.23.1: широкий всегда видимый скроллбар — такой же, как на
        # плашке «Оплата» (просьба пользователя).
        self.table.setStyleSheet(
            f"QTableWidget{{font-size:{styles.fs(12)}px}}"
            f"{styles.WIDE_SCROLLBAR_ON_TABLE}"
        )
        self.table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        # v0.40.0: горизонтальной прокрутки в таблицах нет — колонки
        # всегда укладываются в ширину окна (просьба пользователя).
        # Полосу отключаем, чтобы она не появлялась даже на миг, пока
        # ширины подгоняются.
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        # v0.40.0: стандартный минимум секции (26 px по шрифту) мешает
        # сжать колонки на узком окне — а горизонтальной прокрутки быть
        # не должно, поэтому убираем и этот предел.
        self.table.horizontalHeader().setMinimumSectionSize(1)
        week_delegate = DaySeparatorDelegate(
            self.separator_after_cols, self.table
        )
        self.table.setItemDelegate(week_delegate)
        install_row_hover(self.table, week_delegate)
        # Ячейка суммы становится полем ввода уже по одному клику
        # (v0.12.0): «клацаешь мышкой — и сразу вводишь». Клик по
        # названию контрагента (колонка 0) по-прежнему открывает окно
        # поставки/реализации — там ячейка не редактируемая.
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.table.itemClicked.connect(self._on_item_clicked)
        # Двойной клик по контрагенту — прежнее действие (окно
        # «Реализация» у покупателя), чтобы оно не пропало после того,
        # как одинарный клик стал открывать журнал операций (v0.13.0).
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        # v0.27.3: двойной клик убран (просьба пользователя), поэтому
        # «Поставку»/«Реализацию» открывает правый клик по строке —
        # иначе к окну не осталось бы ни одного способа.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        # v0.23.1: закреплённая шапка — три строки шапки и строка
        # «ВСЕГО» лежат в отдельной таблице поверх основной: при
        # вертикальной прокрутке они остаются на месте.
        self.frozen_table = QTableWidget(self)
        self.frozen_table.setObjectName("FrozenWeekHeader")
        self.frozen_table.setStyleSheet(
            f"QTableWidget{{font-size:{styles.fs(12)}px;"
            f"background:#fffdf8;border:0px;gridline-color:transparent}}"
        )
        self.frozen_table.horizontalHeader().setVisible(False)
        self.frozen_table.verticalHeader().setVisible(False)
        # v0.40.0: те же узкие колонки, что и в основной таблице.
        self.frozen_table.horizontalHeader().setMinimumSectionSize(1)
        # Полос прокрутки у закреплённой шапки нет: её горизонтальный
        # сдвиг повторяет прокрутку основной таблицы.
        self.frozen_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.frozen_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.frozen_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.frozen_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.frozen_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Клик по закреплённой шапке идёт в таблицу под ней.
        self.frozen_table.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self.frozen_table.setItemDelegate(
            DaySeparatorDelegate(self.separator_after_cols, self.frozen_table)
        )
        self.table.horizontalScrollBar().valueChanged.connect(
            self._sync_frozen_offset
        )
        root.addWidget(self.table, stretch=1)
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> QHBoxLayout:
        # Обычная горизонтальная раскладка, как в v0.15.0: самописный
        # перенос по строкам (FlowLayout) оставлял за таблицей
        # негерерисованные «призраки» кнопок (v0.16.0 — возвращено).
        # Кнопки «← Неделя»/«Неделя →» при этом широкие, по тексту.
        bar = QHBoxLayout()
        bar.setSpacing(8)

        # v0.25.2: подпись «Неделя» — тем же шрифтом, что кнопка
        # «Сегодня» (просьба пользователя), поэтому без своего кегля.
        self.prev_week_btn = arrow_button(
            "← Неделя", "Неделя назад", plain=True
        )
        self.prev_week_btn.clicked.connect(lambda: self._shift_week(-7))
        bar.addWidget(self.prev_week_btn)

        self.week_label = QLabel()
        # v0.23.1: шрифт «Недели» — такой же, как у кнопки «Сегодня»
        # (просьба пользователя): базовый кегль и начертание кнопки,
        # без отдельного увеличения.
        self.week_label.setStyleSheet(f"font-weight:600;font-size:{styles.fs(13)}px")
        bar.addWidget(self.week_label)

        self.next_week_btn = arrow_button(
            "Неделя →", "Неделя вперёд", plain=True
        )
        self.next_week_btn.clicked.connect(lambda: self._shift_week(7))
        bar.addWidget(self.next_week_btn)

        self.today_btn = QPushButton("Сегодня")
        self.today_btn.clicked.connect(self._go_today)
        bar.addWidget(self.today_btn)

        bar.addWidget(QLabel("Дата"))
        self.date_edit = make_date_edit(self.week_start)
        bar.addWidget(self.date_edit)

        go_btn = QPushButton("Перейти")
        go_btn.clicked.connect(self._go_to_date)
        bar.addWidget(go_btn)

        bar.addStretch(1)
        # v0.23.1: кнопка «Очистить суммы» убрана из таблицы
        # «Поставщики» по просьбе пользователя — она удаляла введённые
        # суммы за неделю безвозвратно и была лишней.

        # Кнопка карточки живёт в панели таблицы (с v0.11.0), а не в шапке
        # окна: пользователь просил перенести её слева от «Очистить суммы».
        card_title = "Карточка поставщика" if self.kind == "supplier" else (
            "Карточка покупателя"
        )
        self.card_button = QPushButton(card_title)
        self.card_button.setToolTip(
            f"Открыть карточку "
            f"{'поставщика' if self.kind == 'supplier' else 'покупателя'}"
        )
        self.card_button.clicked.connect(lambda: self.on_open_card(None))
        bar.addWidget(self.card_button)

        if self.kind == "supplier":
            debt_title = QLabel("Задолженность за неделю:")
            debt_title.setObjectName("Muted")
            bar.addWidget(debt_title)
            self.right_value_label = QLabel(fmt_money(ZERO))
            self.right_value_label.setStyleSheet(f"font-weight:800;font-size:{styles.fs(14)}px")
            bar.addWidget(self.right_value_label)
        else:
            count_title = QLabel("Покупателей:")
            count_title.setObjectName("Muted")
            bar.addWidget(count_title)
            self.right_value_label = QLabel("0")
            self.right_value_label.setStyleSheet(f"font-weight:800;font-size:{styles.fs(14)}px")
            bar.addWidget(self.right_value_label)
        return bar

    def set_read_only(self, read_only: bool) -> None:
        """Права пользователя: «только просмотр» или полный доступ (v0.18.0)."""

        self.read_only = read_only
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if read_only
            else QAbstractItemView.EditTrigger.AllEditTriggers
        )
        # Кнопки, меняющие данные, без права ввода не показываем.
        self.card_button.setVisible(not read_only)

    # ------------------------------------------------------------- навигация
    def _shift_week(self, days: int) -> None:
        self.week_start += timedelta(days=days)
        self.refresh()

    def _go_today(self) -> None:
        """Кнопка «Сегодня»: неделя сегодняшняя и в поле — сегодня.

        v0.27.4: раньше вызывался ``goto_date``, а ``refresh``
        ставит в поле даты начало недели — получалось, что «Сегодня»
        показывает понедельник (26.10 вместо 31.10). Поэтому здесь
        тот же переход, что при переключении вкладки.
        """

        self.reset_to_today()

    def _go_to_date(self) -> None:
        self.goto_date(date_edit_value(self.date_edit))

    def goto_date(self, day: date) -> None:
        """Перейти на неделю, содержащую ``day`` (также после «Поставки»)."""
        self.week_start = week_monday(day)
        self.refresh()

    def reset_to_today(self) -> None:
        """Показать сегодняшний день (v0.23.1).

        ``refresh`` ставит в поле даты начало недели, поэтому после
        перехода на вкладку поле показываем отдельно: неделя — та, в
        которой сегодня, а в окошке даты — само сегодняшнее число.
        """

        today = date.today()
        self.goto_date(today)
        set_date_edit(self.date_edit, today)

    def _open_journal_for_item(self, item: QTableWidgetItem) -> None:
        """Журнал операций контрагента из строки (v0.52.0).

        Журнал только **показывает** операции — менять их в нём нельзя
        (окно открывается без кнопок «Добавить/Изменить/Удалить»),
        поэтому он доступен любому пользователю, у которого стоит
        галочка на вкладку (просьба пользователя).
        """

        if item is None or item.column() != 0 or item.row() < FIRST_DATA_ROW:
            return
        counterparty_id = item.data(Qt.ItemDataRole.UserRole)
        if not counterparty_id:
            return
        if self.on_open_operations is not None:
            self.on_open_operations(str(counterparty_id))

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        # v0.52.0: без права ввода клик по имени открывает **журнал
        # операций** — он доступен всем, у кого есть вкладка (просьба
        # пользователя). Ввод сумм и метки «в работе/выписано» меняют
        # данные, поэтому без права ввода они закрыты, как и раньше.
        if self.read_only:
            self._open_journal_for_item(item)
            return
        # v0.29.0: клик по узкой колонке метки переключает цвет
        # (красная «в работе» <-> зелёная «выписано»).
        mark = item.data(MARK_ROLE)
        if mark:
            partner_id, date_iso = str(mark).split("|", 1)
            self._toggle_invoice_mark(partner_id, date.fromisoformat(date_iso))
            return
        if item.column() != 0 or item.row() < FIRST_DATA_ROW:
            return
        counterparty_id = item.data(Qt.ItemDataRole.UserRole)
        if not counterparty_id:
            return
        # v0.23.1: и у поставщика, и у покупателя клик по имени
        # открывает **журнал операций** контрагента (просьба
        # пользователя). Раньше у поставщика клик открывал «Поставку» —
        # она осталась на двойном клике.
        if self.on_open_operations is not None:
            self.on_open_operations(str(counterparty_id))
        elif self.kind == "supplier" and self.on_open_delivery is not None:
            # Запасной вариант, если журнал не задан.
            self.on_open_delivery(str(counterparty_id), None)
        elif self.kind == "customer" and self.on_open_realization is not None:
            self.on_open_realization(str(counterparty_id), None)
        else:
            self.on_open_card(str(counterparty_id))

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        """v0.27.3: двойной клик больше ничего не открывает.

        Раньше он открывал «Поставку»/«Реализацию», но по просьбе
        пользователя двойной клик убран — окно открывается правой
        кнопкой мыши. Метод оставлен: сигнал подключён, и пустой
        обработчик нагляднее, чем снятое подключение.
        """

        _ = item

    def _on_context_menu(self, position) -> None:
        """Правый клик по строке контрагента: «Поставка»/«Реализация».

        v0.27.3: вместо двойного клика, который убран по просьбе
        пользователя. Работает по любой ячейке строки — как раньше
        двойной клик по имени.
        """

        item = self.table.itemAt(position)
        if item is None or item.row() < FIRST_DATA_ROW:
            return
        name_item = self.table.item(item.row(), 0)
        if name_item is None:
            return
        counterparty_id = name_item.data(Qt.ItemDataRole.UserRole)
        if not counterparty_id:
            return
        # v0.52.0: «Поставка»/«Реализация» меняют данные, поэтому без
        # права ввода (v0.18.0) правый клик открывает журнал операций —
        # он доступен всем, у кого есть вкладка.
        if self.read_only:
            self._open_journal_for_item(name_item)
            return
        if self.kind == "supplier" and self.on_open_delivery is not None:
            self.on_open_delivery(str(counterparty_id), None)
        elif self.kind == "customer" and self.on_open_realization is not None:
            self.on_open_realization(str(counterparty_id), None)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """Пользователь ввёл сумму в редактируемую ячейку: проверить и сохранить."""
        if self._rebuilding:
            return
        meta = item.data(META_ROLE)
        if not meta or "|" not in str(meta):
            return  # не ячейка ввода (заголовки, имя, ВСЕГО, Долг…)
        counterparty_id, date_iso, column_kind = str(meta).split("|", 2)
        op_kind = OP_KIND_BY_COLUMN.get(column_kind)
        if op_kind is None:
            return

        try:
            amount = parse_money(item.text())
        except ValueError:
            amount = None
            invalid = True
        else:
            invalid = amount is not None and amount < 0
        if invalid:
            QMessageBox.warning(
                self,
                "Некорректная сумма",
                "Введите сумму цифрами, например: 1500 или 1 500,75\n"
                "Чтобы удалить сумму — очистите ячейку.",
            )
            self._restore_cell(item, counterparty_id, date_iso, column_kind)
            return

        due_iso = ""
        if self.kind == "supplier" and op_kind == KIND_INCOME:
            partner = calc.find_counterparty(self.store.data, counterparty_id)
            if partner is not None:
                due_iso = calc.payment_due_date(
                    date.fromisoformat(date_iso),
                    partner,
                    self.store.data.day_overrides,
                ).isoformat()
        set_daily_operation(
            self.store.data, counterparty_id, date_iso, op_kind, amount, due_iso
        )
        # v0.29.0: ввод реализации — метка «выписано» сбрасывается:
        # новая сумма ещё в работе (красная), цвет ставит пользователь.
        if self.kind == "customer" and op_kind == KIND_SALE:
            self._set_invoice_mark(
                counterparty_id, date.fromisoformat(date_iso), False
            )
        self.store.save()
        # Показать отформатированное значение и пересчитать Долг/итоги,
        # не перестраивая таблицу целиком.
        self._restore_cell(item, counterparty_id, date_iso, column_kind)
        self._update_calculated_cells()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def _restore_cell(
        self, item: QTableWidgetItem, counterparty_id: str, date_iso: str, column_kind: str
    ) -> None:
        """Перерисовать ячейку значением из хранилища (формат ``1 500,75``)."""
        day = date.fromisoformat(date_iso)
        text = calc.week_cell_value(
            counterparty_id,
            day,
            column_kind,
            self.store.data.operations,
            self.store.data,
        )
        self._rebuilding = True
        try:
            item.setText(text)
        finally:
            self._rebuilding = False

    # ------------------------------------------------------- расчётные ячейки
    def _remaining_due_maps(self) -> list[dict[date, Decimal]]:
        """Остаток долга по срокам после оплат — для «Итого»/«за неделю».

        v0.15.0: из «за неделю» вычитается оплата; если есть
        просроченная задолженность («на сегодня»), она гасится первой,
        а остаток оплаты идёт в «за неделю». Оплаты берутся по конец
        показанной недели, недоплаченный остаток остаётся на своём
        сроке и потому переносится в «на сегодня» следующих недель.

        v0.41.0: этой картой считаются только итоговые столбцы
        («Итого», «Просрочено», «Неделя») — ячейки дней «Долг»
        берут архивный график :meth:`_archival_due_maps`.
        """

        if self.kind != "supplier" or not self._days:
            return [{} for _ in self._partners]
        upto = self._days[-1]
        return [
            calc.debt_remaining_by_due_date(self.store.data, partner, upto)
            for partner in self._partners
        ]

    def _archival_due_maps(self) -> list[dict[date, Decimal]]:
        """Архивный график долга по срокам — для «Долга» в ячейках дней.

        v0.41.0: «Долг» в дне — это сумма поставок, чей срок оплаты
        приходится на этот день. Оплаты её **не уменьшают** (просьба
        пользователя): результат оплаты виден только в «Итого»,
        «Просрочено» и «Неделя», которые считаются по остатку
        (:meth:`_remaining_due_maps`).
        """

        if self.kind != "supplier" or not self._days:
            return [{} for _ in self._partners]
        return [
            calc.debt_by_due_date(self.store.data, partner)
            for partner in self._partners
        ]

    @staticmethod
    def _debt_columns(
        due_map: dict[date, Decimal],
        days: list[date],
        today: date,
        open_ended: bool = False,
        week_end: date | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        """(итого задолженность, просрочено, итог за показанную неделю).

        v0.22.1: столбец «на сегодня» заменён на **«Итого»** — общая
        текущая задолженность по поставщику: весь остаток долга после
        оплат, по всем срокам (и просроченным, и будущим).

        «Просрочено» — долги с истёкшим сроком (строго раньше даты).
        «За неделю» — итог по срокам внутри показанной недели, какой бы
        неделю ни смотрели.

        ``open_ended`` — поставщик со статусом «Реализация»: срок оплаты
        у него бессрочный, поэтому просрочки у него не бывает (v0.15.0),
        а в «Итого» его долг участвует.
        """
        if not days:
            return ZERO, ZERO, ZERO
        # v0.18.0: ``days`` — это дни таблицы (без воскресенья), а
        # считать надо по полной неделе, поэтому конец передают отдельно.
        monday, sunday = days[0], week_end or days[-1]
        # v0.22.1: «Итого» — вся текущая задолженность поставщику
        # (остаток долга по всем срокам).
        total = sum(due_map.values(), ZERO)
        # v0.16.0: «просрочено» — долги с истёкшим сроком (строго раньше
        # даты); при просмотре прошлой недели считаем на её конец.
        late_limit = min(today, sunday)
        late = (
            ZERO
            if open_ended
            else sum((a for d, a in due_map.items() if d < late_limit), ZERO)
        )
        week = sum(
            (a for d, a in due_map.items() if monday <= d <= sunday), ZERO
        )
        return total, late, week

    def _set_cell_text(self, row: int, col: int, text: str) -> None:
        item = self.table.item(row, col)
        if item is not None:
            item.setText(text)

    def _day_column(self, day_index: int, subcolumn_index: int) -> int:
        return (
            1
            + self.lead_offset
            + self.lead_columns
            + day_index * self.columns_per_day
            + subcolumn_index
        )

    @staticmethod
    def _conditions_text(partner: Counterparty) -> str:
        """Условия поставщика для узкой колонки (v0.27.3).

        «П» — предоплата, «Р» — реализация (бессрочный долг), иначе —
        срок отсрочки в днях.
        """

        if partner.prepayment:
            return "П"
        if partner.realization:
            return "Р"
        return str(partner.deferment_days)

    # ------------------------------------- метка «выписано» (v0.29.0)
    def _mark_operations(self, partner_id: str, day: date) -> list[Operation]:
        """Реализации покупателя за день — к ним относится метка."""

        date_iso = day.isoformat()
        return [
            op
            for op in self.store.data.operations
            if op.counterparty_id == partner_id
            and op.date == date_iso
            and op.kind == KIND_SALE
        ]

    def _mark_color(self, partner_id: str, day: date) -> str | None:
        """Цвет метки: зелёный — выписано, красный — в работе.

        ``None`` — реализации за этот день нет, метку не показываем.
        Если за день несколько реализаций, зелёная метка только когда
        выписаны все.
        """

        operations = self._mark_operations(partner_id, day)
        if not operations:
            return None
        total = sum((operation_amount(op) for op in operations), ZERO)
        if total == 0:
            return None
        if all(op.invoiced for op in operations):
            return styles.MARK_INVOICED
        return styles.MARK_IN_WORK

    def _day_mark_bg(self, day: date) -> str | None:
        """Фон ячейки дня под меткой (розовые выходные, как в таблице)."""

        if day == date.today():
            return styles.WEEK_TABLE_TODAY_GREEN_BODY
        if calc.is_day_off(day, self.store.data.day_overrides):
            return styles.WEEK_TABLE_WEEKEND_PINK
        return None

    def _paint_mark_cell(
        self,
        item: QTableWidgetItem,
        partner_id: str,
        day: date,
    ) -> None:
        """Залить ячейку метки и положить в неё данные для клика."""

        color = self._mark_color(partner_id, day)
        background = QColor(color or self._day_mark_bg(day) or "#fffdf8")
        item.setBackground(background)
        item.setData(MARK_ROLE, f"{partner_id}|{day.isoformat()}")
        item.setToolTip(
            ""
            if color is None
            else "Зелёная — документы выписаны; красная — в работе. "
            "Клик переключает цвет"
        )

    def _put_mark_cell(
        self,
        row: int,
        column: int,
        partner_id: str,
        day: date,
    ) -> QTableWidgetItem:
        """Создать ячейку узкой колонки метки."""

        item = QTableWidgetItem("")
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        )
        font = QFont()
        font.setPixelSize(styles.fs(12))
        item.setFont(font)
        self.table.setItem(row, column, item)
        self._paint_mark_cell(item, partner_id, day)
        return item

    def _set_invoice_mark(
        self, partner_id: str, day: date, invoiced: bool
    ) -> bool:
        """Поставить метку «выписано» на реализации дня. True — было изменение."""

        operations = self._mark_operations(partner_id, day)
        if not operations:
            return False
        changed = False
        for op in operations:
            if op.invoiced != invoiced:
                op.invoiced = invoiced
                changed = True
        return changed

    def _toggle_invoice_mark(self, partner_id: str, day: date) -> None:
        """Клик по метке: красная <-> зелёная (реверсивное поле)."""

        invoiced = self._mark_color(partner_id, day) != styles.MARK_INVOICED
        if not self._set_invoice_mark(partner_id, day, invoiced):
            return
        self.store.save()
        self._update_calculated_cells()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def _refresh_marks(self) -> None:
        """Перекрасить все метки (после ввода суммы или переключения)."""

        if self.mark_index is None:
            return
        days = self._days[: self.table_days]
        for r, partner in enumerate(self._partners):
            row = FIRST_DATA_ROW + r
            for i, day in enumerate(days):
                item = self.table.item(
                    row, self._day_column(i, self.mark_index)
                )
                if item is not None:
                    self._paint_mark_cell(item, partner.id, day)

    def _update_calculated_cells(self) -> None:
        """Пересчитать Долг, «Итого»/«за неделю», ВСЕГО и тулбар без перестройки."""
        today = date.today()
        remaining_maps = self._remaining_due_maps()
        # v0.41.0: «Долг» в днях — архивный, без оплат.
        archival_maps = self._archival_due_maps()
        partner_ids = [p.id for p in self._partners]
        operations = self.store.data.operations
        # v0.22.1: «Итого» по всем поставщикам. v0.26.5: считается той
        # же функцией, что строка «Всего задолженность» в «Сводке»,
        # чтобы цифры совпадали.
        total_all = ZERO
        total_week = ZERO
        total_late = ZERO
        total_customer = ZERO
        total_customer_invoiced = ZERO
        total_customer_work = ZERO
        # v0.18.0: колонки дней в таблице — без воскресенья, расчёты
        # при этом идут по полной неделе (self._days / self._week_end).
        days = self._days[: self.table_days]
        self._rebuilding = True
        try:
            for r, remaining_map in enumerate(remaining_maps):
                row = FIRST_DATA_ROW + r
                # v0.41.0: «Итого»/«за неделю» — по остатку долга
                # после оплат, а ячейки дней «Долг» — по архиву.
                total, late, week = self._debt_columns(
                    remaining_map,
                    days,
                    today,
                    open_ended=calc.is_open_ended(self._partners[r]),
                    week_end=self._week_end,
                )
                total_all += total
                total_week += week
                total_late += late
                if self.kind == "supplier":
                    # v0.22.1: порядок столбцов — «Итого», «просрочено»,
                    # «за неделю».
                    self._set_cell_text(
                        row, 1 + self.lead_offset, _money_text(total)
                    )
                    self._set_cell_text(
                        row, 2 + self.lead_offset, _money_text(late)
                    )
                    self._set_cell_text(
                        row, 3 + self.lead_offset, _money_text(week)
                    )
                else:
                    # Покупатели (v0.29.0): «Всего» = «Выписано» +
                    # «В работе»; «Выписано» — реализация с зелёной
                    # меткой минус оплаты, «В работе» — с красной.
                    # v0.29.1: считаем за всё время (без границ недели),
                    # иначе при переходе на другую неделю долг
                    # «терялся» — как «Итого» у поставщиков и как
                    # задолженность покупателей в «Сводке».
                    partner = self._partners[r]
                    invoiced, work, debt = calc.customer_debt_split(
                        self.store.data, partner
                    )
                    total_customer += debt
                    total_customer_invoiced += invoiced
                    total_customer_work += work
                    self._set_cell_text(row, 1, _money_text(debt))
                    self._set_cell_text(row, 2, _money_text(invoiced))
                    self._set_cell_text(row, 3, _money_text(work))
                if "debt" in self.subheader_kinds:
                    debt_index = self.subheader_kinds.index("debt")
                    for i, day in enumerate(days):
                        self._set_cell_text(
                            row,
                            self._day_column(i, debt_index),
                            _money_text(archival_maps[r].get(day, ZERO)),
                        )
            if self.kind == "supplier":
                total_all = sum(
                    calc.supplier_debt_remaining(
                        self._partners, self.store.data, self._week_end
                    ).values(),
                    ZERO,
                )
                self._set_cell_text(
                    TOTAL_ROW, 1 + self.lead_offset, _money_text(total_all)
                )
                self._set_cell_text(
                    TOTAL_ROW, 2 + self.lead_offset, _money_text(total_late)
                )
                self._set_cell_text(
                    TOTAL_ROW, 3 + self.lead_offset, _money_text(total_week)
                )
            else:
                self._set_cell_text(TOTAL_ROW, 1, _money_text(total_customer))
                self._set_cell_text(
                    TOTAL_ROW, 2, _money_text(total_customer_invoiced)
                )
                self._set_cell_text(
                    TOTAL_ROW, 3, _money_text(total_customer_work)
                )
            for i, day in enumerate(days):
                for j, kind_name in enumerate(self.subheader_kinds):
                    if kind_name == "mark":
                        continue  # метка не суммируется
                    if kind_name == "debt":
                        # v0.41.0: строка «ВСЕГО» по дням — тоже архив.
                        amount = sum(
                            (m.get(day, ZERO) for m in archival_maps), ZERO
                        )
                        text = _money_text(amount)
                    else:
                        text = calc.week_column_total(
                            partner_ids, day, kind_name, operations
                        )
                    self._set_cell_text(TOTAL_ROW, self._day_column(i, j), text)
        finally:
            self._rebuilding = False
        # v0.29.0: метки перекрашиваем и здесь — сумма реализации могла
        # появиться или исчезнуть, а с ней и метка.
        self._refresh_marks()
        if self.kind == "supplier":
            self.right_value_label.setText(fmt_money(total_week))
        # v0.23.1: строка «ВСЕГО» закреплена — обновляем и её копию.
        self._sync_frozen()

    # ----------------------------------------------- закреплённая шапка
    def _sync_frozen(self) -> None:
        """Перерисовать закреплённые строки по содержимому таблицы."""

        source = self.table
        frozen = self.frozen_table
        rows = TOTAL_ROW + 1
        frozen.setColumnCount(self.column_count)
        frozen.setRowCount(rows)
        frozen.clearSpans()
        covered: set[tuple[int, int]] = set()
        for row in range(rows):
            frozen.setRowHeight(row, source.rowHeight(row))
            for column in range(self.column_count):
                frozen.setColumnWidth(column, source.columnWidth(column))
                item = source.item(row, column)
                cell = QTableWidgetItem(
                    item.text() if item is not None else ""
                )
                cell.setFlags(Qt.ItemFlag.NoItemFlags)
                if item is not None:
                    cell.setFont(item.font())
                    cell.setBackground(item.background())
                    cell.setForeground(item.foreground())
                    cell.setTextAlignment(Qt.Alignment(item.textAlignment()))
                frozen.setItem(row, column, cell)
                if (row, column) in covered:
                    continue
                row_span = source.rowSpan(row, column)
                column_span = source.columnSpan(row, column)
                if row_span > 1 or column_span > 1:
                    frozen.setSpan(row, column, row_span, column_span)
                    for spanned_row in range(row, row + row_span):
                        for spanned_column in range(
                            column, column + column_span
                        ):
                            covered.add((spanned_row, spanned_column))
        self._update_frozen_geometry()

    def _update_frozen_geometry(self) -> None:
        """Посадить закреплённую шапку на верхнюю кромку таблицы."""

        frame = self.table.frameWidth()
        height = sum(
            self.table.rowHeight(row) for row in range(TOTAL_ROW + 1)
        )
        origin = self.table.mapTo(self, QPoint(frame, frame))
        self.frozen_table.setGeometry(
            origin.x(), origin.y(), self.table.viewport().width(), height
        )
        self._sync_frozen_offset()

    def _sync_frozen_offset(self) -> None:
        """Повторить горизонтальную прокрутку основной таблицы."""

        self.frozen_table.horizontalScrollBar().setValue(
            self.table.horizontalScrollBar().value()
        )


    # --------------------------------------------------------------- данные
    def counterparties(self) -> list[Counterparty]:
        """Строки таблицы: неархивные контрагенты **по алфавиту**.

        v0.55.0: раньше порядок был как в файле данных — новый
        контрагент появлялся в конце списка, и искать его приходилось
        глазами. Теперь таблица всегда отсортирована по наименованию
        (просьба пользователя). Порядок в ``data.json`` не меняем:
        операции привязаны к идентификаторам, а не к месту в списке.
        """

        items = (
            self.store.data.suppliers
            if self.kind == "supplier"
            else self.store.data.customers
        )
        return sorted(
            (c for c in items if not c.archived), key=alphabetical_key
        )

    def refresh(self) -> None:
        partners = self.counterparties()
        days = week_days(self.week_start)
        self._partners = partners
        # Расчётная неделя — полная (пн–вс): долги и оплаты считаются
        # по всем семи дням, даже если в таблице их не показываем.
        self._days = days
        self._week_end = days[-1]
        today = date.today()
        set_date_edit(self.date_edit, self.week_start)
        self.week_label.setText(
            f"Неделя: {fmt_date(days[0])} — {fmt_date(days[-1])}"
        )
        if self.kind == "customer":
            self.right_value_label.setText(str(len(partners)))
        # В таблицу уходят только первые TABLE_DAYS дней (без воскресенья).
        self._rebuild_table(partners, days[: self.table_days], today)

    # --------------------------------------------------------------- таблица
    def _rebuild_table(self, partners, days, today) -> None:
        # Программная перестройка не должна срабатывать как «ввод пользователя».
        self._rebuilding = True
        try:
            self._rebuild_table_inner(partners, days, today)
        finally:
            self._rebuilding = False
        self._fit_columns_to_width()
        # v0.23.1: закреплённая шапка повторяет перестроенную таблицу.
        self._sync_frozen()

    def _rebuild_table_inner(self, partners, days, today) -> None:
        table = self.table
        table.clear()
        table.clearSpans()
        self._amount_items = []
        table.setRowCount(FIRST_DATA_ROW + len(partners))
        table.setColumnCount(self.column_count)

        for row in range(HEADER_ROWS):
            table.setRowHeight(row, 38)  # три строки шапки под общий шрифт

        def put(row, col, text, *, bg=None, bold=False, center=True,
                editable=False, color=None, underline=False, size=None):
            item = QTableWidgetItem(text)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if editable:
                flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            if bg:
                item.setBackground(QColor(bg))
            if center:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            font = QFont()
            font.setBold(bold)
            font.setUnderline(underline)
            if size:
                font.setPixelSize(size)
            item.setFont(font)
            if color:
                item.setForeground(QColor(color))
            table.setItem(row, col, item)
            return item

        def money_cell(row, col, text, **kwargs) -> QTableWidgetItem:
            """Ячейка суммы: правый край + шрифт, который потом подгонит _fit_*."""
            cell = put(row, col, text, size=styles.fs(12), **kwargs)
            cell.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._amount_items.append(cell)
            return cell

        def day_body_bg(day: date) -> str | None:
            """Фон ячеек под датой: розовые выходные и весь столбец «сегодня».

            v0.38.0 (просьба пользователя): колонка текущей даты
            красится **на всю высоту**, а не только шапкой. Цвет взят
            светлее шапочного, чтобы таблица не пестрела.
            """
            if day == today:
                return styles.WEEK_TABLE_TODAY_GREEN_BODY
            if calc.is_day_off(day, self.store.data.day_overrides):
                return styles.WEEK_TABLE_WEEKEND_PINK
            return None

        def day_header_bg(day: date) -> str | None:
            """Фон верхней ячейки даты: сегодня — салатовый, иначе как тело."""
            if day == today:
                return styles.WEEK_TABLE_TODAY_GREEN
            return day_body_bg(day)

        # Строка 0: месяцы (неделя может захватывать 1–2 месяца)
        lead = self.lead_columns + self.lead_offset
        put(0, 0, "")
        table.setSpan(0, 0, 1, 1 + lead)
        col = 1 + lead
        for (_year, _month), group in groupby(days, key=lambda d: (d.year, d.month)):
            group_days = list(group)
            put(0, col, month_title(group_days[0]), bold=True,
                size=styles.fs(12))
            table.setSpan(0, col, 1, len(group_days) * self.columns_per_day)
            col += len(group_days) * self.columns_per_day

        # Строка 1: дни недели (шрифт шапки увеличен по запросу пользователя)
        put(1, 0, "ПОСТАВЩИК" if self.kind == "supplier" else "ПОКУПАТЕЛЬ",
            bold=True, size=styles.fs(12))
        # v0.14.0: «ПРОСРОЧЕН.» и «ЗАДОЛЖ.» — разные ячейки, без
        # объединения: каждая подпись стоит над своей колонкой.
        # v0.27.3: узкая колонка условий (только у поставщиков) входит
        # в объединённую шапку названия: своей подписи у неё нет, смысл
        # — в подзаголовке «Отсрочка» строкой ниже. У покупателей
        # lead_offset = 0, объединение не нужно (Qt на него ругается).
        if self.lead_offset:
            table.setSpan(1, 0, 1, 1 + self.lead_offset)
        # v0.16.0: три итоговые колонки объединены одной шапкой, а их
        # смысловые подписи стоят строкой ниже. v0.28.3: шапка — слово
        # «Задолженность» целиком, посередине объединённой ячейки
        # (просьба пользователя). v0.29.0: то же самое у покупателей —
        # раньше там было сокращение «ЗАДОЛЖ.».
        put(1, 1 + self.lead_offset, "Задолженность", bold=True,
            size=styles.fs(12), center=True)
        table.setSpan(1, 1 + self.lead_offset, 1, self.lead_columns)
        for i, day in enumerate(days):
            c = self._day_column(i, 0)
            put(1, c, f"{day.day}  {WEEKDAYS_RU_SHORT[day.weekday()]}",
                bg=day_header_bg(day), bold=True, size=styles.fs(12))
            table.setSpan(1, c, 1, self.columns_per_day)

        # Строка 2: подзаголовки; выходные розовые, сегодня здесь без заливки.
        put(2, 0, "")
        if self.kind == "supplier":
            # v0.27.3: узкая колонка условий — «П»/«Р»/дней отсрочки.
            # v0.28.2: подпись узкой колонки — в две строки, обычным
            # начертанием (просьба пользователя).
            put(2, 1, "От-\nсрочка", size=styles.fs(12))
            # v0.22.1: вместо «на сегодня» — «Итого», оно слева от
            # «Просрочено».
            put(2, 1 + self.lead_offset, "Итого", bold=True,
                size=LEAD_FONT_SIZE)
            # v0.28.3: подписи целиком и с большой буквы — «Просрочено»
            # и «Неделя» (были «просрочено» и «за неделю»).
            put(2, 2 + self.lead_offset, "Просрочено", bold=True,
                size=LEAD_FONT_SIZE)
            put(2, 3 + self.lead_offset, "Неделя", bold=True,
                size=LEAD_FONT_SIZE)
        else:
            # v0.29.0: «Всего» = «Выписано» + «В работе»; порядок — по
            # просьбе пользователя (итог левее слагаемых).
            put(2, 1, "Всего", bold=True, size=LEAD_FONT_SIZE)
            put(2, 2, "Выписано", bold=True, size=LEAD_FONT_SIZE)
            put(2, 3, "В работе", bold=True, size=LEAD_FONT_SIZE)
        for i, day in enumerate(days):
            for j, sub in enumerate(self.subheaders):
                cell = put(2, self._day_column(i, j), sub,
                           bg=day_body_bg(day), bold=True, size=styles.fs(12))
                if self.subheader_kinds[j] == "mark":
                    cell.setToolTip(
                        "Метка «выписано»: красная — реализация в работе, "
                        "зелёная — документы выписаны. Клик переключает "
                        "цвет. Метка появляется, когда в «Реализации» "
                        "есть сумма"
                    )

        operations = self.store.data.operations
        partner_ids = [p.id for p in partners]
        remaining_maps = self._remaining_due_maps()
        # v0.41.0: «Долг» в днях — архивный график без оплат; по
        # остатку считаются только «Итого»/«Просрочено»/«Неделя».
        archival_maps = self._archival_due_maps()
        debt_columns = [
            self._debt_columns(
                m,
                days,
                today,
                open_ended=calc.is_open_ended(partner),
                week_end=self._week_end,
            )
            for m, partner in zip(remaining_maps, partners)
        ]

        # Строка «ВСЕГО»
        # v0.22.1: debt_columns = (Итого, просрочено, за неделю).
        total_all = sum((c[0] for c in debt_columns), ZERO)
        total_late = sum((c[1] for c in debt_columns), ZERO)
        total_week = sum((c[2] for c in debt_columns), ZERO)
        # Покупатели: задолженность = Реализация − Оплата за неделю
        # (v0.15.0). Считается до строки «ВСЕГО», поэтому собирается
        # заранее, а не по ходу вывода строк контрагентов.
        # v0.29.0: (Выписано, В работе, Всего) по каждому покупателю.
        customer_splits: list[tuple[Decimal, Decimal, Decimal]] = []
        if self.kind != "supplier":
            # v0.26.0: плашка «Данных» удалена — считаем только операции.
            # v0.29.1: итоги покупателя — за всё время, без границ
            # недели: долг накапливается и виден на любой неделе
            # (границы были причиной «пустых» колонок при переходе).
            customer_splits = [
                calc.customer_debt_split(self.store.data, partner)
                for partner in partners
            ]
            total_customer_debt = sum((split[2] for split in customer_splits), ZERO)
            total_customer_invoiced = sum(
                (split[0] for split in customer_splits), ZERO
            )
            total_customer_work = sum((split[1] for split in customer_splits), ZERO)
        put(TOTAL_ROW, 0, "ВСЕГО", bold=True, center=False,
            size=styles.fs(12))
        total_columns = (
            (
                (1 + self.lead_offset, total_all),
                (2 + self.lead_offset, total_late),
                (3 + self.lead_offset, total_week),
            )
            if self.kind == "supplier"
            else (
                (1, total_customer_debt),
                (2, total_customer_invoiced),
                (3, total_customer_work),
            )
        )
        # v0.27.3: в колонке условий у «ВСЕГО» пусто — суммы условий
        # не бывает.
        if self.lead_offset:
            put(TOTAL_ROW, 1, "", bold=True, size=styles.fs(12))
        for col_idx, amount in total_columns:
            # v0.56.0: шрифт итоговых колонок меньше основного
            # (просьба пользователя) — и в шапке, и в значениях.
            cell = put(TOTAL_ROW, col_idx, _money_text(amount), bold=True,
                       size=LEAD_FONT_SIZE)
            cell.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        for i, day in enumerate(days):
            for j, kind_name in enumerate(self.subheader_kinds):
                if kind_name == "mark":
                    # v0.29.0: в строке «ВСЕГО» метки не суммируются.
                    put(TOTAL_ROW, self._day_column(i, j), "",
                        bg=day_body_bg(day), bold=True, size=styles.fs(12))
                    continue
                if kind_name == "debt":
                    # v0.41.0: «Долг» в днях — архивная сумма поставок
                    # со сроком в этот день (оплаты её не уменьшают).
                    amount = sum((m.get(day, ZERO) for m in archival_maps), ZERO)
                    value = _money_text(amount)
                else:
                    value = calc.week_column_total(
                        partner_ids, day, kind_name, operations
                    )
                money_cell(
                    TOTAL_ROW, self._day_column(i, j), value,
                    bg=day_body_bg(day), bold=True,
                )

        # Строки контрагентов
        for r, partner in enumerate(partners):
            row = FIRST_DATA_ROW + r
            # v0.41.0: ячейки дней показывают архивную сумму поставки
            # (оплата её не уменьшает), а не остаток долга.
            due_map = archival_maps[r]
            total, late, week = debt_columns[r]
            # v0.55.0: шрифт названия меньше основного (просьба
            # пользователя) — длинные наименования влезают целиком.
            name_item = put(row, 0, partner.name, center=False,
                            color=styles.LINK_BLUE, underline=True,
                            size=NAME_FONT_SIZE)
            name_item.setData(Qt.ItemDataRole.UserRole, partner.id)
            if self.kind == "supplier":
                name_item.setToolTip(
                    "Клик — журнал операций этого поставщика; двойной "
                    "клик — окно «Поставка»"
                )
            elif self.kind == "customer":
                name_item.setToolTip(
                    "Клик — журнал операций этого покупателя; двойной "
                    "клик — окно «Реализация»"
                )
            # «Итого», «просрочено» / задолженность «за неделю» (calc);
            # у покупателей — одна колонка «Всего» = Реализация − Оплата.
            if self.kind == "supplier":
                # v0.27.3: условия поставщика — узкая колонка справа от
                # названия. Подсказка расшифровывает «П» и «Р».
                # v0.28.2: значения «П»/«Р»/дней — без жирного
                # начертания (просьба пользователя).
                conditions = put(
                    row, 1, self._conditions_text(partner),
                    size=styles.fs(12),
                )
                conditions.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                conditions.setToolTip(
                    "Условия: П — предоплата, Р — реализация, "
                    "число — срок отсрочки в днях"
                )
                # v0.22.1: «Итого» стоит слева от «просрочено».
                lead_amounts = (
                    (1 + self.lead_offset, total),
                    (2 + self.lead_offset, late),
                    (3 + self.lead_offset, week),
                )
            else:
                # v0.29.0: «Всего» = «Выписано» + «В работе».
                invoiced, work, debt = customer_splits[r]
                lead_amounts = ((1, debt), (2, invoiced), (3, work))
            for col_idx, amount in lead_amounts:
                # v0.56.0: тот же шрифт, что у подписей итоговых колонок.
                cell = put(row, col_idx, _money_text(amount),
                           size=LEAD_FONT_SIZE)
                cell.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            for i, day in enumerate(days):
                for j, kind_name in enumerate(self.subheader_kinds):
                    col_idx = self._day_column(i, j)
                    meta = f"{partner.id}|{day.isoformat()}|{kind_name}"
                    if kind_name == "mark":
                        # v0.29.0: узкая колонка метки «выписано».
                        self._put_mark_cell(row, col_idx, partner.id, day)
                        continue
                    if kind_name == "debt":
                        cell = money_cell(
                            row, col_idx, _money_text(due_map.get(day, ZERO)),
                            bg=day_body_bg(day),
                        )
                        cell.setToolTip(
                            "Долг со сроком оплаты в этот день: сумма "
                            "поставок, чей срок — этот день. Сумма "
                            "архивная, оплаты её не уменьшают; результат "
                            "оплаты виден в столбцах «Итого», "
                            "«Просрочено» и «Неделя»"
                        )
                        continue

                    value = calc.week_cell_value(
                        partner.id, day, kind_name, operations
                    )
                    cell = money_cell(
                        row, col_idx, value,
                        bg=day_body_bg(day), editable=True,
                    )
                    cell.setData(META_ROLE, meta)
                    if kind_name == "income":
                        cell.setToolTip(
                            "Приход: введите сумму и нажмите Enter. Срок "
                            "оплаты посчитается по отсрочке и календарю "
                            "«Данные». Пустая ячейка — удалить."
                        )
                    else:
                        cell.setToolTip(
                            f"{self.subheaders[j]}: введите сумму и нажмите Enter. "
                            "Пустая ячейка — удалить."
                        )

        if self.kind == "supplier":
            self.right_value_label.setText(fmt_money(total_week))

    # ---------------------------------------------------- ширина на весь экран
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_columns_to_width)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # v0.40.0: ширина закреплённой шапки и колонок зависит от
        # размера таблицы — пересчитываем и то, и другое.
        self._update_frozen_geometry()
        # Отложенно: viewport таблицы получает финальный размер чуть позже
        # самого resizeEvent (особенно при первом показе вкладки).
        QTimer.singleShot(0, self._fit_columns_to_width)

    def _column_widths(self, available: int) -> list[int]:
        """Ширины колонок, которые дают ровно ``available`` пикселей (v0.40.0).

        Между минимальной и базовой шириной колонка занимает
        пропорциональную долю. Если не влезают даже минимумы, они
        сжимаются пропорционально — так горизонтальной прокрутки нет
        ни при какой ширине окна.
        """

        total_base = sum(self.base_widths)
        total_min = sum(self.min_widths)
        if available >= total_base:
            widths = [
                max(min_w, available * base // total_base)
                for base, min_w in zip(self.base_widths, self.min_widths)
            ]
        elif available >= total_min:
            # Между минимумом и базой: к минимуму добавляем свою долю
            # разницы — колонки сужаются плавно и все вместе.
            extra = available - total_min
            span = total_base - total_min or 1
            widths = [
                min_w + extra * (base - min_w) // span
                for base, min_w in zip(self.base_widths, self.min_widths)
            ]
        else:
            # Уже минимумы не влезают: сжимаем их пропорционально.
            # Иначе Qt покажет горизонтальную полосу прокрутки, а её
            # быть не должно (просьба пользователя).
            widths = [
                max(1, min_w * available // total_min)
                for min_w in self.min_widths
            ]
        # Остаток от деления раздаём по одному пикселю: сумма должна
        # быть ровно available, иначе появится полоса прокрутки.
        diff = available - sum(widths)
        if diff > 0:
            while diff > 0:
                for col in range(self.column_count):
                    if diff == 0:
                        break
                    widths[col] += 1
                    diff -= 1
        while diff < 0:
            shrinked = False
            for col in range(self.column_count):
                if diff == 0:
                    break
                if widths[col] > 1:
                    widths[col] -= 1
                    diff += 1
                    shrinked = True
            if not shrinked:
                break
        return widths

    def _fit_columns_to_width(self) -> None:
        """Растянуть колонки ровно на ширину таблицы и подогнать шрифт сумм.

        Ширина раздаётся пропорционально базовым весам текущей таблицы;
        ниже минимальных ширин колонки сжимаются (v0.40.0), поэтому
        горизонтальной прокрутки нет ни при какой ширине окна.
        """
        table = getattr(self, "table", None)
        if table is None or table.columnCount() != self.column_count:
            return
        available = table.viewport().width()
        if available <= 0:
            widths = list(self.base_widths)
        else:
            widths = self._column_widths(available)
        for col, width in enumerate(widths):
            table.setColumnWidth(col, width)
            if self.frozen_table.columnCount() == self.column_count:
                self.frozen_table.setColumnWidth(col, width)
        # v0.18.0: ширину берём у колонки дня (первая после итоговых),
        # а не у фиксированного индекса: тот указывал на итоговую
        # колонку, и шрифт сумм подбирался слишком крупным —
        # «88 888,88» не влезало.
        # v0.29.0: узкую колонку метки не учитываем — иначе шрифт
        # подбирался бы по 30 px и числа стали бы нечитаемыми.
        money_columns = [
            self._day_column(day_index, subcolumn)
            for day_index in range(self.table_days)
            for subcolumn in range(self.columns_per_day)
            if subcolumn != self.mark_index
        ]
        self._fit_amount_font(min(widths[column] for column in money_columns))
        # v0.40.0: закреплённая шапка должна лечь на те же колонки.
        self._update_frozen_geometry()

    def _fit_amount_font(self, day_column_width: int) -> None:
        """Подобрать размер шрифта сумм так, чтобы «88 888,88» влезало целиком."""
        if not self._amount_items:
            return
        chosen = 8
        # Верхняя граница тоже увеличена: шрифт интерфейса +30% (v0.12.0).
        for size in range(styles.fs(14), 7, -1):
            font = QFont(self.table.font())
            font.setPixelSize(size)
            font.setBold(True)  # по жирной строке ВСЕГО — худший случай
            if QFontMetrics(font).horizontalAdvance(MAX_AMOUNT_TEXT) <= (
                day_column_width - 8
            ):
                chosen = size
                break
        self._rebuilding = True  # setFont тоже может дёрнуть itemChanged
        try:
            for item in self._amount_items:
                font = item.font()
                if font.pixelSize() != chosen:
                    font.setPixelSize(chosen)
                    item.setFont(font)
        except RuntimeError:
            self._amount_items = []  # таблица уже перестроена, ячейки мертвы
        finally:
            self._rebuilding = False
