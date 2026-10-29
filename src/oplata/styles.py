"""Цвета и стили оболочки — подобраны по скриншотам макетов.

Если пользователь пришлёт уточнённый дизайн, менять прежде всего здесь.
"""

NAVY = "#0f2a5c"        # тёмно-синяя шапка (база МИР)
MAROON = "#8e1b3f"      # вишнёвая шапка (база КОРЕЛИЧИ, v0.17.0)
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

# Запрос пользователя v0.12.0: шрифты интерфейса на 30% больше.
# Базовый размер 13px -> 17px, остальные пересчитаны тем же множителем.
FONT_SCALE = 1.3


# Широкий, всегда видимый вертикальный скроллбар (v0.13.0: «закрепить
# и сделать широкий»). С v0.23.1 он один на всю программу: так же
# выглядит прокрутка в таблицах «Поставщики» и «Покупатели».
WIDE_SCROLLBAR = (
    "QScrollBar:vertical{background:#f2f5fa;width:22px;margin:0}"
    "QScrollBar::handle:vertical{background:#b9c4d6;border-radius:6px;"
    "min-height:40px;margin:2px 3px}"
    "QScrollBar::handle:vertical:hover{background:#93a3bd}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{"
    "background:#f2f5fa}"
)


def fs(base_px: int) -> int:
    """Размер шрифта, увеличенный на 30% (v0.12.0)."""

    return int(round(base_px * FONT_SCALE))


APP_STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: {fs(13)}px;
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
    font-size: {fs(15)}px;
    font-weight: 700;
}}
QLabel#KpiTitle {{
    font-size: {fs(11)}px;
    color: {MUTED};
    font-weight: 600;
}}
QLabel#KpiValue {{
    font-size: {fs(20)}px;
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
