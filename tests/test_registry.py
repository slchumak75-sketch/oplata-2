"""Реестр проверяем только на вымышленных книгах и изолированной базе."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from oplata import calc
from oplata.excelio import export_bases, import_bases
from oplata.models import (
    KIND_INCOME, KIND_PAYMENT, KIND_SALE, KKM_KIND_REPORT, KKM_KIND_RETURN,
    AppData, Counterparty, KkmReport, Operation,
)
from oplata.registry import (
    REPORT, RETURN, SALE, SUPPLY, RegistryError, auto_assignments,
    capture_registry_snapshot, commit_registry_plan, parse_registry, plan_registry,
)
from oplata.storage import BASE_KORELICHI, BASE_MIR, DataStore


def document(kind, number, amount, *, day="10.11.2026", status="Проведен",
             unp=None, info="Тестовая информация", currency="BYN"):
    return [None, 1, day, kind, number, "Тест", status, amount, currency, info, unp]


def workbook(path: Path, rows):
    book = Workbook()
    sheet = book.active
    sheet.append(["Тестовый реестр"])
    for _ in range(5):
        sheet.append([])
    sheet.append([None, "№ п/п", "Дата", "Документ", "Номер", "Автор",
                  "Статус", "Сумма", "Вал.", "Информация", None])
    for row in rows:
        sheet.append(row)
    book.save(path)
    book.close()
    return path


def example_rows():
    return [
        document("Поступление в розницу (Товары)", 11, "1'234.56", status=" ", unp=111222333),
        document("Поступление в розницу (Товары)", 12, "10.44", unp=111222333),
        document("Реализация (Товары)", 21, "200.00", status="Проведен", unp=444555666),
        document("Реализация (Товары)", 22, "30.00", status=" ", unp=444555666),
        document("Отчет ККМ (Касса)", 31, "100.00"),
        document("Отчет ККМ (Касса)", 32, "50.00"),
        document("Возврат от покупателя (Товары)", 41, "3.00"),
        document("Возврат покупателя (Товары)", 42, "4.00"),
        document("Переоценка (Товары)", 51, "ошибка", currency="???"),
        document("Другой документ", 61, "не сумма", currency="???"),
    ]


def store_with_partners(tmp_path):
    store = DataStore(tmp_path / "data.json", base_key=BASE_MIR)
    store.data = AppData(
        suppliers=[Counterparty(name="Тестовый поставщик", unp="111222333",
                                id="supplier", deferment_days=3)],
        customers=[Counterparty(name="Тестовый покупатель", unp="444555666", id="customer")],
    )
    store.save()
    return store


def prepare(store, path):
    registry = parse_registry(path)
    snapshot = capture_registry_snapshot(store)
    plan = plan_registry(registry, store.data, store.base_key,
                         auto_assignments(registry, store.data))
    return registry, snapshot, plan


@pytest.mark.parametrize("value, expected", [
    ("19.11.26", date(2026, 11, 19)),
    ("19.11.26 12:34:56", date(2026, 11, 19)),
    ("01.01.69", date(2069, 1, 1)),
    ("10.11.2026", date(2026, 11, 10)),
    ("2026-11-10", date(2026, 11, 10)),
], ids=["short", "short_with_time", "short_late_century", "four_digit", "iso"])
def test_registry_recognizes_text_dates(tmp_path, value, expected):
    path = workbook(tmp_path / "registry.xlsx", [document("Отчет ККМ", 1, "10", day=value)])
    registry = parse_registry(path)
    assert registry.rows[0].day == expected
    assert not registry.rows[0].issues


@pytest.mark.parametrize("value", ["31.02.26", "19.11.26 неверно", "19.11.202"],
                         ids=["impossible", "extra_text", "three_digit_year"])
def test_invalid_text_dates_still_block_import(tmp_path, value):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", [document("Отчет ККМ", 1, "10", day=value)])
    registry, _, plan = prepare(store, path)
    assert registry.rows[0].day is None
    assert "неверная или пустая дата" in registry.rows[0].issues
    assert plan.issues and plan.candidate is None


def test_full_import_replaces_days_aggregates_and_is_idempotent(tmp_path):
    store = store_with_partners(tmp_path)
    day = "2026-11-10"
    store.data.operations = [
        Operation(kind=KIND_INCOME, counterparty_id="supplier", date=day, amount="99.00",
                  document="старая", comment="старый комментарий", due_date="2026-11-30"),
        Operation(kind=KIND_SALE, counterparty_id="customer", date=day, amount="80.00",
                  document="старая", invoiced=True),
        Operation(kind=KIND_PAYMENT, counterparty_id="customer", date=day, amount="7.00"),
        Operation(kind=KIND_SALE, counterparty_id="customer", date="2026-11-09", amount="5.00",
                  invoiced=True),
    ]
    store.data.kkm_reports = [
        KkmReport(date=day, kind=KKM_KIND_REPORT, slot=0, amount="1.00"),
        KkmReport(date=day, kind=KKM_KIND_REPORT, slot=1, amount="2.00"),
        KkmReport(date=day, kind=KKM_KIND_RETURN, slot=2, amount="3.00"),
    ]
    store.save()
    old_bytes = store.path.read_bytes()
    path = workbook(tmp_path / "registry.xlsx", example_rows())
    registry, snapshot, plan = prepare(store, path)
    assert registry.ignored_revaluation == 1
    assert registry.ignored_other == 1
    assert not plan.issues
    assert all(change.action == "replace" for change in plan.changes)
    assert next(c for c in plan.changes if c.kind == SUPPLY).amount == Decimal("1245.00")
    sale = next(c for c in plan.changes if c.kind == SALE)
    assert sale.mixed_status and not sale.invoiced
    assert "выписано (зелёная)" in sale.existing_details[0]
    assert next(c for c in plan.changes if c.kind == RETURN).amount == Decimal("7.00")

    original_object = store.data
    result = commit_registry_plan(store, snapshot, plan)
    assert result.saved and result.backups
    assert all(backup.read_bytes() == old_bytes for backup in result.backups)
    assert store.data is original_object
    income = [op for op in store.data.operations if op.kind == KIND_INCOME and op.date == day]
    assert [(op.document, op.amount, op.due_date) for op in income] == [
        ("11", "1234.56", "2026-11-13"), ("12", "10.44", "2026-11-13")
    ]
    sales = [op for op in store.data.operations if op.kind == KIND_SALE and op.date == day]
    assert len(sales) == 1
    assert (sales[0].amount, sales[0].document, sales[0].invoiced) == ("230.00", "21, 22", False)
    assert calc.month_realization_invoiced(store.data, 2026, 11) == Decimal("5.00")
    assert [(r.slot, r.kind, r.amount) for r in store.data.kkm_reports] == [
        (0, KKM_KIND_REPORT, "100.00"), (1, KKM_KIND_REPORT, "50.00"),
        (2, KKM_KIND_RETURN, "7.00"),
    ]
    assert any(op.kind == KIND_PAYMENT and op.amount == "7.00" for op in store.data.operations)
    assert any(op.date == "2026-11-09" for op in store.data.operations)

    saved_bytes = store.path.read_bytes()
    _, again_snapshot, again_plan = prepare(store, path)
    assert not again_plan.issues and not again_plan.has_changes
    assert all(change.action == "same" for change in again_plan.changes)
    assert not commit_registry_plan(store, again_snapshot, again_plan).saved
    assert store.path.read_bytes() == saved_bytes

    # Обновление статуса документа меняет объединённую сумму на зелёную.
    rows = example_rows()
    rows[3][6] = "Проведен"
    workbook(path, rows)
    _, snapshot, changed = prepare(store, path)
    assert next(c for c in changed.changes if c.kind == SALE).invoiced
    commit_registry_plan(store, snapshot, changed)
    assert calc.month_realization_invoiced(store.data, 2026, 11) == Decimal("235.00")


def test_old_due_and_comment_preserved_when_same_document_is_updated(tmp_path):
    store = store_with_partners(tmp_path)
    previous = Operation(kind=KIND_INCOME, counterparty_id="supplier", date="2026-11-10",
                         amount="8.00", document="11", comment="Тест",
                         due_date="2026-12-05", id="existing")
    store.data.operations.append(previous)
    store.save()
    path = workbook(tmp_path / "registry.xlsx", [example_rows()[0]])
    _, snapshot, plan = prepare(store, path)
    commit_registry_plan(store, snapshot, plan)
    saved = store.data.operations[0]
    assert (saved.id, saved.amount, saved.due_date, saved.comment) == (
        "existing", "1234.56", "2026-12-05", "Тест"
    )


def test_no_guessing_on_missing_duplicate_or_archived_unp(tmp_path):
    store = store_with_partners(tmp_path)
    store.data.suppliers.extend([
        Counterparty(name="Тест дубль", unp="111222333", id="duplicate", note="другая карточка"),
        Counterparty(name="Архивный", unp="222333444", id="archived", archived=True),
    ])
    store.save()
    path = workbook(tmp_path / "registry.xlsx", [
        document("Поступление в розницу", 1, "1.00", unp=111222333),
        document("Поступление в розницу", 2, "2.00", unp=None),
        document("Поступление в розницу", 3, "3.00", unp=222333444),
    ])
    registry = parse_registry(path)
    assert auto_assignments(registry, store.data) == {}
    blocked = plan_registry(registry, store.data, BASE_MIR, {})
    assert len(blocked.issues) == 3 and blocked.candidate is None
    assignments = {r.row_number: target for r, target in zip(
        registry.rows, ("supplier", "duplicate", "archived")
    )}
    permitted = plan_registry(registry, store.data, BASE_MIR, assignments)
    assert not permitted.issues
    assert [c.counterparty_id for c in permitted.changes] == ["archived", "duplicate", "supplier"]


def test_korelichi_single_report_and_aggregated_returns_use_correct_slots(tmp_path):
    store = DataStore(tmp_path / "data-korelichi.json", base_key=BASE_KORELICHI)
    store.data = AppData()
    store.save()
    rows = [example_rows()[4], example_rows()[6], example_rows()[7]]
    path = workbook(tmp_path / "registry.xlsx", rows)
    _, snapshot, plan = prepare(store, path)
    assert not plan.issues
    commit_registry_plan(store, snapshot, plan)
    assert [(r.slot, r.kind, r.amount) for r in store.data.kkm_reports] == [
        (0, KKM_KIND_REPORT, "100.00"), (1, KKM_KIND_RETURN, "7.00")
    ]


def test_reordered_customer_documents_do_not_replace_identical_import(tmp_path):
    store = store_with_partners(tmp_path)
    rows = [example_rows()[2], example_rows()[3]]
    path = workbook(tmp_path / "registry.xlsx", rows)
    _, snapshot, plan = prepare(store, path)
    commit_registry_plan(store, snapshot, plan)
    before = store.path.read_bytes()
    workbook(path, list(reversed(rows)))
    _, snapshot, plan = prepare(store, path)
    assert not plan.issues and not plan.has_changes
    assert not commit_registry_plan(store, snapshot, plan).saved
    assert store.path.read_bytes() == before


def test_more_reports_than_slots_are_blocked_not_dropped_or_summed(tmp_path):
    store = store_with_partners(tmp_path)
    rows = [example_rows()[4], example_rows()[5]]
    path = workbook(tmp_path / "registry.xlsx", rows)
    registry = parse_registry(path)
    plan = plan_registry(registry, store.data, BASE_KORELICHI, {})
    assert plan.issues and plan.candidate is None
    assert "больше, чем строк" in " ".join(plan.issues)
    assert not store.data.kkm_reports


def test_unknown_status_foreign_currency_invalid_amount_formula_and_duplicate_fail_closed(tmp_path):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", [
        document("Реализация", 1, "12.00", status="неясно", unp=444555666),
        document("Реализация", 2, "1,234", currency="USD", unp=444555666),
        document("Отчет ККМ", 3, "=1+2"),
        document("Отчет ККМ", 3, "5.00"),
    ])
    registry = parse_registry(path)
    plan = plan_registry(registry, store.data, BASE_MIR, auto_assignments(registry, store.data))
    assert any("статус" in issue for issue in plan.issues)
    assert any("валют" in issue for issue in plan.issues)
    assert any("суммы" in issue for issue in plan.issues)
    assert any("формул" in issue for issue in plan.issues)
    assert any("повторяется" in issue for issue in plan.issues)
    assert plan.candidate is None
    before = store.path.read_bytes()
    with pytest.raises(RegistryError):
        commit_registry_plan(store, capture_registry_snapshot(store), plan)
    assert store.path.read_bytes() == before


def test_partial_file_warns_and_moved_document_blocks_if_old_target_remains(tmp_path):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", example_rows())
    _, snapshot, plan = prepare(store, path)
    commit_registry_plan(store, snapshot, plan)

    # Меньше документов за этот день: сумма заменится, а не прибавится.
    workbook(path, [example_rows()[2]])
    _, _, partial = prepare(store, path)
    assert not partial.issues
    assert partial.changes[0].missing_previous == 1
    assert partial.changes[0].action == "replace"

    moved = example_rows()[2]
    moved[2] = "11.11.2026"
    workbook(path, [moved])
    _, _, blocked = prepare(store, path)
    assert any("другую дату" in issue for issue in blocked.issues)
    assert blocked.candidate is None


def test_report_reordering_requires_replacing_the_old_slot_too(tmp_path):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", [example_rows()[4], example_rows()[5]])
    _, snapshot, plan = prepare(store, path)
    commit_registry_plan(store, snapshot, plan)
    workbook(path, [example_rows()[5]])
    _, _, blocked = prepare(store, path)
    assert any("другую дату или строку" in issue for issue in blocked.issues)
    workbook(path, [example_rows()[5], example_rows()[4]])
    _, snapshot, swapped = prepare(store, path)
    assert not swapped.issues
    commit_registry_plan(store, snapshot, swapped)
    reports = sorted(store.data.kkm_reports, key=lambda item: item.slot)
    assert [item.amount for item in reports] == ["50.00", "100.00"]


def test_disk_change_guard_and_save_error_leave_original_data_intact(tmp_path, monkeypatch):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", [example_rows()[0]])
    _, snapshot, plan = prepare(store, path)
    original_data = store.data.to_dict()
    newer = store.path.read_bytes() + b"\n"
    store.path.write_bytes(newer)
    with pytest.raises(RegistryError, match="изменился"):
        commit_registry_plan(store, snapshot, plan)
    assert store.path.read_bytes() == newer
    assert store.data.to_dict() == original_data

    # Сначала вернём синтетический файл к версии снимка, затем имитируем
    # отказ атомарной замены: ни байты, ни объект базы не изменятся.
    store.path.write_bytes(snapshot.content)
    monkeypatch.setattr("oplata.registry.os.replace", lambda *_: (_ for _ in ()).throw(OSError()))
    with pytest.raises(RegistryError, match="безопасно сохранить"):
        commit_registry_plan(store, snapshot, plan)
    assert store.path.read_bytes() == snapshot.content
    assert store.data.to_dict() == original_data


def test_rights_rechecked_before_commit_and_backup_required(tmp_path, monkeypatch):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", [example_rows()[0]])
    _, snapshot, plan = prepare(store, path)
    before = store.path.read_bytes()
    with pytest.raises(RegistryError, match="Права"):
        commit_registry_plan(store, snapshot, plan, guard=lambda: "Права изменились.")
    assert store.path.read_bytes() == before
    monkeypatch.setattr("oplata.registry.backup_dirs", lambda: [tmp_path / "not-a-dir" / "child"])
    (tmp_path / "not-a-dir").write_text("x", encoding="utf-8")
    with pytest.raises(RegistryError, match="копию"):
        commit_registry_plan(store, snapshot, plan)
    assert store.path.read_bytes() == before


def test_registry_history_survives_excel_export_and_older_files(tmp_path):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", [example_rows()[0], example_rows()[4]])
    _, snapshot, plan = prepare(store, path)
    commit_registry_plan(store, snapshot, plan)
    exported = tmp_path / "export.xlsx"
    export_bases({BASE_MIR: store.data}, exported)
    loaded = import_bases(exported)[BASE_MIR]
    assert loaded.imported_registry_rows == store.data.imported_registry_rows
    book = load_workbook(exported)
    del book["МИР. Импорт реестра"]
    book.save(exported)
    book.close()
    assert import_bases(exported)[BASE_MIR].imported_registry_rows == []


@pytest.mark.parametrize("damage", ["extra_top", "extra_nested", "future_schema", "dropped_entry"])
def test_unknown_or_dropped_database_content_blocks_import(tmp_path, damage):
    store = store_with_partners(tmp_path)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    if damage == "extra_top":
        payload["future_section"] = {"example": "не терять"}
    elif damage == "extra_nested":
        payload["suppliers"][0]["future_property"] = "не терять"
    elif damage == "future_schema":
        payload["schema"] = 2
    else:
        payload["year_start"]["supplier_debts"].append("неизвестная запись")
    store.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    store.data = AppData.from_dict(payload)
    before = store.path.read_bytes()
    with pytest.raises(RegistryError, match="неизвестные или повреждённые"):
        capture_registry_snapshot(store)
    assert store.path.read_bytes() == before


def test_literal_excel_formula_in_document_number_is_blocked(tmp_path):
    store = store_with_partners(tmp_path)
    path = workbook(tmp_path / "registry.xlsx", [example_rows()[0]])
    book = load_workbook(path)
    cell = book.active.cell(8, 5)
    cell.value = "=1+2"
    cell.data_type = "s"  # Excel-текст, а не ячейка с формулой.
    book.save(path)
    book.close()
    registry, _, plan = prepare(store, path)
    assert "недопустимом формате" in " ".join(registry.rows[0].issues)
    assert plan.issues and plan.candidate is None


def test_invalid_or_ambiguous_header_rejected(tmp_path):
    path = tmp_path / "registry.xlsx"
    book = Workbook()
    book.active.append(["Не реестр"])
    book.save(path)
    book.close()
    with pytest.raises(RegistryError, match="заголовок"):
        parse_registry(path)
    workbook(path, [example_rows()[0]])
    book = load_workbook(path)
    book.copy_worksheet(book.active)
    book.save(path)
    book.close()
    with pytest.raises(RegistryError, match="единственный"):
        parse_registry(path)
