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
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def store(tmp_path):
    from oplata.storage import DataStore

    return DataStore(tmp_path / "data.json")


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
    assert [view.incoming.table.horizontalHeaderItem(c).text() for c in range(6)] == [
        "",
        "Сумма",
        "Неделя",
        "Месяц",
        "Квартал",
        "Год",
    ]
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
        window.show_page(index)
        assert window.stack.currentIndex() == index
    # Сводка — построчная плашка из трёх блоков (v0.14.0)
    summary = window.summary_view
    assert summary.caption_text("funds") == "Остаток на счету"
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
    assert bg_name(FIRST_DATA_ROW, _DAY(view, 6, 0)) == styles.WEEK_TABLE_WEEKEND_PINK
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
    dialog = OperationsJournalDialog(store, "supplier", counterparty_id=supplier.id)
    journal = dialog.view
    assert journal.found_label.text() == "Найдено: 2"  # поставка + оплата
    journal.kind_filter.setCurrentIndex(0)  # все типы, без фиксированного
    journal.supplier_filter.setCurrentIndex(0)
    journal._select_operation_row(payment_op.id)
    assert journal.selected_operation_id() == payment_op.id
    assert journal.edit_btn.isEnabled() and journal.delete_btn.isEnabled()

    journal.date_from.setText("28.10.2026")  # операции октября — вне диапазона
    assert journal.found_label.text() == "Найдено: 0"
    journal.reset_filters()
    assert journal.found_label.text() == "Найдено: 2"

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


