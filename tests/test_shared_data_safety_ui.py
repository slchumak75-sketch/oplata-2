"""Регрессии совместного просмотра и прав в настоящих окнах Qt.

Все файлы и записи создаются только в изолированном временном каталоге.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from oplata import access, local_settings, storage
from oplata.models import Counterparty


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def store(tmp_path):
    return storage.DataStore(base_key=storage.BASE_MIR)


def test_saving_users_preserves_manager_both_bases_with_or_without_edit(
    qt_app, store, tmp_path, monkeypatch
):
    from oplata.views.data_view import DataView, PAGE_TITLES

    monkeypatch.setattr(access, "local_dir", lambda: tmp_path / "local")
    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path / "local"))
    admin, _, _, _ = access.default_users()
    access.save_users(access.default_users())
    access.save_current_user(admin)
    view = DataView(store)
    view.show()
    qt_app.processEvents()
    table = view.users_table
    edit_col = 2 + len(PAGE_TITLES)
    manager_row = next(
        row for row in range(table.rowCount())
        if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "manager"
    )

    for is_editable in (False, True, False):
        table.item(manager_row, edit_col).setCheckState(
            Qt.CheckState.Checked if is_editable else Qt.CheckState.Unchecked
        )
        view._save_users()
        manager = next(user for user in access.load_users() if user.id == "manager")
        assert manager.can_edit is is_editable
        assert access.Access(manager).can_switch_base is True
        # Обычные пользователи всё ещё ограничены своими базами.
        for user in access.load_users():
            if user.id in ("user-mir", "user-korelichi"):
                assert access.Access(user).can_switch_base is False
    view.close()


def test_manager_switch_does_not_overwrite_other_computers_new_entry(
    qt_app, store, tmp_path, monkeypatch
):
    from oplata.app import MainWindow

    monkeypatch.setattr(access, "local_dir", lambda: tmp_path / "local")
    storage.DataStore(base_key=storage.BASE_KORELICHI)
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    manager = access.default_users()[1]
    window = MainWindow(store, access.Access(manager))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()

    writer.data.suppliers.append(Counterparty(name="Тестовая запись другого ПК"))
    writer.save()
    latest = writer.path.read_bytes()
    assert not any(s.name == "Тестовая запись другого ПК" for s in store.data.suppliers)
    window.base_button.click()
    qt_app.processEvents()
    assert store.base_key == storage.BASE_KORELICHI
    assert writer.path.read_bytes() == latest
    window.base_button.click()
    qt_app.processEvents()
    assert any(s.name == "Тестовая запись другого ПК" for s in store.data.suppliers)
    window.close()


def test_publishing_requires_unlocked_configuration_and_test_computer(
    qt_app, store, tmp_path, monkeypatch
):
    from oplata.app import IDX_DATA, MainWindow

    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path / "local"))
    manager = access.default_users()[1]
    local_settings.set_test_computer(True, manager.id)
    window = MainWindow(store, access.Access(manager))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()

    # Кнопка на верхней панели без пароля позволяет только установку.
    window.update_button.click()
    qt_app.processEvents()
    dialog = window.update_dialog
    assert dialog.pick_button.isVisible()
    assert not dialog.test_button.isVisible()
    assert not dialog.release_button.isVisible()
    assert not dialog.retract_button.isVisible()

    # Защищаем не только видимость, но и сами действия раздачи.
    from types import SimpleNamespace
    from oplata import updates

    published = []
    fake_archive = tmp_path / "test-update.zip"
    dialog.info = SimpleNamespace(path=fake_archive, version="0.99.0")
    monkeypatch.setattr(
        updates,
        "copy_to_test",
        lambda *args: published.append(args) or fake_archive,
    )
    monkeypatch.setattr(updates, "log_event", lambda *args, **kwargs: None)
    dialog.publish_to_test()
    assert published == []

    # Даже когда конфигурация открыта, верхняя кнопка — только установка.
    window._config_unlocked = True
    window.stack.setCurrentIndex(IDX_DATA)
    window.update_button.click()
    assert not dialog.test_button.isVisible()
    # После вызова из самой конфигурации разрешение появляется.
    window.data_view._open_update_dialog()
    qt_app.processEvents()
    assert dialog.test_button.isVisible()
    assert dialog.release_button.isVisible()
    assert dialog.retract_button.isVisible()
    dialog.publish_to_test()
    assert len(published) == 1

    # Через пять минут бездействия разрешение исчезает даже в открытом окне.
    window._start_config_timer()
    window._lock_config_by_idle()
    qt_app.processEvents()
    assert not dialog.test_button.isVisible()
    assert not dialog.release_button.isVisible()
    assert not dialog.retract_button.isVisible()
    assert not dialog.publish_visible
    dialog.publish_to_test()
    assert len(published) == 1

    # Даже открытая конфигурация не даёт раздачу на обычном компьютере.
    local_settings.set_test_computer(False)
    window._config_unlocked = True
    window.stack.setCurrentIndex(IDX_DATA)
    window.data_view._open_update_dialog()
    qt_app.processEvents()
    assert not dialog.release_button.isVisible()
    dialog.close()
    window.close()


def test_configuration_idle_timer_restarts_after_reentering(qt_app, store, monkeypatch):
    from oplata.app import IDX_DATA, IDX_RECEIPTS, MainWindow

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()

    def unlock():
        window._config_unlocked = True
        return True

    monkeypatch.setattr(window, "_ask_config_password", unlock)
    window.show_page(IDX_DATA)
    assert window._config_timer.isActive()
    window.show_page(IDX_RECEIPTS)
    assert not window._config_timer.isActive()
    window.show_page(IDX_DATA)
    assert window._config_timer.isActive()
    window.close()


def test_pending_week_cell_is_saved_to_original_base_before_switch(qt_app, store):
    """Реальный щелчок на переключателе завершает ввод до смены файла."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    from oplata.app import IDX_SUPPLIERS, MainWindow
    from oplata.views.week_view import FIRST_DATA_ROW

    storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.show_page(IDX_SUPPLIERS)
    qt_app.processEvents()
    view = window.suppliers_view
    table = view.table
    item = table.item(FIRST_DATA_ROW, view._day_column(0, 0))
    table.setCurrentItem(item)
    table.editItem(item)
    qt_app.processEvents()
    editor = table.viewport().findChild(QLineEdit)
    assert editor is not None
    editor.setText("875,30")
    assert not store.data.operations

    QTest.mouseClick(window.base_button, Qt.MouseButton.LeftButton)
    qt_app.processEvents()
    assert store.base_key == storage.BASE_KORELICHI
    assert not store.data.operations
    saved = storage.DataStore(base_key=storage.BASE_MIR)
    assert len(saved.data.operations) == 1
    assert saved.data.operations[0].amount == "875.30"
    window.close()


