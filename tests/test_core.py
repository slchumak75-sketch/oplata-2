"""Тесты ядра без Qt: даты, модели, хранилище, заглушки расчётов."""

from datetime import date
from decimal import Decimal

import pytest

from oplata import calc
from oplata.dates import fmt_date, fmt_money, month_title, parse_money, week_days, week_monday
from oplata.models import (
    CARD_SOURCE,
    CASH_SOURCE,
    KIND_INCOME,
    KIND_PAYMENT,
    KKM_KIND_REPORT,
    KKM_KIND_RETURN,
    AppData,
    CashReceipt,
    Counterparty,
    Operation,
    add_cash_receipt,
    add_customer_receipt,
    add_delivery,
    add_realization,
    add_supplier_payment,
    cash_receipt_amount,
    daily_operation_total,
    seed_data,
    set_daily_operation,
    set_kkm_amount,
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


def test_old_customer_income_migrates_to_sale_without_due_date():
    customer = Counterparty(name="Покупатель")
    supplier = Counterparty(name="Поставщик")
    data = AppData(
        suppliers=[supplier],
        customers=[customer],
        operations=[
            Operation(
                kind=KIND_INCOME,
                counterparty_id=customer.id,
                date="2026-10-26",
                amount="100.00",
                due_date="2026-10-28",
            ),
            Operation(
                kind=KIND_INCOME,
                counterparty_id=supplier.id,
                date="2026-10-26",
                amount="200.00",
                due_date="2026-10-28",
            ),
        ],
    )

    restored = AppData.from_dict(data.to_dict())
    customer_op, supplier_op = restored.operations
    assert (customer_op.kind, customer_op.due_date) == ("Реализация", "")
    assert (supplier_op.kind, supplier_op.due_date) == (
        KIND_INCOME,
        "2026-10-28",
    )


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
    # Долг: у «ааа» отсрочка 0, срок = день поставки. Здесь считается
    # архивный график (без оплат) — таблица «Поставщики» показывает
    # не его, а остаток после оплат (v0.27.2).
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

    # Пример пользователя (отсрочка 10 дней, срок выпал на выходной):
    # приход 28.10 + 10 дней = Сб 07.11 -> перенос на Пн 09.11.
    ten_days = Counterparty(name="W", deferment_days=10)
    assert calc.raw_due_date(date(2026, 10, 28), ten_days) == date(2026, 11, 7)
    assert date(2026, 11, 7).weekday() == 5  # суббота
    assert calc.payment_due_date(date(2026, 10, 28), ten_days, {}) == date(
        2026, 11, 9
    )
    # Приход 27.10 с той же отсрочкой: срок Пт 06.11 — рабочий, переноса нет.
    assert calc.payment_due_date(date(2026, 10, 27), ten_days, {}) == date(
        2026, 11, 6
    )

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


def test_cash_receipts_round_trip_and_old_files(tmp_path):
    """Наличные/карты: новое поле данных переживает перезапись файла.

    Поле ``cash_receipts`` добавлено в v0.9.0; в файлах более старых
    версий его нет — такие данные должны читаться с пустым списком.
    """
    path = tmp_path / "data.json"
    store = DataStore(path)
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-27", Decimal("1500.75"))
    add_cash_receipt(store.data, CARD_SOURCE, "2026-10-27", Decimal("2000"))
    store.save()

    reloaded = DataStore(path)
    assert [(r.source, r.date, r.amount) for r in reloaded.data.cash_receipts] == [
        ("cash", "2026-10-27", "1500.75"),
        ("card", "2026-10-27", "2000.00"),
    ]
    assert sum(cash_receipt_amount(r) for r in reloaded.data.cash_receipts) == (
        Decimal("3500.75")
    )

    # Старый файл без нового поля читается без ошибок.
    old_file = tmp_path / "old.json"
    old_file.write_text(
        '{"schema": 1, "suppliers": [], "customers": [], "operations": [],'
        ' "day_overrides": {}}',
        encoding="utf-8",
    )
    assert DataStore(old_file).data.cash_receipts == []

    # Битый source не проходит дальше модели.
    broken = CashReceipt.from_dict({"source": "кэш", "amount": "10"})
    assert broken.source == CASH_SOURCE


def test_receipts_helpers_write_expected_operations():
    """Поступление покупателя и оплата поставщику — операции «Оплата»."""
    customer = Counterparty(name="Покупатель")
    supplier = Counterparty(name="Поставщик")
    data = AppData(customers=[customer], suppliers=[supplier])

    add_customer_receipt(data, customer.id, "2026-10-27", Decimal("3000"))
    add_supplier_payment(data, supplier.id, "2026-10-27", Decimal("4500.5"))

    assert [(op.kind, op.date, op.amount, op.due_date) for op in data.operations] == [
        ("Оплата", "2026-10-27", "3000.00", ""),
        ("Оплата", "2026-10-27", "4500.50", ""),
    ]
    assert daily_operation_total(
        data.operations, customer.id, "2026-10-27", KIND_PAYMENT
    ) == Decimal("3000")
    assert daily_operation_total(
        data.operations, supplier.id, "2026-10-27", KIND_PAYMENT
    ) == Decimal("4500.50")
    # Срока оплаты у оплат нет — календарь их не сдвигает.
    assert calc.operation_due_date(data.operations[0], customer, {}) is None
    assert calc.operation_due_date(data.operations[1], supplier, {}) is None


def test_payments_summary_sums_months_quarters_and_year():
    """v0.31.0: «Сводка оплат» — по видам, месяцам, кварталам и году.

    Считаются только исходящие оплаты: операции вида «Оплата» (строка
    «Поставщики») и прочие виды оплат блока «Оплата». Приход и
    прочие поступления в таблицу не попадают, чужой год — тоже.
    """

    from oplata.models import INCOMING, OUTGOING, OtherPayment

    data = AppData()
    supplier = Counterparty(name="Поставщик")
    data.suppliers = [supplier]
    data.operations = [
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=supplier.id,
            date="2026-01-15",
            amount="1000.00",
        ),
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=supplier.id,
            date="2026-02-05",
            amount="500.50",
        ),
        # Приход (поставка) в оплаты не входит.
        Operation(
            kind=KIND_INCOME,
            counterparty_id=supplier.id,
            date="2026-03-01",
            amount="9000.00",
        ),
        # Оплата прошлого года.
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=supplier.id,
            date="2025-12-31",
            amount="777.00",
        ),
    ]
    data.other_payments = [
        OtherPayment(
            date="2026-01-20", name="Аренда", amount="3000.00", direction=OUTGOING
        ),
        OtherPayment(
            date="2026-03-20", name="Аренда", amount="3000.00", direction=OUTGOING
        ),
        OtherPayment(
            date="2026-05-20", name="Зарплата", amount="1500.00", direction=OUTGOING
        ),
        # Прочее поступление — тоже не оплата.
        OtherPayment(
            date="2026-05-21", name="Возврат", amount="900.00", direction=INCOMING
        ),
    ]

    summary = calc.payments_summary(data, 2026)
    assert [row.name for row in summary.rows] == [
        "Поставщики",
        "Аренда",
        "Зарплата",
    ]

    suppliers_row = summary.rows[0]
    assert suppliers_row.months[0] == Decimal("1000.00")   # январь
    assert suppliers_row.months[1] == Decimal("500.50")    # февраль
    assert suppliers_row.months[2] == Decimal("0")         # март
    assert suppliers_row.quarters == (
        Decimal("1500.50"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    assert suppliers_row.total == Decimal("1500.50")

    rent_row = summary.rows[1]
    assert rent_row.months[0] == Decimal("3000")
    assert rent_row.months[2] == Decimal("3000")
    assert rent_row.total == Decimal("6000")

    # Итог: январь 4 000, февраль 500,50, март 3 000, май 1 500.
    assert summary.total.months[:5] == (
        Decimal("4000.00"),
        Decimal("500.50"),
        Decimal("3000.00"),
        Decimal("0"),
        Decimal("1500.00"),
    )
    # II квартал — только майская зарплата (апрель и июнь пустые).
    assert summary.total.quarters[0] == Decimal("7500.50")
    assert summary.total.quarters[1] == Decimal("1500.00")
    assert summary.total.quarters[2] == Decimal("0")
    assert summary.total.total == Decimal("9000.50")

    # Чужой год считается отдельно; вид оплаты виден и без оплат года.
    assert calc.payments_summary(data, 2025).total.total == Decimal("777.00")
    future = calc.payments_summary(data, 2030)
    assert [row.name for row in future.rows] == [
        "Поставщики",
        "Аренда",
        "Зарплата",
    ]
    assert future.total.total == Decimal("0")


def test_incoming_summary_counts_cash_customers_and_other_income():
    """v0.33.0: «Сводка поступлений» — ровно четыре строки блока.

    Оплата покупателя попадает только в поступления, оплата поставщику
    — только в оплаты: одна и та же сумма не должна учитываться дважды.

    v0.33.0: посторонняя запись направления INCOMING (например,
    «Новое поступление», оставшееся от старого вызова «+») в сводку
    не попадает: в блоке «Поступление» своих видов нет.
    """

    from oplata.models import (
        CARD_SOURCE,
        CASH_SOURCE,
        INCOMING,
        OUTGOING,
        CashReceipt,
        OtherPayment,
    )

    data = AppData()
    supplier = Counterparty(name="Поставщик")
    customer = Counterparty(name="Покупатель")
    data.suppliers = [supplier]
    data.customers = [customer]
    data.operations = [
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=supplier.id,
            date="2026-01-15",
            amount="1000.00",
        ),
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=customer.id,
            date="2026-02-05",
            amount="700.00",
        ),
    ]
    data.cash_receipts = [
        CashReceipt(
            date="2026-01-10", source=CASH_SOURCE, amount="5000.00", comment=""
        ),
        CashReceipt(
            date="2026-03-10", source=CARD_SOURCE, amount="2500.00", comment=""
        ),
    ]
    data.other_payments = [
        OtherPayment(
            date="2026-02-20", name="Аренда", amount="3000.00", direction=OUTGOING
        ),
        OtherPayment(
            date="2026-04-20", name="Возврат", amount="900.00", direction=INCOMING
        ),
        OtherPayment(
            date="2026-04-21",
            name="Прочие поступления",
            amount="150.00",
            direction=INCOMING,
        ),
    ]

    payments = calc.payments_summary(data, 2026)
    assert [row.name for row in payments.rows] == ["Поставщики", "Аренда"]
    # Оплата покупателя (700,00 в феврале) в оплаты не попала.
    assert payments.rows[0].months[1] == Decimal("0")
    assert payments.total.total == Decimal("4000.00")

    incoming = calc.incoming_summary(data, 2026)
    # v0.33.0: ровно четыре строки — как в блоке «Поступление».
    assert [row.name for row in incoming.rows] == [
        "Поступление наличными",
        "Поступление по картам",
        "Покупатели",
        "Прочие поступления",
    ]
    assert incoming.rows[0].months[0] == Decimal("5000")   # наличные, январь
    assert incoming.rows[1].months[2] == Decimal("2500")   # карты, март
    assert incoming.rows[2].months[1] == Decimal("700")    # покупатель, февраль
    assert incoming.rows[3].months[3] == Decimal("150")    # прочие поступления

    # v0.33.0: 900 р. записи «Возврат» (апрель) в сводку не попали.
    # I квартал: 5 000 + 700 + 2 500 = 8 200; за год — 8 350.
    assert incoming.total.quarters[0] == Decimal("8200.00")
    assert incoming.total.total == Decimal("8350.00")
    # Оплата поставщику в поступления не попала: январь — только касса.
    assert incoming.total.months[0] == Decimal("5000.00")

    # v0.33.0: посторонние записи INCOMING не добавляют строк.
    data.other_payments.append(
        OtherPayment(
            date="2026-05-01",
            name="Новое поступление",
            amount="777.00",
            direction=INCOMING,
        )
    )
    extra = calc.incoming_summary(data, 2026)
    assert [row.name for row in extra.rows] == [
        "Поступление наличными",
        "Поступление по картам",
        "Покупатели",
        "Прочие поступления",
    ]
    assert extra.total.total == Decimal("8350.00")

    # Виды видны и в году без данных: таблица не «прыгает».
    future = calc.incoming_summary(data, 2030)
    assert len(future.rows) == 4
    assert future.total.total == Decimal("0")


