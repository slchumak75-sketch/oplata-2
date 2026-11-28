"""Чтение и безопасный план загрузки реестра документов Excel.

Модуль не показывает окна и не обращается к сети. Сначала формируется
план, затем пользователь проверяет его и отдельно подтверждает запись.
Неизвестные документы (включая переоценку) не меняют данные.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable
from zipfile import BadZipFile, ZipFile

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - зависит от установленного пакета
    load_workbook = None

from .calc import payment_due_date
from .models import (
    KIND_INCOME,
    KIND_SALE,
    KKM_DAY_KINDS,
    KKM_KIND_REPORT,
    KKM_KIND_RETURN,
    AppData,
    Counterparty,
    KkmReport,
    Operation,
)
from .storage import DataStore, backup_dirs

SUPPLY = "supply"
SALE = "sale"
REPORT = "report"
RETURN = "return"
KINDS = (SUPPLY, SALE, REPORT, RETURN)
KIND_TITLES = {
    SUPPLY: "Поступление в розницу",
    SALE: "Реализация",
    REPORT: "Отчет ККМ",
    RETURN: "Возврат от покупателя",
}
# Столбец «Документ» в образце содержит после названия вида уточнение в
# скобках. Сравниваем начало и границу слова, а не всю ячейку целиком.
DOCUMENT_PREFIXES = (
    ("поступление в розницу", SUPPLY),
    ("реализация", SALE),
    ("отчет ккм", REPORT),
    ("возврат от покупателя", RETURN),
    ("возврат покупателя", RETURN),
)
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 150 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 64
HEADER_SEARCH_ROWS = 50
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("99999999999999999999.99")
_MONEY_RE = re.compile(
    r"^[+]?(?:[0-9]{1,3}(?:[ '\u00a0\u202f\u2019][0-9]{3})+|[0-9]+)"
    r"(?:[.,][0-9]{1,2})?$"
)


class RegistryError(Exception):
    """Реестр нельзя прочитать, проверить или безопасно записать."""


@dataclass(frozen=True)
class RegistryRow:
    row_number: int
    kind: str
    day: date | None
    number: str
    amount: Decimal | None
    unp: str
    information: str
    invoiced: bool | None
    status: str = ""
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegistryPreviewRow:
    """Исходная строка для просмотра, включая пропускаемые документы."""
    row_number: int
    day: date | None
    values: tuple[str, ...]


@dataclass(frozen=True)
class Registry:
    rows: tuple[RegistryRow, ...]
    ignored_revaluation: int = 0
    ignored_other: int = 0
    issues: tuple[str, ...] = ()
    preview_rows: tuple[RegistryPreviewRow, ...] = ()


@dataclass(frozen=True)
class RegistryChange:
    kind: str
    day: str
    rows: tuple[RegistryRow, ...]
    amount: Decimal
    existing_amount: Decimal | None
    existing_count: int
    action: str  # new | replace | same
    counterparty_id: str = ""
    counterparty_name: str = ""
    archived: bool = False
    slot: int | None = None
    invoiced: bool | None = None
    due_date: str = ""
    missing_previous: int = 0
    existing_details: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, str, int | None]:
        return (self.kind, self.day, self.counterparty_id, self.slot)

    @property
    def mixed_status(self) -> bool:
        return self.kind == SALE and len({row.invoiced for row in self.rows}) > 1


@dataclass
class RegistryPlan:
    base_key: str
    changes: tuple[RegistryChange, ...]
    issues: tuple[str, ...]
    baseline_hash: str
    candidate: AppData | None = field(default=None, repr=False)
    has_changes: bool = False


@dataclass(frozen=True)
class RegistrySnapshot:
    base_key: str
    path: Path
    content: bytes = field(repr=False)
    baseline_hash: str


@dataclass(frozen=True)
class RegistryCommitResult:
    saved: bool
    backups: tuple[Path, ...] = ()
    refresh_failed: bool = False


def _normal(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


def _kind(value: object) -> str:
    text = _normal(value)
    for prefix, kind in DOCUMENT_PREFIXES:
        if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "("):
            return kind
    return ""


def _is_revaluation(value: object) -> bool:
    text = _normal(value)
    return text == "переоценка" or text.startswith("переоценка ") or text.startswith("переоценка(")


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    # В выгрузке реестра встречается текстовая дата «19.11.26».
    # Двузначный год трактуем как 2000–2099, без системного порога %y
    # (который неожиданно относит 69–99 к предыдущему веку).
    short_year = re.fullmatch(r"(\d{1,2}\.\d{1,2}\.)(\d{2})(?=\s|$)(.*)", text)
    if short_year:
        text = f"{short_year.group(1)}20{short_year.group(2)}{short_year.group(3)}"
    for fmt in ("%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("неверная или пустая дата")


def _money(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("нет суммы")
    if isinstance(value, (int, float, Decimal)):
        text = str(value)
    else:
        text = str(value).strip()
        if not _MONEY_RE.fullmatch(text):
            raise ValueError("непонятный формат суммы")
        for sep in (" ", "'", "\u00a0", "\u202f", "\u2019"):
            text = text.replace(sep, "")
        text = text.replace(",", ".")
    try:
        amount = Decimal(text)
        if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
            raise ValueError("сумма должна быть положительной и конечной")
        cents = amount.quantize(CENT)
        if amount != cents:
            raise ValueError("в сумме больше двух знаков после запятой")
        return cents
    except InvalidOperation as exc:
        raise ValueError("непонятный формат суммы") from exc


def _existing_money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > MAX_AMOUNT:
            raise ValueError
        cents = amount.quantize(CENT)
        if amount != cents:
            raise ValueError
        return cents
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("неверная сумма в базе") from exc


def _number(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    # Строка позже попадает в выгрузку Excel. Даже текстовое значение,
    # начинающееся с «=», openpyxl записал бы там как формулу.
    if text.startswith("=") or len(text) > 128 or any(ord(ch) < 32 for ch in text):
        return ""
    return text


def _unp(value: object) -> str:
    """Не достраивать ведущие нули и не угадывать по части УНП."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _invoiced(value: object) -> bool:
    if isinstance(value, bool):
        return value
    status = _normal(value)
    if status in {"", "☐", "□", "нет", "false", "0", "не проведен"}:
        return False
    if status in {"проведен", "☑", "✓", "✔", "да", "true", "1"}:
        return True
    raise ValueError("неизвестный статус реализации")


