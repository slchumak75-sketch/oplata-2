"""Мелкие общие виджеты/фабрики, чтобы экраны не дублировали стили кнопок."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
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
