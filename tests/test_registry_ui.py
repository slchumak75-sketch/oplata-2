"""Интерфейс импорта реестра: только вымышленные данные во временной папке."""

import os
import sys
from datetime import date

import pytest
from openpyxl import Workbook

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from oplata.access import (  # noqa: E402
    ALL_PAGES, IDX_CUSTOMERS, IDX_KKM, IDX_SUPPLIERS, Access, User, save_users,
)
from oplata.app import MainWindow  # noqa: E402
from oplata.dialogs.registry_dialog import RegistryDialog  # noqa: E402
from oplata.models import AppData, Counterparty, KIND_SALE  # noqa: E402
from oplata.registry import capture_registry_snapshot, parse_registry  # noqa: E402
from oplata.storage import BASE_KORELICHI, BASE_MIR, DataStore  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


def fake_book(path):
    book = Workbook()
    sheet = book.active
    sheet.append(["Тестовый реестр"])
    for _ in range(5):
        sheet.append([])
    sheet.append([None, "№ п/п", "Дата", "Документ", "Номер", "Автор", "Статус",
                  "Сумма", "Вал.", "Информация", None])
    for row in (
        [None, 1, "10.11.2026", "Поступление в розницу", 1, "Тест", " ", "10.00", "BYN", "Тест", 111222333],
        [None, 2, "10.11.2026", "Реализация", 2, "Тест", "Проведен", "20.00", "BYN", "Тест", 444555666],
        [None, 3, "10.11.2026", "Реализация", 3, "Тест", " ", "3.00", "BYN", "Тест", 444555666],
        [None, 4, "10.11.2026", "Отчет ККМ", 4, "Тест", "Проведен", "40.00", "BYN", "Тест", None],
        [None, 5, "10.11.2026", "Переоценка", 5, "Тест", " ", "ошибка", "???", "Тест", None],
    ):
        sheet.append(row)
    book.save(path)
    book.close()
    return path


def make_store(tmp_path, *, ambiguous=False, base=BASE_MIR):
    path = tmp_path / ("data.json" if base == BASE_MIR else "data-korelichi.json")
    store = DataStore(path, base_key=base)
    store.data = AppData(
        suppliers=[Counterparty(name="Тестовый поставщик", unp="111222333", id="sup")],
        customers=[Counterparty(name="Тестовый покупатель", unp="444555666", id="cust")],
    )
    if ambiguous:
        store.data.customers.append(
            Counterparty(name="Второй покупатель", unp="444555666", id="cust2", note="другая карточка")
        )
    store.save()
    return store


def test_preview_requires_selection_base_confirmation_and_final_approval(qt_app, tmp_path, monkeypatch):
    store = make_store(tmp_path, ambiguous=True)
    registry = parse_registry(fake_book(tmp_path / "registry.xlsx"))
    dialog = RegistryDialog(store, registry, capture_registry_snapshot(store), "Тест.xlsx")
    dialog.show()
    qt_app.processEvents()
    before = store.path.read_bytes()
    assert "переоценка — 1" in dialog.ignored_label.text()
    assert dialog.targets[8].currentData() == "sup"
    assert dialog.targets[9].currentData() == ""
    assert dialog.targets[10].currentData() == ""
    assert dialog.source_table.item(2, 4).text() == "Не проведен (пусто)"
    assert dialog.source_table.item(3, 4).text() == "Проведен"
    assert not dialog.import_button.isEnabled()
    for number in (9, 10):
        combo = dialog.targets[number]
        combo.setCurrentIndex(combo.findData("cust"))
    assert not dialog.plan.issues
    assert not dialog.import_button.isEnabled()
    dialog.confirm_base.setChecked(True)
    assert dialog.import_button.isEnabled()
    assert any("смешанные статусы" in dialog.plan_table.item(i, 6).text()
               for i in range(dialog.plan_table.rowCount()))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.No)
    dialog._import()
    assert store.path.read_bytes() == before
    assert not dialog.changed

    calls = []
    dialog.on_imported = lambda: calls.append("refreshed")
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok)
    dialog._import()
    assert dialog.changed and calls == ["refreshed"]
    sale = next(op for op in store.data.operations if op.kind == KIND_SALE)
    assert (sale.amount, sale.invoiced) == ("23.00", False)
    assert dialog.result() == dialog.DialogCode.Accepted
    dialog.close()


def test_read_only_role_cannot_open_file_picker(qt_app, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    user = User(id="viewer", name="Тест", pages=list(ALL_PAGES), can_edit=False)
    window = MainWindow(store, Access(user))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    assert window.registry_button.isHidden()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: pytest.fail("файл не должен открываться"))
    window._on_load_registry_clicked()
    assert "право ввода" in warnings[0]
    window.close()


