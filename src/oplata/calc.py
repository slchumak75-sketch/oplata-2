"""Расчёты и ЗАГЛУШКИ расчётов.

Состояние на 27.10.2026 (v0.2.0):
* ФОРМУЛ ДОЛГА ЗДЕСЬ ПОКА НЕТ — ``summary_totals``, ``supplier_debt_row``,
  ``overdue_payments``, ``week_debt`` и колонка «Долг» возвращают нули/пусто,
  как на макетах. Пользователь: «формулы будем писать позже».
* Единственное не-заглушечное — отображение ВВОДА пользователя: ячейки
  «Приход»/«Оплата» недельной таблицы показывают суммы введённых операций
  за день (``week_cell_value``/``week_column_total``). Это не формула долга,
  а просто сумма того, что человек напечатал.

ПРАВИЛО ДЛЯ СЛЕДУЮЩЕГО АГЕНТА:
вся будущая расчётная логика пишется ТОЛЬКО в этом модуле (экраны уже
вызывают эти функции), и только после того, как пользователь даст
формулы. Список открытых вопросов к формулам — ``docs/ROADMAP.md`` §Этап 1.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .dates import fmt_money_plain
from .models import KIND_INCOME, KIND_PAYMENT, Operation, daily_operation_total

ZERO = Decimal("0")

_COLUMN_KIND_TO_OP_KIND = {"income": KIND_INCOME, "payment": KIND_PAYMENT}


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
    """TODO(формулы): посчитать 6 KPI на дату ``on_date``. Пока — нули."""
    return SummaryTotals()


def supplier_debt_row(supplier_id: str, on_date: date) -> SupplierDebtRow:
    """TODO(формулы): задолженность/просрочка/ближайший срок поставщика."""
    return SupplierDebtRow()


def overdue_payments(on_date: date) -> list:
    """TODO(формулы): список просроченных оплат для панели на «Сводке»."""
    return []


def week_debt(week_monday: date) -> Decimal:
    """TODO(формулы): «Задолженность за неделю» в тулбаре «Поставщики»."""
    return ZERO


def week_cell_value(
    counterparty_id: str,
    day: date,
    column_kind: str,
    operations: Sequence[Operation] = (),
) -> str:
    """Значение ячейки Приход/Долг/Оплата недельной таблицы.

    ``"income"``/``"payment"`` — сумма введённых пользователем операций
    за день (пусто, если ввода не было). ``"debt"`` — ⚠️ ЗАГЛУШКА:
    формула долга появится на этапе формул, до тех пор всегда пусто.
    """
    op_kind = _COLUMN_KIND_TO_OP_KIND.get(column_kind)
    if op_kind is None:  # "debt" и всё неизвестное
        return ""
    total = daily_operation_total(
        list(operations), counterparty_id, day.isoformat(), op_kind
    )
    return fmt_money_plain(total) if total != 0 else ""


def week_column_total(
    counterparty_ids: Iterable[str],
    day: date,
    column_kind: str,
    operations: Sequence[Operation] = (),
) -> str:
    """Строка «ВСЕГО» для Приход/Оплата: сумма ввода по всем контрагентам за день.

    Для ``"debt"`` — заглушка (пусто), как и сама колонка «Долг».
    """
    op_kind = _COLUMN_KIND_TO_OP_KIND.get(column_kind)
    if op_kind is None:
        return ""
    ops = list(operations)
    total = ZERO
    for counterparty_id in counterparty_ids:
        total += daily_operation_total(ops, counterparty_id, day.isoformat(), op_kind)
    return fmt_money_plain(total) if total != 0 else ""
