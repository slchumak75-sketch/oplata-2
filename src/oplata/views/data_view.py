"""Экран «Данные» — календарь «Выходные и рабочие дни» (макет 05).

Алгоритм пользователя (v0.6.0):

1. выбрать галочку-режим «Выходные дни» и кликами расставить выходные
   (подсвечиваются красным);
2. выбрать галочку-режим «Рабочие дни» и кликами расставить рабочие
   (подсвечиваются серым) — так отмечаются законодательные переносы;
3. нажать кнопку «Сохранить».

До сохранения клики меняют только черновик внутри этого экрана
(``_pending_overrides``): файл данных и расчёт сроков не трогаются.
После «Сохранить» отметки попадают в ``AppData.day_overrides`` и
используются ``calc.payment_due_date`` для таблиц «Поставщики» и
«Покупатели». Уже сохранённый в операции срок задним числом не
переписывается (ADR-007/009); календарь влияет на новые вводы и на
операции без сохранённого срока.

По умолчанию Сб/Вс — выходные, Пн–Пт — рабочие. Если выбранный режим
совпадает с умолчанием дня, ручная отметка для него не хранится.

Внизу добавлен служебный блок с путём к файлу данных (его не было на
макете — нужен пользователю/агентам, чтобы знать, где лежат данные).
"""

from __future__ import annotations

import calendar as calendar_module
from collections.abc import Callable
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
from ..widgets import blue_button, make_panel

MONTHS_PER_ROW = 5  # как на макете: Январь–Май в первой строке


class DataView(QWidget):
    def __init__(
        self,
        store: DataStore,
        on_calendar_saved: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.on_calendar_saved = on_calendar_saved
        self.year = date.today().year
        self._day_buttons: dict[date, QPushButton] = {}
        # Черновик отметок: клики по календарю меняют его, а не файл
        # данных. В store он попадёт только по кнопке «Сохранить».
        self._pending_overrides: dict[str, str] = dict(store.data.day_overrides)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        panel, panel_layout = make_panel()

        title = QLabel("📅  Выходные и рабочие дни")
        title.setObjectName("PanelTitle")
        panel_layout.addWidget(title)

        # --- режим разметки + навигация по годам ---
        top = QHBoxLayout()
        self.chk_off = QCheckBox("Выходные дни")
        self.chk_off.setChecked(True)
        self.chk_off.setToolTip("Клик по дню сделает его выходным (красным)")
        self.chk_work = QCheckBox("Рабочие дни")
        self.chk_work.setToolTip("Клик по дню сделает его рабочим (серым)")
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

        # --- подсказка, статус черновика и явное сохранение ---
        actions = QHBoxLayout()
        hint = QLabel(
            "Выберите режим и кликайте по дням: выходные — красные, "
            "рабочие — серые. Затем нажмите «Сохранить»."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        actions.addWidget(hint, stretch=1)

        self.status_label = QLabel("Изменений нет")
        self.status_label.setObjectName("Muted")
        actions.addWidget(self.status_label)

        self.cancel_button = QPushButton("Отменить изменения")
        self.cancel_button.clicked.connect(self.discard_changes)
        actions.addWidget(self.cancel_button)

        self.save_button = blue_button("Сохранить")
        self.save_button.clicked.connect(self.save_calendar)
        actions.addWidget(self.save_button)
        panel_layout.addLayout(actions)

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
        self.path_label = QLabel(str(self.store.path))
        self.path_label.setObjectName("Muted")
        self.path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        info_layout.addWidget(self.path_label)
        info_hint = QLabel(
            "Импорт / экспорт / резервные копии — позже (см. docs/ROADMAP.md)."
        )
        info_hint.setObjectName("Muted")
        info_layout.addWidget(info_hint)
        root.addWidget(info_panel)

        self.refresh()
        self._update_save_state()

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
        self._update_save_state()

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
                btn.clicked.connect(
                    lambda _checked=False, d=day: self._on_day_clicked(d)
                )
                self._day_buttons[day] = btn
                self._paint_day(btn, day)
                days_grid.addWidget(btn, r, c)
        layout.addLayout(days_grid)
        layout.addStretch(1)
        return frame

    # ----------------------------------------------------------- отметки дней
    @property
    def has_unsaved_changes(self) -> bool:
        """Есть ли отметки, ещё не попавшие в файл данных."""

        return self._pending_overrides != self.store.data.day_overrides

    def _is_day_off(self, day: date) -> bool:
        override = self._pending_overrides.get(day.isoformat())
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
        btn.setToolTip(
            f"{day:%d.%m.%Y}: {'выходной' if is_off else 'рабочий'} день"
        )

    def _on_day_clicked(self, day: date) -> None:
        mode = "off" if self.chk_off.isChecked() else "work"
        is_default_off = day.weekday() >= 5
        if (mode == "off") == is_default_off:
            self._pending_overrides.pop(day.isoformat(), None)
        else:
            self._pending_overrides[day.isoformat()] = mode

        btn = self._day_buttons.get(day)
        if btn is not None:
            self._paint_day(btn, day)
        self._update_save_state()

    def _update_save_state(self, message: str | None = None) -> None:
        if not hasattr(self, "save_button"):
            return
        dirty = self.has_unsaved_changes
        self.save_button.setEnabled(dirty)
        self.cancel_button.setEnabled(dirty)
        if message is not None:
            self.status_label.setText(message)
        elif dirty:
            self.status_label.setText("Есть несохранённые изменения")
        else:
            self.status_label.setText("Изменений нет")

    def discard_changes(self) -> None:
        """Вернуть черновик календаря к последнему сохранённому состоянию."""

        self._pending_overrides = dict(self.store.data.day_overrides)
        for day, btn in self._day_buttons.items():
            self._paint_day(btn, day)
        self._update_save_state("Изменения отменены")

    def save_calendar(self) -> bool:
        """Сохранить черновик отметок и уведомить главное окно.

        Возвращает ``False``, если сохранять было нечего.
        """

        if not self.has_unsaved_changes:
            self._update_save_state("Изменений нет")
            return False
        self.store.data.day_overrides = dict(self._pending_overrides)
        self.store.save()
        self._update_save_state("Сохранено")
        if self.on_calendar_saved is not None:
            self.on_calendar_saved()
        return True
