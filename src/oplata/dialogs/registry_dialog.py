"""Предпросмотр и подтверждение загрузки реестра документов."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..dates import fmt_date, fmt_money_plain
from ..models import alphabetical_key
from ..registry import (
    KIND_TITLES,
    REPORT,
    RETURN,
    SALE,
    SUPPLY,
    Registry,
    RegistryChange,
    RegistryError,
    RegistrySnapshot,
    auto_assignments,
    commit_registry_plan,
    plan_registry,
)
from ..storage import BASE_TITLES, DataStore
from ..widgets import blue_button

SOURCE_HEADERS = (
    "№ стр.", "Дата", "Документ", "Номер", "Статус", "Сумма", "УНП",
    "Карточка в базе", "Информация", "Проверка",
)
PLAN_HEADERS = (
    "Дата", "Куда", "Документы", "Сейчас", "Станет", "Действие", "Подробности",
)


class RegistryDialog(QDialog):
    """Файл не изменяет базу до явного выбора и подтверждения пользователя."""

    def __init__(
        self,
        store: DataStore,
        registry: Registry,
        snapshot: RegistrySnapshot,
        source_name: str = "",
        parent: QWidget | None = None,
        *,
        guard: Callable[[], str | None] | None = None,
        on_imported: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.registry = registry
        self.snapshot = snapshot
        self.guard = guard
        self.on_imported = on_imported
        self.targets: dict[int, QComboBox] = {}
        self.changed = False
        self.plan = None
        self.setWindowTitle("Загрузка реестра — предпросмотр")
        self.setModal(True)
        self.resize(1300, 760)
        self.setMinimumSize(900, 500)

        root = QVBoxLayout(self)
        title = QLabel(f"Реестр: {source_name}  ·  база: {BASE_TITLES[snapshot.base_key]}")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-weight:800;font-size:17px")
        root.addWidget(title)
        hint = QLabel(
            "Файл должен содержать все документы каждого загружаемого дня: "
            "совпадающие суммы заменяются, а не прибавляются. Переоценка и "
            "прочие виды документов не загружаются. Проверьте обе таблицы ниже."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.ignored_label = QLabel(
            f"Документов к проверке: {len(registry.rows)}; "
            f"пропущено: переоценка — {registry.ignored_revaluation}, "
            f"остальные виды — {registry.ignored_other}."
        )
        self.ignored_label.setWordWrap(True)
        root.addWidget(self.ignored_label)
        choice_hint = QLabel(
            "Если УНП отсутствует, совпадает с несколькими карточками или карточка в "
            "архиве — выберите её вручную. Нет карточки? Закройте окно, создайте "
            "её и откройте реестр снова. Таблицы можно прокручивать вправо; "
            "для полного текста наведите указатель на ячейку."
        )
        choice_hint.setWordWrap(True)
        root.addWidget(choice_hint)

        splitter = QSplitter(Qt.Orientation.Vertical)
        source_panel = QWidget()
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(QLabel("Документы реестра"))
        self.source_table = QTableWidget(0, len(SOURCE_HEADERS))
        self._configure_table(self.source_table, SOURCE_HEADERS)
        for column, width in ((0, 75), (1, 125), (2, 230), (3, 90), (4, 145),
                              (5, 125), (6, 125), (7, 260), (8, 180), (9, 220)):
            self.source_table.setColumnWidth(column, width)
        source_layout.addWidget(self.source_table)
        splitter.addWidget(source_panel)

        plan_panel = QWidget()
        plan_layout = QVBoxLayout(plan_panel)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_layout.addWidget(QLabel("Что будет записано вместо нынешних сумм"))
        self.plan_table = QTableWidget(0, len(PLAN_HEADERS))
        self._configure_table(self.plan_table, PLAN_HEADERS)
        self.plan_table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        for column, width in ((0, 125), (1, 255), (2, 150), (3, 165), (4, 125), (5, 180)):
            self.plan_table.setColumnWidth(column, width)
        plan_layout.addWidget(self.plan_table)
        splitter.addWidget(plan_panel)
        splitter.setSizes([350, 260])
        root.addWidget(splitter, stretch=1)

        self.errors = QPlainTextEdit()
        self.errors.setReadOnly(True)
        self.errors.setMaximumHeight(90)
        self.errors.setStyleSheet("color:#a22525")
        root.addWidget(self.errors)
        self.confirm_base = QCheckBox(
            f"Я проверил(а): этот реестр относится к базе «{BASE_TITLES[snapshot.base_key]}»"
        )
        self.confirm_base.toggled.connect(self._refresh_enabled)
        root.addWidget(self.confirm_base)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.import_button = blue_button("Разнести реестр")
        self.import_button.clicked.connect(self._import)
        buttons.addWidget(self.import_button)
        root.addLayout(buttons)

        self._fill_source()
        self._rebuild_plan()

    @staticmethod
    def _configure_table(table: QTableWidget, headers: tuple[str, ...]) -> None:
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(False)  # порядок файла определяет строки отчёта ККМ
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

    @staticmethod
    def _cell(table: QTableWidget, row: int, column: int, text: str,
              tooltip: str = "", warning: bool = False) -> None:
        item = QTableWidgetItem(text)
        if tooltip:
            item.setToolTip(tooltip)
        if warning:
            item.setForeground(QColor("#a22525"))
        table.setItem(row, column, item)

    def _fill_source(self) -> None:
        automatic = auto_assignments(self.registry, self.store.data)
        self.source_table.setRowCount(len(self.registry.rows))
        for index, row in enumerate(self.registry.rows):
            if row.kind == SALE and row.invoiced is not None:
                status = "Проведен" if row.invoiced else "Не проведен"
                if not row.status:
                    status += " (пусто)"
            else:
                status = row.status or "—"
            values = (
                str(row.row_number), fmt_date(row.day) if row.day else "—",
                KIND_TITLES[row.kind], row.number or "—", status,
                fmt_money_plain(row.amount) if row.amount is not None else "—",
                row.unp or "—", "", row.information, "; ".join(row.issues),
            )
            for column, value in enumerate(values):
                self._cell(self.source_table, index, column, value, value, bool(row.issues))
            if row.kind not in (SUPPLY, SALE):
                self._cell(self.source_table, index, 7, "Отчеты ККМ")
                continue
            items = self.store.data.suppliers if row.kind == SUPPLY else self.store.data.customers
            combo = QComboBox()
            combo.view().setMinimumWidth(550)
            combo.addItem("— выберите карточку —", "")
            for item in sorted(items, key=alphabetical_key):
                text = item.name
                if item.unp:
                    text += f" · УНП {item.unp}"
                if item.note:
                    text += f" · {item.note}"
                if item.archived:
                    text += " · в архиве"
                combo.addItem(text, item.id)
            match = combo.findData(automatic.get(row.row_number, ""))
            combo.setCurrentIndex(max(match, 0))
            combo.currentIndexChanged.connect(lambda *_: self._rebuild_plan())
            self.targets[row.row_number] = combo
            self.source_table.setCellWidget(index, 7, combo)

    @staticmethod
    def _destination(change: RegistryChange) -> str:
        if change.kind == SUPPLY:
            return f"Поставщик: {change.counterparty_name}"
        if change.kind == SALE:
            return f"Покупатель: {change.counterparty_name}"
        if change.kind == REPORT:
            return f"Отчет ККМ · строка {int(change.slot) + 1}"
        return "Возврат покупателя"

    @staticmethod
    def _explanation(change: RegistryChange) -> str:
        parts = []
        if change.archived:
            parts.append("контрагент в архиве, в недельной таблице он скрыт")
        if change.kind == SALE:
            parts.append("смешанные статусы: вся сумма в работе" if change.mixed_status else
                         ("выписано (зелёная)" if change.invoiced else "в работе (красная)"))
        if change.kind == SUPPLY:
            parts.append(f"срок новых поставок {change.due_date}; "
                         "срок совпавшего документа сохраняется")
        if change.missing_previous:
            parts.append(f"в файле отсутствуют ранее загруженные документы: {change.missing_previous}")
        if change.existing_details:
            parts.append("было: " + " | ".join(change.existing_details))
        return "; ".join(parts)

    def _rebuild_plan(self) -> None:
        assignments = {number: str(combo.currentData() or "")
                       for number, combo in self.targets.items()}
        self.plan = plan_registry(self.registry, self.store.data,
                                  self.snapshot.base_key, assignments)
        self.plan_table.setRowCount(len(self.plan.changes))
        for index, change in enumerate(self.plan.changes):
            old = "—" if change.existing_amount is None else (
                f"{fmt_money_plain(change.existing_amount)} ({change.existing_count} зап.)"
            )
            action = {
                "new": "Новая запись",
                "replace": "Заменить прежнее",
                "same": "Уже совпадает",
            }[change.action]
            details = self._explanation(change)
            values = (
                fmt_date(date.fromisoformat(change.day)), self._destination(change),
                ", ".join(row.number for row in change.rows), old,
                fmt_money_plain(change.amount), action, details,
            )
            for column, value in enumerate(values):
                self._cell(self.plan_table, index, column, value, value,
                           bool(change.missing_previous) or (change.action == "replace" and column == 5))
        self.errors.setPlainText("\n".join(self.plan.issues))
        self.errors.setVisible(bool(self.plan.issues))
        self._refresh_enabled()

    def _refresh_enabled(self, *_args) -> None:
        self.import_button.setEnabled(bool(
            self.confirm_base.isChecked() and self.plan is not None
            and not self.plan.issues and self.plan.changes
        ))

    def _import(self) -> None:
        # Кнопку можно вызвать и программно: те же проверки обязательны здесь.
        self._rebuild_plan()
        if not self.confirm_base.isChecked() or self.plan is None or self.plan.issues:
            QMessageBox.warning(self, "Загрузка реестра", "Проверьте базу и исправьте ошибки предпросмотра.")
            return
        if self.plan.has_changes:
            replacements = sum(change.action == "replace" for change in self.plan.changes)
            missing = sum(change.missing_previous for change in self.plan.changes)
            archived = sum(change.archived for change in self.plan.changes)
            question = (
                f"Разнести документы в базу «{BASE_TITLES[self.snapshot.base_key]}»?\n\n"
                f"Строк назначения: {len(self.plan.changes)}; с заменой прежних сумм: {replacements}.\n"
                "Для совпавших дат старые операции этого вида могут быть удалены вместе "
                "с документами, сроками и комментариями. В файле должны быть все "
                "документы каждого загружаемого дня. Отсутствующие дни и строки "
                "ККМ без документа не очищаются.\n"
                "Перед записью программа создаст копию текущей базы."
            )
            if missing:
                question += f"\n\nВнимание: ранее загруженных документов, отсутствующих в этом файле: {missing}."
            if archived:
                question += f"\n\nНазначений на архивные карточки: {archived}; в недельных таблицах они скрыты."
            answer = QMessageBox.question(
                self, "Подтвердите замену", question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            result = commit_registry_plan(self.store, self.snapshot, self.plan, self.guard)
        except RegistryError as exc:
            QMessageBox.warning(self, "Загрузка реестра", str(exc))
            return
        self.changed = result.saved
        if result.saved and self.on_imported is not None:
            try:
                self.on_imported()
            except Exception:
                # Файл уже сохранён: нельзя сообщать, будто записи не было.
                result = type(result)(result.saved, result.backups, True)
        if result.saved:
            text = "Реестр разнесён. Копии базы до загрузки:\n" + "\n".join(map(str, result.backups))
            if result.refresh_failed:
                text += "\n\nДанные сохранены, но некоторые окна не обновились; откройте их заново."
        else:
            text = "Данные уже совпадают с реестром. Повторная запись не потребовалась."
        QMessageBox.information(self, "Загрузка реестра", text)
        self.accept()
