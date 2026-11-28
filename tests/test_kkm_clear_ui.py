"""Очистка ККМ только на изолированных, вымышленных данных."""
from datetime import date

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from oplata.app import MainWindow
from oplata.models import AppData, KkmReport, KKM_KIND_REPORT, KKM_KIND_RETURN
from oplata.storage import BASE_KORELICHI, BASE_MIR, DataStore


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize("base", [BASE_MIR, BASE_KORELICHI], ids=["mir", "korelichi"])
@pytest.mark.parametrize("mode", ["period", "base"], ids=["period", "base"])
def test_clear_kkm_and_history_with_confirmation(qt_app, tmp_path, monkeypatch, base, mode):
    store = DataStore(tmp_path / "data.json", base_key=base)
    store.data = AppData(kkm_reports=[
        KkmReport(date="2026-11-10", kind=KKM_KIND_REPORT, slot=0, amount="123", id="inside"),
        KkmReport(date="2026-12-01", kind=KKM_KIND_RETURN,
                  slot=2 if base == BASE_MIR else 1, amount="7", id="outside"),
    ], imported_registry_rows=[
        {"kind": "report", "date": "2026-11-10", "slot": 0, "number": "1", "record_id": "inside"},
        {"kind": "return", "date": "2026-12-01", "slot": 2 if base == BASE_MIR else 1,
         "number": "2", "record_id": "outside"},
    ])
    store.save()
    window = MainWindow(store)
    window.autosave_on_exit = False
    window.show()
    monkeypatch.setattr(window, "_ask_password", lambda *_: True)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: pytest.fail("неожиданная ошибка"))

    class Period:
        def __init__(self, *args, **kwargs): pass
        def exec(self): return True
        def period(self): return date(2026, 11, 1), date(2026, 11, 30)

    monkeypatch.setattr("oplata.app.PeriodDialog", Period)
    def make_copy(*_args):
        path = tmp_path / "backup.json"
        path.write_bytes(store.path.read_bytes())
        return [path]
    monkeypatch.setattr("oplata.app.make_backup", make_copy)
    def confirm(_title, _question, details):
        messages.append(details)
        return True
    monkeypatch.setattr(window, "_confirm_yes_no", confirm)
    if mode == "period":
        window._on_clear_tables_clicked()
        assert [r.id for r in store.data.kkm_reports] == ["outside"]
        assert [link["record_id"] for link in store.data.imported_registry_rows] == ["outside"]
        assert "ККМ и возвратов — 1" in messages[0]
    else:
        window._on_clear_base_clicked()
        assert store.data.kkm_reports == []
        assert store.data.imported_registry_rows == []
        assert "ККМ и возвратов — 2" in messages[0]
    assert (tmp_path / "backup.json").exists()
    loaded = DataStore(store.path, base_key=base)
    assert [r.id for r in loaded.data.kkm_reports] == [r.id for r in store.data.kkm_reports]
    window.close()


@pytest.mark.parametrize("mode", ["period", "base"], ids=["period", "base"])
def test_clear_kkm_stops_if_backup_fails(qt_app, tmp_path, monkeypatch, mode):
    store = DataStore(tmp_path / "data.json", base_key=BASE_MIR)
    store.data = AppData(kkm_reports=[KkmReport(date="2026-11-10", amount="10")])
    store.save()
    before = store.path.read_bytes()
    window = MainWindow(store)
    window.autosave_on_exit = False
    monkeypatch.setattr(window, "_ask_password", lambda *_: True)
    monkeypatch.setattr(window, "_confirm_yes_no", lambda *_: True)
    monkeypatch.setattr("oplata.app.make_backup", lambda *_: [])
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    class Period:
        def __init__(self, *args, **kwargs): pass
        def exec(self): return True
        def period(self): return date(2026, 11, 1), date(2026, 11, 30)
    monkeypatch.setattr("oplata.app.PeriodDialog", Period)
    (window._on_clear_tables_clicked if mode == "period" else window._on_clear_base_clicked)()
    assert warnings and "копию базы" in warnings[0]
    assert store.path.read_bytes() == before and len(store.data.kkm_reports) == 1
    window.close()


@pytest.mark.parametrize("mode", ["period", "base"], ids=["period", "base"])
def test_clear_kkm_stops_if_database_changed(qt_app, tmp_path, monkeypatch, mode):
    store = DataStore(tmp_path / "data.json", base_key=BASE_MIR)
    store.data = AppData(kkm_reports=[KkmReport(date="2026-11-10", amount="10")])
    store.save()
    window = MainWindow(store)
    window.autosave_on_exit = False
    monkeypatch.setattr(window, "_ask_password", lambda *_: True)
    monkeypatch.setattr(window, "_confirm_yes_no", lambda *_: True)
    monkeypatch.setattr("oplata.app.make_backup", lambda *_: pytest.fail("устаревшую базу не копировать"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    class Period:
        def __init__(self, *args, **kwargs): pass
        def exec(self): return True
        def period(self): return date(2026, 11, 1), date(2026, 11, 30)
    monkeypatch.setattr("oplata.app.PeriodDialog", Period)
    other = DataStore(store.path, base_key=BASE_MIR)
    other.data.kkm_reports.append(KkmReport(date="2026-11-11", amount="55"))
    other.save()
    before = store.path.read_bytes()
    (window._on_clear_tables_clicked if mode == "period" else window._on_clear_base_clicked)()
    assert warnings
    assert store.path.read_bytes() == before
    assert len(store.data.kkm_reports) == 1
    window.close()


def test_update_not_found_explains_release_folder(qt_app, tmp_path):
    from oplata.access import Access
    from oplata.views.update_view import UpdateDialog

    store = DataStore(tmp_path / "data.json", base_key=BASE_MIR)
    store.data = AppData()
    store.save()
    dialog = UpdateDialog(store, Access())
    dialog.check_for_update()
    assert "Выпуск" in dialog.status_label.text()
    assert "GitHub автоматически" in dialog.status_label.text()
    assert "Update.zip" in dialog.status_label.text()
    assert not dialog.install_button.isEnabled()
    dialog.close()


def test_clear_kkm_stops_if_database_changes_while_confirming(qt_app, tmp_path, monkeypatch):
    store = DataStore(tmp_path / "data.json", base_key=BASE_MIR)
    store.data = AppData(kkm_reports=[KkmReport(date="2026-11-10", amount="10")])
    store.save()
    window = MainWindow(store)
    window.autosave_on_exit = False
    monkeypatch.setattr(window, "_ask_password", lambda *_: True)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    def external_change(*_):
        writer = DataStore(store.path, base_key=BASE_MIR)
        writer.data.kkm_reports.append(KkmReport(date="2026-11-11", amount="55"))
        writer.save()
        return True
    monkeypatch.setattr(window, "_confirm_yes_no", external_change)
    monkeypatch.setattr("oplata.app.make_backup", lambda *_: pytest.fail("изменённую базу не очищать"))
    window._on_clear_base_clicked()
    assert warnings
    assert len(DataStore(store.path, base_key=BASE_MIR).data.kkm_reports) == 2
    assert len(store.data.kkm_reports) == 1
    window.close()
