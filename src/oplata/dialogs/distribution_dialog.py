"""Окно «Разнести выписку» — таблица с колонками «Куда» и «Уже в базе».

Перед записью показываем, куда попадёт каждая строка выписки (примечание
считается заново — база наполняется постепенно), и даём поправить
назначение вручную.

v0.57.0:
* колонка **«Уже в базе»** — сколько по этому назначению уже записано
  на дату операции, и что станет после разноса (запись дня одна, поэтому
  прежняя сумма заменяется — расхождение видно до записи);
* строки, которым не хватило вида оплаты, создают его **карточкой прямо
  здесь**: УНП и название подставляются из строки выписки.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
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
from ..models import OUTGOING, add_other_payment, other_payment_names
from ..statement import Statement
from ..statement_match import (
    OTHER_INCOME_ROW_TITLE,
    base_amount,
    classify_commission,
    find_by_unp,
    find_payment_by_unp,
    iso_date,
    plan_key,
    plan_totals,
    row_is_blocked,
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

COLUMNS = (
    "Дата",
    "Наименование",
    "Сумма",
    "Уже в базе",
    "Куда",
    "Примечание",
)
COL_DATE, COL_NAME, COL_AMOUNT, COL_BASE, COL_TARGET, COL_NOTE = range(6)

# Пункт списка «Куда» для строки, которой не хватило вида оплаты.
NEW_PAYMENT_CHOICE = "Новый вид оплаты…"

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
        # Страховка от повторного входа: список «Куда» может дёрнуть
        # создание карточки, пока карточка уже открыта.
        self._creating_payment = False
        # Примечания считаются заново: с момента открытия файла в базе
        # могли появиться поставщики и покупатели.
        self.matches: list[Match] = [
            classify(row, store.data, store.data.bank.unp)
            for row in statement.rows
        ]
        # Комиссия банка по строке — отдельная запись в оплатах.
        self.commissions: list[Match] = [
            classify_commission(row, store.data, store.data.bank.unp)
            for row in statement.rows
        ]
        # Сколько запишем по каждой записи дня: строки одного дня и
        # одного назначения складываются (запись дня в базе одна).
        self.planned: dict[tuple, Decimal] = plan_totals(
            statement, store.data, self.matches, self.commissions
        )
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
                "Дата записи — фактическая дата операции из выписки. "
                "Колонка «Уже в базе» показывает, что за этот день "
                "записано сейчас и что станет после разноса."
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
        for column, width in ((COL_DATE, 100), (COL_AMOUNT, 115),
                              (COL_BASE, 150)):
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
            self._base_cell(index, row, match)
            self._target_cell(index, row_index, match)
            commission = self.commissions[row_index]
            text = match.note
            if commission.note:
                text = f"{text} · {commission.note}"
            note = self._cell(index, COL_NOTE, text)
            if row_is_blocked(match, commission):
                note.setForeground(QColor(styles.RED))

    def _base_cell(self, index: int, row, match: Match) -> None:
        """«Уже в базе»: что записано за день и что станет (v0.57.0).

        Запись дня в базе одна, поэтому прежняя сумма **заменяется** —
        об этом пишем и в ячейке, и в подсказке.
        """

        date_iso = iso_date(row)
        current = base_amount(self.store.data, match, date_iso)
        result = self.planned.get(plan_key(row, match), Decimal("0"))
        if match.target == TARGET_NEW_PAYMENT:
            text = "—"  # вид оплаты ещё не создан, писать некуда
        elif current == result:
            text = fmt_money_plain(current)
        else:
            text = f"{fmt_money_plain(current)} → {fmt_money_plain(result)}"
        cell = self._cell(index, COL_BASE, text)
        cell.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        if match.target != TARGET_NEW_PAYMENT and current and current != result:
            # Расхождение: день в базе уже заполнен другой суммой.
            cell.setForeground(QColor(styles.RED))
        cell.setToolTip(
            f"За {date_iso} в базе: {fmt_money_plain(current)}.\n"
            f"После разноса станет: {fmt_money_plain(result)}\n"
            "Запись дня одна, прежняя сумма заменяется."
        )

    def _refresh_planned(self) -> None:
        """Пересчитать суммы и обновить колонку «Уже в базе»."""

        self.planned = plan_totals(
            self.statement, self.store.data, self.matches, self.commissions
        )
        for index, row_index in enumerate(self.visible):
            row = self.statement.rows[row_index]
            match = self.matches[row_index]
            self.table.takeItem(index, COL_BASE)
            self._base_cell(index, row, match)

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
            combo.insertItem(1, NEW_PAYMENT_CHOICE, match.target)
        index = combo.findData(match.target)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(
            lambda _value, r=row, i=row_index: self._target_changed(r, i)
        )
        # v0.57.0: рядом со списком — кнопка создания вида оплаты. Она
        # видна только тем строкам, которым вида не хватает.
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(combo, stretch=1)
        button = QPushButton("＋")
        button.setFixedWidth(30)
        button.setToolTip(
            "Создать вид оплаты: УНП и наименование возьмём из строки "
            "выписки"
        )
        button.clicked.connect(
            lambda _checked=False, r=row, i=row_index: self._create_payment(
                r, i
            )
        )
        layout.addWidget(button)
        self.table.setCellWidget(row, COL_TARGET, holder)
        self._sync_target_button(row, row_index)

    def _target_changed(self, row: int, row_index: int) -> None:
        """Ручная правка назначения: примечание подстраиваем под выбор."""

        combo = self._target_combo(row)
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
        self._sync_target_button(row, row_index)
        if combo is not None and combo.currentText() == NEW_PAYMENT_CHOICE:
            # Выбрали «Новый вид оплаты…» — сразу открываем карточку.
            self._create_payment(row, row_index)
            return
        self._refresh_planned()

    def _sync_target_button(self, row: int, row_index: int) -> None:
        """Показать кнопку «＋» строкам, которым не хватает вида оплаты."""

        holder = self.table.cellWidget(row, COL_TARGET)
        if holder is None:
            return
        button = holder.findChild(QPushButton)
        if button is None:
            return
        match = self.matches[row_index]
        commission = self.commissions[row_index]
        needed = (
            match.target == TARGET_NEW_PAYMENT
            or commission.target == TARGET_NEW_PAYMENT
            or (match.target == TARGET_OTHER_PAYMENT and not match.payment_name)
        )
        button.setVisible(needed)

    # ------------------------------------------- карточка вида оплаты
    def _create_payment(self, row: int, row_index: int) -> bool:
        """Создать вид оплаты карточкой (v0.57.0).

        УНП и наименование — из строки выписки: пользователь наполняет
        базу по ходу разбора файла, и уходить в «Конфигурацию» незачем.
        """

        if self._creating_payment:  # повторный вход из списка «Куда»
            return False
        from ..views.receipts_view import OtherPaymentCardDialog

        self._creating_payment = True
        try:
            return self._create_payment_card(row, row_index)
        finally:
            self._creating_payment = False

    def _create_payment_card(self, row: int, row_index: int) -> bool:
        """Сама карточка: поля, сохранение, обновление строки."""

        from ..views.receipts_view import OtherPaymentCardDialog

        row_data = self.statement.rows[row_index]
        used = set(other_payment_names(self.store.data, OUTGOING))
        day = date.fromisoformat(iso_date(row_data))
        # Имя из выписки: «ООО "ОМА"» → то же, но без лишних пробелов.
        suggested = " ".join((row_data.name or row_data.unp).split())
        dialog = OtherPaymentCardDialog(
            OUTGOING,
            used,
            day,
            self,
            name=suggested,
            unp=row_data.unp,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        name = dialog.name_value
        add_other_payment(
            self.store.data,
            name,
            day.isoformat(),
            OUTGOING,
            dates=list(dialog.marked_dates),
            vat_rate=dialog.vat_value,
            unp=dialog.unp_value,
        )
        self.store.save()
        match = self.matches[row_index]
        if match.target == TARGET_NEW_PAYMENT:
            match.target = TARGET_OTHER_PAYMENT
        match.payment_name = name
        match.counterparty_id = ""
        match.note = f"Вид оплаты: {name}"
        self._set_target_text(row, name, TARGET_OTHER_PAYMENT)
        # Комиссия банка по строке теперь тоже знает, куда писать.
        self.commissions[row_index] = classify_commission(
            self.statement.rows[row_index],
            self.store.data,
            self.store.data.bank.unp,
        )
        note = self.table.item(row, COL_NOTE)
        if note is not None:
            commission = self.commissions[row_index]
            note.setText(
                f"{match.note} · {commission.note}" if commission.note
                else match.note
            )
            # Пустая кисть возвращает цвет по умолчанию: строка больше
            # не «неизвестная», вид оплаты для неё создан.
            note.setForeground(QBrush())
        self._refresh_planned()
        return True

    def _set_target_text(self, row: int, text: str, data: str) -> None:
        """Выбрать в списке «Куда» пункт с нужным значением."""

        combo = self._target_combo(row)
        if combo is None:
            return
        # Сигналы гасим на всё время правки: вставка пункта сама по себе
        # меняет текущий индекс и иначе дёрнула бы _target_changed.
        combo.blockSignals(True)
        try:
            index = combo.findData(data)
            if index < 0:
                combo.insertItem(1, text, data)
                index = combo.findData(data)
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _target_combo(self, row: int) -> QComboBox | None:
        widget = self.table.cellWidget(row, COL_TARGET)
        if widget is None:
            return None
        if isinstance(widget, QComboBox):
            return widget
        return widget.findChild(QComboBox)

    # --------------------------------------------------------------- запись
    def _apply(self) -> None:
        def blocked(match: Match, commission: Match) -> bool:
            return row_is_blocked(match, commission)

        unresolved = [
            row
            for row, match, commission in zip(
                self.statement.rows, self.matches, self.commissions
            )
            if blocked(match, commission)
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
            (row, match, commission)
            for row, match, commission in zip(
                self.statement.rows, self.matches, self.commissions
            )
            if match.target and not blocked(match, commission)
        ]
        statement = replace(self.statement, rows=[row for row, _a, _b in kept])
        matches = [match for _row, match, _commission in kept]
        commissions = [commission for _row, _match, commission in kept]
        self.result_distribution = distribute(
            statement, self.store.data, matches, commissions
        )
        self.accept()
