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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .dates import fmt_money_plain, week_monday
from .models import (
    KKM_KIND_RETURN,
    AppData,
    CASH_SOURCE,
    Counterparty,
    INCOMING,
    KIND_INCOME,
    KIND_PAYMENT,
    KIND_SALE,
    OUTGOING,
    Operation,
    cash_receipt_amount,
    daily_operation_total,
    operation_amount,
    other_payment_amount,
    other_payment_names,
    year_start_amount,
    year_start_customer_debt,
)

ZERO = Decimal("0")
# Подпись строки итогов в обеих сводках (v0.34.0).
TOTAL_ROW_TITLE = "Всего"

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
    """Блок «Месяц» плашки «Сводка» (v0.43.0).

    С v0.43.0 в блоке — **итоги реестра ККМ** за весь календарный
    месяц (с 1-го по последнее число), а не приход и оплаты: строки
    «Приход с начала месяца» и «Оплаты с начала месяца» убраны по
    просьбе пользователя.
    """

    kkm_reports: Decimal = ZERO  # «Итого по Отчетам ККМ»
    kkm_returns: Decimal = ZERO  # «Итого Возврат покупателя»
    kkm_total: Decimal = ZERO    # «Итого за месяц» = отчеты − возвраты
    sale_invoiced: Decimal = ZERO  # «Реализация Покупателям» (выписанная)


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

    Складываются суммы поставок (``debt_by_due_date``). Оплаты долг не
    уменьшают (v0.3.1). Разовые долги переноса на 1 января больше не
    учитываются: плашка «Данные» удалена в v0.26.0.
    """

    if suppliers is None:
        suppliers = data.suppliers
    schedule: dict[date, Decimal] = {}
    for supplier in suppliers:
        for due, amount in debt_by_due_date(data, supplier).items():
            schedule[due] = schedule.get(due, ZERO) + amount
    return schedule


def supplier_schedule(
    data: AppData, counterparty: Counterparty
) -> dict[date, Decimal]:
    """Полный график долга одного поставщика по срокам.

    Как :func:`debt_schedule`, но для одного контрагента: суммы поставок
    по срокам (перенос на 1 января не учитывается, v0.26.0).
    """

    return dict(debt_by_due_date(data, counterparty))


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
    v0.35.0: операция «Оплата» **по покупателю** — это поступление
    (деньги пришли), по поставщику — оплата (деньги ушли).
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
    # v0.35.0: деньги, пришедшие от покупателя, — это поступление, а не
    # оплата (правило пользователя): сумма идёт плюсом в «Поступление
    # за день», в остаток и в «Приход с начала месяца». Оплата
    # поставщику (и оплата без контрагента) остаётся оплатой.
    customer_ids = {item.id for item in data.customers}
    for op in data.operations:
        if op.kind == KIND_PAYMENT:
            is_income = op.counterparty_id in customer_ids
            partner = find_counterparty(data, op.counterparty_id)
            title = "Поступление" if is_income else "Оплата"
            name = f"{title}: {partner.name}" if partner is not None else title
            yield _iso_day(op.date), operation_amount(op), is_income, name


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

    # v0.26.0: плашка «Данные» (перенос остатков) удалена, поэтому
    # остаток на счёте считается только по операциям — иначе из-за даты
    # остатков в «Сводку» не попадали данные блока «Поступление».
    funds = ZERO

    income_before = payments_before = ZERO
    income_day = payments_day = ZERO
    for day, amount, is_income, _name in _funds_records(data):
        if day is None:
            # Запись без даты (старые файлы) — считаем как прошедшую.
            slot = "before"
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


def supplier_debt_remaining(
    suppliers: Sequence[Counterparty],
    data: AppData,
    upto: date,
) -> dict[date, Decimal]:
    """Остаток долга после оплат — суммарно по поставщикам (v0.26.5).

    То же, что столбец «Итого» в таблице поставщиков: по каждому
    поставщику берётся :func:`supplier_schedule_remaining` (оплаты
    гасят долги в порядке сроков — от ранних к поздним) и складывается.
    """

    schedule: dict[date, Decimal] = {}
    for supplier in suppliers:
        for due, amount in supplier_schedule_remaining(
            data, supplier, upto
        ).items():
            schedule[due] = schedule.get(due, ZERO) + amount
    return schedule


def supplier_debt_total_on_week(
    data: AppData,
    on_date: date,
    include_archived: bool = False,
    upto: date | None = None,
) -> Decimal:
    """«Всего задолженность» — как строка «Всего», столбец «Итого»
    таблицы поставщиков (v0.26.5).

    Сумма остатков долга после оплат, сделанных не позже ``upto``
    (по умолчанию — конец недели, на которую приходится ``on_date``,
    как в таблице поставщиков).
    """

    limit = (
        upto
        if upto is not None
        else week_monday(on_date) + timedelta(days=6)
    )
    schedule = supplier_debt_remaining(
        visible_suppliers(data, include_archived), data, limit
    )
    return sum(schedule.values(), ZERO)


def weekly_payment_plan(
    data: AppData,
    on_date: date,
    weeks: int = 4,
    include_archived: bool = False,
) -> list[WeekPlan]:
    """«Оплата понедельно»: ``weeks`` недель, следующих за текущей.

    v0.26.5: считается **остаток долга после оплат**, как столбец
    «Итого» в таблице поставщиков, а не полный график поставок. Поэтому
    оплата, сделанная раньше срока отсрочки, уменьшает сумму будущей
    недели: долги гасятся в порядке сроков — от ранних к поздним, и
    оплаченный долг в плане не показывается.
    """

    suppliers = visible_suppliers(data, include_archived)
    current_monday = week_monday(on_date)
    # Горизонт плана: все оплаты вплоть до конца последней недели
    # гасят долги по срокам, даже если оплата сделана раньше срока.
    horizon = current_monday + timedelta(days=7 * weeks + 6)
    schedule = supplier_debt_remaining(suppliers, data, horizon)
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
    # v0.26.0: перенос остатков из плашки «Данные» больше не учитывается
    # (вкладка удалена) — считаем только операции.
    return sale - payment


def customer_debt_split(
    data: AppData,
    customer: Counterparty,
    start: date | None = None,
    end: date | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """Задолженность покупателя в разбивке: ``(Выписано, В работе, Всего)``.

    v0.29.0, правило пользователя:

    * **«Выписано»** — реализации, у которых в узкой колонке стоит
      зелёная метка (``invoiced``), **минус произведенные оплаты**;
    * **«В работе»** — реализации с красной меткой (оплаты не
      вычитаются);
    * **«Всего»** = «Выписано» + «В работе».

    Из «Выписано» вычитаются оплаты за тот же период, что и реализация
    (без границ — все): так «Всего» остаётся прежней задолженностью
    «Реализация − Оплата».
    """

    invoiced_sale = ZERO
    work_sale = ZERO
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
            if op.invoiced:
                invoiced_sale += operation_amount(op)
            else:
                work_sale += operation_amount(op)
        elif op.kind == KIND_PAYMENT:
            payment += operation_amount(op)
    invoiced_total = invoiced_sale - payment
    return invoiced_total, work_sale, invoiced_total + work_sale


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


def month_realization_invoiced(data: AppData, year: int, month: int) -> Decimal:
    """Строка «Реализация Покупателям»: выписанная реализация за месяц.

    v0.43.0, правило пользователя: в блоке «Месяц» показывается сумма
    **всей реализации покупателям за месяц с зелёной меткой**
    («Выписано»). Считаются операции «Реализация» с ``invoiced=True``
    за весь календарный месяц — с 1-го по последнее число.

    Реализация без метки («в работе») сюда **не** входит, оплаты **не**
    вычитаются: строка показывает оборот по выписанным документам, а не
    долг покупателей (он по-прежнему в блоке «Задолженность
    покупателей»).
    """

    prefix = f"{year:04d}-{month:02d}-"
    customer_ids = {item.id for item in data.customers}
    total = ZERO
    for op in data.operations:
        if op.kind != KIND_SALE or not op.invoiced:
            continue
        # Считаем только покупателей: у поставщиков «Реализации» нет.
        if op.counterparty_id not in customer_ids:
            continue
        if not (op.date or "").startswith(prefix):
            continue
        total += operation_amount(op)
    return total


def month_totals(data: AppData, on_date: date) -> MonthTotals:
    """Блок «Месяц» плашки «Сводка»: итоги ККМ за месяц ``on_date``.

    **v0.43.0:** в блоке больше нет «Приход с начала месяца» и «Оплаты с
    начала месяца» (убраны по просьбе пользователя). Теперь это итоги
    реестра «Отчеты ККМ» за **весь календарный месяц** — с 1-го по
    последнее число, независимо от выбранного дня:
    ``Итого по Отчетам ККМ``, ``Итого Возврат покупателя`` и
    ``Итого за месяц`` (отчеты минус возвраты).

    Считает :func:`kkm_month_totals` по записям активной базы: у МИР и
    КОРЕЛИЧИ свои цифры.
    """

    reports, returns, total = kkm_month_totals(data, on_date.year, on_date.month)
    return MonthTotals(
        kkm_reports=reports,
        kkm_returns=returns,
        kkm_total=total,
        sale_invoiced=month_realization_invoiced(
            data, on_date.year, on_date.month
        ),
    )


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


def kkm_month_totals(
    data: AppData, year: int, month: int
) -> tuple[Decimal, Decimal, Decimal]:
    """Итог реестра ККМ за месяц: (отчёты ККМ, возвраты, разница).

    v0.42.0: считаем по записям этого месяца — «Отчет ККМ» отдельно от
    «Возврат покупателя», разница = отчёты минус возвраты. К денежным
    расчётам программы реестр не относится.
    """

    prefix = f"{year:04d}-{month:02d}-"
    reports = ZERO
    returns = ZERO
    for row in data.kkm_reports:
        if not row.date.startswith(prefix):
            continue
        try:
            amount = Decimal(row.amount or "0")
        except InvalidOperation:
            continue
        if row.kind == KKM_KIND_RETURN:
            returns += amount
        else:
            reports += amount
    return reports, returns, reports - returns


def find_counterparty(data: AppData, counterparty_id: str) -> Counterparty | None:
    for counterparty in (*data.suppliers, *data.customers):
        if counterparty.id == counterparty_id:
            return counterparty
    return None


def debt_by_due_date(
    data: AppData, counterparty: Counterparty
) -> dict[date, Decimal]:
    """Суммы поставок по датам срока оплаты («Долг») — архивный график.

    Оплаты здесь НЕ вычитаются — это исходный график поставок по
    срокам (архив). Им таблица «Поставщики» считает «Долг» в ячейках
    дней (v0.41.0; в v0.27.2–v0.40.0 там был остаток после оплат):
    итоговые столбцы считаются :func:`debt_remaining_by_due_date`.
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

    v0.41.0: этой функцией считаются только итоговые столбцы таблицы
    «Поставщики» — «Итого», «Просрочено» и «Неделя». Ячейки дней
    «Долг» снова архивные (:func:`debt_by_due_date`): оплата их не
    уменьшает (в v0.27.2 было наоборот).
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

    ``income``/``sale``/``payment`` — суммы операций за день. ``debt`` —
    исходная сумма поставок со сроком в этот день (архив, оплата не
    уменьшает): с v0.41.0 таблица «Поставщики» берёт для «Долга» ровно
    её, поэтому здесь — архивный график.
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


