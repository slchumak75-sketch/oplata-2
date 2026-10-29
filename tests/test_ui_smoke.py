"""Smoke-тесты интерфейса (headless, QT_QPA_PLATFORM=offscreen в CI).

Проверяем построение окна и главные пользовательские сценарии:
ввод оплаты, поставку и долг, блоки «Поступление»/«Оплата», CRUD
операций в окне журнала контрагента, карточки.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFileDialog,
    QMessageBox,
)


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def store(tmp_path):
    from oplata.storage import DataStore

    return DataStore(tmp_path / "data.json")


def patch_other_payment_card(monkeypatch, names=()):
    """Подменить карточку нового вида оплаты (v0.20.0).

    Сама карточка — модальный диалог: в headless-прогоне ``exec()``
    задел бы цикл событий и тест повис. Подмена возвращает имя из
    очереди (а дальше — как прежде: «Новый вид оплаты», «… 2», …),
    дату блока и нулевую сумму.
    """
    from decimal import Decimal

    from PySide6.QtWidgets import QDialog

    from oplata.views import receipts_view as receipts_view_module

    queue = list(names)

    class FakeCard:
        """Карточка без показа: имя из очереди, дата и сумма не нужны."""

        def __init__(self, direction, used_names, day, parent=None):
            if queue:
                self._name = queue.pop(0)
            else:
                base = "Новый вид оплаты"
                name = base
                index = 2
                while name in used_names:
                    name = f"{base} {index}"
                    index += 1
                self._name = name

        def exec(self):
            return QDialog.DialogCode.Accepted

        @property
        def name_value(self):
            return self._name

        @property
        def marked_dates(self):
            """Календаря в подменной карточке нет — дат не отмечаем."""
            return []

    monkeypatch.setattr(receipts_view_module, "OtherPaymentCardDialog", FakeCard)


def test_main_window_all_pages(qt_app, store):
    from oplata.app import MainWindow

    window = MainWindow(store)
    assert [button.text() for button in window.nav_buttons] == [
        "Поступление и оплаты",
        "Поставщики",
        "Покупатели",
        "Конфигурация",  # v0.14.0: прежняя вкладка «Данные»
    ]
    assert window.stack.count() == 4  # вкладки «Сводка» больше нет
    # Вкладка «Поступление и оплаты»: два блока один под другим, в каждом
    # таблица со столбцами «Сумма/Неделя/Месяц/Год».
    view = window.receipts_view
    assert [view.incoming.table.item(row, 0).text() for row in range(4)] == [
        "Поступление наличными",
        "Поступление по картам",
        "Покупатели",
        "Прочие поступления",  # постоянная строка, v0.13.0
    ]
    assert view.outgoing.table.item(0, 0).text() == "Поставщики"
    # v0.22.0: вместо «Неделя/Месяц/Квартал/Год» — колонки дней недели.
    assert [
        view.incoming.table.horizontalHeaderItem(c).text()
        for c in range(COLUMN_COUNT)
    ][:2] == ["", "Сумма"]
    assert view.incoming.days == view.incoming.week_days()
    assert len(view.incoming.days) == TABLE_DAYS
    # Кнопок «+»/«−» в блоке «Поступление» нет, в «Оплате» — есть.
    assert not hasattr(view.incoming, "add_btn")
    assert view.outgoing.add_btn.text() == "+"
    assert view.outgoing.remove_btn.text() == "−"
    # Блок «Оплата» стоит под блоком «Поступление» (в левой колонке).
    layout = view.left_layout
    assert layout.indexOf(view.outgoing) > layout.indexOf(view.incoming)
    # «Сводка» — панель справа на этой же вкладке, отдельной вкладки нет.
    assert view.summary_widget is window.summary_view
    assert window.stack.indexOf(window.summary_view) == -1
    # Кнопки карточек перенесены из шапки в панели таблиц.
    assert window.suppliers_view.card_button.text() == "Карточка поставщика"
    assert window.customers_view.card_button.text() == "Карточка покупателя"
    assert not hasattr(window, "operations_view")
    for index in range(4):
        # v0.18.0: «Конфигурация» открывается по паролю — в тесте
        # считаем, что пароль уже введён (иначе встанет модальный диалог).
        window._config_unlocked = True  # noqa: SLF001 - доступ к флагу окна
        window.show_page(index)
        assert window.stack.currentIndex() == index
    # Сводка — построчная плашка из трёх блоков (v0.14.0)
    summary = window.summary_view
    assert summary.caption_text("funds") == "Остаток на счёте"
    assert summary.caption_text("debt_total") == "Всего задолженность"
    assert summary.caption_text("month_income") == "Приход с начала месяца"
    assert summary.caption_text("plan_1").startswith("Оплата ")
    # В покупателях справа «0»
    assert window.customers_view.right_value_label.text() == "0"
    window.close()


def test_week_table_income_and_payment_input(qt_app, store):
    """Прямой ввод; салатовая только ячейка даты, выходные — розовые."""
    from datetime import date

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyleOptionViewItem

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.views.week_view import FIRST_DATA_ROW, TOTAL_ROW
    from oplata.widgets import AmountLineEdit

    window = MainWindow(store)
    view = window.suppliers_view
    view.goto_date(date(2026, 10, 26))
    holiday = date(2026, 10, 30)       # помеченный в «Данных» праздник в пятницу
    saturday = date(2026, 10, 31)       # помеченный рабочий перенос
    today = date(2026, 10, 27)
    store.data.day_overrides.update(
        {
            holiday.isoformat(): "off",
            saturday.isoformat(): "work",
            today.isoformat(): "off",  # даже так красим только поле «27 вт»
        }
    )
    view._rebuild_table(view._partners, view._days, today)
    table = view.table
    # Номера колонок дней считаем от числа итоговых столбцов: их три
    # («на сегодня», «за неделю», «просрочено», v0.16.0).
    income = table.item(FIRST_DATA_ROW, _DAY(view, 0, 0))    # пн, Приход
    debt = table.item(FIRST_DATA_ROW, _DAY(view, 0, 1))      # пн, Долг
    payment = table.item(FIRST_DATA_ROW, _DAY(view, 0, 2))   # пн, Оплата
    assert income.flags() & Qt.ItemFlag.ItemIsEditable
    assert payment.flags() & Qt.ItemFlag.ItemIsEditable
    assert not (debt.flags() & Qt.ItemFlag.ItemIsEditable)
    editor = table.itemDelegate().createEditor(
        table.viewport(),
        QStyleOptionViewItem(),
        table.model().index(FIRST_DATA_ROW, _DAY(view, 0, 0)),
    )
    assert isinstance(editor, AmountLineEdit)  # нули и в редакторе ячейки не мешают
    editor.deleteLater()

    def bg_name(row, col):
        brush = table.item(row, col).background()
        return None if brush.style() == Qt.BrushStyle.NoBrush else brush.color().name()

    assert bg_name(1, _DAY(view, 1, 0)) == styles.WEEK_TABLE_TODAY_GREEN  # только «27 вт»
    assert bg_name(2, _DAY(view, 1, 0)) is None
    assert bg_name(TOTAL_ROW, _DAY(view, 1, 0)) is None
    assert bg_name(FIRST_DATA_ROW, _DAY(view, 1, 0)) is None
    assert bg_name(1, _DAY(view, 4, 0)) == styles.WEEK_TABLE_WEEKEND_PINK  # праздник
    assert bg_name(FIRST_DATA_ROW, _DAY(view, 4, 0)) == styles.WEEK_TABLE_WEEKEND_PINK
    assert bg_name(FIRST_DATA_ROW, _DAY(view, 5, 0)) is None  # рабочая суббота
    # v0.18.0: воскресенье из таблицы убрали — колонок дней шесть.
    assert view.table_days == 6
    assert table.columnCount() == 1 + view.lead_columns + 6 * view.columns_per_day
    assert bg_name(FIRST_DATA_ROW, 3) is None  # обычный понедельник без заливки
    assert view.date_edit.minimumWidth() >= 138  # год не под стрелкой календаря
    assert view.date_edit.calendarPopup()
    # Кастомный drop-down в QSS скрывал нативную стрелку на Windows.
    assert "QDateEdit::drop-down" not in styles.APP_STYLESHEET

    monday = view._days[0]
    income.setText("300")  # программная правка = тот же сигнал, что от Enter
    assert [(op.kind, op.date, op.due_date, op.amount) for op in store.data.operations] == [
        ("Приход", monday.isoformat(), monday.isoformat(), "300.00")
    ]
    assert income.text() == "300,00"
    assert debt.text() == "300,00"  # у первого поставщика отсрочка 0
    assert table.item(TOTAL_ROW, _DAY(view, 0, 0)).text() == "300,00"

    payment.setText("200")
    assert [op.kind for op in store.data.operations] == ["Приход", "Оплата"]
    assert payment.text() == "200,00"  # переформатировано после сохранения
    assert table.item(TOTAL_ROW, _DAY(view, 0, 2)).text() == "200,00"  # ВСЕГО
    # Блок «Оплата» вкладки «Поступление и оплаты» видит итог этого дня.
    window.receipts_view.outgoing.set_date(monday)
    assert window.receipts_view.outgoing.amount_text("suppliers") == "200,00"

    payment.setText("")  # очистка ячейки удаляет только операцию оплаты
    assert [op.kind for op in store.data.operations] == ["Приход"]
    income.setText("0")
    assert store.data.operations == []
    window.close()


def test_delivery_dialog_saves_and_debt_shows_in_week(qt_app, store):
    """Окно «Поставка»: срок = дата + отсрочка, сумма встаёт в «Долг» на срок."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.dialogs.delivery_dialog import DeliveryDialog

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    assert supplier.deferment_days == 10  # сид с макета карточки
    dialog = DeliveryDialog(store, supplier.id, preset_date=date(2026, 10, 26))
    assert dialog.amount_edit.text() == ""  # нули не надо стирать перед вводом
    assert dialog.conditions_label.text() == "Условия: Отсрочка: 10 дн."
    assert dialog.due_label.text() == "05.11.2026"  # 26.10 + 10 дней
    dialog.amount_edit.setText("88 888,88")  # максимальное число от пользователя
    dialog._save()
    assert dialog.saved_due_date == date(2026, 11, 5)
    (op,) = store.data.operations
    assert (op.kind, op.date, op.due_date, op.amount) == (
        "Приход", "2026-10-26", "2026-11-05", "88888.88",
    )
    dialog.close()

    # Предоплата (как на скрине «eee»): срок = дата поставки
    eee = next(s for s in store.data.suppliers if s.name == "eee")
    eee.prepayment = True
    prepaid = DeliveryDialog(store, eee.id, preset_date=date(2026, 10, 27))
    assert prepaid.conditions_label.text() == "Условия: Предоплата"
    assert prepaid.due_label.text() == "27.10.2026"
    assert "отсрочка не применяется" in prepaid.due_reason_label.text()
    prepaid.close()

    # Таблица показывает долг в неделе срока: 05.11 — чт (день №3 от пн 02.11)
    window = MainWindow(store)
    window.suppliers_view.goto_date(date(2026, 11, 5))
    table = window.suppliers_view.table
    ggg_row = 4 + [s.name for s in store.data.suppliers].index("ггг")
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "88 888,88"  # Долг чт
    assert table.item(ggg_row, 3).text() == "88 888,88"  # задолженность за неделю
    assert table.item(3, 3).text() == "88 888,88"  # ВСЕГО за неделю
    assert window.suppliers_view.right_value_label.text() == "88 888,88 руб."

    # Оплата НЕ убирает сумму из «Долга» (требование пользователя
    # v0.3.1: остаётся для архива), но уменьшает «за неделю» (v0.15.0:
    # оплата вычитается из «за неделю», а если есть просроченная
    # задолженность — приоритетно из «на сегодня»).
    payment_cell = table.item(ggg_row, _DAY(window.suppliers_view, 3, 2))  # Оплата чт 05.11
    payment_cell.setText("88 888,88")
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "88 888,88"  # Долг на месте
    assert table.item(ggg_row, 3).text() == ""  # «за неделю» погашено
    assert table.item(3, 3).text() == ""  # ВСЕГО за неделю
    assert window.suppliers_view.right_value_label.text() == "0,00 руб."
    window.close()