def _headers(sheet) -> list[tuple[int, dict[str, int]]]:
    required = {"дата", "документ", "номер", "статус", "сумма", "информация"}
    found: list[tuple[int, dict[str, int]]] = []
    for row_number, row in enumerate(
        sheet.iter_rows(min_row=1, max_row=min(sheet.max_row or 1, HEADER_SEARCH_ROWS)), 1
    ):
        positions: dict[str, list[int]] = defaultdict(list)
        for index, cell in enumerate(row):
            text = _normal(cell.value)
            if text:
                positions[text].append(index)
        if required.issubset(positions):
            if any(len(positions[name]) != 1 for name in required):
                raise RegistryError("В заголовке реестра повторяются названия столбцов.")
            mapping = {name: positions[name][0] for name in required}
            currencies = positions.get("вал.", []) + positions.get("валюта", [])
            if len(currencies) != 1:
                raise RegistryError("Не найден однозначный столбец валюты «Вал.». ")
            mapping["валюта"] = currencies[0]
            unps = positions.get("унп", [])
            if len(unps) > 1:
                raise RegistryError("В заголовке реестра несколько столбцов УНП.")
            mapping["унп"] = unps[0] if unps else mapping["информация"] + 1
            if mapping["унп"] <= max(mapping[name] for name in required | {"валюта"}):
                raise RegistryError("Последний столбец реестра должен содержать УНП.")
            found.append((row_number, mapping))
    return found


