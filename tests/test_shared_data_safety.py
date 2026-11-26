"""Проверки общей базы и прав на изолированных вымышленных данных."""

from __future__ import annotations

import json

import pytest

from oplata import access, storage
from oplata.models import Counterparty


@pytest.mark.parametrize(
    ("initial", "other"),
    [
        (storage.BASE_MIR, storage.BASE_KORELICHI),
        (storage.BASE_KORELICHI, storage.BASE_MIR),
    ],
)
def test_switch_does_not_overwrite_newer_data_from_another_instance(
    tmp_path, initial, other
):
    """Просматривающий экземпляр не перезаписывает чужую новую запись."""
    # Оба файла уже существуют: проверяем именно переход, а не первый запуск.
    writer = storage.DataStore(base_key=initial)
    storage.DataStore(base_key=other)
    viewer = storage.DataStore(base_key=initial)

    writer.data.suppliers.append(Counterparty(name="Тестовый поставщик"))
    writer.save()
    initial_path = storage.data_path_for(initial)
    newer_bytes = initial_path.read_bytes()

    # У viewer в памяти старый снимок, обновление ещё не считано.
    assert not any(s.name == "Тестовый поставщик" for s in viewer.data.suppliers)
    viewer.switch_base(other)
    assert initial_path.read_bytes() == newer_bytes

    # При возвращении в базу читаются новые данные с диска.
    viewer.switch_base(initial)
    assert any(s.name == "Тестовый поставщик" for s in viewer.data.suppliers)


def test_first_creation_does_not_replace_file_that_appears_during_creation(
    tmp_path, monkeypatch
):
    target = storage.data_path_for(storage.BASE_MIR)
    new_data = storage.seed_data()
    new_data.suppliers = [Counterparty(name="Другая тестовая база")]
    original_write = storage.write_new_json

    def file_arrives(path, payload):
        path.write_text(json.dumps(new_data.to_dict(), ensure_ascii=False), encoding="utf-8")
        original_write(path, payload)

    monkeypatch.setattr(storage, "write_new_json", file_arrives)
    store = storage.DataStore(base_key=storage.BASE_MIR)
    assert [item.name for item in store.data.suppliers] == ["Другая тестовая база"]
    assert json.loads(target.read_text(encoding="utf-8"))["suppliers"][0]["name"] == "Другая тестовая база"


@pytest.mark.parametrize("problem", ["missing", "broken", "not-object"])
def test_shared_initial_open_requires_readable_existing_data(tmp_path, problem):
    path = storage.data_path_for(storage.BASE_MIR)
    if problem != "missing":
        path.write_text("{bad" if problem == "broken" else "[]", encoding="utf-8")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(storage.DataUnavailableError):
        storage.DataStore(base_key=storage.BASE_MIR, require_existing=True)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("problem", ["missing", "broken", "not-object"])
def test_reload_leaves_data_and_disk_untouched_when_file_unreadable(tmp_path, problem):
    store = storage.DataStore(base_key=storage.BASE_MIR)
    store.data.suppliers[0].name = "Последняя доступная запись"
    store.save()
    callbacks = []
    store.on_saved.append(lambda: callbacks.append(True))
    if problem == "missing":
        store.path.unlink()
        before = None
    else:
        store.path.write_text("{bad" if problem == "broken" else "[]", encoding="utf-8")
        before = store.path.read_bytes()

    assert store.reload() is False
    assert store.data.suppliers[0].name == "Последняя доступная запись"
    assert (store.path.read_bytes() if store.path.exists() else None) == before
    assert not callbacks