def test_operation_dialog_and_journal_crud(qt_app, store):
    """Редактор поставки и CRUD оплаты в окне журнала контрагента."""
    from datetime import date

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.dialogs.operation_dialog import OperationDialog
    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    dialog = OperationDialog(store, initial_counterparty_id=supplier.id)
    assert dialog.amount_edit.text() == ""
    dialog.date_edit.setDate(QDate(2026, 10, 26))
    dialog.amount_edit.setText("1 000,50")
    dialog.document_edit.setText("Накладная 42")
    dialog.comment_edit.setText("первая поставка")
    assert dialog.due_label.text() == "05.11.2026"  # ггг: отсрочка 10 дней
    dialog._save()
    (op,) = store.data.operations
    assert (op.kind, op.date, op.due_date, op.amount) == (
        "Приход", "2026-10-26", "2026-11-05", "1000.50",
    )
    dialog.close()

    # Правка только суммы НЕ пересчитывает сохранённый срок, даже если
    # отсрочка поставщика уже изменилась.
    supplier.deferment_days = 30
    edit = OperationDialog(store, operation=op)
    edit.amount_edit.setText("1200")
    assert edit.due_label.text() == "05.11.2026"
    edit._save()
    assert op.amount == "1200.00" and op.due_date == "2026-11-05"
    edit.close()

    # А изменение даты поставки срок пересчитывает (27.10 + 30 дней).
    edit2 = OperationDialog(store, operation=op)
    edit2.date_edit.setDate(QDate(2026, 10, 27))
    assert edit2.due_label.text() == "26.11.2026"
    edit2._save()
    assert op.date == "2026-10-27" and op.due_date == "2026-11-26"
    assert op.document == "Накладная 42"  # документ/комментарий пережили правки
    edit2.close()

    # Отдельная оплата для экрана «Оплаты»: вкладка должна видеть только её,
    # а не поставку выше.
    payment_dialog = OperationDialog(
        store, initial_counterparty_id=supplier.id, initial_kind="Оплата"
    )
    assert payment_dialog.kind_combo.currentText() == "Оплата"
    assert payment_dialog.due_panel.isHidden()
    payment_dialog.date_edit.setDate(QDate(2026, 10, 27))
    payment_dialog.amount_edit.setText("200")
    payment_dialog._save()
    payment_op = next(item for item in store.data.operations if item.kind == "Оплата")
    payment_dialog.close()

    window = MainWindow(store)

    # v0.23.1: журнал из карточки — только список операций контрагента:
    # ни фильтров, ни кнопок «Добавить/Изменить/Удалить».
    dialog = OperationsJournalDialog(store, "supplier", counterparty_id=supplier.id)
    journal = dialog.view
    assert journal.supplier_filter is None
    assert journal.kind_filter is None
    assert journal.add_btn is None
    assert journal.edit_btn is None
    assert journal.delete_btn is None
    # Журнал поставщика показывает только поставки (v0.23.1).
    assert journal.table.rowCount() == 1
    assert [
        journal.table.horizontalHeaderItem(col).text() for col in range(4)
    ] == ["Дата\nпоставки", "Сумма", "Отсрочка,\nдней", "Дата\nоплаты"]

    # Полный журнал (с фильтрами и правкой) живёт отдельным виджетом —
    # его и проверяем на фильтры и удаление.
    from oplata.views.operations_view import OperationsView

    full = OperationsView(store)
    full.set_counterparty_filter(supplier.id)
    assert full.found_label.text() == "Найдено: 2"  # поставка + оплата
    full.kind_filter.setCurrentIndex(0)  # все типы, без фиксированного
    full.supplier_filter.setCurrentIndex(0)
    full._select_operation_row(payment_op.id)
    assert full.selected_operation_id() == payment_op.id
    assert full.edit_btn.isEnabled() and full.delete_btn.isEnabled()

    full.date_from.setText("28.10.2026")  # операции октября — вне диапазона
    assert full.found_label.text() == "Найдено: 0"
    full.reset_filters()
    assert full.found_label.text() == "Найдено: 2"
    journal = full

    # Блок «Оплата» показывает ту же оплату поставщику за 27.10.
    window.receipts_view.outgoing.set_date(date(2026, 10, 27))
    assert window.receipts_view.outgoing.amount_text("suppliers") == "200,00"

    # Поставка по-прежнему даёт долг на сроке 26.11 (чт). Удаление оплаты
    # из журнала его не меняет: оплата архивный долг пока не уменьшает.
    window.suppliers_view.goto_date(date(2026, 11, 26))
    week_table = window.suppliers_view.table
    ggg_row = 4 + [s.name for s in store.data.suppliers].index("ггг")
    assert week_table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "1 200,00"

    assert journal.delete_operation(payment_op.id)
    assert week_table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "1 200,00"
    assert [item.id for item in store.data.operations] == [op.id]
    assert journal.found_label.text() == "Найдено: 1"  # осталась поставка
    assert journal.selected_operation_id() == op.id  # выделение перешло на неё
    dialog.close()
    window.close()


def test_supplier_card_adds_counterparty(qt_app, store):
    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    dialog = CounterpartyCardDialog(store, "supplier")
    dialog.name_edit.setText("Новый поставщик")
    dialog.deferment_spin.setValue(14)
    dialog._add()
    names = [s.name for s in store.data.suppliers]
    assert "Новый поставщик" in names
    added = store.data.suppliers[-1]
    assert added.deferment_days == 14

    # Режим «Предоплата» отключает поле отсрочки (правило с макета карточки)
    dialog.chk_prepayment.setChecked(True)
    assert not dialog.deferment_spin.isEnabled()
    dialog.chk_prepayment.setChecked(False)
    assert dialog.deferment_spin.isEnabled()
    dialog.close()


def test_customer_card_has_no_supplier_payment_terms(qt_app, store):
    """В карточке покупателя нет отсрочки и режимов поставщика."""
    from PySide6.QtWidgets import QLabel

    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    dialog = CounterpartyCardDialog(store, "customer")
    assert dialog.deferment_spin is None
    assert dialog.chk_prepayment is None
    assert dialog.chk_realization is None
    label_texts = {label.text() for label in dialog.findChildren(QLabel)}
    assert "Отсрочка платежа, дней" not in label_texts
    assert "Режим оплаты" not in label_texts

    dialog.name_edit.setText("Новый покупатель")
    dialog._add()
    added = store.data.customers[-1]
    assert (added.deferment_days, added.prepayment, added.realization) == (
        0,
        False,
        False,
    )
    dialog.close()


def test_amount_edit_clears_zero_on_focus(qt_app):
    """Нулевое значение поля суммы не мешает начинать ввод."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtWidgets import QWidget

    from oplata.widgets import AmountLineEdit

    holder = QWidget()
    edit = AmountLineEdit("0,00", holder)
    assert edit.text() == ""  # начальные нули не показываются как текст

    edit.setText(",00")
    holder.show()
    edit.setFocus()
    qt_app.processEvents()
    assert edit.text() == ""

    edit.setText("123,45")
    edit.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    qt_app.processEvents()
    assert edit.selectedText() == "123,45"
    holder.close()


def test_data_calendar_marks_days_and_saves_explicitly(qt_app, store):
    """Календарь: режим → клики по дням → только кнопка «Сохранить» пишет файл."""
    from datetime import date

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import Operation
    from oplata.storage import DataStore

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    store.data.operations.append(
        Operation(
            kind="Приход",
            counterparty_id=supplier.id,
            date="2026-10-26",
            amount="700.00",
            due_date="",  # срок пересчитывается по текущему календарю
        )
    )
    window = MainWindow(store)
    calendar = window.data_view
    calendar.year = 2026
    calendar.refresh()

    monday = date(2026, 10, 26)
    saturday = date(2026, 10, 31)
    due_day = date(2026, 11, 5)  # 26.10 + отсрочка «ггг» 10 дней
    assert calendar.chk_off.isChecked()
    assert not calendar.save_button.isEnabled()

    calendar._on_day_clicked(monday)  # будний день -> выходной в черновике
    assert calendar._pending_overrides[monday.isoformat()] == "off"
    assert monday.isoformat() not in store.data.day_overrides  # ещё не сохранено
    assert calendar.has_unsaved_changes
    assert calendar.save_button.isEnabled()
    assert styles.WEEKEND_RED in calendar._day_buttons[monday].styleSheet()

    calendar.chk_work.setChecked(True)
    assert not calendar.chk_off.isChecked()  # режимы взаимоисключающие
    calendar._on_day_clicked(saturday)  # суббота -> рабочий перенос
    assert calendar._pending_overrides[saturday.isoformat()] == "work"
    assert styles.WORKDAY_GREY in calendar._day_buttons[saturday].styleSheet()

    calendar.discard_changes()
    assert calendar._pending_overrides == {}
    assert not calendar.has_unsaved_changes

    # Отмечаем день срока выходным и сохраняем: долг в недельной таблице
    # должен сдвинуться на следующий рабочий день без ручного refresh.
    window.suppliers_view.goto_date(due_day)
    table = window.suppliers_view.table
    ggg_row = 4 + [s.name for s in store.data.suppliers].index("ггг")
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "700,00"  # чт 05.11
    calendar.chk_off.setChecked(True)
    calendar._on_day_clicked(due_day)
    assert calendar.save_calendar()
    assert store.data.day_overrides[due_day.isoformat()] == "off"
    assert DataStore(store.path).data.day_overrides[due_day.isoformat()] == "off"
    assert calendar.status_label.text() == "Сохранено"
    assert not calendar.save_button.isEnabled()
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == ""
    assert table.item(ggg_row, _DAY(window.suppliers_view, 4, 1)).text() == "700,00"  # пт 06.11
    window.close()


def test_customer_sale_table_and_direct_input(qt_app, store):
    """Покупатели: «Реализация/Оплата», без «Долга» и без срока оплаты."""
    from datetime import date

    from PySide6.QtCore import Qt

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import Counterparty
    from oplata.views.week_view import FIRST_DATA_ROW, TOTAL_ROW

    customer = Counterparty(name="Покупатель А")
    store.data.customers.append(customer)
    window = MainWindow(store)
    view = window.customers_view
    monday = date(2026, 10, 26)
    today = date(2026, 10, 27)
    view.goto_date(monday)
    store.data.day_overrides[today.isoformat()] = "off"
    view._rebuild_table(view._partners, view._days[: view.table_days], today)
    table = view.table

    # v0.15.0: у покупателей нет столбца «просрочено» — 1 имя + 1 итог
    # («Всего»). v0.18.0: дней в таблице шесть, без воскресенья.
    assert table.columnCount() == 14
    assert table.item(1, 1).text() == "ЗАДОЛЖ."
    assert table.item(2, 1).text() == "Всего"
    assert table.item(2, 2).text() == "Реализация"
    assert table.item(2, 3).text() == "Оплата"
    assert all(
        table.item(2, col) is None or table.item(2, col).text() != "Долг"
        for col in range(table.columnCount())
    )

    def bg_name(row, col):
        brush = table.item(row, col).background()
        return None if brush.style() == Qt.BrushStyle.NoBrush else brush.color().name()

    # Только верхнее поле «27 вт» салатовое; ниже этот день не красим.
    assert bg_name(1, 2 + 1 * 2) == styles.WEEK_TABLE_TODAY_GREEN
    assert bg_name(2, 2 + 1 * 2) is None
    assert bg_name(TOTAL_ROW, 2 + 1 * 2) is None
    assert bg_name(FIRST_DATA_ROW, 2 + 1 * 2) is None
    # Суббота как обычный выходной остаётся розовой и в теле таблицы.
    assert bg_name(FIRST_DATA_ROW, 3 + 5 * 2) == styles.WEEK_TABLE_WEEKEND_PINK

    sale = table.item(FIRST_DATA_ROW, 2)
    sale.setText("500")
    (op,) = store.data.operations
    assert (op.counterparty_id, op.kind, op.date, op.amount, op.due_date) == (
        customer.id,
        "Реализация",
        monday.isoformat(),
        "500.00",
        "",
    )
    assert sale.text() == "500,00"
    window.close()


def test_realization_dialog_saves_without_due_date(qt_app, store):
    """Окно покупателя: «Реализация», дата реализации, без срока оплаты."""
    from datetime import date

    from PySide6.QtWidgets import QLabel

    from oplata.dialogs.realization_dialog import RealizationDialog
    from oplata.models import Counterparty

    customer = Counterparty(name="Покупатель Б")
    store.data.customers.append(customer)
    store.save()
    dialog = RealizationDialog(
        store, customer.id, preset_date=date(2026, 10, 27)
    )
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert dialog.windowTitle() == "Реализация"
    assert "Дата реализации *" in labels
    assert not any("СРОК ОПЛАТЫ" in text.upper() for text in labels)
    assert not hasattr(dialog, "due_label")

    dialog.amount_edit.setText("1 250,50")
    dialog._save()
    assert dialog.saved_date == date(2026, 10, 27)
    (op,) = store.data.operations
    assert (op.kind, op.counterparty_id, op.date, op.amount, op.due_date) == (
        "Реализация",
        customer.id,
        "2026-10-27",
        "1250.50",
        "",
    )
    dialog.close()


def test_receipts_table_direct_input_and_totals(qt_app, store):
    """Таблица «Поступление»: ввод в ячейку и суммы по дням недели."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_AMOUNT

    window = MainWindow(store)
    view = window.receipts_view
    incoming = view.incoming

    # Заголовок блока и строка даты идут в одну строчку.
    assert incoming.date_edit.calendarPopup()
    assert incoming.date_edit.minimumWidth() >= 138
    assert incoming.today_btn.text() == "Сегодня"
    assert incoming.prev_btn.text() == "←" and incoming.next_btn.text() == "→"
    assert incoming.date_edit.y() <= incoming.today_btn.y() + 4

    incoming.set_date(date(2026, 10, 27))
    incoming.next_btn.click()
    assert incoming.current_date() == date(2026, 10, 28)
    incoming.prev_btn.click()
    incoming.prev_btn.click()
    assert incoming.current_date() == date(2026, 10, 26)
    view.set_date(date(2026, 10, 27))

    # Наличные и карты вводятся прямо в ячейку таблицы.
    incoming.table.item(0, COL_AMOUNT).setText("1 500,75")
    assert [(r.source, r.date, r.amount) for r in store.data.cash_receipts] == [
        ("cash", "2026-10-27", "1500.75")
    ]
    assert incoming.amount_text("cash") == "1 500,75"
    incoming.table.item(1, COL_AMOUNT).setText("2 000")
    assert [(r.source, r.amount) for r in store.data.cash_receipts] == [
        ("cash", "1500.75"),
        ("card", "2000.00"),
    ]
    assert incoming.amount_text("card") == "2 000,00"

    # Колонки дней недели (Пн 26.10 – Сб 31.10): сумма видна в колонке
    # своего дня, а «Сумма» — за дату, выбранную в блоке (v0.22.0).
    incoming.table.item(0, COL_AMOUNT).setText("1 000")
    col_26 = _DAY_COL(incoming, date(2026, 10, 26))
    col_27 = _DAY_COL(incoming, date(2026, 10, 27))
    assert incoming.amount_text("cash", COL_AMOUNT) == "1 000,00"
    assert incoming.amount_text("cash", col_27) == "1 000,00"
    assert incoming.amount_text("cash", col_26) == ""

    # Сумма другого дня этой же недели стоит в своей колонке.
    view.incoming.set_date(date(2026, 10, 26))
    view.incoming.table.item(0, COL_AMOUNT).setText("500")
    assert view.incoming.amount_text("cash", COL_AMOUNT) == "500,00"
    view.incoming.set_date(date(2026, 10, 27))
    assert view.incoming.amount_text("cash", COL_AMOUNT) == "1 000,00"
    assert view.incoming.amount_text("cash", col_26) == "500,00"
    assert view.incoming.amount_text("cash", col_27) == "1 000,00"
    assert view.incoming.amount_text("card", col_27) == "2 000,00"

    # Пустая ячейка убирает запись дня (только по своему источнику).
    view.incoming.table.item(0, COL_AMOUNT).setText("")
    assert view.incoming.amount_text("cash") == ""
    assert [(r.source, r.date) for r in store.data.cash_receipts] == [
        ("card", "2026-10-27"),
        ("cash", "2026-10-26"),
    ]
    window.close()


