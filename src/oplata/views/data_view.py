"""Экран «Данные» — календарь «Выходные и рабочие дни» (макет 05).

Логика оболочки (это не формулы, а разметка календаря):
* по умолчанию Сб/Вс — выходные (красные), Пн–Пт — рабочие (серые);
* режим сверху («Выходные дни» / «Рабочие дни») задаёт, чем станет день
  при клике; ручные отметки хранятся в ``AppData.day_overrides``;
* как эти отметки повлияют на расчёт сроков оплаты — вопрос этапа формул
  (см. docs/ROADMAP.md), здесь они только сохраняются.

Внизу добавлен служебный блок с путём к файлу данных (его не было на
макете — нужен пользователю/агентам, чтобы знать, где лежат данные).
"""

from __future__ import annotations

import calendar as calendar_module
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import styles
from ..dates import MONTHS_RU, WEEKDAYS_RU_TITLE
from ..storage import DataStore
from ..widgets import make_panel

MONTHS_PER_ROW = 5  # как на макете: Январь–Май в первой строке


class DataView(QWidget):
    def __init__(self, store: DataStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.year = date.today().year
        self._day_buttons: dict[date, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        panel, panel_layout = make_panel()

        title = QLabel("📅  Выходные и рабочие дни")
        title.setObjectName("PanelTitle")
        panel_layout.addWidget(title)

        # --- режим + навигация по годам ---
        top = QHBoxLayout()
        self.chk_off = QCheckBox("Выходные дни")
        self.chk_off.setChecked(True)
        self.chk_work = QCheckBox("Рабочие дни")
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        mode_group.addButton(self.chk_off)
        mode_group.addButton(self.chk_work)
        top.addWidget(self.chk_off)
        top.addWidget(self.chk_work)
        top.addStretch(1)

        prev_year = QPushButton("←")
        prev_year.clicked.connect(lambda: self._shift_year(-1))
        top.addWidget(prev_year)
        self.year_label = QLabel(str(self.year))
        self.year_label.setStyleSheet("font-weight:800;font-size:16px")
        top.addWidget(self.year_label)
        next_year = QPushButton("→")
        next_year.clicked.connect(lambda: self._shift_year(1))
        top.addWidget(next_year)
        panel_layout.addLayout(top)

        # --- сетка из 12 мини-календарей ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        panel_layout.addWidget(self.scroll, stretch=1)
        root.addWidget(panel, stretch=1)

        # --- служебный блок: где лежит файл данных ---
        info_panel, info_layout = make_panel()
        info_title = QLabel("Файл данных")
        info_title.setObjectName("PanelTitle")
        info_layout.addWidget(info_title)
        path_label = QLabel(str(self.store.path))
        path_label.setObjectName("Muted")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(path_label)
        hint = QLabel(
            "Импорт / экспорт / резервные копии — позже (см. docs/ROADMAP.md)."
        )
        hint.setObjectName("Muted")
        info_layout.addWidget(hint)
        root.addWidget(info_panel)

        self.refresh()

    # ------------------------------------------------------------ навигация
    def _shift_year(self, delta: int) -> None:
        self.year += delta
        self.refresh()

    # ------------------------------------------------------------- календарь
    def refresh(self) -> None:
        self.year_label.setText(str(self.year))
        self._day_buttons = {}

        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(12)
        for month in range(1, 13):
            row = (month - 1) // MONTHS_PER_ROW
            col = (month - 1) % MONTHS_PER_ROW
            grid.addWidget(self._build_month(self.year, month), row, col)
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(container)

    def _build_month(self, year: int, month: int) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        title = QLabel(MONTHS_RU[month - 1])
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight:800;font-size:14px")
        layout.addWidget(title)

        days_grid = QGridLayout()
        days_grid.setSpacing(3)
        for col, name in enumerate(WEEKDAYS_RU_TITLE):
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("Muted")
            days_grid.addWidget(label, 0, col)

        weeks = calendar_module.monthcalendar(year, month)  # недели с Пн
        for r, week in enumerate(weeks, start=1):
            for c, day_num in enumerate(week):
                if day_num == 0:
                    continue
                day = date(year, month, day_num)
                btn = QPushButton(str(day_num))
                btn.setFixedSize(32, 26)
                btn.clicked.connect(lambda _checked=False, d=day: self._on_day_clicked(d))
                self._day_buttons[day] = btn
                self._paint_day(btn, day)
                days_grid.addWidget(btn, r, c)
        layout.addLayout(days_grid)
        layout.addStretch(1)
        return frame

    # ----------------------------------------------------------- отметки дней
    def _is_day_off(self, day: date) -> bool:
        override = self.store.data.day_overrides.get(day.isoformat())
        if override == "off":
            return True
        if override == "work":
            return False
        return day.weekday() >= 5

    def _paint_day(self, btn: QPushButton, day: date) -> None:
        is_off = self._is_day_off(day)
        bg = styles.WEEKEND_RED if is_off else styles.WORKDAY_GREY
        fg = "#ffffff" if is_off else styles.TEXT
        border = f"2px solid {styles.BLUE}" if day == date.today() else "none"
        btn.setStyleSheet(
            f"QPushButton{{background:{bg};color:{fg};border:{border};"
            f"border-radius:5px;padding:0;font-weight:600}}"
        )

    def _on_day_clicked(self, day: date) -> None:
        mode = "off" if self.chk_off.isChecked() else "work"
        overrides = self.store.data.day_overrides
        is_default_off = day.weekday() >= 5
        if (mode == "off") == is_default_off:
            overrides.pop(day.isoformat(), None)  # совпало с умолчанием
        else:
            overrides[day.isoformat()] = mode
        self.store.save()
        btn = self._day_buttons.get(day)
        if btn is not None:
            self._paint_day(btn, day)
