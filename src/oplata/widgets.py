"""Мелкие общие виджеты/фабрики, чтобы экраны не дублировали стили кнопок."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFontMetrics,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QStyledItemDelegate,
    QDateEdit,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionViewItem,
    QTableView,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from . import styles


def make_panel() -> tuple[QFrame, QVBoxLayout]:
    """Белая панель со скруглением (как блоки на макетах) + её layout."""
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(10)
    return frame, layout


class CollapsiblePanel(QFrame):
    """Белая панель со строкой-заголовком, которая раскрывает содержимое.

    Запрос пользователя v0.9.0: на экране «Данные» плашку с календарём
    сделать раскрывающейся с названием «Настройка календаря». Свёрнутая
    панель показывает только заголовок со стрелкой ▸/▾; содержимое
    добавляют в ``content_layout``.
    """

    def __init__(
        self,
        title: str,
        expanded: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self._expanded = expanded

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        self.toggle = QPushButton()
        self.toggle.setStyleSheet(
            "QPushButton{background:transparent;border:none;text-align:left;"
            f"padding:2px 0;font-weight:800;font-size:14px;color:{styles.TEXT}}}"
            "QPushButton:hover{background:transparent}"
        )
        self.toggle.clicked.connect(self.toggle_expanded)
        root.addWidget(self.toggle)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        root.addWidget(self.content)

        self._title = title
        self._apply_state()

    # ------------------------------------------------------------------ вид
    @property
    def title(self) -> str:
        return self._title

    def set_title(self, title: str) -> None:
        self._title = title
        self._apply_state()

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._apply_state()

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def _apply_state(self) -> None:
        arrow = "▾" if self._expanded else "▸"
        # Свёрнутая панель не должна растягиваться по высоте: иначе она
        # занимает весь экран и «Администрирование» уезжает вниз.
        vertical = (
            QSizePolicy.Policy.Preferred
            if self._expanded
            else QSizePolicy.Policy.Maximum
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, vertical)
        self.toggle.setText(f"{arrow}  {self._title}")
        self.toggle.setToolTip(
            "Свернуть" if self._expanded else "Развернуть"
        )
        self.content.setVisible(self._expanded)


def _is_zero_amount_text(text: str) -> bool:
    """True для ``0``, ``0,00``, ``,00``, ``0.00 руб.`` и подобных нулей."""

    cleaned = (text or "").strip().lower().removesuffix("руб.").removesuffix("руб")
    cleaned = cleaned.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned:
        return False
    try:
        return Decimal(cleaned) == 0
    except InvalidOperation:
        return False


class AmountLineEdit(QLineEdit):
    """Поле денежной суммы без мешающих начальных нулей.

    Запрос пользователя: «нули при вводе должны убираться автоматически».
    Поэтому начальное ``0,00`` не показывается как настоящий текст, а
    если нулевое значение попало в поле программно, оно очищается при
    получении фокуса. Ненулевая сумма при фокусе выделяется целиком,
    чтобы новая цифра сразу заменяла старое значение.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__("" if _is_zero_amount_text(text) else text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt API
        super().focusInEvent(event)
        if _is_zero_amount_text(self.text()):
            self.clear()
        elif self.text():
            if event.reason() == Qt.FocusReason.MouseFocusReason:
                # После щелчка мыши Qt ещё поставит курсор в точку клика;
                # выделяем отложенно, чтобы выделение не сбросилось.
                QTimer.singleShot(0, self.selectAll)
            else:
                # Клавиатурный/программный фокус: выделить сразу, чтобы
                # первая же набранная цифра заменила старую сумму.
                self.selectAll()


def make_amount_edit(text: str = "") -> AmountLineEdit:
    """Создать поле суммы с автоматической очисткой нулевого значения."""

    return AmountLineEdit(text)


_DISABLED_BUTTON_STYLE = "QPushButton:disabled{background:#dccdb8;color:#fffdf8}"


def danger_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.RED};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#a81515}}"
        f"{_DISABLED_BUTTON_STYLE}"
    )
    return btn


def green_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.GREEN};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#0f7435}}"
        f"{_DISABLED_BUTTON_STYLE}"
    )
    return btn