def test_counterparty_popup_scrolls_long_list(qt_app, store):
    """v0.23.1: окно контрагентов — во весь экран и с прокруткой.

    Проверяем окно покупателей (блок «Поступление») и окно
    поставщиков (блок «Оплата»): список прокручивается, а высота
    окна ограничена только высотой экрана.
    """

    from PySide6.QtCore import Qt

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.customers.extend(
        Counterparty(name=f"Покупатель {i}") for i in range(12)
    )
    expected_suppliers = len(store.data.suppliers) + 12
    store.data.suppliers.extend(
        Counterparty(name=f"Поставщик {i}") for i in range(12)
    )
    window = MainWindow(store)
    window.show()
    window.resize(1280, 900)
    qt_app.processEvents()
    view = window.receipts_view

    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert popup is not None
    assert len(popup.edits) == 12
    # Список лежит в прокручиваемой области, скроллбар закреплён.
    assert popup.scroll_area.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    # Высота окна упирается в экран, а не в старые 320 px: при длинном
    # списке окно занимает всю доступную высоту.
    screen = popup.screen().availableGeometry().height()
    assert popup.height() <= screen - 16

    suppliers = view.open_popup("suppliers", row=0)
    qt_app.processEvents()
    assert suppliers is not None
    assert len(suppliers.edits) == expected_suppliers
    assert suppliers.scroll_area.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    window.close()


def test_receipts_customers_popup_opens_right_and_sums(qt_app, store):
    """Строка «Покупатели»: окно справа, итог по всем покупателям."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    first = Counterparty(name="Покупатель В")
    second = Counterparty(name="Покупатель Г")
    store.data.customers.extend([first, second])
    window = MainWindow(store)
    window.show()
    # Окно должно быть достаточно широким: проверка «попап справа от
    # таблицы» бессмысленна на узком headless-окне.
    window.resize(1280, 800)
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    popup = view.open_popup("customers", row=2)
    assert popup is not None
    assert list(popup.edits) == [first.id, second.id]
    # Окно всплывает справа от таблицы, а не под строкой. Проверяем,
    # только когда справа есть место: в headless-среде без настоящей
    # геометрии (платформа minimal) окна остаются узкими.
    table_right = view.incoming.table.mapToGlobal(
        view.incoming.table.rect().topRight()
    ).x()
    screen_width = window.screen().availableGeometry().width()
    free_space = min(screen_width, window.width()) - table_right
    if free_space >= popup.width():
        assert popup.x() > table_right - popup.width()

    # Итог пересчитывается по мере ввода и до сохранения.
    assert popup.total_label.text() == "0,00"
    popup.edits[first.id].setText("3 000")
    assert popup.total_label.text() == "3 000,00"
    popup.edits[second.id].setText("1 250,50")
    assert popup.total_label.text() == "4 250,50"
    popup._save()

    assert [
        (op.kind, op.counterparty_id, op.date, op.amount, op.due_date)
        for op in store.data.operations
        if op.kind == "Оплата"
    ] == [
        ("Оплата", first.id, "2026-10-27", "3000.00", ""),
        ("Оплата", second.id, "2026-10-27", "1250.50", ""),
    ]
    assert view.incoming.amount_text("customers") == "4 250,50"

    # Та же сумма видна в колонке «Оплата» недельной таблицы покупателей.
    window.customers_view.goto_date(date(2026, 10, 27))
    table = window.customers_view.table
    names = [c.name for c in store.data.customers]
    assert table.item(4 + names.index(first.name), 2 + 1 * 2 + 1).text() == "3 000,00"
    assert table.item(4 + names.index(second.name), 2 + 1 * 2 + 1).text() == "1 250,50"
    window.close()


def test_receipts_outgoing_block_saves_supplier_payment(qt_app, store):
    """Блок «Оплата» под «Поступлением»: окно поставщиков с полями сумм."""
    from datetime import date

    from oplata.app import MainWindow

    supplier = next(s for s in store.data.suppliers if s.name == "ббб")
    window = MainWindow(store)
    view = window.receipts_view
    # Блок «Оплата» расположен под блоком «Поступление» (в левой колонке).
    layout = view.left_layout
    assert layout.indexOf(view.outgoing) > layout.indexOf(view.incoming)
    view.outgoing.set_date(date(2026, 10, 27))

    popup = view.open_popup("suppliers", row=0)
    assert supplier.id in popup.edits
    popup.edits[supplier.id].setText("4 500")
    assert popup.total_label.text() == "4 500,00"
    popup._save()
    (op,) = store.data.operations
    assert (op.kind, op.counterparty_id, op.date, op.amount) == (
        "Оплата",
        supplier.id,
        "2026-10-27",
        "4500.00",
    )
    assert view.outgoing.amount_text("suppliers") == "4 500,00"

    # Недельная таблица поставщиков показывает ту же оплату в своей ячейке.
    window.suppliers_view.goto_date(date(2026, 10, 27))
    table = window.suppliers_view.table
    bbb_row = 4 + [s.name for s in store.data.suppliers].index("ббб")
    assert table.item(bbb_row, _DAY(window.suppliers_view, 1, 2)).text() == "4 500,00"
    window.close()


def test_data_panels_are_collapsible(qt_app, store):
    """Плашки «Настройка календаря» и «Администрирование» раскрываются."""
    from oplata.app import MainWindow

    window = MainWindow(store)
    data = window.data_view

    assert data.calendar_panel.title == "Настройка календаря"
    assert data.admin_panel.title == "Администрирование"
    assert "Настройка календаря" in data.calendar_panel.toggle.text()
    assert "Администрирование" in data.admin_panel.toggle.text()

    # По умолчанию свёрнуты: календарь нужен редко и занимает много места.
    assert not data.calendar_panel.is_expanded()
    assert not data.admin_panel.is_expanded()
    assert data.calendar_panel.content.isHidden()

    data.calendar_panel.toggle.click()
    assert data.calendar_panel.is_expanded()
    assert not data.calendar_panel.content.isHidden()
    assert data.year_label.text()  # календарь внутри плашки работает

    data.admin_panel.toggle.click()
    assert data.admin_panel.is_expanded()
    assert str(store.path) in data.path_label.text()
    assert "Поставщиков: 5" in data.stats_label.text()
    assert data.save_button.text() == "Сохранить"
    window.close()


from oplata.views.receipts_view import (  # noqa: E402
    COL_AMOUNT,
    COL_NAME,
    COLUMN_COUNT,
    TABLE_DAYS,
)


def test_receipts_popup_keeps_saved_amounts(qt_app, store):
    """Повторное открытие окна показывает уже введённые суммы."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    buyer = Counterparty(name="Покупатель Д")
    store.data.customers.append(buyer)
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    popup = view.open_popup("customers", row=2)
    assert popup.edits[buyer.id].text() == ""  # пока ничего не вводили
    popup.edits[buyer.id].setText("2 500")
    popup._save()

    again = view.open_popup("customers", row=2)
    assert again.edits[buyer.id].text() == "2 500,00"
    assert again.total_label.text() == "2 500,00"
    # Очистка поля и сохранение убирают сумму дня.
    again.edits[buyer.id].setText("")
    again._save()
    assert [op for op in store.data.operations if op.kind == "Оплата"] == []
    assert view.incoming.amount_text("customers") == ""
    window.close()


def test_other_payment_rows_add_sum_and_remove(qt_app, store, monkeypatch):
    """Блок «Оплата»: вид — карточкой, сумма — в ячейку, «−» удаляет."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_NAME, JournalPopup

    # v0.20.0: вид оплаты вводится карточкой (модальный диалог) — в
    # прогоне её подменяем на заполненные ответы.
    patch_other_payment_card(monkeypatch, ["Новый вид оплаты", "Второй вид"])
    window = MainWindow(store)
    view = window.receipts_view
    day_column = _DAY_COL(view.outgoing, date(2026, 10, 27))
    view.outgoing.set_date(date(2026, 10, 27))
    assert view.outgoing.row_keys == ["suppliers"]  # пока только поставщики

    view.add_other_row()
    assert view.outgoing.row_keys == ["suppliers", "other:Новый вид оплаты"]

    # Название в ячейке не правится (v0.21.0): клик по названию открывает
    # журнал операций, а не поле ввода.
    name_item = view.outgoing.table.item(1, 0)
    assert not (name_item.flags() & Qt.ItemFlag.ItemIsEditable)
    view.outgoing.table.cellClicked.emit(1, COL_NAME)
    assert isinstance(view._popup, JournalPopup)

    # Сумма — прямо в ячейку, итоги считаются по названию.
    view.outgoing.table.item(1, COL_AMOUNT).setText("12 000")
    assert view.outgoing.amount_text("other:Новый вид оплаты") == "12 000,00"
    assert view.outgoing.amount_text("other:Новый вид оплаты", day_column) == "12 000,00"
    assert [
        (p.name, p.date, p.amount, p.direction)
        for p in store.data.other_payments
        if p.name == "Новый вид оплаты"
    ] == [("Новый вид оплаты", "2026-10-27", "12000.00", "out")]

    # Вторая строка и удаление первой (кнопка «−»).
    view.add_other_row()
    assert len(view.outgoing.row_keys) == 3
    view.outgoing.table.setCurrentCell(1, 0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    view.remove_other_row("other:Новый вид оплаты")
    assert view.outgoing.row_keys == [
        "suppliers",
        "other:Второй вид",
    ]
    assert [
        p.name
        for p in store.data.other_payments
        if getattr(p, "direction", "out") == "out"
    ] == ["Второй вид"]

    # Постоянную строку «Поставщики» удалить нельзя.
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: None),
    )
    view.outgoing.table.setCurrentCell(0, 0)
    view.outgoing._remove_selected()
    assert view.outgoing.row_keys[0] == "suppliers"
    window.close()


def test_supplier_payments_sync_both_ways(qt_app, store):
    """Суммы поставщиков видны и здесь, и в недельной таблице."""
    from datetime import date

    from oplata.app import MainWindow

    supplier = next(s for s in store.data.suppliers if s.name == "ббб")
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    # Из блока «Оплата» — в таблицу поставщиков.
    popup = view.open_popup("suppliers", row=0)
    popup.edits[supplier.id].setText("4 500")
    popup._save()
    assert view.outgoing.amount_text("suppliers") == "4 500,00"

    window.suppliers_view.goto_date(date(2026, 10, 27))
    table = window.suppliers_view.table
    bbb_row = 4 + [s.name for s in store.data.suppliers].index("ббб")
    payment_cell = table.item(bbb_row, _DAY(window.suppliers_view, 1, 2))
    assert payment_cell.text() == "4 500,00"

    # И обратно: правка в таблице поставщиков видна в блоке «Оплата».
    payment_cell.setText("7 000")
    assert view.outgoing.amount_text("suppliers") == "7 000,00"
    check = view.open_popup("suppliers", row=0)
    assert check.edits[supplier.id].text() == "7 000,00"
    check.close()

    # То же самое для покупателей.
    from oplata.models import Counterparty

    buyer = Counterparty(name="Покупатель Е")
    store.data.customers.append(buyer)
    window.customers_view.goto_date(date(2026, 10, 27))
    customers_table = window.customers_view.table
    buyer_row = 4 + [c.name for c in store.data.customers].index("Покупатель Е")
    customers_table.item(buyer_row, 2 + 1 * 2 + 1).setText("1 750")
    assert view.incoming.amount_text("customers") == "1 750,00"
    window.close()


def test_incoming_block_has_fixed_other_income_row(qt_app, store, monkeypatch):
    """В «Поступлении» одна постоянная строка «Прочие поступления» (v0.13.0)."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import INCOMING, OUTGOING

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))
    assert view.incoming.row_keys == [
        "cash",
        "card",
        "customers",
        "other:Прочие поступления",
    ]
    # Кнопок «+»/«−» у блока «Поступление» нет — строку не добавить и
    # не убрать, название не редактируется.
    assert not hasattr(view.incoming, "add_btn")
    name_item = view.incoming.table.item(3, COL_NAME)
    assert not (name_item.flags() & Qt.ItemFlag.ItemIsEditable)

    # Сумма вводится прямо в ячейку и пишется с направлением «in».
    view.incoming.table.item(3, COL_AMOUNT).setText("5 000")
    qt_app.processEvents()
    assert view.incoming.amount_text("other:Прочие поступления") == "5 000,00"
    assert [
        (p.name, p.direction, p.date, p.amount)
        for p in store.data.other_payments
        if p.name == "Прочие поступления"
    ] == [("Прочие поступления", INCOMING, "2026-10-27", "5000.00")]

    # Свои строки блока «Оплата» не пересекаются с постоянной строкой.
    patch_other_payment_card(monkeypatch)
    view.add_other_row(OUTGOING)
    assert view.outgoing.row_keys == ["suppliers", "other:Новый вид оплаты"]
    assert view.incoming.row_keys[-1] == "other:Прочие поступления"
    window.close()