def parse_registry(path: Path | str) -> Registry:
    """Прочитать образец реестра без записи в базу и без вывода содержимого."""
    if load_workbook is None:
        raise RegistryError("Для чтения реестра необходим openpyxl.")
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise RegistryError("Выберите реестр в формате .xlsx.")
    try:
        if path.stat().st_size > MAX_ARCHIVE_BYTES:
            raise RegistryError("Файл реестра слишком большой.")
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise RegistryError("Содержимое реестра слишком большое.")
            if any(item.flag_bits & 1 for item in entries):
                raise RegistryError("Защищённый паролем реестр не поддерживается.")
        book = load_workbook(path, data_only=False, read_only=True, keep_links=False)
    except RegistryError:
        raise
    except (OSError, BadZipFile, ValueError, KeyError) as exc:
        raise RegistryError("Не удалось открыть книгу .xlsx. Проверьте файл.") from exc
    except Exception as exc:  # openpyxl выдаёт разные ошибки повреждённого XML
        raise RegistryError("Не удалось прочитать книгу .xlsx. Проверьте файл.") from exc

    try:
        candidates = []
        for sheet in book.worksheets:
            if (sheet.max_column or 0) > MAX_COLUMNS or (sheet.max_row or 0) > MAX_ROWS:
                raise RegistryError("Слишком много строк или столбцов в книге.")
            candidates.extend((sheet, number, cols) for number, cols in _headers(sheet))
        if len(candidates) != 1:
            raise RegistryError(
                "Не найден единственный заголовок реестра: нужны столбцы "
                "Дата, Документ, Номер, Статус, Сумма, Вал. и Информация."
            )
        sheet, header_row, columns = candidates[0]
        max_column = max(sheet.max_column or 0, columns["унп"] + 1)
        rows: list[RegistryRow] = []
        preview: list[RegistryPreviewRow] = []
        issues: list[str] = []
        revaluation = other = 0
        header_cells = next(sheet.iter_rows(min_row=header_row, max_row=header_row,
                                            max_col=max_column))
        header_positions = {_normal(cell.value): index for index, cell in enumerate(header_cells)
                            if cell.value not in (None, "")}
        serial_col = header_positions.get("№ п/п")
        author_col = header_positions.get("автор")

        def display(cell) -> str:
            value = cell.value
            if isinstance(value, (date, datetime)):
                return value.strftime("%d.%m.%Y")
            return "" if value is None else str(value).strip()

        for row_number, cells in enumerate(
            sheet.iter_rows(min_row=header_row + 1, max_col=max_column), header_row + 1
        ):
            if any(cell.value not in (None, "") for cell in cells):
                try:
                    preview_day = _date(cells[columns["дата"]].value)
                except ValueError:
                    preview_day = None
                preview_values = (
                    display(cells[serial_col]) if serial_col is not None else "",
                    display(cells[columns["дата"]]), display(cells[columns["документ"]]),
                    display(cells[columns["номер"]]),
                    display(cells[author_col]) if author_col is not None else "",
                    display(cells[columns["статус"]]), display(cells[columns["сумма"]]),
                    display(cells[columns["валюта"]]), display(cells[columns["информация"]]),
                    display(cells[columns["унп"]]),
                )
                preview.append(RegistryPreviewRow(row_number, preview_day, preview_values))
            document_cell = cells[columns["документ"]]
            if document_cell.data_type == "f":
                issues.append(f"Строка {row_number}: вид документа задан формулой.")
                continue
            document = document_cell.value
            if not str(document or "").strip():
                # Итоговая строка без даты и вида документа — не документ.
                if cells[columns["дата"]].value not in (None, ""):
                    issues.append(f"Строка {row_number}: указана дата, но нет вида документа.")
                continue
            kind = _kind(document)
            if not kind:
                if _is_revaluation(document):
                    revaluation += 1
                else:
                    other += 1
                continue

            row_issues: list[str] = []
            used = ["дата", "номер", "сумма", "валюта"]
            if kind in (SUPPLY, SALE):
                used.append("унп")
            if kind == SALE:
                used.append("статус")
            for name in used:
                if cells[columns[name]].data_type == "f":
                    row_issues.append(f"столбец «{name}» содержит формулу")
            if any(cell.value not in (None, "") for cell in cells[columns["унп"] + 1 :]):
                row_issues.append("после столбца УНП есть дополнительные данные")
            try:
                day = _date(cells[columns["дата"]].value)
            except ValueError as exc:
                day = None
                row_issues.append(str(exc))
            number = _number(cells[columns["номер"]].value)
            if not number:
                row_issues.append("нет номера документа или он записан в недопустимом формате")
            try:
                amount = _money(cells[columns["сумма"]].value)
            except ValueError as exc:
                amount = None
                row_issues.append(str(exc))
            currency = _normal(cells[columns["валюта"]].value)
            if currency not in {"byn", "руб", "руб.", "бел. руб.", "белорусский рубль"}:
                row_issues.append("не указана валюта BYN или указана другая валюта")
            try:
                invoiced = _invoiced(cells[columns["статус"]].value) if kind == SALE else None
            except ValueError as exc:
                invoiced = None
                row_issues.append(str(exc))
            rows.append(
                RegistryRow(
                    row_number=row_number,
                    kind=kind,
                    day=day,
                    number=number,
                    amount=amount,
                    unp=_unp(cells[columns["унп"]].value),
                    information=str(cells[columns["информация"]].value or "").strip(),
                    invoiced=invoiced,
                    status=str(cells[columns["статус"]].value or "").strip(),
                    issues=tuple(row_issues),
                )
            )
        return Registry(tuple(rows), revaluation, other, tuple(issues), tuple(preview))
    except RegistryError:
        raise
    except Exception as exc:
        raise RegistryError("Не удалось прочитать строки реестра. Проверьте файл.") from exc
    finally:
        book.close()


