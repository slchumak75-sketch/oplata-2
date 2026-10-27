"""Модели данных оболочки (без Qt, без формул).

Поля взяты строго из макетов (см. ``docs/SPEC.md``):

* Карточка поставщика: наименование*, отсрочка платежа (дней),
  режим оплаты (Предоплата / Реализация), «В архиве».
* Карточка покупателя: макета не было — сделана симметрично поставщику
  (ЗАФИКСИРОВАННОЕ ПРЕДПОЛОЖЕНИЕ, см. SPEC.md §8, подтвердить у пользователя).
* Операция журнала: тип, поставщик, дата, сумма, документ, комментарий.

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
    """Строка «Журнала операций».

    Типы пока: «Приход» (поставка) и «Оплата». У поставки ``date`` — дата
    поставки, а ``due_date`` — срок оплаты, рассчитанный при вводе
    (дата поставки + отсрочка, см. calc.payment_due_date); пустой
    ``due_date`` у старых записей пересчитывается на лету.
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


@dataclass
class AppData:
    """Весь сохраняемый набор данных (один JSON-файл, см. storage.py)."""

    suppliers: list[Counterparty] = field(default_factory=list)
    customers: list[Counterparty] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
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
            "day_overrides": dict(self.day_overrides),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppData":
        return cls(
            suppliers=[Counterparty.from_dict(x) for x in data.get("suppliers", [])],
            customers=[Counterparty.from_dict(x) for x in data.get("customers", [])],
            operations=[Operation.from_dict(x) for x in data.get("operations", [])],
            day_overrides={str(k): str(v) for k, v in data.get("day_overrides", {}).items()},
        )


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