def test_report_respects_manager_read_only_even_after_role_change(qt_app, store):
    from oplata.app import MainWindow

    admin, manager, _, _ = access.default_users()
    window = MainWindow(store, access.Access(manager))
    window.autosave_on_exit = False
    window.show()
    window.summary_view.report_button.click()
    qt_app.processEvents()
    dialog = window.summary_view.report_dialog

    def vat_cell():
        table = dialog.table
        row = next(
            row for row in range(table.rowCount())
            if table.item(row, 0).text() == "Входящий НДС"
        )
        return table.item(row, 3)

    cell = vat_cell()
    assert "просмотра" in dialog.hint_label.text()
    assert not cell.flags() & Qt.ItemFlag.ItemIsEditable
    before = store.path.read_bytes()
    cell.setText("123,00")
    qt_app.processEvents()
    assert store.path.read_bytes() == before

    window.apply_current_user(admin)
    assert "двойной клик" in dialog.hint_label.text()
    assert vat_cell().flags() & Qt.ItemFlag.ItemIsEditable
    vat_cell().setText("77,00")
    qt_app.processEvents()
    assert store.data.org_report.quarter(dialog.current_year(), "incoming_vat", 0) == "77,00"

    window.apply_current_user(manager)
    assert not vat_cell().flags() & Qt.ItemFlag.ItemIsEditable
    before = store.path.read_bytes()
    vat_cell().setText("999,00")
    qt_app.processEvents()
    assert store.path.read_bytes() == before
    dialog.close()
    window.close()