def test_missing_section_permission_blocks_whole_import(qt_app, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    path = fake_book(tmp_path / "registry.xlsx")
    user = User(id="limited", name="Тест", pages=[IDX_SUPPLIERS], can_edit=True)
    window = MainWindow(store, Access(user))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    before = store.path.read_bytes()
    warnings = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(RegistryDialog, "exec", lambda *_args: pytest.fail("нет прав на все разделы"))
    window._on_load_registry_clicked()
    assert warnings and "нет доступа" in warnings[-1]
    assert store.path.read_bytes() == before
    window.close()


def test_rights_change_while_confirming_blocks_commit(qt_app, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    path = fake_book(tmp_path / "registry.xlsx")
    user = User(id="admin-test", name="Тест", pages=[IDX_SUPPLIERS, IDX_CUSTOMERS, IDX_KKM], can_edit=True)
    save_users([user])
    window = MainWindow(store, Access(user))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    before = store.path.read_bytes()
    warnings = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: pytest.fail("запись должна быть запрещена"))

    def revoke(*_args, **_kwargs):
        user.can_edit = False
        return QMessageBox.StandardButton.Yes

    def run_preview(dialog):
        dialog.confirm_base.setChecked(True)
        assert not dialog.plan.issues
        dialog._import()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(QMessageBox, "question", revoke)
    monkeypatch.setattr(RegistryDialog, "exec", run_preview)
    window._on_load_registry_clicked()
    assert warnings and "право ввода" in warnings[-1]
    assert store.path.read_bytes() == before
    window.close()


def test_rights_revoked_while_file_is_parsed_block_preview(qt_app, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    path = fake_book(tmp_path / "registry.xlsx")
    user = User(id="writer", name="Тест", pages=list(ALL_PAGES), can_edit=True)
    save_users([user])
    window = MainWindow(store, Access(user))
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    before = store.path.read_bytes()
    warnings = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: warnings.append(_[-1]))
    monkeypatch.setattr(RegistryDialog, "exec", lambda *_: pytest.fail("права устарели"))

    def parse_and_revoke(source):
        registry = parse_registry(source)
        save_users([User(id="writer", name="Тест", pages=list(ALL_PAGES), can_edit=False)])
        return registry

    monkeypatch.setattr("oplata.app.parse_registry", parse_and_revoke)
    window._on_load_registry_clicked()
    assert warnings and "Права в файле отличаются" in warnings[-1]
    assert store.path.read_bytes() == before
    window.close()


def test_missing_users_in_shared_directory_blocks_import(qt_app, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    path = fake_book(tmp_path / "registry.xlsx")
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    qt_app.processEvents()
    # Главное окно могло создать локальные тестовые права: моделируем
    # общую папку, где файла не было при последнем снимке.
    from oplata.access import users_path
    users_path().unlink(missing_ok=True)
    window._data_signature = window._shared_signature()
    monkeypatch.setattr(window, "_shared_data_dir", lambda: True)
    warnings = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: warnings.append(_[-1]))
    monkeypatch.setattr(RegistryDialog, "exec", lambda *_: pytest.fail("файла прав нет"))
    before = store.path.read_bytes()
    window._on_load_registry_clicked()
    assert warnings and "Файл пользователей и прав недоступен" in warnings[-1]
    assert store.path.read_bytes() == before
    window.close()


def test_full_ui_import_refreshes_week_kkm_summary_report_and_journal(qt_app, tmp_path, monkeypatch):
    from oplata import styles
    from oplata.views.kkm_view import COL_AMOUNT
    from oplata.views.week_view import FIRST_DATA_ROW

    store = make_store(tmp_path)
    path = fake_book(tmp_path / "registry.xlsx")
    user = User(id="writer", name="Тест", pages=list(ALL_PAGES), can_edit=True)
    save_users([user])
    window = MainWindow(store, Access(user))
    window.autosave_on_exit = False
    window.show()
    day = date(2026, 11, 10)
    window.suppliers_view.goto_date(day)
    window.customers_view.goto_date(day)
    window.kkm_view.set_month(day)
    window.summary_view.set_date(day)
    window.summary_view.report_button.click()
    report = window.summary_view.report_dialog
    window.open_operations_journal("supplier", "sup")
    journal = window._journal_dialogs[-1]
    journal.view.period_months = None
    journal.refresh()
    qt_app.processEvents()
    assert journal.view.table.item(0, 0).text() == "Ничего не найдено."
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kw: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kw: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: pytest.fail("предупреждение при правильных данных"))

    def approve(dialog):
        dialog.confirm_base.setChecked(True)
        dialog._import()
        assert dialog.changed
        return dialog.result()

    monkeypatch.setattr(RegistryDialog, "exec", approve)
    window._on_load_registry_clicked()
    supplier = window.suppliers_view
    customer = window.customers_view
    assert supplier.table.item(FIRST_DATA_ROW, supplier._day_column(1, 0)).text() == "10,00"
    assert customer.table.item(FIRST_DATA_ROW, customer._day_column(1, 0)).text() == "23,00"
    mark = customer.table.item(FIRST_DATA_ROW, customer._day_column(1, customer.mark_index))
    assert mark.background().color().name() == styles.MARK_IN_WORK
    assert window.kkm_view.table.item(27, COL_AMOUNT).text() == "40,00"
    assert window.summary_view.value_text("month_kkm_total") == "40,00"
    assert window.summary_view.value_text("month_sale_invoiced") == "0,00"
    kkm_row = next(i for i in range(report.table.rowCount())
                   if report.table.item(i, 0).text() == "Выручка по отчетам ККМ с НДС")
    assert report.table.item(kkm_row, 6).text() == "40,00"
    assert journal.view.table.item(0, 1).text() == "10,00"
    journal.close()
    report.close()
    window.close()


@pytest.mark.parametrize("base", [BASE_MIR, BASE_KORELICHI])
def test_header_fits_import_buttons_on_narrow_and_wide_windows(qt_app, tmp_path, base):
    store = make_store(tmp_path, base=base)
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    for width, compact in ((1100, True), (1366, None), (1600, None), (1920, False)):
        window.resize(width, 500)
        qt_app.processEvents()
        if compact is not None:
            assert window._header_compact is compact
        assert window.statement_button.parentWidget() is window.registry_button.parentWidget()
        assert window.statement_button.geometry().right() < window.registry_button.geometry().left()
        for button in [*window.nav_buttons[:4], window.base_button,
                       window.statement_button, window.registry_button, window.update_button]:
            assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 8
    window.close()