def auto_assignments(registry: Registry, data: AppData) -> dict[int, str]:
    """Автоматически выбирать только единственную неархивную карточку по УНП."""
    result: dict[int, str] = {}
    for row in registry.rows:
        if row.kind not in (SUPPLY, SALE) or not re.fullmatch(r"[0-9]{9}", row.unp):
            continue
        items = data.suppliers if row.kind == SUPPLY else data.customers
        matches = [item for item in items if item.unp.strip() == row.unp]
        if len(matches) == 1 and not matches[0].archived:
            result[row.row_number] = matches[0].id
    return result


def _data_hash(data: AppData) -> str:
    text = json.dumps(data.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _doc_key(row: RegistryRow) -> tuple[str, int, str]:
    assert row.day is not None
    return (row.kind, row.day.year, row.number.casefold())


def _link_key(link: dict) -> tuple[str, int, str]:
    day = date.fromisoformat(str(link["date"]))
    return (str(link["kind"]), day.year, str(link["number"]).casefold())


def _link_target(link: dict) -> tuple[str, str, str, int | None]:
    kind = str(link["kind"])
    day = str(link["date"])
    if kind in (SUPPLY, SALE):
        return (kind, day, str(link["counterparty_id"]), None)
    return (kind, day, "", int(link["slot"]))


def _valid_link(link: dict, base_key: str) -> bool:
    try:
        kind = str(link["kind"])
        if kind not in KINDS or not str(link["number"]).strip() or not str(link["record_id"]).strip():
            return False
        date.fromisoformat(str(link["date"]))
        if kind in (SUPPLY, SALE):
            return bool(str(link["counterparty_id"]).strip())
        slot = int(link["slot"])
        kinds = KKM_DAY_KINDS[base_key]
        expected = KKM_KIND_REPORT if kind == REPORT else KKM_KIND_RETURN
        return 0 <= slot < len(kinds) and kinds[slot] == expected
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def _link_active(link: dict, data: AppData) -> bool:
    kind, day, partner, slot = _link_target(link)
    identity = str(link["record_id"])
    if kind in (SUPPLY, SALE):
        op_kind = KIND_INCOME if kind == SUPPLY else KIND_SALE
        return any(op.id == identity and op.kind == op_kind and op.date == day
                   and op.counterparty_id == partner for op in data.operations)
    return any(item.id == identity and item.date == day and item.slot == slot
               and item.kind == (KKM_KIND_REPORT if kind == REPORT else KKM_KIND_RETURN)
               for item in data.kkm_reports)


def _existing(data: AppData, key: tuple[str, str, str, int | None]) -> list:
    kind, day, partner, slot = key
    if kind in (SUPPLY, SALE):
        op_kind = KIND_INCOME if kind == SUPPLY else KIND_SALE
        return [op for op in data.operations if op.kind == op_kind
                and op.date == day and op.counterparty_id == partner]
    return [item for item in data.kkm_reports if item.date == day and item.slot == slot]


def _sale_document(rows: tuple[RegistryRow, ...]) -> str:
    """Порядок документов покупателя не влияет на повторный импорт."""
    return ", ".join(sorted((row.number for row in rows), key=str.casefold))


def _same(kind: str, old: list, rows: tuple[RegistryRow, ...], amount: Decimal,
          invoiced: bool | None) -> bool:
    if kind == SUPPLY:
        return sorted((_existing_money(op.amount), op.document) for op in old) == sorted(
            (row.amount, row.number) for row in rows
        )
    if kind == SALE:
        return (len(old) == 1 and _existing_money(old[0].amount) == amount
                and old[0].invoiced == invoiced
                and old[0].document == _sale_document(rows))
    expected = KKM_KIND_REPORT if kind == REPORT else KKM_KIND_RETURN
    return len(old) == 1 and old[0].kind == expected and _existing_money(old[0].amount) == amount


def plan_registry(registry: Registry, data: AppData, base_key: str,
                  assignments: dict[int, str]) -> RegistryPlan:
    """Сформировать полный план; ошибки и неопределённости блокируют запись."""
    baseline_hash = _data_hash(data)
    if base_key not in KKM_DAY_KINDS:
        return RegistryPlan(base_key, (), ("Неизвестная база.",), baseline_hash)
    issues = list(registry.issues)
    if not registry.rows:
        issues.append("В реестре нет документов для загрузки.")
    for row in registry.rows:
        issues.extend(f"Строка {row.row_number}: {message}." for message in row.issues)

    for items, title in ((data.suppliers, "поставщиков"), (data.customers, "покупателей")):
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)) or any(not identity for identity in ids):
            issues.append(f"В справочнике {title} повторяется или отсутствует ID карточки.")

    by_doc: dict[tuple, list[int]] = defaultdict(list)
    for row in registry.rows:
        if row.day is not None and row.number:
            by_doc[_doc_key(row)].append(row.row_number)
    for numbers in by_doc.values():
        if len(numbers) > 1:
            issues.append("Номер документа одного вида повторяется в строках "
                          + ", ".join(map(str, numbers)) + ". Проверьте реестр.")

    grouped: dict[tuple[str, str, str, int | None], list[RegistryRow]] = defaultdict(list)
    partners: dict[tuple, Counterparty] = {}
    reports: dict[str, list[RegistryRow]] = defaultdict(list)
    returns: dict[str, list[RegistryRow]] = defaultdict(list)
    for row in registry.rows:
        if row.day is None or row.amount is None or row.issues:
            continue
        day = row.day.isoformat()
        if row.kind in (SUPPLY, SALE):
            items = data.suppliers if row.kind == SUPPLY else data.customers
            selected = assignments.get(row.row_number, "")
            matches = [item for item in items if item.id == selected]
            if len(matches) != 1:
                issues.append(f"Строка {row.row_number}: выберите карточку контрагента.")
                continue
            key = (row.kind, day, selected, None)
            grouped[key].append(row)
            partners[key] = matches[0]
        elif row.kind == REPORT:
            reports[day].append(row)
        elif row.kind == RETURN:
            returns[day].append(row)

    report_slots = [i for i, kind in enumerate(KKM_DAY_KINDS[base_key]) if kind == KKM_KIND_REPORT]
    return_slots = [i for i, kind in enumerate(KKM_DAY_KINDS[base_key]) if kind == KKM_KIND_RETURN]
    for day, rows in reports.items():
        if len(rows) > len(report_slots):
            issues.append(f"{date.fromisoformat(day):%d.%m.%Y}: отчётов ККМ больше, чем строк "
                          "в этой базе; исправьте файл или разнесите вручную.")
            continue
        for row, slot in zip(rows, report_slots):
            grouped[(REPORT, day, "", slot)].append(row)
    for day, rows in returns.items():
        grouped[(RETURN, day, "", return_slots[0])].extend(rows)

    links = data.imported_registry_rows
    if any(not isinstance(link, dict) or not _valid_link(link, base_key) for link in links):
        issues.append("История импорта реестра повреждена; загрузка остановлена.")
        links = []
    active_links = [link for link in links if _link_active(link, data)]
    active_by_doc: dict[tuple, list[dict]] = defaultdict(list)
    for link in active_links:
        active_by_doc[_link_key(link)].append(link)
    if any(len(entries) > 1 for entries in active_by_doc.values()):
        issues.append("В истории реестра один документ связан с несколькими записями.")

    planned_keys = set(grouped)
    for key, rows in grouped.items():
        for row in rows:
            for previous in active_by_doc.get(_doc_key(row), []):
                old_key = _link_target(previous)
                if old_key != key and old_key not in planned_keys:
                    issues.append(
                        f"Строка {row.row_number}: документ ранее был загружен в другую "
                        "дату или строку. Прежняя запись не заменяется этим файлом; "
                        "проверьте её вручную."
                    )

    changes: list[RegistryChange] = []
    for key, source in grouped.items():
        kind, day, partner_id, slot = key
        rows = tuple(source)
        amount = sum((row.amount for row in rows if row.amount is not None), Decimal("0"))
        if kind == SALE and len(_sale_document(rows)) > 30_000:
            issues.append(f"{date.fromisoformat(day):%d.%m.%Y}: слишком много документов "
                          "одного покупателя за день для безопасной загрузки.")
            continue
        if amount > MAX_AMOUNT:
            issues.append(f"{date.fromisoformat(day):%d.%m.%Y}: сумма дня слишком большая.")
            continue
        existing = _existing(data, key)
        try:
            old_amount = sum((_existing_money(item.amount) for item in existing), Decimal("0"))
            invoiced = all(row.invoiced is True for row in rows) if kind == SALE else None
            action = "new" if not existing else (
                "same" if _same(kind, existing, rows, amount, invoiced) else "replace"
            )
        except ValueError:
            issues.append(f"{date.fromisoformat(day):%d.%m.%Y}: в существующей записи неверная сумма.")
            continue
        counterparty = partners.get(key)
        due = ""
        if kind == SUPPLY and counterparty is not None:
            try:
                due = payment_due_date(date.fromisoformat(day), counterparty,
                                       data.day_overrides).isoformat()
            except (OverflowError, ValueError):
                issues.append(f"{date.fromisoformat(day):%d.%m.%Y}: не удалось рассчитать срок поставки.")
                continue
        source_keys = {_doc_key(row) for row in rows}
        missing = sum(1 for link in active_links if _link_target(link) == key
                      and _link_key(link) not in source_keys)
        details = tuple(
            f"Сумма: {item.amount}; документ: {getattr(item, 'document', '') or '—'}; "
            + (f"статус: {'выписано (зелёная)' if item.invoiced else 'в работе (красная)'}; "
               if kind == SALE else "")
            + f"срок: {getattr(item, 'due_date', '') or '—'}; "
            f"комментарий: {getattr(item, 'comment', '') or '—'}"
            for item in existing
        )
        changes.append(RegistryChange(
            kind=kind, day=day, rows=rows, amount=amount,
            existing_amount=old_amount if existing else None, existing_count=len(existing),
            action=action, counterparty_id=partner_id,
            counterparty_name=counterparty.name if counterparty else "",
            archived=bool(counterparty and counterparty.archived), slot=slot,
            invoiced=invoiced, due_date=due, missing_previous=missing,
            existing_details=details,
        ))
    changes.sort(key=lambda item: (item.day, KINDS.index(item.kind),
                                   item.counterparty_name.casefold(), item.slot or 0))
    plan = RegistryPlan(base_key, tuple(changes), tuple(issues), baseline_hash)
    if not issues:
        plan.candidate = _apply_plan(data, plan.changes)
        plan.has_changes = plan.candidate.to_dict() != data.to_dict()
    return plan