def test_open_report_does_not_keep_old_base_after_switch(qt_app, store):
    from oplata.app import MainWindow

    storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.summary_view.report_button.click()
    qt_app.processEvents()
    dialog = window.summary_view.report_dialog
    assert "МИР" in dialog.windowTitle()
    window.base_button.click()
    qt_app.processEvents()
    # Окно либо закрыто, либо переключено вместе с базой, но не должно
    # показывать старую базу при записи в новую.
    assert not dialog.isVisible() or "КОРЕЛИЧИ" in dialog.windowTitle()
    dialog.close()
    window.close()


def test_unseen_external_user_change_is_not_masked_by_switch(
    qt_app, store, tmp_path, monkeypatch
):
    from oplata.app import MainWindow

    monkeypatch.setattr(access, "local_dir", lambda: tmp_path / "local")
    users = access.default_users()
    access.save_users(users)
    access.save_current_user(users[1])
    storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store, access.Access(users[1]))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)

    users[1].name = "Руководитель обновлён"
    access.save_users(users)  # имитация записи другого компьютера
    window.base_button.click()
    qt_app.processEvents()
    assert window.pull_external_changes() is True
    assert window.access.title == "Руководитель обновлён"
    window.close()


def test_pending_report_cell_is_saved_to_original_base_before_switch(qt_app, store):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    from oplata.app import MainWindow

    storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.summary_view.report_button.click()
    qt_app.processEvents()
    dialog = window.summary_view.report_dialog
    table = dialog.table
    row = next(
        row for row in range(table.rowCount())
        if table.item(row, 0).text() == "Входящий НДС"
    )
    table.setCurrentCell(row, 3)
    table.editItem(table.item(row, 3))
    qt_app.processEvents()
    editor = table.viewport().findChild(QLineEdit)
    assert editor is not None
    editor.setText("88,00")
    year = dialog.current_year()

    QTest.mouseClick(window.base_button, Qt.MouseButton.LeftButton)
    qt_app.processEvents()
    assert store.base_key == storage.BASE_KORELICHI
    assert not dialog.isVisible()
    assert store.data.org_report.quarter(year, "incoming_vat", 0) == ""
    saved = storage.DataStore(base_key=storage.BASE_MIR)
    assert saved.data.org_report.quarter(year, "incoming_vat", 0) == "88,00"
    window.close()


def test_pending_popup_is_saved_to_original_base_before_switch(qt_app, store):
    from PySide6.QtTest import QTest
    from oplata.app import MainWindow

    buyer = Counterparty(name="Тестовый покупатель")
    store.data.customers.append(buyer)
    store.save()
    storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    popup = window.receipts_view.open_popup("customers", row=2)
    assert popup is not None and popup.isVisible()
    popup.edits[buyer.id].setText("42,00")

    QTest.mouseClick(window.base_button, Qt.MouseButton.LeftButton)
    qt_app.processEvents()
    assert store.base_key == storage.BASE_KORELICHI
    assert not store.data.operations
    saved = storage.DataStore(base_key=storage.BASE_MIR)
    assert len(saved.data.operations) == 1
    assert saved.data.operations[0].counterparty_id == buyer.id
    assert saved.data.operations[0].amount == "42.00"
    window.close()


def test_unseen_other_base_and_width_changes_survive_own_save(
    qt_app, store, monkeypatch
):
    from oplata import column_widths
    from oplata.app import MainWindow

    other = storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)

    other.data.suppliers.append(Counterparty(name="Другая тестовая база"))
    other.save()
    view = window.suppliers_view
    widths = [120] * view.column_count
    column_widths.save(view._widths_key, sum(widths), widths)
    store.data.suppliers[0].name = "Своя тестовая запись"
    store.save()
    assert window.pull_external_changes() is True
    assert view._saved_widths == widths
    window.base_button.click()
    assert any(s.name == "Другая тестовая база" for s in store.data.suppliers)
    window.close()


