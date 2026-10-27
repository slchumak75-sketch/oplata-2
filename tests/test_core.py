"""Тесты ядра без Qt: даты, модели, хранилище, заглушки расчётов."""

from datetime import date
from decimal import Decimal

import pytest

from oplata import calc
from oplata.dates import fmt_date, fmt_money, month_title, parse_money, week_days, week_monday
from oplata.models import (
    KIND_INCOME,
    KIND_PAYMENT,
    AppData,
    Counterparty,
    Operation,
    add_delivery,
    daily_operation_total,
    seed_data,
    set_daily_operation,
)
from oplata.storage import DataStore


def test_week_monday_from_mockup():
    # С макета: понедельник 26.10.2026, неделя 26.10.2026 — 01.11.2026.
    assert date(2026, 10, 26).weekday() == 0
    assert week_monday(date(2026, 10, 28)) == date(2026, 10, 26)
    assert week_monday(date(2026, 11, 1)) == date(2026, 10, 26)


def test_week_days_cross_month():
    days = week_days(date(2026, 10, 26))
    assert days[0] == date(2026, 10, 26)
    assert days[-1] == date(2026, 11, 1)
    assert [month_title(d) for d in (days[0], days[-1])] == [
        "Октябрь 2026",
        "Ноябрь 2026",
    ]


def test_formatting_like_mockups():
    assert fmt_date(date(2026, 10, 26)) == "26.10.2026"
    assert fmt_money(Decimal("0")) == "0,00 руб."
    assert fmt_money(Decimal("1234.5")) == "1 234,50 руб."
    assert fmt_money(Decimal("-10")) == "-10,00 руб."


def test_seed_matches_mockups():
    data = seed_data()
    assert [s.name for s in data.suppliers] == ["ааа", "ббб", "ггг", "eee", "жжж"]
    by_name = {s.name: s for s in data.suppliers}
    assert by_name["ггг"].deferment_days == 10  # отсрочка с макета карточки
    assert data.customers == []   # на макете «Покупателей: 0»
    assert data.operations == []  # на макете «Найдено: 0»


def test_appdata_roundtrip():
    data = AppData(
        suppliers=[Counterparty(name="X", deferment_days=5, prepayment=True)],
        day_overrides={"2026-01-01": "off"},
    )
    restored = AppData.from_dict(data.to_dict())
    assert restored.suppliers[0].name == "X"
    assert restored.suppliers[0].deferment_days == 5
    assert restored.suppliers[0].prepayment is True
    assert restored.day_overrides == {"2026-01-01": "off"}


def test_storage_creates_and_reloads(tmp_path):
    path = tmp_path / "data.json"
    store = DataStore(path)
    assert path.exists()  # первый запуск создал файл с демо-данными
    store.data.customers.append(Counterparty(name="Покупатель 1"))
    store.save()
    reloaded = DataStore(path)
    assert [c.name for c in reloaded.data.customers] == ["Покупатель 1"]


def test_calc_summary_still_stub():
    """«Сводка» пока заглушка: её формулы пользователь ещё не давал.

    Долг в таблице «Поставщики» уже считается — см. тесты ниже.
    """
    today = date(2026, 10, 26)
    totals = calc.summary_totals(today)
    assert totals.total_debt == totals.overdue == Decimal("0")
    row = calc.supplier_debt_row("any", today)
    assert row.debt == Decimal("0") and row.next_due_date is None
    assert calc.overdue_payments(today) == []
    assert calc.week_debt(today) == Decimal("0")  # без данных считать нечего
    assert calc.week_cell_value("any", today, "income") == ""
    assert calc.week_cell_value("any", today, "debt") == ""  # без data нет условий


def test_parse_money_user_input():
    assert parse_money("") is None
    assert parse_money("  ") is None
    assert parse_money("1500") == Decimal("1500.00")
    assert parse_money("1 500,75") == Decimal("1500.75")
    assert parse_money("1500.5") == Decimal("1500.50")
    assert parse_money("1 500,75 руб.") == Decimal("1500.75")
    with pytest.raises(ValueError):
        parse_money("привет")


def test_daily_operation_input_roundtrip():
    data = seed_data()
    supplier = data.suppliers[0]
    day = date(2026, 10, 26)
    iso = day.isoformat()

    # Ввод Приход/Оплата в ячейки -> операции дня
    set_daily_operation(data, supplier.id, iso, KIND_INCOME, Decimal("1000"))
    set_daily_operation(data, supplier.id, iso, KIND_PAYMENT, Decimal("250.5"))
    assert daily_operation_total(data.operations, supplier.id, iso, KIND_INCOME) == Decimal("1000.00")
    assert calc.week_cell_value(supplier.id, day, "income", data.operations) == "1 000,00"
    assert calc.week_cell_value(supplier.id, day, "payment", data.operations) == "250,50"
    # Долг: у «ааа» отсрочка 0, срок = день поставки. Сумма остаётся в
    # «Долге» полностью (1000), хотя рядом есть оплата 250,50: по
    # требованию пользователя оплата пока долг не уменьшает (архив).
    assert calc.week_cell_value(supplier.id, day, "debt", data.operations, data) == "1 000,00"
    # ВСЕГО по колонке
    ids = [s.id for s in data.suppliers]
    assert calc.week_column_total(ids, day, "income", data.operations) == "1 000,00"

    # Повторный ввод заменяет, а не суммирует; пусто/0 — удаляет
    set_daily_operation(data, supplier.id, iso, KIND_INCOME, Decimal("300"))
    assert daily_operation_total(data.operations, supplier.id, iso, KIND_INCOME) == Decimal("300.00")
    set_daily_operation(data, supplier.id, iso, KIND_INCOME, None)
    assert daily_operation_total(data.operations, supplier.id, iso, KIND_INCOME) == Decimal("0")
    set_daily_operation(data, supplier.id, iso, KIND_PAYMENT, Decimal("0"))
    assert data.operations == []