def test_receipts_popup_has_no_buttons_and_saves_on_click_outside(qt_app, store):
    """Окно без «Закрыть»/«Сохранить»: сохраняет по клику вне поля ввода."""
    from datetime import date

    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QPushButton

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    buyer = Counterparty(name="Покупатель Ж")
    store.data.customers.append(buyer)
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert [b.text() for b in popup.findChildren(QPushButton)] == []
    # Автоподсветки нет: фокус не забирает ни одно из полей.
    assert qt_app.focusWidget() is not popup.edits[buyer.id]

    # Клик по полю ввода окно не закрывает.
    edit = popup.edits[buyer.id]
    edit.setText("7 250")
    point = edit.mapTo(popup, edit.rect().center())
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qt_app.sendEvent(popup, event)
    qt_app.processEvents()
    assert popup.isVisible()

    # Клик в свободном месте — сохраняет и скрывает окно.
    point = popup.total_label.mapTo(popup, popup.total_label.rect().center())
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qt_app.sendEvent(popup, event)
    qt_app.processEvents()
    assert view.incoming.amount_text("customers") == "7 250,00"
    assert [
        (op.kind, op.date, op.amount)
        for op in store.data.operations
        if op.kind == "Оплата"
    ] == [("Оплата", "2026-10-27", "7250.00")]
    window.close()