def test_publish_access_is_revoked_on_role_test_pc_and_page_changes(
    qt_app, store, tmp_path, monkeypatch
):
    from oplata.app import IDX_DATA, IDX_RECEIPTS, MainWindow

    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path / "local"))
    admin, manager, _, _ = access.default_users()
    local_settings.set_test_computer(True, manager.id)
    window = MainWindow(store, access.Access(manager))
    window.autosave_on_exit = False
    window.show()
    window._config_unlocked = True
    window.stack.setCurrentIndex(IDX_DATA)
    window.data_view._open_update_dialog()
    qt_app.processEvents()
    dialog = window.update_dialog
    assert dialog.publish_visible and dialog.release_button.isVisible()

    window.apply_current_user(admin)  # галочка привязана к руководителю
    assert not dialog.publish_visible and not dialog.release_button.isVisible()
    window.apply_current_user(manager)
    assert dialog.publish_visible and dialog.release_button.isVisible()
    local_settings.set_test_computer(False)
    window._on_test_computer_changed()
    assert not dialog.publish_visible and not dialog.release_button.isVisible()
    local_settings.set_test_computer(True, manager.id)
    window._on_test_computer_changed()
    assert dialog.publish_visible and dialog.release_button.isVisible()

    window.show_page(IDX_RECEIPTS)
    qt_app.processEvents()
    assert not window._config_unlocked
    assert not dialog.publish_visible and not dialog.release_button.isVisible()
    dialog.close()
    window.close()


def test_test_update_can_be_found_from_header_without_publish_access(
    qt_app, store, tmp_path, monkeypatch
):
    from oplata import updates
    from oplata.app import MainWindow

    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path / "local"))
    manager = access.default_users()[1]
    local_settings.set_test_computer(True, manager.id)
    seen = []
    monkeypatch.setattr(
        updates,
        "find_update",
        lambda folder, version, allow_test=False: seen.append(allow_test),
    )
    window = MainWindow(store, access.Access(manager))
    window.autosave_on_exit = False
    window.show()
    window.update_button.click()
    qt_app.processEvents()
    assert seen and seen[-1] is True
    assert not window.update_dialog.publish_visible
    assert window.update_dialog.pick_button.isVisible()
    window.update_dialog.close()
    window.close()


def test_idle_timeout_closes_config_even_with_modal_dialog(qt_app, store):
    from PySide6.QtWidgets import QDialog
    from oplata.app import IDX_DATA, MainWindow

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window._config_unlocked = True
    window.stack.setCurrentIndex(IDX_DATA)
    window._start_config_timer()
    modal = QDialog(window)
    modal.setModal(True)
    modal.show()
    nested = QDialog(modal)
    nested.setModal(True)
    nested.show()
    qt_app.processEvents()
    assert modal.isVisible() and modal.isModal()
    assert nested.isVisible() and nested.isModal()

    window._lock_config_by_idle()
    qt_app.processEvents()
    assert not window._config_unlocked
    assert window.stack.currentIndex() != IDX_DATA
    assert not modal.isVisible()
    assert not nested.isVisible()
    window.close()


def test_external_widths_apply_and_reset_on_all_open_tables(qt_app, store, monkeypatch):
    from oplata import column_widths
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.summary_view.report_button.click()
    qt_app.processEvents()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    views = window._resizable_views()
    assert len(views) == 5  # две недели, два блока оплат, открытый отчёт
    payload = {}
    for view in views:
        widths = [100 + i * 5 for i in range(view.table.columnCount())]
        payload[view._widths_key] = {"viewport": sum(widths), "columns": widths}
    column_widths.save_all(payload)  # имитируем изменение извне
    assert window.pull_external_changes() is True
    for view in views:
        expected = payload[view._widths_key]["columns"]
        actual = view._saved_widths
        if isinstance(actual, dict):
            actual = [actual[i] for i in range(len(expected))]
        assert actual == expected

    column_widths.save_all({})  # сброс на другом компьютере
    reset_bytes = column_widths.path().read_bytes()
    assert window.pull_external_changes() is True
    assert column_widths.path().read_bytes() == reset_bytes  # не переписали файл
    for view in views:
        assert not view._saved_widths
    window.summary_view.report_dialog.close()
    window.close()


