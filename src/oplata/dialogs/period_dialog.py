"""Окно выбора периода: две даты «с … по …» (v0.75.0).

Нужно для команды «Очистить таблицы»: она стирает цифровые значения
не целиком, а за выбранный период.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from .. import styles


class PeriodDialog(QDialog):
    """Две даты и кнопки «Очистить» / «Отмена».

    Дата «по» не раньше даты «с»: при выборе более ранней «по» она
    подтягивается к «с», чтобы период не оказался пустым.
    """

    def __init__(
        self,
        parent=None,
        title: str = "Период",
        hint: str = "",
        day_from: date | None = None,
        day_to: date | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        today = date.today()
        self.day_from = day_from or date(today.year, 1, 1)
        self.day_to = day_to or today

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        if hint:
            label = QLabel(hint)
            label.setObjectName("Muted")
            label.setWordWrap(True)
            root.addWidget(label)

        form = QFormLayout()
        form.setSpacing(8)
        self.from_edit = QDateEdit()
        self.from_edit.setCalendarPopup(True)
        self.from_edit.setDisplayFormat("dd.MM.yyyy")
        self.to_edit = QDateEdit()
        self.to_edit.setCalendarPopup(True)
        self.to_edit.setDisplayFormat("dd.MM.yyyy")
        for edit, value in (
            (self.from_edit, self.day_from),
            (self.to_edit, self.day_to),
        ):
            edit.setDate(value)
            edit.setStyleSheet(f"QDateEdit{{font-size:{styles.fs(13)}px}}")
        self.from_edit.dateChanged.connect(self._sync_to_date)
        self.to_edit.dateChanged.connect(self._sync_from_date)
        form.addRow("С:", self.from_edit)
        form.addRow("По:", self.to_edit)
        root.addLayout(form)

        buttons = QDialogButtonBox()
        ok = buttons.addButton("Очистить", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _sync_to_date(self) -> None:
        """«По» не раньше «с»."""

        if self.to_edit.date() < self.from_edit.date():
            self.to_edit.blockSignals(True)
            self.to_edit.setDate(self.from_edit.date())
            self.to_edit.blockSignals(False)

    def _sync_from_date(self) -> None:
        """«С» не позже «по»."""

        if self.from_edit.date() > self.to_edit.date():
            self.from_edit.blockSignals(True)
            self.from_edit.setDate(self.to_edit.date())
            self.from_edit.blockSignals(False)

    def period(self) -> tuple[date, date]:
        """Выбранный период (обе даты включительно)."""

        return (
            self.from_edit.date().toPython(),
            self.to_edit.date().toPython(),
        )
