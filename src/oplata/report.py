"""«Отчет по организации» — расчёт (v0.85.0).

Сделан по файлу «ОТЧЕТ ПО ОРГАНИЗАЦИИ мир 2.xlsx» (запрос пользователя).
Структура файла сохранена 1:1: столбцы «1кв», «2кв», «3кв», «4кв» и
«Итого», строки — в том же порядке, а рядом с названием стоит колонка
«НДС» — **справочная** ставка (0, 20, 25) для строк затрат.

Откуда берутся числа (проверено на файле — сходится до копейки):

**Считает программа:**

* «Выручка по отчетам ККМ с НДС» — реестр «Отчеты ККМ» за квартал
  (отчёты минус возвраты, как «Итого по Отчетам ККМ» в «Сводке»);
* «Реализация (Накладные) с НДС» — «Реализация Покупателям» за квартал;
* «Себестоимость затрат без НДС» и сами строки затрат — виды оплат
  блока «Оплата», кроме строки «Поставщики»;
* всё остальное, что в файле считается формулами.

**Вводит пользователь** (цифры приходят из бухгалтерии), по кварталам:
«НДС Реализация (Накладные)», «Товары реализация без НДС»,
«Входящий НДС», «Себестоимость реализация без НДС».

Формулы из файла (записаны в формате R1C1 — так их дал пользователь):

* «Итого НДС» = ``R[-3]C - R[1]C - (R[-3]C - R[1]C)/1,2`` — это
  (Итого выручка − Товары) минус НДС 20 % с этой разницы;
* «НДС Выручка по отчетам ККМ» = ``R[2]C - R[1]C`` — Итого НДС минус
  НДС реализации;
* «Вычет по товарам без НДС» считается **накоплением с начала года**:
  доля входящего НДС, приходящаяся на товары без НДС, минус то, что уже
  отнесено к прежним кварталам. Поэтому «Итого» у этой строки — своя
  формула (``R[-1]C/R[-5]C*R[1]C``), а 4 квартал — остаток
  (``RC[1]-RC[-1]-RC[-2]-RC[-3]``). В файле из-за этого «Итого» на
  копейку больше суммы кварталов — здесь так же.

Промежуточные значения не округляются (округляется только показ) — иначе
«Итого» расходилось бы с файлом на копейки.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import calc
from .dates import parse_money
from .models import AppData, OrganizationReport

ZERO = Decimal("0")
# Ставка налога на прибыль — 20 % (в файле так во всех кварталах).
PROFIT_TAX_RATE = Decimal("0.2")
# Коэффициент НДС из формулы «Итого НДС»: 1,2 — это 20 %.
VAT_DIVISOR = Decimal("1.2")

# Названия строк — как в файле (порядок тоже как в файле).
KKM_ROW = "emission_kkm"
SALE_ROW = "sale_invoiced"
REVENUE_ROW = "revenue_total"
KMM_VAT_ROW = "vat_kkm"
SALE_VAT_ROW = "vat_sale"
VAT_TOTAL_ROW = "vat_total"
GOODS_ROW = "goods_no_vat"
DEDUCTION_ROW = "vat_deduction"
INCOMING_VAT_ROW = "incoming_vat"
INCOMING_NET_ROW = "incoming_vat_net"
COST_SALE_ROW = "cost_realization"
COST_EXPENSES_ROW = "cost_expenses"
VAT_TAX_ROW = "vat_tax"
PROFIT_TAX_ROW = "profit_tax"
PROFIT_ROW = "profit"
NET_PROFIT_ROW = "net_profit"

# Пометки строки: что с ней делает пользователь и как её красить.
MANUAL = "manual"        # вводится руками (клетку можно править)
CALCULATED = "calc"      # считается программой
EXPENSE = "expense"      # строка затрат (в файле — жёлтая)


@dataclass(frozen=True)
class ReportRow:
    """Строка «Отчета»: название, ставка НДС справочно и суммы."""

    key: str
    title: str
    # Четыре квартала и «Итого» — уже посчитанные значения.
    quarters: tuple[Decimal, Decimal, Decimal, Decimal]
    total: Decimal
    kind: str = CALCULATED
    # Справочная ставка НДС (0, 20, 25) — только у строк затрат.
    vat_rate: str = ""
    # Примечание вида оплаты — из его карточки (v0.86.0). У остальных
    # строк примечания нет.
    note: str = ""
    # Суммы из «Оплаты» до ручной правки — только у строк затрат.
    # По ним видно, что именно поправили в «Отчете» (v0.86.0).
    from_payments: tuple = ()
    # Жирным: итоговые строки файла.
    bold: bool = False

    @property
    def manual(self) -> bool:
        return self.kind == MANUAL

    @property
    def expense(self) -> bool:
        return self.kind == EXPENSE


@dataclass(frozen=True)
class OrganizationReportData:
    """Весь «Отчет» за год: строки по порядку файла."""

    year: int
    rows: tuple[ReportRow, ...]

    def row(self, key: str) -> ReportRow | None:
        """Строка по ключу (``None``, если такой нет)."""

        for row in self.rows:
            if row.key == key:
                return row
        return None

    def value(self, key: str, quarter: int) -> Decimal:
        """Значение строки за квартал ``quarter`` (0…3) или «Итого» (4)."""

        row = self.row(key)
        if row is None:
            return ZERO
        if quarter == 4:
            return row.total
        return row.quarters[quarter]


# --------------------------------------------------------------------- ввод
def _money(text: str) -> Decimal:
    """Сумма из строки ввода: пусто и мусор — ноль."""

    if not str(text or "").strip():
        return ZERO
    parsed = parse_money(text)
    return parsed if parsed is not None else ZERO


def quarter_kkm(data: AppData, year: int, quarter: int) -> Decimal:
    """«Выручка по отчетам ККМ с НДС» за квартал: отчёты минус возвраты."""

    total = ZERO
    for month in calc.QUARTER_MONTHS[quarter]:
        total += calc.kkm_month_totals(data, year, month)[2]
    return total


def quarter_realization(data: AppData, year: int, quarter: int) -> Decimal:
    """«Реализация (Накладные) с НДС» за квартал — накладные покупателям."""

    total = ZERO
    for month in calc.QUARTER_MONTHS[quarter]:
        total += calc.month_realization_invoiced(data, year, month)
    return total


def expense_key(name: str) -> str:
    """Ключ строки затрат в файле данных: ``expense:<название вида>``."""

    return f"expense:{name}"


@dataclass(frozen=True)
class ExpenseRow:
    """Строка затрат «Отчета» — вид оплаты из блока «Оплата» (v0.86.0)."""

    name: str
    note: str
    vat_rate: str
    quarters: tuple[Decimal, Decimal, Decimal, Decimal]
    # Какие кварталы введены руками в «Отчете» (индексы 0…3).
    manual_quarters: tuple[int, ...] = ()
    # Суммы из «Оплаты» до правки — чтобы показать их в подсказке.
    from_payments: tuple[Decimal, Decimal, Decimal, Decimal] = (
        ZERO, ZERO, ZERO, ZERO,
    )

    @property
    def total(self) -> Decimal:
        return sum(self.quarters, ZERO)


def expense_rows(
    data: AppData,
    year: int,
    report: OrganizationReport | None = None,
) -> list[ExpenseRow]:
    """Строки затрат: вид оплаты, примечание, ставка НДС и суммы.

    Берём «Сводку оплат» — там по строке на каждый вид оплаты с суммами
    по месяцам и кварталам. Строку «Поставщики» пропускаем: оплаты
    поставщикам в отчёте отдельной строкой не идут (в файле такой
    строки нет).

    **v0.86.0: затраты можно поправить вручную.** Правка живёт только
    внутри «Отчета»: введённое число заменяет сумму из «Оплаты» за этот
    квартал, а сами оплаты не меняются. Поэтому «Оплата» и «Отчет»
    после правки расходятся — так и задумано (правило пользователя:
    «правки в таблице отчет влияют только на данные и результат в
    таблице отчет, в обратную сторону на оплаты они не влияют»).
    Пустая клетка — снова сумма из «Оплаты».
    """

    summary = calc.payments_summary(data, year)
    notes = calc.outgoing_notes(data)
    manual = report if report is not None else getattr(
        data, "org_report", OrganizationReport()
    )
    result: list[ExpenseRow] = []
    for row in summary.rows:
        if row.name == calc.SUPPLIERS_PAYMENTS_TITLE:
            continue
        key = expense_key(row.name)
        quarters: list[Decimal] = []
        edited: list[int] = []
        for number in range(4):
            entered = manual.quarter(year, key, number)
            if str(entered or "").strip():
                quarters.append(_money(entered))
                edited.append(number)
            else:
                quarters.append(row.quarters[number])
        result.append(
            ExpenseRow(
                name=row.name,
                note=notes.get(row.name, ""),
                vat_rate=row.vat_rate,
                quarters=tuple(quarters),  # type: ignore[arg-type]
                manual_quarters=tuple(edited),
                from_payments=tuple(row.quarters),  # type: ignore[arg-type]
            )
        )
    return result


def _deduction(
    manual: OrganizationReport,
    year: int,
    revenue: tuple[Decimal, ...],
    goods: tuple[Decimal, ...],
    incoming: tuple[Decimal, ...],
) -> tuple[tuple[Decimal, Decimal, Decimal, Decimal], Decimal]:
    """«Вычет по товарам без НДС» — формула файла (накопление с года).

    Доля входящего НДС, приходящаяся на товары без НДС, считается от
    выручки **с начала года**, поэтому каждый квартал — это накопленная
    доля минус то, что уже отнесено к прежним кварталам. «Итого» — своя
    формула (за год), а 4 квартал — остаток до «Итого» (в файле так:
    ``RC[1]-RC[-1]-RC[-2]-RC[-3]``).
    """

    quarters: list[Decimal] = []
    accumulated = ZERO
    for number in range(4):
        revenue_sum = sum(revenue[: number + 1], ZERO)
        goods_sum = sum(goods[: number + 1], ZERO)
        incoming_sum = sum(incoming[: number + 1], ZERO)
        if revenue_sum == 0:
            value = ZERO
        else:
            value = incoming_sum / revenue_sum * goods_sum - accumulated
        quarters.append(value)
        accumulated += value

    revenue_year = sum(revenue, ZERO)
    total = (
        sum(incoming, ZERO) / revenue_year * sum(goods, ZERO)
        if revenue_year
        else ZERO
    )
    # 4 квартал — остаток до «Итого»: сумма всех четырёх даёт «Итого».
    quarters[3] = total - quarters[0] - quarters[1] - quarters[2]
    return (quarters[0], quarters[1], quarters[2], quarters[3]), total


def organization_report(
    data: AppData,
    year: int,
    report: OrganizationReport | None = None,
) -> OrganizationReportData:
    """Посчитать «Отчет» за год ``year``.

    ``report`` — введённые вручную значения (по умолчанию — из данных).
    """

    manual = report if report is not None else getattr(
        data, "org_report", OrganizationReport()
    )

    def entered(key: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        return tuple(
            _money(manual.quarter(year, key, number)) for number in range(4)
        )  # type: ignore[return-value]

    # --- данные, которые приходят из программы ---
    kkm = tuple(quarter_kkm(data, year, number) for number in range(4))
    sale = tuple(quarter_realization(data, year, number) for number in range(4))
    # --- данные от бухгалтерии ---
    sale_vat = entered(SALE_VAT_ROW)
    goods = entered(GOODS_ROW)
    incoming = entered(INCOMING_VAT_ROW)
    cost_sale = entered(COST_SALE_ROW)

    expenses = expense_rows(data, year, manual)
    expense_quarters = [
        sum((row.quarters[number] for row in expenses), ZERO)
        for number in range(4)
    ]
    cost_expenses = tuple(expense_quarters)

    # --- формулы файла ---
    revenue = tuple(kkm[i] + sale[i] for i in range(4))

    # «Итого НДС» = (выручка − товары) − (выручка − товары)/1,2
    vat_total = []
    for i in range(4):
        base = revenue[i] - goods[i]
        vat_total.append(base - base / VAT_DIVISOR)

    # «НДС Выручка по отчетам ККМ» = Итого НДС − НДС реализации
    vat_kkm = tuple(vat_total[i] - sale_vat[i] for i in range(4))

    deductions, deduction_total = _deduction(
        manual, year, revenue, goods, incoming
    )

    # «Входящий НДС с вычетом» = входящий НДС − вычет
    incoming_net = tuple(incoming[i] - deductions[i] for i in range(4))

    # «Налог НДС» = Итого НДС − Входящий НДС с вычетом
    vat_tax = tuple(vat_total[i] - incoming_net[i] for i in range(4))

    # «Прибыль» = (выручка − НДС) − себестоимости
    profit = tuple(
        revenue[i] - vat_total[i] - cost_sale[i] - cost_expenses[i]
        for i in range(4)
    )
    profit_tax = tuple(value * PROFIT_TAX_RATE for value in profit)
    net_profit = tuple(profit[i] - profit_tax[i] for i in range(4))

    def row(
        key: str,
        title: str,
        values: tuple,
        kind: str = CALCULATED,
        vat_rate: str = "",
        bold: bool = False,
    ) -> ReportRow:
        quarters = tuple(values)  # type: ignore[arg-type]
        return ReportRow(
            key=key,
            title=title,
            quarters=quarters,
            total=sum(quarters, ZERO),
            kind=kind,
            vat_rate=vat_rate,
            bold=bold,
        )

    rows: list[ReportRow] = [
        # Файл начинается с итогов — так и оставляем.
        row(VAT_TAX_ROW, "Налог НДС", vat_tax, bold=True),
        row(PROFIT_TAX_ROW, "Налог на прибыль", profit_tax, bold=True),
        row(PROFIT_ROW, "Прибыль", profit, bold=True),
        row(NET_PROFIT_ROW, "Чистая прибыль", net_profit, bold=True),
        row(KKM_ROW, "Выручка по отчетам ККМ с НДС", kkm),
        row(SALE_ROW, "Реализация (Накладные) с НДС", sale),
        row(REVENUE_ROW, "Итого выручка", revenue),
        row(KMM_VAT_ROW, "НДС Выручка по отчетам ККМ", vat_kkm),
        row(
            SALE_VAT_ROW,
            "НДС Реализация (Накладные)",
            sale_vat,
            kind=MANUAL,
        ),
        row(VAT_TOTAL_ROW, "Итого НДС", tuple(vat_total)),
        row(GOODS_ROW, "Товары реализация без НДС", goods, kind=MANUAL),
        # «Вычет» — своя формула и в кварталах, и в «Итого».
        ReportRow(
            key=DEDUCTION_ROW,
            title="Вычет по товарам без НДС",
            quarters=deductions,
            total=deduction_total,
        ),
        row(INCOMING_VAT_ROW, "Входящий НДС", incoming, kind=MANUAL),
        row(INCOMING_NET_ROW, "Входящий НДС с вычетом", incoming_net),
        row(
            COST_SALE_ROW,
            "Себестоимость реализация без НДС",
            cost_sale,
            kind=MANUAL,
        ),
        row(
            COST_EXPENSES_ROW,
            "Себестоимость затрат без НДС",
            cost_expenses,
            bold=True,
        ),
    ]
    for expense in expenses:
        rows.append(
            ReportRow(
                key=expense_key(expense.name),
                title=expense.name,
                quarters=expense.quarters,
                total=expense.total,
                kind=EXPENSE,
                vat_rate=expense.vat_rate,
                note=expense.note,
                from_payments=expense.from_payments,
            )
        )
    return OrganizationReportData(year=year, rows=tuple(rows))