def test_other_payment_vat_rate_defaults_and_roundtrip():
    """v0.33.0: ставка НДС вида оплаты — пустая по умолчанию и в файле.

    У старых записей поля нет: ``from_dict`` подставляет пустую строку
    (вид оплаты без НДС), а новое значение доезжает до JSON и назад.
    """

    from oplata.models import OtherPayment, add_other_payment

    old = OtherPayment.from_dict(
        {"date": "2026-05-01", "name": "Аренда", "amount": "100.00"}
    )
    assert old.vat_rate == ""

    data = AppData()
    payment = add_other_payment(data, "Аренда", "2026-05-01", vat_rate="20")
    assert payment.vat_rate == "20"

    restored = AppData.from_dict(data.to_dict())
    assert restored.other_payments[0].vat_rate == "20"

    # Без ставки вид оплаты остаётся без НДС — ячейка пустая.
    plain = add_other_payment(data, "Услуги", "2026-05-02")
    assert plain.vat_rate == ""


def test_fmt_vat_rate_shows_percent_only_for_set_rates():
    """v0.33.0: ставка показывается как «20 %», пустая — пустая ячейка."""

    from oplata.dates import fmt_vat_rate

    assert fmt_vat_rate("") == ""
    assert fmt_vat_rate("   ") == ""
    assert fmt_vat_rate("20") == "20 %"
    assert fmt_vat_rate("20.00") == "20 %"
    assert fmt_vat_rate("10,5") == "10.5 %"
    assert fmt_vat_rate("0") == "0 %"