def test_summary_print_snapshot_and_pdf_render(qt_app, store, tmp_path):
    """Печать «Сводки» берёт экранный снимок и реально рисуется в PDF."""
    from datetime import datetime

    from decimal import Decimal

    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtPrintSupport import QPrinter

    from oplata.app import MainWindow
    from oplata.models import add_delivery

    window = MainWindow(store)
    view = window.summary_view
    assert view.print_button.text() == "🖨  Печать"

    # Поставка со сроком 20.10 попадает в задолженность поставщика.
    archived = store.data.suppliers[0]
    add_delivery(
        store.data, archived.id, "2026-10-10", "2026-10-20", Decimal("1000")
    )
    view.refresh()
    hidden_total = view.value_text("debt_total")
    assert hidden_total == "1 000,00"
    assert view.value_text("debt_overdue") == "1 000,00"
    hidden_html = view.build_print_html(datetime(2026, 10, 27, 12, 0))
    assert "Архивные поставщики: скрыты" in hidden_html
    assert "Задолженность поставщикам" in hidden_html
    assert "Средства" in hidden_html
    assert "Месяц" in hidden_html

    archived.archived = True
    view.show_archived.setChecked(True)  # сигнал сам вызовет refresh()
    shown_html = view.build_print_html(datetime(2026, 10, 27, 12, 0))
    assert view.value_text("debt_total") == hidden_total
    assert "Архивные поставщики: показаны" in shown_html
    assert "1 000,00" in shown_html

    document = view.create_print_document(datetime(2026, 10, 27, 12, 0))
    printer = view.configure_print_printer(
        QPrinter(QPrinter.PrinterMode.HighResolution)
    )
    assert printer.pageLayout().orientation() == QPageLayout.Orientation.Portrait
    assert printer.pageLayout().pageSize().id() == QPageSize.PageSizeId.A4
    pdf_path = tmp_path / "summary.pdf"
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(pdf_path))

    view.render_print_document(printer, document)
    assert document.pageCount() >= 1
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert pdf_path.stat().st_size > 1000
    window.close()


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
    view._rebuild_table(view._partners, view._days, today)
    table = view.table

    # v0.15.0: у покупателей нет столбца «просрочено» — 1 имя + 1 итог
    # («Всего») + 7 дней * 2 подколонки.
    assert table.columnCount() == 16
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
    """Таблица «Поступление»: ввод в ячейку и нарастающие итоги."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.views.receipts_view import (
        COL_AMOUNT,
        COL_MONTH,
        COL_WEEK,
        COL_YEAR,
    )

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

    # Нарастающий итог: неделя (с Пн 26.10), месяц, год — по выбранную дату.
    incoming.table.item(0, COL_AMOUNT).setText("1 000")
    assert incoming.amount_text("cash", COL_AMOUNT) == "1 000,00"
    assert incoming.amount_text("cash", COL_WEEK) == "1 000,00"
    assert incoming.amount_text("cash", COL_MONTH) == "1 000,00"
    assert incoming.amount_text("cash", COL_YEAR) == "1 000,00"

    # Сумма предыдущего дня этой же недели входит в итог недели.
    view.incoming.set_date(date(2026, 10, 26))
    view.incoming.table.item(0, COL_AMOUNT).setText("500")
    assert view.incoming.amount_text("cash", COL_AMOUNT) == "500,00"
    view.incoming.set_date(date(2026, 10, 27))
    assert view.incoming.amount_text("cash", COL_AMOUNT) == "1 000,00"
    assert view.incoming.amount_text("cash", COL_WEEK) == "1 500,00"
    assert view.incoming.amount_text("cash", COL_MONTH) == "1 500,00"
    assert view.incoming.amount_text("cash", COL_YEAR) == "1 500,00"
    assert view.incoming.amount_text("card", COL_WEEK) == "2 000,00"

    # Пустая ячейка убирает запись дня (только по своему источнику).
    view.incoming.table.item(0, COL_AMOUNT).setText("")
    assert view.incoming.amount_text("cash") == ""
    assert [(r.source, r.date) for r in store.data.cash_receipts] == [
        ("card", "2026-10-27"),
        ("cash", "2026-10-26"),
    ]
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
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    popup = view.open_popup("customers", row=2)
    assert popup is not None
    assert list(popup.edits) == [first.id, second.id]
    # Окно всплывает справа от таблицы, а не под строкой.
    table_right = view.incoming.table.mapToGlobal(
        view.incoming.table.rect().topRight()
    ).x()
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
    COL_MONTH,
    COL_NAME,
    COL_WEEK,
    COL_YEAR,
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


def test_other_payment_rows_add_rename_and_remove(qt_app, store, monkeypatch):
    """Блок «Оплата»: свои виды оплат, кнопки «+» и «−»."""
    from datetime import date

    from oplata.app import MainWindow

    window = MainWindow(store)
    view = window.receipts_view
    view.outgoing.set_date(date(2026, 10, 27))
    assert view.outgoing.row_keys == ["suppliers"]  # пока только поставщики

    view.add_other_row()
    assert view.outgoing.row_keys == ["suppliers", "other:Новый вид оплаты"]

    # Название вида оплаты вводится текстом прямо в ячейку.
    name_item = view.outgoing.table.item(1, 0)
    assert name_item.flags() & Qt.ItemFlag.ItemIsEditable
    name_item.setText("Аренда")
    assert view.outgoing.row_keys == ["suppliers", "other:Аренда"]

    # Сумма — прямо в ячейку, итоги считаются по названию.
    view.outgoing.table.item(1, COL_AMOUNT).setText("12 000")
    assert view.outgoing.amount_text("other:Аренда") == "12 000,00"
    assert view.outgoing.amount_text("other:Аренда", COL_WEEK) == "12 000,00"
    assert view.outgoing.amount_text("other:Аренда", COL_YEAR) == "12 000,00"
    assert [
        (p.name, p.date, p.amount, p.direction)
        for p in store.data.other_payments
        if p.name == "Аренда"
    ] == [("Аренда", "2026-10-27", "12000.00", "out")]

    # Вторая строка и удаление первой (кнопка «−»).
    view.add_other_row()
    assert len(view.outgoing.row_keys) == 3
    view.outgoing.table.setCurrentCell(1, 0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    view.remove_other_row("other:Аренда")
    assert view.outgoing.row_keys == [
        "suppliers",
        "other:Новый вид оплаты",
    ]
    assert [
        p.name
        for p in store.data.other_payments
        if getattr(p, "direction", "out") == "out"
    ] == ["Новый вид оплаты"]

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


def test_incoming_block_has_fixed_other_income_row(qt_app, store):
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


def test_tables_edit_on_single_click_and_numbers_right_aligned(qt_app, store):
    """Ввод — по одному клику; числа во всех таблицах — по правому краю."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_MONTH, COL_WEEK, COL_YEAR

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

    for table in (
        view.incoming.table,
        view.outgoing.table,
        window.suppliers_view.table,
        window.customers_view.table,
    ):
        assert table.editTriggers() == QAbstractItemView.EditTrigger.AllEditTriggers

    for column in (COL_AMOUNT, COL_WEEK, COL_MONTH, COL_YEAR):
        assert view.incoming.table.item(0, column).textAlignment() == right
        assert (
            view.incoming.table.horizontalHeaderItem(column).textAlignment()
            == right
        )
    window.close()


