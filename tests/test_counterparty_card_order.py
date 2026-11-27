"""Порядок выпадающих списков карточек (только вымышленные данные)."""

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from oplata.dialogs.counterparty_card import (  # noqa: E402
    NEW_ITEM_LABEL,
    CounterpartyCardDialog,
)
from oplata.models import Counterparty  # noqa: E402
from oplata.storage import DataStore  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.mark.parametrize("kind, attr", [("supplier", "suppliers"), ("customer", "customers")])
def test_card_combo_is_alphabetical_without_reordering_data(qt_app, tmp_path, kind, attr):
    store = DataStore(tmp_path / "data.json")
    original = [
        Counterparty(name="Ясень"),
        Counterparty(name="ёлка", note="выбранная карточка"),
        Counterparty(name="Акация"),
        Counterparty(name="Берёза", archived=True),
        Counterparty(name="еж"),
    ]
    setattr(store.data, attr, original.copy())
    selected = original[1]
    dialog = CounterpartyCardDialog(store, kind, selected_id=selected.id)

    assert [dialog.combo.itemText(i) for i in range(dialog.combo.count())] == [
        NEW_ITEM_LABEL, "Акация", "Берёза", "еж", "ёлка", "Ясень"
    ]
    assert dialog.combo.currentData() == selected.id
    assert dialog.name_edit.text() == selected.name
    assert dialog.note_edit.text() == selected.note
    assert getattr(store.data, attr) == original

    # При обновлении не сбивается выбор, в том числе архивной карточки.
    archived = original[3]
    dialog._reload_combo(archived.id)
    assert dialog.combo.currentData() == archived.id
    assert dialog.chk_archived.isChecked()

    # Добавление заново сортирует отображение и выбирает созданную запись.
    dialog.name_edit.setText("Василёк")
    dialog.note_edit.clear()
    dialog.chk_archived.setChecked(False)
    dialog._add()
    added = getattr(store.data, attr)[-1]
    assert dialog.combo.currentData() == added.id
    assert [dialog.combo.itemText(i) for i in range(dialog.combo.count())] == [
        NEW_ITEM_LABEL, "Акация", "Берёза", "Василёк", "еж", "ёлка", "Ясень"
    ]
    assert getattr(store.data, attr)[:-1] == original
    dialog.close()


@pytest.mark.parametrize("kind", ["supplier", "customer"])
def test_card_defaults_to_new_item(qt_app, tmp_path, kind):
    store = DataStore(tmp_path / "data.json")
    dialog = CounterpartyCardDialog(store, kind)
    assert dialog.combo.currentIndex() == 0
    assert dialog.combo.currentText() == NEW_ITEM_LABEL
    assert dialog.name_edit.text() == ""
    dialog.close()
