"""Расчёты программы. ВСЕ формулы живут только здесь.

Состояние на 27.10.2026 (v0.3.1) — первые формулы дал пользователь
(окно «Поставка», макет docs/mockups/07-delivery-dialog.png):

РЕАЛИЗОВАНО (только таблица «Поставщики»):
* Срок оплаты поставки: дата поставки + отсрочка; при «Предоплате» или
  «Реализации» отсрочка НЕ применяется (срок = дата поставки).
  Если срок выпал на выходной по календарю «Данные» — сдвиг на ближайший
  следующий рабочий день. См. ``payment_due_date``.
* Ячейка «Долг» на дату срока: исходная сумма поставок с этим сроком.
  По требованию пользователя (v0.3.1) она ОСТАЁТСЯ для архива и не
  уменьшается и не исчезает при оплате.
* Колонка «за неделю» (задолженность): сумма «Долгов» со сроками внутри
  показанной недели.
* Колонка «на сегодня» (просрочено): сумма «Долгов» со сроками раньше
  сегодняшнего дня.
* ⚠️ «Оплата» ПОКА НЕ УМЕНЬШАЕТ долг нигде: как именно оплата будет
  влиять на долг/просрочку, пользователь разъяснит отдельным этапом
  («когда дойдём до оплат»). Не вводить распределение оплат самому.

ПО-ПРЕЖНЕМУ ЗАГЛУШКИ (нули): «Сводка» целиком —
``summary_totals``, ``supplier_debt_row``, ``overdue_payments``.
Не распространять на неё формулы без отдельного ТЗ.

Отображение ввода: «Приход»/«Реализация»/«Оплата» за день = суммы
операций соответствующего типа за день. Срок и долг по-прежнему
считаются только для поставщицкого «Прихода».
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .dates import fmt_money_plain, week_monday
from .models import (
    AppData,
    CASH_SOURCE,
    Counterparty,
    INCOMING,
    KIND_INCOME,
    KIND_PAYMENT,
    KIND_SALE,
    Operation,
    cash_receipt_amount,
    daily_operation_total,
    operation_amount,
    other_payment_amount,
    year_start_amount,
    year_start_customer_debt,
)

ZERO = Decimal("0")

_COLUMN_KIND_TO_OP_KIND = {
    "income": KIND_INCOME,
    "sale": KIND_SALE,
    "payment": KIND_PAYMENT,
}


# ---------------------------------------------------------------------------
# Сводка: пока заглушки
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SummaryTotals:
    """Шесть KPI-карточек экрана «Сводка»."""

    total_debt: Decimal = ZERO        # ВСЕГО ЗАДОЛЖЕННОСТЬ
    overdue: Decimal = ZERO           # ПРОСРОЧЕНО
    due_today: Decimal = ZERO         # СРОК СЕГОДНЯ
    due_next_7_days: Decimal = ZERO   # СРОК В БЛИЖАЙШИЕ 7 ДНЕЙ
    income_month: Decimal = ZERO      # ПРИХОД ЗА МЕСЯЦ
    paid_month: Decimal = ZERO        # ОПЛАЧЕНО ЗА МЕСЯЦ


@dataclass(frozen=True)
class SupplierDebtRow:
    """Строка таблицы «Задолженность по поставщикам» на «Сводке»."""

    debt: Decimal = ZERO              # Задолженность
    overdue: Decimal = ZERO           # Просрочено
    next_due_date: date | None = None # Ближайший срок (None -> «—»)
    next_due_amount: Decimal | None = None  # Сумма срока (None -> «—»)


def summary_totals(on_date: date) -> SummaryTotals:
    """TODO(формулы Сводки): посчитать 6 KPI на дату ``on_date``. Пока — нули."""
    return SummaryTotals()


def supplier_debt_row(supplier_id: str, on_date: date) -> SupplierDebtRow:
    """TODO(формулы Сводки): задолженность/просрочка/ближайший срок поставщика."""
    return SupplierDebtRow()


def overdue_payments(on_date: date) -> list:
    """TODO(формулы Сводки): список просроченных оплат для панели на «Сводке»."""
    return []


# ---------------------------------------------------------------------------
# Плашка «Сводка»: три блока построчно (v0.14.0)
#
# Формулы дал пользователь: блок «Средства» (остаток на счету, итого
# поступление, итого оплаты, итого остаток), блок «Задолженность
# поставщикам» (всего, на неделе, на сегодня, просрочено, оплата
# понедельно на три недели вперёд) и блок «Месяц» (приход и оплаты с
# начала месяца). Считаются простые суммы по сохранённым данным; долг
# берётся архивным графиком ``debt_by_due_date`` (оплата долг не
# уменьшает — требование v0.3.1).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FundsTotals:
    """Блок «Средства» плашки «Сводка» (на выбранную дату, v0.22.1)."""

    funds: Decimal = ZERO        # остаток на счету НА НАЧАЛО дня
    income: Decimal = ZERO       # поступление за день
    payments: Decimal = ZERO     # оплаты за день
    balance: Decimal = ZERO      # итого остаток = остаток + поступление − оплаты


@dataclass(frozen=True)
class FundsMovementRow:
    """Одна строка журнала движения средств (v0.22.1)."""

    day: date
    name: str = ""
    income: Decimal = ZERO        # поступление (пусто у оплат)
    payment: Decimal = ZERO       # оплата (пусто у поступлений)
    balance: Decimal = ZERO       # остаток после этой записи


@dataclass(frozen=True)
class FundsMovement:
    """Журнал движения средств за период (v0.22.1)."""

    start: date                   # первый день периода
    end: date                     # последний день периода (выбранная дата)
    start_funds: Decimal = ZERO   # остаток на счету НА НАЧАЛО периода
    rows: list[FundsMovementRow] = field(default_factory=list)
    income: Decimal = ZERO        # всего поступлений за период
    payments: Decimal = ZERO      # всего оплат за период
    balance: Decimal = ZERO       # итого остаток на конец периода


@dataclass(frozen=True)
class DebtTotals:
    """Блок «Задолженность поставщикам» плашки «Сводка»."""

    total: Decimal = ZERO        # всего задолженность по всем поставщикам
    week: Decimal = ZERO         # задолженность на текущей неделе
    today: Decimal = ZERO        # задолженность со сроком ровно на эту дату
    overdue: Decimal = ZERO      # задолженность на сегодня (столбец «на сегодня»)
    late: Decimal = ZERO         # просроченный платёж (столбец «ПРОСРОЧЕНО»)


@dataclass(frozen=True)
class WeekPlan:
    """Одна неделя блока «Оплата понедельно»."""

    monday: date
    sunday: date
    amount: Decimal = ZERO


@dataclass(frozen=True)
class CustomerDebtTotals:
    """Блок «Задолженность покупателей» плашки «Сводка» (v0.15.0).

    Считается как **Реализация − Оплата** по сохранённым операциям
    покупателя плюс долг переноса на 1 января (плашка «Данные»).
    """

    total: Decimal = ZERO        # всего задолженность покупателей
    week: Decimal = ZERO         # за текущую неделю


@dataclass(frozen=True)
class MonthTotals:
    """Блок «Месяц» плашки «Сводка»."""

    income: Decimal = ZERO       # приход с начала месяца
    payments: Decimal = ZERO     # оплаты с начала месяца


def _iso_day(value: str) -> date | None:
    """Дата из ISO-строки; битая или пустая — ``None``."""

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def debt_schedule(
    data: AppData,
    suppliers: Sequence[Counterparty] | None = None,
) -> dict[date, Decimal]:
    """График долга поставщикам: дата срока -> сумма.

    Складываются суммы поставок (``debt_by_due_date``) и разовые долги
    переноса на 1 января (плашка «Данные» вкладки «Конфигурация») — у
    них тоже есть дата оплаты. Оплаты долг не уменьшают (v0.3.1).
    """

    if suppliers is None:
        suppliers = data.suppliers
    schedule: dict[date, Decimal] = {}
    for supplier in suppliers:
        for due, amount in debt_by_due_date(data, supplier).items():
            schedule[due] = schedule.get(due, ZERO) + amount
    for entry in data.year_start.supplier_debts:
        due = _iso_day(entry.due_date)
        amount = year_start_amount(entry)
        if due is None or amount == 0:
            continue
        schedule[due] = schedule.get(due, ZERO) + amount
    return schedule


def supplier_schedule(
    data: AppData, counterparty: Counterparty
) -> dict[date, Decimal]:
    """Полный график долга одного поставщика по срокам.

    Как :func:`debt_schedule`, но для одного контрагента: суммы поставок
    по срокам **плюс его разовые долги переноса на 1 января**.
    """

    schedule = dict(debt_by_due_date(data, counterparty))
    for entry in data.year_start.supplier_debts:
        if entry.counterparty_id != counterparty.id:
            continue
        due = _iso_day(entry.due_date)
        amount = year_start_amount(entry)
        if due is None or amount == 0:
            continue
        schedule[due] = schedule.get(due, ZERO) + amount
    return schedule


def supplier_schedule_remaining(
    data: AppData, counterparty: Counterparty, upto: date
) -> dict[date, Decimal]:
    """Остаток полного графика долга после оплат (FIFO по срокам).

    То же, что :func:`debt_remaining_by_due_date`, но с учётом долгов
    переноса на 1 января — для «Сводки» (v0.15.0).
    """

    remaining = {
        due: amount
        for due, amount in supplier_schedule(data, counterparty).items()
        if amount != 0
    }
    paid = ZERO
    for op in data.operations:
        if op.counterparty_id != counterparty.id or op.kind != KIND_PAYMENT:
            continue
        day = _iso_day(op.date)
        if day is None or day > upto:
            continue
        paid += operation_amount(op)
    if paid <= 0:
        return remaining
    for due in sorted(remaining):
        if paid <= 0:
            break
        amount = remaining[due]
        if amount <= 0:
            continue
        covered = amount if amount <= paid else paid
        remaining[due] = amount - covered
        paid -= covered
    return remaining


def _schedule_sum(
    schedule: Mapping[date, Decimal], start: date, end: date
) -> Decimal:
    return sum(
        (amount for due, amount in schedule.items() if start <= due <= end),
        ZERO,
    )


def visible_suppliers(data: AppData, include_archived: bool) -> list[Counterparty]:
    return [s for s in data.suppliers if include_archived or not s.archived]


def _funds_records(
    data: AppData,
) -> Iterator[tuple[date | None, Decimal, bool, str]]:
    """Записи блока «Средства»: (дата, сумма, это поступление, название).

    v0.15.0: «Реализация» покупателей в ИТОГ ПОСТУПЛЕНИЕ не входит,
    поэтому из операций берём только ``KIND_PAYMENT``.
    """

    for receipt in data.cash_receipts:
        name = (
            "Поступление наличными"
            if receipt.source == CASH_SOURCE
            else "Поступление по картам"
        )
        yield _iso_day(receipt.date), cash_receipt_amount(receipt), True, name
    for payment in data.other_payments:
        yield (
            _iso_day(payment.date),
            other_payment_amount(payment),
            payment.direction == INCOMING,
            payment.name or "Прочий вид",
        )
    for op in data.operations:
        if op.kind == KIND_PAYMENT:
            partner = find_counterparty(data, op.counterparty_id)
            name = f"Оплата: {partner.name}" if partner is not None else "Оплата"
            yield _iso_day(op.date), operation_amount(op), False, name


def funds_totals(data: AppData, on_date: date) -> FundsTotals:
    """Блок «Средства» **на дату** ``on_date`` (v0.22.1).

    Правило пользователя: вчерашний «Итого остаток» становится
    сегодняшним «Остатком на счету», к нему за день прибавляются
    поступления и отнимаются оплаты — результат снова показывается в
    «Итого остаток». Поэтому:

    * ``funds`` — остаток на счету **на начало дня** ``on_date``:
      перенос с начала года плюс все поступления и минус все оплаты
      за дни до ``on_date``;
    * ``income`` / ``payments`` — поступления и оплаты **за этот день**;
    * ``balance`` — итого остаток на конец дня:
      ``funds + income − payments``.
    """

    try:
        funds = Decimal(data.year_start.funds or "0")
    except InvalidOperation:
        funds = ZERO
    # Дата, на которую внесён остаток: записи раньше неё уже учтены в нём.
    start = _iso_day(data.year_start.date)

    income_before = payments_before = ZERO
    income_day = payments_day = ZERO
    for day, amount, is_income, _name in _funds_records(data):
        if day is None:
            # Запись без даты (старые файлы) — считаем как прошедшую.
            slot = "before"
        elif start is not None and day < start:
            continue  # уже в остатке на начало года
        elif day > on_date:
            continue  # будущее в остаток не входит
        elif day == on_date:
            slot = "day"
        else:
            slot = "before"
        if is_income:
            if slot == "day":
                income_day += amount
            else:
                income_before += amount
        elif slot == "day":
            payments_day += amount
        else:
            payments_before += amount

    funds += income_before - payments_before
    return FundsTotals(
        funds=funds,
        income=income_day,
        payments=payments_day,
        balance=funds + income_day - payments_day,
    )


def funds_movement(data: AppData, on_date: date, days: int = 1) -> FundsMovement:
    """Журнал движения средств за последние ``days`` дней (v0.22.1).

    С v0.23.1 журнал показывается **за один день** (``days=1``): первая
    строка — остаток на начало дня, затем все поступления, затем все
    оплаты, последняя — итого остаток.

    Первый день периода — ``on_date - (days - 1)``, последний — сама
    выбранная дата. Остаток на начало периода берётся по тем же
    правилам, что и в блоке «Средства» (``funds_totals``), поэтому
    вчерашний «Итого остаток» становится сегодняшним «Остатком на
    счету» и внутри журнала всё сходится.
    """

    start = on_date - timedelta(days=days - 1)
    start_funds = funds_totals(data, start).funds

    records = [
        (day, name, amount, is_income)
        for day, amount, is_income, name in _funds_records(data)
        if day is not None and start <= day <= on_date
    ]
    # Внутри дня поступления показываем раньше оплат.
    records.sort(key=lambda row: (row[0], not row[3]))

    balance = start_funds
    income = payments = ZERO
    rows: list[FundsMovementRow] = []
    for day, name, amount, is_income in records:
        if is_income:
            income += amount
            balance += amount
            rows.append(
                FundsMovementRow(day, name, income=amount, balance=balance)
            )
        else:
            payments += amount
            balance -= amount
            rows.append(
                FundsMovementRow(day, name, payment=amount, balance=balance)
            )
    return FundsMovement(
        start=start,
        end=on_date,
        start_funds=start_funds,
        rows=rows,
        income=income,
        payments=payments,
        balance=balance,
    )


def supplier_overdue_total(
    data: AppData, on_date: date, include_archived: bool = False
) -> Decimal:
    """«Просрочено» = сумма столбца «на сегодня» таблицы поставщиков.

    v0.15.0: пользователь задал источник явно — «Просрочено» в «Сводке»
    берётся из столбца «на сегодня» таблицы поставщиков, то есть это
    оплаты, просроченные на эту дату (со сроком по сегодня
    включительно). Формула та же, что в ``WeekView._debt_columns`` для
    текущей недели.
    """

    monday = week_monday(on_date)
    sunday = monday + timedelta(days=6)
    limit = monday - timedelta(days=1) if on_date < monday else min(on_date, sunday)
    # Оплаты, сделанные до конца показанной недели, уже погасили часть
    # долга — «на сегодня» в «Сводке» и в таблице поставщиков совпадают.
    upto = sunday
    total = ZERO
    for supplier in visible_suppliers(data, include_archived):
        # У поставщика со статусом «Реализация» срок бессрочный: просрочки
        # у него не бывает (v0.15.0).
        if is_open_ended(supplier):
            continue
        due_map = supplier_schedule_remaining(data, supplier, upto)
        total += sum((amount for due, amount in due_map.items() if due <= limit), ZERO)
    return total


def supplier_late_total(
    data: AppData, on_date: date, include_archived: bool = False
) -> Decimal:
    """«Просроченный платёж» = сумма столбца «ПРОСРОЧЕНО» поставщиков.

    v0.16.0: считаются долги со сроком оплаты, который **уже истёк**
    (строго раньше даты), за вычетом оплат. У поставщика со статусом
    «Реализация» срок бессрочный — просрочки у него не бывает.
    """

    sunday = week_monday(on_date) + timedelta(days=6)
    limit = min(on_date, sunday)
    total = ZERO
    for supplier in visible_suppliers(data, include_archived):
        if is_open_ended(supplier):
            continue
        due_map = supplier_schedule_remaining(data, supplier, sunday)
        total += sum((amount for due, amount in due_map.items() if due < limit), ZERO)
    return total


def debt_totals(
    data: AppData, on_date: date, include_archived: bool = False
) -> DebtTotals:
    """Блок «Задолженность поставщикам» на дату ``on_date``."""

    schedule = debt_schedule(data, visible_suppliers(data, include_archived))
    monday = week_monday(on_date)
    sunday = monday + timedelta(days=6)
    # «За неделю» — тоже с вычетом оплат, как в таблице поставщиков.
    week = ZERO
    for supplier in visible_suppliers(data, include_archived):
        due_map = supplier_schedule_remaining(data, supplier, sunday)
        week += sum(
            (amount for due, amount in due_map.items() if monday <= due <= sunday),
            ZERO,
        )
    return DebtTotals(
        total=sum(schedule.values(), ZERO),
        week=week,
        today=schedule.get(on_date, ZERO),
        # «Задолженность на сегодня» считается по столбцу «на сегодня»
        # таблицы поставщиков (v0.15.0), а не по срокам раньше сегодня.
        overdue=supplier_overdue_total(data, on_date, include_archived),
        # v0.16.0: отдельная строка «Просроченный платёж» — столбец
        # «ПРОСРОЧЕНО» (срок уже истёк).
        late=supplier_late_total(data, on_date, include_archived),
    )


def weekly_payment_plan(
    data: AppData,
    on_date: date,
    weeks: int = 4,
    include_archived: bool = False,
) -> list[WeekPlan]:
    """«Оплата понедельно»: ``weeks`` недель, следующих за текущей."""

    schedule = debt_schedule(data, visible_suppliers(data, include_archived))
    current_monday = week_monday(on_date)
    result: list[WeekPlan] = []
    for index in range(1, weeks + 1):
        monday = current_monday + timedelta(days=7 * index)
        sunday = monday + timedelta(days=6)
        result.append(
            WeekPlan(
                monday=monday,
                sunday=sunday,
                amount=_schedule_sum(schedule, monday, sunday),
            )
        )
    return result


def _customer_debt_components(
    data: AppData, customer: Counterparty
) -> tuple[Decimal, Decimal]:
    """(реализация, оплата) покупателя: суммы по всем его операциям."""

    sale = ZERO
    payment = ZERO
    for op in data.operations:
        if op.counterparty_id != customer.id:
            continue
        if op.kind == KIND_SALE:
            sale += operation_amount(op)
        elif op.kind == KIND_PAYMENT:
            payment += operation_amount(op)
    return sale, payment


def customer_debt_amount(
    data: AppData,
    customer: Counterparty,
    start: date | None = None,
    end: date | None = None,
) -> Decimal:
    """Задолженность покупателя: Реализация − Оплата (+ перенос 1 января).

    Без ``start``/``end`` берутся все операции; с границами — только
    операции внутри периода. Перенос на 1 января (плашка «Данные»)
    входит в общую сумму и не входит в «за неделю».
    """

    sale = ZERO
    payment = ZERO
    for op in data.operations:
        if op.counterparty_id != customer.id:
            continue
        if start is not None or end is not None:
            day = _iso_day(op.date)
            if day is None:
                continue
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
        if op.kind == KIND_SALE:
            sale += operation_amount(op)
        elif op.kind == KIND_PAYMENT:
            payment += operation_amount(op)
    total = sale - payment
    if start is None and end is None:
        entry = year_start_customer_debt(data, customer.id)
        if entry is not None:
            total += year_start_amount(entry)
    return total


def customer_debt_totals(
    data: AppData, on_date: date, include_archived: bool = False
) -> CustomerDebtTotals:
    """Блок «Задолженность покупателей» на дату ``on_date``."""

    customers = [
        c for c in data.customers if include_archived or not c.archived
    ]
    monday = week_monday(on_date)
    sunday = monday + timedelta(days=6)
    total = sum(
        (customer_debt_amount(data, customer) for customer in customers), ZERO
    )
    week = sum(
        (
            customer_debt_amount(data, customer, monday, sunday)
            for customer in customers
        ),
        ZERO,
    )
    return CustomerDebtTotals(total=total, week=week)


def month_totals(data: AppData, on_date: date) -> MonthTotals:
    """Блок «Месяц»: приход и оплаты с 1-го числа месяца по ``on_date``."""

    start = on_date.replace(day=1)
    income = ZERO
    payments = ZERO
    for op in data.operations:
        day = _iso_day(op.date)
        if day is None or not (start <= day <= on_date):
            continue
        if op.kind == KIND_INCOME:
            income += operation_amount(op)
        elif op.kind == KIND_PAYMENT:
            payments += operation_amount(op)
    for payment in data.other_payments:
        day = _iso_day(payment.date)
        if day is None or not (start <= day <= on_date):
            continue
        if payment.direction != INCOMING:
            payments += other_payment_amount(payment)
    return MonthTotals(income=income, payments=payments)


# ---------------------------------------------------------------------------
# Срок оплаты поставки (формула пользователя, v0.3.0)
# ---------------------------------------------------------------------------

def is_day_off(day: date, day_overrides: Mapping[str, str]) -> bool:
    """Выходной ли день по календарю вкладки «Данные» (по умолчанию Сб/Вс)."""
    override = day_overrides.get(day.isoformat())
    if override == "off":
        return True
    if override == "work":
        return False
    return day.weekday() >= 5


def next_working_day(day: date, day_overrides: Mapping[str, str]) -> date:
    """Ближайший рабочий день, начиная с ``day`` (сдвиг только вперёд)."""
    result = day
    while is_day_off(result, day_overrides):
        result += timedelta(days=1)
    return result


def is_open_ended(counterparty: Counterparty) -> bool:
    """Бессрочный срок оплаты: у поставщика со статусом «Реализация».

    v0.15.0: пользователь задал — у такого поставщика срок оплаты не
    наступает, поэтому его суммы **никогда не попадают в столбец «на
    сегодня»** (просрочки у него не бывает). Сама задолженность
    по-прежнему видна в «за неделю» и в общем долге.
    """

    return bool(getattr(counterparty, "realization", False))


def raw_due_date(delivery_date: date, counterparty: Counterparty) -> date:
    """Срок оплаты БЕЗ переноса с выходного.

    Предоплата/Реализация — отсрочка не применяется; иначе дата + отсрочка
    (отсрочка 0 — срок равен дате поставки).
    """
    if counterparty.prepayment or counterparty.realization:
        return delivery_date
    return delivery_date + timedelta(days=max(counterparty.deferment_days, 0))


def payment_due_date(
    delivery_date: date,
    counterparty: Counterparty,
    day_overrides: Mapping[str, str],
) -> date:
    """Итоговый срок оплаты: raw_due_date + перенос с выходного вперёд."""
    return next_working_day(raw_due_date(delivery_date, counterparty), day_overrides)


def operation_due_date(
    op: Operation, counterparty: Counterparty, day_overrides: Mapping[str, str]
) -> date | None:
    """Срок оплаты поставки: сохранённый при вводе или пересчёт на лету
    (для операций, введённых до v0.3.0, у которых due_date пуст).

    У «Реализации» покупателя и у «Оплаты» срока оплаты нет.
    """
    if op.kind != KIND_INCOME:
        return None
    if op.due_date:
        try:
            return date.fromisoformat(op.due_date)
        except ValueError:
            pass
    try:
        delivery = date.fromisoformat(op.date)
    except ValueError:
        return None
    return payment_due_date(delivery, counterparty, day_overrides)


def find_counterparty(data: AppData, counterparty_id: str) -> Counterparty | None:
    for counterparty in (*data.suppliers, *data.customers):
        if counterparty.id == counterparty_id:
            return counterparty
    return None


def debt_by_due_date(
    data: AppData, counterparty: Counterparty
) -> dict[date, Decimal]:
    """Суммы поставок по датам срока оплаты («Долг») — архивный график.

    ⚠️ Оплаты здесь сознательно НЕ вычитаются (требование пользователя
    v0.3.1: сумма в «Долге» остаётся для архива и не исчезает). Как
    «Оплата» будет влиять на долг — отдельный этап по разъяснениям
    пользователя; до тех пор это исходные суммы поставок по срокам.
    """
    result: dict[date, Decimal] = {}
    for op in data.operations:
        if op.counterparty_id != counterparty.id or op.kind != KIND_INCOME:
            continue
        due = operation_due_date(op, counterparty, data.day_overrides)
        if due is None:
            continue
        amount = operation_amount(op)
        if amount != 0:
            result[due] = result.get(due, ZERO) + amount
    return result


def debt_remaining_by_due_date(
    data: AppData, counterparty: Counterparty, upto: date
) -> dict[date, Decimal]:
    """Остаток долга по срокам после оплат, сделанных не позже ``upto``.

    v0.15.0: пользователь задал порядок списания. Оплата гасит долги
    **в порядке сроков — от ранних к поздним**: сначала просроченная
    задолженность (столбец «на сегодня»), а уже остаток оплаты — долги
    показанной недели (столбец «за неделю»). Недоплаченный остаток
    остаётся на своём сроке, поэтому при переходе на следующую неделю
    он сам попадает в «на сегодня».

    Архивный «Долг» в ячейках дней при этом не меняется (v0.3.1) —
    для него по-прежнему есть :func:`debt_by_due_date`.
    """

    remaining = {
        due: amount
        for due, amount in debt_by_due_date(data, counterparty).items()
        if amount != 0
    }
    paid = ZERO
    for op in data.operations:
        if op.counterparty_id != counterparty.id or op.kind != KIND_PAYMENT:
            continue
        day = _iso_day(op.date)
        if day is None or day > upto:
            continue
        paid += operation_amount(op)
    if paid <= 0:
        return remaining
    for due in sorted(remaining):
        if paid <= 0:
            break
        amount = remaining[due]
        if amount <= 0:
            continue
        covered = amount if amount <= paid else paid
        remaining[due] = amount - covered
        paid -= covered
    return remaining


def week_debt(
    week_monday: date,
    data: AppData | None = None,
    counterparties: Iterable[Counterparty] = (),
) -> Decimal:
    """«Задолженность за неделю»: сумма «Долгов» со сроками
    внутри недели (Пн–Вс) по заданным поставщикам. Без ``data`` — 0."""
    if data is None:
        return ZERO
    week_sunday = week_monday + timedelta(days=6)
    total = ZERO
    for counterparty in counterparties:
        for due, amount in debt_by_due_date(data, counterparty).items():
            if week_monday <= due <= week_sunday:
                total += amount
    return total


def overdue_amount(
    data: AppData, counterparty: Counterparty, today: date
) -> Decimal:
    """«Просрочено на сегодня»: «Долги» со сроком раньше ``today`` (оплата пока не вычитается)."""
    total = ZERO
    for due, amount in debt_by_due_date(data, counterparty).items():
        if due < today:
            total += amount
    return total


# ---------------------------------------------------------------------------
# Ячейки недельной таблицы
# ---------------------------------------------------------------------------

def week_cell_amount(
    counterparty_id: str,
    day: date,
    column_kind: str,
    operations: Sequence[Operation] = (),
    data: AppData | None = None,
) -> Decimal:
    """Числовое значение ячейки Приход/Реализация/Долг/Оплата.

    ``income``/``sale``/``payment`` — суммы операций за день. ``debt`` — исходная сумма
    поставок со сроком в этот день (архив, оплата не уменьшает) (нужен ``data``: поставщик,
    его условия и календарь выходных).
    """
    if column_kind == "debt":
        if data is None:
            return ZERO
        counterparty = find_counterparty(data, counterparty_id)
        if counterparty is None:
            return ZERO
        return debt_by_due_date(data, counterparty).get(day, ZERO)

    op_kind = _COLUMN_KIND_TO_OP_KIND.get(column_kind)
    if op_kind is None:
        return ZERO
    return daily_operation_total(
        list(operations), counterparty_id, day.isoformat(), op_kind
    )


def week_cell_value(
    counterparty_id: str,
    day: date,
    column_kind: str,
    operations: Sequence[Operation] = (),
    data: AppData | None = None,
) -> str:
    """Текст ячейки недельной таблицы (``week_cell_amount`` в формате суммы)."""
    amount = week_cell_amount(counterparty_id, day, column_kind, operations, data)
    return fmt_money_plain(amount) if amount != 0 else ""


def week_column_total(
    counterparty_ids: Iterable[str],
    day: date,
    column_kind: str,
    operations: Sequence[Operation] = (),
    data: AppData | None = None,
) -> str:
    """Строка «ВСЕГО» по колонке дня (Приход/Долг/Оплата)."""
    total = ZERO
    for counterparty_id in counterparty_ids:
        total += week_cell_amount(
            counterparty_id, day, column_kind, operations, data
        )
    return fmt_money_plain(total) if total != 0 else ""
