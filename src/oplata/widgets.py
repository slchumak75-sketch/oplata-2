"""Мелкие общие виджеты/фабрики, чтобы экраны не дублировали стили кнопок."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QFocusEvent
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
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


_DISABLED_BUTTON_STYLE = "QPushButton:disabled{background:#cbd5e1;color:#fff}"


def danger_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.RED};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#c81e1e}}"
        f"{_DISABLED_BUTTON_STYLE}"
    )
    return btn


def green_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.GREEN};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#11853d}}"
        f"{_DISABLED_BUTTON_STYLE}"
    )
    return btn


def blue_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.BLUE};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#1d4ed8}}"
        f"{_DISABLED_BUTTON_STYLE}"
    )
    return btn


def make_date_edit(day) -> QDateEdit:
    """Поле даты в формате макетов дд.мм.гггг с всплывающим календарём."""
    edit = QDateEdit()
    edit.setDisplayFormat("dd.MM.yyyy")
    edit.setCalendarPopup(True)
    # Дата целиком + зона стрелки календаря справа (без наложения на год).
    edit.setMinimumWidth(138)
    edit.setDate(QDate(day.year, day.month, day.day))
    return edit


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
def arrow_button(text: str, tooltip: str = "") -> QPushButton:
    """Кнопка со **жирной** стрелкой (v0.13.0): крупнее и заметнее."""

    button = QPushButton(text)
    button.setFixedWidth(52)
    font = button.font()
    font.setBold(True)
    if font.pointSize() > 0:
        font.setPointSize(font.pointSize() + 6)
    else:
        font.setPixelSize(styles.fs(20))
    button.setFont(font)
    button.setStyleSheet(
        f"QPushButton{{font-weight:800;font-size:{styles.fs(16)}px;"
        "padding:2px 8px;}"
    )
    if tooltip:
        button.setToolTip(tooltip)
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