def _apply_plan(data: AppData, changes: tuple[RegistryChange, ...]) -> AppData:
    """Построить копию базы и историю; исходная база не меняется."""
    result = copy.deepcopy(data)
    targets = {change.key for change in changes}
    new_doc_keys = {_doc_key(row) for change in changes for row in change.rows}
    links = [link for link in result.imported_registry_rows
             if _link_target(link) not in targets and _link_key(link) not in new_doc_keys]
    for change in changes:
        old = _existing(result, change.key)
        ids: dict[int, str] = {}
        if change.kind in (SUPPLY, SALE):
            op_kind = KIND_INCOME if change.kind == SUPPLY else KIND_SALE
            if change.action == "same":
                if change.kind == SUPPLY:
                    for row in change.rows:
                        op = next(item for item in old if item.document == row.number)
                        ids[row.row_number] = op.id
                else:
                    ids.update({row.row_number: old[0].id for row in change.rows})
            else:
                result.operations = [op for op in result.operations if op not in old]
                if change.kind == SUPPLY:
                    for row in change.rows:
                        matches = [op for op in old if op.document == row.number]
                        if len(matches) == 1:
                            op = matches[0]
                        elif len(old) == 1 and len(change.rows) == 1 and not old[0].document:
                            op = old[0]
                        else:
                            op = Operation(kind=op_kind, counterparty_id=change.counterparty_id,
                                           date=change.day, due_date=change.due_date)
                        op.amount = f"{row.amount:.2f}"
                        op.document = row.number
                        if not op.due_date:
                            op.due_date = change.due_date
                        result.operations.append(op)
                        ids[row.row_number] = op.id
                else:
                    document = _sale_document(change.rows)
                    if len(old) == 1 and old[0].document in ("", document):
                        op = old[0]
                    else:
                        op = Operation(kind=op_kind, counterparty_id=change.counterparty_id,
                                       date=change.day)
                    op.amount = f"{change.amount:.2f}"
                    op.invoiced = bool(change.invoiced)
                    op.document = document
                    result.operations.append(op)
                    ids.update({row.row_number: op.id for row in change.rows})
        else:
            expected = KKM_KIND_REPORT if change.kind == REPORT else KKM_KIND_RETURN
            if change.action == "same":
                item = old[0]
            else:
                result.kkm_reports = [item for item in result.kkm_reports if item not in old]
                if len(old) == 1 and old[0].kind == expected:
                    item = old[0]
                    item.amount = f"{change.amount:.2f}"
                else:
                    item = KkmReport(date=change.day, slot=int(change.slot), kind=expected,
                                     amount=f"{change.amount:.2f}")
                result.kkm_reports.append(item)
            ids.update({row.row_number: item.id for row in change.rows})
        for row in change.rows:
            links.append({
                "kind": change.kind,
                "number": row.number,
                "date": change.day,
                "counterparty_id": change.counterparty_id,
                "slot": change.slot,
                "record_id": ids[row.row_number],
            })
    result.imported_registry_rows = sorted(
        links,
        key=lambda item: (str(item["kind"]), str(item["date"]),
                          str(item["number"]).casefold(), str(item["record_id"])),
    )
    return result


