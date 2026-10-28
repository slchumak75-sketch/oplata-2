"""Печатные формы программы.

Этот модуль намеренно не зависит от Qt: он только превращает уже
посчитанные значения экрана в HTML. Саму печать и предпросмотр делает
``views/summary_view.py`` через ``QTextDocument``/``QPrinter``.

Важно: отчёт ничего не пересчитывает. Для «Сводки» он получает те же
объекты ``calc.SummaryTotals`` и строки поставщиков, которые видны на
экране, поэтому появление печати не меняет формулы.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import escape

from .calc import SummaryTotals
from .dates import fmt_date, fmt_money, fmt_money_plain


@dataclass(frozen=True)
class SummaryLine:
    """Одна строка построчной «Сводки» (подпись и уже посчитанное значение)."""

    title: str
    value: str


@dataclass(frozen=True)
class SummarySection:
    """Блок строк построчной «Сводки» (v0.14.0)."""

    title: str
    lines: list[SummaryLine]


@dataclass(frozen=True)
class SummaryReportRow:
    """Одна строка таблицы поставщиков в печатной «Сводке»."""

    supplier_name: str
    debt: Decimal
    overdue: Decimal
    next_due_date: date | None = None
    next_due_amount: Decimal | None = None


_KPI_LAYOUT = (
    (
        ("ВСЕГО ЗАДОЛЖЕННОСТЬ", "total_debt", False),
        ("ПРОСРОЧЕНО", "overdue", False),
        ("СРОК СЕГОДНЯ", "due_today", False),
    ),
    (
        ("СРОК В БЛИЖАЙШИЕ 7 ДНЕЙ", "due_next_7_days", False),
        ("ПРИХОД ЗА МЕСЯЦ", "income_month", False),
        ("ОПЛАЧЕНО ЗА МЕСЯЦ", "paid_month", True),
    ),
)


def _kpi_rows(totals: SummaryTotals) -> str:
    rows: list[str] = []
    for group in _KPI_LAYOUT:
        cells: list[str] = []
        for title, field_name, green in group:
            value = fmt_money(getattr(totals, field_name))
            value_class = "kpi-value paid" if green else "kpi-value"
            cells.append(
                "<td class=\"kpi-card\" width=\"33%\">"
                f"<div class=\"kpi-title\">{escape(title)}</div>"
                f"<div class=\"{value_class}\">{escape(value)}</div>"
                "</td>"
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(rows)


def _supplier_rows(rows: list[SummaryReportRow]) -> str:
    if not rows:
        return (
            "<tr><td colspan=\"5\" class=\"empty\">"
            "Поставщиков для выбранных условий нет."
            "</td></tr>"
        )

    rendered: list[str] = []
    for row in rows:
        due_date = fmt_date(row.next_due_date) if row.next_due_date else "—"
        due_amount = (
            fmt_money_plain(row.next_due_amount)
            if row.next_due_amount is not None
            else "—"
        )
        rendered.append(
            "<tr>"
            f"<td>{escape(row.supplier_name)}</td>"
            f"<td class=\"money\">{escape(fmt_money_plain(row.debt))}</td>"
            f"<td class=\"money\">{escape(fmt_money_plain(row.overdue))}</td>"
            f"<td class=\"date\">{escape(due_date)}</td>"
            f"<td class=\"money\">{escape(due_amount)}</td>"
            "</tr>"
        )
    return "".join(rendered)


def build_summary_report_html(
    *,
    on_date: date,
    totals: SummaryTotals,
    rows: Iterable[SummaryReportRow],
    overdue_count: int,
    include_archived: bool,
    generated_at: datetime | None = None,
    app_name: str = "Оплаты — Строймаркет",
    app_version: str | None = None,
) -> str:
    """Собрать HTML печатной формы экрана «Сводка».

    ``rows`` материализуется один раз, чтобы в отчёт попали ровно те же
    поставщики и в том же порядке, что показаны в экранной таблице.
    """

    materialized_rows = list(rows)
    generated = generated_at or datetime.now()
    generated_text = generated.strftime("%d.%m.%Y %H:%M")
    archived_text = "показаны" if include_archived else "скрыты"
    overdue_text = (
        "Просроченных оплат нет."
        if overdue_count == 0
        else f"Просрочено операций: {overdue_count}."
    )
    footer = f"Сформировано в программе «{escape(app_name)}»"
    if app_version:
        footer += f", версия {escape(app_version)}"
    footer += f" · {escape(generated_text)}"

    return f"""<!doctype html>
