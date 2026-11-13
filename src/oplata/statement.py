"""Разбор выписки банка из PDF (v0.54.0).

Первый этап загрузки выписки: прочитать файл и показать операции
таблицей — **без** разнесения их по «Поступлению и оплате» (это второй
этап). Модуль ничего не знает про базу программы: он только превращает
файл в список строк.

Формат — выписка «Клиент-банк» ОАО «АСБ Беларусбанк». Одна операция в
файле печатается блоком:

    03.11.2026 839 1 Счет: BY96 BPSB 3012 3167 4701 5933 0000
    БИК: BPSBBY2X УНП: 100005713
    5 549,97 0,00                       <- Дебет (оплата) и Кредит (поступление)
    ООО "ОМА"                           <- наименование контрагента
    Назначение платежа: ЗА СТРОЙМАТЕРИАЛЫ …   (бывает в несколько строк)
    Фактическая дата и время совершения операции: 05.11.2026 11:44:06

Правила чтения (пояснение пользователя):

* «Входящее сальдо» — остаток на счёте на начало дня;
* **Дебет — это оплата** (деньги ушли);
* **Кредит — это поступление** (деньги пришли);
* «Исходящее сальдо» — остаток после всех операций дня;
* дата строки — из «Фактическая дата и время совершения операции».

Разбор идёт по этим фразам, а не по номерам колонок: в выписке разная
ширина строк, и «Назначение платежа» переносится.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

DATE = r"\d{2}\.\d{2}\.\d{4}"
AMOUNT = r"\d[\d ]*,\d{2}"

RE_UNP = re.compile(r"УНП:\s*(\d+)")
RE_FACT_DATE = re.compile(
    r"Фактическая дата и время совершения операции:\s*(" + DATE + r")"
)
RE_DOC = re.compile(rf"^({DATE})\s+(\S+)\s+(\d+)\s+Счет:")
RE_AMOUNTS = re.compile(rf"^({AMOUNT})\s+({AMOUNT})$")
# v0.64.0: у платежей в бюджет перед суммами стоит **код платежа**
# (в шапке файла есть колонка «Код пл. в бюдж»): «00101 300,00 0,00»
# — это код 00101, оплата 300,00 и поступление 0,00. Раньше код
# принимался за старшие разряды суммы, и вместо 300,00 в базу уходило
# 101 300,00. Код — слитные цифры (без пробела) не меньше четырёх
# подряд: у суммы разряды разделены пробелами группами по три, поэтому
# первая группа суммы короче («2 300,00» → «2»).
RE_AMOUNTS_BUDGET = re.compile(
    rf"^\s*\d{{4,}}\s+({AMOUNT})\s+({AMOUNT})\s*$"
)
RE_PURPOSE = re.compile(r"^Назначение платежа:")
# v0.55.0: «Комиссия банка - 18.02» в назначении платежа. Банк держит
# комиссию за зачисление выручки: на счёт приходит сумма за вычетом
# комиссии, но платим её мы. Поэтому комиссия показывается и в оплате,
# и добавленной к поступлению (пояснение пользователя).
# Сумму комиссии банк печатает по-разному: в образце это «- 18.02»
# (с точкой), тогда как все остальные суммы — через запятую. Поэтому
# принимаем оба разделителя.
RE_COMMISSION = re.compile(
    r"Комиссия\s+банка\s*[-–—:]\s*(\d[\d ]*[.,]\d{2})", re.IGNORECASE
)
RE_OPENING = re.compile(rf"Входящее сальдо:\s*({AMOUNT})")
RE_CLOSING = re.compile(rf"Исходящее сальдо:\s*({AMOUNT})")
RE_TURNOVER = re.compile(rf"^ОБОРОТЫ\s+({AMOUNT})\s+({AMOUNT})$")
RE_ACCOUNT = re.compile(r"Счет клиента:\s*([A-Z0-9 ]+)")
# v0.56.0: по имени счёта узнаём, чья это выписка — МИР или КОРЕЛИЧИ
# («Наименование счета: ООО МирСтройМаркет» / «ООО Мирсититорг»).
RE_ACCOUNT_NAME = re.compile(r"Наименование\s+счета:\s*(.+)")
# Признаки строк «Поступление»: поступление по картам и выручка
# (наличными). Слова — из назначения платежа (пояснение пользователя).
CARD_TRANSFER_PHRASE = (
    "перечисление денежных средств по операциям с платежными карточками"
)
CASH_PROCEEDS_PHRASE = "выручка от реализации"
RE_STATEMENT_DATE = re.compile(r"ВЫПИСКА ИЗ ЛИЦЕВОГО СЧЕТА ЗА\s*(" + DATE + r")")

MISSING_PYPDF_TEXT = "Не найден пакет pypdf — загрузка выписки недоступна."

# Без fontTools pypdf на каждой странице предупреждает про шрифты, хотя
# текст читает правильно. Пользователю это ни к чему — гасим.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class StatementError(Exception):
    """Файл не читается или не похож на выписку."""


@dataclass
class StatementRow:
    """Одна операция выписки.

    Суммы — ``Decimal``: деньги считаем, а не показываем. ``incoming``
    — поступление (кредит), ``outgoing`` — оплата (дебет). Одна из сумм
    всегда нулевая: операция либо приход, либо расход.
    """

    date: str  # ДД.ММ.ГГГГ — как в файле
    unp: str
    name: str
    incoming: Decimal = Decimal("0")
    outgoing: Decimal = Decimal("0")
    purpose: str = ""  # назначение платежа: пригодится при разнесении
    # v0.55.0: комиссия банка из назначения платежа (0 — её нет).
    commission: Decimal = Decimal("0")

    @property
    def is_incoming(self) -> bool:
        """Строка — поступление (деньги пришли)."""

        return self.incoming_shown > 0

    @property
    def is_cards(self) -> bool:
        """Поступление по платежным карточкам (эквайринг)."""

        return CARD_TRANSFER_PHRASE in self.purpose.lower()

    @property
    def is_cash(self) -> bool:
        """Выручка от реализации — наличными."""

        return CASH_PROCEEDS_PHRASE in self.purpose.lower()

    @property
    def incoming_shown(self) -> Decimal:
        """Поступление к показу: сумма **плюс** комиссия банка.

        Комиссию банк удержал из суммы, поэтому на счёт пришло меньше,
        чем платил покупатель: «1 203,75 + 18,02 = 1 221,77».
        """
        return self.incoming + self.commission

    @property
    def outgoing_shown(self) -> Decimal:
        """Оплата к показу: сумма **плюс** комиссия банка (её платим мы)."""
        return self.outgoing + self.commission

    @property
    def amount(self) -> Decimal:
        """Сумма строки: поступление или оплата."""

        return self.incoming_shown if self.is_incoming else self.outgoing_shown

    def import_key(self) -> str:
        """Ключ строки для защиты от повторного разноса (v0.56.0).

        Одна и та же выписка может быть загружена两次: ключ по дате,
        УНП, сумме и направлению не даст записать операцию дважды.
        """

        side = "in" if self.is_incoming else "out"
        return f"{self.date}|{self.unp}|{self.amount}|{side}"


@dataclass
class Statement:
    """Выписка целиком: шапка и операции."""

    statement_date: str = ""
    account: str = ""
    # v0.56.0: «Наименование счета» — по нему определяем базу
    # (ООО МирСтройМаркет — МИР, ООО Мирсититорг — КОРЕЛИЧИ).
    account_name: str = ""
    opening_balance: Decimal = Decimal("0")
    closing_balance: Decimal = Decimal("0")
    turnover_outgoing: Decimal = Decimal("0")  # обороты по дебету
    turnover_incoming: Decimal = Decimal("0")  # обороты по кредиту
    rows: list[StatementRow] = field(default_factory=list)

    @property
    def total_incoming(self) -> Decimal:
        return sum((row.incoming_shown for row in self.rows), Decimal("0"))

    @property
    def total_outgoing(self) -> Decimal:
        return sum((row.outgoing_shown for row in self.rows), Decimal("0"))

    @property
    def total_commission(self) -> Decimal:
        """Сумма комиссий банка по всем строкам (v0.55.0)."""

        return sum((row.commission for row in self.rows), Decimal("0"))


def parse_amount(text: str) -> Decimal:
    """``"5 549,97"`` -> ``Decimal("5549.97")``."""

    try:
        return Decimal(text.replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise StatementError(f"Непонятная сумма: {text!r}") from exc


def _require_pypdf():
    try:
        import pypdf  # noqa: PLC0415 - импорт нужен для PyInstaller

        return pypdf
    except ImportError as exc:  # pragma: no cover - в CI пакет установлен
        raise StatementError(MISSING_PYPDF_TEXT) from exc


def extract_text(path: Path | str) -> str:
    """Текст всех страниц PDF (выписка бывает многостраничной)."""

    pypdf = _require_pypdf()
    path = Path(path)
    try:
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except StatementError:
        raise
    except Exception as exc:  # pypdf кидает разные исключения
        raise StatementError(f"Не удалось прочитать PDF: {exc}") from exc
    text = "\n".join(pages)
    if not text.strip():
        raise StatementError(
            "В файле нет текста. Выписка должна быть настоящим PDF, "
            "а не отсканированной картинкой."
        )
    return text


def parse_statement(path: Path | str) -> Statement:
    """Прочитать выписку из PDF.

    Ничего не меняет в базе программы — только разбирает файл.
    """

    return parse_statement_text(extract_text(path))


# Заголовки, которые pypdf может разорвать переносом строки: версия
# библиотеки влияет на разбивку текста (в 5.9 «Назначение» и «платежа: …»
# печатаются на разных строках), а по назначению платежа определяются
# выручка наличными и эквайринг (v0.56.0).
SPLIT_HEADERS = (
    "Назначение платежа:",
    "Фактическая дата и время совершения операции:",
    "Наименование счета:",
    "Входящее сальдо:",
    "Исходящее сальдо:",
    "Кол-во операций",
)

# v0.62.0: «УНП» в конце строки, цифры — в начале следующей (после «УНП»
# двоеточие может стоять, а может быть потеряно переносом).
RE_SPLIT_UNP = re.compile(
    r"УНП[^\S\n]*:?[^\S\n]*\n[^\S\n]*:?[^\S\n]*(\d{6,})\b"
)


def join_split_headers(text: str) -> str:
    """Склеить заголовок, разорванный переносом строки (v0.56.0)."""

    for header in SPLIT_HEADERS:
        words = header.split()
        pattern = r"\s+".join(re.escape(word) for word in words[:-1])
        pattern += r"\s*" + re.escape(words[-1])
        text = re.sub(pattern, header, text)
    return text


def join_split_unp(text: str) -> str:
    """Склеить УНП, разорванный переносом строки (v0.62.0).

    В выписке за 05.11.2026 «БИК: AKBBBY2X УНП» и «: 100325912» попали
    на разные строки. УНП в операции оставался пустым, а значит комиссию
    банка было некуда записать и строка не разносилась целиком (979,72
    по картам и 13,17 комиссией). Годятся все три вида разрыва: после
    «УНП», после «УНП:» и без двоеточия вовсе.
    """

    return RE_SPLIT_UNP.sub(lambda m: "УНП: " + m.group(1), text)


def parse_statement_text(text: str) -> Statement:
    """Разобрать уже извлечённый текст выписки."""

    text = join_split_headers(text)
    text = join_split_unp(text)
    if "УНП:" not in text or "Фактическая дата" not in text:
        raise StatementError(
            "Файл не похож на выписку из банка: в нём нет ни одного "
            "платежа с УНП и датой операции."
        )

    statement = Statement()
    if match := RE_STATEMENT_DATE.search(text):
        statement.statement_date = match.group(1)
    if match := RE_ACCOUNT.search(text):
        statement.account = match.group(1).strip()
    if match := RE_ACCOUNT_NAME.search(text):
        statement.account_name = match.group(1).strip()
    if match := RE_OPENING.search(text):
        statement.opening_balance = parse_amount(match.group(1))
    if match := RE_CLOSING.search(text):
        statement.closing_balance = parse_amount(match.group(1))

    rows: list[StatementRow] = []
    # Накапливаемые поля текущей операции.
    doc_date = ""
    unp = ""
    name = ""
    purpose: list[str] = []
    incoming = Decimal("0")
    outgoing = Decimal("0")
    in_purpose = False  # идёт многострочное «Назначение платежа»
    started = False  # начали ли читать операцию

    def finish(operation_date: str) -> None:
        """Закрыть операцию: всё, что собрали, уходит в строку."""
        nonlocal unp, name, purpose, incoming, outgoing, in_purpose, started
        if not started:
            return
        text = " ".join(" ".join(purpose).split())
        commission = Decimal("0")
        if match := RE_COMMISSION.search(text):
            commission = parse_amount(match.group(1))
        rows.append(
            StatementRow(
                date=operation_date,
                unp=unp,
                name=" ".join(name.split()),
                incoming=incoming,
                outgoing=outgoing,
                purpose=text,
                commission=commission,
            )
        )
        unp, name, purpose = "", "", []
        incoming = outgoing = Decimal("0")
        in_purpose = False
        started = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if match := RE_TURNOVER.match(line):
            statement.turnover_outgoing = parse_amount(match.group(1))
            statement.turnover_incoming = parse_amount(match.group(2))
            continue

        if match := RE_FACT_DATE.search(line):
            finish(match.group(1))
            continue

        if in_purpose:  # продолжение назначения платежа
            purpose.append(line)
            continue

        if RE_PURPOSE.match(line):
            in_purpose = True
            purpose.append(line.removeprefix("Назначение платежа:").strip())
            continue

        if match := RE_DOC.match(line):
            # Начало новой операции. Предыдущую закрываем датой
            # документа: «Фактической даты» в предварительной выписке
            # может не быть.
            finish(doc_date)
            doc_date = match.group(1)
            started = True
            continue

        if match := RE_UNP.search(line):
            unp = match.group(1)
            started = True
            continue

        # v0.64.0: сначала пробуем строку с кодом платежа в бюджет: у
        # неё **три** числа, и первое — не сумма, а код.
        if match := RE_AMOUNTS_BUDGET.match(line):
            outgoing = parse_amount(match.group(1))  # Дебет — оплата
            incoming = parse_amount(match.group(2))  # Кредит — поступление
            continue
        if match := RE_AMOUNTS.match(line):
            outgoing = parse_amount(match.group(1))  # Дебет — оплата
            incoming = parse_amount(match.group(2))  # Кредит — поступление
            continue

        if line.startswith("Счет:") or line.startswith("БИК:"):
            continue

        # Строка с наименованием идёт сразу после сумм; всё остальное
        # (шапка, итоги, подписи) до первой суммы операции игнорируем.
        if outgoing or incoming:
            if not name:
                name = line

    finish(doc_date)  # операция без «Фактической даты» в конце файла
    statement.rows = rows
    if not rows:
        raise StatementError("В выписке не нашлось ни одной операции.")
    return statement
