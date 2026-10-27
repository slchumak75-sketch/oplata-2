"""Smoke-тесты интерфейса (headless, QT_QPA_PLATFORM=offscreen в CI).

Проверяем построение окна и главные пользовательские сценарии:
ввод оплаты, поставку и долг, CRUD операций в журнале, карточки.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


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
    assert window.stack.count() == 5
    for index in range(5):
        window.show_page(index)
        assert window.stack.currentIndex() == index
    # В сводке — 5 демо-поставщиков с макетов
    assert window.summary_view.table.rowCount() == 5
    # В покупателях справа «0»
    assert window.customers_view.right_value_label.text() == "0"
    window.close()


def test_week_table_payment_input(qt_app, store):
    """Оплата вводится в ячейке; Приход у поставщиков — через окно «Поставка»."""
    from PySide6.QtCore import Qt

    from oplata.app import MainWindow
    from oplata.views.week_view import DIALOG_ROLE, FIRST_DATA_ROW, TOTAL_ROW

    window = MainWindow(store)
    table = window.suppliers_view.table
    income = table.item(FIRST_DATA_ROW, 3)   # пн, Приход -> окно «Поставка»
    debt = table.item(FIRST_DATA_ROW, 4)     # пн, Долг -> считается программой
    payment = table.item(FIRST_DATA_ROW, 5)  # пн, Оплата -> ввод в ячейке
    assert not (income.flags() & Qt.ItemFlag.ItemIsEditable)
    assert income.data(DIALOG_ROLE)  # двойной клик откроет «Поставку»
    assert payment.flags() & Qt.ItemFlag.ItemIsEditable
    assert not (debt.flags() & Qt.ItemFlag.ItemIsEditable)

    payment.setText("200")  # программная правка = тот же сигнал, что от Enter
    assert [op.kind for op in store.data.operations] == ["Оплата"]
    assert payment.text() == "200,00"  # переформатировано после сохранения
    assert table.item(TOTAL_ROW, 5).text() == "200,00"  # ВСЕГО обновилось
    assert window.operations_view.found_label.text() == "Найдено: 1"

    payment.setText("")  # очистка ячейки удаляет операцию
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
    assert table.item(ggg_row, 3 + 3 * 3 + 1).text() == "88 888,88"  # Долг чт
    assert table.item(ggg_row, 2).text() == "88 888,88"  # задолженность за неделю
    assert table.item(3, 2).text() == "88 888,88"  # ВСЕГО за неделю
    assert window.suppliers_view.right_value_label.text() == "88 888,88 руб."

    # Оплата в день срока НЕ убирает сумму из «Долга» (требование
    # пользователя v0.3.1: остаётся для архива; правила оплат — позже)
    payment_cell = table.item(ggg_row, 3 + 3 * 3 + 2)  # Оплата чт 05.11
    payment_cell.setText("88 888,88")
    assert table.item(ggg_row, 3 + 3 * 3 + 1).text() == "88 888,88"  # Долг на месте
    assert table.item(ggg_row, 2).text() == "88 888,88"  # и «за неделю» тоже
    window.close()


def test_operation_dialog_and_journal_crud(qt_app, store):
    """Журнал: добавить операцию, поправить её, отфильтровать и удалить."""
    from datetime import date

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.dialogs.operation_dialog import OperationDialog

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    dialog = OperationDialog(store, initial_counterparty_id=supplier.id)
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

    window = MainWindow(store)
    journal = window.operations_view
    assert journal.found_label.text() == "Найдено: 1"
    journal._select_operation_row(op.id)
    assert journal.selected_operation_id() == op.id
    assert journal.edit_btn.isEnabled() and journal.delete_btn.isEnabled()

    journal.date_from.setText("28.10.2026")  # операция от 27.10 — вне диапазона
    assert journal.found_label.text() == "Найдено: 0"
    journal.reset_filters()
    assert journal.found_label.text() == "Найдено: 1"

    # Главное окно после CRUD в журнале обновляет недельную таблицу:
    # до удаления долг виден на сроке 26.11 (чт), после — ячейка пуста.
    window.suppliers_view.goto_date(date(2026, 11, 26))
    week_table = window.suppliers_view.table
    ggg_row = 4 + [s.name for s in store.data.suppliers].index("ггг")
    assert week_table.item(ggg_row, 3 + 3 * 3 + 1).text() == "1 200,00"

    assert journal.delete_operation(op.id)
    assert week_table.item(ggg_row, 3 + 3 * 3 + 1).text() == ""
    assert store.data.operations == []
    assert journal.found_label.text() == "Найдено: 0"
    assert not journal.edit_btn.isEnabled()
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


def test_summary_print_snapshot_and_pdf_render(qt_app, store, tmp_path):
    """Печать «Сводки» берёт экранный снимок и реально рисуется в PDF."""
    from datetime import datetime

    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtPrintSupport import QPrinter

    from oplata.app import MainWindow

    window = MainWindow(store)
    view = window.summary_view
    assert view.print_button.text() == "🖨  Печать"

    archived = store.data.suppliers[0]
    archived.archived = True
    view.refresh()
    hidden_html = view.build_print_html(datetime(2026, 10, 27, 12, 0))
    assert view.table.rowCount() == 4
    assert archived.name not in hidden_html
    assert "Архивные поставщики: скрыты" in hidden_html

    view.show_archived.setChecked(True)  # сигнал сам вызовет refresh()
    shown_html = view.build_print_html(datetime(2026, 10, 27, 12, 0))
    assert view.table.rowCount() == 5
    assert archived.name in shown_html
    assert "Архивные поставщики: показаны" in shown_html
    assert "Задолженность по поставщикам" in shown_html

    document = view.create_print_document(datetime(2026, 10, 27, 12, 0))
    printer = view.configure_print_printer(
        QPrinter(QPrinter.PrinterMode.HighResolution)
    )
    assert printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape
    assert printer.pageLayout().pageSize().id() == QPageSize.PageSizeId.A4
    pdf_path = tmp_path / "summary.pdf"
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(pdf_path))

    view.render_print_document(printer, document)
    assert document.pageCount() >= 1
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert pdf_path.stat().st_size > 1000
    window.close()
