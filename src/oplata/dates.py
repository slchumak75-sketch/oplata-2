"""Работа с датами и форматирование отображения (без Qt, без формул).

Здесь только календарная арифметика (понедельник недели и т.п.)
и форматирование строк. Денежные РАСЧЁТЫ здесь не живут — они будут
в ``calc.py`` на следующем этапе.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MONTHS_RU = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)

# Пн..Вс — как в заголовках недельной таблицы на макетах (строчные).
WEEKDAYS_RU_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
# Пн..Вс — как в мини-календарях экрана «Данные».
WEEKDAYS_RU_TITLE = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def week_monday(day: date) -> date:
    """Понедельник недели, к которой принадлежит ``day`` (неделя = Пн–Вс)."""
    return day - timedelta(days=day.weekday())


def week_days(monday: date) -> list[date]:
    """7 дат недели начиная с понедельника ``monday``."""
    return [monday + timedelta(days=i) for i in range(7)]


def fmt_date(day: date) -> str:
    """Дата в формате макетов: ``26.10.2026``."""
    return day.strftime("%d.%m.%Y")


def month_title(day: date) -> str:
    """Например: ``Октябрь 2026`` (заголовок группы дней в недельной таблице)."""
    return f"{MONTHS_RU[day.month - 1]} {day.year}"


def fmt_money(value: Decimal) -> str:
    """Отображение суммы как на макетах: ``0,00 руб.`` / ``1 234,50 руб.``.

    Это только форматирование, не расчёт.
    """
    quantized = Decimal(value).quantize(Decimal("0.01"))
    sign = "-" if quantized < 0 else ""
    quantized = abs(quantized)
    whole = int(quantized)
    cents = int((quantized - whole) * 100)
    whole_str = f"{whole:,}".replace(",", " ")
    return f"{sign}{whole_str},{cents:02d} руб."


def fmt_money_plain(value: Decimal) -> str:
    """Сумма без ``руб.`` — как в ячейках таблицы «Сводка» (``0,00``)."""
    return fmt_money(value).removesuffix(" руб.")


def parse_money(text: str) -> Decimal | None:
    """Разбор суммы, введённой пользователем в ячейку Приход/Оплата.

    Принимает ``1234``, ``1234,5``, ``1 234,50``, ``1234.50``, ``1 234,50 руб.``
    Пустая строка -> ``None`` (ячейку очистили). Нечисло -> ``ValueError``.
    Результат округляется до копеек. Знак не запрещается здесь —
    неотрицательность проверяет вызывающий UI.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    cleaned = cleaned.removesuffix("руб.").removesuffix("руб").strip()
    cleaned = cleaned.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Не сумма: {text!r}") from exc
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