# ---------------------------------------------------------------------------
# Сводки оплат и поступлений: помесячно, поквартально и за год (v0.31.0,
# поступления — v0.32.0)
# ---------------------------------------------------------------------------

# Строка «Поставщики» — оплаты поставщикам. v0.32.0: операции вида
# «Оплата» делятся по контрагенту — оплата покупателю (деньги пришли
# от покупателя) уходит в «Сводку поступлений», строка «Покупатели»,
# чтобы одна и та же сумма не попадала в обе сводки.
SUPPLIERS_PAYMENTS_TITLE = "Поставщики"
# Строки «Сводки поступлений» — те же подписи, что в блоке
# «Поступление» (views/receipts_view.py): названия не должны разъезжаться.
CASH_INCOME_TITLE = "Поступление наличными"
CARD_INCOME_TITLE = "Поступление по картам"
CUSTOMERS_INCOME_TITLE = "Покупатели"
OTHER_INCOME_TITLE = "Прочие поступления"
MONTH_TITLES = (
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)
QUARTER_TITLES = ("I кв.", "II кв.", "III кв.", "IV кв.")
QUARTER_MONTHS = ((1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12))


@dataclass(frozen=True)
class PaymentsSummaryRow:
    """Строка «Сводки оплат»: вид оплаты и суммы по месяцам года."""

    name: str
    months: tuple[Decimal, ...]  # 12 месяцев, январь — первый
    quarters: tuple[Decimal, ...]  # 4 квартала
    total: Decimal
    # v0.34.0: ставка НДС вида — в процентах, пусто = без НДС.
    # У строки «Поставщики» и у строки «Всего» ставки нет.
    vat_rate: str = ""