def test_change_during_reload_is_not_marked_as_seen(qt_app, store, tmp_path, monkeypatch):
    from oplata.app import MainWindow

    monkeypatch.setattr(access, "local_dir", lambda: tmp_path / "local")
    users = access.default_users()
    access.save_users(users)
    access.save_current_user(users[1])
    window = MainWindow(store, access.Access(users[1]))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    writer.data.suppliers[0].name = "Первое изменение"
    writer.save()
    original_reload = store.reload

    def reload_and_write_again():
        result = original_reload()
        writer.data.suppliers[0].name = "Второе изменение во время чтения"
        writer.save()
        monkeypatch.setattr(store, "reload", original_reload)
        return result

    monkeypatch.setattr(store, "reload", reload_and_write_again)
    assert window.pull_external_changes() is True
    assert store.data.suppliers[0].name == "Первое изменение"
    assert window.pull_external_changes() is True
    assert store.data.suppliers[0].name == "Второе изменение во время чтения"
    window.close()


@pytest.mark.parametrize("problem", ["missing", "broken"])
def test_sync_waits_for_unreadable_users_without_replacing_file_or_rights(
    qt_app, store, monkeypatch, problem
):
    from oplata.app import MainWindow

    users = access.default_users()
    access.save_users(users)
    access.save_current_user(users[1])
    path = access.users_path()
    valid = path.read_bytes()
    window = MainWindow(store, access.Access(users[1]))
    window.autosave_on_exit = False
    window.show()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    if problem == "missing":
        path.unlink()
        before = None
    else:
        path.write_text("{broken", encoding="utf-8")
        before = path.read_bytes()

    assert window.pull_external_changes() is False
    assert (path.read_bytes() if path.exists() else None) == before
    assert window.access.user.id == "manager"
    assert not window.access.can_edit
    assert "пользовател" in window.statusBar().currentMessage().lower()
    path.write_bytes(valid)
    assert window.pull_external_changes() is True
    assert window.access.user.id == "manager"
    window.close()


@pytest.mark.parametrize("problem", ["missing", "broken"])
def test_sync_waits_for_unreadable_active_base_and_retries(
    qt_app, store, monkeypatch, problem
):
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    valid = store.path.read_bytes()
    old_names = [s.name for s in store.data.suppliers]
    if problem == "missing":
        store.path.unlink()
        before = None
    else:
        store.path.write_text("{broken", encoding="utf-8")
        before = store.path.read_bytes()
    assert window.pull_external_changes() is False
    assert [s.name for s in store.data.suppliers] == old_names
    assert (store.path.read_bytes() if store.path.exists() else None) == before
    assert "не удалось" in window.statusBar().currentMessage().lower()
    store.path.write_bytes(valid)
    assert window.pull_external_changes() is True
    window.close()


def test_external_data_updates_open_report_and_journal(qt_app, store, monkeypatch):
    from datetime import date
    from oplata.app import MainWindow
    from oplata.models import KIND_INCOME, Operation

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.summary_view.report_button.click()
    qt_app.processEvents()
    report = window.summary_view.report_dialog
    year = report.current_year()
    partner = store.data.suppliers[0]
    window.open_operations_journal("supplier", partner.id)
    journal = window._journal_dialogs[-1]
    qt_app.processEvents()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)

    writer = storage.DataStore(base_key=storage.BASE_MIR)
    writer.data.org_report.set_quarter(year, "incoming_vat", 0, "62,00")
    writer.data.suppliers[0].name = "Обновлённый поставщик"
    writer.data.operations.append(
        Operation(
            kind=KIND_INCOME,
            counterparty_id=partner.id,
            date=date.today().isoformat(),
            amount="321.00",
        )
    )
    writer.save()
    assert window.pull_external_changes() is True
    table = report.table
    row = next(
        row for row in range(table.rowCount())
        if table.item(row, 0).text() == "Входящий НДС"
    )
    assert table.item(row, 3).text() == "62,00"
    assert journal.view.table.item(0, 1).text() == "321,00"
    assert journal.title_label.text() == "Обновлённый поставщик"
    journal.close()
    report.close()
    window.close()


