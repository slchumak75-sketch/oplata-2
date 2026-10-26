"""Модели данных оболочки (без Qt, без формул).

Поля взяты строго из макетов (см. ``docs/SPEC.md``):

* Карточка поставщика: наименование*, отсрочка платежа (дней),
  режим оплаты (Предоплата / Реализация), «В архиве».
* Карточка покупателя: макета не было — сделана симметрично поставщику
  (ЗАФИКСИРОВАННОЕ ПРЕДПОЛОЖЕНИЕ, см. SPEC.md §7, подтвердить у пользователя).
* Операция журнала: тип, поставщик, дата, сумма, документ, комментарий.

Никаких вычисляемых полей (долг, просрочка) здесь нет и быть не должно —
они появятся в ``calc.py`` на этапе формул.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field


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
    """Строка «Журнала операций». На этапе оболочки журнал пуст.

    TODO(этап формул/операций): уточнить у пользователя список типов
    операций (приход/оплата/…) — на макете виден лишь фильтр «Все операции».
    """

    kind: str = ""
    counterparty_id: str = ""
    date: str = ""          # ISO ``YYYY-MM-DD`` строкой, чтобы JSON был простым
    amount: str = "0"       # Decimal строкой (без float!) — расчётов пока нет
    document: str = ""
    comment: str = ""
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
