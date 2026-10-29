"""Выгрузка и загрузка баз в Excel (v0.20.0).

Кнопки «Выгрузить базу» / «Загрузить базу» в шапке работают **сразу с
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

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from .models import (
    AppData,
    CashReceipt,
    Counterparty,
    Operation,
    OtherPayment,
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

# Обязательные листы базы: по ним понимаем, что база в файле есть.
_REQUIRED_SHEETS = (SHEET_SUPPLIERS, SHEET_CUSTOMERS)


class ExcelError(Exception):
    """Не удалось выгрузить или загрузить файл Excel."""


def _sheet_title(base_key: str, suffix: str) -> str:
    return f"{BASE_TITLES[base_key]}. {suffix}"


def _load_workbook():
    """openpyxl импортируем лениво: без пакета программа всё равно идёт."""

    try:
        from openpyxl import Workbook, load_workbook
    except ImportError as exc:  # pragma: no cover - зависит от установки
        raise ExcelError(
            "Не найден пакет openpyxl — выгрузка в Excel недоступна."
        ) from exc
    return Workbook, load_workbook


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

    counterparty_header = [
        "Название",
        "Идентификатор",
        "Отсрочка (дней)",
        "Предоплата",
        "Реализация",
        "В архиве",
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
            ["Дата", "Название", "Сумма", "Направление", "Идентификатор"],
            [
                [
                    item.date,
                    item.name,
                    _cell_number(item.amount),
                    item.direction,
                    item.id,
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
    }


def export_bases(bases: dict[str, AppData], path: Path | str) -> Path:
    """Выгрузить **обе** базы (МИР и КОРЕЛИЧИ) в один файл Excel.

    Возвращает путь к сохранённому файлу. Порядок баз в книге — МИР,
    затем КОРЕЛИЧИ: так привычнее читать.
    """

    Workbook, _unused = _load_workbook()
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

    _unused, load_workbook = _load_workbook()
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
        )


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