def test_money_block_scrolls_long_row_list(qt_app, store):
    """Длинный список своих строк прокручивается, блок не растёт дальше."""
    from oplata.app import MainWindow
    from oplata.models import OUTGOING

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
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


def test_quarter_column_and_period_bounds(qt_app, store):
    """Колонка «Квартал»: границы периодов — Пн, 1-е число, квартал, год."""
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import set_daily_cash_receipt
    from oplata.views.receipts_view import COL_QUARTER

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    # 27.10.2026 — вторник; неделя начинается 26.10 (понедельник).
    view.set_date(date(2026, 10, 27))
    # Прошлый квартал (июль) и начало года попадают только в «Год».
    set_daily_cash_receipt(store.data, "cash", "2026-07-02", Decimal("500"))
    # Тот же квартал, но раньше: попадает в месяц? нет — октябрь, 05.10.
    set_daily_cash_receipt(store.data, "cash", "2026-10-05", Decimal("1000"))
    # Текущая неделя (понедельник 26.10).
    set_daily_cash_receipt(store.data, "cash", "2026-10-26", Decimal("2000"))
    # Выбранный день.
    set_daily_cash_receipt(store.data, "cash", "2026-10-27", Decimal("3000"))
    view.refresh()
    qt_app.processEvents()

    row = view.incoming.row_keys.index("cash")
    cells = [view.incoming.table.item(row, column).text() for column in range(1, 6)]
    amount, week, month, quarter, year = cells
    assert amount == "3 000,00"        # только за выбранную дату
    assert week == "5 000,00"          # 26.10 + 27.10 (неделя Пн–Вс)
    assert month == "6 000,00"         # с 1 октября
    assert quarter == "6 000,00"       # с 1 октября (IV квартал)
    assert year == "6 500,00"          # с 1 января: + июльская запись
    assert COL_QUARTER == 4
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
    # v0.16.0: «просрочено» стоит слева от «на сегодня».
    assert table.item(2, 1).text() == "просрочено"
    assert table.item(2, 2).text() == "на сегодня"
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

    # Средства: 50 000 на счету, поступление 3 000, оплата 1 200.
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
    assert view.caption_text("funds") == "Остаток на счету"
    assert view.value_text("funds") == "50 000,00"
    assert view.value_text("income") == "3 000,00"
    assert view.value_text("payments") == "1 450,00"
    assert view.value_text("balance") == "51 550,00"

    # Блок «Задолженность поставщикам».
    assert view.value_text("debt_total") == "2 100,00"
    # «Задолженность на сегодня» удалена. Оплата поставщику (1 200)
    # гасит сначала просроченное (700 со сроком 20.10), остаток 500 —
    # долги недели (v0.15.0): и «на неделе», и «Просрочено» пусты.
    assert view.value_text("debt_week") == "0,00"
    assert view.value_text("debt_overdue") == "0,00"
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
    # Блок поставщиков: всего, на неделе, на сегодня, просроченный
    # платёж (v0.16.0) + четыре недели оплаты.
    assert len(view._values) == len(view._captions) == 4 + 8 + 1 + 2
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

    funds = calc.funds_totals(store.data)
    assert funds.funds == Decimal("0")   # «текущие средства» не задавали
    assert funds.balance == funds.income - funds.payments


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
    add_customer_receipt(store.data, buyer.id, "2026-10-28", Decimal("1500"))

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


def _DAY(view, day_index, subcolumn):
    """Номер колонки дня: считается от числа итоговых столбцов таблицы."""
    return view._day_column(day_index, subcolumn)