<html>
<head>
<meta charset=\"utf-8\">
<style>
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 10pt;
    color: #17233b;
  }}
  h1 {{
    font-size: 20pt;
    margin: 0 0 4px 0;
    color: #13294b;
  }}
  h2 {{
    font-size: 13pt;
    margin: 18px 0 7px 0;
    color: #13294b;
  }}
  .subtitle {{
    font-size: 11pt;
    font-weight: 700;
    margin-bottom: 3px;
  }}
  .meta {{
    color: #5b6a82;
    font-size: 8.5pt;
    margin-bottom: 12px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  .kpi-table {{
    margin-top: 4px;
  }}
  .kpi-card {{
    width: 33.33%;
    border: 1px solid #c9d4e6;
    padding: 8px 10px;
    vertical-align: top;
  }}
  .kpi-title {{
    color: #6b7a90;
    font-size: 7.5pt;
    font-weight: 700;
    margin-bottom: 4px;
  }}
  .kpi-value {{
    font-size: 15pt;
    font-weight: 700;
  }}
  .kpi-value.paid {{
    color: #087f3f;
  }}
  .data-table th {{
    background-color: #e9eef6;
    border: 1px solid #c9d4e6;
    padding: 6px 7px;
    text-align: left;
  }}
  .data-table td {{
    border: 1px solid #d7dfeb;
    padding: 5px 7px;
  }}
  .data-table .money {{
    text-align: right;
    white-space: nowrap;
  }}
  .data-table .date {{
    text-align: center;
    white-space: nowrap;
  }}
  .data-table .empty {{
    color: #5b6a82;
    text-align: center;
    padding: 14px;
  }}
  .overdue-table {{
    margin-top: 10px;
  }}
  .overdue-table td {{
    border: 1px solid #d7dfeb;
    padding: 9px 11px;
  }}
  .overdue-title {{
    font-weight: 700;
    margin-bottom: 3px;
  }}
  .footer {{
    color: #758399;
    font-size: 8pt;
    margin-top: 16px;
  }}
</style>
</head>
<body>
  <div class=\"subtitle\">{escape(app_name)}</div>
  <h1>Сводка на {escape(fmt_date(on_date))}</h1>
  <div class=\"meta\">
    Архивные поставщики: {archived_text} · поставщиков в таблице: {len(materialized_rows)}
  </div>

  <table class=\"kpi-table\">
    {_kpi_rows(totals)}
  </table>

  <h2>Задолженность по поставщикам</h2>
  <table class=\"data-table\">
    <thead>
      <tr>
        <th width=\"38%\">Поставщик</th>
        <th width=\"18%\">Задолженность</th>
        <th width=\"14%\">Просрочено</th>
        <th width=\"15%\">Ближайший срок</th>
        <th width=\"15%\">Сумма срока</th>
      </tr>
    </thead>
    <tbody>
      {_supplier_rows(materialized_rows)}
    </tbody>
  </table>

  <table class=\"overdue-table\" width=\"100%\">
    <tr>
      <td width=\"100%\">
        <div class=\"overdue-title\">Просроченные оплаты ({overdue_count})</div>
        <div>{escape(overdue_text)}</div>
      </td>
    </tr>
  </table>

  <div class=\"footer\">{footer}</div>
</body>
</html>
"""


def build_summary_sections_html(
    *,
    on_date: date,
    sections: Iterable[SummarySection],
    include_archived: bool,
    generated_at: datetime | None = None,
    app_name: str = "Оплаты — Строймаркет",
    app_version: str | None = None,
) -> str:
    """HTML печатной формы построчной «Сводки» (v0.14.0).

    Отчёт получает уже посчитанные строки — ровно те, что показаны на
    экране; ничего не пересчитывается.
    """

    materialized = list(sections)
    generated = generated_at or datetime.now()
    generated_text = generated.strftime("%d.%m.%Y %H:%M")
    archived_text = "показаны" if include_archived else "скрыты"
    footer = f"Сформировано в программе «{escape(app_name)}»"
    if app_version:
        footer += f", версия {escape(app_version)}"
    footer += f" · {escape(generated_text)}"

    blocks: list[str] = []
    for section in materialized:
        rows = "".join(
            "<tr>"
            f"<td>{escape(line.title)}</td>"
            f"<td class=\"money\">{escape(line.value)}</td>"
            "</tr>"
            for line in section.lines
        )
        blocks.append(
            f"<h2>{escape(section.title)}</h2>"
            f"<table class=\"lines-table\">{rows}</table>"
        )

    return f"""<!doctype html>
<html>
<head>
<meta charset=\"utf-8\">
<style>
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 11pt;
    color: #17233b;
  }}
  h1 {{
    font-size: 20pt;
    margin: 0 0 4px 0;
    color: #13294b;
  }}
  h2 {{
    font-size: 13pt;
    margin: 16px 0 6px 0;
    color: #13294b;
  }}
  .meta {{
    color: #5b6a82;
    font-size: 9pt;
    margin-bottom: 10px;
  }}
  .lines-table {{
    width: 100%;
    border-collapse: collapse;
  }}
  .lines-table td {{
    border: 1px solid #d7dfeb;
    padding: 5px 8px;
  }}
  .lines-table .money {{
    text-align: right;
    white-space: nowrap;
    font-weight: 700;
    width: 35%;
  }}
  .footer {{
    margin-top: 16px;
    font-size: 8.5pt;
    color: #5b6a82;
  }}
</style>
</head>
<body>
  <h1>Сводка</h1>
  <div class=\"meta\">
    Сводка на {escape(fmt_date(on_date))} ·
    Архивные поставщики: {escape(archived_text)}
  </div>
  {''.join(blocks)}
  <div class=\"footer\">{footer}</div>
</body>
</html>"""
