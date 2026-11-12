"""Разнесение выписки по базе программы (v0.56.0, второй этап).

Первый этап (v0.54.0) только показывал выписку таблицей. Здесь строка
выписки превращается в запись базы:

* **поступление**
  * «Перечисление денежных средств по операциям с платежными
    карточками» → строка «Поступление по картам»;
  * «ВЫРУЧКА ОТ РЕАЛИЗАЦИИ» → строка «Поступление наличными»;
  * УНП совпал с покупателем → строка «Покупатели»;
  * иначе → «Прочие поступления»;
* **оплата**
  * УНП совпал с поставщиком → строка «Поставщики»;
  * иначе ищем вид оплаты с таким УНП;
  * не нашёлся — строка ждёт карточку нового вида оплаты.

Правило пользователя: **поиск всегда в первую очередь по УНП**, дата
записи — **фактическая дата проведения операции**.

Поставщиков и покупателей в базе наполняют постепенно, поэтому
``classify`` ничего не запоминает: при повторном открытии выписки
строки, для которых контрагент появился, определятся сами.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .calc import OTHER_INCOME_TITLE
from .models import (
    CARD_SOURCE,
    CASH_SOURCE,
    INCOMING,
    KIND_PAYMENT,
    KIND_SALE,
    OUTGOING,
    AppData,
    Counterparty,
    add_realization,
    cash_receipt_amount,
    daily_operation_total,
    set_daily_cash_receipt,
    set_daily_operation,
    set_daily_other_payment,
)
from .statement import Statement, StatementRow

# Куда попадает строка.
TARGET_CARD = "card"                  # «Поступление по картам»
TARGET_CASH = "cash"                  # «Поступление наличными»
TARGET_CUSTOMERS = "customers"        # строка «Покупатели»
TARGET_SUPPLIERS = "suppliers"        # строка «Поставщики»
TARGET_OTHER_INCOME = "other_income"  # «Прочие поступления»
TARGET_OTHER_PAYMENT = "other_payment"  # свой вид оплаты (по УНП)
TARGET_NEW_PAYMENT = "new_payment"    # нужна карточка вида оплаты

# Строка «Прочие поступления» блока «Поступление» (имя из calc, чтобы
# выписка писала ровно ту строку, которую показывает таблица).
OTHER_INCOME_ROW_TITLE = OTHER_INCOME_TITLE


@dataclass
class Match:
    """Куда попадает строка выписки и что писать в «Примечание»."""

    target: str
    note: str
    counterparty_id: str = ""
    payment_name: str = ""


@dataclass
class Distribution:
    """Итог разнесения: что записали, что пропустили, что ждёт карточек."""

    applied: list[str] = field(default_factory=list)   # строки отчёта
    already: list[str] = field(default_factory=list)   # уже разносили раньше
    unresolved: list[StatementRow] = field(default_factory=list)
    rows: list[tuple[StatementRow, Match]] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return len(self.applied)


def account_base_key(account_name: str) -> str | None:
    """База по имени счёта из выписки (v0.56.0).

    «ООО МирСтройМаркет» — МИР, «ООО Мирсититорг» — КОРЕЛИЧИ. Сверка по
    части имени без пробелов и регистра: в выписке написание плавает.
    """

    from .storage import BASE_KORELICHI, BASE_MIR

    flat = "".join(account_name.split()).casefold().replace("ооо", "")
    if not flat:
        return None
    if "мирсититорг" in flat or "мисититорг" in flat or "сититорг" in flat:
        return BASE_KORELICHI
    if "мирстроймаркет" in flat or "строймаркет" in flat:
        return BASE_MIR
    return None


def find_by_unp(items, unp: str) -> Counterparty | None:
    """Контрагент по УНП: сначала действующий, потом архивный."""

    unp = (unp or "").strip()
    if not unp:
        return None
    for item in items:
        if item.unp == unp and not item.archived:
            return item
    for item in items:
        if item.unp == unp:
            return item
    return None


def find_payment_by_unp(data: AppData, unp: str):
    """Вид оплаты (блока «Оплата») по УНП — из карточки вида (v0.55.0)."""

    unp = (unp or "").strip()
    if not unp:
        return None
    for payment in data.other_payments:
        if payment.unp == unp and payment.direction == OUTGOING:
            return payment
    return None


def classify(row: StatementRow, data: AppData, bank_unp: str = "") -> Match:
    """Определить, куда попадёт строка выписки.

    ``bank_unp`` — УНП обслуживающего банка из «Конфигурации»: его
    операции не поставщик и не покупатель. Строки эквайринга и выручки
    приходят с УНП банка, но это деньги магазина — их разносим.
    """

    if row.is_incoming:
        if row.is_cards:
            return Match(TARGET_CARD, "Поступление по картам")
        if row.is_cash:
            return Match(TARGET_CASH, "Поступление наличными")
        customer = find_by_unp(data.customers, row.unp)
        if customer is not None:
            return Match(
                TARGET_CUSTOMERS,
                f"Покупатель: {customer.name}",
                counterparty_id=customer.id,
            )
        return Match(
            TARGET_OTHER_INCOME,
            f"Прочие поступления (УНП {row.unp} не найден)"
            if row.unp
            else "Прочие поступления (без УНП)",
        )
    if bank_unp and row.unp == bank_unp:
        return Match("", "Банк: не разносится")

    supplier = find_by_unp(data.suppliers, row.unp)
    if supplier is not None:
        return Match(
            TARGET_SUPPLIERS,
            f"Поставщик: {supplier.name}",
            counterparty_id=supplier.id,
        )
    payment = find_payment_by_unp(data, row.unp)
    if payment is not None:
        return Match(
            TARGET_OTHER_PAYMENT,
            f"Вид оплаты: {payment.name}",
            payment_name=payment.name,
        )
    return Match(
        TARGET_NEW_PAYMENT,
        f"Нужен новый вид оплаты (УНП {row.unp})"
        if row.unp
        else "Нужен новый вид оплаты",
    )


def classify_all(
    statement: Statement, data: AppData, bank_unp: str = ""
) -> list[Match]:
    return [classify(row, data, bank_unp) for row in statement.rows]


def iso_date(row: StatementRow) -> str:
    """«05.11.2026» → «2026-11-05» (фактическая дата операции)."""

    day, month, year = row.date.split(".")
    return f"{year}-{month}-{day}"


def plan_key(row: StatementRow, match: Match) -> tuple:
    """Ключ записи дня: назначение + получатель + дата операции."""

    return (match.target, match.counterparty_id, match.payment_name,
            iso_date(row))


def plan_totals(
    statement: Statement,
    data: AppData,
    matches: list[Match] | None = None,
) -> dict[tuple, Decimal]:
    """Суммы, которые разнос запишет по каждой записи дня (v0.57.0).

    Запись дня в базе одна, поэтому строки выписки с одним назначением
    и одной датой складываются: иначе вторая затёрла бы первую.
    """

    planned: dict[tuple, Decimal] = {}
    for index, row in enumerate(statement.rows):
        match = (
            matches[index]
            if matches is not None and index < len(matches)
            else classify(row, data)
        )
        if not match.target or match.target == TARGET_NEW_PAYMENT:
            continue
        key = plan_key(row, match)
        planned[key] = planned.get(key, Decimal("0")) + row.amount
    return planned


def base_amount(data: AppData, match: Match, date_iso: str) -> Decimal:
    """Сколько по этому назначению **уже записано** в базе за день.

    Показывается в окне разноса до записи (v0.57.0): видно, совпадает
    сумма выписки с уже введённой или день придётся перезаписать.
    """

    if match.target == TARGET_CARD:
        return _cash_receipt_total(data, CARD_SOURCE, date_iso)
    if match.target == TARGET_CASH:
        return _cash_receipt_total(data, CASH_SOURCE, date_iso)
    if match.target == TARGET_CUSTOMERS:
        return daily_operation_total(
            data.operations, match.counterparty_id, date_iso, KIND_SALE
        )
    if match.target == TARGET_SUPPLIERS:
        return daily_operation_total(
            data.operations, match.counterparty_id, date_iso, KIND_PAYMENT
        )
    if match.target == TARGET_OTHER_INCOME:
        return _other_payment_total(
            data, OTHER_INCOME_ROW_TITLE, date_iso, INCOMING
        )
    if match.target == TARGET_OTHER_PAYMENT:
        return _other_payment_total(
            data, match.payment_name, date_iso, OUTGOING
        )
    return Decimal("0")


def _cash_receipt_total(data: AppData, source: str, date_iso: str) -> Decimal:
    return sum(
        (
            cash_receipt_amount(receipt)
            for receipt in data.cash_receipts
            if receipt.source == source and receipt.date == date_iso
        ),
        Decimal("0"),
    )


def _other_payment_total(
    data: AppData, name: str, date_iso: str, direction: str
) -> Decimal:
    total = Decimal("0")
    for payment in data.other_payments:
        if (
            payment.name == name
            and payment.date == date_iso
            and payment.direction == direction
        ):
            try:
                total += Decimal(payment.amount or "0")
            except InvalidOperation:  # битую сумму считаем нулевой
                continue
    return total


def distribute(
    statement: Statement,
    data: AppData,
    matches: list[Match] | None = None,
) -> Distribution:
    """Разнести выписку по базе.

    ``matches`` — назначение строк, поправленное пользователем в окне
    «Разнести»; без него назначение считается заново.

    Суммы одного дня и одного получателя складываем, а не пишем подряд:
    запись дня в базе одна (``set_daily_*``), иначе вторая строка затёрла
    бы первую.
    """

    result = Distribution()
    planned: dict[tuple, Decimal] = {}
    seen: set[str] = set(getattr(data, "imported_statement_rows", []))
    labels: dict[tuple, str] = {}

    for index, row in enumerate(statement.rows):
        match = (
            matches[index]
            if matches is not None and index < len(matches)
            else classify(row, data)
        )
        result.rows.append((row, match))
        key = row.import_key()
        if key in seen:
            result.already.append(
                f"{row.date} · {row.name or row.unp} · {row.amount}"
            )
            continue
        if match.target == TARGET_NEW_PAYMENT:
            result.unresolved.append(row)
            continue
        target_key = plan_key(row, match)
        planned[target_key] = planned.get(target_key, Decimal("0")) + row.amount
        labels[target_key] = match.note
        seen.add(key)

    for (target, counterparty_id, payment_name, date_iso), amount in sorted(
        planned.items()
    ):
        if target == TARGET_CARD:
            set_daily_cash_receipt(data, CARD_SOURCE, date_iso, amount)
        elif target == TARGET_CASH:
            set_daily_cash_receipt(data, CASH_SOURCE, date_iso, amount)
        elif target == TARGET_CUSTOMERS:
            add_realization(data, counterparty_id, date_iso, amount)
        elif target == TARGET_SUPPLIERS:
            set_daily_operation(
                data, counterparty_id, date_iso, KIND_PAYMENT, amount
            )
        elif target == TARGET_OTHER_INCOME:
            set_daily_other_payment(
                data, OTHER_INCOME_ROW_TITLE, date_iso, amount, INCOMING
            )
        elif target == TARGET_OTHER_PAYMENT:
            set_daily_other_payment(
                data, payment_name, date_iso, amount, OUTGOING
            )
        result.applied.append(
            f"{labels[(target, counterparty_id, payment_name, date_iso)]}: "
            f"{date_iso} — {amount}"
        )

    data.imported_statement_rows = sorted(seen)
    return result
