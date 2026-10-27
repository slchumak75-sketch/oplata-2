"""Расчёты программы. ВСЕ формулы живут только здесь.

Состояние на 27.10.2026 (v0.3.0) — первые формулы дал пользователь
(окно «Поставка», макет docs/mockups/07-delivery-dialog.png):

РЕАЛИЗОВАНО (только таблица «Поставщики»):
* Срок оплаты поставки: дата поставки + отсрочка; при «Предоплате» или
  «Реализации» отсрочка НЕ применяется (срок = дата поставки).
  Если срок выпал на выходной по календарю «Данные» — сдвиг на ближайший
  следующий рабочий день. См. ``payment_due_date``.
* Ячейка «Долг» на дату срока: неоплаченный остаток поставок с этим сроком.
* Колонка «за неделю» (задолженность): сумма неоплаченных остатков со
  сроками внутри показанной недели.
* Колонка «на сегодня» (просрочено): сумма неоплаченных остатков со
  сроками раньше сегодняшнего дня.
* Оплаты распределяются на поставки по FIFO (сначала самый ранний срок) —
  ПРЕДПОЛОЖЕНИЕ, пользователь распределение ещё не описывал (SPEC Q1).

ПО-ПРЕЖНЕМУ ЗАГЛУШКИ (нули): «Сводка» целиком —
``summary_totals``, ``supplier_debt_row``, ``overdue_payments``.
Не распространять на неё формулы без отдельного ТЗ.

Отображение ввода: «Приход»/«Оплата» за день = суммы операций за день.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .dates import fmt_money_plain
from .models import (
    KIND_INCOME,
    KIND_PAYMENT,
    AppData,
    Counterparty,
    Operation,
    daily_operation_total,
    operation_amount,
)

ZERO = Decimal("0")

_COLUMN_KIND_TO_OP_KIND = {"income": KIND_INCOME, "payment": KIND_PAYMENT}


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
    (для операций, введённых до v0.3.0, у которых due_date пуст)."""
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


def unpaid_by_due_date(
    data: AppData, counterparty: Counterparty
) -> dict[date, Decimal]:
    """Неоплаченные остатки поставок по датам срока оплаты.

    Все «Оплаты» контрагента суммируются в общий пул и гасят поставки
    в порядке сроков (FIFO) — предположение этапа v0.3.0.
    """
    deliveries: list[tuple[date, date, Decimal]] = []  # (срок, дата поставки, сумма)
    payment_pool = ZERO
    for op in data.operations:
        if op.counterparty_id != counterparty.id:
            continue
        if op.kind == KIND_INCOME:
            due = operation_due_date(op, counterparty, data.day_overrides)
            if due is None:
                continue
            try:
                delivery_day = date.fromisoformat(op.date)
            except ValueError:
                continue
            deliveries.append((due, delivery_day, operation_amount(op)))
        elif op.kind == KIND_PAYMENT:
            payment_pool += operation_amount(op)

    result: dict[date, Decimal] = {}
    for due, _delivery_day, amount in sorted(deliveries):
        paid = min(amount, payment_pool)
        payment_pool -= paid
        remaining = amount - paid
        if remaining > 0:
            result[due] = result.get(due, ZERO) + remaining
    return result


def week_debt(
    week_monday: date,
    data: AppData | None = None,
    counterparties: Iterable[Counterparty] = (),
) -> Decimal:
    """«Задолженность за неделю»: сумма неоплаченных остатков со сроками
    внутри недели (Пн–Вс) по заданным поставщикам. Без ``data`` — 0."""
    if data is None:
        return ZERO
    week_sunday = week_monday + timedelta(days=6)
    total = ZERO
    for counterparty in counterparties:
        for due, amount in unpaid_by_due_date(data, counterparty).items():
            if week_monday <= due <= week_sunday:
                total += amount
    return total


def overdue_amount(
    data: AppData, counterparty: Counterparty, today: date
) -> Decimal:
    """«Просрочено на сегодня»: неоплаченные остатки со сроком раньше ``today``."""
    total = ZERO
    for due, amount in unpaid_by_due_date(data, counterparty).items():
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
    """Числовое значение ячейки Приход/Долг/Оплата.

    ``income``/``payment`` — суммы операций за день. ``debt`` — неоплаченный
    остаток поставок со сроком в этот день (нужен ``data``: поставщик,
    его условия и календарь выходных).
    """
    if column_kind == "debt":
        if data is None:
            return ZERO
        counterparty = find_counterparty(data, counterparty_id)
        if counterparty is None:
            return ZERO
        return unpaid_by_due_date(data, counterparty).get(day, ZERO)

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
