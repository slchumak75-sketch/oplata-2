"""⚠️ ЗАГЛУШКИ РАСЧЁТОВ — ФОРМУЛ ЗДЕСЬ ПОКА НЕТ (этап «только оболочка»).

Пользователь прямо сказал: «формулы будем писать позже, пока делаем
только оболочку». Поэтому каждая функция ниже возвращает нули/пустоты,
как на макетах (``0,00 руб.``, ``—``).

ПРАВИЛО ДЛЯ СЛЕДУЮЩЕГО АГЕНТА:
вся будущая расчётная логика пишется ТОЛЬКО в этом модуле (экраны уже
вызывают эти функции), и только после того, как пользователь даст
формулы. Список открытых вопросов к формулам — ``docs/ROADMAP.md`` §Этап 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

ZERO = Decimal("0")


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


def week_cell_value(counterparty_id: str, day: date, column_kind: str) -> str:
    """TODO(формулы): значение ячейки Приход/Долг/Оплата недельной таблицы.

    ``column_kind``: ``"income"`` | ``"debt"`` | ``"payment"``.
    Пока всегда пустая строка — как пустые ячейки на макетах.
    """
    return ""
