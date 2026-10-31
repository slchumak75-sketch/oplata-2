"""Экран «Поступление и оплаты» (вкладка главного окна, v0.11.0).

Как устроен экран (по запросам пользователя):

* слева — два блока друг под другом: **«Поступление»** и **«Оплата»**.
  Они занимают половину ширины окна (плашки «в два раза уже»);
* справа на всю высоту — панель **«Сводка»** (прежняя вкладка «Сводка»
  удалена, её содержимое живёт здесь);
* вверху каждого блока **одна строчка**: заголовок, подпись «Дата»,
  поле даты с выпадающим календарём, кнопка «Сегодня» и стрелки
  «←»/«→»;
* ниже в блоке — **таблица** со столбцами «Сумма / дни текущей
  недели», где «Сумма» — за выбранную дату, а колонки дней (Пн–Сб)
  показывают сумму за каждый день (v0.22.0; раньше здесь были
  нарастающие итоги «Неделя / Месяц / Квартал / Год»)
  (понедельника недели, 1-го числа месяца, 1 января) по выбранную дату
  включительно. Это агрегаты по сохранённым записям, без формул долгов
  (ADR-017).

Строки блока «Поступление»: «Поступление наличными», «Поступление по
картам» (суммы вводятся **прямо в ячейку**) и «Покупатели» (клик открывает
окно справа со списком покупателей и полями сумм; поля при повторном
открытии заполнены уже введёнными суммами за эту дату).

Строки блока «Оплата»: «Поставщики» (то же окно справа) и **прочие виды
оплат**, которые пользователь добавляет сам кнопкой **«+»**: название
вводится текстом прямо в ячейку, сумма — в ячейку «Сумма». Кнопка
**«−»** убирает выделенную строку.

Куда попадают суммы (новых формул нет, см. ADR-016/017/018):

* наличные/карты — записи ``AppData.cash_receipts`` (контрагента нет, в
  недельных таблицах не участвуют), одна запись на день и источник;
* покупатели/поставщики — операции «Оплата» контрагента на дату, то есть
  те же записи, что показывает колонка «Оплата» недельных таблиц.
  Поэтому суммы видны в обе стороны: что введено здесь — появляется в
  таблице поставщиков/покупателей, и наоборот;
* прочие виды оплат — записи ``AppData.other_payments``: своё название и
  сумма за дату, итоги считаются по названию.
"""

from __future__ import annotations

import calendar as calendar_module
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import QDate, QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFontMetrics
from PySide6.QtWidgets import QStyle, QStyleOptionHeader
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QAbstractItemDelegate,
    QApplication,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import calc, styles
from ..dates import (
    MONTHS_RU,
    WEEKDAYS_RU_TITLE,
    fmt_money_plain,
    parse_money,
    week_days,
    week_monday,
)
from ..models import (
    CARD_SOURCE,
    CASH_SOURCE,
    INCOMING,
    KIND_PAYMENT,
    OUTGOING,
    add_other_payment,
    cash_receipt_amount,
    delete_other_payment,
    operation_amount,
    other_payment_amount,
    other_payment_names,
    rename_other_payment,
    set_daily_cash_receipt,
    set_daily_operation,
    set_daily_other_payment,
)
from ..storage import DataStore
from ..widgets import (
    AmountLineEdit,
    RowHoverMixin,
    arrow_button,
    blue_button,
    install_row_hover,
    make_amount_edit,
    make_date_edit,
)

CASH_ROW = "cash"
CARD_ROW = "card"
CUSTOMERS_ROW = "customers"
SUPPLIERS_ROW = "suppliers"

# Префикс ключей строк, которые добавляет пользователь кнопкой «+»:
# OTHER_PREFIX + название. В блоке «Поступление» это «прочие поступления»,
# в блоке «Оплата» — «прочие виды оплат» (v0.12.0).
OTHER_PREFIX = "other:"
NEW_ROW_NAME = {
    INCOMING: "Новое поступление",
    OUTGOING: "Новый вид оплаты",
}
NEW_PAYMENT_NAME = NEW_ROW_NAME[OUTGOING]  # короткий псевдоним
# Сколько строк видно в блоке без прокрутки; дальше включается скроллбар.
MAX_VISIBLE_ROWS = 8

def other_row_key(name: str) -> str:
    return f"{OTHER_PREFIX}{name}"


def other_row_name(key: str) -> str:
    return key[len(OTHER_PREFIX):] if key.startswith(OTHER_PREFIX) else ""


# Постоянная строка «Прочие поступления» в блоке «Поступление» (v0.13.0):
# она не добавляется кнопкой «+» и не удаляется — только сумма в ячейку.
OTHER_INCOME_NAME = "Прочие поступления"
FIXED_OTHER_ROW = other_row_key(OTHER_INCOME_NAME)


ROW_TITLES = {
    CASH_ROW: "Поступление наличными",
    CARD_ROW: "Поступление по картам",
    CUSTOMERS_ROW: "Покупатели",
    SUPPLIERS_ROW: "Поставщики",
}

# Строки, сумму которых вводят прямо в ячейку (прочие поступления
# тоже вводятся в ячейку, но без названия — строка постоянная).
DIRECT_INPUT_ROWS = (CASH_ROW, CARD_ROW, FIXED_OTHER_ROW)
# Строки, по клику открывающие окно со списком контрагентов.
# Строки-контрагенты: по клику открывается окно со списком людей и
# полями сумм. Остальные строки-источники (касса, карты, прочие виды)
# открывают **журнал операций** этого вида (v0.20.0).
POPUP_ROWS = (CUSTOMERS_ROW, SUPPLIERS_ROW)
JOURNAL_ROWS = (CASH_ROW, CARD_ROW)


def is_popup_row(key: str) -> bool:
    """Открывать ли по клику выпадающее окно (контрагенты или журнал)."""

    return (
        key in POPUP_ROWS or key in JOURNAL_ROWS or key.startswith(OTHER_PREFIX)
    )


def _fmt_day(iso: str) -> str:
    """``2026-10-28`` -> ``28.10.2026`` (без падения на битых данных)."""

    if len(iso) == 10 and iso[4] == "-" and iso[7] == "-":
        return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"
    return iso

COL_NAME, COL_AMOUNT = 0, 1
# v0.22.0: вместо итоговых колонок «Неделя / Месяц / Квартал / Год» в
# таблице стоят колонки дней текущей недели — как в недельных таблицах,
# шесть дней (Пн–Сб, воскресенья нет, v0.19.0).
FIRST_DAY_COL = 2
TABLE_DAYS = 6
COLUMN_COUNT = FIRST_DAY_COL + TABLE_DAYS
WEEKDAY_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


class DayHeaderView(QHeaderView):
    """Горизонтальная шапка с подсветкой отдельных дней (v0.23.7).

    Фон секции рисуется самим классом: в стиле Windows Qt игнорирует
    ``BackgroundRole`` ячейки шапки, поэтому ``setBackground`` у
    ``horizontalHeaderItem`` там не видно — приходится заливать
    прямоугольник секции перед штатной отрисовкой.
    """

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, table)
        self._colors: dict[int, QColor] = {}

    def set_section_colors(self, colors: dict[int, QColor]) -> None:
        """Задать фон секциям шапки: ``{номер колонки: цвет}``."""

        self._colors = dict(colors)
        self.viewport().update()

    def section_color(self, column: int) -> QColor | None:
        """Фон секции ``column`` (``None`` — без подсветки)."""

        return self._colors.get(column)

    def paintSection(self, painter, rect, logicalIndex) -> None:  # noqa: N802
        color = self._colors.get(int(logicalIndex))
        if color is None:
            super().paintSection(painter, rect, logicalIndex)
            return
        # Штатная отрисовка секции заливает свой фон из палитры и
        # перекрывает заливку, поэтому подсвеченную секцию рисуем сами:
        # сначала фон, потом только текст (CE_HeaderLabel).
        painter.save()
        painter.fillRect(rect, color)
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = int(logicalIndex)
        model = self.model()
        option.text = str(
            model.headerData(
                int(logicalIndex),
                Qt.Orientation.Horizontal,
                Qt.ItemDataRole.DisplayRole,
            )
            or ""
        )
        alignment = model.headerData(
            int(logicalIndex),
            Qt.Orientation.Horizontal,
            Qt.ItemDataRole.TextAlignmentRole,
        )
        if alignment is not None:
            option.textAlignment = Qt.AlignmentFlag(int(alignment))
        font = model.headerData(
            int(logicalIndex), Qt.Orientation.Horizontal, Qt.ItemDataRole.FontRole
        )
        option.font = font if font is not None else self.font()
        option.fontMetrics = QFontMetrics(option.font)
        self.style().drawControl(
            QStyle.ControlElement.CE_HeaderLabel, option, painter, self
        )
        painter.restore()
# Ширины числовых колонок подобраны под шрифт +30% (v0.12.0): в них
# влезает «88 888,88», а колонке названия остаётся место на
# «Поступление наличными».
COLUMN_WIDTHS = {COL_AMOUNT: 104}
# Колонки дней уже итогов «Неделя/Месяц/Квартал/Год»: в них день и сумма
# за день («88 888,88» влезает и в 92 px).
DAY_COLUMN_WIDTH = 96
# Высота строки: с запасом под перенос длинного названия строки на две
# строки (wordWrap включён, колонка названия ограничена пятью
# числовыми колонками).
ROW_HEIGHT = 40