def panel_title_button(
    text: str,
    font_size: int | None = None,
    tooltip: str = "",
) -> QPushButton:
    """Заголовок плашки в виде кнопки (v0.85.0).

    Просьба пользователя: слова «Поступление», «Оплата» и «Сводка» —
    кнопки, по нажатию открывается своя таблица (сводка поступлений,
    сводка оплат, журнал движения средств). Шрифт — как у прежних
    заголовков-подписей (жирный, крупный), но с рамкой и подсветкой,
    чтобы было видно: это кнопка, а не подпись.
    """

    button = QPushButton(text)
    button.setObjectName("PanelTitleButton")
    size = f"font-size:{font_size}px;" if font_size else ""
    button.setStyleSheet(
        "QPushButton#PanelTitleButton{"
        f"background:{styles.PANEL_BG};border:1px solid {styles.BORDER};"
        "border-radius:8px;text-align:left;padding:4px 12px;"
        f"font-weight:800;{size}color:{styles.TEXT}}}"
        "QPushButton#PanelTitleButton:hover{"
        f"background:{styles.ACCENT};border-color:{styles.ACCENT};color:#fff}}"
        "QPushButton#PanelTitleButton:pressed{"
        f"background:{styles.ACCENT_HOVER};border-color:{styles.ACCENT_HOVER};"
        "color:#fff}"
    )
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def blue_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.BLUE};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#a8542f}}"
        f"{_DISABLED_BUTTON_STYLE}"
    )
    return btn


