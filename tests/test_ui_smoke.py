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


def test_week_table_income_payment_input(qt_app, store):
    """Ввод в Приход/Оплата сохраняется; Долг вводить нельзя (формула позже)."""
    from PySide6.QtCore import Qt

    from oplata.app import MainWindow
    from oplata.views.week_view import FIRST_DATA_ROW, TOTAL_ROW

    window = MainWindow(store)
    table = window.suppliers_view.table
    income = table.item(FIRST_DATA_ROW, 3)   # пн, Приход
    debt = table.item(FIRST_DATA_ROW, 4)     # пн, Долг
    payment = table.item(FIRST_DATA_ROW, 5)  # пн, Оплата
    assert income.flags() & Qt.ItemFlag.ItemIsEditable
    assert payment.flags() & Qt.ItemFlag.ItemIsEditable
    assert not (debt.flags() & Qt.ItemFlag.ItemIsEditable)

    income.setText("1 500,75")  # программная правка = тот же сигнал, что от Enter
    payment.setText("200")
    kinds = sorted(op.kind for op in store.data.operations)
    assert kinds == ["Оплата", "Приход"]
    assert income.text() == "1 500,75"  # переформатировано после сохранения
    assert table.item(TOTAL_ROW, 3).text() == "1 500,75"  # ВСЕГО обновилось
    # Операции видны в журнале
    assert window.operations_view.found_label.text() == "Найдено: 2"

    payment.setText("")  # очистка ячейки удаляет операцию
    assert [op.kind for op in store.data.operations] == ["Приход"]
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