ZERO = Decimal("0")


def _today() -> date:
    return date.today()


def _qdate_to_date(qdate) -> date:
    return date(qdate.year(), qdate.month(), qdate.day())


def _money_text(amount: Decimal) -> str:
    """Пусто вместо ``0,00`` — как в недельных таблицах (макет 02/03)."""

    return fmt_money_plain(amount) if amount else ""


class _HoverDelegate(RowHoverMixin, QStyledItemDelegate):
    """Обычная ячейка блока + бежевая подсветка строки под курсором."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        # v0.26.1: ввод встаёт ровно на место значения — без этого текст
        # «прыгал» влево и строка после клика выглядела иначе.
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # v0.26.1: рамка снимается и здесь — на Windows она
            # проявлялась синими штрихами по краям ячейки.
            editor.setFrame(False)
        return editor


class AmountItemDelegate(RowHoverMixin, QStyledItemDelegate):
    """Редактор суммы для ячейки: без мешающих нулей (AmountLineEdit).

    Ссылку на созданный редактор держим сами: иначе Python-обёртка
    остаётся без владельца и сборка мусора падает на уже удалённом
    C++-объекте (видели ``none_dealloc`` на прогонах тестов).
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._editors: list[AmountLineEdit] = []

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt API
        editor = AmountLineEdit(parent=parent)
        # v0.26.1: сумма в ячейке по центру (v0.23.9) — ввод тоже по
        # центру, иначе при клике число сдвигалось.
        editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        editor.setFrame(False)  # v0.26.1: без рамки (синие штрихи)
        self._editors.append(editor)
        editor.destroyed.connect(
            lambda *_: self._editors.remove(editor)
            if editor in self._editors
            else None
        )
        return editor