class TodayFrameCalendar(QCalendarWidget):
    """Календарь, в котором сегодняшняя дата обведена рамкой (v0.27.5).

    Стиль Windows не выделяет текущий день вовсе, поэтому рамку
    рисуем сами — общим для всех календарей цветом
    ``styles.TODAY_FRAME``.
    """

    def paintCell(self, painter, rect, day):  # noqa: N802 - Qt API
        super().paintCell(painter, rect, day)
        if day != QDate.currentDate():
            return
        painter.save()
        painter.setPen(QPen(QColor(styles.TODAY_FRAME), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Рамка внутри ячейки: вплотную к краю она срезается соседями.
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.restore()


def make_date_edit(day) -> QDateEdit:
    """Поле даты в формате макетов дд.мм.гггг с всплывающим календарём."""
    edit = QDateEdit()
    edit.setDisplayFormat("dd.MM.yyyy")
    edit.setCalendarPopup(True)
    # v0.27.5: текущая дата в календаре обведена рамкой.
    edit.setCalendarWidget(TodayFrameCalendar(edit))
    # Дата целиком + зона стрелки календаря справа (без наложения на
    # год). v0.28.2: стрелка вдвое шире — минимум увеличен со 138.
    # v0.76.0: минимум считаем по метрикам текста, а не числом.
    edit.setMinimumWidth(DATE_EDIT_MIN_WIDTH)
    edit.setDate(QDate(day.year, day.month, day.day))
    return edit


# Ширина поля даты: поля ячейки (8+8), рамка (2), запас под кнопку
# календаря (44, см. стиль QDateEdit в styles.py) и ещё 4 про запас.
DATE_EDIT_MIN_WIDTH = 158
DATE_EDIT_PADDING = 8 * 2 + 2 + 44 + 4


def fit_date_edit_width(edit, sample: str = "00.00.0000") -> None:
    """Растянуть поле даты под самый широкий текст (v0.76.0).

    Ширина 158 точек подобрана под обычный кегль интерфейса. Там, где
    шрифт поля крупнее (в «Сводке» он свой), её не хватало, и год
    обрезался: «14.11.202» вместо «14.11.2026». Теперь минимум
    считается по метрикам самого текста.
    """

    metrics = QFontMetrics(edit.font())
    edit.setMinimumWidth(
        max(DATE_EDIT_MIN_WIDTH, metrics.horizontalAdvance(sample) + DATE_EDIT_PADDING)
    )


def date_edit_value(edit: QDateEdit):
    qd = edit.date()
    from datetime import date

    return date(qd.year(), qd.month(), qd.day())


def set_date_edit(edit: QDateEdit, day) -> None:
    edit.setDate(QDate(day.year, day.month, day.day))


def kpi_card(title: str, value_text: str, green_value: bool = False) -> tuple[QFrame, QLabel]:
    """KPI-карточка «Сводки». Возвращает (карточка, label значения)."""
    frame = QFrame()
    frame.setObjectName("KpiCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(4)
    title_label = QLabel(title)
    title_label.setObjectName("KpiTitle")
    value_label = QLabel(value_text)
    value_label.setObjectName("KpiValue")
    if green_value:
        value_label.setStyleSheet(f"color:{styles.GREEN}")
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    return frame, value_label


# ------------------------------------------------------------------ стрелки
class NoFocusItemDelegate(QStyledItemDelegate):
    """Ячейка таблицы без рамки фокуса (v0.26.9).

    Qt рисует рамку на **текущей** ячейке поверх выделения: в узких
    колонках дней она выглядит как два вертикальных штриха и
    «переезжает» на каждую следующую нажатую ячейку.
    """

    def paint(self, painter, option, index):  # noqa: N802 - Qt API
        option = QStyleOptionViewItem(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)


def disable_selection_color(root: "QWidget") -> None:
    """Сделать цвет выделения невидимым у всех таблиц (v0.27.1).

    Линии и фон на текущей (нажатой) строке Qt рисует цветом
    ``QPalette.Highlight``. Замер показал: QSS, заданный **на самом
    виджете**, его палитру не меняет — у таблиц с
    ``WIDE_SCROLLBAR_ON_TABLE`` цвет выделения остаётся системным
    (``#308cc6``, на Windows — синий), отсюда синие штрихи-артефакты.
    Поэтому цвет задаётся напрямую, а не через QSS.
    """

    white = QColor("#fffdf8")
    text = QColor("#4a3f35")
    for table in root.findChildren(QAbstractItemView):
        palette = table.palette()
        palette.setColor(QPalette.ColorRole.Highlight, white)
        palette.setColor(QPalette.ColorRole.HighlightedText, text)
        table.setPalette(palette)
        table.viewport().setPalette(palette)


def disable_item_focus(root: "QWidget") -> None:
    """Снять фокус у всех таблиц внутри ``root`` (v0.26.5).

    Без фокуса Qt не рисует на текущей (нажатой) ячейке рамку и
    отдельный прямоугольник — тот самый «артефакт», который появлялся
    после клика и переезжал на следующую ячейку. Ввод по клику
    (v0.12.0) работает и без фокуса: его берёт сам редактор.
    """

    for table in root.findChildren(QAbstractItemView):
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)


def arrow_button(
    text: str, tooltip: str = "", bold: bool = False, plain: bool = False
) -> QPushButton:
    """Кнопка-стрелка «←»/«→».

    По умолчанию — обычная, как до v0.13.0. С ``bold=True`` стрелка
    жирная и крупнее: с v0.15.0 жирными остались только стрелки блоков
    «Поступление» и «Оплата» на первой вкладке.

    С ``plain=True`` (v0.25.2) свой кегль и жирность не задаются вовсе:
    кнопка набрана обычным шрифтом — так же, как кнопка «Сегодня»
    (просьба пользователя: подпись «Неделя» — шрифтом «Сегодня»).
    """

    button = QPushButton(text)
    # Узкая фиксированная ширина — только для одиночных стрелок
    # «←»/«→». Кнопкам с подписью («← Неделя», «Неделя →») ширина
    # нужна по содержимому: иначе текст не помещается и обрезается
    # (v0.15.0).
    if len(text) <= 2:
        button.setFixedWidth(52)
    if plain:
        pass  # шрифт как у обычной кнопки — без своего кегля
    elif bold:
        # Стиль приложения задаёт кнопкам свой шрифт, поэтому жирность
        # и кегль задаются таблицей стилей самого виджета: она имеет
        # приоритет над общим QSS и над ``setFont``.
        button.setStyleSheet(
            "QPushButton{"
            f"font-weight:800;font-size:{styles.fs(20)}px;"
            "padding:0 4px;}"
        )
    else:
        button.setStyleSheet(
            f"QPushButton{{font-weight:800;font-size:{styles.fs(16)}px;"
            "padding:2px 8px;}"
        )
    if tooltip:
        button.setToolTip(tooltip)
    if len(text) > 2:
        # Ширина по тексту: иначе длинная подпись не помещается.
        button.adjustSize()
        button.setMinimumWidth(max(52, button.sizeHint().width() + 12))
    return button


# ------------------------------------------------------- подсветка строки
class RowHoverMixin:
    """Рисует бежевую подсветку **всей строки** под курсором (v0.13.0).

    Подмешивается к делегату таблицы: ``paint`` вызывает базовую
    отрисовку, а поверх кладёт полупрозрачный бежевый прямоугольник —
    так подсветка видна и на ячейках со своим фоном (розовые выходные
    в недельных таблицах). Номер строки под курсором задаёт
    ``install_row_hover``.
    """

    HOVER_COLOR = QColor(246, 236, 210, 120)  # бежевый, полупрозрачный

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._hover_row = -1

    def set_hover_row(self, row: int) -> None:
        self._hover_row = row

    def paint(self, painter, option, index):  # noqa: N802 - Qt API
        # v0.24.3: без пунктирной рамки фокуса на ячейке. В Windows Qt
        # рисует её после клика, и по таблице шли «штришки»-артефакты.
        option = QStyleOptionViewItem(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        # v0.26.9: без состояния Selected Qt не рисует ни фон, ни рамку
        # выделения — ячейка после клика выглядит так же, как до него
        # (жалоба на «штрихи»-артефакт на нажатой ячейке). Само
        # выделение как механизм остаётся: по нему работают «−»,
        # currentRow и ввод.
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, index)
        if (
            self._hover_row >= 0
            and index.row() == self._hover_row
            and (index.flags() & Qt.ItemFlag.ItemIsEnabled)
        ):
            painter.fillRect(option.rect, self.HOVER_COLOR)


class _HoverLeaveFilter(QObject):
    """Снимает подсветку, когда курсор ушёл с таблицы."""

    def __init__(self, parent, on_leave) -> None:
        super().__init__(parent)
        self._on_leave = on_leave

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Leave:
            self._on_leave()
        return False


def install_row_hover(table, *delegates) -> None:
    """Подсвечивать строку под курсором во всех таблицах (v0.13.0)."""

    table.setMouseTracking(True)
    holders = list(delegates)

    def set_row(row: int) -> None:
        for delegate in holders:
            setter = getattr(delegate, "set_hover_row", None)
            if setter is not None:
                setter(row)
        table.viewport().update()

    table.itemEntered.connect(lambda item: set_row(item.row()))
    viewport = table.viewport()
    viewport.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
    filter_obj = _HoverLeaveFilter(table, lambda: set_row(-1))
    viewport.installEventFilter(filter_obj)
    # Держим ссылку, иначе фильтр соберётся сборщиком мусора.
    table._hover_filter = filter_obj  # noqa: SLF001 - служебное поле Qt


class ColumnResizer(QObject):
    """Протяжка границ столбцов мышью **по телу таблицы** (v0.75.0).

    Штатно Qt тянет границы только за заголовок, а у недельных таблиц
    заголовок занят закреплённой шапкой, до которой мышью не добраться.
    Поэтому границу ищем по координатам ячеек: попав в полосу в
    ``EDGE`` пикселей от края ячейки, курсор становится «разделителем»,
    и нажатая кнопка тянет границу.

    Ширина пары соседних колонок при этом не меняется — правая
    отдаёт столько, сколько взяла левая, — поэтому сумма ширин всегда
    равна ширине таблицы и горизонтальная полоса прокрутки не
    появляется (просьба пользователя: прокрутки быть не должно).
    """

    EDGE = 5  # px от края ячейки: ширина «ручки» за которую тянем
    MIN_WIDTH = 24

    def __init__(self, table, host=None, min_width: int = MIN_WIDTH) -> None:
        super().__init__(table)
        self.table = table
        self.host = host
        self.min_width = min_width
        # v0.76.0: пока правка выключена, границы не тянутся — иначе
        # случайный захват мышью у края ячейки менял ширину столбца.
        # Включает её кнопка «Редактировать поля» в «Конфигурации».
        self.enabled = False
        self.column: int | None = None  # колонка **слева** от границы
        self._start_x = 0
        self._start_width = 0
        self._neighbor_width = 0
        self._on_edge = False
        viewport = table.viewport()
        viewport.setMouseTracking(True)
        viewport.installEventFilter(self)

    # ------------------------------------------------------------- граница
    def edge_at(self, x: int) -> int | None:
        """Номер колонки слева от границы под точкой ``x`` (или ``None``)."""

        count = self.table.columnCount()
        for col in range(count):
            start = self.table.columnViewportPosition(col)
            end = start + self.table.columnWidth(col)
            if start - self.EDGE <= x <= end + self.EDGE:
                if x - start <= self.EDGE and col > 0:
                    return col - 1
                if end - x <= self.EDGE and col + 1 < count:
                    return col
                return None
        return None

    def _notify(self, column: int, width: int) -> None:
        host = self.host
        if host is None:
            return
        for name in ("_sync_column_width", "_on_column_resized"):
            handler = getattr(host, name, None)
            if handler is not None:
                handler(column, width)

    # -------------------------------------------------------------- события
    def set_enabled(self, enabled: bool) -> None:
        """Разрешить или запретить протяжку (v0.76.0)."""

        self.enabled = enabled
        if not enabled:
            self.column = None
            self.table.viewport().unsetCursor()

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        kind = event.type()
        if not self.enabled:
            return False
        if kind == QEvent.Type.MouseButtonPress:
            if (event.button() == Qt.MouseButton.LeftButton
                    and self.edge_at(int(event.position().x())) is not None):
                self.column = self.edge_at(int(event.position().x()))
                self._start_x = int(event.position().x())
                self._start_width = self.table.columnWidth(self.column)
                self._neighbor_width = (
                    self.table.columnWidth(self.column + 1)
                    if self.column + 1 < self.table.columnCount()
                    else 0
                )
                return True  # не пускаем клик в ячейку: это не ввод
        elif kind == QEvent.Type.MouseMove:
            x = int(event.position().x())
            if self.column is not None:
                self._drag_to(x)
                return True
            if event.buttons() == Qt.MouseButton.NoButton:
                edge = self.edge_at(x)
                self._on_edge = edge is not None
                self.table.viewport().setCursor(
                    Qt.CursorShape.SplitHCursor if self._on_edge
                    else Qt.CursorShape.ArrowCursor
                )
        elif kind == QEvent.Type.MouseButtonRelease:
            if self.column is not None:
                column = self.column
                self.column = None
                host = self.host
                notify = getattr(host, "_on_column_resized", None)
                if notify is not None:
                    notify(column, self.table.columnWidth(column))
                return True
        elif kind == QEvent.Type.Leave:
            self._on_edge = False
            self.table.viewport().unsetCursor()
        return False

    def _drag_to(self, x: int) -> None:
        """Тянем границу: левая забирает, правая отдаёт."""

        taken = max(
            self.min_width, self._start_width + (x - self._start_x)
        ) - self._start_width
        width = self._start_width + taken
        self.table.setColumnWidth(self.column, width)
        self._notify(self.column, width)
        if self._neighbor_width:
            neighbor = max(
                self.min_width, self._neighbor_width - taken
            )
            self.table.setColumnWidth(self.column + 1, neighbor)
            self._notify(self.column + 1, neighbor)


def install_column_resizer(table, host=None, min_width: int = ColumnResizer.MIN_WIDTH):
    """Разрешить тянуть границы столбцов мышью по телу таблицы (v0.75.0).

    Ссылку на фильтр держим на самой таблице, иначе сборщик мусора
    уберёт его и протяжка перестанет работать (как в ``install_row_hover``).
    """

    resizer = ColumnResizer(table, host, min_width=min_width)
    table._column_resizer = resizer  # noqa: SLF001 - служебное поле Qt
    return resizer


class FrozenColumns(QObject):
    """Закреплённые левые колонки таблицы (v0.77.0).

    Таблица шире окна и прокручивается по горизонтали — без
    закрепления уезжали бы названия строк, и непонятно было бы, чьи
    цифры на экране. Работает наложением: вторая таблица-вид с **той
    же моделью** лежит поверх основной, показывает первые ``count``
    колонок и не прокручивается по горизонтали. Модель общая, поэтому
    правки основной таблицы видны в закреплённой сразу; ``QTableView``
    (а не ``QTableWidget``) — потому что у ``QTableWidget`` ``setModel``
    закрыт, а модель надо отдать обеим таблицам.
    """

    def __init__(self, table: QTableWidget, count: int) -> None:
        super().__init__(table)
        self.table = table
        self.count = count
        self.view = QTableView(table)
        view = self.view
        view.setModel(table.model())
        # Стиль основной таблицы написан для QTableWidget — подменяем
        # имя класса, чтобы накладка выглядела так же.
        view.setStyleSheet(
            table.styleSheet().replace("QTableWidget", "QTableView")
        )
        # Сама накладка не должна перехватывать ни фокус, ни ввод:
        # это просто картинка поверх левой части таблицы.
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # v0.86.0: накладка — только картинка. Мышь (колесо, клики,
        # наведение) проходит сквозь неё к основной таблице. Иначе над
        # левыми колонками прокрутка и наведение вели себя иначе, чем
        # над правой частью: казалось, что слева лежит отдельная таблица.
        view.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        view.verticalHeader().setVisible(False)
        view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )
        for column in range(count, table.columnCount()):
            view.setColumnHidden(column, True)
        # Прокрутка по вертикали — общая с основной таблицей: колесо
        # над закреплёнными колонками листает обе таблицы. Следим только
        # за основной: значение её полосы и смещение накладки считаются
        # по-разному (см. ``_follow_main``), поэтому обратной связи нет.
        table.verticalScrollBar().valueChanged.connect(self._follow_main)
        # Ширина закреплённых колонок меняется вместе с основной.
        table.horizontalHeader().sectionResized.connect(self._sync_widths)
        view.show()
        self.relayout()

    # ------------------------------------------------------------- раскладка
    def relayout(self) -> None:
        """Переставить накладку: она закрывает левые колонки таблицы."""

        table = self.table
        self._sync_widths()
        self._sync_heights()
        # v0.86.0: у накладки своя рамка — её надо вычесть из смещения,
        # иначе содержимое (и строки) стоит на 1 px ниже и правее, чем в
        # таблице: «строки слева немного двигаются вверх-вниз».
        frame = table.frameWidth()
        own = self.view.frameWidth()
        header = table.horizontalHeader().height()
        # Верх накладки ставим так, чтобы **поле строк** началось там же,
        # где у таблицы: своя шапка накладки встаёт на место шапки
        # таблицы, а не под неё.
        top = max(frame + header - own - self.view.horizontalHeader().height(), 0)
        self.view.setGeometry(
            table.verticalHeader().width() + frame - own,
            top,
            sum(table.columnWidth(column) for column in range(self.count))
            + 2 * own,
            frame + header + table.viewport().height() - top,
        )
        self.view.raise_()

    def _sync_widths(self, *_args) -> None:
        for column in range(self.count):
            self.view.setColumnWidth(column, self.table.columnWidth(column))

    def _sync_heights(self) -> None:
        table = self.table
        self.view.verticalHeader().setDefaultSectionSize(
            table.verticalHeader().defaultSectionSize()
        )
        for row in range(table.rowCount()):
            self.view.setRowHeight(row, table.rowHeight(row))

    def _follow_main(self, _value: int) -> None:
        """Держать строки накладки там же, где они стоят в таблице.

        v0.86.0: основная таблица листается **по строкам**, а накладка —
        по пикселям, поэтому копировать значение полосы нельзя: «8» у
        одной значит 8 строк, у другой — 8 пикселей, и строки слева
        уезжали относительно правой части (замечание пользователя:
        «левая часть как будто другая наклеенная сверху таблица, в
        которой строки могут немного двигаться вверх вниз»). Берём
        настоящее смещение строки в пикселях.
        """

        table = self.table
        if table.rowCount() == 0:
            return
        offset = -table.rowViewportPosition(0)
        bar = self.view.verticalScrollBar()
        if bar.value() != offset:
            bar.setValue(offset)
