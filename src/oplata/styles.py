"""Цвета и стили оболочки — подобраны по скриншотам макетов.

Если пользователь пришлёт уточнённый дизайн, менять прежде всего здесь.
"""

from PySide6.QtWidgets import QAbstractItemView, QProxyStyle, QStyle

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
WEEK_TABLE_MARKED_RED = "#ff0000"    # даты, отмеченные в карточке вида оплаты: ячейка красная (v0.24.2)
WEEK_TABLE_WEEKEND_PINK = "#ffe1e8"  # розовые колонки выходных/праздников
WEEK_TABLE_TODAY_GREEN = "#ccf5a3"   # салатовая верхняя ячейка текущей даты
TABLE_GRID = "#e3e8f0"      # линии сетки таблиц
SELECTION_BG = "#dbeafe"    # фон выделенной строки

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


# ВАЖНО (v0.23.1): виджет, которому задана своя таблица стилей,
# становится корнем новой области стилей Qt — правила APP_STYLESHEET для
# него самого и его детей перестают действовать. Поэтому везде, где к
# виджету применяется WIDE_SCROLLBAR, белый фон (а у таблицы — рамку и
# сетку) приходится задавать заново: иначе вместо белого проглядывает
# серый фон по умолчанию. Так и было в окнах «Покупатели»/«Поставщики»
# после появления в них вертикальной прокрутки.
# v0.26.6: правила, гасящие рамку фокуса на текущей ячейке и рамку
# поля ввода. Их нужно повторять в **каждом** QSS, который применяется
# к таблице отдельно: виджет со своей таблицей стилей становится корнем
# новой области стилей Qt, и правила APP_STYLESHEET на него и на его
# детей перестают действовать. Из-за этого с v0.23.1 (появление
# WIDE_SCROLLBAR_ON_TABLE) на таблицах «Оплата», «Поступление»,
# «Покупатели», «Поставщики» после клика появлялась системная синяя
# рамка на нажатой ячейке — «артефакт».
TABLE_CELL_RULES = (
    # v0.26.8: ячейка после клика **не меняет вид**: ни фон выделения,
    # ни рамка, ни контур. Иначе на нажатой ячейке (в блоках
    # «Оплата»/«Поступление» — по всей строке, там выделяется строка)
    # проявляется системный синий прямоугольник — «артефакт», который
    # переезжает на каждую следующую нажатую ячейку.
    # Обычное состояние не трогаем: фон ячейки задаёт сама таблица
    # (розовые выходные, салатовая дата, красные отметки), и QSS здесь
    # перекрыл бы его.
    "QTableWidget::item:selected,QTableView::item:selected,"
    "QTableWidget::item:selected:focus,QTableView::item:selected:focus,"
    "QTableWidget::item:focus,QTableView::item:focus,"
    "QTableWidget::item:hover,QTableView::item:hover{"
    f"background:{PANEL_BG};border:none;outline:none;"
    f"color:{TEXT}}}"
    "QTableWidget QLineEdit,QTableView QLineEdit{"
    "border:none;outline:none;background:#ffffff;"
    f"color:{TEXT};"
    f"selection-background-color:{SELECTION_BG};"
    f"selection-color:{TEXT};"
    "padding:0 4px}"
)

WIDE_SCROLLBAR_ON_AREA = (
    "QScrollArea{background:#ffffff;border:none}"
    "QScrollArea > QWidget > QWidget{background:#ffffff}"
    + WIDE_SCROLLBAR
    + TABLE_CELL_RULES
)
WIDE_SCROLLBAR_ON_TABLE = (
    # v0.24.3: outline:none — рамка фокуса виджета (пунктир по периметру
    # таблицы после клика) больше не рисуется.
    "QTableWidget{background:#ffffff;outline:none;"
    f"border:1px solid {BORDER};border-radius:8px;"
    f"gridline-color:{TABLE_GRID};"
    # v0.27.0: выделение строки не заливается цветом — подсветка
    # всей строки после клика выглядела как артефакт.
    f"selection-background-color:{PANEL_BG};"
    f"selection-color:{TEXT}}}"
    + WIDE_SCROLLBAR
    + TABLE_CELL_RULES
)


class NoFocusFrameStyle(QProxyStyle):
    """Стиль без рамки фокуса на ячейках таблиц (v0.26.4).

    После клика Qt рисует рамку фокуса на **текущей** ячейке
    (``PE_FrameFocusRect``) — она и выглядела как артефакт: две
    вертикальные синие линии, которые переезжают на каждую следующую
    нажатую ячейку. Рамку рисует не делегат, а сам стиль, поэтому
    снятие ``State_HasFocus`` в делегате (v0.24.3) её не убирало.
    На остальных виджетах (кнопки, поля) рамка фокуса остаётся.
    """

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        # v0.26.9: рамку фокуса не рисуем нигде. Сначала её гасили
        # только на таблицах (и с проверкой виджета), но штрихи по краям
        # нажатой ячейки оставались: ячейку рисует делегат, а он
        # вызывает стиль и без виджета, и с viewport.
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
            return
        super().drawPrimitive(element, option, painter, widget)


def fs(base_px: int) -> int:
    """Размер шрифта, увеличенный на 30% (v0.12.0)."""

    return int(round(base_px * FONT_SCALE))


# v0.23.6: шрифт **панели «Сводка»** — на 50% крупнее обычного
# (просьба пользователя: увеличить шрифт именно в этой области, а не во
# всей программе, как было ошибочно сделано в v0.23.2).
SUMMARY_FONT_SCALE = 1.5


def fs_summary(base_px: int) -> int:
    """Кегль панели «Сводка»: обычный ``fs`` + 50%."""

    return int(round(base_px * FONT_SCALE * SUMMARY_FONT_SCALE))


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
    gridline-color: {TABLE_GRID};
    /* v0.27.0: без заливки — см. WIDE_SCROLLBAR_ON_TABLE. */
    selection-background-color: {PANEL_BG};
    selection-color: {TEXT};
}}
{TABLE_CELL_RULES}
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
