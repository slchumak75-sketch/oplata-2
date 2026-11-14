"""Разнесение выписки по базе программы (v0.56.0, второй этап).

Первый этап (v0.54.0) только показывал выписку таблицей. Здесь строка
выписки превращается в запись базы:

* **поступление**
  * «Перечисление денежных средств по операциям с платежными
    карточками» → строка «Поступление по картам»;
  * «ВЫРУЧКА ОТ РЕАЛИЗАЦИИ» → строка «Поступление наличными»;
  * УНП совпал с покупателем → строка «Покупатели»: деньги покупателя
    — это **оплата** его долга (v0.58.0);
  * иначе → «Прочие поступления»;
* **оплата**
  * УНП совпал с поставщиком → строка «Поставщики»;
  * иначе ищем вид оплаты с таким УНП (в том числе для самого банка:
    комиссия и вознаграждение — тоже оплата, v0.58.0);
  * не нашёлся — строка ждёт карточку нового вида оплаты.

Правило пользователя: **поиск всегда в первую очередь по УНП**, дата
записи — **фактическая дата проведения операции**.

Поставщиков и покупателей в базе наполняют постепенно, поэтому
``classify`` ничего не запоминает: при повторном открытии выписки
строки, для которых контрагент появился, определятся сами.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation

from .calc import OTHER_INCOME_TITLE
from .dates import fmt_money_plain as fmt_amount
from .models import (
    CARD_SOURCE,
    CASH_SOURCE,
    INCOMING,
    KIND_PAYMENT,
    KIND_SALE,
    OUTGOING,
    AppData,
    Counterparty,
    cash_receipt_amount,
    operation_amount,
    daily_operation_total,
    set_daily_cash_receipt,
    set_daily_operation,
    set_daily_other_payment,
)
from .statement import Statement, StatementRow, stored_row_key

# Куда попадает строка.
TARGET_CARD = "card"                  # «Поступление по картам»
TARGET_CASH = "cash"                  # «Поступление наличными»
TARGET_CUSTOMERS = "customers"        # строка «Покупатели»
TARGET_SUPPLIERS = "suppliers"        # строка «Поставщики»
TARGET_OTHER_INCOME = "other_income"  # «Прочие поступления»
TARGET_OTHER_PAYMENT = "other_payment"  # свой вид оплаты (по УНП)
TARGET_NEW_PAYMENT = "new_payment"    # нужна карточка вида оплаты
# v0.61.0: УНП строки подходит несколько карточек — куда занести,
# решает пользователь (вариант А: пока не выбран получатель, строка
# в разнос не идёт). Выбор показывается в списке «Куда» подписью
# «Наименование · УНП · Примечание».
TARGET_CHOOSE = "choose"

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
    # v0.61.0: кандидаты, если УНП подходит несколько карточек.
    # Элемент — кортеж (target, counterparty_id, payment_name, подпись).
    options: list = field(default_factory=list)


@dataclass
class Distribution:
    """Итог разнесения: что записали, что пропустили, что ждёт карточек."""

    applied: list[str] = field(default_factory=list)   # строки отчёта
    already: list[str] = field(default_factory=list)   # уже разносили раньше
    # v0.59.0: что сняли при переразносе.
    replaced: list[str] = field(default_factory=list)
    # v0.67.0: снятые записи, у которых сумму дня правили вручную.
    # Переразнос пишет поверх такой правки, но говорит об этом: иначе
    # человек не узнал бы, что его цифры затёрты.
    overwritten: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)   # снятые «реализации»
    warnings: list[str] = field(default_factory=list)  # на что посмотреть
    unresolved: list[StatementRow] = field(default_factory=list)
    # v0.65.0: строки, которым не хватает не карточки, а **выбора**:
    # УНП подходит несколько карточек, и получателя выбирает
    # пользователь (правило v0.61.0).
    needs_choice: list[StatementRow] = field(default_factory=list)
    # Строки, у которых прежнюю запись сняли, а новой пока нет: сумма
    # не должна пропасть незаметно.
    rolled_unwritten: list[StatementRow] = field(default_factory=list)
    # Строки, которые разносились раньше **с другой суммой** (файл
    # прочитан по-новому): прежние записи сняты, записаны новые.
    restated: list[StatementRow] = field(default_factory=list)
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


def find_all_by_unp(items, unp: str) -> list:
    """Все контрагенты с таким УНП (v0.61.0).

    УНП может повторяться: одна организация бывает и поставщиком, и
    покупателем, а у одной организации бывает несколько карточек с
    разными примечаниями. Сначала действующие, потом архивные — внутри
    каждой группы сохраняется порядок карточек.
    """

    unp = (unp or "").strip()
    if not unp:
        return []
    active = [item for item in items if item.unp == unp and not item.archived]
    if active:
        return active
    return [item for item in items if item.unp == unp]


def find_by_unp(items, unp: str):
    """Контрагент по УНП: сначала действующий, потом архивный."""

    found = find_all_by_unp(items, unp)
    return found[0] if found else None


def find_all_payments_by_unp(data: AppData, unp: str) -> list:
    """Все виды оплаты с таким УНП (v0.61.0).

    У одного плательщика бывает несколько назначений платежей — их
    различают примечанием, поэтому список, а не первый попавшийся.
    """

    unp = (unp or "").strip()
    if not unp:
        return []
    return [
        payment
        for payment in data.other_payments
        if payment.unp == unp and payment.direction == OUTGOING
    ]


def find_payment_by_unp(data: AppData, unp: str):
    """Вид оплаты (блока «Оплата») по УНП — из карточки вида (v0.55.0)."""

    found = find_all_payments_by_unp(data, unp)
    return found[0] if found else None


def candidate_label(item) -> str:
    """Подпись кандидата: «Наименование · УНП · Примечание» (v0.61.0).

    УНП у карточек повторяется, поэтому в списке выбора показываем и
    наименование, и УНП, и примечание — по нему карточки различают.
    """

    parts = [str(getattr(item, "name", "") or "").strip()]
    unp = str(getattr(item, "unp", "") or "").strip()
    if unp:
        parts.append(f"УНП {unp}")
    note = str(getattr(item, "note", "") or "").strip()
    if note:
        parts.append(note)
    return " · ".join(part for part in parts if part)


# Обороты, с которых в назначении платежа начинаются реквизиты, а не
# смысл платежа (v0.63.0).
PURPOSE_NOTE_STOPS = (
    " за ",
    " по сроку",
    " согласно",
    " согл ",
    " от ",
    " на основании",
    " в сумме",
    " в т.ч",
    " в том числе",
    " унп",
    " ндс",
    " (",
)


def purpose_note(purpose: str, limit: int = 60) -> str:
    """Примечание вида оплаты из назначения платежа (v0.63.0).

    У одной организации (ГУ МФ по Гродненской области) бывает несколько
    назначений платежей: подоходный налог, страховые взносы. Виды
    различают примечанием, а писать его руками каждый раз — лишняя
    работа, поэтому берём смысл из назначения платежа: «ПОДОХОДНЫЙ
    НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ ЗА ПЕРВУЮ ПОЛОВИНУ ОКТЯБРЯ 2026Г. …»
    → «ПОДОХОДНЫЙ НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ».
    """

    text = " ".join(str(purpose or "").split())
    if not text:
        return ""
    # Хвост «ЗА …», «ПО СРОКУ ПЛАТЕЖА …», «УНП ФБ: …» — это не смысл
    # платежа, а его реквизиты: обрезаем по первому такому обороту.
    lowered = text.lower()
    cut = len(text)
    for stop in PURPOSE_NOTE_STOPS:
        index = lowered.find(stop)
        if index > 0:
            cut = min(cut, index)
    text = text[:cut].strip(" ,;.")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip(" ,;.")
    return text


def free_payment_name(used_names, base: str) -> str:
    """Имя вида оплаты, которого ещё нет в блоке (v0.63.0).

    УНП у видов повторяется (одна организация — несколько платежей), а
    вот **название** в блоке должно быть одно: по нему вид узнаётся в
    таблице и в базе. Поэтому второму виду той же организации
    предлагаем имя с номером: «ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)».
    """

    used = {str(name or "").strip().lower() for name in used_names}
    name = " ".join(str(base or "").split())
    if not name:
        return ""
    if name.lower() not in used:
        return name
    number = 2
    while f"{name} ({number})".lower() in used:
        number += 1
    return f"{name} ({number})"


def choose_option_data(
    target: str, counterparty_id: str, payment_name: str
) -> str:
    """Метка пункта списка «Куда» для выбранного кандидата (v0.61.0).

    В данных пункта лежит ``choose:<назначение>:<id>:<вид оплаты>`` —
    по ней окно разноса понимает, что выбран один из кандидатов, а не
    общее назначение из ``TARGET_CHOICES``.
    """

    return f"choose:{target}:{counterparty_id}:{payment_name}"


def _choose_match(row: StatementRow, target: str, items: list) -> Match:
    """Строка, которой подходит несколько карточек (v0.61.0)."""

    unp = row.unp or "—"
    return Match(
        TARGET_CHOOSE,
        f"Уточните: УНП {unp} подходит {len(items)} "
        f"{_cards_word(len(items))} — выберите в колонке «Куда»",
        options=[
            (target, getattr(item, "id", ""), "", candidate_label(item))
            for item in items
        ],
    )


def _cards_word(count: int) -> str:
    """«карточка» / «карточки» / «карточек» по числу."""

    if count % 10 == 1 and count % 100 != 11:
        return "карточка"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "карточки"
    return "карточек"


def classify(row: StatementRow, data: AppData, bank_unp: str = "") -> Match:
    """Определить, куда попадёт строка выписки.

    ``bank_unp`` — УНП обслуживающего банка из «Конфигурации»: его
    операции не поставщик и не покупатель, поэтому строку банка не
    ищем среди контрагентов, а только среди видов оплаты (v0.58.0).
    Строки эквайринга и выручки приходят с УНП банка, но это деньги
    магазина — их разносим по назначению платежа.
    """

    if row.is_incoming:
        if row.is_cards:
            return Match(TARGET_CARD, "Поступление по картам")
        if row.is_cash:
            return Match(TARGET_CASH, "Поступление наличными")
        customers = find_all_by_unp(data.customers, row.unp)
        if len(customers) > 1:
            # v0.61.0: УНП подходит несколько покупателей — угадывать
            # не надо, получателя выбирает пользователь.
            return _choose_match(row, TARGET_CUSTOMERS, customers)
        if customers:
            return Match(
                TARGET_CUSTOMERS,
                f"Покупатель: {customers[0].name}",
                counterparty_id=customers[0].id,
            )
        return Match(
            TARGET_OTHER_INCOME,
            f"Прочие поступления (УНП {row.unp} не найден)"
            if row.unp
            else "Прочие поступления (без УНП)",
        )
    suppliers = find_all_by_unp(data.suppliers, row.unp)
    if len(suppliers) > 1:
        return _choose_match(row, TARGET_SUPPLIERS, suppliers)
    if suppliers:
        return Match(
            TARGET_SUPPLIERS,
            f"Поставщик: {suppliers[0].name}",
            counterparty_id=suppliers[0].id,
        )
    payments = find_all_payments_by_unp(data, row.unp)
    if len(payments) > 1:
        # Виды оплаты с одинаковым УНП различаются примечанием —
        # показываем их списком, пусть выберет пользователь.
        return Match(
            TARGET_CHOOSE,
            f"Уточните: УНП {row.unp} подходит "
            f"{len(payments)} {_cards_word(len(payments))} видов оплаты "
            "— выберите в колонке «Куда»",
            options=[
                (
                    TARGET_OTHER_PAYMENT,
                    "",
                    payment.name,
                    candidate_label(payment),
                )
                for payment in payments
            ],
        )
    if payments:
        return Match(
            TARGET_OTHER_PAYMENT,
            f"Вид оплаты: {payments[0].name}",
            payment_name=payments[0].name,
        )
    # v0.58.0: строку банка (комиссия, вознаграждение) разносим как
    # любую оплату — ищем вид оплаты по УНП. Банк «не поставщик и не
    # покупатель» лишь в том смысле, что его не надо искать среди
    # контрагентов: вид оплаты для него заводится обычной карточкой.
    note = (
        f"Нужен вид оплаты для банка (УНП {row.unp})"
        if bank_unp and row.unp == bank_unp
        else (
            f"Нужен новый вид оплаты (УНП {row.unp})"
            if row.unp
            else "Нужен новый вид оплаты"
        )
    )
    return Match(TARGET_NEW_PAYMENT, note)


def classify_all(
    statement: Statement, data: AppData, bank_unp: str = ""
) -> list[Match]:
    return [classify(row, data, bank_unp) for row in statement.rows]


def iso_date(row: StatementRow) -> str:
    """«05.11.2026» → «2026-11-05» (фактическая дата операции)."""

    day, month, year = row.date.split(".")
    return f"{year}-{month}-{day}"


def classify_commission(
    row: StatementRow, data: AppData, bank_unp: str = ""
) -> Match:
    """Куда записать комиссию банка по строке (v0.58.0).

    Банк удержал комиссию из зачисляемой суммы, а платим её мы. Сумма
    комиссии показывается в «Оплате» строки, значит при разносе она
    должна лечь в оплаты: ищем вид оплаты по УНП, как для любой оплаты.
    """

    if not row.commission:
        return Match("", "")
    # Комиссию обнуляем обязательно: иначе ``incoming_shown`` снова
    # прибавит её к поступлению, и строка опять окажется приходом.
    payment_row = replace(
        row,
        incoming=Decimal("0"),
        outgoing=row.commission,
        commission=Decimal("0"),
    )
    match = classify(payment_row, data, bank_unp)
    if match.target == TARGET_CHOOSE:
        # v0.61.0: комиссию тоже некуда писать, пока не выбран
        # получатель, — но подписываем это как комиссию, а не строкой.
        match.note = "Комиссия банка: уточните получателя в «Куда»"
        return match
    if match.target == TARGET_NEW_PAYMENT:
        # v0.62.0: по одной подписи «Нужен новый вид оплаты» не понять,
        # из-за какой суммы строка стоит. Говорим прямо: сколько комиссии
        # и по какому УНП ищем вид оплаты.
        where = f"УНП {row.unp}" if row.unp else "УНП в строке не указан"
        match.note = (
            f"Комиссия {fmt_amount(row.commission)}: "
            f"нужен вид оплаты ({where})"
        )
        return match
    if match.target:
        match.note = f"Комиссия банка: {match.payment_name or match.note}"
    return match


def row_records(
    row: StatementRow, match: Match, commission: Match
) -> list[tuple[tuple, Decimal, str]]:
    """Записи, которые строка внесёт в базу: (ключ, сумма, подпись).

    Их бывает две: сама сумма строки и комиссия банка (если она есть) —
    комиссия уходит в оплаты отдельной записью (v0.58.0).
    """

    records: list[tuple[tuple, Decimal, str]] = []
    if match.target and match.target not in (
        TARGET_NEW_PAYMENT,
        TARGET_CHOOSE,
    ):
        records.append((plan_key(row, match), row.amount, match.note))
    if commission.target and commission.target != TARGET_NEW_PAYMENT:
        records.append(
            (plan_key(row, commission), row.commission, commission.note)
        )
    return records


def row_is_blocked(match: Match, commission: Match) -> bool:
    """Можно ли записать строку целиком (v0.58.0).

    Если комиссию некуда писать, строку не трогаем вовсе: иначе при
    повторном разносе (после создания вида оплаты) сумма удвоилась бы.
    """

    if not match.target or match.target == TARGET_NEW_PAYMENT:
        return True
    # v0.61.0: пока получатель не выбран, строку не пишем.
    if match.target == TARGET_CHOOSE:
        return True
    if commission.target in (TARGET_NEW_PAYMENT, TARGET_CHOOSE):
        return True
    if match.target in (TARGET_CUSTOMERS, TARGET_SUPPLIERS):
        return not match.counterparty_id
    if match.target == TARGET_OTHER_PAYMENT:
        return not match.payment_name
    return False


def plan_key(row: StatementRow, match: Match) -> tuple:
    """Ключ записи дня: назначение + получатель + дата операции."""

    return (match.target, match.counterparty_id, match.payment_name,
            iso_date(row))


def plan_totals(
    statement: Statement,
    data: AppData,
    matches: list[Match] | None = None,
    commissions: list[Match] | None = None,
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
        commission = (
            commissions[index]
            if commissions is not None and index < len(commissions)
            else classify_commission(row, data)
        )
        for key, amount, _note in row_records(row, match, commission):
            planned[key] = planned.get(key, Decimal("0")) + amount
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
        # v0.58.0: поступление от покупателя — оплата его долга.
        return daily_operation_total(
            data.operations, match.counterparty_id, date_iso, KIND_PAYMENT
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


def write_target(
    data: AppData,
    target: str,
    counterparty_id: str,
    payment_name: str,
    date_iso: str,
    amount: Decimal | None,
) -> None:
    """Записать сумму дня по назначению (``None`` — очистить запись)."""

    if target == TARGET_CARD:
        set_daily_cash_receipt(data, CARD_SOURCE, date_iso, amount)
    elif target == TARGET_CASH:
        set_daily_cash_receipt(data, CASH_SOURCE, date_iso, amount)
    elif target == TARGET_CUSTOMERS:
        # v0.58.0: деньги от покупателя — это **оплата** его долга,
        # а не новая реализация. Так же пишет и ручной ввод в строке
        # «Покупатели» блока «Поступление» (колонка «Оплата»
        # покупателя); раньше выписка создавала «Реализацию» и долг
        # покупателя только рос.
        set_daily_operation(
            data, counterparty_id, date_iso, KIND_PAYMENT, amount
        )
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


def target_title(
    target: str, counterparty_id: str, payment_name: str, data: AppData
) -> str:
    """Человеческое название назначения — для отчёта (v0.59.0)."""

    if target in (TARGET_CARD, TARGET_CASH):
        return "Поступление по картам" if target == TARGET_CARD else (
            "Поступление наличными"
        )
    if target == TARGET_OTHER_INCOME:
        return OTHER_INCOME_ROW_TITLE
    if payment_name:
        return f"Вид оплаты: {payment_name}"
    name = ""
    for person in list(data.customers) + list(data.suppliers):
        if person.id == counterparty_id:
            name = person.name
            break
    who = "Покупатель" if target == TARGET_CUSTOMERS else "Поставщик"
    return f"{who}: {name or counterparty_id}"


def rollback_write(result: Distribution, data: AppData, item: dict) -> None:
    """Снять запись, сделанную прошлым разносом (v0.59.0).

    v0.67.0: снимаем **всегда**, даже если сумму дня после разноса
    правили вручную. Прежде в этом случае запись не снималась, а новая
    сумма для того же получателя и дня и вовсе не писалась: человек
    стирал неверные суммы руками, нажимал «переразнести» — и не
    получал ничего, потому что программа принимала его же правку за
    «чужой ввод, который не смеем трогать». Теперь переразнос пишет
    поверх, но честно показывает в отчёте, что затёр.
    """

    amount = Decimal(str(item.get("amount") or "0"))
    date_iso = str(item.get("date") or "")
    target = str(item.get("target") or "")
    counterparty_id = str(item.get("counterparty_id") or "")
    payment_name = str(item.get("payment_name") or "")
    current = base_amount(
        data, Match(target, "", counterparty_id, payment_name), date_iso
    )
    title = target_title(target, counterparty_id, payment_name, data)
    if current != amount:
        result.overwritten.append(
            f"{title}: {date_iso} — день правили вручную, было "
            f"{fmt_amount(current)} (разнос писал {fmt_amount(amount)}); "
            "перезаписано нынешней суммой"
        )
    write_target(data, target, counterparty_id, payment_name, date_iso, None)
    result.replaced.append(
        f"{title}: {date_iso} — {fmt_amount(amount)} (снято)"
    )


def find_stale_realizations(
    data: AppData,
    statement: Statement,
    matches: list[Match] | None = None,
) -> list:
    """Найти «реализации», которые раньше создавал разнос (v0.59.0).

    Версии 0.56.0–0.57.0 писали поступление от покупателя операцией
    «Реализация». Теперь та же строка пишется оплатой, а старая
    реализация осталась в базе. Узнаём её по совпадению даты,
    покупателя и суммы.
    """

    found = []
    for index, row in enumerate(statement.rows):
        match = (
            matches[index]
            if matches is not None and index < len(matches)
            else classify(row, data)
        )
        if match.target != TARGET_CUSTOMERS or not match.counterparty_id:
            continue
        date_iso = iso_date(row)
        for operation in data.operations:
            if (
                operation.kind == KIND_SALE
                and operation.counterparty_id == match.counterparty_id
                and operation.date == date_iso
                and operation_amount(operation) == row.amount
                and operation not in found
            ):
                found.append(operation)
    return found


def remove_operations(data: AppData, operations: list) -> None:
    """Убрать операции из базы (подтверждение спрашивает вызывающий)."""

    for operation in operations:
        if operation in data.operations:
            data.operations.remove(operation)


def distribute(
    statement: Statement,
    data: AppData,
    matches: list[Match] | None = None,
    commissions: list[Match] | None = None,
    redo: bool = False,
) -> Distribution:
    """Разнести выписку по базе.

    ``matches`` — назначение строк, поправленное пользователем в окне
    «Разнести»; ``commissions`` — назначение комиссий банка. Без них
    назначение считается заново.

    Суммы одного дня и одного получателя складываем, а не пишем подряд:
    запись дня в базе одна (``set_daily_*``), иначе вторая строка затёрла
    бы первую.
    """

    result = Distribution()
    planned: dict[tuple, Decimal] = {}
    labels: dict[tuple, str] = {}
    # v0.59.0: реестр разнесённых строк — с подробностями, чтобы
    # переразнос мог снять именно то, что записал прошлый раз.
    previous_writes: list[dict] = list(
        getattr(data, "imported_statement_writes", [])
    )
    entries_by_key: dict[str, list[dict]] = {}
    # v0.65.0: тот же реестр, но по «мягкому» ключу строки (дата, УНП,
    # направление **без суммы**). Нужен, когда сумма строки прочиталась
    # иначе, чем при прошлом разносе: например, код платежа в бюджет
    # (v0.64.0) перестал попадать в сумму, и 101 300,00 стала 300,00.
    entries_by_row_key: dict[str, list[dict]] = {}
    rows_by_row_key: dict[str, list] = {}
    for item in previous_writes:
        for key in item.get("keys") or [item.get("key")]:
            entries_by_key.setdefault(str(key), []).append(item)
            entries_by_row_key.setdefault(stored_row_key(key), []).append(item)
    for row in statement.rows:
        rows_by_row_key.setdefault(row.row_key(), []).append(row)

    def stored_row_count(row_key: str) -> int:
        """Сколько строк файла с таким мягким ключом разносили раньше."""

        keys = set()
        for item in entries_by_row_key.get(row_key, ()):
            for saved in item.get("keys") or [item.get("key")]:
                keys.add(str(saved))
        return len(keys)
    seen: set[str] = set(getattr(data, "imported_statement_rows", [])) | set(
        entries_by_key
    )
    new_writes: list[dict] = [item for item in previous_writes]
    redone: list[tuple[str, str]] = []  # (ключ строки, мягкий ключ)
    rolled_back: list[dict] = []
    planned_keys: dict[tuple, list[str]] = {}

    for index, row in enumerate(statement.rows):
        match = (
            matches[index]
            if matches is not None and index < len(matches)
            else classify(row, data)
        )
        commission = (
            commissions[index]
            if commissions is not None and index < len(commissions)
            else classify_commission(row, data)
        )
        result.rows.append((row, match))
        key = row.import_key()
        row_key = row.row_key()
        known = key in seen
        # v0.65.0: строка уже разносилась, но **с другой суммой** —
        # например, после исправления разбора файла. Снимать прежнюю
        # запись можно, только когда строк файла с этим ключом столько
        # же, сколько разнесённых строк: иначе сняли бы чужую строку,
        # которой в этом файле нет (одному УНП в один день бывает
        # несколько платежей).
        restated = (
            not known
            and row_key in entries_by_row_key
            and len(rows_by_row_key.get(row_key, ()))
            == stored_row_count(row_key)
        )
        if known and not redo:
            result.already.append(
                f"{row.date} · {row.name or row.unp} · {row.amount}"
            )
            continue
        if known or restated:
            if restated:
                result.restated.append(row)
            # Переразнос: снимаем то, что записали в прошлый раз.
            # Снимаем и для строк, которые сейчас записать нельзя
            # (ждут выбора получателя): прежняя сумма всё равно уже
            # неверна, и оставлять её в базе нельзя.
            redone.append((key, row_key))
            took = False
            for item in entries_by_key.get(key, []) or entries_by_row_key.get(
                row_key, []
            ):
                if item in new_writes:
                    new_writes.remove(item)
                took = True  # запись этой строки снимаем (снята ранее — тоже)
                if item in rolled_back:
                    continue
                rolled_back.append(item)
                rollback_write(result, data, item)
            if row_is_blocked(match, commission):
                # Прежнюю запись сняли, а новой пока нет — об этом
                # надо сказать: иначе сумма пропадёт незаметно.
                if match.target == TARGET_CHOOSE:
                    result.needs_choice.append(row)
                else:
                    result.unresolved.append(row)
                if took:
                    result.rolled_unwritten.append(row)
                continue
        if row_is_blocked(match, commission):
            if match.target == TARGET_CHOOSE:
                result.needs_choice.append(row)
            else:
                result.unresolved.append(row)
            continue
        for target_key, amount, note in row_records(row, match, commission):
            planned[target_key] = (
                planned.get(target_key, Decimal("0")) + amount
            )
            planned_keys.setdefault(target_key, []).append(key)
            labels[target_key] = note
        seen.add(key)

    for (target, counterparty_id, payment_name, date_iso), amount in sorted(
        planned.items()
    ):
        write_target(
            data, target, counterparty_id, payment_name, date_iso, amount
        )
        result.applied.append(
            f"{labels[(target, counterparty_id, payment_name, date_iso)]}: "
            f"{date_iso} — {fmt_amount(amount)}"
        )
        # В реестр пишем запись дня целиком: суммы одного дня и одного
        # назначения складываются, поэтому и снимать их надо вместе.
        new_writes.append(
            {
                "keys": list(planned_keys[
                    (target, counterparty_id, payment_name, date_iso)
                ]),
                "target": target,
                "counterparty_id": counterparty_id,
                "payment_name": payment_name,
                "date": date_iso,
                "amount": str(amount),
            }
        )

    if redo:
        # Файл разносили версией до 0.59.0: подробного реестра нет,
        # поэтому снять прежние записи мы не можем — новые суммы легли
        # поверх. Предупреждаем, чтобы день не оказался удвоен.
        blind = [
            key
            for key, soft in redone
            if key not in entries_by_key and soft not in entries_by_row_key
        ]
        if blind:
            result.warnings.append(
                "Снимать прежние записи нечего: эти строки разнесены "
                "более старой версией, подробного реестра у неё не было "
                f"(строк: {len(blind)}). Новые суммы записаны поверх "
                "прежних — проверьте дни выписки."
            )
    data.imported_statement_rows = sorted(seen)
    data.imported_statement_writes = new_writes
    return result