def test_net_of_vat_divides_by_rate_and_keeps_zero_rate():
    """v0.34.0: сумма без НДС — 240 при 20 % даёт 200, ставка 0 — без изменений."""

    from oplata.calc import net_of_vat

    assert net_of_vat(Decimal("240"), "20") == Decimal("200.00")
    assert net_of_vat(Decimal("240"), "20.0") == Decimal("200.00")
    assert net_of_vat(Decimal("120"), "20,5") == Decimal("99.59")
    # Ставка 0 и пустая (вид без НДС) — сумма не меняется.
    assert net_of_vat(Decimal("240"), "0") == Decimal("240")
    assert net_of_vat(Decimal("240"), "") == Decimal("240")
    assert net_of_vat(Decimal("240"), "  ") == Decimal("240")
    # Ноль показывать незачем: пустая ячейка таблицы.
    assert net_of_vat(Decimal("0"), "20") == Decimal("0.00")


def test_row_without_vat_recalculates_months_quarters_and_total():
    """v0.34.0: строка сводки без НДС — по месяцам, кварталам и итогу."""

    from oplata.calc import PaymentsSummaryRow, row_without_vat

    row = PaymentsSummaryRow(
        name="Аренда",
        months=(
            Decimal("240"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("360"),
            Decimal("0"),
        ),
        quarters=(Decimal("240"), Decimal("0"), Decimal("0"), Decimal("360")),
        total=Decimal("600"),
        vat_rate="20",
    )
    net = row_without_vat(row)
    assert net.months[0] == Decimal("200.00")
    assert net.months[10] == Decimal("300.00")
    assert net.quarters[0] == Decimal("200.00")
    assert net.total == Decimal("500.00")

    # Вид без НДС: строка осталась прежней (тот же объект).
    plain = row_without_vat(
        PaymentsSummaryRow(
            name="Услуги",
            months=(Decimal("100"),) + (Decimal("0"),) * 11,
            quarters=(Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0")),
            total=Decimal("100"),
            vat_rate="",
        )
    )
    assert plain.total == Decimal("100")


def test_payments_summary_rows_keep_vat_rate_of_the_kind():
    """v0.34.0: строка сводки знает ставку НДС своего вида."""

    data = AppData()
    supplier = Counterparty(name="Поставщик")
    data.suppliers = [supplier]
    data.operations = [
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=supplier.id,
            date="2026-01-15",
            amount="1200.00",
        )
    ]
    from oplata.models import OUTGOING, OtherPayment

    data.other_payments = [
        OtherPayment(
            date="2026-01-20",
            name="Аренда",
            amount="240.00",
            direction=OUTGOING,
            vat_rate="20",
        ),
        OtherPayment(
            date="2026-02-20",
            name="Услуги",
            amount="100.00",
            direction=OUTGOING,
            vat_rate="0",
        ),
    ]

    summary = calc.payments_summary(data, 2026)
    rates = {row.name: row.vat_rate for row in summary.rows}
    assert rates == {"Поставщики": "", "Аренда": "20", "Услуги": "0"}
    # Ставка не влияет на обычный расчёт: в сводке суммы как ввели.
    assert summary.rows[1].total == Decimal("240.00")
    assert summary.total.total == Decimal("1540.00")


def test_customer_payment_is_income_in_funds_only():
    """v0.35.0: деньги покупателя — поступление (плюс), поставщику — оплата.

    Правило пользователя: сумма, внесённая в блоке «Поступление» по
    строке «Покупатели», идёт **плюсом** — в «Поступление за день» и
    в остаток. В блок «Месяц» деньги покупателя не попадают: с v0.43.0
    там итоги реестра «Отчеты ККМ», а не приход и оплаты.
    """

    data = AppData()
    supplier = Counterparty(name="Поставщик")
    customer = Counterparty(name="Покупатель")
    data.suppliers = [supplier]
    data.customers = [customer]
    add_supplier_payment(data, supplier.id, "2026-10-27", Decimal("1000"))
    add_customer_receipt(data, customer.id, "2026-10-27", Decimal("1500"))

    funds = calc.funds_totals(data, date(2026, 10, 27))
    assert funds.income == Decimal("1500")      # покупатель — плюсом
    assert funds.payments == Decimal("1000")    # поставщик — минусом
    assert funds.balance == Decimal("500")

    # Оплата покупателя уменьшает его задолженность (Реализация − Оплата).
    assert calc.customer_debt_amount(data, customer) == Decimal("-1500")

    # v0.43.0: в блоке «Месяц» — итоги ККМ; записей нет, поэтому нули.
    month = calc.month_totals(data, date(2026, 10, 27))
    assert month.kkm_reports == Decimal("0")
    assert month.kkm_returns == Decimal("0")
    assert month.kkm_total == Decimal("0")

    # Оплата без контрагента (старые данные) остаётся оплатой.
    data.operations.append(
        Operation(kind=KIND_PAYMENT, counterparty_id="", date="2026-10-27", amount="100")
    )
    extra = calc.funds_totals(data, date(2026, 10, 27))
    assert extra.income == Decimal("1500")
    assert extra.payments == Decimal("1100")


def test_month_totals_count_kkm_reports_for_whole_month():
    """v0.43.0: блок «Месяц» — итоги реестра ККМ за весь месяц.

    Считаются **все** дни календарного месяца выбранной даты — с 1-го
    по последнее число, а не «с начала месяца по выбранный день»:
    запись 31.10 видна и тогда, когда в «Сводке» выбрано 5.10.
    """

    data = AppData()
    # Отчеты ККМ: 1 000 первым числом и 500 — последним (31.10).
    set_kkm_amount(data, "2026-10-01", 0, KKM_KIND_REPORT, Decimal("1000"))
    set_kkm_amount(data, "2026-10-31", 0, KKM_KIND_REPORT, Decimal("500"))
    # Возврат покупателя — отдельная строка дня (МИР: слот 2).
    set_kkm_amount(data, "2026-10-31", 2, KKM_KIND_RETURN, Decimal("120"))
    # Соседний месяц не должен попасть в итог.
    set_kkm_amount(data, "2026-11-01", 0, KKM_KIND_REPORT, Decimal("7000"))
    set_kkm_amount(data, "2026-09-30", 0, KKM_KIND_REPORT, Decimal("8000"))

    for day in (1, 5, 27, 31):
        month = calc.month_totals(data, date(2026, 10, day))
        assert month.kkm_reports == Decimal("1500")   # 1 000 + 500
        assert month.kkm_returns == Decimal("120")
        assert month.kkm_total == Decimal("1380")     # отчеты − возвраты

    # Ноябрь — только своя запись.
    november = calc.month_totals(data, date(2026, 11, 15))
    assert november.kkm_reports == Decimal("7000")
    assert november.kkm_returns == Decimal("0")
    assert november.kkm_total == Decimal("7000")

    # Деньги операций в блок «Месяц» больше не попадают (v0.43.0).
    supplier = Counterparty(name="Поставщик")
    data.suppliers = [supplier]
    add_delivery(data, supplier.id, "2026-10-27", "2026-11-03", Decimal("3000"))
    add_supplier_payment(data, supplier.id, "2026-10-27", Decimal("1000"))
    month = calc.month_totals(data, date(2026, 10, 27))
    assert month.kkm_reports == Decimal("1500")   # поставка не прибавилась
    assert month.kkm_returns == Decimal("120")
    # Поставка и оплата по-прежнему видны в блоке «Средства» (v0.35.0).
    funds = calc.funds_totals(data, date(2026, 10, 27))
    assert funds.payments == Decimal("1000")


def test_month_realization_invoiced_counts_only_green_marked_sales():
    """v0.43.0: «Реализация Покупателям» — только выписанная реализация.

    В строку идут операции «Реализация» с зелёной меткой
    (``invoiced=True``) за весь календарный месяц: реализация «в
    работе», соседний месяц, приход поставщика и оплата покупателя в
    сумму не входят.
    """

    data = AppData()
    customer = Counterparty(name="Покупатель")
    supplier = Counterparty(name="Поставщик")
    data.customers = [customer]
    data.suppliers = [supplier]

    def sale(day: str, amount: str, invoiced: bool) -> None:
        op = add_realization(data, customer.id, day, Decimal(amount))
        op.invoiced = invoiced

    sale("2026-10-01", "4000", True)     # выписано, первое число
    sale("2026-10-31", "600", True)      # выписано, последнее число
    sale("2026-10-15", "900", False)     # в работе — не считается
    sale("2026-11-05", "777", True)      # другой месяц
    # Приход поставщика и оплата покупателя в реализацию не входят.
    add_delivery(data, supplier.id, "2026-10-10", "2026-10-20", Decimal("3000"))
    add_customer_receipt(data, customer.id, "2026-10-20", Decimal("1500"))

    assert calc.month_realization_invoiced(data, 2026, 10) == Decimal("4600")
    assert calc.month_realization_invoiced(data, 2026, 11) == Decimal("777")
    assert calc.month_realization_invoiced(data, 2026, 12) == Decimal("0")

    # Строка есть и в блоке «Месяц» целиком.
    totals = calc.month_totals(data, date(2026, 10, 20))
    assert totals.sale_invoiced == Decimal("4600")


def test_funds_movement_puts_customer_payment_into_income_column():
    """v0.35.0: в журнале платёж покупателя стоит в графе «Поступление»."""

    data = AppData()
    supplier = Counterparty(name="Поставщик")
    customer = Counterparty(name="Покупатель")
    data.suppliers = [supplier]
    data.customers = [customer]
    add_supplier_payment(data, supplier.id, "2026-10-27", Decimal("1000"))
    add_customer_receipt(data, customer.id, "2026-10-27", Decimal("1500"))

    movement = calc.funds_movement(data, date(2026, 10, 27))
    rows = [(row.name, row.income, row.payment, row.balance) for row in movement.rows]
    assert rows == [
        # Внутри дня поступления идут раньше оплат.
        ("Поступление: Покупатель", Decimal("1500"), Decimal("0"), Decimal("1500")),
        ("Оплата: Поставщик", Decimal("0"), Decimal("1000"), Decimal("500")),
    ]
    assert movement.income == Decimal("1500")
    assert movement.payments == Decimal("1000")