@dataclass(frozen=True)
class PaymentsSummary:
    """Оплаты одного года по видам (v0.31.0)."""

    year: int
    rows: tuple[PaymentsSummaryRow, ...]
    total: PaymentsSummaryRow


def payments_row(
    name: str,
    months: Sequence[Decimal],
    vat_rate: str = "",
) -> PaymentsSummaryRow:
    """Собрать строку: по месяцам, по кварталам и всего за год."""

    quarters = tuple(
        sum((months[index - 1] for index in quarter), ZERO)
        for quarter in QUARTER_MONTHS
    )
    return PaymentsSummaryRow(
        name=name,
        months=tuple(months),
        quarters=quarters,
        total=sum(months, ZERO),
        vat_rate=vat_rate,
    )


def _vat_rate_number(vat_rate: str) -> Decimal | None:
    """Ставка НДС числом: «20» -> 20; пусто и не число -> ``None``."""

    text = str(vat_rate or "").strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def net_of_vat(amount: Decimal, vat_rate: str) -> Decimal:
    """Сумма без НДС: 240 при ставке 20 % -> 200 (v0.34.0).

    Ставка 0 или пустая — сумма не меняется (вид оплаты без НДС).
    Результат округляется до копеек, как и любой ввод денег.
    """

    rate = _vat_rate_number(vat_rate)
    if rate is None or rate == 0:
        return amount
    return (amount / (1 + rate / 100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def row_without_vat(row: PaymentsSummaryRow) -> PaymentsSummaryRow:
    """Строка сводки с суммами за минусом НДС (v0.34.0).

    Месяцы, кварталы и итог делятся на (1 + ставка/100). У вида без
    НДС (ставка пустая или 0) строка остаётся прежней.
    """

    if not _vat_rate_number(row.vat_rate):
        return row
    months = [net_of_vat(amount, row.vat_rate) for amount in row.months]
    return payments_row(row.name, months, row.vat_rate)


def _year_buckets() -> tuple[dict[str, list[Decimal]], list[str]]:
    """Заготовка таблицы: 12 нулей на вид и порядок строк."""

    return {}, []


def _add_to_bucket(
    amounts: dict[str, list[Decimal]],
    names: list[str],
    name: str,
    month: int,
    amount: Decimal,
) -> None:
    row = amounts.get(name)
    if row is None:
        row = [ZERO] * 12
        amounts[name] = row
        names.append(name)
    row[month - 1] += amount


def _summary_from_buckets(
    year: int,
    amounts: dict[str, list[Decimal]],
    names: list[str],
    vat_rates: dict[str, str] | None = None,
) -> PaymentsSummary:
    """Собрать сводку: строки — в порядке ``names``, последняя — «Всего»."""

    rates = vat_rates or {}
    rows = tuple(
        payments_row(name, amounts.get(name, [ZERO] * 12), rates.get(name, ""))
        for name in names
    )
    totals = [ZERO] * 12
    for row in rows:
        for index, amount in enumerate(row.months):
            totals[index] += amount
    return PaymentsSummary(
        year=year, rows=rows, total=payments_row(TOTAL_ROW_TITLE, totals)
    )


def outgoing_vat_rates(data: AppData) -> dict[str, str]:
    """Ставка НДС по видам оплат: название -> ставка (v0.34.0).

    Ставка лежит в первой записи вида — это и есть его карточка
    (как и отмеченные даты, v0.24.0). У «Поставщики» ставки нет.
    """

    rates: dict[str, str] = {}
    for payment in data.other_payments:
        if getattr(payment, "direction", OUTGOING) == INCOMING:
            continue
        name = payment.name or "Прочий вид"
        if name not in rates:
            rates[name] = str(getattr(payment, "vat_rate", "") or "").strip()
    return rates


def payments_summary(data: AppData, year: int) -> PaymentsSummary:
    """Оплаты года ``year`` по видам: месяцы, кварталы, итог (v0.31.0).

    Считаем **исходящие** оплаты активной базы — ровно то, что попадает
    в блок «Оплата»:

    * операции вида «Оплата» **по поставщикам** — строка «Поставщики»
      (v0.32.0: оплата покупателя — это поступление, в оплаты она не
      идёт, иначе одна сумма попадала бы в обе сводки);
    * прочие виды оплат (``direction`` = ``OUTGOING``) — по строке на
      каждый вид, в порядке появления (как в блоке «Оплата»); у
      строки есть ставка НДС вида (v0.34.0).

    Вид показывается, даже если в этом году оплат по нему не было:
    таблица не должна «прыгать» при смене года.
    """

    amounts, names = _year_buckets()
    customer_ids = {item.id for item in data.customers}

    for op in data.operations:
        if op.kind != KIND_PAYMENT:
            continue
        if op.counterparty_id in customer_ids:
            continue
        day = _iso_day(op.date)
        if day is None or day.year != year:
            continue
        _add_to_bucket(
            amounts, names, SUPPLIERS_PAYMENTS_TITLE, day.month, operation_amount(op)
        )

    for payment in data.other_payments:
        if getattr(payment, "direction", OUTGOING) == INCOMING:
            continue
        day = _iso_day(payment.date)
        if day is None or day.year != year:
            continue
        _add_to_bucket(
            amounts,
            names,
            payment.name or "Прочий вид",
            day.month,
            other_payment_amount(payment),
        )

    # Порядок строк: «Поставщики», затем виды в порядке настройки
    # блока «Оплата». Вид виден и без оплат года — таблица не «прыгает».
    order = [SUPPLIERS_PAYMENTS_TITLE]
    for name in other_payment_names(data):
        if name not in order:
            order.append(name)
    for name in names:  # виды, которых нет в настройке (данные важнее)
        if name not in order:
            order.append(name)
    # v0.34.0: ставка НДС вида — из его карточки.
    return _summary_from_buckets(year, amounts, order, outgoing_vat_rates(data))


def incoming_summary(data: AppData, year: int) -> PaymentsSummary:
    """Поступления года ``year`` по видам (v0.32.0).

    Зеркало ``payments_summary`` и блока «Поступление»:

    * касса — «Поступление наличными» и «Поступление по картам»;
    * операции вида «Оплата» **по покупателям** — строка «Покупатели»
      (деньги, пришедшие от покупателя);
    * прочие поступления (``direction`` = ``INCOMING``) — только
      постоянная строка «Прочие поступления» (v0.33.0): в блоке
      «Поступление» своих видов нет, поэтому и в сводке других строк
      быть не должно.
    """

    amounts, names = _year_buckets()
    supplier_ids = {item.id for item in data.suppliers}

    for receipt in data.cash_receipts:
        day = _iso_day(receipt.date)
        if day is None or day.year != year:
            continue
        title = (
            CASH_INCOME_TITLE if receipt.source == CASH_SOURCE else CARD_INCOME_TITLE
        )
        _add_to_bucket(
            amounts, names, title, day.month, cash_receipt_amount(receipt)
        )

    for op in data.operations:
        if op.kind != KIND_PAYMENT:
            continue
        # Оплата поставщику — деньги ушли, это не поступление.
        if op.counterparty_id in supplier_ids or not op.counterparty_id:
            continue
        day = _iso_day(op.date)
        if day is None or day.year != year:
            continue
        _add_to_bucket(
            amounts, names, CUSTOMERS_INCOME_TITLE, day.month, operation_amount(op)
        )

    for payment in data.other_payments:
        if getattr(payment, "direction", OUTGOING) != INCOMING:
            continue
        # v0.33.0: в блоке «Поступление» своих видов нет — там ровно
        # четыре строки. Поэтому считаем только постоянную строку
        # «Прочие поступления»: посторонние записи направления
        # INCOMING (например, «Новое поступление», оставшееся от
        # старого вызова «+») в сводку не попадают.
        if (payment.name or OTHER_INCOME_TITLE) != OTHER_INCOME_TITLE:
            continue
        day = _iso_day(payment.date)
        if day is None or day.year != year:
            continue
        _add_to_bucket(
            amounts,
            names,
            OTHER_INCOME_TITLE,
            day.month,
            other_payment_amount(payment),
        )

    # Порядок строк — как в блоке «Поступление»: наличные, карты,
    # покупатели, «Прочие поступления». Других строк нет (v0.33.0),
    # все четыре видны и без данных за год.
    order = [
        CASH_INCOME_TITLE,
        CARD_INCOME_TITLE,
        CUSTOMERS_INCOME_TITLE,
        OTHER_INCOME_TITLE,
    ]
    return _summary_from_buckets(year, amounts, order)