def _retains_structure(original: object, normalized: object) -> bool:
    """Не потерять поля новой версии или отброшенные повреждённые записи."""
    if isinstance(original, dict):
        return isinstance(normalized, dict) and all(
            key in normalized and _retains_structure(value, normalized[key])
            for key, value in original.items()
        )
    if isinstance(original, list):
        return (isinstance(normalized, list) and len(original) == len(normalized)
                and all(_retains_structure(a, b) for a, b in zip(original, normalized)))
    # Старые пустые поля можно дополнить стандартной структурой; непустое
    # скалярное значение вместо списка/объекта терять нельзя.
    return not isinstance(normalized, (dict, list)) or original in (None, "")


def capture_registry_snapshot(store: DataStore) -> RegistrySnapshot:
    """Не начинать предпросмотр поверх устаревшей или нечитаемой базы."""
    try:
        raw = store.path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        raw_links = payload.get("imported_registry_rows", [])
        if not isinstance(raw_links, list) or any(not isinstance(item, dict) for item in raw_links):
            raise ValueError
        disk_data = AppData.from_dict(payload)
        normalized = disk_data.to_dict()
        if payload.get("schema", 1) != 1 or not _retains_structure(payload, normalized):
            raise RegistryError(
                "База содержит неизвестные или повреждённые данные, которые "
                "эта версия не может сохранить без потерь. Загрузка отменена."
            )
        if normalized != store.data.to_dict():
            raise RegistryError(
                "Данные на диске отличаются от открытых. Завершите ввод и дождитесь "
                "синхронизации, затем откройте реестр заново."
            )
    except RegistryError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError, RecursionError) as exc:
        raise RegistryError("База недоступна или повреждена; реестр не загружен.") from exc
    return RegistrySnapshot(store.base_key, store.path, raw, _data_hash(store.data))


