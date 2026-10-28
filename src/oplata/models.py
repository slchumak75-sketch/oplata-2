"""Модели данных оболочки (без Qt, без формул).

Поля взяты строго из макетов (см. ``docs/SPEC.md``):

* Карточка поставщика: наименование*, отсрочка платежа (дней),
  режим оплаты (Предоплата / Реализация), «В архиве».
* Карточка покупателя: только наименование и архив (уточнено v0.7.0).
* Операция: тип («Приход» поставщика, «Реализация» покупателя или
  «Оплата»), контрагент, дата, сумма, документ, комментарий.

Никаких вычисляемых полей (долг, просрочка) здесь нет и быть не должно —
они появятся в ``calc.py`` на этапе формул.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Counterparty:
    """Поставщик или покупатель (структура карточек одинаковая)."""

    name: str
    id: str = field(default_factory=new_id)
    deferment_days: int = 0
    prepayment: bool = False    # Режим оплаты: «Предоплата»
    realization: bool = False   # Режим оплаты: «Реализация»
    archived: bool = False      # «В архиве (скрывать из списков, историю сохранить)»

    @classmethod
    def from_dict(cls, data: dict) -> "Counterparty":
        return cls(
            name=str(data.get("name", "")),
            id=str(data.get("id") or new_id()),
            deferment_days=int(data.get("deferment_days", 0)),
            prepayment=bool(data.get("prepayment", False)),
            realization=bool(data.get("realization", False)),
            archived=bool(data.get("archived", False)),
        )


@dataclass
class Operation:
    """Операция недельных таблиц и журнала операций.

    Типы: «Приход» (поставка поставщика), «Реализация» (покупатель) и
    «Оплата». Срок ``due_date`` есть только у «Прихода» и рассчитывается
    при вводе (дата поставки + отсрочка, см. calc.payment_due_date);
    у «Реализации» и «Оплаты» он пуст.
    """

    kind: str = ""
    counterparty_id: str = ""
    date: str = ""          # ISO ``YYYY-MM-DD`` строкой, чтобы JSON был простым
    amount: str = "0"       # Decimal строкой (без float!)
    document: str = ""
    comment: str = ""
    due_date: str = ""      # ISO срок оплаты (только для «Прихода»-поставки)
    id: str = field(default_factory=new_id)

    @classmethod
    def from_dict(cls, data: dict) -> "Operation":
        return cls(
            kind=str(data.get("kind", "")),
            counterparty_id=str(data.get("counterparty_id", "")),
            date=str(data.get("date", "")),
            amount=str(data.get("amount", "0")),
            document=str(data.get("document", "")),
            comment=str(data.get("comment", "")),
            due_date=str(data.get("due_date", "")),
            id=str(data.get("id") or new_id()),
        )


CASH_SOURCE = "cash"   # «Поступление наличными»
CARD_SOURCE = "card"   # «Поступление по картам»


# Направления прочих строк на вкладке «Поступление и оплаты» (v0.12.0).
INCOMING = "in"    # блок «Поступление» — «прочие поступления»
OUTGOING = "out"   # блок «Оплата» — прочие виды оплат


@dataclass
class CashReceipt:
    """Поступление денег в кассу/на карту без привязки к покупателю.

    Появилось на экране «Поступление и оплаты» (v0.9.0): строки
    «Поступление наличными» и «Поступление по картам». Это отдельная запись,
    а не операция контрагента, поэтому в недельных таблицах она не
    участвует — показывается только итогом дня на своём экране.
    """

    date: str = ""             # ISO ``YYYY-MM-DD``
    source: str = CASH_SOURCE  # CASH_SOURCE | CARD_SOURCE
    amount: str = "0"          # Decimal строкой
    comment: str = ""
    id: str = field(default_factory=new_id)

    @classmethod
    def from_dict(cls, data: dict) -> "CashReceipt":
        source = str(data.get("source", CASH_SOURCE))
        return cls(
            date=str(data.get("date", "")),
            source=source if source in (CASH_SOURCE, CARD_SOURCE) else CASH_SOURCE,
            amount=str(data.get("amount", "0")),
            comment=str(data.get("comment", "")),
            id=str(data.get("id") or new_id()),
        )


def cash_receipt_amount(receipt: CashReceipt) -> Decimal:
    """Сумма поступления как Decimal (битая строка трактуется как 0)."""

    try:
        return Decimal(receipt.amount or "0")
    except InvalidOperation:
        return Decimal("0")


@dataclass
class OtherPayment:
    """Прочий вид оплаты: своё название, сумма за дату (v0.11.0).

    Пользователь добавляет такие строки на вкладке «Поступление и оплаты»
    кнопкой «+» — в блоке «Оплата», а с v0.12.0 и в блоке «Поступление»
    («прочие поступления»): название вводится прямо в ячейку, сумма — в
    ячейку «Сумма». Запись хранится за конкретную дату, а нарастающие
    итоги строки считаются по названию (по всему виду оплаты).

    ``direction`` разделяет два блока: ``INCOMING`` — «Поступление»,
    ``OUTGOING`` — «Оплата». У старых файлов поля нет, поэтому
    ``from_dict`` подставляет ``OUTGOING``: ранее созданные виды оплат
    остаются в блоке «Оплата».
    """

    date: str = ""      # ISO ``YYYY-MM-DD``
    name: str = ""      # название вида оплаты, которое ввёл пользователь
    amount: str = "0"   # Decimal строкой
    direction: str = OUTGOING
    id: str = field(default_factory=new_id)

    @classmethod
    def from_dict(cls, data: dict) -> "OtherPayment":
        return cls(
            date=str(data.get("date", "")),
            name=str(data.get("name", "")),
            amount=str(data.get("amount", "0")),
            id=str(data.get("id") or new_id()),
            direction=str(data.get("direction") or OUTGOING),
        )


def other_payment_amount(payment: OtherPayment) -> Decimal:
    """Сумма прочей оплаты как Decimal (битая строка — 0)."""

    try:
        return Decimal(payment.amount or "0")
    except InvalidOperation:
        return Decimal("0")


@dataclass
class AppData:
    """Весь сохраняемый набор данных (один JSON-файл, см. storage.py)."""

    suppliers: list[Counterparty] = field(default_factory=list)
    customers: list[Counterparty] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    # Поступления наличными/по картам без контрагента (экран
    # «Поступление и оплаты»). Поле добавлено в v0.9.0, в старых файлах
    # его нет — from_dict подставит пустой список.
    cash_receipts: list[CashReceipt] = field(default_factory=list)
    # Прочие виды оплат блока «Оплата» (своё название + сумма за дату).
    # Поле добавлено в v0.11.0; в старых файлах его нет — пустой список.
    other_payments: list[OtherPayment] = field(default_factory=list)
    # Ручные отметки календаря «Выходные и рабочие дни»:
    # "YYYY-MM-DD" -> "off" (выходной) | "work" (рабочий).
    # Без отметки Сб/Вс считаются выходными, Пн–Пт — рабочими.
    day_overrides: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "suppliers": [asdict(s) for s in self.suppliers],
            "customers": [asdict(c) for c in self.customers],
            "operations": [asdict(o) for o in self.operations],
            "cash_receipts": [asdict(r) for r in self.cash_receipts],
            "other_payments": [asdict(p) for p in self.other_payments],
            "day_overrides": dict(self.day_overrides),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppData":
        result = cls(
            suppliers=[Counterparty.from_dict(x) for x in data.get("suppliers", [])],
            customers=[Counterparty.from_dict(x) for x in data.get("customers", [])],
            operations=[Operation.from_dict(x) for x in data.get("operations", [])],
            cash_receipts=[
                CashReceipt.from_dict(x) for x in data.get("cash_receipts", [])
            ],
            other_payments=[
                OtherPayment.from_dict(x) for x in data.get("other_payments", [])
            ],
            day_overrides={
                str(k): str(v) for k, v in data.get("day_overrides", {}).items()
            },
        )
        # Миграция v0.8.0: у покупателей прежний «Приход» теперь называется
        # «Реализация» и не имеет срока оплаты.
        customer_ids = {customer.id for customer in result.customers}
        for operation in result.operations:
            if (
                operation.kind == KIND_INCOME
                and operation.counterparty_id in customer_ids
            ):
                operation.kind = KIND_SALE
                operation.due_date = ""
        return result


def seed_data() -> AppData:
    """Демо-данные первого запуска — в точности имена с макетов.

    Поставщики со скриншотов: ааа, ббб, ггг, eee, жжж (у «ггг» на макете
    карточки отсрочка 10 дней). Покупателей на макете 0, операций 0.
    Пользователь может удалить/переименовать их через карточки.
    """
    return AppData(
        suppliers=[
            Counterparty(name="ааа"),
            Counterparty(name="ббб"),
            Counterparty(name="ггг", deferment_days=10),
            Counterparty(name="eee"),
            Counterparty(name="жжж"),
        ],
        customers=[],
        operations=[],
        day_overrides={},
    )


# ---------------------------------------------------------------------------
# Ввод сумм в недельной таблице (Приход / Оплата)
#
# Запрос пользователя 27.10.2026: «сделать возможность ввода в поле приход
# и оплата». Введённая в ячейку сумма хранится как Operation за этот день,
# поэтому она видна в «Журнале операций» и является исходными данными для
# формул срока и архивного долга поставщиков.
# Ячейка показывает ИТОГ дня. С v0.4.0 единственная операция дня при правке
# ячейки обновляется на месте (документ/комментарий/срок сохраняются); если
# операций несколько, итог дня по-прежнему заменяется одной (ADR-006/009).
# ---------------------------------------------------------------------------

KIND_INCOME = "Приход"
KIND_SALE = "Реализация"
KIND_PAYMENT = "Оплата"


def operation_amount(op: Operation) -> Decimal:
    """Сумма операции как Decimal (битая строка трактуется как 0)."""
    try:
        return Decimal(op.amount or "0")
    except InvalidOperation:
        return Decimal("0")


_op_amount = operation_amount


def daily_operation_total(
    operations: list[Operation], counterparty_id: str, date_iso: str, kind: str
) -> Decimal:
    """Сумма операций контрагента за день по типу (Приход/Оплата)."""
    total = Decimal("0")
    for op in operations:
        if op.counterparty_id == counterparty_id and op.date == date_iso and op.kind == kind:
            total += _op_amount(op)
    return total


def set_daily_operation(
    data: AppData,
    counterparty_id: str,
    date_iso: str,
    kind: str,
    amount: Decimal | None,
    due_date_iso: str = "",
) -> None:
    """Задать итог дня из ячейки таблицы. ``None`` или 0 — очистить день.

    Если операция такого типа за день ровно одна, она обновляется на месте:
    документ, комментарий и уже сохранённый срок оплаты не теряются
    (важно после появления редактора операций). Если операций несколько,
    итог дня заменяется одной операцией — известное ограничение ADR-006.

    ``due_date_iso`` — срок оплаты для новой «Приход»-операции (считает
    вызывающий код через calc.payment_due_date), для «Оплаты» не нужен.
    """

    def matches(op: Operation) -> bool:
        return (
            op.counterparty_id == counterparty_id
            and op.date == date_iso
            and op.kind == kind
        )

    if amount is None or amount == 0:
        data.operations = [op for op in data.operations if not matches(op)]
        return

    amount_text = f"{amount.quantize(Decimal('0.01')):.2f}"
    existing = [op for op in data.operations if matches(op)]
    if len(existing) == 1:
        op = existing[0]
        op.amount = amount_text
        if kind == KIND_INCOME and due_date_iso and not op.due_date:
            op.due_date = due_date_iso
        return

    data.operations = [op for op in data.operations if not matches(op)]
    data.operations.append(
        Operation(
            kind=kind,
            counterparty_id=counterparty_id,
            date=date_iso,
            amount=amount_text,
            due_date=due_date_iso if kind == KIND_INCOME else "",
        )
    )


def add_delivery(
    data: AppData,
    counterparty_id: str,
    delivery_date_iso: str,
    due_date_iso: str,
    amount: Decimal,
) -> Operation:
    """Добавить поставку из окна «Поставка» (в отличие от ячейки — НЕ заменяет
    другие поставки этого дня: поставок за день может быть несколько)."""
    op = Operation(
        kind=KIND_INCOME,
        counterparty_id=counterparty_id,
        date=delivery_date_iso,
        amount=f"{amount.quantize(Decimal('0.01')):.2f}",
        due_date=due_date_iso,
    )
    data.operations.append(op)
    return op


def add_realization(
    data: AppData,
    counterparty_id: str,
    realization_date_iso: str,
    amount: Decimal,
) -> Operation:
    """Добавить реализацию покупателя из окна «Реализация».

    Как и поставка, отдельная реализация не заменяет другие операции этого
    дня. Срока оплаты у реализации нет.
    """
    op = Operation(
        kind=KIND_SALE,
        counterparty_id=counterparty_id,
        date=realization_date_iso,
        amount=f"{amount.quantize(Decimal('0.01')):.2f}",
        due_date="",
    )
    data.operations.append(op)
    return op


def add_customer_receipt(
    data: AppData,
    counterparty_id: str,
    receipt_date_iso: str,
    amount: Decimal,
) -> Operation:
    """Добавить поступление от покупателя на выбранную дату.

    В существующих данных это операция «Оплата» покупателя — ровно то,
    что показывает колонка «Оплата» недельной таблицы покупателей. В
    отличие от ввода итога дня в ячейку, каждое сохранение из окна
    покупателей — отдельная операция, поэтому несколько сумм одного дня
    складываются, а не заменяют друг друга.
    """
    op = Operation(
        kind=KIND_PAYMENT,
        counterparty_id=counterparty_id,
        date=receipt_date_iso,
        amount=f"{amount.quantize(Decimal('0.01')):.2f}",
        comment="Поступление от покупателя",
        due_date="",
    )
    data.operations.append(op)
    return op


def add_supplier_payment(
    data: AppData,
    counterparty_id: str,
    payment_date_iso: str,
    amount: Decimal,
) -> Operation:
    """Добавить оплату поставщику на выбранную дату (блок «Оплата»).

    Тип тот же «Оплата», что и при вводе в ячейку недельной таблицы
    поставщиков, — новые формулы не вводятся (см. ADR-016).
    """
    op = Operation(
        kind=KIND_PAYMENT,
        counterparty_id=counterparty_id,
        date=payment_date_iso,
        amount=f"{amount.quantize(Decimal('0.01')):.2f}",
        comment="Оплата поставщику",
        due_date="",
    )
    data.operations.append(op)
    return op


def add_cash_receipt(
    data: AppData,
    source: str,
    receipt_date_iso: str,
    amount: Decimal,
    comment: str = "",
) -> CashReceipt:
    """Добавить «Поступление наличными» / «Поступление по картам» за дату."""

    receipt = CashReceipt(
        date=receipt_date_iso,
        source=source,
        amount=f"{amount.quantize(Decimal('0.01')):.2f}",
        comment=comment,
    )
    data.cash_receipts.append(receipt)
    return receipt


def set_daily_cash_receipt(
    data: AppData,
    source: str,
    date_iso: str,
    amount: Decimal | None,
) -> None:
    """Задать итог дня для «Поступление наличными»/«Поступление по картам».

    Сумма вводится прямо в ячейку таблицы (v0.10.0), поэтому день хранит
    **одну** запись на каждый источник: при повторном вводе она
    обновляется на месте. ``None`` или 0 — убрать запись дня.
    """

    def matches(item: CashReceipt) -> bool:
        return item.date == date_iso and item.source == source

    if amount is None or amount == 0:
        data.cash_receipts = [r for r in data.cash_receipts if not matches(r)]
        return

    amount_text = f"{amount.quantize(Decimal('0.01')):.2f}"
    existing = [r for r in data.cash_receipts if matches(r)]
    if existing:
        existing[0].amount = amount_text
        # Лишние записи того же дня и источника сводим в одну.
        for extra in existing[1:]:
            data.cash_receipts.remove(extra)
        return
    data.cash_receipts.append(
        CashReceipt(date=date_iso, source=source, amount=amount_text)
    )


def other_payment_names(
    data: AppData, direction: str = OUTGOING
) -> list[str]:
    """Названия прочих строк в порядке появления (без пустых).

    ``direction`` = ``INCOMING`` — блок «Поступление», ``OUTGOING`` —
    блок «Оплата».
    """

    names: list[str] = []
    for payment in data.other_payments:
        if getattr(payment, "direction", OUTGOING) != direction:
            continue
        if payment.name and payment.name not in names:
            names.append(payment.name)
    return names


def add_other_payment(
    data: AppData, name: str, date_iso: str, direction: str = OUTGOING
) -> OtherPayment:
    """Создать строку вида оплаты (сумма 0 — строка только появилась)."""

    payment = OtherPayment(
        date=date_iso, name=name, amount="0", direction=direction
    )
    data.other_payments.append(payment)
    return payment


def set_daily_other_payment(
    data: AppData,
    name: str,
    date_iso: str,
    amount: Decimal | None,
    direction: str = OUTGOING,
) -> None:
    """Задать сумму вида оплаты за день (ввод прямо в ячейку)."""

    def matches(item: OtherPayment) -> bool:
        return (
            item.name == name
            and item.date == date_iso
            and getattr(item, "direction", OUTGOING) == direction
        )

    if amount is None or amount == 0:
        data.other_payments = [
            item for item in data.other_payments if not matches(item)
        ]
        # Строку вида оплаты не теряем: остаётся запись с нулевой суммой.
        if name and not any(
            item.name == name
            and getattr(item, "direction", OUTGOING) == direction
            for item in data.other_payments
        ):
            data.other_payments.append(
                OtherPayment(
                    date=date_iso, name=name, amount="0", direction=direction
                )
            )
        return

    amount_text = f"{amount.quantize(Decimal('0.01')):.2f}"
    existing = [item for item in data.other_payments if matches(item)]
    if existing:
        existing[0].amount = amount_text
        for extra in existing[1:]:
            data.other_payments.remove(extra)
        return
    data.other_payments.append(
        OtherPayment(
            date=date_iso, name=name, amount=amount_text, direction=direction
        )
    )


def rename_other_payment(
    data: AppData,
    old_name: str,
    new_name: str,
    direction: str = OUTGOING,
) -> None:
    """Переименовать вид оплаты во всех его записях блока."""

    cleaned = (new_name or "").strip()
    if not cleaned or cleaned == old_name:
        return
    for item in data.other_payments:
        if (
            item.name == old_name
            and getattr(item, "direction", OUTGOING) == direction
        ):
            item.name = cleaned


def delete_other_payment(
    data: AppData, name: str, direction: str = OUTGOING
) -> None:
    """Убрать вид оплаты вместе со всеми его суммами."""

    data.other_payments = [
        item
        for item in data.other_payments
        if not (
            item.name == name
            and getattr(item, "direction", OUTGOING) == direction
        )
    ]