def test_publish_confirmation_rechecks_permission(qt_app, store, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from oplata import updates
    from oplata.app import IDX_DATA, MainWindow

    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path / "local"))
    manager = access.default_users()[1]
    local_settings.set_test_computer(True, manager.id)
    window = MainWindow(store, access.Access(manager))
    window.autosave_on_exit = False
    window.show()
    window._config_unlocked = True
    window.stack.setCurrentIndex(IDX_DATA)
    window.data_view._open_update_dialog()
    dialog = window.update_dialog
    dialog.info = SimpleNamespace(path=tmp_path / "test.zip", version="0.99.0")
    actions = []
    monkeypatch.setattr(updates, "publish", lambda *args: actions.append("publish"))
    monkeypatch.setattr(updates, "retract", lambda *args: actions.append("retract"))

    def lose_access(*args, **kwargs):
        window._config_unlocked = False
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", lose_access)
    dialog.publish_release()
    window._config_unlocked = True
    dialog.retract()
    assert actions == []
    dialog.close()
    window.close()


def test_sync_waits_for_active_table_editor(qt_app, store, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit
    from oplata.app import IDX_SUPPLIERS, MainWindow
    from oplata.views.week_view import FIRST_DATA_ROW

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.show_page(IDX_SUPPLIERS)
    qt_app.processEvents()
    table = window.suppliers_view.table
    item = table.item(FIRST_DATA_ROW, window.suppliers_view._day_column(0, 0))
    table.setCurrentItem(item)
    table.editItem(item)
    qt_app.processEvents()
    editor = table.viewport().findChild(QLineEdit)
    assert editor is not None
    editor.setText("54,00")
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    writer.data.suppliers[0].name = "Новая запись на другом ПК"
    writer.save()
    # Не перерисовывать ячейку поверх незавершённого ввода.
    assert window.pull_external_changes() is False
    assert editor.isVisible() and editor.text() == "54,00"
    QTest.keyClick(editor, Qt.Key.Key_Escape)  # отменили ввод
    qt_app.processEvents()
    assert window.pull_external_changes() is True
    assert store.data.suppliers[0].name == "Новая запись на другом ПК"
    window.close()


def test_sync_waits_for_configuration_draft(qt_app, store, monkeypatch):
    from oplata.app import IDX_DATA, IDX_RECEIPTS, MainWindow

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window._config_unlocked = True
    window.stack.setCurrentIndex(IDX_DATA)
    window.data_view._pending_overrides["2026-10-26"] = "off"
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    writer.data.suppliers[0].name = "Изменение извне"
    writer.save()
    assert window.pull_external_changes() is False
    assert window.data_view._pending_overrides["2026-10-26"] == "off"
    window.show_page(IDX_RECEIPTS)
    # Уход со вкладки не должен молча стереть черновик синхронизацией.
    assert window.pull_external_changes() is False
    assert window.data_view._pending_overrides["2026-10-26"] == "off"
    window.data_view.discard_changes()
    assert window.pull_external_changes() is True
    assert store.data.suppliers[0].name == "Изменение извне"
    window.close()


def test_sync_can_refresh_while_read_only_report_is_active(qt_app, store):
    from oplata.app import MainWindow

    manager = access.default_users()[1]
    window = MainWindow(store, access.Access(manager))
    access.save_users(access.default_users())
    access.save_current_user(manager)
    window.autosave_on_exit = False
    window.show()
    window.summary_view.report_button.click()
    report = window.summary_view.report_dialog
    report.activateWindow()
    qt_app.setActiveWindow(report)
    qt_app.processEvents()
    assert report.isActiveWindow()
    assert not window.isActiveWindow()
    year = report.current_year()
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    writer.data.org_report.set_quarter(year, "incoming_vat", 0, "19,00")
    writer.save()
    assert window.pull_external_changes() is True
    row = next(
        row for row in range(report.table.rowCount())
        if report.table.item(row, 0).text() == "Входящий НДС"
    )
    assert report.table.item(row, 3).text() == "19,00"
    report.close()
    window.close()


def test_incomplete_transfer_stops_launch_and_explains_the_problem(
    qt_app, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox
    from oplata import main

    files = [tmp_path / "data.json", tmp_path / "data-korelichi.json"]
    copied = [tmp_path / "shared" / "data.json"]
    monkeypatch.setattr(storage, "pending_promotion", lambda: files)
    monkeypatch.setattr(storage, "promote_to_shared", lambda expected: copied)
    monkeypatch.setattr(storage, "local_data_dir", lambda: tmp_path / "local")
    monkeypatch.setattr(storage, "shared_dir_candidate", lambda: str(tmp_path / "shared"))
    questions = []
    warnings = []

    def answer_yes(*args):
        questions.append(args)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    assert main._offer_shared_base_transfer() is False
    assert questions[0][-1] == QMessageBox.StandardButton.No
    assert "1 из 2" in warnings[0][2]
    assert "закроется" in warnings[0][2]


def test_declining_transfer_stops_without_copying(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from oplata import main

    monkeypatch.setattr(storage, "pending_promotion", lambda: [tmp_path / "data.json"])
    monkeypatch.setattr(storage, "local_data_dir", lambda: tmp_path / "local")
    monkeypatch.setattr(storage, "shared_dir_candidate", lambda: str(tmp_path / "shared"))
    calls = []
    monkeypatch.setattr(storage, "promote_to_shared", lambda *args: calls.append(args))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert main._offer_shared_base_transfer() is False
    assert not calls


def test_partial_shared_folder_does_not_silently_create_demo(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from oplata import main

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(storage, "data_dir", lambda: shared)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))
    existing = shared / "data-korelichi.json"
    existing.write_text('{"suppliers": []}', encoding="utf-8")
    before = existing.read_bytes()
    questions = []

    def decline(*args):
        questions.append(args)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    assert main._prepare_shared_files() is False
    assert "data.json" in questions[0][2]
    assert "демо" in questions[0][2]
    assert questions[0][-1] == QMessageBox.StandardButton.No
    assert sorted(path.name for path in shared.iterdir()) == ["data-korelichi.json"]
    assert existing.read_bytes() == before


def test_new_shared_files_require_explicit_consent_and_are_readable(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from oplata import main

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(storage, "data_dir", lambda: shared)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert main._prepare_shared_files() is True
    assert set(path.name for path in shared.iterdir()) == {
        "data.json", "data-korelichi.json", "users.json"
    }
    for key in (storage.BASE_MIR, storage.BASE_KORELICHI):
        assert storage.DataStore(base_key=key).data.suppliers
    import json
    users = json.loads((shared / "users.json").read_text(encoding="utf-8"))
    assert users["users"][0]["id"] == "admin"
    assert "password" not in users


@pytest.mark.parametrize("broken", ["data.json", "users.json"])
def test_unreadable_shared_file_stops_startup_without_replacement(
    qt_app, tmp_path, monkeypatch, broken
):
    from PySide6.QtWidgets import QMessageBox
    from oplata import main

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(storage, "data_dir", lambda: shared)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))
    path = shared / broken
    path.write_text("{некорректный файл", encoding="utf-8")
    before = path.read_bytes()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    assert main._prepare_shared_files() is False
    assert path.read_bytes() == before
    assert list(shared.iterdir()) == [path]
    assert warnings and broken in warnings[0]


def test_new_shared_file_not_replaced_if_it_appears_during_startup(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from oplata import main

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(storage, "data_dir", lambda: shared)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    real_write = storage.write_new_json

    def file_arrives(path, payload):
        path.write_text("новый тестовый файл", encoding="utf-8")
        real_write(path, payload)

    monkeypatch.setattr(storage, "write_new_json", file_arrives)
    assert main._prepare_shared_files() is False
    assert (shared / "data.json").read_text(encoding="utf-8") == "новый тестовый файл"
    assert sorted(path.name for path in shared.iterdir()) == ["data.json"]
    assert warnings


def test_sync_does_not_reset_unsaved_widths_when_only_data_changed(qt_app, store, monkeypatch):
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    view = window.suppliers_view
    unsaved_widths = [90] * view.column_count
    view._saved_widths = unsaved_widths.copy()
    view._saved_viewport = sum(unsaved_widths)
    view._widths_locked = True
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    writer.data.suppliers[0].name = "Изменение без изменения ширин"
    writer.save()
    assert window.pull_external_changes() is True
    assert view._saved_widths == unsaved_widths
    window.close()


def test_exit_export_reads_latest_active_base_from_disk(qt_app, store, monkeypatch):
    from oplata import app as app_module
    from oplata.app import MainWindow

    storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    writer.data.suppliers.append(Counterparty(name="Новая тестовая запись"))
    writer.save()
    newest = writer.path.read_bytes()
    assert not any(s.name == "Новая тестовая запись" for s in store.data.suppliers)
    exported = []
    monkeypatch.setattr(app_module, "export_bases", lambda bases, path: exported.append(bases))

    assert window.autosave_bases_to_excel() is not None
    assert any(s.name == "Новая тестовая запись" for s in exported[0][storage.BASE_MIR].suppliers)
    assert writer.path.read_bytes() == newest
    window.close()


def test_export_reads_bases_after_file_dialog(qt_app, store, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from oplata import app as app_module
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.autosave_on_exit = False
    writer = storage.DataStore(base_key=storage.BASE_MIR)
    exported = []

    def choose_file(*args):
        writer.data.suppliers.append(Counterparty(name="Изменение пока выбирали файл"))
        writer.save()
        return str(tmp_path / "synthetic.xlsx"), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_file)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(app_module, "export_bases", lambda bases, path: exported.append(bases))
    window.export_bases_to_excel()
    assert any(s.name == "Изменение пока выбирали файл" for s in exported[0][storage.BASE_MIR].suppliers)
    window.close()


@pytest.mark.parametrize("bad_file", ["active", "other", "missing-shared"])
def test_exit_export_does_not_replace_previous_copy_when_input_is_unreadable(
    qt_app, store, tmp_path, monkeypatch, bad_file
):
    from PySide6.QtWidgets import QMessageBox
    from oplata.app import MainWindow

    other = storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    output = storage.local_base_file()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"earlier synthetic export")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    if bad_file == "active":
        store.path.write_text("{broken", encoding="utf-8")
    elif bad_file == "other":
        other.path.write_text("[]", encoding="utf-8")
    else:
        monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(tmp_path))
        other.path.unlink()

    assert window.autosave_bases_to_excel() is None
    assert output.read_bytes() == b"earlier synthetic export"
    assert warnings and "Не удалось" in warnings[0]
    window.close()