def test_supplier_overdue_carries_over_to_next_week(qt_app, store):
    """«На сегодня» — просроченное; при переходе недели долги переносятся."""
    from datetime import date, timedelta
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import add_delivery

    supplier = store.data.suppliers[0]
    # Срок 27.10 — внутри недели 26.10–01.11; срок 05.11 — в следующей.
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-10-27", Decimal("700"))
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-11-05", Decimal("900"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    row = 4  # первая строка контрагента
    table = view.table

    view.goto_date(date(2026, 10, 26))
    assert table.item(1, 1).text() == "ЗАДОЛЖЕННОСТЬ"
    assert table.item(2, 1).text() == "просрочено"
    assert table.item(2, 2).text() == "на сегодня"
    assert table.item(2, 3).text() == "за неделю"
    # На текущей неделе: просрочено 700 (срок 27.10 < сегодня 28.10),
    # «на сегодня» — 700, итог за неделю — 700.
    assert table.item(row, 1).text() == "700,00"
    assert table.item(row, 2).text() == "700,00"
    assert table.item(row, 3).text() == "700,00"

    # Следующая неделя: долг 700 перенесён в «на сегодня», в «за неделю»
    # уже 900 (срок 05.11).
    view.goto_date(date(2026, 10, 26) + timedelta(days=7))
    assert table.item(row, 1).text() == "700,00"
    assert table.item(row, 2).text() == "700,00"
    assert table.item(row, 3).text() == "900,00"
    window.close()


def test_supplier_payment_reduces_overdue_first_then_week(qt_app, store):
    """v0.15.0: оплата гасит просроченное («на сегодня»), остаток — «за неделю».

    Недоплаченный остаток остаётся на своём сроке, поэтому на следующей
    неделе он снова виден в «на сегодня».
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import add_delivery, set_daily_operation

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

    assert table.item(row, 1).text() == "700,00"  # просрочено
    assert table.item(row, 2).text() == "700,00"  # на сегодня
    assert table.item(row, 3).text() == "500,00"  # за неделю

    # Оплата 900: сначала гасит просрочку 700, остаток 200 — неделю.
    set_daily_operation(
        store.data, supplier.id, "2026-10-27", "Оплата", Decimal("900")
    )
    view.refresh()
    assert table.item(row, 1).text() == ""  # просрочка погашена
    assert table.item(row, 2).text() == ""  # «на сегодня» тоже пусто
    assert table.item(row, 3).text() == "300,00"  # 500 − 200

    # Недоплата: оплата 100 вместо 900 — остаток просрочки переносится.
    store.data.operations[:] = [
        op for op in store.data.operations if op.kind != "Оплата"
    ]
    set_daily_operation(
        store.data, supplier.id, "2026-10-20", "Оплата", Decimal("100")
    )
    view.refresh()
    assert table.item(row, 1).text() == "600,00"  # 700 − 100
    assert table.item(row, 2).text() == "600,00"  # «на сегодня» = то же
    assert table.item(row, 3).text() == "500,00"  # оплата ушла в просрочку
    # Следующая неделя: недоплаченный остаток 600 перенесён в «на
    # сегодня»; к нему добавился долг со сроком 29.10 — он тоже стал
    # просроченным (600 + 500 = 1 100).
    view.goto_date(date(2026, 10, 26) + timedelta(days=7))
    # «Просрочено» — только истёкшие сроки: 600 со сроком 20.10.
    assert table.item(row, 1).text() == "600,00"
    # «На сегодня» на следующей неделе: 600 перенесено + 500 (срок 29.10).
    assert table.item(row, 2).text() == "1 100,00"
    window.close()


def test_supplier_with_realization_never_shows_overdue(qt_app, store):
    """Поставщик со статусом «Реализация»: срок бессрочный, «на сегодня» — пусто."""
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
    assert table.item(row, 1).text() == ""  # в «на сегодня» не попадает никогда
    assert table.item(row, 2).text() == ""  # срок 05.10 вне недели 26.10–01.11
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
        assert view.week_label.font().pixelSize() == styles.fs(14)
        assert view.right_value_label.font().pixelSize() == styles.fs(14)
    window.close()
