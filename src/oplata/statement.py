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
RE_PURPOSE = re.compile(r"^Назначение платежа:")
RE_OPENING = re.compile(rf"Входящее сальдо:\s*({AMOUNT})")
RE_CLOSING = re.compile(rf"Исходящее сальдо:\s*({AMOUNT})")
RE_TURNOVER = re.compile(rf"^ОБОРОТЫ\s+({AMOUNT})\s+({AMOUNT})$")
RE_ACCOUNT = re.compile(r"Счет клиента:\s*([A-Z0-9 ]+)")
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


@dataclass
class Statement:
    """Выписка целиком: шапка и операции."""

    statement_date: str = ""
    account: str = ""
    opening_balance: Decimal = Decimal("0")
    closing_balance: Decimal = Decimal("0")
    turnover_outgoing: Decimal = Decimal("0")  # обороты по дебету
    turnover_incoming: Decimal = Decimal("0")  # обороты по кредиту
    rows: list[StatementRow] = field(default_factory=list)

    @property
    def total_incoming(self) -> Decimal:
        return sum((row.incoming for row in self.rows), Decimal("0"))

    @property
    def total_outgoing(self) -> Decimal:
        return sum((row.outgoing for row in self.rows), Decimal("0"))


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


def parse_statement_text(text: str) -> Statement:
    """Разобрать уже извлечённый текст выписки."""

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
        rows.append(
            StatementRow(
                date=operation_date,
                unp=unp,
                name=" ".join(name.split()),
                incoming=incoming,
                outgoing=outgoing,
                purpose=" ".join(" ".join(purpose).split()),
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
