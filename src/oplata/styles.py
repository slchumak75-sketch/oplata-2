"""Цвета и стили оболочки — подобраны по скриншотам макетов.

Если пользователь пришлёт уточнённый дизайн, менять прежде всего здесь.
"""

from PySide6.QtWidgets import QAbstractItemView, QProxyStyle, QStyle

from .resources import assets_dir

# v0.39.0: шапка базы снова цветная и тёмная — МИР зелёная,
# КОРЕЛИЧИ вишнёвая (в v0.38.0 они были светлыми: песочной и
# карамельной). Текст, вкладки и кнопки на шапке — светлые.
BASE_GREEN = "#1f5c3a"   # зелёная шапка (база МИР)
BASE_MAROON = "#8e1b3f"  # вишнёвая шапка (база КОРЕЛИЧИ, v0.17.0)
BASE_TEXT = "#fffdf8"    # текст на цветной шапке
PAGE_BG = "#faf6ef"     # фон страницы под панелями
PANEL_BG = "#fffdf8"
BORDER = "#e6dccb"
TEXT = "#4a3f35"
MUTED = "#8a7b6b"
LINK_BLUE = "#a8542f"
RED = "#e02424"
GREEN = "#159947"
BLUE = "#c0714a"
# v0.27.6: рамка текущей даты во всех календарях (поле даты, карточка
# вида оплаты, календарь «Данные»). Раньше в «Данных» был этот же
# синий, а в полях даты рамки не было вовсе.
TODAY_FRAME = BLUE
WEEKEND_RED = "#e53935"     # красные ячейки выходных в календаре «Данные»
WORKDAY_GREY = "#f0e9dc"    # серые ячейки рабочих дней там же
WEEK_TABLE_MARKED_RED = "#ff0000"    # даты, отмеченные в карточке вида оплаты: ячейка красная (v0.24.2)
WEEK_TABLE_WEEKEND_PINK = "#fbe0d6"  # розовые колонки выходных/праздников
WEEK_TABLE_TODAY_GREEN = "#d9e7ce"   # салатовая верхняя ячейка текущей даты
# v0.38.0: весь столбец текущей даты — светлее шапки, иначе таблица
# «пестрит» (просьба пользователя: красить день на всю высоту).
WEEK_TABLE_TODAY_GREEN_BODY = "#eaf1e4"
# v0.29.0: метка «выписано» в узкой колонке таблицы покупателей.
# Красная — реализация ещё в работе, зелёная — документы выписаны.
# Красный тот же, что у отмеченных дат (просьба пользователя).
MARK_IN_WORK = "#ff0000"
MARK_INVOICED = "#22c55e"
TABLE_GRID = "#ebe3d6"      # линии сетки таблиц

# v0.28.2: стрелка выпадающего календаря для всех полей даты — своя
# картинка (см. правило QDateEdit::down-arrow ниже). Путь считается на
# ходу: из исходников и из собранной программы (sys._MEIPASS) он
# разный, а в QSS нужен готовый путь до файла.
DATE_ARROW_FILE = assets_dir() / "arrow-down.png"
DATE_ARROW_URL = DATE_ARROW_FILE.as_posix()

# Тёплая светлая тема «Тёплый песок» (v0.38.0) живёт на панелях и
# таблицах; шапка базы с v0.39.0 снова тёмная (зелёная/вишнёвая),
# поэтому текст и рамки на ней — светлые.
HEADER_TEXT = "#fffdf8"           # основной текст на шапке
HEADER_TEXT_MUTED = "#e6d7c3"     # подпись под названием
HEADER_CHECKED_BG = "#fffdf8"     # выбранная вкладка
HEADER_HOVER = "rgba(255,255,255,0.14)"
HEADER_TOOL_BG = "rgba(255,255,255,0.16)"
HEADER_TOOL_HOVER = "rgba(255,255,255,0.32)"
HEADER_TOOL_BORDER = "rgba(255,255,255,0.85)"
ACCENT = "#c0714a"                # терракота: акцент и кнопка базы
ACCENT_HOVER = "#a8542f"
SELECTION_BG = "#f6e7d6"    # фон выделенной строки

# Запрос пользователя v0.12.0: шрифты интерфейса на 30% больше.
# Базовый размер 13px -> 17px, остальные пересчитаны тем же множителем.
FONT_SCALE = 1.3