@pytest.mark.parametrize("problem", ["missing", "broken"])
def test_switch_does_not_create_or_open_demo_when_shared_base_is_unavailable(
    tmp_path, monkeypatch, problem
):
    current = storage.DataStore(base_key=storage.BASE_MIR)
    other = storage.DataStore(base_key=storage.BASE_KORELICHI)
    before_current = current.path.read_bytes()
    before_names = [s.name for s in current.data.suppliers]
    if problem == "missing":
        other.path.unlink()
        before_other = None
    else:
        other.path.write_text("{broken", encoding="utf-8")
        before_other = other.path.read_bytes()
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(tmp_path))

    assert current.switch_base(storage.BASE_KORELICHI) is False
    assert current.base_key == storage.BASE_MIR
    assert [s.name for s in current.data.suppliers] == before_names
    assert current.path.read_bytes() == before_current
    assert (other.path.read_bytes() if other.path.exists() else None) == before_other


def test_switch_keeps_previously_saved_changes_and_only_reads_old_base(tmp_path):
    store = storage.DataStore(base_key=storage.BASE_MIR)
    storage.DataStore(base_key=storage.BASE_KORELICHI)
    store.data.suppliers.append(Counterparty(name="Сохранённый поставщик"))
    store.save()
    path = store.path
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    store.switch_base(storage.BASE_KORELICHI)
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime
    store.switch_base(storage.BASE_MIR)
    assert any(s.name == "Сохранённый поставщик" for s in store.data.suppliers)


def test_old_manager_rights_recover_both_bases_without_enabling_input(
    tmp_path, monkeypatch
):
    """Восстанавливаем уже сохранённый v0.88.1 ошибочный флаг."""
    monkeypatch.setattr(access, "local_dir", lambda: tmp_path / "local")
    payload = {
        "users": [
            {
                "id": "manager",
                "name": "Руководитель",
                "pages": ["receipts", "suppliers", "customers", "data"],
                "base": None,
                "can_edit": False,
                "can_switch_base": False,
            },
            {
                "id": "mir",
                "name": "Пользователь МИР",
                "pages": ["customers"],
                "base": storage.BASE_MIR,
                "can_edit": False,
                "can_switch_base": True,
            },
        ],
        "password": {"salt": "test-salt", "hash": "test-hash"},
        "mail_settings": {"user": "test@example.invalid"},
    }
    users_path = tmp_path / "users.json"
    users_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    original = users_path.read_bytes()

    manager, mir = access.load_users()
    assert access.Access(manager).can_switch_base is True
    assert access.Access(manager).can_edit is False
    assert access.Access(mir).can_switch_base is False
    assert access.Access(mir).fixed_base == storage.BASE_MIR
    # При простом чтении файл, пароль и настройки не перезаписываются.
    assert users_path.read_bytes() == original

    access.save_current_user(manager)
    current = access.load_current_user()
    assert current is not None and current.id == "manager"
    assert access.Access(current).can_switch_base is True
    assert access.Access(current).can_edit is False


@pytest.mark.parametrize("can_edit", [False, True])
def test_both_bases_access_is_independent_of_editing(can_edit):
    user = access.User.from_dict(
        {
            "id": "custom",
            "name": "Просмотр двух баз",
            "pages": ["customers"],
            "base": None,
            "can_edit": can_edit,
            "can_switch_base": False,  # дефектные права прежней версии
        }
    )
    assert access.Access(user).can_switch_base is True
    assert access.Access(user).can_edit is can_edit


@pytest.mark.parametrize("bad_base", ["unknown", "", [], 123])
def test_invalid_base_does_not_grant_switching(bad_base):
    user = access.User.from_dict({"id": "invalid", "base": bad_base})
    assert access.Access(user).can_switch_base is False
    assert access.Access(access.User.from_dict({"id": "missing"})).can_switch_base is False


