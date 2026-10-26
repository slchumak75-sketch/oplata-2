"""Мелкие общие виджеты/фабрики, чтобы экраны не дублировали стили кнопок."""

from __future__ import annotations

from PySide6.QtWidgets import QDateEdit, QFrame, QPushButton, QVBoxLayout, QLabel
from PySide6.QtCore import QDate, Qt

from . import styles


def make_panel() -> tuple[QFrame, QVBoxLayout]:
    """Белая панель со скруглением (как блоки на макетах) + её layout."""
    frame = QFrame()
    frame.setObjectName("Panel")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(10)
    return frame, layout


def danger_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.RED};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#c81e1e}}"
    )
    return btn


def green_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.GREEN};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#11853d}}"
    )
    return btn


def blue_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        f"QPushButton{{background:{styles.BLUE};color:#fff;border:none;"
        f"border-radius:8px;padding:7px 16px;font-weight:700}}"
        f"QPushButton:hover{{background:#1d4ed8}}"
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