# Широкий, всегда видимый вертикальный скроллбар (v0.13.0: «закрепить
# и сделать широкий»). С v0.23.1 он один на всю программу: так же
# выглядит прокрутка в таблицах «Поставщики» и «Покупатели».
WIDE_SCROLLBAR = (
    "QScrollBar:vertical{background:#f7f2e9;width:22px;margin:0}"
    "QScrollBar::handle:vertical{background:#d9c9b0;border-radius:6px;"
    "min-height:40px;margin:2px 3px}"
    "QScrollBar::handle:vertical:hover{background:#c0a882}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{"
    "background:#f7f2e9}"
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
    "QTableWidget::item:focus,QTableView::item:focus{"
    f"background:{PANEL_BG};border:none;outline:none;"
    f"color:{TEXT}}}"
    # v0.29.1: при наведении рамку и контур гасим так же, а фон — НЕ
    # задаём. Раньше здесь было background:PANEL_BG, и под курсором
    # ячейка теряла свой цвет: метка «выписано» (красная/зелёная),
    # розовые выходные и салатовая текущая дата становились серыми.
    # Из-за этого окраска метки исчезала ещё до нажатия на неё.
    "QTableWidget::item:hover,QTableView::item:hover{"
    f"border:none;outline:none;color:{TEXT}}}"
    "QTableWidget QLineEdit,QTableView QLineEdit{"
    "border:none;outline:none;background:#fffdf8;"
    f"color:{TEXT};"
    f"selection-background-color:{SELECTION_BG};"
    f"selection-color:{TEXT};"
    "padding:0 4px}"
)

WIDE_SCROLLBAR_ON_AREA = (
    "QScrollArea{background:#fffdf8;border:none}"
    "QScrollArea > QWidget > QWidget{background:#fffdf8}"
    + WIDE_SCROLLBAR
    + TABLE_CELL_RULES
)
WIDE_SCROLLBAR_ON_TABLE = (
    # v0.24.3: outline:none — рамка фокуса виджета (пунктир по периметру
    # таблицы после клика) больше не рисуется.
    "QTableWidget{background:#fffdf8;outline:none;"
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
# v0.31.0: по скриншоту пользователя цифры в «Сводке» оказались
# слишком крупными — уменьшаем панель на 20% (1,5 × 0,8 = 1,2).
# v0.43.0: плашка «Сводка» стала ещё на строку выше (в блоке «Месяц»
# три строки вместо двух) и перестала влезать на экран 900 точек по
# высоте — уменьшаем кегль ещё на ~12% (1,2 × 0,88 ≈ 1,05), чтобы всё
# было видно сразу, без прокрутки.
SUMMARY_FONT_SCALE = 1.05


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
    background: #fffdf8;
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
       должна накладываться на последние цифры года.
       v0.28.2: кнопка вдвое шире (36 px вместо 18), поэтому запас
       под неё увеличен с 28 до 44 px. */
    padding-right: 44px;
}}
QDateEdit::drop-down {{
    /* v0.28.2: стрелка справа от даты вдвое шире (просьба
       пользователя) — по узкой кнопке неудобно попадать. */
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 36px;
    border-left: 1px solid {BORDER};
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QDateEdit::down-arrow {{
    /* v0.28.2: стрелку рисуем сами. Как только в QSS появляется
       правило для ::drop-down, Qt перестаёт рисовать нативную стрелку
       — именно поэтому её и убрали в v0.8.0 (ADR-015). Теперь кнопка
       вдвое шире, а стрелка — своей картинкой и вдвое крупнее
       нативной, чтобы она не пропала. */
    image: url("{DATE_ARROW_URL}");
    width: 20px;
    height: 12px;
}}
QPushButton {{
    background: #fffdf8;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #f7f2e9;
}}
QPushButton:disabled {{
    color: #a9977f;
    background: #f4eee3;
}}
QTableWidget {{
    background: #fffdf8;
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {TABLE_GRID};
    /* v0.27.0: без заливки — см. WIDE_SCROLLBAR_ON_TABLE. */
    selection-background-color: {PANEL_BG};
    selection-color: {TEXT};
}}
{TABLE_CELL_RULES}
QHeaderView::section {{
    background: #f4eee3;
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