@pytest.mark.parametrize("existing_name", storage.PROMOTE_FILES)
def test_transfer_does_not_replace_any_existing_shared_file(
    tmp_path, monkeypatch, existing_name
):
    local = tmp_path / "local"
    shared = tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    for name in storage.PROMOTE_FILES:
        (local / name).write_text(f"локальный тест: {name}", encoding="utf-8")
    destination = shared / existing_name
    destination.write_text("существующие общие тестовые данные", encoding="utf-8")
    original = destination.read_bytes()
    monkeypatch.setattr(storage, "local_data_dir", lambda: local)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))

    assert storage.pending_promotion() == []
    assert storage.promote_to_shared() == []
    assert destination.read_bytes() == original
    assert [p.name for p in shared.iterdir()] == [existing_name]


def test_transfer_copies_only_into_empty_folder_and_keeps_originals(tmp_path, monkeypatch):
    local = tmp_path / "local"
    shared = tmp_path / "shared"
    local.mkdir()
    for name in storage.PROMOTE_FILES:
        (local / name).write_text(f"тестовые данные: {name}", encoding="utf-8")
    monkeypatch.setattr(storage, "local_data_dir", lambda: local)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))

    assert len(storage.pending_promotion()) == len(storage.PROMOTE_FILES)
    made = storage.promote_to_shared()
    assert {path.name for path in made} == set(storage.PROMOTE_FILES)
    for name in storage.PROMOTE_FILES:
        assert (local / name).read_bytes() == (shared / name).read_bytes()
    assert set(p.name for p in shared.iterdir()) == set(storage.PROMOTE_FILES)
    assert storage.pending_promotion() == []


def test_failed_excel_write_does_not_damage_previous_export(tmp_path, monkeypatch):
    from oplata import excelio
    from oplata.models import AppData

    target = tmp_path / "предыдущая-выгрузка.xlsx"
    target.write_bytes(b"previous synthetic export")

    def partial_failure(book, path):
        path.write_bytes(b"partial synthetic output")
        raise OSError("simulated disk error")

    monkeypatch.setattr(excelio.Workbook, "save", partial_failure)
    with pytest.raises(excelio.ExcelError):
        excelio.export_bases({storage.BASE_MIR: AppData()}, target)
    assert target.read_bytes() == b"previous synthetic export"
    assert list(tmp_path.iterdir()) == [target]


def test_transfer_stops_if_other_shared_file_appears_during_copy(tmp_path, monkeypatch):
    local = tmp_path / "local"
    shared = tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    (local / "data.json").write_text("локальная тестовая база", encoding="utf-8")
    monkeypatch.setattr(storage, "local_data_dir", lambda: local)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))
    real_copy = storage.shutil.copy2

    def copy_then_simulate_other_shared_file(source, temporary):
        result = real_copy(source, temporary)
        (shared / "users.json").write_text("чужие настройки", encoding="utf-8")
        return result

    monkeypatch.setattr(storage.shutil, "copy2", copy_then_simulate_other_shared_file)
    assert storage.promote_to_shared() == []
    assert (shared / "users.json").read_text(encoding="utf-8") == "чужие настройки"
    assert sorted(path.name for path in shared.iterdir()) == ["users.json"]


def test_transfer_does_not_replace_file_that_appears_during_copy(tmp_path, monkeypatch):
    local = tmp_path / "local"
    shared = tmp_path / "shared"
    local.mkdir()
    shared.mkdir()
    (local / "data.json").write_text("старая тестовая база", encoding="utf-8")
    monkeypatch.setattr(storage, "local_data_dir", lambda: local)
    monkeypatch.setenv("OPLATA_DATA_DIR_GOOGLE", str(shared))
    real_copy = storage.shutil.copy2

    def copy_then_simulate_new_shared_file(source, temporary):
        result = real_copy(source, temporary)
        (shared / "data.json").write_text("новая тестовая база", encoding="utf-8")
        return result

    monkeypatch.setattr(storage.shutil, "copy2", copy_then_simulate_new_shared_file)
    assert storage.promote_to_shared() == []
    assert (shared / "data.json").read_text(encoding="utf-8") == "новая тестовая база"
    assert sorted(path.name for path in shared.iterdir()) == ["data.json"]