def test_tables_edit_on_single_click_and_numbers_centered(qt_app, store):
    """Ввод — по одному клику; значения в таблицах — по центру (v0.23.9)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_AMOUNT, COLUMN_COUNT

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    center = Qt.AlignmentFlag.AlignCenter  # v0.23.9: не по правому краю

    for table in (
        view.incoming.table,
        view.outgoing.table,
        window.suppliers_view.table,
        window.customers_view.table,
    ):
        assert table.editTriggers() == QAbstractItemView.EditTrigger.AllEditTriggers

    # «Сумма» и все колонки дней недели — по центру (v0.23.9).
    for column in range(COL_AMOUNT, COLUMN_COUNT):
        assert view.incoming.table.item(0, column).textAlignment() == center
        assert (
            view.incoming.table.horizontalHeaderItem(column).textAlignment()
            == center
        )
    window.close()


def test_money_block_scrolls_long_row_list(qt_app, store, monkeypatch):
    """Длинный список своих строк прокручивается, блок не растёт дальше."""
    from oplata.app import MainWindow
    from oplata.models import OUTGOING

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    patch_other_payment_card(monkeypatch)
    for _ in range(9):  # всего 10 строк — больше видимых восьми
        view.add_other_row(OUTGOING)
    qt_app.processEvents()
    assert len(view.outgoing.row_keys) == 10
    # Блок «Оплата» тянется до низа, а скроллбар закреплён и широкий.
    assert view.outgoing.table.verticalScrollBar().isVisible()
    assert (
        view.outgoing.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    assert view.outgoing.table.verticalScrollBar().width() >= 18
    # Блок «Оплата» опущен до низа: он ниже блока «Поступление».
    assert view.outgoing.geometry().bottom() >= view.incoming.geometry().bottom()
    window.close()


def test_week_day_columns_show_daily_amounts(qt_app, store):
    """Колонки дней недели: сумма за каждый день (v0.22.0)."""
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import set_daily_cash_receipt
    from oplata.views.receipts_view import COLUMN_COUNT, TABLE_DAYS

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    # 27.10.2026 — вторник; неделя начинается 26.10 (понедельник).
    view.set_date(date(2026, 10, 27))
    # День текущей недели (понедельник 26.10).
    set_daily_cash_receipt(store.data, "cash", "2026-10-26", Decimal("2000"))
    # Выбранный день.
    set_daily_cash_receipt(store.data, "cash", "2026-10-27", Decimal("3000"))
    # Прошлые даты: в колонках этой недели их не видно.
    set_daily_cash_receipt(store.data, "cash", "2026-10-05", Decimal("1000"))
    set_daily_cash_receipt(store.data, "cash", "2026-07-02", Decimal("500"))
    view.refresh()
    qt_app.processEvents()

    # Шесть дней недели: Пн 26.10 – Сб 31.10 (воскресенья нет).
    assert view.incoming.table.columnCount() == COLUMN_COUNT == 2 + TABLE_DAYS
    assert view.incoming.days[0] == date(2026, 10, 26)
    assert view.incoming.days[-1] == date(2026, 10, 31)
    headers = [
        view.incoming.table.horizontalHeaderItem(column).text()
        for column in range(COLUMN_COUNT)
    ]
    assert headers[0] == "" and headers[1] == "Сумма"
    assert headers[2].startswith("26.10") and headers[-1].startswith("31.10")

    row = view.incoming.row_keys.index("cash")
    assert view.incoming.amount_text("cash") == "3 000,00"  # выбранный день
    assert (
        view.incoming.amount_text("cash", _DAY_COL(view.incoming, date(2026, 10, 26)))
        == "2 000,00"
    )
    assert (
        view.incoming.amount_text("cash", _DAY_COL(view.incoming, date(2026, 10, 27)))
        == "3 000,00"
    )
    # Остальные дни недели пустые: суммы вне недели (05.10, 02.07) в
    # колонки дней не попадают.
    for day in (date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)):
        assert view.incoming.amount_text("cash", _DAY_COL(view.incoming, day)) == ""
    window.close()


def test_arrows_are_bold_and_row_hover_installed(qt_app, store):
    """Стрелки жирные; в таблицах есть подсветка строки под курсором."""
    from PySide6.QtGui import QFont

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view

    for button in (
        view.incoming.prev_btn,
        view.incoming.next_btn,
        window.suppliers_view.findChildren(type(view.incoming.prev_btn))[0],
    ):
        font: QFont = button.font()
        assert font.bold() or font.weight() >= QFont.Weight.Bold

    # Подсветка строки: у делегатов есть метод set_hover_row (миксин).
    for table in (
        view.incoming.table,
        view.outgoing.table,
        window.suppliers_view.table,
        window.customers_view.table,
    ):
        assert table.hasMouseTracking()
        delegate = table.itemDelegate()
        assert hasattr(delegate, "set_hover_row")
        delegate.set_hover_row(0)  # не падает — подсветка применилась
        delegate.set_hover_row(-1)
    window.close()


def test_year_start_panel_saves_opening_balances(qt_app, store):
    """Плашка «Данные»: перенос остатков на 1 января сохраняется в файл."""
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import (
        year_start_customer_debt,
        year_start_supplier_debt,
    )
    from oplata.storage import DataStore

    from oplata.models import Counterparty

    buyer = Counterparty(name="Покупатель З")
    store.data.customers.append(buyer)
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.data_view
    supplier = next(s for s in store.data.suppliers if s.name == "ббб")

    # Плашка свёрнута по умолчанию, таблицы заполнены контрагентами.
    assert view.data_panel is not None
    assert view.supplier_debts_table.rowCount() == len(
        [s for s in store.data.suppliers if not s.archived]
    )
    assert view.customer_debts_table.rowCount() == len(
        [c for c in store.data.customers if not c.archived]
    )

    # Вводим долг поставщику: дата оплаты и сумма.
    row = [s.id for s in store.data.suppliers if not s.archived].index(supplier.id)
    view.supplier_debts_table.item(row, 1).setText("15.01.2026")
    view.supplier_debts_table.item(row, 2).setText("10 000")
    # Задолженность покупателя и текущие средства.
    brow = [c.id for c in store.data.customers if not c.archived].index(buyer.id)
    view.customer_debts_table.item(brow, 1).setText("2 500")
    view.funds_edit.setText("75 000")
    view._save_year_start()

    data = store.data
    assert data.year_start.date.startswith("2026-01-01")
    entry = year_start_supplier_debt(data, supplier.id)
    assert entry is not None
    assert (entry.due_date, entry.amount) == ("2026-01-15", "10000.00")
    debt = year_start_customer_debt(data, buyer.id)
    assert debt is not None and debt.amount == "2500.00"
    assert data.year_start.funds == "75000.00"

    # Прочитали файл заново — перенос на месте.
    reloaded = DataStore(store.path).data
    assert len(reloaded.year_start.supplier_debts) == 1
    assert reloaded.year_start.funds == "75000.00"

    # Пустая сумма убирает запись переноса.
    view.supplier_debts_table.item(row, 2).setText("")
    view._save_year_start()
    assert year_start_supplier_debt(store.data, supplier.id) is None
    window.close()


def test_year_start_tables_are_side_by_side_and_full_height(qt_app, store):
    """v0.24.0: таблицы остатков — две вертикальные, слева и справа, и
    занимают всю высоту экрана."""

    from oplata.app import IDX_DATA, MainWindow

    window = MainWindow(store)
    window.resize(1400, 900)
    window.show()
    # Переход на вкладку «Конфигурация» раскрывает плашку «Данные».
    window._config_unlocked = True
    window.show_page(IDX_DATA)
    qt_app.processEvents()
    view = window.data_view
    assert view.data_panel.is_expanded()

    left = view.supplier_debts_table
    right = view.customer_debts_table
    # Слева и справа: одна высота, разные колонки, равная ширина.
    assert left.pos().x() < right.pos().x()
    assert abs(left.pos().y() - right.pos().y()) <= 2
    assert abs(left.width() - right.width()) <= 2
    # Таблицы растянуты по высоте, а не сжаты до содержимого.
    assert left.height() > left.sizeHint().height()
    assert right.height() > right.sizeHint().height()
    window.close()


def test_arrows_are_next_to_today_and_bold_only_on_receipts(qt_app, store):
    """Стрелки — справа от «Сегодня»; жирные только на первой вкладке."""
    from PySide6.QtCore import Qt

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view

    # Стрелки стоят сразу справа от кнопки «Сегодня».
    for block in (view.incoming, view.outgoing):
        assert block.prev_btn.x() > block.today_btn.x()
        assert block.next_btn.x() > block.prev_btn.x()
    outgoing = view.outgoing
    assert outgoing.add_btn.x() > outgoing.next_btn.x()
    assert outgoing.remove_btn.x() > outgoing.add_btn.x()
    # В блоке «Поступление» кнопок добавления строк нет.
    assert not hasattr(view.incoming, "add_btn")

    # Жирные стрелки только здесь: у них увеличенный кегль, а на
    # других вкладках стрелки обычного размера (v0.15.0).
    plain = window.suppliers_view.prev_week_btn.font().pixelSize()
    assert outgoing.next_btn.font().pixelSize() > plain
    assert view.incoming.next_btn.font().pixelSize() > plain

    # Скроллинга в «Поступлении» нет, в «Оплате» он закреплён.
    assert (
        view.incoming.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert (
        outgoing.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    window.close()


def test_week_table_overdue_and_debt_headers_are_separate(qt_app, store):
    """v0.16.0: три итоговые колонки — под одной шапкой «ЗАДОЛЖЕННОСТЬ»."""
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    table = window.suppliers_view.table
    assert table.item(1, 1).text() == "ЗАДОЛЖЕННОСТЬ"
    # Ячейки объединены: подписи столбцов стоят строкой ниже.
    assert table.columnSpan(1, 1) == 3
    # v0.22.1: «Итого» стоит слева от «просрочено» (вместо «на сегодня»).
    assert table.item(2, 1).text() == "Итого"
    assert table.item(2, 2).text() == "просрочено"
    assert table.item(2, 3).text() == "за неделю"
    window.close()


def test_popup_closes_on_mouse_click_anywhere(qt_app, store):
    """Окно сумм закрывается по клику мышью по нему (кроме полей ввода)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.customers.append(Counterparty(name="Покупатель И"))
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.show()
    qt_app.processEvents()
    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert popup.isVisible()

    def press(widget, point):
        QApplication.sendEvent(
            widget,
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(point),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        qt_app.processEvents()

    # Клик по свободному месту окна (не по полю ввода) — окно закрыто.
    press(popup, QPointF(popup.width() - 10, 10))
    assert not popup.isVisible()

    # Клик по таблице под окном тоже закрывает его.
    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert popup.isVisible()
    press(view.incoming.table, QPointF(5.0, 5.0))
    assert not popup.isVisible()

    # Клик по самому полю ввода окно не закрывает (ввод продолжается).
    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    edit = next(iter(popup.edits.values()))
    press(popup, QPointF(edit.mapTo(popup, edit.rect().center())))
    assert popup.isVisible()
    window.close()


def test_summary_panel_shows_three_blocks_line_by_line(qt_app, store):
    """Плашка «Сводка»: три блока, все значения построчно."""
    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import (
        CASH_SOURCE,
        add_cash_receipt,
        add_delivery,
        add_supplier_payment,
        set_daily_other_payment,
        set_year_start_funds,
    )

    # 27.10.2026 — вторник; неделя 26.10–01.11, следующие: 02.11–08.11,
    # 09.11–15.11, 16.11–22.11.
    on_date = date(2026, 10, 27)
    supplier = store.data.suppliers[0]

    # Средства: 50 000 на счету, поступление 3 000, оплата 1 200
    # (v0.22.1: ещё аренда 250 от 15.10 — она уже в остатке на 27.10,
    # а в «Оплаты за день» не входит).
    set_year_start_funds(store.data, Decimal("50000"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-27", Decimal("3000"))
    add_supplier_payment(store.data, supplier.id, "2026-10-27", Decimal("1200"))

    # Задолженность: срок сегодня (27.10), просрочка (20.10) и план на
    # вторую неделю вперёд (09.11–15.11).
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-10-27", Decimal("500"))
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("700"))
    add_delivery(store.data, supplier.id, "2026-10-05", "2026-11-10", Decimal("900"))
    # Прочий вид оплаты с начала месяца.
    set_daily_other_payment(
        store.data, "Аренда", "2026-10-15", Decimal("250")
    )

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    view.refresh()
    qt_app.processEvents()

    # Блок «Средства».
    assert view.caption_text("funds") == "Остаток на счёте"
    # Остаток на начало 27.10: 50 000 − аренда 250 (15.10).
    assert view.value_text("funds") == "49 750,00"
    assert view.value_text("income") == "3 000,00"
    assert view.value_text("payments") == "1 200,00"
    assert view.value_text("balance") == "51 550,00"

    # Блок «Задолженность поставщикам».
    assert view.value_text("debt_total") == "2 100,00"
    # «Задолженность на сегодня» удалена. Оплата поставщику (1 200)
    # гасит сначала просроченное (700 со сроком 20.10), остаток 500 —
    # долги недели (v0.15.0): и «на неделе», и «Просрочено» пусты.
    assert view.value_text("debt_week") == "0,00"
    # v0.22.1: строки «Задолженность на сегодня» в сводке нет.
    assert "debt_overdue" not in view._values
    assert view.caption_text("plan_1") == "Оплата 02.11 – 08.11"
    assert view.value_text("plan_1") == "0,00"
    assert view.caption_text("plan_2") == "Оплата 09.11 – 15.11"
    assert view.value_text("plan_2") == "900,00"
    assert view.caption_text("plan_3") == "Оплата 16.11 – 22.11"
    assert view.value_text("plan_3") == "0,00"
    assert view.caption_text("plan_4") == "Оплата 23.11 – 29.11"
    assert view.value_text("plan_4") == "0,00"

    # Блок «Месяц»: приход 05.10 + 10.10 + 20.10, оплаты 27.10 + аренда.
    assert view.value_text("month_income") == "2 100,00"
    assert view.value_text("month_payments") == "1 450,00"

    # Все значения построчно: подписи и числа лежат в сетке блоками.
    assert view.panel.isVisible()
    # Блок поставщиков: всего, на неделе, просроченный платёж
    # (v0.16.0) + четыре недели оплаты (v0.22.1: без «на сегодня»).
    assert len(view._values) == len(view._captions) == 4 + 7 + 1 + 2
    # Блок «Задолженность покупателей» пока пуст (покупателей нет).
    assert view.value_text("cust_total") == "0,00"
    window.close()


def test_summary_calc_helpers_are_pure(qt_app, store):
    """Расчёты сводки — простые суммы по сохранённым данным."""
    from datetime import date
    from decimal import Decimal

    from oplata import calc
    from oplata.models import (
        KIND_PAYMENT,
        add_delivery,
        set_daily_operation,
        set_year_start_supplier_debt,
    )

    supplier = store.data.suppliers[0]
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("700"))
    set_year_start_supplier_debt(
        store.data, supplier.id, "2026-10-28", Decimal("300")
    )

    schedule = calc.debt_schedule(store.data)
    assert schedule == {date(2026, 10, 20): Decimal("700"), date(2026, 10, 28): Decimal("300")}

    totals = calc.debt_totals(store.data, date(2026, 10, 28))
    assert totals.total == Decimal("1000")
    assert totals.today == Decimal("300")
    # Неделя 26.10–01.11: срок 20.10 уже в прошлой неделе. Оплат нет,
    # поэтому неделя и просроченное считаются по графику.
    assert totals.week == Decimal("300")
    # «Просрочено» = столбец «на сегодня»: долги со сроком по дату
    # сводки включительно — 700 (срок 20.10) и 300 (перенос, срок 28.10).
    assert totals.overdue == Decimal("1000")

    # Оплата 500 гасит сначала просроченное (срок 20.10), остаток 0.
    set_daily_operation(
        store.data, supplier.id, "2026-10-27", KIND_PAYMENT, Decimal("500")
    )
    paid = calc.debt_totals(store.data, date(2026, 10, 27))
    assert paid.overdue == Decimal("200")  # 700 − 500: оплата в просрочку
    # Долг переноса со сроком 28.10 внутри недели 26.10–01.11 не тронут:
    # оплата приоритетно ушла в просроченное (v0.15.0).
    assert paid.week == Decimal("300")

    plan = calc.weekly_payment_plan(store.data, date(2026, 10, 28))
    assert [week.amount for week in plan] == [Decimal("0")] * 4
    assert [week.monday.isoformat() for week in plan] == [
        "2026-11-02",
        "2026-11-09",
        "2026-11-16",
        "2026-11-23",
    ]

    # v0.22.1: блок «Средства» считается на дату.
    funds = calc.funds_totals(store.data, date(2026, 10, 27))
    assert funds.funds == Decimal("0")   # «текущие средства» не задавали
    assert funds.balance == funds.funds + funds.income - funds.payments


def test_summary_customer_debt_block_counts_sales_minus_payments(qt_app, store):
    """Блок «Задолженность покупателей»: Реализация − Оплата."""
    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import (
        Counterparty,
        add_customer_receipt,
        add_realization,
    )

    buyer = Counterparty(name="Покупатель К")
    store.data.customers.append(buyer)
    add_realization(store.data, buyer.id, "2026-10-27", Decimal("5000"))
    add_customer_receipt(store.data, buyer.id, "2026-10-27", Decimal("1500"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    view.refresh()

    assert view.caption_text("cust_total") == "Задолженность покупателей"
    assert view.value_text("cust_total") == "3 500,00"

    # «Итого поступление» не включает реализацию покупателей (v0.15.0).
    assert view.value_text("income") == "0,00"
    assert view.value_text("payments") == "1 500,00"
    window.close()


def test_funds_movement_dialog_fills_screen_height(qt_app, store):
    """v0.23.4: журнал движения средств — на всю высоту экрана, со скроллом."""

    from datetime import date

    from PySide6.QtWidgets import QApplication

    from oplata.views.funds_view import FundsMovementDialog

    dialog = FundsMovementDialog(store, date(2026, 10, 28))
    # На экране меньше минимальной высоты окно не сжимается ниже 320.
    available = QApplication.primaryScreen().availableGeometry().height()
    assert dialog.height() == max(320, available - 24)
    assert (
        dialog.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    assert "QScrollBar:vertical" in dialog.table.styleSheet()
    dialog.close()


def test_funds_movement_journal_shows_one_day(qt_app, store):
    """v0.23.1: журнал движения средств — за один день.

    Порядок строк: «Остаток на счёте» (на начало дня), все поступления
    дня, все оплаты дня и «Итого остаток». Над таблицей — дата со
    стрелками, как в блоках «Поступление»/«Оплата».
    """

    from datetime import date
    from decimal import Decimal

    from oplata import calc
    from oplata.models import (
        CASH_SOURCE,
        add_cash_receipt,
        add_supplier_payment,
        set_year_start_funds,
    )
    from oplata.views.funds_view import MOVEMENT_DAYS, FundsMovementDialog

    supplier = store.data.suppliers[0]
    set_year_start_funds(store.data, Decimal("100"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-27", Decimal("500"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-28", Decimal("700"))
    add_supplier_payment(store.data, supplier.id, "2026-10-28", Decimal("200"))

    on_date = date(2026, 10, 28)
    movement = calc.funds_movement(store.data, on_date)
    assert MOVEMENT_DAYS == 1
    assert movement.start == on_date == movement.end
    # Остаток на начало 28.10: 100 (перенос) + 500 (поступление 27.10).
    assert movement.start_funds == Decimal("600")
    assert movement.income == Decimal("700")
    assert movement.payments == Decimal("200")
    assert movement.balance == Decimal("1100")

    dialog = FundsMovementDialog(store, on_date)
    table = dialog.table
    # Остаток на счёте, поступление, оплата, итог остаток.
    assert table.rowCount() == 4
    assert table.item(0, 1).text() == "Остаток на счёте"
    assert table.item(0, 4).text() == "600,00"
    assert table.item(1, 1).text() == "Поступление наличными"
    assert table.item(1, 2).text() == "700,00"
    assert table.item(2, 3).text() == "200,00"
    assert table.item(3, 1).text() == "Итого остаток"
    assert table.item(3, 2).text() == "700,00"
    assert table.item(3, 3).text() == "200,00"
    assert table.item(3, 4).text() == "1 100,00"

    # Стрелки и «Сегодня» переставляют дату журнала.
    assert dialog.current_date() == on_date
    dialog.shift_date(1)
    assert dialog.current_date() == date(2026, 10, 29)
    # 29.10: поступлений и оплат нет — только перенесённый остаток.
    assert table.rowCount() == 2
    assert table.item(0, 1).text() == "Остаток на счёте"
    assert table.item(0, 4).text() == "1 100,00"
    assert table.item(1, 1).text() == "Итого остаток"
    assert table.item(1, 4).text() == "1 100,00"
    dialog.close()


def test_funds_block_carries_balance_to_next_day(qt_app, store):
    """v0.22.1: вчерашний «Итого остаток» = сегодняшний «Остаток на счету».

    Правило пользователя: «сегодня после всех поступлений и оплат
    итого остаток 350 руб, на следующий день эта сумма переходит в
    остаток на счету, к ней добавляются поступления и отнимаются
    оплаты».
    """

    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import CASH_SOURCE, add_cash_receipt, set_year_start_funds

    set_year_start_funds(store.data, Decimal("100"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-27", Decimal("500"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    qt_app.processEvents()

    # 27.10: остаток на начало дня 100, поступление 500, итого 600.
    assert view.value_text("funds") == "100,00"
    assert view.value_text("income") == "500,00"
    assert view.value_text("payments") == "0,00"
    assert view.value_text("balance") == "600,00"

    # 28.10: 600 перешло в «Остаток на счету», за день пока ничего.
    view.date_edit.setDate(QDate(2026, 10, 28))
    qt_app.processEvents()
    assert view.value_text("funds") == "600,00"
    assert view.value_text("income") == "0,00"
    assert view.value_text("payments") == "0,00"
    assert view.value_text("balance") == "600,00"

    # Дата блоков «Поступление»/«Оплата» идёт за датой сводки.
    assert window.receipts_view.incoming.current_date() == date(2026, 10, 28)
    assert window.receipts_view.outgoing.current_date() == date(2026, 10, 28)

    # И наоборот: смена даты в блоке переставляет дату сводки.
    window.receipts_view.incoming.set_date(date(2026, 10, 29))
    qt_app.processEvents()
    assert view.current_date() == date(2026, 10, 29)
    assert view.value_text("funds") == "600,00"
    window.close()


def test_summary_refreshes_right_after_typing_amount(qt_app, store):
    """Сводка пересчитывается сразу после ввода суммы в ячейку."""
    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_AMOUNT

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    summary = window.summary_view

    assert summary.value_text("income") == "0,00"
    view.incoming.table.item(0, COL_AMOUNT).setText("2 500")
    qt_app.processEvents()
    assert summary.value_text("income") == "2 500,00"
    assert summary.value_text("balance") == "2 500,00"

    # Убрали сумму — сводка снова пересчиталась.
    view.incoming.table.item(0, COL_AMOUNT).setText("")
    qt_app.processEvents()
    assert summary.value_text("income") == "0,00"
    window.close()


def test_week_customer_debt_column_is_sales_minus_payments(qt_app, store):
    """У покупателей столбец «ЗАДОЛЖ.»/«Всего» = Реализация − Оплата."""
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import (
        Counterparty,
        add_customer_receipt,
        add_realization,
    )

    buyer = Counterparty(name="Покупатель Л")
    store.data.customers.append(buyer)
    add_realization(store.data, buyer.id, "2026-10-27", Decimal("4000"))
    add_customer_receipt(store.data, buyer.id, "2026-10-28", Decimal("1000"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.customers_view
    view.goto_date(date(2026, 10, 27))
    table = view.table

    assert table.item(1, 1).text() == "ЗАДОЛЖ."
    assert table.item(2, 1).text() == "Всего"
    # Столбца «просрочено» у покупателей нет: вторая колонка — день.
    assert table.item(2, 2).text() == "Реализация"
    assert table.item(4, 1).text() == "3 000,00"
    assert table.item(3, 1).text() == "3 000,00"  # строка «ВСЕГО»
    window.close()


def _DAY_COL(block, day):
    """Номер колонки дня в блоке «Поступление»/«Оплата» (v0.22.0)."""
    from oplata.views.receipts_view import FIRST_DAY_COL

    return FIRST_DAY_COL + block.days.index(day)


def _DAY(view, day_index, subcolumn):
    """Номер колонки дня: считается от числа итоговых столбцов таблицы."""
    return view._day_column(day_index, subcolumn)


def test_supplier_click_opens_operations_journal(qt_app, store, monkeypatch):
    """v0.23.1: клик по поставщику открывает журнал операций.

    Раньше клик открывал окно «Поставка» — по просьбе пользователя
    поставщик теперь ведёт себя так же, как покупатель: клик — журнал
    операций, двойной клик — окно «Поставка».
    """

    from datetime import date

    from oplata.app import MainWindow

    # Подменяем методы до создания окна: WeekView получает ссылки на них
    # в конструкторе, поэтому подмена атрибута готового окна не поможет.
    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MainWindow,
        "open_operations_journal",
        lambda self, kind, cid: opened.append((kind, cid)),
    )
    deliveries: list[str] = []
    monkeypatch.setattr(
        MainWindow,
        "open_delivery_dialog",
        lambda self, cid, _op=None: deliveries.append(cid),
    )

    window = MainWindow(store)
    view = window.suppliers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table
    row = 4  # первая строка контрагента

    supplier_id = store.data.suppliers[0].id
    view._on_item_clicked(table.item(row, 0))
    assert opened == [("supplier", supplier_id)]
    assert deliveries == []

    # Двойной клик по-прежнему открывает окно «Поставка».
    view._on_item_double_clicked(table.item(row, 0))
    assert deliveries == [supplier_id]
    window.close()


def test_tab_switch_resets_date_to_today(qt_app, store):
    """v0.23.1: переход на вкладку возвращает дату на сегодняшний день."""

    from datetime import date

    from oplata.app import IDX_CUSTOMERS, IDX_RECEIPTS, IDX_SUPPLIERS, MainWindow
    from oplata.dates import week_monday

    window = MainWindow(store)
    today = date.today()

    # Ушли на другую неделю у поставщиков и в блоках ввода.
    window.suppliers_view.goto_date(date(2026, 10, 26))
    window.receipts_view.set_date(date(2026, 10, 26))
    assert window.suppliers_view.week_start == week_monday(date(2026, 10, 26))

    window.show_page(IDX_CUSTOMERS)
    assert window.customers_view.week_start == week_monday(today)

    window.show_page(IDX_SUPPLIERS)
    assert window.suppliers_view.week_start == week_monday(today)

    window.show_page(IDX_RECEIPTS)
    assert window.receipts_view.incoming.current_date() == today
    assert window.receipts_view.outgoing.current_date() == today
    window.close()


def test_wide_scrollbar_keeps_white_background(qt_app, store):
    """v0.23.1: широкий скроллбар не делает фон серым.

    Виджет со своей таблицей стилей становится корнем новой области
    стилей Qt: правила APP_STYLESHEET для него и его детей перестают
    действовать, и без повторного «background:#ffffff» вместо белого
    проглядывает серый фон (так было в окнах «Покупатели»/«Поставщики»
    после появления вертикальной прокрутки).
    """

    from PySide6.QtGui import QColor, QPalette

    from oplata.app import MainWindow
    from oplata.views.receipts_view import AmountPopup

    white = QColor("#ffffff")

    entries = [(f"id{index}", f"Контрагент {index}") for index in range(12)]
    popup = AmountPopup("Покупатели", entries, lambda values: None)
    popup.show()
    qt_app.processEvents()
    scroll = popup.scroll_area
    assert "background:#ffffff" in scroll.styleSheet()
    viewport = scroll.viewport()
    viewport.ensurePolished()
    assert viewport.palette().color(QPalette.ColorRole.Window) == white
    popup.close()

    # Недельные таблицы и таблица блока «Оплата» — с тем же скроллбаром.
    window = MainWindow(store)
    for table in (
        window.suppliers_view.table,
        window.suppliers_view.frozen_table,
        window.receipts_view.outgoing.table,
    ):
        table.ensurePolished()
        assert table.palette().color(QPalette.ColorRole.Window) == white
    window.close()


def test_journal_period_three_and_twelve_months(qt_app, store, monkeypatch):
    """v0.23.2: журнал поставщика — 3 месяца, покупателя — 12 месяцев."""

    from datetime import date

    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog
    from oplata.models import Counterparty, Operation
    from oplata.views import operations_view as operations_view_module

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 29)

    monkeypatch.setattr(operations_view_module, "date", _FixedDate)

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    # В демо-данных покупателей нет — заводим своего.
    customer = Counterparty(name="Покупатель Тест")
    store.data.customers.append(customer)
    # 2 месяца назад — попадает и в 3, и в 12; 5 месяцев — только в 12;
    # 13 месяцев — никуда.
    for months_ago in (2, 5, 13):
        day = operations_view_module._months_back(months_ago, _FixedDate.today())
        store.data.operations.append(
            Operation(
                kind="Приход",
                counterparty_id=supplier.id,
                date=day.isoformat(),
                due_date=day.isoformat(),
                amount="100.00",
            )
        )
        store.data.operations.append(
            Operation(
                kind="Реализация",
                counterparty_id=customer.id,
                date=day.isoformat(),
                amount="100.00",
            )
        )

    supplier_journal = OperationsJournalDialog(
        store, "supplier", counterparty_id=supplier.id
    )
    assert supplier_journal.view.period_months == 3
    assert supplier_journal.view.table.rowCount() == 1  # только 2 месяца назад
    supplier_journal.close()

    customer_journal = OperationsJournalDialog(
        store, "customer", counterparty_id=customer.id
    )
    assert customer_journal.view.period_months == 12
    assert customer_journal.view.table.rowCount() == 2  # 2 и 5 месяцев назад
    customer_journal.close()


def test_journals_are_same_width_and_centered(qt_app, store):
    """v0.23.9: журналы одной ширины, без горизонтального скроллинга,
    шапка и значения — по центру."""

    from PySide6.QtCore import Qt

    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog
    from oplata.models import Counterparty, Operation

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    customer = Counterparty(name="Покупатель Тест")
    store.data.customers.append(customer)
    for counterparty in (supplier, customer):
        store.data.operations.append(
            Operation(
                kind="Приход",
                counterparty_id=counterparty.id,
                date="2026-10-20",
                due_date="2026-11-03",
                amount="100.00",
            )
        )

    journals = [
        OperationsJournalDialog(store, kind, counterparty_id=person.id)
        for kind, person in (("supplier", supplier), ("customer", customer))
    ]
    # Оба журнала одной ширины (v0.23.9).
    assert journals[0].width() == journals[1].width()
    for journal in journals:
        table = journal.view.table
        header = table.horizontalHeader()
        # Горизонтального скроллинга нет, колонки занимают всю ширину.
        assert (
            table.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert sum(
            header.sectionSize(column) for column in range(table.columnCount())
        ) == table.viewport().width()
        # Шапка и значения — по центру.
        assert header.defaultAlignment() & Qt.AlignmentFlag.AlignHCenter
        for column in range(table.columnCount()):
            assert table.item(0, column).textAlignment() & (
                Qt.AlignmentFlag.AlignHCenter
            ), column
        journal.close()


def test_summary_panel_font_is_larger_others_unchanged(qt_app, store):
    """v0.23.6: шрифт увеличен только в панели «Сводка»."""

    from oplata import styles
    from oplata.app import MainWindow

    window = MainWindow(store)
    summary = window.summary_view
    # Кегли блоков «Сводки» — на 50% крупнее обычных.
    assert summary.panel.toggle.font().pixelSize() == styles.fs_summary(15)
    caption = next(iter(summary._captions.values()))
    value = next(iter(summary._values.values()))
    assert caption.font().pixelSize() == styles.fs_summary(12)
    assert value.font().pixelSize() == styles.fs_summary(12)
    assert styles.fs_summary(12) == int(round(12 * styles.FONT_SCALE * 1.5))

    # Остальная программа осталась при обычном шрифте (общее +50% из
    # v0.23.2 отменено).
    assert styles.FONT_SCALE == 1.3
    assert window.suppliers_view.week_label.font().pixelSize() == styles.fs(13)
    window.close()


def test_money_block_paints_weekends_and_today(qt_app, store, monkeypatch):
    """v0.23.3: столбец выходного — розовый, сегодняшняя дата — салатовая."""

    from datetime import date

    from PySide6.QtGui import QColor

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.views import receipts_view as receipts_view_module

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 29)

    monkeypatch.setattr(receipts_view_module, "date", _FixedDate)

    window = MainWindow(store)
    block = window.receipts_view.incoming
    block.set_date(date(2026, 10, 26))  # неделя 26.10 — 31.10
    green = QColor(styles.WEEK_TABLE_TODAY_GREEN)
    assert _FixedDate.today() in block.days
    pink = QColor(styles.WEEK_TABLE_WEEKEND_PINK)
    # Фон секции рисует сама шапка: в Windows BackgroundRole ячейки
    # шапки Qt игнорирует (v0.23.7).
    assert isinstance(block.day_header, receipts_view_module.DayHeaderView)
    for index, day in enumerate(block.days):
        column = receipts_view_module.FIRST_DAY_COL + index
        is_weekend = day.weekday() >= 5
        color = block.day_header.section_color(column)
        if day == _FixedDate.today():
            assert color == green, day
        else:
            assert (color == pink) == is_weekend, day
        # Розовый красит весь столбец, а не только шапку.
        for row in range(block.table.rowCount()):
            item = block.table.item(row, column)
            if item is None:
                continue
            assert (item.background().color() == pink) == is_weekend, (row, day)
    # Сетка таблицы (вертикальная и горизонтальная разметка) на месте
    # в обоих блоках: стиль с gridline-color задаётся каждой таблице
    # отдельно, иначе в «Поступлении» сетка не рисовалась (v0.23.8).
    outgoing = window.receipts_view.outgoing
    for table in (block.table, outgoing.table):
        assert table.showGrid() is True
        assert f"gridline-color:{styles.TABLE_GRID}" in table.styleSheet()
        assert f"border:1px solid {styles.BORDER}" in table.styleSheet()


def test_payment_card_calendar_marks_dates(qt_app, store):
    """v0.24.0: в карточке нового вида оплаты есть календарь — клик по
    дате отмечает её, повторный клик снимает отметку."""

    from datetime import date

    from PySide6.QtWidgets import QPushButton

    from oplata.models import OUTGOING
    from oplata.views.receipts_view import (
        OtherPaymentCardDialog,
        PaymentDatesCalendar,
    )

    calendar = PaymentDatesCalendar(date(2026, 10, 29))
    buttons = calendar.findChildren(QPushButton)
    # Кнопки дней есть, месяц открыт на октябре 2026.
    assert calendar.month_label.text() == "Октябрь 2026"
    for day_number in ("27", "30"):
        for button in buttons:
            if button.text() == day_number:
                button.click()
                break
    assert calendar.marked_dates() == ["2026-10-27", "2026-10-30"]
    # Повторный клик снимает отметку.
    for button in buttons:
        if button.text() == "27":
            button.click()
            break
    assert calendar.marked_dates() == ["2026-10-30"]

    # Карточка отдаёт отмеченные даты наружу.
    dialog = OtherPaymentCardDialog(OUTGOING, set(), date(2026, 10, 29))
    assert dialog.marked_dates == []
    dialog.calendar._marked.add("2026-11-02")
    assert dialog.marked_dates == ["2026-11-02"]
    dialog.calendar.deleteLater()


def test_marked_dates_of_payment_paint_cells_red(qt_app, store):
    """v0.24.0: даты, отмеченные в карточке вида оплаты, — красные
    ячейки в таблице блока «Оплата»."""

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment
    from oplata.views.receipts_view import FIRST_DAY_COL

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    block = view.outgoing
    marked = [block.days[0].isoformat(), block.days[1].isoformat()]
    add_other_payment(
        store.data,
        "Тестовая оплата",
        block.current_date().isoformat(),
        OUTGOING,
        dates=marked,
    )
    view.refresh_rows()
    view.refresh()
    qt_app.processEvents()

    row = block.row_keys.index("other:Тестовая оплата")
    red = styles.WEEK_TABLE_MARKED_RED
    for index, day in enumerate(block.days):
        item = block.table.item(row, FIRST_DAY_COL + index)
        assert item is not None
        color = item.background().color().name()
        if day.isoformat() in marked:
            assert color == red, day
        elif day.weekday() >= 5:
            assert color == styles.WEEK_TABLE_WEEKEND_PINK, day
        else:
            assert color != red, day
    window.close()


def test_money_block_first_column_left_others_centered(qt_app, store):
    """v0.23.9/v0.24.2: в таблицах «Поступление» и «Оплата» шапка и
    значения — по центру, кроме первого столбца: названия — слева."""

    from PySide6.QtCore import Qt

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_NAME

    window = MainWindow(store)
    center = Qt.AlignmentFlag.AlignHCenter
    left = Qt.AlignmentFlag.AlignLeft
    for block in (window.receipts_view.incoming, window.receipts_view.outgoing):
        table = block.table
        header = table.horizontalHeader()
        assert header.defaultAlignment() & center
        for column in range(table.columnCount()):
            expected = left if column == COL_NAME else center
            header_item = table.horizontalHeaderItem(column)
            assert header_item is not None
            assert header_item.textAlignment() & expected, column
            for row in range(table.rowCount()):
                item = table.item(row, column)
                if item is not None:
                    assert item.textAlignment() & expected, (row, column)
    window.close()
    window.close()


def test_supplier_journal_shows_delivery_columns(qt_app, store):
    """v0.23.1: журнал поставщика — дата поставки, сумма, отсрочка, оплата."""

    from datetime import date

    from PySide6.QtCore import QDate

    from oplata.dialogs.operation_dialog import OperationDialog
    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    dialog = OperationDialog(store, initial_counterparty_id=supplier.id)
    dialog.date_edit.setDate(QDate(2026, 10, 26))
    dialog.amount_edit.setText("1 000,50")
    dialog._save()
    dialog.close()

    # Оплата в журнал поставщика не попадает: это журнал поставок.
    payment = OperationDialog(
        store, initial_counterparty_id=supplier.id, initial_kind="Оплата"
    )
    payment.date_edit.setDate(QDate(2026, 10, 28))
    payment.amount_edit.setText("200")
    payment._save()
    payment.close()

    window = OperationsJournalDialog(store, "supplier", counterparty_id=supplier.id)
    table = window.view.table
    assert table.rowCount() == 1
    assert [
        table.item(0, col).text() for col in range(table.columnCount())
    ] == ["26.10.2026", "1 000,50", "10", "05.11.2026"]

    # Журнал поставщика — той же ширины, что и журнал покупателя
    # (v0.23.9), и с широким скроллбаром (v0.23.2).
    assert window.width() == 800
    assert (
        table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )

    # У покупателя журнал без «Документа» и «Комментария»: 4 колонки.
    from oplata.views.operations_view import OperationsView

    cust_view = OperationsView(store, counterparty_kind="customer")
    assert cust_view.table.columnCount() == 4
    assert [
        cust_view.table.horizontalHeaderItem(col).text() for col in range(4)
    ] == ["Тип", "Контрагент", "Дата", "Сумма"]
    window.close()


def test_supplier_total_column_shows_whole_debt(qt_app, store, monkeypatch):
    """Столбец «Итого» — вся текущая задолженность поставщика (v0.22.1).

    «Итого» не зависит от показанной недели и от сегодняшней даты: это
    весь остаток долга по всем срокам. Просроченное лежит в столбце
    «просрочено» (срок раньше даты), а «за неделю» — сроки внутри
    показанной недели. Дата в тесте зафиксирована, иначе расчёт
    менялся бы вместе с календарём.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import add_delivery
    from oplata.views import week_view as week_view_module

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 27)

    monkeypatch.setattr(week_view_module, "date", _FixedDate)

    supplier = store.data.suppliers[0]
    # Срок 27.10 (700), 05.11 (900 — следующая неделя), 20.10 (150 —
    # просрочка). Всего задолженность 1 750.
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-10-27", Decimal("700"))
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-11-05", Decimal("900"))
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("150"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    row = 4  # первая строка контрагента
    table = view.table

    view.goto_date(date(2026, 10, 26))
    assert table.item(1, 1).text() == "ЗАДОЛЖЕННОСТЬ"
    assert table.item(2, 1).text() == "Итого"
    assert table.item(2, 2).text() == "просрочено"
    assert table.item(2, 3).text() == "за неделю"
    # Итого — 700 + 900 + 150; просрочка — 150 (срок 20.10); за неделю
    # (26.10–01.11) — 700.
    assert table.item(row, 1).text() == "1 750,00"
    assert table.item(row, 2).text() == "150,00"
    assert table.item(row, 3).text() == "700,00"

    # Следующая неделя: «Итого» то же, «за неделю» — срок 05.11.
    view.goto_date(date(2026, 10, 26) + timedelta(days=7))
    assert table.item(row, 1).text() == "1 750,00"
    assert table.item(row, 2).text() == "150,00"
    assert table.item(row, 3).text() == "900,00"

    # Наступил следующий день: просрочка выросла (150 + 700), «Итого»
    # не изменилось — это весь долг, а не только просроченный.
    class _NextDay(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 28)

    monkeypatch.setattr(week_view_module, "date", _NextDay)
    view.refresh()
    assert table.item(row, 1).text() == "1 750,00"
    assert table.item(row, 2).text() == "850,00"  # 150 + 700 просрочены
    window.close()


def test_supplier_payment_reduces_overdue_first_then_week(qt_app, store, monkeypatch):
    """Оплата гасит сначала просрочку, потом долг недели.

    Сроки в тесте фиксированные (20.10 и 29.10), поэтому «сегодня»
    закрепляем на 27.10: иначе расчёт менялся бы вместе с календарём.
    Столбец «Итого» (v0.22.1) показывает весь остаток долга
    поставщику: до оплаты 1 200, после оплаты 900 — 300.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from datetime import date as real_date

    from oplata.app import MainWindow
    from oplata.models import add_delivery, set_daily_operation
    from oplata.views import week_view as week_view_module

    class _FixedDate(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 10, 27)

    monkeypatch.setattr(week_view_module, "date", _FixedDate)

    supplier = store.data.suppliers[0]
    # Просрочка 700 (срок 20.10) + долг недели 500 (срок 29.10).
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("700"))
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-29", Decimal("500"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    row = 4
    table = view.table
    view.goto_date(date(2026, 10, 26))

    assert table.item(row, 1).text() == "1 200,00"  # Итого (700 + 500)
    assert table.item(row, 2).text() == "700,00"  # просрочено
    assert table.item(row, 3).text() == "500,00"  # за неделю

    # Оплата 900: сначала гасит просрочку 700, остаток 200 — неделю.
    set_daily_operation(
        store.data, supplier.id, "2026-10-27", "Оплата", Decimal("900")
    )
    view.refresh()
    assert table.item(row, 1).text() == "300,00"  # Итого: 1 200 − 900
    assert table.item(row, 2).text() == ""  # просрочка погашена
    assert table.item(row, 3).text() == "300,00"  # 500 − 200

    # Недоплата: оплата 100 вместо 900 — остаток просрочки переносится.
    store.data.operations[:] = [
        op for op in store.data.operations if op.kind != "Оплата"
    ]
    set_daily_operation(
        store.data, supplier.id, "2026-10-20", "Оплата", Decimal("100")
    )
    view.refresh()
    assert table.item(row, 1).text() == "1 100,00"  # Итого: 1 200 − 100
    assert table.item(row, 2).text() == "600,00"  # 700 − 100
    assert table.item(row, 3).text() == "500,00"  # оплата ушла в просрочку
    # Следующая неделя (02.11–08.11): просрочка всё та же 600, а долг со
    # сроком 29.10 в эту неделю не попадает («за неделю» пусто).
    view.goto_date(date(2026, 10, 26) + timedelta(days=7))
    assert table.item(row, 1).text() == "1 100,00"
    assert table.item(row, 2).text() == "600,00"
    assert table.item(row, 3).text() == ""
    window.close()


def test_supplier_with_realization_never_shows_overdue(qt_app, store):
    """Поставщик «Реализация»: срока нет, «просрочено» пусто, «Итого» — долг."""
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.suppliers.append(Counterparty(name="Реализация", realization=True))
    store.save()
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table
    names = [s.name for s in view.counterparties()]
    row = 4 + names.index("Реализация")

    from oplata.models import add_delivery

    partner = view.counterparties()[names.index("Реализация")]
    # Поставка со сроком в прошлом — но у «Реализации» срока нет.
    add_delivery(store.data, partner.id, "2026-10-01", "2026-10-05", Decimal("400"))
    view.refresh()
    # v0.22.1: «Итого» — вся задолженность, «просрочено» у «Реализации»
    # не бывает, срок 05.10 вне недели 26.10–01.11.
    assert table.item(row, 1).text() == "400,00"
    assert table.item(row, 2).text() == ""
    assert table.item(row, 3).text() == ""
    window.close()


def test_week_table_fonts_are_uniform(qt_app, store):
    """v0.16.0: в недельных таблицах один размер шрифта (был разнобой)."""
    from oplata import styles
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    expected = styles.fs(12)
    for view in (window.suppliers_view, window.customers_view):
        table = view.table
        sizes = set()
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is not None and item.text():
                    sizes.add(item.font().pixelSize())
        assert sizes == {expected}, f"разнобой шрифтов: {sorted(sizes)}"
        # Заголовок недели и итог справа — одного размера.
        # v0.23.1: шрифт заголовка «Неделя: …» — как у кнопки «Сегодня»
        # (просьба пользователя): базовый кегль и начертание кнопки из
        # APP_STYLESHEET (fs(13) = 17px, weight 600), без отдельного
        # увеличения до fs(14).
        assert view.week_label.font().pixelSize() == styles.fs(13)
        assert view.week_label.font().weight() == 600
        assert view.right_value_label.font().pixelSize() == styles.fs(14)
    window.close()


def test_base_switch_button_changes_database_and_header(qt_app, store, monkeypatch, tmp_path):
    """v0.17.0: кнопка МИР/КОРЕЛИЧИ — две не связанные базы."""
    from oplata import storage, styles
    from oplata.app import MainWindow
    from oplata.models import Counterparty

    # Данные теста целиком во временном каталоге: переключение базы не
    # должно трогать настоящий каталог данных пользователя.
    monkeypatch.setattr(storage, "data_dir", lambda: tmp_path)

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    # Кнопка стоит в шапке и вдвое крупнее вкладок («Конфигурация»).
    nav_font = window.nav_buttons[-1].font().pixelSize()
    assert window.base_button.text() == storage.BASE_TITLES[store.base_key]
    assert window.base_button.font().pixelSize() == nav_font * 2

    mir_path = store.path
    store.data.suppliers.append(Counterparty(name="Поставщик МИР"))
    store.save()

    window.base_button.click()
    qt_app.processEvents()
    assert window.base_button.text() == "КОРЕЛИЧИ"
    assert styles.MAROON in window.header.styleSheet()  # шапка вишнёвая
    assert store.path == tmp_path / "data-korelichi.json"
    assert not any(s.name == "Поставщик МИР" for s in store.data.suppliers)
    # Выбор базы запоминается для следующего запуска.
    assert storage.load_active_base() == storage.BASE_KORELICHI

    window.base_button.click()
    qt_app.processEvents()
    assert window.base_button.text() == "МИР"
    assert styles.NAVY in window.header.styleSheet()  # шапка снова синяя
    assert store.path == mir_path
    assert any(s.name == "Поставщик МИР" for s in store.data.suppliers)
    # Таблица поставщиков перерисована по активной базе.
    names = [
        window.suppliers_view.table.item(row, 0).text()
        for row in range(4, window.suppliers_view.table.rowCount())
    ]
    assert "Поставщик МИР" in names
    window.close()


def test_user_rights_limit_pages_base_and_input(qt_app, store, monkeypatch, tmp_path):
    """v0.18.0: права пользователя — вкладки, база, ввод данных."""
    from oplata import access as access_module
    from oplata.app import MainWindow

    # Файлы пользователей теста — во временном каталоге.
    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)

    admin, manager, user_mir, user_kor = access_module.default_users()

    window = MainWindow(store, access_module.Access(admin))
    window.show()
    qt_app.processEvents()
    assert [b.text() for b in window.nav_buttons if not b.isHidden()] == [
        "Поступление и оплаты",
        "Поставщики",
        "Покупатели",
        "Конфигурация",
    ]
    assert window.suppliers_view.read_only is False
    assert not window.data_view.admin_panel.isHidden()
    window.close()

    window = MainWindow(store, access_module.Access(manager))
    window.show()
    qt_app.processEvents()
    # Руководитель видит все вкладки, но без права ввода.
    assert all(not b.isHidden() for b in window.nav_buttons)
    assert window.suppliers_view.read_only is True
    assert window.receipts_view.incoming.read_only is True
    # v0.23.1: «Администрирование» видно всем, кто открыл вкладку
    # «Конфигурация» — сама вкладка закрыта паролем (просьба
    # пользователя: кнопка «Очистить базу» должна быть доступна).
    assert not window.data_view.admin_panel.isHidden()
    window.close()

    window = MainWindow(store, access_module.Access(user_mir))
    window.show()
    qt_app.processEvents()
    # Пользователь МИР: только «Покупатели», база МИР, только просмотр.
    assert [b.text() for b in window.nav_buttons if not b.isHidden()] == ["Покупатели"]
    assert window.base_button.isHidden()
    assert store.base_key == "mir"
    assert window.customers_view.read_only is True
    window.close()

    window = MainWindow(store, access_module.Access(user_kor))
    window.show()
    qt_app.processEvents()
    assert store.base_key == "korelichi"
    assert [b.text() for b in window.nav_buttons if not b.isHidden()] == ["Покупатели"]
    window.close()


def test_users_are_listed_in_configuration_and_password_gate(
    qt_app, store, monkeypatch, tmp_path
):
    """v0.18.0: четыре пользователя в «Конфигурации» и пароль на раздел."""
    from oplata import access as access_module
    from oplata.app import MainWindow

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)

    window = MainWindow(store, access_module.Access(access_module.default_users()[0]))
    window.show()
    qt_app.processEvents()

    # Плашка свёрнута — раскрываем, иначе её кнопки невидимы.
    window.data_view.users_panel.set_expanded(True)
    table = window.data_view.users_table
    assert table.rowCount() == 4
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert names == [
        "Администратор",
        "Руководитель",
        "Пользователь МИР",
        "Пользователь КОРЕЛИЧИ",
    ]
    # У администратора отмечены все вкладки и право ввода.
    assert table.item(0, 1).checkState() is not None
    # Вкладка «Конфигурация» не открыта, поэтому проверяем не «видна ли
    # кнопка», а «не скрыта ли она правами».
    assert not window.data_view.users_save_btn.isHidden()

    # Пароль: придумываем, проверяем верный и неверный.
    assert not access_module.has_password()
    access_module.set_password("1234")
    assert access_module.has_password()
    assert access_module.check_password("1234")
    assert not access_module.check_password("0000")

    # Добавление пользователя и сохранение списка.
    window.data_view.users_add_btn.click()
    qt_app.processEvents()
    assert table.rowCount() == 5
    table.item(4, 0).setText("Кладовщик")
    window.data_view.users_save_btn.click()
    qt_app.processEvents()
    assert "Кладовщик" in [user.name for user in access_module.load_users()]
    window.close()


def test_amount_font_is_chosen_to_fit_day_column(qt_app, store):
    """v0.18.0: «88 888,88» влезает в колонку дня при любой её ширине."""
    from PySide6.QtGui import QFont, QFontMetrics

    from oplata.app import MainWindow
    from oplata.views.week_view import MAX_AMOUNT_TEXT, TABLE_DAYS

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    view = window.suppliers_view
    # Воскресенье из таблицы убрали: дней шесть.
    assert view.table_days == TABLE_DAYS == 6
    assert view.table.columnCount() == (
        1 + view.lead_columns + TABLE_DAYS * view.columns_per_day
    )

    def advance_for(pixel_size: int) -> int:
        font = QFont(view.table.font())
        font.setPixelSize(pixel_size)
        font.setBold(True)
        return QFontMetrics(font).horizontalAdvance(MAX_AMOUNT_TEXT)

    # Подбор идёт по самой узкой колонке дня (в v0.18.0 и ранее бралась
    # ширина итоговой колонки, из-за чего сумма вылезала за ячейку).
    captured: list[int] = []
    original = view._fit_amount_font
    view._fit_amount_font = lambda width: captured.append(width)
    try:
        view._fit_columns_to_width()
    finally:
        view._fit_amount_font = original
    assert captured, "подгонка шрифта не вызывалась"
    day_widths = [
        view.table.columnWidth(view._day_column(day, sub))
        for day in range(view.table_days)
        for sub in range(view.columns_per_day)
    ]
    assert captured[-1] == min(day_widths)

    # Метрики шрифта в headless-среде без системных шрифтов могут быть
    # недоступны — тогда проверку ширины текста пропускаем.
    if advance_for(8) > 60 - 8:
        window.close()
        return

    for width in (60, 72, 80, 100, 160):
        view._fit_amount_font(width)
        item = view._amount_items[0] if view._amount_items else None
        assert item is not None
        advance = advance_for(item.font().pixelSize())
        assert advance <= width - 8, (
            f"колонка {width} px: «{MAX_AMOUNT_TEXT}» занимает {advance} px"
        )
    window.close()


def test_clear_database_confirms_and_exports(qt_app, store, monkeypatch, tmp_path):
    """v0.23.1: «Очистить базу» — подтверждение, копии в «Загрузки»."""

    from pathlib import Path

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QMessageBox

    from oplata.models import Operation
    from oplata.views.data_view import DataView

    view = DataView(store, on_calendar_saved=lambda: None)
    # Настоящий путь — внутри папки «Загрузки» системной учётной записи.
    assert DataView.BACKUP_DIR_NAME == "База поставщики оплата"
    real_dir = view._backup_dir()
    downloads = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DownloadLocation
    )
    assert real_dir.name == "База поставщики оплата"
    assert not downloads or real_dir.parent == Path(downloads)
    # Дальше копии ищем в подменённой папке: путь к «Загрузкам» на
    # машине сборки другой, а проверять надо сам факт копий.
    backup_dir = tmp_path / "Загрузки" / "База поставщики оплата"
    monkeypatch.setattr(DataView, "_backup_dir", lambda self: backup_dir)

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    store.data.operations.append(
        Operation(kind="Приход", counterparty_id=supplier.id)
    )
    store.save()

    # «Нет» — база цела, копий нет.
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
    view._clear_database()
    assert store.data.suppliers and store.data.operations
    assert not backup_dir.exists()

    # «Да» — выгрузка в Excel и копия data.json лежат в папке Загрузок,
    # база пуста.
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    view._clear_database()
    assert list(backup_dir.glob("oplata-базы-*.xlsx"))
    assert list(backup_dir.glob("data-backup-*.json"))
    assert store.data.suppliers == []
    assert store.data.customers == []
    assert store.data.operations == []
    assert store.data.cash_receipts == []
    assert store.data.other_payments == []
    assert store.data.day_overrides == {}
    assert "База очищена" in view.admin_status_label.text()

    # Кнопка видна любому пользователю: вкладка «Конфигурация» за паролем.
    # (isHidden, а не isVisible: само окно в тесте не показано.)
    view.set_read_only(True, can_admin=False)
    assert not view.admin_panel.isHidden()
    assert not view.clear_button.isHidden()
    view.close()


def test_base_exchange_buttons_are_admin_only(qt_app, store):
    """«Выгрузить базы»/«Загрузить базы» видит только администратор."""
    from oplata.access import ALL_PAGES, Access, User, save_users
    from oplata.app import MainWindow

    admin = User(
        id="admin",
        name="Администратор",
        pages=list(ALL_PAGES),
        can_edit=True,
        can_switch_base=True,
    )
    viewer = User(id="viewer", name="Наблюдатель", pages=[0])
    save_users([admin, viewer])

    window = MainWindow(store, access=Access(admin))
    window.show()  # без показа isVisible() у кнопок был бы False
    qt_app.processEvents()
    assert window.export_button.isVisible()
    assert window.import_button.isVisible()
    # Кнопки работают с обеими базами сразу — подписи во множественном числе.
    assert window.export_button.text() == "Выгрузить базы"
    assert window.import_button.text() == "Загрузить базы"

    # Сменили текущего пользователя в «Конфигурации» — кнопки скрылись.
    window.apply_current_user(viewer)
    assert not window.export_button.isVisible()
    assert not window.import_button.isVisible()

    window.apply_current_user(admin)
    assert window.export_button.isVisible()
    assert window.import_button.isVisible()
    window.close()


def test_base_exchange_export_and_import_both_bases(
    qt_app, store, tmp_path, monkeypatch
):
    """Кнопки шапки: обе базы — в один Excel и обратно (v0.20.0)."""
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    window = MainWindow(store)
    window._config_unlocked = True
    # Данные только этого прогона: пустая база с одной записью.
    store.data.suppliers = [
        Counterparty(name="Тестовый поставщик", deferment_days=7)
    ]
    store.save()

    xlsx = tmp_path / "bases.xlsx"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(xlsx), "")),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(xlsx), "")),
    )
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: messages.append(str(args[-1]))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: messages.append("ОШИБКА: " + str(args[-1]))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )

    window.export_bases_to_excel()
    assert xlsx.exists()
    names = load_workbook(xlsx).sheetnames
    assert any(name.startswith("МИР.") for name in names)
    assert any(name.startswith("КОРЕЛИЧИ.") for name in names)

    # Стираем данные активной базы и загружаем их обратно из файла.
    store.data.suppliers = []
    window.import_bases_from_excel()
    assert [item.name for item in store.data.suppliers] == ["Тестовый поставщик"]
    assert store.data.suppliers[0].deferment_days == 7
    assert not any(message.startswith("ОШИБКА") for message in messages)
    window.close()


