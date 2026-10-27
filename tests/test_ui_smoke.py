"""Smoke-тесты интерфейса (headless, QT_QPA_PLATFORM=offscreen в CI).

Проверяем только оболочку: окно строится, все 5 экранов переключаются,
карточка открывается и умеет добавить контрагента. Формул здесь нет.
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