def test_set_daily_operation_preserves_single_operation_metadata():
    supplier = Counterparty(name="S")
    operation = Operation(
        kind=KIND_PAYMENT,
        counterparty_id=supplier.id,
        date="2026-10-26",
        amount="100.00",
        document="П/п 17",
        comment="частично",
    )
    data = AppData(suppliers=[supplier], operations=[operation])

    set_daily_operation(
        data, supplier.id, "2026-10-26", KIND_PAYMENT, Decimal("150")
    )

    assert data.operations == [operation]  # тот же объект, только сумма новая
    assert operation.amount == "150.00"
    assert operation.document == "П/п 17"
    assert operation.comment == "частично"

    delivery = Operation(
        kind=KIND_INCOME,
        counterparty_id=supplier.id,
        date="2026-10-26",
        amount="100.00",
        document="Накладная 42",
    )
    data.operations.append(delivery)
    set_daily_operation(
        data, supplier.id, "2026-10-26", KIND_INCOME, Decimal("200"), "2026-11-05"
    )
    assert delivery.amount == "200.00"
    assert delivery.document == "Накладная 42"
    assert delivery.due_date == "2026-11-05"  # пустому сроку дата присвоена

    set_daily_operation(
        data, supplier.id, "2026-10-26", KIND_INCOME, Decimal("250"), "2026-12-01"
    )
    assert delivery.amount == "250.00"
    assert delivery.due_date == "2026-11-05"  # сохранённый срок не перезаписан


def test_due_date_rules():
    """Срок оплаты: дата + отсрочка, режимы без отсрочки, перенос с выходного."""
    deferred = Counterparty(name="X", deferment_days=10)
    # Пн 26.10.2026 + 10 дней = Чт 05.11.2026 (рабочий, переноса нет)
    assert date(2026, 10, 26).weekday() == 0
    assert calc.payment_due_date(date(2026, 10, 26), deferred, {}) == date(2026, 11, 5)

    deferred2 = Counterparty(name="Y", deferment_days=2)
    # Чт 29.10 + 2 = Сб 31.10 (выходной) -> Пн 02.11
    assert calc.payment_due_date(date(2026, 10, 29), deferred2, {}) == date(2026, 11, 2)
    # Пн 02.11 помечен выходным в календаре «Данные» -> Вт 03.11
    overrides = {"2026-11-02": "off"}
    assert calc.payment_due_date(date(2026, 10, 29), deferred2, overrides) == date(2026, 11, 3)

    # Предоплата: отсрочка не применяется, но перенос с выходного работает
    prepaid = Counterparty(name="Z", deferment_days=30, prepayment=True)
    assert calc.payment_due_date(date(2026, 10, 27), prepaid, {}) == date(2026, 10, 27)
    assert calc.payment_due_date(date(2026, 10, 31), prepaid, {}) == date(2026, 11, 2)
    realization = Counterparty(name="R", deferment_days=30, realization=True)
    assert calc.raw_due_date(date(2026, 10, 27), realization) == date(2026, 10, 27)


def test_debt_is_archival_payment_does_not_reduce_it():
    """Требование пользователя (v0.3.1): сумма из «Долга» не исчезает.

    Оплата пока НЕ уменьшает ни «Долг», ни «за неделю», ни «на сегодня» —
    правила оплат пользователь разъяснит отдельным этапом.
    """
    supplier = Counterparty(name="S", deferment_days=7)
    data = AppData(suppliers=[supplier])
    add_delivery(data, supplier.id, "2026-10-19", "2026-10-26", Decimal("1000"))
    add_delivery(data, supplier.id, "2026-10-20", "2026-10-27", Decimal("500"))
    # Оплата 1200 записана, но долг не трогает (архив сроков как есть)
    set_daily_operation(data, supplier.id, "2026-10-27", KIND_PAYMENT, Decimal("1200"))

    due_map = calc.debt_by_due_date(data, supplier)
    assert due_map == {
        date(2026, 10, 26): Decimal("1000"),
        date(2026, 10, 27): Decimal("500"),
    }
    assert calc.week_cell_value(supplier.id, date(2026, 10, 26), "debt", data.operations, data) == "1 000,00"
    assert calc.week_cell_value(supplier.id, date(2026, 10, 27), "debt", data.operations, data) == "500,00"
    # Приход виден в день поставки, долг — в день срока
    assert calc.week_cell_value(supplier.id, date(2026, 10, 19), "income", data.operations) == "1 000,00"
    # «За неделю» = сумма Долгов недели; «на сегодня» = Долги со сроком < сегодня
    assert calc.week_debt(date(2026, 10, 26), data, [supplier]) == Decimal("1500")
    assert calc.week_debt(date(2026, 11, 2), data, [supplier]) == Decimal("0")
    assert calc.overdue_amount(data, supplier, date(2026, 10, 27)) == Decimal("1000")  # срок 26.10 уже прошёл
    assert calc.overdue_amount(data, supplier, date(2026, 10, 28)) == Decimal("1500")