def test_receipts_source_row_click_opens_journal(qt_app, store):
    """Клик по виду поступления открывает журнал операций (v0.20.0)."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import CASH_SOURCE, CashReceipt
    from oplata.views.receipts_view import COL_NAME
    from oplata.views.receipts_view import JournalPopup

    window = MainWindow(store)
    view = window.receipts_view
    view.incoming.set_date(date(2026, 10, 27))
    store.data.cash_receipts = [
        CashReceipt(
            date="2026-10-27",
            source=CASH_SOURCE,
            amount="1000.00",
            comment="прогон",
        )
    ]
    store.save()
    view.refresh()

    row = view.incoming.row_keys.index("cash")
    # Клик по названию вида — журнал; клик по «Сумме» остаётся вводом.
    view.incoming.table.cellClicked.emit(row, COL_NAME)
    # События не прокручиваем: всплывающее окно закрылось бы «кликом вне»
    # от хвоста событий предыдущего теста.
    popup = view._popup
    assert isinstance(popup, JournalPopup)
    assert popup.table.item(0, 0).text() == "27.10.2026"
    assert popup.table.item(0, 1).text() == "1 000,00"
    assert popup.table.item(0, 2).text() == "прогон"
    popup.close()
    window.close()


def test_other_payment_card_asks_name_only(qt_app, monkeypatch):
    """Карточка нового вида оплаты: название, календарь и «Сохранить».

    Полей «Дата» и «Сумма» в карточке нет: дата берётся из блока
    «Оплата», сумма вводится в ячейку таблицы. С v0.24.1 в карточке
    есть календарь: отмеченные даты красят ячейки красным, поэтому
    окно больше не делаем вдвое шире содержимого — иначе на ноутбуке
    карточка не влезала в экран (v0.24.2).
    """
    from datetime import date

    from PySide6.QtWidgets import QApplication, QDialog

    from oplata.views.receipts_view import OtherPaymentCardDialog

    card = OtherPaymentCardDialog("out", {"Налоги"}, date(2026, 10, 27))
    # В карточке одно поле ввода — название; даты и суммы нет.
    assert not hasattr(card, "date_edit")
    assert not hasattr(card, "amount_edit")
    # Календарь есть и он не шире экрана (v0.24.2).
    assert card.calendar is not None
    assert card.width() <= max(QApplication.primaryScreen().availableGeometry().width() - 80, 420)

    card.name_edit.setText("Аренда")
    card._try_accept()
    assert card.result() == QDialog.DialogCode.Accepted
    assert card.name_value == "Аренда"
    card.close()

    # Пустое имя и занятое имя не сохраняются.
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *args, **kwargs: None)
    )
    empty = OtherPaymentCardDialog("out", set(), date(2026, 10, 27))
    empty._try_accept()
    assert not empty.isVisible() or empty.result() != QDialog.DialogCode.Accepted
    empty.close()

    busy = OtherPaymentCardDialog("out", {"Аренда"}, date(2026, 10, 27))
    busy.name_edit.setText("аренда")  # занято без учёта регистра
    busy._try_accept()
    assert busy.result() != QDialog.DialogCode.Accepted
    busy.close()


def test_users_table_current_column_applies_user(qt_app, store):
    """Столбец «Текущий»: выбор пользователя применяется сразу (v0.20.0)."""
    from oplata.access import (
        ALL_PAGES,
        IDX_CUSTOMERS,
        Access,
        User,
        load_current_user,
        save_current_user,
        save_users,
    )
    from oplata.app import MainWindow
    from oplata.storage import BASE_MIR

    admin = User(
        id="admin",
        name="Администратор",
        pages=list(ALL_PAGES),
        can_edit=True,
        can_switch_base=True,
    )
    user_mir = User(
        id="mir", name="Пользователь МИР", pages=[IDX_CUSTOMERS], base=BASE_MIR
    )
    save_users([admin, user_mir])
    save_current_user(admin)

    window = MainWindow(store, access=Access(admin))
    window._config_unlocked = True
    table = window.data_view.users_table
    headers = [
        table.horizontalHeaderItem(col).text() for col in range(table.columnCount())
    ]
    assert headers[-1] == "Текущий"
    current_col = window.data_view.users_col_current
    # Галочка стоит у текущего пользователя (администратора).
    assert (
        table.item(0, current_col).checkState() == Qt.CheckState.Checked
    )
    assert table.item(1, current_col).checkState() == Qt.CheckState.Unchecked

    # Отмечаем второго — галочка переезжает, и права применяются сразу.
    table.item(1, current_col).setCheckState(Qt.CheckState.Checked)
    assert table.item(0, current_col).checkState() == Qt.CheckState.Unchecked
    window.data_view._save_users()

    assert load_current_user().name == "Пользователь МИР"
    assert window.access.title == "Пользователь МИР"
    # У пользователя без права ввода кнопок выгрузки быть не должно.
    assert not window.export_button.isVisible()
    assert not window.nav_buttons[1].isVisible()  # вкладка «Поставщики»
    window.close()

