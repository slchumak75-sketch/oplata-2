"""Предпросмотр и подтверждение загрузки реестра документов."""

from __future__ import annotations

from collections.abc import Callable
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..dates import fmt_money_plain
from ..models import alphabetical_key
from ..registry import (
    REPORT,
    RETURN,
    SALE,
    SUPPLY,
    Registry,
    RegistryChange,
    RegistryError,
    RegistrySnapshot,
    auto_assignments,
    _is_revaluation,
    commit_registry_plan,
    plan_registry,
)
from ..storage import BASE_TITLES, DataStore
from ..widgets import blue_button

SOURCE_HEADERS = ("Дата", "Документ", "Статус", "Сумма", "Контрагент", "УНП", "Примечание")
CHECK_COLUMN = 6
# Индексы столбцов исходной книги, сохранённых в RegistryPreviewRow.
SOURCE_COLUMNS = (1, 2, 5, 6, 8, 9)



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
        self.preview_rows = tuple(row for row in registry.preview_rows
                                  if not _is_revaluation(row.values[2]))
        self.source_table = QTableWidget(0, len(SOURCE_HEADERS))
        self._configure_table(self.source_table, SOURCE_HEADERS)
        for column, width in enumerate((105, 205, 105, 105, 235, 105, 280)):
            self.source_table.setColumnWidth(column, width)
        self.source_table.cellDoubleClicked.connect(self._choose_counterparty)
        root.addWidget(self.source_table, stretch=1)
        self._initial_scroll_done = False

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
        relevant = {row.row_number: row for row in self.registry.rows}
        self._preview_indexes = {}
        self.source_table.setRowCount(len(self.preview_rows))
        for index, source in enumerate(self.preview_rows):
            self._preview_indexes[source.row_number] = index
            row = relevant.get(source.row_number)
            for column, source_column in enumerate(SOURCE_COLUMNS):
                value = source.values[source_column]
                self._cell(self.source_table, index, column, value, value)
            if row is None:
                continue
            if row.kind not in (SUPPLY, SALE):
                continue
            items = self.store.data.suppliers if row.kind == SUPPLY else self.store.data.customers
            # Скрытый выбор хранит назначение; в таблице лишь семь запрошенных
            # столбцов. Карточку можно выбрать двойным щелчком по строке.
            combo = QComboBox(self)
            combo.hide()
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

    def _choose_counterparty(self, row_index: int, _column: int) -> None:
        if not 0 <= row_index < len(self.preview_rows):
            return
        combo = self.targets.get(self.preview_rows[row_index].row_number)
        if combo is None or combo.count() <= 1:
            return
        # Дополнительный номер различает одинаковые подписи карточек.
        choices = [f"{i}. {combo.itemText(i)}" for i in range(1, combo.count())]
        current = max(0, combo.currentIndex() - 1)
        choice, accepted = QInputDialog.getItem(
            self, "Выбор карточки", "Выберите карточку контрагента:",
            choices, current, False,
        )
        if accepted and choice in choices:
            combo.setCurrentIndex(choices.index(choice) + 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initial_scroll_done:
            self._initial_scroll_done = True
            QTimer.singleShot(0, self._scroll_to_latest)

    def _scroll_to_latest(self) -> None:
        dated = [(index, row.day) for index, row in enumerate(self.preview_rows)
                 if row.day is not None]
        if not dated:
            return
        latest = max(day for _, day in dated)
        indices = [index for index, day in dated if day == latest]
        # Порядок файла сохраняется. Для обычного возрастающего реестра
        # последние даты внизу и предыдущие видны при прокрутке вверх.
        ascending = dated[0][1] <= dated[-1][1]
        index = indices[-1] if ascending else indices[0]
        hint = (QAbstractItemView.ScrollHint.PositionAtBottom if ascending
                else QAbstractItemView.ScrollHint.PositionAtTop)
        self.source_table.scrollToItem(self.source_table.item(index, 1), hint)

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
        row_messages: dict[int, list[str]] = {number: [] for number in self._preview_indexes}
        global_issues = []
        relevant = {row.row_number: row for row in self.registry.rows}
        for issue in self.plan.issues:
            numbers = []
            match = re.match(r"Строка (\d+):", issue)
            duplicate = re.search(r"повторяется в строках ([\d, ]+)", issue)
            if match:
                numbers = [int(match.group(1))]
            elif duplicate:
                numbers = [int(number) for number in re.findall(r"\d+", duplicate.group(1))]
            else:
                day = re.match(r"(\d{2}\.\d{2}\.\d{4}):", issue)
                if day:
                    numbers = [row.row_number for row in relevant.values()
                               if row.day and row.day.strftime("%d.%m.%Y") == day.group(1)
                               and ("отчётов ККМ больше" not in issue or row.kind == REPORT)]
            if not any(number in row_messages for number in numbers):
                global_issues.append(issue)
            for number in numbers:
                if number in row_messages:
                    row_messages[number].append(issue)

        change_by_row = {row.row_number: change for change in self.plan.changes
                         for row in change.rows}
        for source in self.preview_rows:
            index = self._preview_indexes[source.row_number]
            messages = row_messages[source.row_number]
            change = change_by_row.get(source.row_number)
            detail = ""
            if change is not None:
                detail = self._explanation(change)
                if change.action == "replace":
                    old = fmt_money_plain(change.existing_amount)
                    detail = f"Замена: было {old}, станет {fmt_money_plain(change.amount)}. " + detail
                if change.archived or change.missing_previous:
                    messages.append(detail)
            combo = self.targets.get(source.row_number)
            if combo is not None and not combo.currentData():
                messages.append("Дважды нажмите на строку и выберите карточку контрагента."
                                if combo.count() > 1 else
                                "Нет карточки: создайте её и откройте реестр заново.")
            message = "\n".join(dict.fromkeys(messages))
            replacement = ""
            if change is not None and change.action == "replace":
                replacement = (f"Замена: было {fmt_money_plain(change.existing_amount)}, "
                               f"станет {fmt_money_plain(change.amount)}"
                               + (" (сумма документов)" if len(change.rows) > 1 else ""))
            note = "\n".join(part for part in (message, replacement) if part)
            if not note and source.row_number not in relevant:
                note = "Не загружается"
            if combo is not None and combo.currentData():
                detail = f"Карточка: {combo.currentText()}. " + detail
            self._cell(self.source_table, index, CHECK_COLUMN, note, message or detail)
            for column in range(self.source_table.columnCount()):
                item = self.source_table.item(index, column)
                if item is not None:
                    item.setBackground(QColor("#ffe0e0") if message else QColor(Qt.GlobalColor.transparent))
                    item.setForeground(QColor("#a22525") if message else QColor("#202020"))
                    original = (source.values[SOURCE_COLUMNS[column]] if column < len(SOURCE_COLUMNS)
                                else "")
                    item.setToolTip("\n".join(part for part in (original, message or detail) if part))
        self.errors.setPlainText("\n".join(global_issues))
        self.errors.setVisible(bool(global_issues))
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