class AmountPopup(QFrame):
    """Окно справа: список контрагентов с полем суммы у каждого.

    Поля при открытии **уже заполнены** сохранёнными суммами за дату
    (иначе повторное открытие выглядит как «суммы пропали»). Внизу окна
    виден итог по всем покупателям — он пересчитывается по мере ввода.
    ``on_save`` получает словарь ``{id: Decimal}``: непустые значения
    записываются, пустые поля означают «убрать сумму этого дня».
    """

    def __init__(
        self,
        title: str,
        entries: list[tuple[str, str]],
        on_save: Callable[[dict[str, Decimal]], None],
        parent: QWidget | None = None,
        hint: str = "",
        values: dict[str, Decimal] | None = None,
        total_caption: str = "Итого по покупателям:",
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("Popup")
        self.setStyleSheet(
            "QFrame#Popup{background:#ffffff;border:1px solid #b9c9ef;"
            "border-radius:10px}"
            f"QLabel#PopupTitle{{font-weight:800;font-size:{styles.fs(15)}px}}"
            f"QLabel#PopupTotal{{font-weight:800;font-size:{styles.fs(15)}px;"
            f"color:{styles.BLUE}}}"
        )
        # Окно НЕ удаляем на Qt-уровне при каждом закрытии: объект
        # живёт, пока его не заменит следующее открытие (см.
        # ``ReceiptsView.open_popup``). При удалении окна Qt (вместе с
        # главным окном) Python-ссылка снимается через ``destroyed``.
        self._on_save = on_save
        # v0.22.1: у блока «Оплата» итог подписывается по поставщикам.
        self._total_caption = total_caption
        self.edits: dict[str, AmountLineEdit] = {}
        # Были ли уже сохранённые суммы: если да, то пустые поля — это
        # «убрать суммы», а не «нечего сохранять».
        self._had_values = any(
            value for value in (values or {}).values() if value
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QLabel(title)
        header.setObjectName("PopupTitle")
        root.addWidget(header)

        fields = QWidget()
        fields_layout = QVBoxLayout(fields)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fields_layout.setSpacing(6)
        for key, name in entries:
            fields_layout.addLayout(
                self._make_row(name, key, (values or {}).get(key))
            )

        # v0.23.1: список контрагентов прокручивается всегда, а окно
        # растёт до высоты экрана (запрос пользователя). Скроллбар —
        # широкий и закреплённый, такой же, как на плашке «Оплата».
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(fields)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        # v0.23.1: вместе со скроллбаром задаём и белый фон — иначе
        # своя таблица стилей виджета отключает правила APP_STYLESHEET
        # и под списком проглядывает серый фон (в окнах «Покупатели»
        # и «Поставщики»).
        scroll.setStyleSheet(styles.WIDE_SCROLLBAR_ON_AREA)
        self.scroll_area = scroll
        root.addWidget(scroll, stretch=1)

        total_row = QHBoxLayout()
        total_row.addStretch(1)
        total_caption = QLabel(self._total_caption)
        total_caption.setStyleSheet("font-weight:700")
        total_row.addWidget(total_caption)
        self.total_label = QLabel("0,00")
        self.total_label.setObjectName("PopupTotal")
        total_row.addWidget(self.total_label)
        root.addLayout(total_row)

        # Кнопок «Закрыть»/«Сохранить» нет (v0.12.0): окно сохраняет
        # введённое и скрывается, как только вы кликнете левой кнопкой
        # в любом месте вне полей ввода сумм. Пояснений по запросу
        # v0.13.0 в окне нет — только заголовок, поля и итог.
        self._closing = False  # True — закрыть без сохранения (Esc)
        self._watching = False  # слушаем клики по всему приложению
        self._update_total()

    # ------------------------------------------------------------------ вид
    def _make_row(
        self, name: str, key: str, value: Decimal | None = None
    ) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(10)
        label = QLabel(name)
        label.setMinimumWidth(200)
        label.setMaximumWidth(320)
        layout.addWidget(label, stretch=1)
        # make_amount_edit сам убирает нулевое значение: «0,00» не мешает.
        edit = make_amount_edit(_money_text(value or ZERO))
        edit.setPlaceholderText("0,00")
        edit.setFixedWidth(170)
        edit.returnPressed.connect(self._save)
        # Итог пересчитывается на каждый ввод, а не только по «Сохранить».
        edit.textChanged.connect(lambda *_: self._update_total())
        self.edits[key] = edit
        layout.addWidget(edit)
        return layout

    def _update_total(self) -> None:
        total = ZERO
        for edit in self.edits.values():
            text = edit.text().strip()
            if not text:
                continue
            try:
                value = parse_money(text)
            except ValueError:
                continue
            if value is not None:
                total += value
        self.total_label.setText(fmt_money_plain(total))

    def open_beside(self, widget: QWidget, row_top: int = 0) -> None:
        """Показать окно **справа** от ``widget`` на уровне строки.

        Запрос пользователя: окно покупателей всплывает справа. Если
        справа места нет (таблица занимает половину окна, справа —
        «Сводка»), окно прижимается к правому краю экрана, но остаётся
        справа, а не под строкой.
        """

        anchor = widget.mapToGlobal(QPoint(widget.width(), row_top))
        self.adjustSize()
        screen = self.screen()
        if screen is not None:
            # v0.23.1: окно занимает всю доступную высоту экрана, чтобы
            # список контрагентов был виден целиком, насколько возможно.
            free = screen.availableGeometry()
            height = min(self.height(), free.height() - 16)
            self.resize(self.width(), height)
            self.move(QPoint(anchor.x() + 8, anchor.y()))
            self._watch_clicks()
            self.show()
            # не даём окну уехать за край экрана
            frame = self.frameGeometry()
            x = min(frame.x(), free.right() - frame.width() - 8)
            y = min(frame.y(), free.bottom() - frame.height() - 8)
            self.move(max(free.left() + 8, x), max(free.top() + 8, y))
        else:
            self.move(QPoint(anchor.x() + 8, anchor.y()))
            self._watch_clicks()
            self.show()
        # Фокус и выделение текста в первое поле НЕ ставим (v0.12.0):
        # подсветка мешала — сохранённая сумма выглядела «выбранной»,
        # и любая цифра затирала её вместо дописывания.

    # ------------------------------------------------------------- сохранение
    def collected(self) -> dict[str, Decimal]:
        """Разобрать поля. Пустые пропускаются; мусор -> ``ValueError``."""

        result: dict[str, Decimal] = {}
        for key, edit in self.edits.items():
            text = edit.text().strip()
            if not text:
                continue
            try:
                value = parse_money(text)
            except ValueError:
                raise ValueError(f"Не понимаю сумму: {text}") from None
            if value is None:
                continue
            if value <= 0:
                raise ValueError(f"Сумма должна быть больше нуля: {text}")
            result[key] = value
        return result

    def _commit(self) -> bool:
        """Записать введённое. ``False`` — остаться в окне (ошибка ввода)."""

        try:
            values = self.collected()
        except ValueError as error:
            QMessageBox.warning(self, "Проверьте суммы", str(error))
            return False
        # Пустые поля означают «убрать сумму»: сохраняем и их, но нулями.
        result: dict[str, Decimal] = {key: ZERO for key in self.edits}
        for key, value in values.items():
            result[key] = value
        if not values and not self._had_values:
            return True  # ничего не вводили — просто закрываем окно
        self._on_save(result)
        return True

    def _save(self) -> None:
        """Сохранить и скрыть (по клику вне поля ввода)."""

        if self._commit():
            self._closing = True
            self.hide()

    def _commit_and_hide(self) -> None:
        self._save()

    def hideEvent(self, event):  # noqa: N802 - Qt API
        # Сохранение делается до скрытия (в mousePressEvent и в
        # eventFilter) — здесь только снимаем слушатель кликов.
        self._stop_watching()
        super().hideEvent(event)

    def _watch_clicks(self) -> None:
        """Слушать клики по приложению: клик вне окна — сохранение."""

        app = QApplication.instance()
        if app is not None and not self._watching:
            app.installEventFilter(self)
            self._watching = True

    def _stop_watching(self) -> None:
        if not self._watching:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._watching = False

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if (
            self.isVisible()
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            widget = obj if isinstance(obj, QWidget) else None
            if widget is not None and not self.isAncestorOf(widget):
                # Клик вне окна: сохраняем, Qt скроет окно сам.
                self._save()
        return False

    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        """Клик вне поля ввода суммы — сохранить и скрыть окно."""

        point = event.position().toPoint() if hasattr(event, "position") else None
        child = self.childAt(point) if point is not None else None
        widget = child
        while widget is not None and widget is not self:
            if isinstance(widget, AmountLineEdit):
                super().mousePressEvent(event)
                return
            widget = widget.parentWidget()
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._save()

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self._closing = True  # Esc — отмена, без сохранения
            self._stop_watching()
            self.hide()
            return
        super().keyPressEvent(event)


class PaymentDatesCalendar(QWidget):
    """Календарь в карточке вида оплаты (v0.24.0).

    Клик по дню отмечает дату красным или снимает отметку. Отмеченные
    даты сохраняются в записи вида оплаты, а в таблице блока их ячейки
    красятся красным (просьба пользователя). Месяц переключается
    стрелками «‹» и «›».
    """

    def __init__(
        self,
        day: date,
        marked: Iterable[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._marked: set[str] = {str(item) for item in marked}
        self._year, self._month = day.year, day.month

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        top = QHBoxLayout()
        prev_btn = arrow_button("\u2039", "Предыдущий месяц")
        prev_btn.clicked.connect(lambda: self._shift_month(-1))
        top.addWidget(prev_btn)
        self.month_label = QLabel()
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.month_label.setStyleSheet(
            f"font-weight:700;font-size:{styles.fs(13)}px"
        )
        top.addWidget(self.month_label, stretch=1)
        next_btn = arrow_button("\u203a", "Следующий месяц")
        next_btn.clicked.connect(lambda: self._shift_month(1))
        top.addWidget(next_btn)
        root.addLayout(top)

        self._grid = QGridLayout()
        self._grid.setSpacing(3)
        root.addLayout(self._grid)
        self._rebuild()

        hint = QLabel("Клик по дате отмечает её красным в таблице.")
        hint.setObjectName("Muted")
        root.addWidget(hint)

    # ------------------------------------------------------------- отрисовка
    def _shift_month(self, delta: int) -> None:
        """Перейти на другой месяц, не выходя за пределы дат."""

        month = self._month + delta
        year = self._year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self._year, self._month = year, month
        self._rebuild()

    def _rebuild(self) -> None:
        """Перерисовать сетку дней текущего месяца."""

        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self.month_label.setText(f"{MONTHS_RU[self._month - 1]} {self._year}")
        for column, name in enumerate(WEEKDAYS_RU_TITLE):
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("Muted")
            self._grid.addWidget(label, 0, column)
        weeks = calendar_module.monthcalendar(self._year, self._month)
        for row, week in enumerate(weeks, start=1):
            for column, day_number in enumerate(week):
                if day_number == 0:
                    continue
                day = date(self._year, self._month, day_number)
                button = QPushButton(str(day_number))
                # v0.24.2: кнопка шире — иначе двузначные числа
                # («28», «30») не влезали и даты были не видны.
                button.setFixedSize(46, 34)
                day_font = button.font()
                day_font.setPixelSize(styles.fs(11))
                button.setFont(day_font)
                button.clicked.connect(
                    lambda _checked=False, d=day: self._toggle(d)
                )
                self._paint(button, day)
                self._grid.addWidget(button, row, column)

    def _paint(self, button: QPushButton, day: date) -> None:
        """Отмеченный день — красный, сегодняшний — в рамке.

        v0.27.6: текущая дата обводится во всех календарях программы.
        """

        marked = day.isoformat() in self._marked
        rules = []
        if marked:
            rules.append(
                f"background:{styles.WEEK_TABLE_MARKED_RED};font-weight:700"
            )
        if day == date.today():
            rules.append(f"border:2px solid {styles.TODAY_FRAME}")
        button.setStyleSheet(";".join(rules))
        button.setToolTip(
            "Отмечено: ячейка в таблице красная"
            if marked
            else "Клик отметит эту дату красным"
        )

    def _toggle(self, day: date) -> None:
        """Отметить дату или снять отметку."""

        key = day.isoformat()
        if key in self._marked:
            self._marked.discard(key)
        else:
            self._marked.add(key)
        self._rebuild()

    # ---------------------------------------------------------------- данные
    def marked_dates(self) -> list[str]:
        """Отмеченные даты (ISO) по порядку."""

        return sorted(self._marked)


class OtherPaymentCardDialog(QDialog):
    """Карточка нового вида оплаты (v0.20.0).

    Раньше строка появлялась сразу, а название правилось прямо в ячейке
    таблицы. По запросу пользователя вид оплаты вводится карточкой и
    сохраняется кнопкой «Сохранить».

    v0.21.0: в карточке **только название** — поля «Дата» и «Сумма»
    убраны (сумма вводится в ячейку таблицы, а дата — та, что стоит в
    блоке «Оплата»). Окно вдвое шире, чем получается по содержимому.

    v0.24.0: в карточке есть **календарь**. Клик по дате отмечает её;
    отмеченные даты сохраняются в записи вида оплаты, и в таблице блока
    ячейки этих дней становятся красными.
    """

    def __init__(
        self,
        direction: str,
        used_names: set[str],
        day: date,
        parent: QWidget | None = None,
        name: str = "",
        dates: Iterable[str] = (),
    ) -> None:
        super().__init__(parent)
        self.direction = direction
        self._used = {item.strip().lower() for item in used_names}
        self.setWindowTitle(
            ("Вид оплаты" if name else "Новый вид оплаты")
            if direction == OUTGOING
            else ("Вид поступления" if name else "Новый вид поступления")
        )
        self.setModal(True)

        form = QFormLayout(self)
        form.setSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Например: Возврат поставщику")
        # v0.25.1: в режиме правки название уже заполнено.
        self.name_edit.setText(name)
        form.addRow("Название:", self.name_edit)

        # v0.24.0: календарь — отмеченные даты красят ячейки таблицы
        # «Оплата» красным (просьба пользователя).
        self.calendar = PaymentDatesCalendar(day, marked=dates)
        form.addRow("Даты:", self.calendar)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        # v0.24.2: окно не шире экрана. Раньше ширина удваивалась
        # (v0.21.0) и на ноутбуке карточка вылезала за край — календарь
        # с датами был не виден.
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().width() if screen else 1920
        width = min(self.sizeHint().width(), max(available - 80, 420))
        self.setMinimumWidth(0)
        self.resize(max(width, 420), self.sizeHint().height())

    def _try_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(
                self, "Название", "Введите название вида оплаты."
            )
            return
        if name.lower() in self._used:
            QMessageBox.information(
                self, "Название", f"Вид «{name}» уже есть в этом блоке."
            )
            return
        self.accept()

    @property
    def name_value(self) -> str:
        """Название вида оплаты, введённое в карточке (v0.21.0)."""

        return self.name_edit.text().strip()

    @property
    def marked_dates(self) -> list[str]:
        """Даты, отмеченные в календаре карточки (v0.24.0)."""

        return self.calendar.marked_dates()


class JournalPopup(QFrame):
    """Журнал операций вида: окно **справа** от строки (v0.20.0).

    Клик по виду поступления (кроме «Покупатели») или виду оплаты
    (кроме «Поставщики») показывает записи этого вида за месяц: дата,
    сумма, комментарий и итог за месяц. Окно всплывающее (Qt Popup) —
    закрывается по клику вне него.
    """

    def __init__(
        self,
        title: str,
        period: str,
        entries: list[tuple[str, str, str]],
        total: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("Popup")
        self.setStyleSheet(
            "QFrame#Popup{background:#ffffff;border:1px solid #b9c9ef;"
            "border-radius:10px}"
            f"QLabel#PopupTitle{{font-weight:800;font-size:{styles.fs(15)}px}}"
            f"QLabel#PopupTotal{{font-weight:800;font-size:{styles.fs(15)}px;"
            f"color:{styles.BLUE}}}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QLabel(title)
        header.setObjectName("PopupTitle")
        root.addWidget(header)

        period_label = QLabel(period)
        period_label.setObjectName("Muted")
        root.addWidget(period_label)

        table = QTableWidget(len(entries), 3)
        self.table = table
        table.setHorizontalHeaderLabels(["Дата", "Сумма", "Комментарий"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        for row, (day, amount, comment) in enumerate(entries):
            for col, text in enumerate((day, amount, comment)):
                item = QTableWidgetItem(text)
                if col == 1:  # сумма — по правому краю
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(row, col, item)
        if not entries:
            table.setRowCount(1)
            empty = QTableWidgetItem("Записей за месяц нет")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            table.setItem(0, 0, empty)
            table.setSpan(0, 0, 1, 3)
        table.setMinimumWidth(360)
        root.addWidget(table)

        total_label = QLabel(f"Итого: {total}")
        total_label.setObjectName("PopupTotal")
        root.addWidget(total_label)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def open_beside(self, widget: QWidget, row_top: int = 0) -> None:
        """Показать окно справа от строки (не уезжая за край экрана)."""

        anchor = widget.mapToGlobal(QPoint(widget.width(), row_top))
        self.adjustSize()
        self.move(QPoint(anchor.x() + 8, anchor.y()))
        self.show()
        screen = self.screen()
        if screen is not None:
            free = screen.availableGeometry()
            frame = self.frameGeometry()
            x = min(frame.x(), free.right() - frame.width() - 8)
            y = min(frame.y(), free.bottom() - frame.height() - 8)
            self.move(max(free.left() + 8, x), max(free.top() + 8, y))


class RowMouseFilter(QObject):
    """Правый и двойной клик по строке своего вида оплаты (v0.26.0).

    Сигнал ``cellDoubleClicked`` для этого не годится: таблица включила
    ``AllEditTriggers``, поэтому первый клик открывает в ячейке поле
    ввода и второго клика таблица уже не видит — карточка не открывалась
    ни двойным кликом, ни правой кнопкой. Перехват события мыши до
    таблицы работает и при открытом вводе.

    v0.27.3: двойной клик левой клавишей больше не открывает карточку —
    остался только правый клик.
    """

    def __init__(self, table: "QTableWidget", block: "MoneyBlock") -> None:
        super().__init__(table)
        self.table = table
        self.block = block

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is not self.table.viewport():
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                # v0.26.3: правый клик открывает карточку (просьба
                # пользователя), а не меню.
                return self._handle(event, menu=False)
            # Любой клик: таблицу перерисовываем заново, иначе в
            # ячейках остаются артефакты (следы рамки и ввода).
            self._repaint_later()
            return False
        # v0.27.3: двойной клик левой клавишей больше не открывает
        # (просьба пользователя) — карточка вида осталась на правом клике.
        return False

    def _repaint_later(self) -> None:
        """Перерисовать таблицу после обработки клика (v0.26.3)."""

        self.block._repaint_table()

    def _handle(self, event, menu: bool) -> bool:
        """Открыть карточку, если это строка своего вида (v0.26.3)."""

        _ = menu  # и правый, и двойной клик открывают карточку
        position = event.position().toPoint()
        row = self.table.rowAt(position.y())
        block = self.block
        if row < 0 or row >= len(block.row_keys):
            return False
        if not block._is_own_payment_row(block.row_keys[row]):
            return False
        block._close_cell_editor()
        block._open_card_for_row(row)
        block._repaint_table()  # v0.26.3: без артефактов после клика
        # Событие до таблицы не доходит: ввод в ячейке не открывается.
        return True


class MoneyBlock(QFrame):
    """Блок «Поступление»/«Оплата»: дата в одну строку с заголовком + таблица."""

    def __init__(
        self,
        title: str,
        row_keys: list[str],
        on_open_popup: Callable[[str, QWidget, int], None],
        on_amount_entered: Callable[[str, str], None],
        on_date_changed: Callable[[], None] | None = None,
        on_name_edited: Callable[[str, str], None] | None = None,
        on_add_row: Callable[[], None] | None = None,
        on_remove_row: Callable[[str], None] | None = None,
        on_edit_card: Callable[[str], None] | None = None,
        on_duplicate_card: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
        direction: str = OUTGOING,
        stretch_table: bool = False,
        arrows_at_end: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.direction = direction
        # Блок «Оплата» тянется до низа экрана (v0.13.0), поэтому его
        # таблица растягиваемая и всегда с широким скроллбаром.
        self._stretch_table = stretch_table
        # Раскладка заголовка одна для обоих блоков (v0.15.0): стрелки
        # справа от «Сегодня», «+»/«−» — в правом краю.
        self._arrows_at_end = arrows_at_end
        self.row_keys = list(row_keys)
        # Отметки календаря «Выходные и рабочие дни» (v0.23.3): блок
        # сам store не держит, их передаёт ReceiptsView при обновлении.
        self.day_overrides: dict[str, str] = {}
        # v0.24.0: даты, отмеченные в карточке вида оплаты, по ключам строк.
        self.payment_marks: dict[str, set[str]] = {}
        self.on_open_popup = on_open_popup
        self.on_amount_entered = on_amount_entered
        self.on_date_changed = on_date_changed
        self.on_name_edited = on_name_edited
        self.on_add_row = on_add_row
        # v0.25.1: двойной клик — карточка вида, правая кнопка — меню
        # с «Дублировать» (просьба пользователя).
        self.on_edit_card = on_edit_card
        self.on_duplicate_card = on_duplicate_card
        self.on_remove_row = on_remove_row
        self._updating = False  # заполнение таблицы не считается вводом

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # --- заголовок и дата в одну строчку ---
        top = QHBoxLayout()
        top.setSpacing(10)
        header = QLabel(title)
        header.setObjectName("PanelTitle")
        top.addWidget(header)
        top.addSpacing(12)
        top.addWidget(QLabel("Дата"))
        self.date_edit = make_date_edit(_today())
        self.date_edit.dateChanged.connect(lambda *_: self._on_date_changed())
        top.addWidget(self.date_edit)

        self.today_btn = QPushButton("Сегодня")
        self.today_btn.clicked.connect(self.go_today)
        top.addWidget(self.today_btn)

        # v0.15.0: стрелки «←»/«→» стоят сразу справа от кнопки
        # «Сегодня» (жирные — только на этой вкладке), а «+»/«−», если
        # они у блока есть, уходят в правый край заголовка.
        self.prev_btn = arrow_button("←", "День назад", bold=True)
        self.prev_btn.clicked.connect(lambda: self.shift_date(-1))
        top.addWidget(self.prev_btn)

        self.next_btn = arrow_button("→", "День вперёд", bold=True)
        self.next_btn.clicked.connect(lambda: self.shift_date(1))
        top.addWidget(self.next_btn)

        has_row_buttons = on_add_row is not None or on_remove_row is not None
        if has_row_buttons:
            top.addStretch(1)
        if on_add_row is not None:
            self.add_btn = arrow_button("+", "Добавить свою строку")
            self.add_btn.clicked.connect(on_add_row)
            top.addWidget(self.add_btn)
        if on_remove_row is not None:
            self.remove_btn = arrow_button("−", "Убрать выделенную строку")
            self.remove_btn.clicked.connect(self._remove_selected)
            top.addWidget(self.remove_btn)
        if not has_row_buttons:
            # На плашке «поступление» кнопок «+»/«−» нет, поэтому
            # заголовок остаётся прежним: стрелки справа от «Сегодня» и
            # растяжка до правого края (как было раньше).
            top.addStretch(1)
        root.addLayout(top)

        # --- таблица: строка + сумма + дни текущей недели (v0.22.0) ---
        self.table = QTableWidget(0, COLUMN_COUNT)
        self.days: list[date] = []  # дни недели, показанные в шапке
        self.refresh_columns()
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Ячейка становится полем ввода уже по одному клику (v0.12.0):
        # «клацаешь мышкой — и сразу вводишь».
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.table.setItemDelegateForColumn(COL_AMOUNT, AmountItemDelegate(self.table))
        # Длинные названия строк («Поступление наличными») переносим, а
        # не обрезаем: пять числовых колонок оставляют им 200–250 px.
        self.table.setWordWrap(True)
        self.table.itemChanged.connect(self._on_item_changed)
        # v0.18.0: без права ввода кнопок «+»/«−» у блока не показываем.
        self.read_only = False
        self.table.cellClicked.connect(self._on_cell_clicked)
        # v0.25.1: двойной клик по своему виду — карточка, правая кнопка —
        # меню («Дублировать»). v0.26.0: сигнал cellDoubleClicked при
        # AllEditTriggers не доходит (первый клик открывает ввод), поэтому
        # оба клика перехватывает фильтр на viewport.
        self._row_filter = RowMouseFilter(self.table, self)
        self.table.viewport().installEventFilter(self._row_filter)
        # v0.26.2: журнал своего вида открывается с задержкой, чтобы
        # первый клик двойного не показывал журнал вместо карточки.
        self._popup_row = -1
        self._popup_timer = QTimer(self)
        self._popup_timer.setSingleShot(True)
        self._popup_timer.timeout.connect(self._open_delayed_popup)
        self.table.setAlternatingRowColors(False)
        # v0.23.7: своя шапка — чтобы подсветка дня была видна и в
        # Windows (фон секции рисует сам класс).
        header_view = DayHeaderView(self.table)
        self.table.setHorizontalHeader(header_view)
        self.day_header = header_view
        # v0.23.9: шапка таблицы — по центру (просьба пользователя).
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header_view.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self._apply_column_widths()
        root.addWidget(self.table)
        # Подсветка всей строки бежевым при наведении (v0.13.0).
        self._hover_delegate = _HoverDelegate(self.table)
        self.table.setItemDelegate(self._hover_delegate)
        install_row_hover(
            self.table,
            self._hover_delegate,
            self.table.itemDelegateForColumn(COL_AMOUNT),
        )

        self.set_rows(self.row_keys)

    # ------------------------------------------------------------------ вид
    def week_days(self) -> list[date]:
        """Шесть дней недели выбранной даты: Пн–Сб (без воскресенья)."""

        return week_days(week_monday(self.current_date()))[:TABLE_DAYS]

    def refresh_columns(self) -> None:
        """Шапка таблицы под неделю выбранной даты (v0.22.0).

        Колонки дней: «26.10 Пн», «27.10 Вт» и так далее. Если дата
        ушла на другую неделю, шапка перестраивается сама.
        """

        days = self.week_days()
        if days == self.days and self.table.columnCount() == COLUMN_COUNT:
            return
        self.days = days
        if self.table.columnCount() != COLUMN_COUNT:
            self.table.setColumnCount(COLUMN_COUNT)
        headers = ["", "Сумма"]
        for day in days:
            headers.append(
                f"{day.strftime('%d.%m')} {WEEKDAY_SHORT[day.weekday()]}"
            )
        self.table.setHorizontalHeaderLabels(headers)
        # v0.23.9: шапка таблицы — по центру во всех колонках; v0.24.2:
        # первый столбец (названия) — по левому краю.
        for column in range(COLUMN_COUNT):
            header_item = self.table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    if column == COL_NAME
                    else Qt.AlignmentFlag.AlignCenter
                )
        self._apply_column_widths()

    def _apply_column_widths(self) -> None:
        """Ширины колонок фиксированы; шапка — по центру (v0.23.9)."""

        for column in range(COL_AMOUNT, COLUMN_COUNT):
            self.table.setColumnWidth(
                column,
                COLUMN_WIDTHS.get(column, DAY_COLUMN_WIDTH),
            )
            header_item = self.table.horizontalHeaderItem(column)
            if header_item is not None:
                # v0.23.9: по центру, раньше числа были по правому краю.
                header_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def mark_current_day(self) -> None:
        """Выделить в шапке день, выбранный в блоке (v0.22.0)."""

        current = self.current_date()
        for index, day in enumerate(self.days):
            header_item = self.table.horizontalHeaderItem(FIRST_DAY_COL + index)
            if header_item is None:
                continue
            font = header_item.font()
            font.setBold(day == current)
            header_item.setFont(font)
            header_item.setToolTip(
                "День, выбранный в блоке" if day == current else ""
            )

    def set_day_overrides(self, overrides: dict[str, str]) -> None:
        """Запомнить отметки календаря и перекрасить колонки дней."""

        self.day_overrides = dict(overrides)
        self.paint_day_columns()

    def set_payment_marks(self, marks: dict[str, set[str]]) -> None:
        """Запомнить даты, отмеченные в карточках видов оплат (v0.24.0).

        ``marks``: ключ строки (``other:<название>``) -> множество дат
        ISO. Ячейки этих дней в строке вида оплаты станут красными.
        """

        self.payment_marks = {key: set(days) for key, days in marks.items()}
        self.paint_day_columns()

    def paint_day_columns(self) -> None:
        """Раскрасить колонки дней, как в таблицах «Поставщики» (v0.23.3).

        Выходной или праздник из календаря вкладки «Данные» — весь
        столбец розовый; сегодняшняя дата — салатовой остаётся только
        ячейка шапки с датой, а не весь столбец (просьба пользователя).
        """

        today = date.today()
        pink = QColor(styles.WEEK_TABLE_WEEKEND_PINK)
        green = QColor(styles.WEEK_TABLE_TODAY_GREEN)
        # v0.24.0: даты, отмеченные в карточке вида оплаты, — красные.
        red = QColor(styles.WEEK_TABLE_MARKED_RED)
        empty: QBrush = QBrush()
        header_colors: dict[int, QColor] = {}
        off_days = {
            day: calc.is_day_off(day, self.day_overrides) for day in self.days
        }
        for index, day in enumerate(self.days):
            column = FIRST_DAY_COL + index
            is_off = off_days[day]
            # v0.23.7: салатовой — только ячейка шапки с сегодняшней
            # датой, розовым — выходной/праздник (просьба пользователя).
            if day == today:
                header_colors[column] = green
            elif is_off:
                header_colors[column] = pink
        for row, key in enumerate(self.row_keys):
            marked = self.payment_marks.get(key, set())
            for index, day in enumerate(self.days):
                column = FIRST_DAY_COL + index
                item = self.table.item(row, column)
                if item is None:
                    continue
                if day.isoformat() in marked:
                    item.setBackground(red)
                else:
                    item.setBackground(pink if off_days[day] else empty)
        self.day_header.set_section_colors(header_colors)
        self.table.viewport().update()

    def set_rows(self, row_keys: list[str]) -> None:
        """Перестроить строки таблицы (состав строк меняется кнопкой «+»)."""

        self.row_keys = list(row_keys)
        self._updating = True
        self.table.setRowCount(len(self.row_keys))
        for row, key in enumerate(self.row_keys):
            name_item = QTableWidgetItem(self._row_title(key))
            # v0.21.0: название вида оплаты в ячейке не правится — оно
            # задаётся карточкой при создании, а клик по названию
            # открывает журнал операций (раньше клик открывал и редактор,
            # и журнал одновременно).
            name_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            name_item.setToolTip(self._row_tooltip(key))
            # v0.24.2: первый столбец (названия) — по левому краю,
            # остальные колонки — по центру (просьба пользователя).
            name_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, COL_NAME, name_item)
            for column in range(COL_AMOUNT, COLUMN_COUNT):
                item = QTableWidgetItem("")
                item_flags = (
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                editable = key in DIRECT_INPUT_ROWS or key.startswith(OTHER_PREFIX)
                if editable and column == COL_AMOUNT:
                    item_flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(item_flags)
                # v0.23.9: значения в таблице — по центру, раньше числа
                # были по правому краю.
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        self._updating = False
        self.paint_day_columns()  # v0.23.3: розовые выходные, салатовая дата
        self._fit_height()

    def _fit_height(self) -> None:
        """Высота по числу строк; больше ``MAX_VISIBLE_ROWS`` — со скроллом.

        Запрос пользователя: когда добавленные строки перестают
        помещаться на экране, в блоке появляется вертикальная прокрутка,
        а сам блок не разрастается за пределы окна.
        """

        rows = max(len(self.row_keys), 1)
        visible = min(rows, MAX_VISIBLE_ROWS)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        row_height = self.table.verticalHeader().defaultSectionSize()
        header_height = self.table.horizontalHeader().height()
        # v0.23.8: рамка, линии сетки и широкий скроллбар — обеим
        # таблицам («Поступление» и «Оплата»). Раньше стиль задавался
        # только растягиваемой таблице, поэтому в «Поступлении» сетка
        # не рисовалась вовсе (замечание пользователя).
        self.table.setStyleSheet(styles.WIDE_SCROLLBAR_ON_TABLE)
        if self._stretch_table:
            # Блок «Оплата» тянется до низа: таблица занимает всё
            # доступное место, а скроллбар закреплён и широкий.
            self.table.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self.table.setMinimumHeight(header_height + row_height * 2 + 2)
            self.table.setMaximumHeight(16777215)
            self.table.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOn
            )
            self.table.setStyleSheet(styles.WIDE_SCROLLBAR_ON_TABLE)
        else:
            # Блок «Поступление» закреплён (v0.15.0): высота таблицы
            # фиксированная по числу строк, чтобы при обновлении и
            # прокрутке содержимое не «болталось» и не сдвигало блок
            # «Оплата» под ним.
            fixed_height = header_height + visible * row_height + 2
            # Высоты строк не пересчитываются по переносу слов: иначе
            # таблица дёргается при каждом пересчёте раскладки.
            self.table.verticalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Fixed
            )
            self.table.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            self.table.setMinimumHeight(fixed_height)
            self.table.setMaximumHeight(fixed_height)
            # Скроллинга в блоке «Поступление» нет (v0.14.0): строк там
            # немного, а полоса прокрутки только мешала.
            self.table.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self.table.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
        for column, width in COLUMN_WIDTHS.items():
            self.table.setColumnWidth(column, width)

    @staticmethod
    def _row_title(key: str) -> str:
        return ROW_TITLES.get(key, other_row_name(key))

    @staticmethod
    def _row_tooltip(key: str) -> str:
        # v0.21.0: название своего вида задаётся карточкой, в ячейке
        # правится только сумма, а клик по названию открывает журнал.
        if key.startswith(OTHER_PREFIX) and key != FIXED_OTHER_ROW:
            return "Сумма вводится прямо в ячейку, клик — журнал операций"
        if key in DIRECT_INPUT_ROWS:
            return "Сумма вводится прямо в ячейку, клик — журнал операций"
        return "Клик — окно со списком и полями сумм"

    def set_amount(self, row_key: str, column: int, text: str) -> None:
        if row_key not in self.row_keys:
            return
        row = self.row_keys.index(row_key)
        item = self.table.item(row, column)
        if item is None:
            return
        self._updating = True
        item.setText(text)
        self._updating = False

    def amount_text(self, row_key: str, column: int = COL_AMOUNT) -> str:
        if row_key not in self.row_keys:
            return ""
        item = self.table.item(self.row_keys.index(row_key), column)
        return item.text() if item is not None else ""

    # ----------------------------------------------------------------- дата
    def current_date(self) -> date:
        return _qdate_to_date(self.date_edit.date())

    def _on_date_changed(self) -> None:
        if self.on_date_changed is not None:
            self.on_date_changed()

    def set_date(self, day: date) -> None:
        # v0.22.1: сигнал не глушим — по нему «Сводка» синхронизирует
        # свою дату с датой блока. Если дата та же, сигнал не придёт,
        # поэтому пересчёт вызываем сами.
        new_date = QDate(day.year, day.month, day.day)
        if self.date_edit.date() == new_date:
            self._on_date_changed()
            return
        self.date_edit.setDate(new_date)  # -> _on_date_changed()

    def go_today(self) -> None:
        self.set_date(_today())

    def shift_date(self, days: int) -> None:
        self.set_date(self.current_date() + timedelta(days=days))

    # ------------------------------------------------------------- действия
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        key = self.row_keys[item.row()]
        if item.column() == COL_NAME:
            # С v0.21.0 название своего вида в ячейке не правится
            # (клик открывает журнал), но программное переименование
            # (rename_other_row) осталось — им пользуются карточки.
            if key.startswith(OTHER_PREFIX) and self.on_name_edited is not None:
                self.on_name_edited(key, item.text())
            return
        if item.column() != COL_AMOUNT:
            return
        if key in POPUP_ROWS:
            return
        self.on_amount_entered(key, item.text())
        # v0.26.3: ячейка после ввода перерисовывается заново.
        self._repaint_table()

    @staticmethod
    def _is_own_payment_row(key: str) -> bool:
        """Свой вид (добавлен кнопкой «+»): его можно править карточкой."""

        return key.startswith(OTHER_PREFIX) and key != FIXED_OTHER_ROW

    def _stop_popup_timer(self) -> None:
        """Отменить отложенный журнал (двойной клик, меню, v0.26.2)."""

        self._popup_timer.stop()
        self._popup_row = -1

    def _open_delayed_popup(self) -> None:
        """Открыть журнал, если двойного клика не было (v0.26.2)."""

        row = self._popup_row
        self._popup_row = -1
        if row < 0 or row >= len(self.row_keys):
            return
        self.on_open_popup(self.row_keys[row], self.table, row)

    def _repaint_table(self) -> None:
        """Перерисовать таблицу и ячейки ввода заново (v0.26.3).

        Просьба пользователя: в таблицах «Оплата» и «Поступление» после
        клика остаются артефакты (следы рамки и поля ввода), поэтому
        после клика и после закрытия ввода таблица перерисовывается
        целиком.
        """

        self.table.viewport().update()
        self.table.update()

    def _close_cell_editor(self) -> None:
        """Закрыть поле ввода, открытое в ячейке (v0.26.0).

        Таблица открывает ввод уже по одному клику (AllEditTriggers), и
        перед карточкой/меню его надо убрать, иначе он остаётся поверх.
        """

        editor = self.table.viewport().findChild(QLineEdit)
        if editor is not None:
            self.table.commitData(editor)
            self.table.closeEditor(
                editor, QAbstractItemDelegate.EndEditHint.NoHint
            )
        # v0.26.3: после ввода ячейку перерисовываем заново — иначе на
        # её месте остаётся след от поля ввода.
        self._repaint_table()

    def _open_card_for_row(self, row: int) -> None:
        """Двойной клик: открыть карточку своего вида (v0.25.1)."""

        if row < 0 or row >= len(self.row_keys) or self.on_edit_card is None:
            return
        key = self.row_keys[row]
        if not self._is_own_payment_row(key):
            return
        self._stop_popup_timer()  # v0.26.2: журнал не открываем
        self.on_edit_card(key)

    def _show_row_menu(self, position) -> None:
        """Правая кнопка по своему виду: «Дублировать» (v0.25.1)."""

        if self.on_duplicate_card is None:
            return
        row = self.table.indexAt(position).row()
        if row < 0 or row >= len(self.row_keys):
            return
        key = self.row_keys[row]
        if not self._is_own_payment_row(key):
            return
        self._stop_popup_timer()  # v0.26.2: журнал не открываем
        menu = QMenu(self.table)
        duplicate = menu.addAction("Дублировать")
        if menu.exec(self.table.viewport().mapToGlobal(position)) == duplicate:
            self.on_duplicate_card(key)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        """Клик по строке: окно контрагентов или журнал строки."""

        key = self.row_keys[row]
        if key in POPUP_ROWS:
            self.on_open_popup(key, self.table, row)
            return
        # v0.20.0: у строк-источников (касса, карты, свои виды) клик по
        # названию открывает журнал операций этого вида.
        # v0.27.3: журнал открывается **только по первому столбцу** —
        # клик по ячейке «Сумма» и по дням остаётся вводом за день.
        if is_popup_row(key) and column == COL_NAME:
            # v0.26.2: у своего вида журнал открывается с задержкой —
            # иначе первый клик двойного показывал журнал, и до карточки
            # было не добраться.
            if self._is_own_payment_row(key):
                self._popup_row = row
                self._popup_timer.start(
                    QApplication.doubleClickInterval() + 80
                )
                return
            self.on_open_popup(key, self.table, row)
            return
        _ = column  # клик по ячейке суммы уходит в режим ввода

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.row_keys):
            return
        key = self.row_keys[row]
        if not key.startswith(OTHER_PREFIX):
            QMessageBox.information(
                self,
                "Строку не удалить",
                f"«{self._row_title(key)}» — постоянная строка. Удалить "
                "можно только добавленные кнопкой «+».",
            )
            return
        if self.on_remove_row is not None:
            self.on_remove_row(key)


    def set_read_only(self, read_only: bool) -> None:
        """Права пользователя: «только просмотр» или полный доступ (v0.18.0)."""

        self.read_only = read_only
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if read_only
            else QAbstractItemView.EditTrigger.AllEditTriggers
        )
        for name in ("add_btn", "remove_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setVisible(not read_only)


class ReceiptsView(QWidget):
    """Слева блоки «Поступление» и «Оплата», справа — «Сводка»."""

    def __init__(
        self,
        store: DataStore,
        on_data_changed: Callable[[], None] | None = None,
        summary_widget: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_data_changed = on_data_changed
        self.summary_widget = summary_widget
        self._popup: AmountPopup | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # --- левая колонка: блоки друг под другом, в половину ширины ---
        left = QWidget()
        self.left = left
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        # Оба блока умеют держать строки, добавленные кнопкой «+»:
        # в «Поступлении» это прочие поступления, в «Оплате» — прочие
        # виды оплат (v0.12.0). Различает их ``direction``.
        self.incoming = MoneyBlock(
            "Поступление",
            self._incoming_rows(),
            on_open_popup=self.open_popup,
            on_amount_entered=self.save_direct_amount,
            on_date_changed=self.refresh,
            # v0.25.1: те же карточка и «Дублировать» для «Поступления».
            on_edit_card=lambda key: self.edit_payment_card(key, INCOMING),
            on_duplicate_card=lambda key: self.duplicate_payment(key, INCOMING),
            direction=INCOMING,
        )
        self.outgoing = MoneyBlock(
            "Оплата",
            self._outgoing_rows(),
            on_open_popup=self.open_popup,
            on_amount_entered=self.save_direct_amount,
            on_date_changed=self.refresh,
            on_name_edited=lambda key, text: self.rename_other_row(
                key, text, OUTGOING
            ),
            on_add_row=lambda: self.add_other_row(OUTGOING),
            on_remove_row=lambda key: self.remove_other_row(key, OUTGOING),
            # v0.25.1: двойной клик открывает карточку вида, правая
            # кнопка — «Дублировать» (просьба пользователя).
            on_edit_card=lambda key: self.edit_payment_card(key, OUTGOING),
            on_duplicate_card=lambda key: self.duplicate_payment(key, OUTGOING),
            direction=OUTGOING,
            stretch_table=True,
        )
        # «Поступление» — по высоте содержимого, «Оплата» тянется до
        # низа экрана (v0.13.0).
        left_layout.addWidget(self.incoming)
        left_layout.addWidget(self.outgoing, stretch=1)

        # Пояснений под блоками нет (v0.13.0): всё видно по заголовкам
        # колонок «Сумма» и дней недели (v0.22.0).

        self.left_layout = left_layout
        # v0.22.0: «Сводка» вдвое у́же — раньше обе половины делили окно
        # поровну (stretch 1 : 1), теперь левым блокам уходит три доли
        # из четырёх, а сводке — одна (было 50% ширины, стало 25%).
        root.addWidget(left, stretch=3 if summary_widget is not None else 1)

        # --- справа на всю высоту — «Сводка» ---
        # v0.27.6: даты платёжек независимы (просьба пользователя) —
        # смена даты в «Поступлении» не переставляет «Оплату» и
        # «Сводку», и наоборот. Раньше они были одной датой (v0.22.1).
        if summary_widget is not None:
            # Ignored по горизонтали: сводка не должна отжимать блоки
            # влево — ширину держим долями раскладки (3 : 1).
            summary_widget.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
            )
            root.addWidget(summary_widget, stretch=1)

        self.refresh()

    # ---------------------------------------------------------------- даты
    @property
    def blocks(self) -> tuple[MoneyBlock, MoneyBlock]:
        return (self.incoming, self.outgoing)

    def set_date(self, day: date) -> None:
        """Задать дату обоим блокам (удобно для тестов и скриншотов)."""

        for block in self.blocks:
            block.set_date(day)   # каждый блок тянет за собой «Сводку»
        self.refresh()

    # ------------------------------------------------------- прочие виды оплат
    def _incoming_rows(self) -> list[str]:
        # Своих строк в «Поступлении» не добавляют (v0.13.0): под
        # «Покупателями» стоит одна постоянная строка — «Прочие
        # поступления» — с суммой прямо в ячейке.
        return [CASH_ROW, CARD_ROW, CUSTOMERS_ROW, FIXED_OTHER_ROW]

    def _outgoing_rows(self) -> list[str]:
        return [SUPPLIERS_ROW] + [
            other_row_key(name) for name in other_payment_names(self.store.data)
        ]

    def _block(self, direction: str) -> MoneyBlock:
        return self.incoming if direction == INCOMING else self.outgoing

    @staticmethod
    def _rows_for(direction: str, keys: list[str]) -> list[str]:
        """Строки блока по направлению (постоянные + добавленные)."""

        if direction == INCOMING:
            return [CASH_ROW, CARD_ROW, CUSTOMERS_ROW, FIXED_OTHER_ROW]
        return [SUPPLIERS_ROW] + [
            key
            for key in keys
            if key.startswith(OTHER_PREFIX) and key != FIXED_OTHER_ROW
        ]

    def refresh_rows(self) -> None:
        """Перечитать состав строк обоих блоков (после «+»/«−»)."""

        self.incoming.set_rows(self._incoming_rows())
        self.outgoing.set_rows(self._outgoing_rows())

    def refresh_incoming_rows(self) -> None:
        self.incoming.set_rows(self._incoming_rows())


    def refresh_outgoing_rows(self) -> None:
        self.outgoing.set_rows(self._outgoing_rows())

    def _find_payment(self, name: str, direction: str):
        """Запись вида оплаты по названию и блоку (v0.25.1)."""

        return next(
            (
                item
                for item in self.store.data.other_payments
                if item.name == name
                and getattr(item, "direction", OUTGOING) == direction
            ),
            None,
        )

    def edit_payment_card(self, key: str, direction: str) -> None:
        """Двойной клик: карточка вида оплаты с правкой (v0.25.1).

        Меняем название и даты, отмеченные в календаре карточки.
        """

        name = other_row_name(key)
        payment = self._find_payment(name, direction)
        if payment is None:
            return
        block = self._block(direction)
        used = set(other_payment_names(self.store.data, direction)) - {name}
        dialog = OtherPaymentCardDialog(
            direction,
            used,
            block.current_date(),
            self,
            name=name,
            dates=payment.dates,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payment.dates = list(dialog.marked_dates)
        new_name = dialog.name_value
        if new_name != name:
            rename_other_payment(self.store.data, name, new_name, direction)
        self.store.save()
        self.refresh_rows()
        self.refresh()

    def duplicate_payment(self, key: str, direction: str) -> None:
        """Правая кнопка → «Дублировать»: копия вида с новым именем
        и теми же отмеченными датами (v0.25.1)."""

        name = other_row_name(key)
        payment = self._find_payment(name, direction)
        if payment is None:
            return
        used = set(other_payment_names(self.store.data, direction))
        base = f"{name} (копия)"
        new_name = base
        index = 2
        while new_name in used:
            new_name = f"{base} {index}"
            index += 1
        block = self._block(direction)
        add_other_payment(
            self.store.data,
            new_name,
            block.current_date().isoformat(),
            direction,
            dates=list(payment.dates),
        )
        self.store.save()
        self.refresh_rows()
        self.refresh()

    def add_other_row(self, direction: str = OUTGOING) -> None:
        """Кнопка «+»: новый вид оплаты — карточкой (v0.20.0)."""

        block = self._block(direction)
        if direction == OUTGOING:
            # Плашка «Оплата»: вид оплаты вводим карточкой с сохранением.
            used = set(other_payment_names(self.store.data, direction))
            dialog = OtherPaymentCardDialog(
                direction, used, block.current_date(), self
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            # Дата — та, что стоит в блоке; сумма вводится в ячейку.
            name = dialog.name_value
            # v0.24.0: даты, отмеченные в календаре карточки, — их
            # ячейки в таблице блока красятся красным.
            add_other_payment(
                self.store.data,
                name,
                block.current_date().isoformat(),
                direction,
                dates=dialog.marked_dates,
            )
            self.store.save()
            self.refresh_rows()
            self.refresh()
            return

        used = set(other_payment_names(self.store.data, direction))
        base_name = NEW_ROW_NAME[direction]
        name = base_name
        index = 2
        while name in used:
            name = f"{base_name} {index}"
            index += 1
        add_other_payment(
            self.store.data, name, block.current_date().isoformat(), direction
        )
        self.store.save()
        self.refresh_rows()
        self.refresh()
        # Сразу даём переименовать строку: курсор в ячейку названия.
        # При AllEditTriggers таблица сама открывает редактор по клику,
        # поэтому повторно его не дёргаем (иначе Qt ругается
        # «edit: editing failed»).
        row = block.row_keys.index(other_row_key(name))
        block.table.setCurrentCell(row, COL_NAME)
        # Редактор названия открываем отложенно, уже после того как
        # таблица перестроена: так редактор не «повисает» на удаляемых
        # ячейках (иначе сборка мусора падала на C++-объектах Qt).
        QTimer.singleShot(0, lambda: self._edit_row_name(block, row))

    def _edit_row_name(self, block: MoneyBlock, row: int) -> None:
        if row >= block.table.rowCount():
            return
        item = block.table.item(row, COL_NAME)
        if item is None:
            return
        block.table.setCurrentCell(row, COL_NAME)
        if block.table.state() != QAbstractItemView.State.EditingState:
            block.table.editItem(item)

    def remove_other_row(self, row_key: str, direction: str = OUTGOING) -> None:
        """Кнопка «−»: убрать строку вместе с её суммами."""

        name = other_row_name(row_key)
        block_title = "Поступление" if direction == INCOMING else "Оплата"
        answer = QMessageBox.question(
            self,
            "Удалить строку",
            f"Убрать «{name}» из блока «{block_title}»?\n\n"
            "Все введённые по этой строке суммы будут удалены.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_other_payment(self.store.data, name, direction)
        self.store.save()
        self.refresh_rows()
        self.refresh()
        if self.on_data_changed is not None:
            self.on_data_changed()

    def rename_other_row(
        self, row_key: str, new_name: str, direction: str = OUTGOING
    ) -> None:
        """Новое название строки, введённое прямо в ячейку."""

        old_name = other_row_name(row_key)
        cleaned = (new_name or "").strip()
        if not cleaned or cleaned == old_name:
            self.refresh_rows()
            self.refresh()
            return
        if cleaned in other_payment_names(self.store.data, direction):
            QMessageBox.information(
                self,
                "Такое название уже есть",
                f"Строка «{cleaned}» уже есть в этом блоке.",
            )
            self.refresh_rows()
            self.refresh()
            return
        rename_other_payment(self.store.data, old_name, cleaned, direction)
        self.store.save()
        self.refresh_rows()
        self.refresh()

    # --------------------------------------------------------- ввод в ячейку
    def save_direct_amount(self, row_key: str, text: str) -> None:
        """Сохранить сумму, введённую прямо в ячейку."""

        direction = OUTGOING
        if row_key == SUPPLIERS_ROW:
            block = self.outgoing  # строка поставщиков не вводится ячейкой
        elif row_key.startswith(OTHER_PREFIX):
            # Строка, добавленная кнопкой «+»: в каком блоке она лежит,
            # тот блок и знает дату и направление записи.
            block = (
                self.incoming
                if row_key in self.incoming.row_keys
                else self.outgoing
            )
            direction = block.direction
        else:
            block = self.incoming
        day_iso = block.current_date().isoformat()
        cleaned = (text or "").strip()
        amount: Decimal | None = None
        if cleaned:
            try:
                amount = parse_money(cleaned)
            except ValueError:
                amount = None
        if amount is not None and amount < 0:
            amount = None

        if row_key == CASH_ROW or row_key == CARD_ROW:
            source = CASH_SOURCE if row_key == CASH_ROW else CARD_SOURCE
            set_daily_cash_receipt(self.store.data, source, day_iso, amount)
        elif row_key.startswith(OTHER_PREFIX):
            set_daily_other_payment(
                self.store.data,
                other_row_name(row_key),
                day_iso,
                amount,
                direction,
            )
        else:
            return
        self.store.save()
        self.refresh()
        # Недельные таблицы и «Сводку» не трогаем: у наличных, карт и
        # прочих видов оплат нет контрагента, в таблицах они не участвуют
        # (ADR-017/018).

    # -------------------------------------------------------------- выпадающее
    def open_popup(
        self, row_key: str, anchor: QWidget | None = None, row: int | None = None
    ) -> AmountPopup | None:
        """Открыть окно со списком контрагентов справа от строки."""

        # v0.20.0: блок определяем по строке — «прочие» бывают в обоих.
        block = self.incoming if row_key in self.incoming.row_keys else self.outgoing
        table = block.table
        if row is None:
            row = block.row_keys.index(row_key) if row_key in block.row_keys else 0
        row_top = table.rowViewportPosition(row)
        if anchor is None:
            anchor = table
        # Строки-источники (касса, карты, свои виды) открывают журнал.
        if row_key not in POPUP_ROWS:
            return self._open_journal_popup(row_key, block, anchor, row_top)
        title = ROW_TITLES[row_key]
        day_iso = block.current_date().isoformat()

        if row_key == CUSTOMERS_ROW:
            people = [c for c in self.store.data.customers if not c.archived]
            empty_title = "Нет покупателей"
            empty_text = (
                "Добавьте покупателя через кнопку «Карточка покупателя»."
            )
            hint = "Сумма поступит в колонку «Оплата» покупателя за дату."
        else:
            people = [s for s in self.store.data.suppliers if not s.archived]
            empty_title = "Нет поставщиков"
            empty_text = (
                "Добавьте поставщика через кнопку «Карточка поставщика»."
            )
            hint = "Сумма появится в колонке «Оплата» поставщика за дату."

        if not people:
            QMessageBox.information(self, empty_title, empty_text)
            return None

        # Поля заполняем уже сохранёнными суммами: при повторном
        # открытии окна введённое не должно «пропадать».
        values = {
            person.id: self.daily_payment_amount(person.id, day_iso)
            for person in people
        }
        popup = AmountPopup(
            title,
            [(person.id, person.name) for person in people],
            self._make_saver(block, row_key, [person.id for person in people]),
            self,
            hint,
            values,
            "Итого по покупателям:"
            if row_key == CUSTOMERS_ROW
            else "Итого по поставщикам:",
        )
        # Прежнее окно больше не нужно: скрываем и удаляем его, чтобы на
        # каждый клик по строке не копились виджеты.
        if self._popup is not None:
            previous = self._popup
            self._popup = None
            previous._closing = True
            previous._stop_watching()
            previous.hide()
            previous.deleteLater()
        self._popup = popup
        popup.destroyed.connect(self._forget_popup)
        popup.open_beside(anchor, row_top)
        return popup

    def _open_journal_popup(
        self, row_key: str, block: MoneyBlock, anchor: QWidget, row_top: int
    ) -> JournalPopup:
        """Журнал операций вида за месяц: окно справа от строки (v0.20.0)."""

        data = self.store.data
        day = block.current_date()
        month_start = day.replace(day=1)
        month_end = (
            (month_start + timedelta(days=31)).replace(day=1) - timedelta(days=1)
        )
        if row_key == CASH_ROW or row_key == CARD_ROW:
            source = CASH_SOURCE if row_key == CASH_ROW else CARD_SOURCE
            title = ROW_TITLES[row_key]
            raw = [
                (item.date, cash_receipt_amount(item), getattr(item, "comment", ""))
                for item in data.cash_receipts
                if item.source == source
            ]
        else:
            title = other_row_name(row_key)
            direction = block.direction
            raw = [
                (item.date, other_payment_amount(item), "")
                for item in data.other_payments
                if item.name == title
                and getattr(item, "direction", OUTGOING) == direction
            ]

        in_month = [
            item
            for item in raw
            if month_start.isoformat() <= item[0] <= month_end.isoformat()
        ]
        in_month.sort(key=lambda item: item[0], reverse=True)  # свежие сверху
        entries = [
            (_fmt_day(day_iso), _money_text(amount), comment)
            for day_iso, amount, comment in in_month
        ]
        total = sum((amount for _day, amount, _comment in in_month), ZERO)
        period = f"Журнал за {MONTHS_RU[day.month - 1]} {day.year} г."

        popup = JournalPopup(title, period, entries, _money_text(total), self)
        # Прежнее окно больше не нужно: скрываем и удаляем, чтобы на
        # каждый клик по строке не копились виджеты.
        if self._popup is not None:
            previous = self._popup
            self._popup = None
            if hasattr(previous, "_stop_watching"):
                previous._closing = True  # noqa: SLF001 - окно контрагентов
                previous._stop_watching()  # noqa: SLF001
            previous.hide()
            previous.deleteLater()
        self._popup = popup
        popup.destroyed.connect(self._forget_popup)
        popup.open_beside(anchor, row_top)
        return popup

    def _forget_popup(self, *_args) -> None:
        self._popup = None

    def _make_saver(self, block: MoneyBlock, row_key: str, ids: list[str]):
        def save(values: dict[str, Decimal]) -> None:
            day_iso = block.current_date().isoformat()
            for counterparty_id in ids:
                amount = values.get(counterparty_id) or ZERO
                set_daily_operation(
                    self.store.data,
                    counterparty_id,
                    day_iso,
                    KIND_PAYMENT,
                    amount if amount else None,
                )
            self.store.save()
            self.refresh()
            if self.on_data_changed is not None:
                self.on_data_changed()

        return save

    # ------------------------------------------------------------------ итоги
    def daily_payment_amount(self, counterparty_id: str, day_iso: str) -> Decimal:
        return sum(
            (
                operation_amount(op)
                for op in self.store.data.operations
                if op.kind == KIND_PAYMENT
                and op.counterparty_id == counterparty_id
                and op.date == day_iso
            ),
            ZERO,
        )

    def _cash_total(self, source: str, day: date) -> Decimal:
        day_iso = day.isoformat()
        return sum(
            (
                cash_receipt_amount(receipt)
                for receipt in self.store.data.cash_receipts
                if receipt.date == day_iso and receipt.source == source
            ),
            ZERO,
        )

    def _payment_total(self, counterparty_ids: set[str], day: date) -> Decimal:
        day_iso = day.isoformat()
        return sum(
            (
                operation_amount(op)
                for op in self.store.data.operations
                if op.kind == KIND_PAYMENT
                and op.date == day_iso
                and op.counterparty_id in counterparty_ids
            ),
            ZERO,
        )

    def _other_total(
        self, name: str, day: date, direction: str = OUTGOING
    ) -> Decimal:
        day_iso = day.isoformat()
        return sum(
            (
                other_payment_amount(item)
                for item in self.store.data.other_payments
                if item.name == name
                and item.date == day_iso
                and getattr(item, "direction", OUTGOING) == direction
            ),
            ZERO,
        )

    def set_read_only(self, read_only: bool) -> None:
        """Права пользователя: «только просмотр» или полный доступ (v0.18.0)."""

        self.read_only = read_only
        self.incoming.set_read_only(read_only)
        self.outgoing.set_read_only(read_only)

    def _row_total(
        self,
        key: str,
        day: date,
        direction: str,
        customer_ids: set[str],
        supplier_ids: set[str],
    ) -> Decimal:
        """Сумма строки за день (v0.22.0): та же формула, что в «Сумме»."""

        if key == SUPPLIERS_ROW:
            return self._payment_total(supplier_ids, day)
        if key == CUSTOMERS_ROW:
            return self._payment_total(customer_ids, day)
        if key in (CASH_ROW, CARD_ROW):
            source = CASH_SOURCE if key == CASH_ROW else CARD_SOURCE
            return self._cash_total(source, day)
        if key.startswith(OTHER_PREFIX):
            return self._other_total(other_row_name(key), day, direction)
        return ZERO

    def _payment_marks(self, direction: str) -> dict[str, set[str]]:
        """Даты, отмеченные в карточках видов оплат (v0.24.0).

        Ключ — строка таблицы (``other:<название>``), значение — даты,
        которые пользователь отметил в календаре карточки: их ячейки в
        таблице блока красятся красным.
        """

        marks: dict[str, set[str]] = {}
        for payment in self.store.data.other_payments:
            if getattr(payment, "direction", OUTGOING) != direction:
                continue
            if not getattr(payment, "dates", None):
                continue
            marks.setdefault(other_row_key(payment.name), set()).update(
                payment.dates
            )
        return marks

    def refresh(self) -> None:
        """Заполнить «Сумму» за выбранный день и колонки дней недели."""

        customer_ids = {c.id for c in self.store.data.customers}
        supplier_ids = {s.id for s in self.store.data.suppliers}

        for block in (self.incoming, self.outgoing):
            # v0.22.0: шапка строится под неделю выбранной даты — при
            # переходе на другую неделю колонки дней меняются сами.
            block.refresh_columns()
            block.mark_current_day()
            block.set_day_overrides(self.store.data.day_overrides)
            # v0.24.0: даты из карточек видов оплат — красные ячейки.
            block.set_payment_marks(self._payment_marks(block.direction))
            day = block.current_date()
            for key in block.row_keys:
                block.set_amount(
                    key,
                    COL_AMOUNT,
                    _money_text(
                        self._row_total(
                            key, day, block.direction, customer_ids, supplier_ids
                        )
                    ),
                )
                for index, column_day in enumerate(block.days):
                    block.set_amount(
                        key,
                        FIRST_DAY_COL + index,
                        _money_text(
                            self._row_total(
                                key,
                                column_day,
                                block.direction,
                                customer_ids,
                                supplier_ids,
                            )
                        ),
                    )


def _parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
