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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .dates import fmt_money_plain, week_monday
from .models import (
    INCOMING,
    KIND_INCOME,
    KIND_PAYMENT,
    KIND_SALE,
    AppData,
    Counterparty,
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
    """Блок «Средства» плашки «Сводка»."""

    funds: Decimal = ZERO        # остаток на счету (перенос на 1 января)
    income: Decimal = ZERO       # итого поступление
    payments: Decimal = ZERO     # итого оплаты
    balance: Decimal = ZERO      # итого остаток = остаток + поступление − оплаты


@dataclass(frozen=True)
class DebtTotals:
    """Блок «Задолженность поставщикам» плашки «Сводка»."""

    total: Decimal = ZERO        # всего задолженность по всем поставщикам
    week: Decimal = ZERO         # задолженность на текущей неделе
    today: Decimal = ZERO        # задолженность на сегодня
    overdue: Decimal = ZERO      # просрочено (срок раньше сегодня)


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


def _schedule_sum(
    schedule: Mapping[date, Decimal], start: date, end: date
) -> Decimal:
    return sum(
        (amount for due, amount in schedule.items() if start <= due <= end),
        ZERO,
    )


def visible_suppliers(data: AppData, include_archived: bool) -> list[Counterparty]:
    return [s for s in data.suppliers if include_archived or not s.archived]


def funds_totals(data: AppData) -> FundsTotals:
    """Блок «Средства»: остаток, поступления и оплаты за всё время."""

    funds = ZERO
    try:
        funds = Decimal(data.year_start.funds or "0")
    except InvalidOperation:
        funds = ZERO

    # v0.15.0: по просьбе пользователя «Реализация» покупателей в
    # ИТОГ ПОСТУПЛЕНИЕ не входит. Больше в расчёте ничего не менялось.
    income = ZERO
    for receipt in data.cash_receipts:
        income += cash_receipt_amount(receipt)
    for payment in data.other_payments:
        if payment.direction == INCOMING:
            income += other_payment_amount(payment)

    payments = ZERO
    for payment in data.other_payments:
        if payment.direction != INCOMING:
            payments += other_payment_amount(payment)
    for op in data.operations:
        if op.kind == KIND_PAYMENT:
            payments += operation_amount(op)

    return FundsTotals(
        funds=funds,
        income=income,
        payments=payments,
        balance=funds + income - payments,
    )


def debt_totals(
    data: AppData, on_date: date, include_archived: bool = False
) -> DebtTotals:
    """Блок «Задолженность поставщикам» на дату ``on_date``."""

    schedule = debt_schedule(data, visible_suppliers(data, include_archived))
    monday = week_monday(on_date)
    return DebtTotals(
        total=sum(schedule.values(), ZERO),
        week=_schedule_sum(schedule, monday, monday + timedelta(days=6)),
        today=schedule.get(on_date, ZERO),
        overdue=sum(
            (amount for due, amount in schedule.items() if due < on_date), ZERO
        ),
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
