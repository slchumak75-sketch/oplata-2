"""Окно «Разнести выписку» — таблица с колонкой «Куда» (v0.56.0).

Перед записью показываем, куда попадёт каждая строка выписки (примечание
считается заново — база наполняется постепенно), и даём поправить вручную.
Строки, которым не хватило вида оплаты, остаются помеченными: их можно
создать карточкой прямо из этой таблицы.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import styles
from ..dates import fmt_money_plain
from ..statement import Statement
from ..statement_match import (
    OTHER_INCOME_ROW_TITLE,
    find_by_unp,
    find_payment_by_unp,
    TARGET_CARD,
    TARGET_CASH,
    TARGET_CUSTOMERS,
    TARGET_NEW_PAYMENT,
    TARGET_OTHER_INCOME,
    TARGET_OTHER_PAYMENT,
    TARGET_SUPPLIERS,
    Match,
    classify,
    distribute,
)
from ..widgets import green_button

COLUMNS = ("Дата", "Наименование", "Сумма", "Куда", "Примечание")
COL_DATE, COL_NAME, COL_AMOUNT, COL_TARGET, COL_NOTE = range(5)

# Что можно выбрать в колонке «Куда»: подпись → ключ строки блока.
TARGET_CHOICES = (
    ("Поступление по картам", TARGET_CARD),
    ("Поступление наличными", TARGET_CASH),
    ("Покупатели", TARGET_CUSTOMERS),
    ("Поставщики", TARGET_SUPPLIERS),
    ("Прочие поступления", TARGET_OTHER_INCOME),
)


class DistributionDialog(QDialog):
    """Таблица со сменой назначения и кнопкой «Разнести»."""

    def __init__(
        self,
        statement: Statement,
        store,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.statement = statement
        self.store = store
        self.result_distribution = None
        # Примечания считаются заново: с момента открытия файла в базе
        # могли появиться поставщики и покупатели.
        self.matches: list[Match] = [
            classify(row, store.data, store.data.bank.unp)
            for row in statement.rows
        ]
        self.setWindowTitle("Разнести выписку")
        self.setModal(True)
        self.resize(980, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        root.addWidget(QLabel("🧾  Разнесение выписки"))
        root.addWidget(
            QLabel(
                "Проверьте, куда попадёт каждая строка. Назначение можно "
                "изменить в колонке «Куда».\n"
                "Дата записи — фактическая дата операции из выписки."
            )
        )

        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )  # горизонтального скролла в таблицах нет (правило проекта)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_TARGET, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Stretch)
        for column, width in ((COL_DATE, 110), (COL_AMOUNT, 130)):
            self.table.setColumnWidth(column, width)
        root.addWidget(self.table, stretch=1)

        self._fill_table()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        apply_btn = green_button("Разнести")
        apply_btn.clicked.connect(self._apply)
        buttons.addWidget(apply_btn)
        root.addLayout(buttons)

    # -------------------------------------------------------------- таблица
    def _fill_table(self) -> None:
        self.visible = [
            index
            for index, match in enumerate(self.matches)
            if match.target
        ]
        self.table.setRowCount(len(self.visible))
        for index, row_index in enumerate(self.visible):
            row = self.statement.rows[row_index]
            match = self.matches[row_index]
            self.table.setRowHeight(index, 26)
            self._cell(index, COL_DATE, row.date)
            self._cell(index, COL_NAME, row.name or row.unp)
            amount = self._cell(index, COL_AMOUNT, fmt_money_plain(row.amount))
            amount.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._target_cell(index, row_index, match)
            note = self._cell(index, COL_NOTE, match.note)
            if match.target == TARGET_NEW_PAYMENT:
                note.setForeground(QColor(styles.RED))

    def _cell(self, row: int, column: int, text: str) -> QTableWidgetItem:
        cell = QTableWidgetItem(text)
        self.table.setItem(row, column, cell)
        return cell

    def _target_cell(self, row: int, row_index: int, match: Match) -> None:
        combo = QComboBox()
        combo.addItem("— не разносить —", "")
        for label, key in TARGET_CHOICES:
            combo.addItem(label, key)
        # Свой вид оплаты и «нужен новый вид» показываем подписью.
        if match.target == TARGET_OTHER_PAYMENT:
            combo.insertItem(1, match.payment_name, match.target)
        elif match.target == TARGET_NEW_PAYMENT:
            combo.insertItem(1, "Новый вид оплаты…", match.target)
        index = combo.findData(match.target)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(
            lambda _value, r=row, i=row_index: self._target_changed(r, i)
        )
        self.table.setCellWidget(row, COL_TARGET, combo)

    def _target_changed(self, row: int, row_index: int) -> None:
        """Ручная правка назначения: примечание подстраиваем под выбор."""

        combo = self.table.cellWidget(row, COL_TARGET)
        target = combo.currentData() if combo is not None else ""
        row_data = self.statement.rows[row_index]
        match = self.matches[row_index]
        match.target = target
        match.counterparty_id = ""
        match.payment_name = ""
        name = row_data.name or row_data.unp
        unp = row_data.unp
        if target == TARGET_CARD:
            match.note = "Поступление по картам"
        elif target == TARGET_CASH:
            match.note = "Поступление наличными"
        elif target == TARGET_CUSTOMERS:
            customer = find_by_unp(self.store.data.customers, unp)
            match.counterparty_id = customer.id if customer else ""
            match.note = (
                f"Покупатель: {customer.name}"
                if customer
                else f"Покупатель с УНП {unp} не найден"
            )
        elif target == TARGET_SUPPLIERS:
            supplier = find_by_unp(self.store.data.suppliers, unp)
            match.counterparty_id = supplier.id if supplier else ""
            match.note = (
                f"Поставщик: {supplier.name}"
                if supplier
                else f"Поставщик с УНП {unp} не найден"
            )
        elif target == TARGET_OTHER_INCOME:
            match.note = OTHER_INCOME_ROW_TITLE
        elif target == TARGET_OTHER_PAYMENT:
            payment = find_payment_by_unp(self.store.data, unp)
            match.payment_name = payment.name if payment else ""
            match.note = (
                f"Вид оплаты: {payment.name}"
                if payment
                else f"Вид оплаты с УНП {unp} не найден"
            )
        else:
            match.note = f"Пропустить строку ({name})"
        cell = self.table.item(row, COL_NOTE)
        if cell is not None:
            cell.setText(match.note)

    # --------------------------------------------------------------- запись
    def _apply(self) -> None:
        def blocked(match: Match) -> bool:
            if match.target == TARGET_NEW_PAYMENT:
                return True
            if match.target in (TARGET_CUSTOMERS, TARGET_SUPPLIERS):
                return not match.counterparty_id
            if match.target == TARGET_OTHER_PAYMENT:
                return not match.payment_name
            return False

        unresolved = [
            row
            for row, match in zip(self.statement.rows, self.matches)
            if blocked(match)
        ]
        if unresolved:
            names = "\n".join(
                f"• {row.name or row.unp} (УНП {row.unp})"
                for row in unresolved[:6]
            )
            answer = QMessageBox.question(
                self,
                "Не хватает данных",
                "Для этих строк в базе нет контрагента или вида оплаты "
                f"с таким УНП:\n{names}\n\n"
                "Создайте карточки в «Конфигурации» и нажмите «Обновить» "
                "в окне выписки.\n\n"
                "Разнести остальные строки?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        # Строки без назначения и без вида оплаты не разносим; ряды и
        # назначения держим парами, иначе примечания «поедут».
        kept = [
            (row, match)
            for row, match in zip(self.statement.rows, self.matches)
            if match.target and not blocked(match)
        ]
        statement = replace(self.statement, rows=[row for row, _ in kept])
        matches = [match for _, match in kept]
        self.result_distribution = distribute(
            statement, self.store.data, matches
        )
        self.accept()
