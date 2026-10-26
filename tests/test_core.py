"""Тесты ядра без Qt: даты, модели, хранилище, заглушки расчётов."""

from datetime import date
from decimal import Decimal

from oplata import calc
from oplata.dates import fmt_date, fmt_money, month_title, week_days, week_monday
from oplata.models import AppData, Counterparty, seed_data
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


def test_calc_is_stub_zeros():
    """Этап оболочки: формулы НЕ реализованы, calc обязан давать нули.

    Когда пользователь даст формулы, этот тест заменяется тестами формул.
    """
    today = date(2026, 10, 26)
    totals = calc.summary_totals(today)
    assert totals.total_debt == totals.overdue == Decimal("0")
    row = calc.supplier_debt_row("any", today)
    assert row.debt == Decimal("0") and row.next_due_date is None
    assert calc.overdue_payments(today) == []
    assert calc.week_debt(today) == Decimal("0")
    assert calc.week_cell_value("any", today, "income") == ""
