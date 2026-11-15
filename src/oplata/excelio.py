"""Выгрузка и загрузка баз в Excel (v0.20.0).

Кнопки «Выгрузить базы» / «Загрузить базы» в шапке работают **сразу с
обеими базами** — МИР и КОРЕЛИЧИ — одним файлом ``.xlsx``:

* листы книги называются ``«МИР. Поставщики»``, ``«КОРЕЛИЧИ. Поставщики»``
  и так далее — по листу на каждую таблицу каждой базы;
* выгрузка не меняет данные программы, загрузка полностью заменяет
  содержимое баз теми данными, что в файле (поэтому просит подтверждения);
* даты пишутся строкой ``YYYY-MM-DD``, суммы — числом с двумя знаками,
  признаки («Предоплата», «В архиве») — словами «да»/«нет».

Формат сделан «человеческим»: файл можно открыть в Excel, поправить
вручную и загрузить обратно. Пользователи и пароль в выгрузку **не
попадают** — это настройки доступа, а не данные учёта.

Модуль работает через ``openpyxl``; если пакет не установлен, функции
бросают :class:`ExcelError` с понятным текстом — программа при этом
продолжает работать.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

try:  # openpyxl нужен только для обмена с Excel
    from openpyxl import Workbook, load_workbook
except ImportError:  # pragma: no cover - зависит от установки
    Workbook = None  # type: ignore[assignment]
    load_workbook = None  # type: ignore[assignment]

from .models import (
    INCOMING,
    KKM_KIND_REPORT,
    AppData,
    Bank,
    CashReceipt,
    Counterparty,
    KkmReport,
    Operation,
    OtherPayment,
    RoutingRule,
    YearStart,
    YearStartCustomerDebt,
    YearStartDebt,
)
from .storage import BASE_TITLES, BASE_MIR

# Суффиксы листов книги (к имени листа прибавляется название базы).
SHEET_SUPPLIERS = "Поставщики"
SHEET_CUSTOMERS = "Покупатели"
SHEET_OPERATIONS = "Операции"
SHEET_CASH = "Касса"
SHEET_OTHER = "Прочие оплаты"
SHEET_CALENDAR = "Календарь"
SHEET_YEAR = "Перенос"
SHEET_YEAR_SUPPLIERS = "Долги поставщикам"
SHEET_YEAR_CUSTOMERS = "Долги покупателей"
# v0.79.0: листы, которых не хватало. Без них выгрузка-загрузка
# теряла настройки, набранные руками: правила разноса (плашка
# «Свойства контрагента»), карточку своего банка, журнал разнесённых
# строк выписки (без него повторная загрузка выписки задваивает
# суммы) и реестр отчётов ККМ.
SHEET_ROUTING = "Правила разноса"
SHEET_BANK = "Свой банк"
SHEET_IMPORTED = "Разнесённые строки"
SHEET_IMPORTED_WRITES = "Разнесённые записи"
SHEET_KKM = "Отчеты ККМ"

# Обязательные листы базы: по ним понимаем, что база в файле есть.
_REQUIRED_SHEETS = (SHEET_SUPPLIERS, SHEET_CUSTOMERS)


class ExcelError(Exception):
    """Не удалось выгрузить или загрузить файл Excel."""


def _sheet_title(base_key: str, suffix: str) -> str:
    return f"{BASE_TITLES[base_key]}. {suffix}"


# openpyxl импортируется на уровне модуля (try/except выше): так
# PyInstaller видит пакет статически и кладёт его в сборку. Без пакета
# программа работает, но кнопки выгрузки сообщают, что он не найден.
OPENPYXL_MISSING_TEXT = (
    "Не найден пакет openpyxl — выгрузка в Excel недоступна."
)


def _require_openpyxl() -> None:
    """Проверить, что openpyxl установлен (иначе — понятная ошибка)."""

    if Workbook is None or load_workbook is None:
        raise ExcelError(OPENPYXL_MISSING_TEXT)


def _dec(value: object) -> str:
    """Сумма ячейки -> строка с двумя знаками (``12.5`` -> ``"12.50"``)."""

    if value is None or value == "":
        return "0"
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return "0"


def _cell_number(value: str) -> float:
    """Строка суммы -> число для ячейки Excel."""

    try:
        return float(Decimal(value or "0"))
    except (InvalidOperation, ValueError):
        return 0.0


def _yes(value: object) -> str:
    return "да" if value else "нет"


def _parse_yes(value: object) -> bool:
    return str(value or "").strip().lower() in {"да", "1", "true", "yes", "+"}


def _bool_or_true(value: object) -> bool:
    """Как ``_parse_yes``, но пустая ячейка — True.

    v0.29.0: отметка «выписано» у реализации. В файлах, сохранённых до
    её появления, колонки нет вовсе, а вся введённая раньше реализация
    по правилу пользователя считается выписанной.
    """

    text = str(value or "").strip().lower()
    if not text:
        return True
    return text in {"да", "1", "true", "yes", "+"}


def _int(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _row(values: list[object], row: int) -> Callable[[int], object]:
    """Доступ к ячейке строки с защитой от коротких строк."""

    def get(index: int) -> object:
        return values[index] if index < len(values) else None

    return get


def _write_sheet(book, title: str, header: list[str], rows: list[list[object]]) -> None:
    sheet = book.create_sheet(title)
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"


def _base_rows(data: AppData) -> dict[str, tuple[list[str], list[list[object]]]]:
    """Все таблицы одной базы: название листа -> (заголовок, строки)."""

    # v0.53.0: УНП — последней колонкой. Импорт читает колонки по
    # порядку, поэтому старые выгрузки (без УНП) загружаются как прежде.
    counterparty_header = [
        "Название",
        "Идентификатор",
        "Отсрочка (дней)",
        "Предоплата",
        "Реализация",
        "В архиве",
        "УНП",
        # v0.79.0: примечание контрагента — оно помогает различать
        # карточки с одинаковым УНП (поле с v0.60.0).
        "Примечание",
    ]

    def counterparty_rows(items: list[Counterparty]) -> list[list[object]]:
        return [
            [
                item.name,
                item.id,
                item.deferment_days,
                _yes(item.prepayment),
                _yes(item.realization),
                _yes(item.archived),
                item.unp,
                item.note,
            ]
            for item in items
        ]

    return {
        SHEET_SUPPLIERS: (counterparty_header, counterparty_rows(data.suppliers)),
        SHEET_CUSTOMERS: (counterparty_header, counterparty_rows(data.customers)),
        SHEET_OPERATIONS: (
            [
                "Вид",
                "Идентификатор контрагента",
                "Дата",
                "Сумма",
                "Документ",
                "Комментарий",
                "Срок оплаты",
                "Идентификатор",
                # v0.29.0: отметка «выписано» у реализации покупателя
                # (зелёная метка в недельной таблице).
                "Выписано",
            ],
            [
                [
                    item.kind,
                    item.counterparty_id,
                    item.date,
                    _cell_number(item.amount),
                    item.document,
                    item.comment,
                    item.due_date,
                    item.id,
                    _yes(item.invoiced),
                ]
                for item in data.operations
            ],
        ),
        SHEET_CASH: (
            ["Дата", "Источник", "Сумма", "Комментарий", "Идентификатор"],
            [
                [
                    item.date,
                    item.source,
                    _cell_number(item.amount),
                    item.comment,
                    item.id,
                ]
                for item in data.cash_receipts
            ],
        ),
        SHEET_OTHER: (
            [
                "Дата",
                "Название",
                "Сумма",
                "Направление",
                "Идентификатор",
                # v0.79.0: карточка вида оплаты. Раньше выгружалась
                # только сумма, и после загрузки вид терял ставку НДС,
                # УНП, примечание и отмеченные даты.
                "НДС",
                "УНП",
                "Примечание",
                "Отмеченные даты",
            ],
            [
                [
                    item.date,
                    item.name,
                    _cell_number(item.amount),
                    item.direction,
                    item.id,
                    item.vat_rate,
                    item.unp,
                    item.note,
                    ";".join(item.dates),
                ]
                for item in data.other_payments
            ],
        ),
        SHEET_CALENDAR: (
            ["Дата", "Отметка"],
            [[day, mark] for day, mark in sorted(data.day_overrides.items())],
        ),
        SHEET_YEAR: (
            ["Дата", "Средства"],
            # Пустой перенос не пишем: иначе у пустой базы появляется
            # строка с нулями и файл выглядит непустым (v0.20.0).
            (
                [[data.year_start.date, _cell_number(data.year_start.funds)]]
                if data.year_start.date or _dec(data.year_start.funds) != "0"
                else []
            ),
        ),
        SHEET_YEAR_SUPPLIERS: (
            ["Идентификатор контрагента", "Дата оплаты", "Сумма"],
            [
                [item.counterparty_id, item.due_date, _cell_number(item.amount)]
                for item in data.year_start.supplier_debts
            ],
        ),
        SHEET_YEAR_CUSTOMERS: (
            ["Идентификатор контрагента", "Сумма"],
            [
                [item.counterparty_id, _cell_number(item.amount)]
                for item in data.year_start.customer_debts
            ],
        ),
        SHEET_ROUTING: (
            [
                "УНП",
                "Название",
                "Слова",
                "Куда",
                "Идентификатор контрагента",
                "Вид оплаты",
            ],
            [
                [
                    rule.unp,
                    rule.name,
                    ";".join(rule.words),
                    rule.target,
                    rule.counterparty_id,
                    rule.payment_name,
                ]
                for rule in data.routing_rules
            ],
        ),
        SHEET_BANK: (
            ["Название", "УНП"],
            [[data.bank.name, data.bank.unp]]
            if (data.bank.name or data.bank.unp)
            else [],
        ),
        SHEET_IMPORTED: (
            ["Ключ строки"],
            [[key] for key in data.imported_statement_rows],
        ),
        SHEET_IMPORTED_WRITES: (
            ["Ключ строки", "Что записали (JSON)"],
            [
                [str(item.get("key", "")), json.dumps(item, ensure_ascii=False)]
                for item in data.imported_statement_writes
            ],
        ),
        SHEET_KKM: (
            ["Дата", "Вид", "Номер строки", "Сумма", "Идентификатор"],
            [
                [
                    item.date,
                    item.kind,
                    item.slot,
                    _cell_number(item.amount),
                    item.id,
                ]
                for item in data.kkm_reports
            ],
        ),
    }


def export_bases(bases: dict[str, AppData], path: Path | str) -> Path:
    """Выгрузить **обе** базы (МИР и КОРЕЛИЧИ) в один файл Excel.

    Возвращает путь к сохранённому файлу. Порядок баз в книге — МИР,
    затем КОРЕЛИЧИ: так привычнее читать.
    """

    _require_openpyxl()
    path = Path(path)
    book = Workbook()
    book.remove(book.active)  # пустой лист от openpyxl не нужен

    order = [BASE_MIR] + [key for key in bases if key != BASE_MIR]
    for base_key in order:
        data = bases.get(base_key)
        if data is None:
            continue
        for suffix, (header, rows) in _base_rows(data).items():
            _write_sheet(book, _sheet_title(base_key, suffix), header, rows)

    try:
        book.save(path)
    except OSError as exc:
        raise ExcelError(f"Не удалось сохранить файл: {exc}") from exc
    return path


def _read_rows(book, title: str) -> list[list[object]]:
    """Строки листа без заголовка (пустые строки пропускаем)."""

    if title not in book.sheetnames:
        return []
    sheet = book[title]
    rows = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if row is None or all(cell is None or cell == "" for cell in row):
            continue
        rows.append(list(row))
    return rows


def _read_counterparties(rows: list[list[object]]) -> list[Counterparty]:
    result = []
    for values in rows:
        get = _row(values, 0)
        name = _text(get(0))
        if not name:
            continue
        result.append(
            Counterparty(
                name=name,
                id=_text(get(1)),
                deferment_days=_int(get(2)),
                prepayment=_parse_yes(get(3)),
                realization=_parse_yes(get(4)),
                archived=_parse_yes(get(5)),
                unp=_text(get(6)),  # v0.53.0: в старых выгрузках пусто
                # v0.79.0: примечание — в старых выгрузках колонки нет.
                note=_text(get(7)),
            )
        )
    return result


def _read_year_start(
    rows: dict[str, list[list[object]]],
) -> YearStart:
    main = rows.get(SHEET_YEAR) or []
    funds = "0"
    start_date = ""
    if main:
        get = _row(main[0], 0)
        start_date = _text(get(0))
        funds = _dec(get(1))

    supplier_debts = []
    for values in rows.get(SHEET_YEAR_SUPPLIERS, []):
        get = _row(values, 0)
        counterparty_id = _text(get(0))
        if not counterparty_id:
            continue
        supplier_debts.append(
            YearStartDebt(
                counterparty_id=counterparty_id,
                due_date=_text(get(1)),
                amount=_dec(get(2)),
            )
        )

    customer_debts = []
    for values in rows.get(SHEET_YEAR_CUSTOMERS, []):
        get = _row(values, 0)
        counterparty_id = _text(get(0))
        if not counterparty_id:
            continue
        customer_debts.append(
            YearStartCustomerDebt(
                counterparty_id=counterparty_id, amount=_dec(get(1))
            )
        )

    return YearStart(
        date=start_date,
        supplier_debts=supplier_debts,
        customer_debts=customer_debts,
        funds=funds,
    )


def import_bases(path: Path | str) -> dict[str, AppData]:
    """Прочитать базы (МИР и КОРЕЛИЧИ) из файла Excel.

    Возвращает словарь ``base_key -> AppData`` только для тех баз, чьи
    листы нашлись в книге. Содержимое программы функция не трогает:
    подмена данных — дело вызывающего кода (и только после вопроса
    пользователю).
    """

    _require_openpyxl()
    try:
        book = load_workbook(Path(path), data_only=True, read_only=True)
    except OSError as exc:
        raise ExcelError(f"Не удалось открыть файл: {exc}") from exc
    except Exception as exc:  # openpyxl кидает разные исключения
        raise ExcelError(f"Файл не похож на книгу Excel: {exc}") from exc

    result: dict[str, AppData] = {}
    try:
        _collect_bases(book, result)
    finally:
        try:
            book.close()
        except AttributeError:  # read_only-книга без close()
            pass
    if not result:
        raise ExcelError(
            "В файле не найдено ни одной базы Оплаты "
            "(листы «МИР. …» или «КОРЕЛИЧИ. …»)."
        )
    return result


def _collect_bases(book, result: dict[str, AppData]) -> None:
    """Собрать базы из открытой книги в ``result``."""

    present = set(book.sheetnames)
    for base_key in BASE_TITLES:
        # База в файле есть, если в книге лежат её листы контрагентов
        # (даже пустые: выгруженная пустая база — тоже база).
        if not all(
            _sheet_title(base_key, name) in present for name in _REQUIRED_SHEETS
        ):
            continue
        sheets = {
            suffix: _read_rows(book, _sheet_title(base_key, suffix))
            for suffix in (
                SHEET_SUPPLIERS,
                SHEET_CUSTOMERS,
                SHEET_OPERATIONS,
                SHEET_CASH,
                SHEET_OTHER,
                SHEET_CALENDAR,
                SHEET_YEAR,
                SHEET_YEAR_SUPPLIERS,
                SHEET_YEAR_CUSTOMERS,
                # v0.79.0: настройки и журналы. В старых файлах этих
                # листов нет — _read_rows вернёт пустоту.
                SHEET_ROUTING,
                SHEET_BANK,
                SHEET_IMPORTED,
                SHEET_IMPORTED_WRITES,
                SHEET_KKM,
            )
        }
        operations = []
        for values in sheets[SHEET_OPERATIONS]:
            get = _row(values, 0)
            if not _text(get(0)) and not _text(get(2)):
                continue
            operations.append(
                Operation(
                    kind=_text(get(0)),
                    counterparty_id=_text(get(1)),
                    date=_text(get(2)),
                    amount=_dec(get(3)),
                    document=_text(get(4)),
                    comment=_text(get(5)),
                    due_date=_text(get(6)),
                    id=_text(get(7)),
                    # v0.29.0: пустая ячейка в старых файлах — реализация
                    # считается выписанной, как и в JSON без поля.
                    invoiced=_bool_or_true(get(8)),
                )
            )

        cash_receipts = []
        for values in sheets[SHEET_CASH]:
            get = _row(values, 0)
            if not _text(get(0)):
                continue
            cash_receipts.append(
                CashReceipt(
                    date=_text(get(0)),
                    source=_text(get(1)),
                    amount=_dec(get(2)),
                    comment=_text(get(3)),
                    id=_text(get(4)),
                )
            )

        other_payments = []
        for values in sheets[SHEET_OTHER]:
            get = _row(values, 0)
            if not _text(get(1)):
                continue
            other_payments.append(
                OtherPayment(
                    date=_text(get(0)),
                    name=_text(get(1)),
                    amount=_dec(get(2)),
                    direction=_text(get(3)) or "out",
                    id=_text(get(4)),
                    # v0.79.0: карточка вида — НДС, УНП, примечание,
                    # отмеченные даты. В старых выгрузках этих колонок
                    # нет, тогда значения пустые (как раньше).
                    vat_rate=_text(get(5)),
                    unp=_text(get(6)),
                    note=_text(get(7)),
                    dates=[
                        day for day in _text(get(8)).split(";") if len(day) >= 10
                    ],
                )
            )

        day_overrides = {}
        for values in sheets[SHEET_CALENDAR]:
            get = _row(values, 0)
            day = _text(get(0))
            mark = _text(get(1)) or "off"
            if day:
                day_overrides[day] = mark

        result[base_key] = AppData(
            suppliers=_read_counterparties(sheets[SHEET_SUPPLIERS]),
            customers=_read_counterparties(sheets[SHEET_CUSTOMERS]),
            operations=operations,
            cash_receipts=cash_receipts,
            other_payments=other_payments,
            day_overrides=day_overrides,
            year_start=_read_year_start(sheets),
            # v0.79.0: правила разноса, карточка банка, журнал
            # разнесённых строк и реестр ККМ едут вместе с базой.
            routing_rules=_read_routing(sheets),
            bank=_read_bank(sheets),
            imported_statement_rows=_read_imported(sheets),
            imported_statement_writes=_read_imported_writes(sheets),
            kkm_reports=_read_kkm(sheets),
        )


def _read_routing(sheets) -> list[RoutingRule]:
    """Правила разноса из листа (v0.79.0); старые файлы — без правил."""

    result = []
    for values in sheets.get(SHEET_ROUTING) or []:
        get = _row(values, 0)
        if not _text(get(0)) and not _text(get(3)):
            continue
        result.append(
            RoutingRule(
                unp=_text(get(0)),
                name=_text(get(1)),
                words=[word for word in _text(get(2)).split(";") if word],
                target=_text(get(3)),
                counterparty_id=_text(get(4)),
                payment_name=_text(get(5)),
            )
        )
    return result


def _read_bank(sheets) -> Bank:
    """Карточка своего банка из листа (v0.79.0)."""

    rows = sheets.get(SHEET_BANK) or []
    if not rows:
        return Bank()
    get = _row(rows[0], 0)
    return Bank(name=_text(get(0)), unp=_text(get(1)))


def _read_imported(sheets) -> list[str]:
    """Ключи строк выписки, уже разнесённых (v0.79.0)."""

    return [
        _text(_row(values, 0)(0))
        for values in sheets.get(SHEET_IMPORTED) or []
        if _text(_row(values, 0)(0))
    ]


def _read_imported_writes(sheets) -> list[dict]:
    """Подробности разноса: ключ + что записали (v0.79.0)."""

    result = []
    for values in sheets.get(SHEET_IMPORTED_WRITES) or []:
        get = _row(values, 0)
        raw = _text(get(1))
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            item.setdefault("key", _text(get(0)))
            result.append(item)
    return result


def _read_kkm(sheets) -> list[KkmReport]:
    """Реестр отчётов ККМ из листа (v0.79.0)."""

    result = []
    for values in sheets.get(SHEET_KKM) or []:
        get = _row(values, 0)
        if not _text(get(0)):
            continue
        result.append(
            KkmReport(
                date=_text(get(0)),
                kind=_text(get(1)) or KKM_KIND_REPORT,
                slot=_int(get(2)),
                amount=_dec(get(3)) or "0",
                id=_text(get(4)),
            )
        )
    return result


def describe_bases(bases: dict[str, AppData]) -> str:
    """Описание баз для вопроса: «МИР (12 записей), КОРЕЛИЧИ (пусто)».

    Пустую базу называем пустой: иначе по невнимательности можно
    затереть рабочие данные выгрузкой, где база оказалась без записей.
    """

    parts = []
    for base_key, data in bases.items():
        count = (
            len(data.suppliers)
            + len(data.customers)
            + len(data.operations)
            + len(data.cash_receipts)
            + len(data.other_payments)
        )
        tail = f"{count} записей" if count else "пусто"
        parts.append(f"{BASE_TITLES[base_key]} ({tail})")
    return ", ".join(parts)


def default_export_name() -> str:
    """Имя файла выгрузки по умолчанию: ``oplata-базы-2026-10-29.xlsx``."""

    from datetime import date

    return f"oplata-базы-{date.today().isoformat()}.xlsx"
