"""Тесты печатных форм (чистый HTML, без Qt и принтера)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from oplata.calc import SummaryTotals
from oplata.reports import SummaryReportRow, build_summary_report_html


def test_summary_report_html_contains_visible_snapshot():
    html = build_summary_report_html(
        on_date=date(2026, 10, 27),
        totals=SummaryTotals(
            total_debt=Decimal("88888.88"),
            overdue=Decimal("1000.50"),
            due_today=Decimal("2000"),
            due_next_7_days=Decimal("3000.25"),
            income_month=Decimal("4000"),
            paid_month=Decimal("5000.75"),
        ),
        rows=[
            SummaryReportRow(
                supplier_name="Поставщик <А&Б>",
                debt=Decimal("88888.88"),
                overdue=Decimal("1000.50"),
                next_due_date=date(2026, 11, 5),
                next_due_amount=Decimal("2000"),
            )
        ],
        overdue_count=2,
        include_archived=False,
        generated_at=datetime(2026, 10, 27, 18, 45),
        app_name="Оплаты — Строймаркет",
        app_version=".".join(("0", "5", "0")),
    )

    assert "Сводка на 27.10.2026" in html
    assert "88 888,88 руб." in html
    assert "1 000,50 руб." in html
    assert "5 000,75 руб." in html
    assert "kpi-value paid" in html
    assert "Поставщик &lt;А&amp;Б&gt;" in html
    assert "Поставщик <А&Б>" not in html
    assert "05.11.2026" in html
    assert "2 000,00" in html
    assert "Архивные поставщики: скрыты" in html
    assert "поставщиков в таблице: 1" in html
    assert "Просроченные оплаты (2)" in html
    assert "Просрочено операций: 2." in html
    assert "27.10.2026 18:45" in html
    assert "версия 0.5.0" in html


def test_summary_report_html_empty_state():
    html = build_summary_report_html(
        on_date=date(2026, 10, 27),
        totals=SummaryTotals(),
        rows=[],
        overdue_count=0,
        include_archived=True,
        generated_at=datetime(2026, 10, 27, 9, 0),
    )

    assert "Поставщиков для выбранных условий нет." in html
    assert "Архивные поставщики: показаны" in html
    assert "поставщиков в таблице: 0" in html
    assert "Просроченных оплат нет." in html
    assert html.count("0,00 руб.") == 6