def _verify_snapshot(store: DataStore, snapshot: RegistrySnapshot,
                     guard: Callable[[], str | None] | None) -> None:
    if guard is not None:
        problem = guard()
        if problem:
            raise RegistryError(problem)
    if store.base_key != snapshot.base_key or store.path != snapshot.path:
        raise RegistryError("Активная база сменилась. Откройте реестр заново.")
    if _data_hash(store.data) != snapshot.baseline_hash:
        raise RegistryError("Данные изменились после предпросмотра. Откройте реестр заново.")
    try:
        same = store.path.read_bytes() == snapshot.content
    except OSError as exc:
        raise RegistryError("База недоступна; реестр не загружен.") from exc
    if not same:
        raise RegistryError("Файл базы изменился после предпросмотра. Откройте реестр заново.")


def _snapshot_backups(snapshot: RegistrySnapshot) -> tuple[Path, ...]:
    """Сохранить точные байты до импорта; старые копии не заменять и не удалять."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    token = secrets.token_hex(4)
    name = f"{snapshot.path.stem}-перед реестром-{token}-{stamp}.json"
    result: list[Path] = []
    for directory in backup_dirs():
        target = directory / name
        created = False
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, "wb") as stream:
                stream.write(snapshot.content)
                stream.flush()
                os.fsync(stream.fileno())
            if target.read_bytes() != snapshot.content:
                raise OSError("backup verification failed")
            result.append(target)
        except OSError:
            if created:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
    if not result:
        raise RegistryError("Не удалось создать проверенную копию базы. Загрузка отменена.")
    return tuple(result)


def commit_registry_plan(store: DataStore, snapshot: RegistrySnapshot, plan: RegistryPlan,
                         guard: Callable[[], str | None] | None = None) -> RegistryCommitResult:
    """Сохранить подтверждённый план атомарно; при ошибке не менять базу в памяти."""
    if plan.issues or plan.candidate is None:
        raise RegistryError("В плане есть ошибки; запись запрещена.")
    if plan.base_key != snapshot.base_key or plan.baseline_hash != snapshot.baseline_hash:
        raise RegistryError("План построен для другой версии базы. Откройте реестр заново.")
    _verify_snapshot(store, snapshot, guard)
    if not plan.has_changes:
        return RegistryCommitResult(False)
    backups = _snapshot_backups(snapshot)
    temporary: Path | None = None
    try:
        # Только временный файл и os.replace: в отличие от общего save()
        # прямой перезаписи при ошибке не делаем.
        fd, name = tempfile.mkstemp(prefix=f".{store.path.name}.registry-", suffix=".tmp",
                                    dir=store.path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            encoded = json.dumps(plan.candidate.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _verify_snapshot(store, snapshot, guard)
        os.replace(temporary, store.path)
        temporary = None
    except RegistryError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise RegistryError("Не удалось безопасно сохранить базу. Изменения не применены.") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    # Сохраняем идентичность AppData: открытые окна могут держать ссылку.
    store.data.__dict__.update(plan.candidate.__dict__)
    failed = False
    for callback in list(store.on_saved):
        try:
            callback()
        except Exception:
            failed = True
    return RegistryCommitResult(True, backups, failed)
