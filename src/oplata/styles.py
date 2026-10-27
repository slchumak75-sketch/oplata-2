"""Цвета и стили оболочки — подобраны по скриншотам макетов.

Если пользователь пришлёт уточнённый дизайн, менять прежде всего здесь.
"""

NAVY = "#0f2a5c"        # тёмно-синяя шапка
NAVY_TEXT = "#ffffff"
PAGE_BG = "#eef1f6"     # фон страницы под панелями
PANEL_BG = "#ffffff"
BORDER = "#d5dbe7"
TEXT = "#1c2733"
MUTED = "#6b7686"
LINK_BLUE = "#1a56db"
RED = "#e02424"
GREEN = "#159947"
BLUE = "#2563eb"
WEEKEND_RED = "#e53935"     # красные ячейки выходных в календаре «Данные»
WORKDAY_GREY = "#eceff3"    # серые ячейки рабочих дней там же
WEEK_TABLE_WEEKEND_PINK = "#ffe1e8"  # розовые колонки выходных/праздников
WEEK_TABLE_TODAY_GREEN = "#ccf5a3"   # салатовая верхняя ячейка текущей даты

APP_STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QWidget#PageRoot {{
    background: {PAGE_BG};
}}
QFrame#Panel {{
    background: {PANEL_BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#KpiCard {{
    background: {PANEL_BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel#PanelTitle {{
    font-size: 15px;
    font-weight: 700;
}}
QLabel#KpiTitle {{
    font-size: 11px;
    color: {MUTED};
    font-weight: 600;
}}
QLabel#KpiValue {{
    font-size: 20px;
    font-weight: 800;
}}
QLabel#Muted {{
    color: {MUTED};
}}
QLineEdit, QComboBox, QDateEdit, QSpinBox {{
    background: #ffffff;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px 8px;
    min-height: 22px;
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{
    border: 1px solid {BLUE};
}}
QDateEdit {{
    /* Место справа зарезервировано под кнопку календаря: стрелка не
       должна накладываться на последние цифры года. */
    padding-right: 28px;
}}
QPushButton {{
    background: #ffffff;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #f2f5fa;
}}
QPushButton:disabled {{
    color: #a3abb8;
    background: #f3f5f8;
}}
QTableWidget {{
    background: #ffffff;
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: #e3e8f0;
    selection-background-color: #dbeafe;
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background: #eef2f8;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: 700;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QCheckBox {{
    spacing: 6px;
}}
"""