def test_failed_shared_switch_preserves_calendar_draft_and_files(qt_app, store, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from oplata.app import MainWindow

    other = storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.data_view._pending_overrides["2026-10-26"] = "off"
    before = store.path.read_bytes()
    other.path.unlink()
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(tmp_path))
    warnings = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window.base_button.click()
    assert store.base_key == storage.BASE_MIR
    assert window.data_view.has_unsaved_changes
    assert store.path.read_bytes() == before
    assert not other.path.exists()
    assert warnings and "не создавалась" in warnings[0]
    window.close()


def test_fixed_role_with_unavailable_shared_base_closes_instead_of_showing_other(
    qt_app, store, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(tmp_path))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window.apply_current_user(access.default_users()[3])
    assert not window.isEnabled()
    assert not window.autosave_on_exit
    assert not storage.data_path_for(storage.BASE_KORELICHI).exists()
    assert warnings and "закроется" in warnings[0]
    qt_app.processEvents()
    assert not window.isVisible()


def test_switch_warns_before_discarding_calendar_draft(qt_app, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from oplata.app import MainWindow

    storage.DataStore(base_key=storage.BASE_KORELICHI)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    window.data_view._pending_overrides["2026-10-26"] = "off"
    original = store.path.read_bytes()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No
    )
    window.base_button.click()
    assert store.base_key == storage.BASE_MIR
    assert window.data_view.has_unsaved_changes
    assert store.path.read_bytes() == original

    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes
    )
    window.base_button.click()
    assert store.base_key == storage.BASE_KORELICHI
    assert storage.data_path_for(storage.BASE_MIR).read_bytes() == original
    window.close()
