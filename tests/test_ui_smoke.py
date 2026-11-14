"""Smoke-тесты интерфейса (headless, QT_QPA_PLATFORM=offscreen в CI).

Проверяем построение окна и главные пользовательские сценарии:
ввод оплаты, поставку и долг, блоки «Поступление»/«Оплата», CRUD
операций в окне журнала контрагента, карточки.
"""

import os
import re
import sys
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFileDialog,
    QMessageBox,
)


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def store(tmp_path):
    from oplata.storage import DataStore

    return DataStore(tmp_path / "data.json")


def week_row(view, name: str) -> int:
    """Номер строки контрагента в недельной таблице.

    v0.55.0: таблица поставщиков и покупателей выводится **по алфавиту**,
    поэтому номер строки нельзя считать по порядку в данных — берём его
    из самого представления.
    """

    return 4 + [c.name for c in view.counterparties()].index(name)


def patch_other_payment_card(monkeypatch, names=(), vat_rate="", unp="",
                            note=""):
    """Подменить карточку нового вида оплаты (v0.20.0).

    Сама карточка — модальный диалог: в headless-прогоне ``exec()``
    задел бы цикл событий и тест повис. Подмена возвращает имя из
    очереди (а дальше — как прежде: «Новый вид оплаты», «… 2», …),
    дату блока и нулевую сумму. ``vat_rate`` — ставка НДС, которую
    карточка «вводит» для каждого создаваемого вида (v0.33.0);
    ``unp`` — УНП вида (v0.55.0); ``note`` — примечание (v0.60.0).
    """
    from decimal import Decimal

    from PySide6.QtWidgets import QDialog

    from oplata.views import receipts_view as receipts_view_module

    queue = list(names)

    class FakeCard:
        """Карточка без показа: имя из очереди, дата и сумма не нужны."""

        def __init__(
            self,
            direction,
            used_names,
            day,
            parent=None,
            name="",
            dates=(),
            vat_rate="",
            unp="",
            note="",
        ):
            self._note = note
            if queue:
                self._name = queue.pop(0)
            else:
                base = "Новый вид оплаты"
                name = base
                index = 2
                while name in used_names:
                    name = f"{base} {index}"
                    index += 1
                self._name = name

        def exec(self):
            return QDialog.DialogCode.Accepted

        @property
        def name_value(self):
            return self._name

        @property
        def marked_dates(self):
            """Календаря в подменной карточке нет — дат не отмечаем."""
            return []

        @property
        def vat_value(self):
            """Ставка НДС из карточки (v0.33.0)."""

            return vat_rate

        @property
        def note_value(self):
            """Примечание вида оплаты из карточки (v0.60.0)."""

            return note

        @property
        def unp_value(self):
            """УНП вида оплаты из карточки (v0.55.0)."""

            return unp

    monkeypatch.setattr(receipts_view_module, "OtherPaymentCardDialog", FakeCard)


def test_main_window_all_pages(qt_app, store):
    from oplata.app import MainWindow

    window = MainWindow(store)
    assert [button.text() for button in window.nav_buttons] == [
        "Поступление и оплаты",
        "Отчеты ККМ",  # v0.42.0: между «Поступление…» и «Поставщики»
        "Поставщики",
        "Покупатели",
        "Конфигурация",  # v0.14.0: прежняя вкладка «Данные»
    ]
    # v0.42.0: пять вкладок — добавились «Отчеты ККМ».
    assert window.stack.count() == 5
    # Вкладка «Поступление и оплаты»: два блока один под другим, в каждом
    # таблица с колонками дней недели (v0.38.0: колонки «Сумма» нет).
    view = window.receipts_view
    # v0.70.0: названия строк набраны заглавными буквами.
    assert [view.incoming.table.item(row, 0).text() for row in range(4)] == [
        "ПОСТУПЛЕНИЕ НАЛИЧНЫМИ",
        "ПОСТУПЛЕНИЕ ПО КАРТАМ",
        "ПОКУПАТЕЛИ",
        "ПРОЧИЕ ПОСТУПЛЕНИЯ",  # постоянная строка, v0.13.0
    ]
    assert view.outgoing.table.item(0, 0).text() == "ПОСТАВЩИКИ"
    # v0.22.0: вместо «Неделя/Месяц/Квартал/Год» — колонки дней недели.
    # v0.33.0: вторым столбцом стоит «НДС» (в блоке «Поступление»
    # колонка скрыта — виды поступлений своей ставки не имеют).
    # v0.60.0: перед НДС стоит «Примечание» (в обоих блоках).
    assert [
        view.outgoing.table.horizontalHeaderItem(c).text()
        for c in range(COLUMN_COUNT)
    ][:3] == ["", "Примечание", "НДС"]
    assert view.incoming.table.isColumnHidden(COL_VAT)
    assert not view.outgoing.table.isColumnHidden(COL_VAT)
    assert view.incoming.days == view.incoming.week_days()
    assert len(view.incoming.days) == TABLE_DAYS
    # Кнопок «+»/«−» в блоке «Поступление» нет, в «Оплате» — есть.
    assert not hasattr(view.incoming, "add_btn")
    assert view.outgoing.add_btn.text() == "+"
    assert view.outgoing.remove_btn.text() == "−"
    # Блок «Оплата» стоит под блоком «Поступление» (в левой колонке).
    layout = view.left_layout
    assert layout.indexOf(view.outgoing) > layout.indexOf(view.incoming)
    # «Сводка» — панель справа на этой же вкладке, отдельной вкладки нет.
    assert view.summary_widget is window.summary_view
    assert window.stack.indexOf(window.summary_view) == -1
    # Кнопки карточек перенесены из шапки в панели таблиц.
    assert window.suppliers_view.card_button.text() == "Карточка поставщика"
    assert window.customers_view.card_button.text() == "Карточка покупателя"
    assert not hasattr(window, "operations_view")
    for index in range(4):
        # v0.18.0: «Конфигурация» открывается по паролю — в тесте
        # считаем, что пароль уже введён (иначе встанет модальный диалог).
        window._config_unlocked = True  # noqa: SLF001 - доступ к флагу окна
        window.show_page(index)
        assert window.stack.currentIndex() == index
    # Сводка — построчная плашка из трёх блоков (v0.14.0)
    summary = window.summary_view
    assert summary.caption_text("funds") == "Остаток на счёте"
    assert summary.caption_text("debt_total") == "Всего задолженность"
    # v0.43.0: в блоке «Месяц» — итоги реестра ККМ, а не приход и оплаты.
    # v0.44.0: в блоке три строки — ККМ за вычетом возвратов,
    # реализация и общий итог; отдельной строки возвратов больше нет.
    assert summary.caption_text("month_kkm_total") == "Итого по Отчетам ККМ"
    assert summary.caption_text("month_sale_invoiced") == "Реализация Покупателям"
    assert summary.caption_text("month_total") == "Итого за месяц"
    assert "month_kkm_returns" not in summary._values
    assert summary.caption_text("plan_1").startswith("Оплата ")
    # В покупателях справа «0»
    assert window.customers_view.right_value_label.text() == "0"
    window.close()


def test_week_table_income_and_payment_input(qt_app, store):
    """Прямой ввод; салатовая только ячейка даты, выходные — розовые."""
    from datetime import date

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyleOptionViewItem

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.views.week_view import FIRST_DATA_ROW, TOTAL_ROW
    from oplata.widgets import AmountLineEdit

    window = MainWindow(store)
    view = window.suppliers_view
    view.goto_date(date(2026, 10, 26))
    holiday = date(2026, 10, 30)       # помеченный в «Данных» праздник в пятницу
    saturday = date(2026, 10, 31)       # помеченный рабочий перенос
    today = date(2026, 10, 27)
    store.data.day_overrides.update(
        {
            holiday.isoformat(): "off",
            saturday.isoformat(): "work",
            today.isoformat(): "off",  # даже так красим только поле «27 вт»
        }
    )
    view._rebuild_table(view._partners, view._days, today)
    table = view.table
    # Номера колонок дней считаем от числа итоговых столбцов: их три
    # («на сегодня»/«Итого», «Неделя», «Просрочено», v0.16.0).
    income = table.item(FIRST_DATA_ROW, _DAY(view, 0, 0))    # пн, Приход
    debt = table.item(FIRST_DATA_ROW, _DAY(view, 0, 1))      # пн, Долг
    payment = table.item(FIRST_DATA_ROW, _DAY(view, 0, 2))   # пн, Оплата
    assert income.flags() & Qt.ItemFlag.ItemIsEditable
    assert payment.flags() & Qt.ItemFlag.ItemIsEditable
    assert not (debt.flags() & Qt.ItemFlag.ItemIsEditable)
    editor = table.itemDelegate().createEditor(
        table.viewport(),
        QStyleOptionViewItem(),
        table.model().index(FIRST_DATA_ROW, _DAY(view, 0, 0)),
    )
    assert isinstance(editor, AmountLineEdit)  # нули и в редакторе ячейки не мешают
    editor.deleteLater()

    def bg_name(row, col):
        brush = table.item(row, col).background()
        return None if brush.style() == Qt.BrushStyle.NoBrush else brush.color().name()

    # v0.38.0: колонка текущего дня — на всю высоту: в шапке свой
    # зелёный, в теле таблицы — светлее (просьба пользователя).
    assert bg_name(1, _DAY(view, 1, 0)) == styles.WEEK_TABLE_TODAY_GREEN
    assert bg_name(2, _DAY(view, 1, 0)) == styles.WEEK_TABLE_TODAY_GREEN_BODY
    assert bg_name(TOTAL_ROW, _DAY(view, 1, 0)) == styles.WEEK_TABLE_TODAY_GREEN_BODY
    assert (
        bg_name(FIRST_DATA_ROW, _DAY(view, 1, 0))
        == styles.WEEK_TABLE_TODAY_GREEN_BODY
    )
    assert bg_name(1, _DAY(view, 4, 0)) == styles.WEEK_TABLE_WEEKEND_PINK  # праздник
    assert bg_name(FIRST_DATA_ROW, _DAY(view, 4, 0)) == styles.WEEK_TABLE_WEEKEND_PINK
    assert bg_name(FIRST_DATA_ROW, _DAY(view, 5, 0)) is None  # рабочая суббота
    # v0.18.0: воскресенье из таблицы убрали — колонок дней шесть.
    assert view.table_days == 6
    # v0.27.3: + узкая колонка условий поставщика (lead_offset).
    assert table.columnCount() == (
        1 + view.lead_offset + view.lead_columns + 6 * view.columns_per_day
    )
    assert bg_name(FIRST_DATA_ROW, 3) is None  # обычный понедельник без заливки
    # v0.28.2: кнопка календаря вдвое шире — минимум вырос со 138.
    assert view.date_edit.minimumWidth() >= 158
    assert view.date_edit.calendarPopup()
    # v0.28.2: кнопка шире, но стрелка на месте — свою стрелку задаём
    # картинкой (нативную Qt прячет, если в QSS есть ::drop-down,
    # см. ADR-015 и test_date_edit_arrow_image_is_shipped).
    assert "QDateEdit::drop-down" in styles.APP_STYLESHEET
    assert "QDateEdit::down-arrow" in styles.APP_STYLESHEET

    monday = view._days[0]
    income.setText("300")  # программная правка = тот же сигнал, что от Enter
    assert [(op.kind, op.date, op.due_date, op.amount) for op in store.data.operations] == [
        ("Приход", monday.isoformat(), monday.isoformat(), "300.00")
    ]
    assert income.text() == "300,00"
    assert debt.text() == "300,00"  # у первого поставщика отсрочка 0
    assert table.item(TOTAL_ROW, _DAY(view, 0, 0)).text() == "300,00"

    payment.setText("200")
    assert [op.kind for op in store.data.operations] == ["Приход", "Оплата"]
    assert payment.text() == "200,00"  # переформатировано после сохранения
    assert table.item(TOTAL_ROW, _DAY(view, 0, 2)).text() == "200,00"  # ВСЕГО
    # Блок «Оплата» вкладки «Поступление и оплаты» видит итог этого дня.
    window.receipts_view.outgoing.set_date(monday)
    assert window.receipts_view.outgoing.amount_text("suppliers") == "200,00"

    payment.setText("")  # очистка ячейки удаляет только операцию оплаты
    assert [op.kind for op in store.data.operations] == ["Приход"]
    income.setText("0")
    assert store.data.operations == []
    window.close()


def test_delivery_dialog_saves_and_debt_shows_in_week(qt_app, store):
    """Окно «Поставка»: срок = дата + отсрочка, сумма встаёт в «Долг» на срок."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.dialogs.delivery_dialog import DeliveryDialog

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    assert supplier.deferment_days == 10  # сид с макета карточки
    dialog = DeliveryDialog(store, supplier.id, preset_date=date(2026, 10, 26))
    assert dialog.amount_edit.text() == ""  # нули не надо стирать перед вводом
    assert dialog.conditions_label.text() == "Условия: Отсрочка: 10 дн."
    assert dialog.due_label.text() == "05.11.2026"  # 26.10 + 10 дней
    dialog.amount_edit.setText("88 888,88")  # максимальное число от пользователя
    dialog._save()
    assert dialog.saved_due_date == date(2026, 11, 5)
    (op,) = store.data.operations
    assert (op.kind, op.date, op.due_date, op.amount) == (
        "Приход", "2026-10-26", "2026-11-05", "88888.88",
    )
    dialog.close()

    # Предоплата (как на скрине «eee»): срок = дата поставки
    eee = next(s for s in store.data.suppliers if s.name == "eee")
    eee.prepayment = True
    prepaid = DeliveryDialog(store, eee.id, preset_date=date(2026, 10, 27))
    assert prepaid.conditions_label.text() == "Условия: Предоплата"
    assert prepaid.due_label.text() == "27.10.2026"
    assert "отсрочка не применяется" in prepaid.due_reason_label.text()
    prepaid.close()

    # Таблица показывает долг в неделе срока: 05.11 — чт (день №3 от пн 02.11)
    window = MainWindow(store)
    window.suppliers_view.goto_date(date(2026, 11, 5))
    table = window.suppliers_view.table
    # v0.55.0: таблица отсортирована по алфавиту, поэтому строку ищем
    # в самом представлении, а не по порядку в данных.
    ggg_row = week_row(window.suppliers_view, "ггг")
    table_lead_offset = window.suppliers_view.lead_offset
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "88 888,88"  # Долг чт
    # v0.27.3: итоговые колонки сдвинуты на узкую колонку условий
    # («П»/«Р»/отсрочка), поэтому их номера считаем от lead_offset.
    week_col = 3 + table_lead_offset
    assert table.item(ggg_row, week_col).text() == "88 888,88"  # за неделю
    assert table.item(3, week_col).text() == "88 888,88"  # ВСЕГО за неделю
    assert window.suppliers_view.right_value_label.text() == "88 888,88 руб."

    # v0.41.0: оплата НЕ уменьшает «Долг» в ячейке дня — сумма там
    # архивная (просьба пользователя). Результат оплаты виден только
    # в «Итого»/«Просрочено»/«Неделя».
    payment_cell = table.item(ggg_row, _DAY(window.suppliers_view, 3, 2))  # Оплата чт 05.11
    payment_cell.setText("88 888,88")
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "88 888,88"  # долг архивный
    assert table.item(ggg_row, week_col).text() == ""  # «за неделю» погашено
    assert table.item(3, week_col).text() == ""  # ВСЕГО за неделю
    assert window.suppliers_view.right_value_label.text() == "0,00 руб."
    window.close()


def test_operations_journal_shows_name_and_minimizes_previous(qt_app, store):
    """v0.27.3: имя контрагента в шапке журнала, прошлый журнал свёрнут.

    Несколько журналов легко перепутать, поэтому в шапке стоит имя
    контрагента, а ранее открытый журнал сворачивается — иначе окна
    перекрывают друг друга.
    """

    from PySide6.QtWidgets import QLabel

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.suppliers[:] = [
        Counterparty(name="Первый"),
        Counterparty(name="Второй"),
    ]
    window = MainWindow(store)
    first_id, second_id = (item.id for item in store.data.suppliers)

    def caption(dialog) -> list[str]:
        return [label.text() for label in dialog.findChildren(QLabel)]

    window.open_operations_journal("supplier", first_id)
    qt_app.processEvents()
    first = window._journal_dialogs[-1]
    assert "Первый" in caption(first)
    assert not first.isMinimized()

    # Второй журнал: предыдущий сворачивается, новый — на экране.
    window.open_operations_journal("supplier", second_id)
    qt_app.processEvents()
    second = window._journal_dialogs[-1]
    assert "Второй" in caption(second)
    assert not second.isMinimized()
    assert first.isMinimized()  # v0.27.3: прошлый журнал свёрнут
    window.close()


def test_all_calendars_frame_today(qt_app, store):
    """v0.27.6: текущая дата в рамке во всех календарях программы.

    Кроме всплывающего календаря поля даты (его проверяет соседний
    тест по пикселям) это календарь в карточке вида оплаты и
    календарь выходных на вкладке «Данные».
    """

    from datetime import date

    from PySide6.QtWidgets import QPushButton

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.views.receipts_view import PaymentDatesCalendar

    today = date.today()
    frame = f"2px solid {styles.TODAY_FRAME}"

    # Календарь в карточке вида оплаты: кнопка нужного дня.
    payment_calendar = PaymentDatesCalendar(today)
    buttons = [
        button
        for button in payment_calendar.findChildren(QPushButton)
        if button.text() == str(today.day)
    ]
    assert buttons, "кнопка текущего дня в календаре не найдена"
    assert frame in buttons[0].styleSheet()
    # Отметка даты не отменяет рамку: правила складываются.
    buttons[0].click()
    qt_app.processEvents()
    assert frame in buttons[0].styleSheet()
    payment_calendar.deleteLater()

    # Календарь выходных на вкладке «Данные».
    window = MainWindow(store)
    data_view = window.data_view
    assert today in data_view._day_buttons
    assert frame in data_view._day_buttons[today].styleSheet()
    window.close()


def test_calendar_marks_today_with_frame(qt_app, store):
    """v0.27.5: в календаре текущая дата обведена рамкой.

    Проверка по пикселям: рамка рисуется общим для календарей
    цветом, поэтому в снимке календаря должны быть его пиксели.
    """

    from datetime import date

    from PySide6.QtCore import QDate

    from oplata import styles
    from oplata.widgets import TodayFrameCalendar, make_date_edit

    frame_color = styles.TODAY_FRAME.lstrip("#")
    target = (
        int(frame_color[0:2], 16),
        int(frame_color[2:4], 16),
        int(frame_color[4:6], 16),
    )

    calendar = TodayFrameCalendar()
    calendar.setSelectedDate(QDate.currentDate())
    calendar.resize(320, 220)
    calendar.show()
    qt_app.processEvents()

    image = calendar.grab().toImage()
    found = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if (image.pixelColor(x, y).red(), image.pixelColor(x, y).green(),
            image.pixelColor(x, y).blue()) == target
    ]
    assert found, "рамка текущей даты в календаре не нарисована"
    # Рамка — контур, а не заливка: пикселей много, но не весь снимок.
    assert len(found) < image.width() * image.height() // 4

    # Поле даты «Сводка на дату» использует тот же календарь.
    edit = make_date_edit(date.today())
    assert isinstance(edit.calendarWidget(), TodayFrameCalendar)
    calendar.close()
    edit.deleteLater()


def test_external_data_change_is_pulled(qt_app, store):
    """v0.28.0: изменения из общей папки подтягиваются без перезапуска.

    Схема пользователя: данные лежат в общей папке Google Диска,
    меняет их компьютер администратора, остальные только смотрят —
    они должны видеть изменения, не перезапуская программу.
    """

    import json

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    # Другой компьютер изменил файл данных в общей папке.
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["suppliers"][0]["name"] = "Изменено на другом компьютере"
    store.path.write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )

    assert window.pull_external_changes() is True
    assert store.data.suppliers[0].name == "Изменено на другом компьютере"

    # Повторно — без изменений, перечитывания нет.
    assert window.pull_external_changes() is False

    # Своё сохранение не считается чужим изменением.
    store.data.suppliers[0].name = "Своё изменение"
    store.save()
    assert window.pull_external_changes() is False
    assert store.data.suppliers[0].name == "Своё изменение"
    window.close()


def test_current_user_choice_is_local(qt_app, store, monkeypatch):
    """v0.28.0: выбор пользователя не лежит в общей папке с данными.

    Иначе выбор «Администратора» на одном компьютере давал бы право
    записи и на остальных, и схема «пишет только администратор» не
    работала бы.
    """

    import json

    from oplata import access
    from oplata.app import MainWindow

    window = MainWindow(store)
    qt_app.processEvents()

    assert access.current_user_path().parent == access.local_dir()
    assert access.current_user_path() != access._legacy_current_user_path()

    # Прежний файл рядом с данными читается, если локального ещё нет —
    # выбор не сбрасывается при первом запуске после обновления.
    manager = next(u for u in access.load_users() if u.name == "Руководитель")
    if access.current_user_path().exists():
        access.current_user_path().unlink()
    access._legacy_current_user_path().parent.mkdir(parents=True, exist_ok=True)
    access._legacy_current_user_path().write_text(
        json.dumps({"user_id": manager.id}), encoding="utf-8"
    )
    assert access.load_current_user() is not None
    assert access.load_current_user().id == manager.id
    # Сохранение выбора уходит в локальный каталог, а не в общую папку.
    access.save_current_user(manager)
    assert access.current_user_path().exists()
    window.close()


def test_today_button_shows_current_date(qt_app, store):
    """v0.27.4: кнопка «Сегодня» показывает сегодня, а не понедельник.

    ``refresh`` ставит в поле даты начало недели, поэтому одного
    перехода на неделю мало — иначе «Сегодня» показывало 26.10 при
    сегодняшнем 31.10. Проверяется в обеих таблицах.
    """

    from datetime import date

    from oplata.app import MainWindow
    from oplata.dates import week_monday
    from oplata.widgets import date_edit_value

    today = date.today()
    monday = week_monday(today)
    window = MainWindow(store)
    for view in (window.suppliers_view, window.customers_view):
        # Сначала уходим на другую неделю, потом возвращаемся.
        view.goto_date(date(2026, 10, 5))
        assert date_edit_value(view.date_edit) != today
        view.today_btn.click()
        qt_app.processEvents()
        assert date_edit_value(view.date_edit) == today
        if monday != today:  # в понедельник обе даты совпадают
            assert date_edit_value(view.date_edit) != monday
    window.close()


def test_summary_week_debt_includes_overdue(qt_app, store):
    """v0.27.3: «Задолженность на неделе» = неделя + просроченная.

    Просроченный долг всё равно платить на этой неделе, поэтому он
    входит в «задолженность на неделе» (просьба пользователя).
    """

    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata import calc
    from oplata.app import MainWindow
    from oplata.models import KIND_INCOME, Counterparty, Operation

    supplier = Counterparty(name="Просрочка")
    store.data.suppliers.append(supplier)
    # Срок 20.10 уже прошёл (просрочка), срок 29.10 — внутри недели.
    for day, amount in (("2026-10-20", "200"), ("2026-10-29", "500")):
        store.data.operations.append(
            Operation(
                kind=KIND_INCOME,
                counterparty_id=supplier.id,
                date=day,
                due_date=day,
                amount=amount,
            )
        )

    window = MainWindow(store)
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    view.refresh()
    qt_app.processEvents()

    totals = calc.debt_totals(store.data, date(2026, 10, 27))
    assert totals.late == Decimal("200.00")   # просрочено
    assert totals.week == Decimal("500.00")   # срок внутри недели
    # v0.36.0: «Задолженность на неделе» — только долги со сроком внутри
    # недели (500), без просрочки: так же, как колонка «Неделя» таблицы
    # поставщиков. Просрочка — отдельная справочная строка.
    assert view.value_text("debt_week") == "500,00"
    assert view.value_text("debt_late") == "200,00"   # отдельная строка
    window.close()


def test_supplier_table_shows_conditions_column(qt_app, store):
    """v0.27.3: узкая колонка условий справа от названия поставщика.

    Число — срок отсрочки в днях, «П» — предоплата, «Р» — реализация.
    """

    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.suppliers[:] = [
        Counterparty(name="Обычный", deferment_days=30),
        Counterparty(name="Предоплата", prepayment=True),
        Counterparty(name="Реализация", realization=True),
    ]

    window = MainWindow(store)
    view = window.suppliers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table

    assert table.item(2, 1).text() == "От-\nсрочка"  # v0.28.2: в две строки
    assert table.item(4, 1).text() == "30"     # срок отсрочки
    assert table.item(5, 1).text() == "П"      # предоплата
    assert table.item(6, 1).text() == "Р"      # реализация
    # Итоговые колонки сдвинуты вправо: «Итого» стоит после условий.
    assert table.item(2, 1 + view.lead_offset).text() == "Итого"
    # У покупателей колонки условий нет — сдвига тоже.
    assert window.customers_view.lead_offset == 0
    window.close()


def test_early_payment_keeps_debt_archival_in_day_cell(qt_app, store):
    """v0.41.0: оплата не уменьшает «Долг» в день срока — он архивный.

    Пример пользователя: приход 100 с отсрочкой 30 дней → в графе
    «Долг» в день срока стоит 100. Оплата 70 через 10 дней после
    прихода → в той же графе остаётся **100**: оплата видна только
    в столбцах «Итого», «Просрочено» и «Неделя».
    """

    from datetime import date, timedelta

    from oplata import calc
    from oplata.app import MainWindow
    from oplata.models import KIND_INCOME, KIND_PAYMENT, Counterparty, Operation

    supplier = Counterparty(name="Тест отсрочки", deferment_days=30)
    store.data.suppliers.append(supplier)

    income_day = date(2026, 11, 2)          # понедельник
    income = Operation(
        kind=KIND_INCOME,
        counterparty_id=supplier.id,
        date=income_day.isoformat(),
        amount="100.00",
    )
    store.data.operations.append(income)
    due = calc.operation_due_date(income, supplier, store.data.day_overrides)
    assert due == date(2026, 12, 2)         # среда, есть в таблице

    window = MainWindow(store)
    window.suppliers_view.goto_date(due)
    table = window.suppliers_view.table
    row = week_row(window.suppliers_view, supplier.name)
    debt_col = _DAY(window.suppliers_view, window.suppliers_view._days.index(due), 1)

    # Оплаты ещё нет — в «Долге» вся сумма поставки.
    assert table.item(row, debt_col).text() == "100,00"
    total_col = 1 + window.suppliers_view.lead_offset
    assert table.item(row, total_col).text() == "100,00"      # «Итого»

    # Досрочная оплата: через 10 дней после прихода, задолго до срока.
    store.data.operations.append(
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=supplier.id,
            date=(income_day + timedelta(days=10)).isoformat(),
            amount="70.00",
        )
    )
    window.suppliers_view.refresh()
    table = window.suppliers_view.table

    # «Долг» в дне остался прежним — 100 (архив).
    assert table.item(row, debt_col).text() == "100,00"
    assert table.item(3, debt_col).text() == "100,00"     # строка «ВСЕГО»
    # А «Итого» уменьшилось: оплата сразу видна в итоговых столбцах.
    assert table.item(row, total_col).text() == "30,00"
    week_col = 3 + window.suppliers_view.lead_offset
    assert table.item(row, week_col).text() == "30,00"    # «Неделя»
    window.close()


def test_operation_dialog_and_journal_crud(qt_app, store):
    """Редактор поставки и CRUD оплаты в окне журнала контрагента."""
    from datetime import date

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.dialogs.operation_dialog import OperationDialog
    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    dialog = OperationDialog(store, initial_counterparty_id=supplier.id)
    assert dialog.amount_edit.text() == ""
    dialog.date_edit.setDate(QDate(2026, 10, 26))
    dialog.amount_edit.setText("1 000,50")
    dialog.document_edit.setText("Накладная 42")
    dialog.comment_edit.setText("первая поставка")
    assert dialog.due_label.text() == "05.11.2026"  # ггг: отсрочка 10 дней
    dialog._save()
    (op,) = store.data.operations
    assert (op.kind, op.date, op.due_date, op.amount) == (
        "Приход", "2026-10-26", "2026-11-05", "1000.50",
    )
    dialog.close()

    # Правка только суммы НЕ пересчитывает сохранённый срок, даже если
    # отсрочка поставщика уже изменилась.
    supplier.deferment_days = 30
    edit = OperationDialog(store, operation=op)
    edit.amount_edit.setText("1200")
    assert edit.due_label.text() == "05.11.2026"
    edit._save()
    assert op.amount == "1200.00" and op.due_date == "2026-11-05"
    edit.close()

    # А изменение даты поставки срок пересчитывает (27.10 + 30 дней).
    edit2 = OperationDialog(store, operation=op)
    edit2.date_edit.setDate(QDate(2026, 10, 27))
    assert edit2.due_label.text() == "26.11.2026"
    edit2._save()
    assert op.date == "2026-10-27" and op.due_date == "2026-11-26"
    assert op.document == "Накладная 42"  # документ/комментарий пережили правки
    edit2.close()

    # Отдельная оплата для экрана «Оплаты»: вкладка должна видеть только её,
    # а не поставку выше.
    payment_dialog = OperationDialog(
        store, initial_counterparty_id=supplier.id, initial_kind="Оплата"
    )
    assert payment_dialog.kind_combo.currentText() == "Оплата"
    assert payment_dialog.due_panel.isHidden()
    payment_dialog.date_edit.setDate(QDate(2026, 10, 27))
    payment_dialog.amount_edit.setText("200")
    payment_dialog._save()
    payment_op = next(item for item in store.data.operations if item.kind == "Оплата")
    payment_dialog.close()

    window = MainWindow(store)

    # v0.23.1: журнал из карточки — только список операций контрагента:
    # ни фильтров, ни кнопок «Добавить/Изменить/Удалить».
    dialog = OperationsJournalDialog(store, "supplier", counterparty_id=supplier.id)
    journal = dialog.view
    assert journal.supplier_filter is None
    assert journal.kind_filter is None
    assert journal.add_btn is None
    assert journal.edit_btn is None
    assert journal.delete_btn is None
    # Журнал поставщика показывает только поставки (v0.23.1).
    assert journal.table.rowCount() == 1
    assert [
        journal.table.horizontalHeaderItem(col).text() for col in range(4)
    ] == ["Дата\nпоставки", "Сумма", "Отсрочка,\nдней", "Дата\nоплаты"]

    # Полный журнал (с фильтрами и правкой) живёт отдельным виджетом —
    # его и проверяем на фильтры и удаление.
    from oplata.views.operations_view import OperationsView

    full = OperationsView(store)
    full.set_counterparty_filter(supplier.id)
    assert full.found_label.text() == "Найдено: 2"  # поставка + оплата
    full.kind_filter.setCurrentIndex(0)  # все типы, без фиксированного
    full.supplier_filter.setCurrentIndex(0)
    full._select_operation_row(payment_op.id)
    assert full.selected_operation_id() == payment_op.id
    assert full.edit_btn.isEnabled() and full.delete_btn.isEnabled()

    full.date_from.setText("28.10.2026")  # операции октября — вне диапазона
    assert full.found_label.text() == "Найдено: 0"
    full.reset_filters()
    assert full.found_label.text() == "Найдено: 2"
    journal = full

    # Блок «Оплата» показывает ту же оплату поставщику за 27.10.
    window.receipts_view.outgoing.set_date(date(2026, 10, 27))
    assert window.receipts_view.outgoing.amount_text("suppliers") == "200,00"

    # Поставка даёт долг 1 200 на сроке 26.11 (чт). Оплата 200 от 27.10
    # в «Долге» дня ничего не меняет (v0.41.0: сумма архивная) — она
    # видна только в «Итого»: 1 200 − 200 = 1 000.
    window.suppliers_view.goto_date(date(2026, 11, 26))
    week_table = window.suppliers_view.table
    # v0.55.0: таблица отсортирована по алфавиту, поэтому строку ищем
    # в самом представлении, а не по порядку в данных.
    ggg_row = week_row(window.suppliers_view, "ггг")
    debt_col = _DAY(window.suppliers_view, 3, 1)
    total_col = 1 + window.suppliers_view.lead_offset
    assert week_table.item(ggg_row, debt_col).text() == "1 200,00"
    assert week_table.item(ggg_row, total_col).text() == "1 000,00"

    # Удаление оплаты возвращает «Итого» к полной сумме поставки
    # (в программе это делает колбэк on_data_changed журнала).
    assert journal.delete_operation(payment_op.id)
    window.suppliers_view.refresh()
    week_table = window.suppliers_view.table
    assert week_table.item(ggg_row, debt_col).text() == "1 200,00"
    assert week_table.item(ggg_row, total_col).text() == "1 200,00"
    assert [item.id for item in store.data.operations] == [op.id]
    assert journal.found_label.text() == "Найдено: 1"  # осталась поставка
    assert journal.selected_operation_id() == op.id  # выделение перешло на неё
    dialog.close()
    window.close()


def test_supplier_card_adds_counterparty(qt_app, store):
    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    dialog = CounterpartyCardDialog(store, "supplier")
    dialog.name_edit.setText("Новый поставщик")
    dialog.deferment_spin.setValue(14)
    dialog._add()
    names = [s.name for s in store.data.suppliers]
    assert "Новый поставщик" in names
    added = store.data.suppliers[-1]
    assert added.deferment_days == 14

    # Режим «Предоплата» отключает поле отсрочки (правило с макета карточки)
    dialog.chk_prepayment.setChecked(True)
    assert not dialog.deferment_spin.isEnabled()
    dialog.chk_prepayment.setChecked(False)
    assert dialog.deferment_spin.isEnabled()
    dialog.close()


def test_customer_card_has_no_supplier_payment_terms(qt_app, store):
    """В карточке покупателя нет отсрочки и режимов поставщика."""
    from PySide6.QtWidgets import QLabel

    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    dialog = CounterpartyCardDialog(store, "customer")
    assert dialog.deferment_spin is None
    assert dialog.chk_prepayment is None
    assert dialog.chk_realization is None
    label_texts = {label.text() for label in dialog.findChildren(QLabel)}
    assert "Отсрочка платежа, дней" not in label_texts
    assert "Режим оплаты" not in label_texts

    dialog.name_edit.setText("Новый покупатель")
    dialog._add()
    added = store.data.customers[-1]
    assert (added.deferment_days, added.prepayment, added.realization) == (
        0,
        False,
        False,
    )
    dialog.close()


def test_amount_edit_clears_zero_on_focus(qt_app):
    """Нулевое значение поля суммы не мешает начинать ввод."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtWidgets import QWidget

    from oplata.widgets import AmountLineEdit

    holder = QWidget()
    edit = AmountLineEdit("0,00", holder)
    assert edit.text() == ""  # начальные нули не показываются как текст

    edit.setText(",00")
    holder.show()
    edit.setFocus()
    qt_app.processEvents()
    assert edit.text() == ""

    edit.setText("123,45")
    edit.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    qt_app.processEvents()
    assert edit.selectedText() == "123,45"
    holder.close()


def test_data_calendar_marks_days_and_saves_explicitly(qt_app, store):
    """Календарь: режим → клики по дням → только кнопка «Сохранить» пишет файл."""
    from datetime import date

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import Operation
    from oplata.storage import DataStore

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    store.data.operations.append(
        Operation(
            kind="Приход",
            counterparty_id=supplier.id,
            date="2026-10-26",
            amount="700.00",
            due_date="",  # срок пересчитывается по текущему календарю
        )
    )
    window = MainWindow(store)
    calendar = window.data_view
    calendar.year = 2026
    calendar.refresh()

    monday = date(2026, 10, 26)
    saturday = date(2026, 10, 31)
    due_day = date(2026, 11, 5)  # 26.10 + отсрочка «ггг» 10 дней
    assert calendar.chk_off.isChecked()
    assert not calendar.save_button.isEnabled()

    calendar._on_day_clicked(monday)  # будний день -> выходной в черновике
    assert calendar._pending_overrides[monday.isoformat()] == "off"
    assert monday.isoformat() not in store.data.day_overrides  # ещё не сохранено
    assert calendar.has_unsaved_changes
    assert calendar.save_button.isEnabled()
    assert styles.WEEKEND_RED in calendar._day_buttons[monday].styleSheet()

    calendar.chk_work.setChecked(True)
    assert not calendar.chk_off.isChecked()  # режимы взаимоисключающие
    calendar._on_day_clicked(saturday)  # суббота -> рабочий перенос
    assert calendar._pending_overrides[saturday.isoformat()] == "work"
    assert styles.WORKDAY_GREY in calendar._day_buttons[saturday].styleSheet()

    calendar.discard_changes()
    assert calendar._pending_overrides == {}
    assert not calendar.has_unsaved_changes

    # Отмечаем день срока выходным и сохраняем: долг в недельной таблице
    # должен сдвинуться на следующий рабочий день без ручного refresh.
    window.suppliers_view.goto_date(due_day)
    table = window.suppliers_view.table
    # v0.55.0: таблица отсортирована по алфавиту, поэтому строку ищем
    # в самом представлении, а не по порядку в данных.
    ggg_row = week_row(window.suppliers_view, "ггг")
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == "700,00"  # чт 05.11
    calendar.chk_off.setChecked(True)
    calendar._on_day_clicked(due_day)
    assert calendar.save_calendar()
    assert store.data.day_overrides[due_day.isoformat()] == "off"
    assert DataStore(store.path).data.day_overrides[due_day.isoformat()] == "off"
    assert calendar.status_label.text() == "Сохранено"
    assert not calendar.save_button.isEnabled()
    assert table.item(ggg_row, _DAY(window.suppliers_view, 3, 1)).text() == ""
    assert table.item(ggg_row, _DAY(window.suppliers_view, 4, 1)).text() == "700,00"  # пт 06.11
    window.close()


def test_customer_sale_table_and_direct_input(qt_app, store):
    """Покупатели: «Реализация/Оплата», без «Долга» и без срока оплаты."""
    from datetime import date

    from PySide6.QtCore import Qt

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import Counterparty
    from oplata.views.week_view import FIRST_DATA_ROW, TOTAL_ROW

    customer = Counterparty(name="Покупатель А")
    store.data.customers.append(customer)
    window = MainWindow(store)
    view = window.customers_view
    monday = date(2026, 10, 26)
    today = date(2026, 10, 27)
    view.goto_date(monday)
    store.data.day_overrides[today.isoformat()] = "off"
    view._rebuild_table(view._partners, view._days[: view.table_days], today)
    table = view.table

    # v0.29.0: 1 имя + 3 итога («Всего», «Выписано», «В работе») +
    # 6 дней * (Реализация, метка, Оплата) = 22. v0.18.0: дней в
    # таблице шесть, без воскресенья.
    assert table.columnCount() == 22
    assert table.item(1, 1).text() == "Задолженность"
    assert table.item(2, 1).text() == "Всего"
    assert table.item(2, 2).text() == "Выписано"
    assert table.item(2, 3).text() == "В работе"
    assert table.item(2, _DAY(view, 0, 0)).text() == "Реализация"
    assert table.item(2, _DAY(view, 0, 1)).text() == ""
    assert table.item(2, _DAY(view, 0, 2)).text() == "Оплата"
    assert all(
        table.item(2, col) is None or table.item(2, col).text() != "Долг"
        for col in range(table.columnCount())
    )

    def bg_name(row, col):
        brush = table.item(row, col).background()
        return None if brush.style() == Qt.BrushStyle.NoBrush else brush.color().name()

    # v0.38.0: колонка текущего дня красится на всю высоту (шапка —
    # своим зелёным, тело таблицы — светлее).
    assert bg_name(1, _DAY(view, 1, 0)) == styles.WEEK_TABLE_TODAY_GREEN
    assert bg_name(2, _DAY(view, 1, 0)) == styles.WEEK_TABLE_TODAY_GREEN_BODY
    assert bg_name(TOTAL_ROW, _DAY(view, 1, 0)) == styles.WEEK_TABLE_TODAY_GREEN_BODY
    assert (
        bg_name(FIRST_DATA_ROW, _DAY(view, 1, 0))
        == styles.WEEK_TABLE_TODAY_GREEN_BODY
    )
    # Суббота как обычный выходной остаётся розовой и в теле таблицы.
    assert bg_name(FIRST_DATA_ROW, _DAY(view, 5, 0)) == styles.WEEK_TABLE_WEEKEND_PINK

    sale = table.item(FIRST_DATA_ROW, _DAY(view, 0, 0))
    sale.setText("500")
    (op,) = store.data.operations
    assert (op.counterparty_id, op.kind, op.date, op.amount, op.due_date) == (
        customer.id,
        "Реализация",
        monday.isoformat(),
        "500.00",
        "",
    )
    assert sale.text() == "500,00"
    window.close()


def test_realization_dialog_saves_without_due_date(qt_app, store):
    """Окно покупателя: «Реализация», дата реализации, без срока оплаты."""
    from datetime import date

    from PySide6.QtWidgets import QLabel

    from oplata.dialogs.realization_dialog import RealizationDialog
    from oplata.models import Counterparty

    customer = Counterparty(name="Покупатель Б")
    store.data.customers.append(customer)
    store.save()
    dialog = RealizationDialog(
        store, customer.id, preset_date=date(2026, 10, 27)
    )
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    # v0.27.3: в шапке окна — наименование покупателя.
    assert dialog.windowTitle() == "Реализация — Покупатель Б"
    assert "Дата реализации *" in labels
    assert not any("СРОК ОПЛАТЫ" in text.upper() for text in labels)
    assert not hasattr(dialog, "due_label")

    dialog.amount_edit.setText("1 250,50")
    dialog._save()
    assert dialog.saved_date == date(2026, 10, 27)
    (op,) = store.data.operations
    assert (op.kind, op.counterparty_id, op.date, op.amount, op.due_date) == (
        "Реализация",
        customer.id,
        "2026-10-27",
        "1250.50",
        "",
    )
    dialog.close()


def test_receipts_table_direct_input_and_totals(qt_app, store):
    """Таблица «Поступление»: ввод в ячейку дня и суммы по дням недели.

    v0.38.0: колонки «Сумма» нет — сумма вводится прямо в ячейку
    своего дня, а стрелки «←»/«→» листают недели.
    """
    from datetime import date

    from oplata.app import MainWindow

    window = MainWindow(store)
    view = window.receipts_view
    incoming = view.incoming

    # Заголовок блока и строка даты идут в одну строчку.
    assert incoming.date_edit.calendarPopup()
    # v0.28.2: кнопка календаря вдвое шире — минимум вырос со 138.
    assert incoming.date_edit.minimumWidth() >= 158
    assert incoming.today_btn.text() == "Сегодня"
    assert incoming.prev_btn.text() == "←" and incoming.next_btn.text() == "→"
    assert incoming.date_edit.y() <= incoming.today_btn.y() + 4

    incoming.set_date(date(2026, 10, 27))
    # v0.38.0: стрелки листают недели, а не дни.
    incoming.next_btn.click()
    assert incoming.current_date() == date(2026, 11, 3)
    incoming.prev_btn.click()
    incoming.prev_btn.click()
    assert incoming.current_date() == date(2026, 10, 20)
    view.set_date(date(2026, 10, 27))

    col_26 = _DAY_COL(incoming, date(2026, 10, 26))
    col_27 = _DAY_COL(incoming, date(2026, 10, 27))

    # Наличные и карты вводятся прямо в ячейку дня (v0.38.0).
    incoming.table.item(0, col_27).setText("1 500,75")
    assert [(r.source, r.date, r.amount) for r in store.data.cash_receipts] == [
        ("cash", "2026-10-27", "1500.75")
    ]
    assert incoming.amount_text("cash") == "1 500,75"
    incoming.table.item(1, col_27).setText("2 000")
    assert [(r.source, r.amount) for r in store.data.cash_receipts] == [
        ("cash", "1500.75"),
        ("card", "2000.00"),
    ]
    assert incoming.amount_text("card") == "2 000,00"

    # Сумма видна в колонке своего дня (v0.22.0).
    incoming.table.item(0, col_27).setText("1 000")
    assert incoming.amount_text("cash", col_27) == "1 000,00"
    assert incoming.amount_text("cash", col_26) == ""

    # Сумма другого дня этой же недели стоит в своей колонке.
    view.incoming.table.item(0, col_26).setText("500")
    assert view.incoming.amount_text("cash", col_26) == "500,00"
    assert view.incoming.amount_text("cash", col_27) == "1 000,00"
    assert view.incoming.amount_text("card", col_27) == "2 000,00"

    # Пустая ячейка убирает запись дня (только по своему источнику).
    view.incoming.table.item(0, col_27).setText("")
    assert view.incoming.amount_text("cash") == ""
    assert [(r.source, r.date) for r in store.data.cash_receipts] == [
        ("card", "2026-10-27"),
        ("cash", "2026-10-26"),
    ]
    window.close()


def test_counterparty_popup_scrolls_long_list(qt_app, store):
    """v0.23.1: окно контрагентов — во весь экран и с прокруткой.

    Проверяем окно покупателей (блок «Поступление») и окно
    поставщиков (блок «Оплата»): список прокручивается, а высота
    окна ограничена только высотой экрана.
    """

    from PySide6.QtCore import Qt

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.customers.extend(
        Counterparty(name=f"Покупатель {i}") for i in range(12)
    )
    expected_suppliers = len(store.data.suppliers) + 12
    store.data.suppliers.extend(
        Counterparty(name=f"Поставщик {i}") for i in range(12)
    )
    window = MainWindow(store)
    window.show()
    window.resize(1280, 900)
    qt_app.processEvents()
    view = window.receipts_view

    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert popup is not None
    assert len(popup.edits) == 12
    # Список лежит в прокручиваемой области, скроллбар закреплён.
    assert popup.scroll_area.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    # Высота окна упирается в экран, а не в старые 320 px: при длинном
    # списке окно занимает всю доступную высоту.
    screen = popup.screen().availableGeometry().height()
    assert popup.height() <= screen - 16

    suppliers = view.open_popup("suppliers", row=0)
    qt_app.processEvents()
    assert suppliers is not None
    assert len(suppliers.edits) == expected_suppliers
    assert suppliers.scroll_area.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    window.close()


def test_receipts_customers_popup_opens_right_and_sums(qt_app, store):
    """Строка «Покупатели»: окно справа, итог по всем покупателям."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    first = Counterparty(name="Покупатель В")
    second = Counterparty(name="Покупатель Г")
    store.data.customers.extend([first, second])
    window = MainWindow(store)
    window.show()
    # Окно должно быть достаточно широким: проверка «попап справа от
    # таблицы» бессмысленна на узком headless-окне.
    window.resize(1280, 800)
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    popup = view.open_popup("customers", row=2)
    assert popup is not None
    assert list(popup.edits) == [first.id, second.id]
    # Окно всплывает справа от таблицы, а не под строкой. Проверяем,
    # только когда справа есть место: в headless-среде без настоящей
    # геометрии (платформа minimal) окна остаются узкими.
    table_right = view.incoming.table.mapToGlobal(
        view.incoming.table.rect().topRight()
    ).x()
    screen_width = window.screen().availableGeometry().width()
    free_space = min(screen_width, window.width()) - table_right
    if free_space >= popup.width():
        assert popup.x() > table_right - popup.width()

    # Итог пересчитывается по мере ввода и до сохранения.
    assert popup.total_label.text() == "0,00"
    popup.edits[first.id].setText("3 000")
    assert popup.total_label.text() == "3 000,00"
    popup.edits[second.id].setText("1 250,50")
    assert popup.total_label.text() == "4 250,50"
    popup._save()

    assert [
        (op.kind, op.counterparty_id, op.date, op.amount, op.due_date)
        for op in store.data.operations
        if op.kind == "Оплата"
    ] == [
        ("Оплата", first.id, "2026-10-27", "3000.00", ""),
        ("Оплата", second.id, "2026-10-27", "1250.50", ""),
    ]
    assert view.incoming.amount_text("customers") == "4 250,50"

    # Та же сумма видна в колонке «Оплата» недельной таблицы покупателей.
    window.customers_view.goto_date(date(2026, 10, 27))
    table = window.customers_view.table
    names = [c.name for c in store.data.customers]
    payment = _DAY(window.customers_view, 1, 2)   # v0.29.0: «Оплата» — 3-я
    assert table.item(4 + names.index(first.name), payment).text() == "3 000,00"
    assert table.item(4 + names.index(second.name), payment).text() == "1 250,50"
    window.close()


def test_receipts_outgoing_block_saves_supplier_payment(qt_app, store):
    """Блок «Оплата» под «Поступлением»: окно поставщиков с полями сумм."""
    from datetime import date

    from oplata.app import MainWindow

    supplier = next(s for s in store.data.suppliers if s.name == "ббб")
    window = MainWindow(store)
    view = window.receipts_view
    # Блок «Оплата» расположен под блоком «Поступление» (в левой колонке).
    layout = view.left_layout
    assert layout.indexOf(view.outgoing) > layout.indexOf(view.incoming)
    view.outgoing.set_date(date(2026, 10, 27))

    popup = view.open_popup("suppliers", row=0)
    assert supplier.id in popup.edits
    popup.edits[supplier.id].setText("4 500")
    assert popup.total_label.text() == "4 500,00"
    popup._save()
    (op,) = store.data.operations
    assert (op.kind, op.counterparty_id, op.date, op.amount) == (
        "Оплата",
        supplier.id,
        "2026-10-27",
        "4500.00",
    )
    assert view.outgoing.amount_text("suppliers") == "4 500,00"

    # Недельная таблица поставщиков показывает ту же оплату в своей ячейке.
    window.suppliers_view.goto_date(date(2026, 10, 27))
    table = window.suppliers_view.table
    bbb_row = week_row(window.suppliers_view, "ббб")
    assert table.item(bbb_row, _DAY(window.suppliers_view, 1, 2)).text() == "4 500,00"
    window.close()


def test_data_panels_are_collapsible(qt_app, store):
    """Плашки «Настройка календаря» и «Администрирование» раскрываются."""
    from oplata.app import MainWindow

    window = MainWindow(store)
    data = window.data_view

    assert data.calendar_panel.title == "Настройка календаря"
    assert data.admin_panel.title == "Администрирование"
    assert "Настройка календаря" in data.calendar_panel.toggle.text()
    assert "Администрирование" in data.admin_panel.toggle.text()

    # По умолчанию свёрнуты: календарь нужен редко и занимает много места.
    assert not data.calendar_panel.is_expanded()
    assert not data.admin_panel.is_expanded()
    assert data.calendar_panel.content.isHidden()

    data.calendar_panel.toggle.click()
    assert data.calendar_panel.is_expanded()
    assert not data.calendar_panel.content.isHidden()
    assert data.year_label.text()  # календарь внутри плашки работает

    data.admin_panel.toggle.click()
    assert data.admin_panel.is_expanded()
    assert str(store.path) in data.path_label.text()
    assert "Поставщиков: 5" in data.stats_label.text()
    assert data.save_button.text() == "Сохранить"
    window.close()


from oplata.views.receipts_view import (  # noqa: E402
    COL_NAME,
    COL_NOTE,
    COL_VAT,
    COLUMN_COUNT,
    TABLE_DAYS,
)


def test_receipts_popup_keeps_saved_amounts(qt_app, store):
    """Повторное открытие окна показывает уже введённые суммы."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    buyer = Counterparty(name="Покупатель Д")
    store.data.customers.append(buyer)
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    popup = view.open_popup("customers", row=2)
    assert popup.edits[buyer.id].text() == ""  # пока ничего не вводили
    popup.edits[buyer.id].setText("2 500")
    popup._save()

    again = view.open_popup("customers", row=2)
    assert again.edits[buyer.id].text() == "2 500,00"
    assert again.total_label.text() == "2 500,00"
    # Очистка поля и сохранение убирают сумму дня.
    again.edits[buyer.id].setText("")
    again._save()
    assert [op for op in store.data.operations if op.kind == "Оплата"] == []
    assert view.incoming.amount_text("customers") == ""
    window.close()


def test_other_payment_rows_add_sum_and_remove(qt_app, store, monkeypatch):
    """Блок «Оплата»: вид — карточкой, сумма — в ячейку, «−» удаляет."""
    from datetime import date

    from PySide6.QtTest import QTest

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_NAME, JournalPopup

    # v0.20.0: вид оплаты вводится карточкой (модальный диалог) — в
    # прогоне её подменяем на заполненные ответы.
    patch_other_payment_card(monkeypatch, ["Новый вид оплаты", "Второй вид"])
    window = MainWindow(store)
    view = window.receipts_view
    # Сначала выставляем дату: колонки дней в блоке перестраиваются под
    # неделю выбранной даты, а при запуске в блоке стоит текущая неделя.
    view.outgoing.set_date(date(2026, 10, 27))
    day_column = _DAY_COL(view.outgoing, date(2026, 10, 27))
    assert view.outgoing.row_keys == ["suppliers"]  # пока только поставщики

    view.add_other_row()
    assert view.outgoing.row_keys == ["suppliers", "other:Новый вид оплаты"]

    # Название в ячейке не правится (v0.21.0): клик по названию открывает
    # журнал операций, а не поле ввода.
    name_item = view.outgoing.table.item(1, 0)
    assert not (name_item.flags() & Qt.ItemFlag.ItemIsEditable)
    view.outgoing.table.cellClicked.emit(1, COL_NAME)
    # v0.26.2: журнал своего вида открывается с задержкой, чтобы первый
    # клик двойного не показывал журнал вместо карточки.
    QTest.qWait(700)
    qt_app.processEvents()
    assert isinstance(view._popup, JournalPopup)

    # Сумма — прямо в ячейку дня (v0.38.0), итоги считаются по названию.
    view.outgoing.table.item(1, day_column).setText("12 000")
    assert view.outgoing.amount_text("other:Новый вид оплаты") == "12 000,00"
    assert view.outgoing.amount_text("other:Новый вид оплаты", day_column) == "12 000,00"
    assert [
        (p.name, p.date, p.amount, p.direction)
        for p in store.data.other_payments
        if p.name == "Новый вид оплаты"
    ] == [("Новый вид оплаты", "2026-10-27", "12000.00", "out")]

    # Вторая строка и удаление первой (кнопка «−»).
    view.add_other_row()
    assert len(view.outgoing.row_keys) == 3
    view.outgoing.table.setCurrentCell(1, 0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    view.remove_other_row("other:Новый вид оплаты")
    assert view.outgoing.row_keys == [
        "suppliers",
        "other:Второй вид",
    ]
    assert [
        p.name
        for p in store.data.other_payments
        if getattr(p, "direction", "out") == "out"
    ] == ["Второй вид"]

    # Постоянную строку «Поставщики» удалить нельзя.
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: None),
    )
    view.outgoing.table.setCurrentCell(0, 0)
    view.outgoing._remove_selected()
    assert view.outgoing.row_keys[0] == "suppliers"
    window.close()


def test_supplier_payments_sync_both_ways(qt_app, store):
    """Суммы поставщиков видны и здесь, и в недельной таблице."""
    from datetime import date

    from oplata.app import MainWindow

    supplier = next(s for s in store.data.suppliers if s.name == "ббб")
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    # Из блока «Оплата» — в таблицу поставщиков.
    popup = view.open_popup("suppliers", row=0)
    popup.edits[supplier.id].setText("4 500")
    popup._save()
    assert view.outgoing.amount_text("suppliers") == "4 500,00"

    window.suppliers_view.goto_date(date(2026, 10, 27))
    table = window.suppliers_view.table
    bbb_row = week_row(window.suppliers_view, "ббб")
    payment_cell = table.item(bbb_row, _DAY(window.suppliers_view, 1, 2))
    assert payment_cell.text() == "4 500,00"

    # И обратно: правка в таблице поставщиков видна в блоке «Оплата».
    payment_cell.setText("7 000")
    assert view.outgoing.amount_text("suppliers") == "7 000,00"
    check = view.open_popup("suppliers", row=0)
    assert check.edits[supplier.id].text() == "7 000,00"
    check.close()

    # То же самое для покупателей.
    from oplata.models import Counterparty

    buyer = Counterparty(name="Покупатель Е")
    store.data.customers.append(buyer)
    window.customers_view.goto_date(date(2026, 10, 27))
    customers_table = window.customers_view.table
    buyer_row = week_row(window.customers_view, "Покупатель Е")
    # v0.29.0: «Оплата» — третья колонка дня (Реализация, метка, Оплата).
    customers_table.item(buyer_row, _DAY(window.customers_view, 1, 2)).setText("1 750")
    assert view.incoming.amount_text("customers") == "1 750,00"
    window.close()


def test_incoming_block_has_fixed_other_income_row(qt_app, store, monkeypatch):
    """В «Поступлении» одна постоянная строка «Прочие поступления» (v0.13.0)."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import INCOMING, OUTGOING

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))
    assert view.incoming.row_keys == [
        "cash",
        "card",
        "customers",
        "other:Прочие поступления",
    ]
    # Кнопок «+»/«−» у блока «Поступление» нет — строку не добавить и
    # не убрать, название не редактируется.
    assert not hasattr(view.incoming, "add_btn")
    name_item = view.incoming.table.item(3, COL_NAME)
    assert not (name_item.flags() & Qt.ItemFlag.ItemIsEditable)

    # Сумма вводится прямо в ячейку дня (v0.38.0) с направлением «in».
    view.incoming.table.item(3, _DAY_COL(view.incoming, date(2026, 10, 27))).setText(
        "5 000"
    )
    qt_app.processEvents()
    assert view.incoming.amount_text("other:Прочие поступления") == "5 000,00"
    assert [
        (p.name, p.direction, p.date, p.amount)
        for p in store.data.other_payments
        if p.name == "Прочие поступления"
    ] == [("Прочие поступления", INCOMING, "2026-10-27", "5000.00")]

    # Свои строки блока «Оплата» не пересекаются с постоянной строкой.
    patch_other_payment_card(monkeypatch)
    view.add_other_row(OUTGOING)
    assert view.outgoing.row_keys == ["suppliers", "other:Новый вид оплаты"]
    assert view.incoming.row_keys[-1] == "other:Прочие поступления"
    window.close()


def test_receipts_popup_has_no_buttons_and_saves_on_click_outside(qt_app, store):
    """Окно без «Закрыть»/«Сохранить»: сохраняет по клику вне поля ввода."""
    from datetime import date

    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QPushButton

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    buyer = Counterparty(name="Покупатель Ж")
    store.data.customers.append(buyer)
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert [b.text() for b in popup.findChildren(QPushButton)] == []
    # Автоподсветки нет: фокус не забирает ни одно из полей.
    assert qt_app.focusWidget() is not popup.edits[buyer.id]

    # Клик по полю ввода окно не закрывает.
    edit = popup.edits[buyer.id]
    edit.setText("7 250")
    point = edit.mapTo(popup, edit.rect().center())
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qt_app.sendEvent(popup, event)
    qt_app.processEvents()
    assert popup.isVisible()

    # Клик в свободном месте — сохраняет и скрывает окно.
    point = popup.total_label.mapTo(popup, popup.total_label.rect().center())
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qt_app.sendEvent(popup, event)
    qt_app.processEvents()
    assert view.incoming.amount_text("customers") == "7 250,00"
    assert [
        (op.kind, op.date, op.amount)
        for op in store.data.operations
        if op.kind == "Оплата"
    ] == [("Оплата", "2026-10-27", "7250.00")]
    window.close()


def test_tables_edit_on_single_click_and_numbers_centered(qt_app, store):
    """Ввод — по одному клику; значения в таблицах — по центру (v0.23.9)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COLUMN_COUNT, FIRST_DAY_COL

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    center = Qt.AlignmentFlag.AlignCenter  # v0.23.9: не по правому краю

    for table in (
        view.incoming.table,
        view.outgoing.table,
        window.suppliers_view.table,
        window.customers_view.table,
    ):
        assert table.editTriggers() == QAbstractItemView.EditTrigger.AllEditTriggers

    # Все колонки дней недели — по центру (v0.23.9; колонки «Сумма»
    # с v0.38.0 нет).
    for column in range(FIRST_DAY_COL, COLUMN_COUNT):
        assert view.incoming.table.item(0, column).textAlignment() == center
        assert (
            view.incoming.table.horizontalHeaderItem(column).textAlignment()
            == center
        )
    window.close()


def test_money_block_scrolls_long_row_list(qt_app, store, monkeypatch):
    """Длинный список своих строк прокручивается, блок не растёт дальше."""
    from oplata.app import MainWindow
    from oplata.models import OUTGOING

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    patch_other_payment_card(monkeypatch)
    for _ in range(9):  # всего 10 строк — больше видимых восьми
        view.add_other_row(OUTGOING)
    qt_app.processEvents()
    assert len(view.outgoing.row_keys) == 10
    # Блок «Оплата» тянется до низа, а скроллбар закреплён и широкий.
    assert view.outgoing.table.verticalScrollBar().isVisible()
    assert (
        view.outgoing.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    assert view.outgoing.table.verticalScrollBar().width() >= 18
    # Блок «Оплата» опущен до низа: он ниже блока «Поступление».
    assert view.outgoing.geometry().bottom() >= view.incoming.geometry().bottom()
    window.close()


def test_week_day_columns_show_daily_amounts(qt_app, store):
    """Колонки дней недели: сумма за каждый день (v0.22.0)."""
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import set_daily_cash_receipt
    from oplata.views.receipts_view import COLUMN_COUNT, TABLE_DAYS

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    # 27.10.2026 — вторник; неделя начинается 26.10 (понедельник).
    view.set_date(date(2026, 10, 27))
    # День текущей недели (понедельник 26.10).
    set_daily_cash_receipt(store.data, "cash", "2026-10-26", Decimal("2000"))
    # Выбранный день.
    set_daily_cash_receipt(store.data, "cash", "2026-10-27", Decimal("3000"))
    # Прошлые даты: в колонках этой недели их не видно.
    set_daily_cash_receipt(store.data, "cash", "2026-10-05", Decimal("1000"))
    set_daily_cash_receipt(store.data, "cash", "2026-07-02", Decimal("500"))
    view.refresh()
    qt_app.processEvents()

    # Шесть дней недели: Пн 26.10 – Сб 31.10 (воскресенья нет).
    # v0.33.0: колонок девять — название, НДС, сумма и шесть дней.
    # v0.38.0: колонки «Сумма» нет, поэтому столбцов на один меньше.
    # v0.60.0: между названием и НДС стоит колонка «Примечание».
    assert view.incoming.table.columnCount() == COLUMN_COUNT == 3 + TABLE_DAYS
    assert view.incoming.days[0] == date(2026, 10, 26)
    assert view.incoming.days[-1] == date(2026, 10, 31)
    headers = [
        view.incoming.table.horizontalHeaderItem(column).text()
        for column in range(COLUMN_COUNT)
    ]
    # v0.33.0: вторая колонка — «НДС» (в блоке «Поступление» скрыта);
    # v0.38.0: третьей колонкой «Сумма» больше нет — сразу дни.
    # v0.60.0: вторая колонка — «Примечание», НДС стал третьим.
    assert headers[0] == "" and headers[1] == "Примечание"
    assert headers[2] == "НДС"
    assert view.incoming.table.isColumnHidden(COL_VAT)
    assert not view.incoming.table.isColumnHidden(COL_NOTE)
    assert headers[3].startswith("26.10") and headers[-1].startswith("31.10")

    row = view.incoming.row_keys.index("cash")
    assert view.incoming.amount_text("cash") == "3 000,00"  # выбранный день
    assert (
        view.incoming.amount_text("cash", _DAY_COL(view.incoming, date(2026, 10, 26)))
        == "2 000,00"
    )
    assert (
        view.incoming.amount_text("cash", _DAY_COL(view.incoming, date(2026, 10, 27)))
        == "3 000,00"
    )
    # Остальные дни недели пустые: суммы вне недели (05.10, 02.07) в
    # колонки дней не попадают.
    for day in (date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)):
        assert view.incoming.amount_text("cash", _DAY_COL(view.incoming, day)) == ""
    window.close()


def test_arrows_are_bold_and_row_hover_installed(qt_app, store):
    """Стрелки жирные; в таблицах есть подсветка строки под курсором."""
    from PySide6.QtGui import QFont

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view

    # v0.25.1: жирными остались только стрелки блоков «Поступление» и
    # «Оплата»; кнопки «← Неделя»/«Неделя →» набраны как «Сегодня».
    for button in (
        view.incoming.prev_btn,
        view.incoming.next_btn,
        view.outgoing.prev_btn,
        view.outgoing.next_btn,
    ):
        font: QFont = button.font()
        assert font.bold() or font.weight() >= QFont.Weight.Bold

    # Подсветка строки: у делегатов есть метод set_hover_row (миксин).
    for table in (
        view.incoming.table,
        view.outgoing.table,
        window.suppliers_view.table,
        window.customers_view.table,
    ):
        assert table.hasMouseTracking()
        delegate = table.itemDelegate()
        assert hasattr(delegate, "set_hover_row")
        delegate.set_hover_row(0)  # не падает — подсветка применилась
        delegate.set_hover_row(-1)
    window.close()






def test_arrows_are_next_to_today_and_bold_only_on_receipts(qt_app, store):
    """Стрелки — справа от «Сегодня»; жирные только на первой вкладке."""
    from PySide6.QtCore import Qt

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view

    # Стрелки стоят сразу справа от кнопки «Сегодня».
    for block in (view.incoming, view.outgoing):
        assert block.prev_btn.x() > block.today_btn.x()
        assert block.next_btn.x() > block.prev_btn.x()
    outgoing = view.outgoing
    assert outgoing.add_btn.x() > outgoing.next_btn.x()
    assert outgoing.remove_btn.x() > outgoing.add_btn.x()
    # В блоке «Поступление» кнопок добавления строк нет.
    assert not hasattr(view.incoming, "add_btn")

    # Жирные стрелки только здесь: у них увеличенный кегль, а на
    # других вкладках стрелки обычного размера (v0.15.0).
    plain = window.suppliers_view.prev_week_btn.font().pixelSize()
    assert outgoing.next_btn.font().pixelSize() > plain
    assert view.incoming.next_btn.font().pixelSize() > plain

    # Скроллинга в «Поступлении» нет, в «Оплате» он закреплён.
    assert (
        view.incoming.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert (
        outgoing.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    window.close()


def test_week_table_overdue_and_debt_headers_are_separate(qt_app, store):
    """v0.16.0: три итоговые колонки — под одной шапкой «ЗАДОЛЖЕННОСТЬ»."""
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    table = window.suppliers_view.table
    offset = window.suppliers_view.lead_offset
    # v0.28.3: слово целиком и посередине объединённой ячейки.
    assert table.item(1, 1 + offset).text() == "Задолженность"
    assert (
        table.item(1, 1 + offset).textAlignment() == Qt.AlignmentFlag.AlignCenter
    )
    # Ячейки объединены: подписи столбцов стоят строкой ниже.
    assert table.columnSpan(1, 1 + offset) == 3
    # v0.27.3: название поставщика занимает и колонку условий.
    assert table.columnSpan(1, 0) == 1 + offset
    # v0.22.1: «Итого» стоит слева от «Просрочено» (вместо «на сегодня»).
    # v0.28.2: подпись узкой колонки — в две строки.
    assert table.item(2, 1).text() == "От-\nсрочка"
    assert table.item(2, 1 + offset).text() == "Итого"
    assert table.item(2, 2 + offset).text() == "Просрочено"
    assert table.item(2, 3 + offset).text() == "Неделя"
    window.close()


def test_popup_closes_on_mouse_click_anywhere(qt_app, store):
    """Окно сумм закрывается по клику мышью по нему (кроме полей ввода)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.customers.append(Counterparty(name="Покупатель И"))
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.show()
    qt_app.processEvents()
    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert popup.isVisible()

    def press(widget, point):
        QApplication.sendEvent(
            widget,
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(point),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        qt_app.processEvents()

    # Клик по свободному месту окна (не по полю ввода) — окно закрыто.
    press(popup, QPointF(popup.width() - 10, 10))
    assert not popup.isVisible()

    # Клик по таблице под окном тоже закрывает его.
    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    assert popup.isVisible()
    press(view.incoming.table, QPointF(5.0, 5.0))
    assert not popup.isVisible()

    # Клик по самому полю ввода окно не закрывает (ввод продолжается).
    popup = view.open_popup("customers", row=2)
    qt_app.processEvents()
    edit = next(iter(popup.edits.values()))
    press(popup, QPointF(edit.mapTo(popup, edit.rect().center())))
    assert popup.isVisible()
    window.close()


def test_summary_panel_shows_three_blocks_line_by_line(qt_app, store):
    """Плашка «Сводка»: три блока, все значения построчно."""
    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import (
        CASH_SOURCE,
        add_cash_receipt,
        add_delivery,
        add_supplier_payment,
        set_daily_other_payment,
    )

    # 27.10.2026 — вторник; неделя 26.10–01.11, следующие: 02.11–08.11,
    # 09.11–15.11, 16.11–22.11.
    on_date = date(2026, 10, 27)
    supplier = store.data.suppliers[0]

    # Средства: 50 000 на счету — поступлением 26.10 (v0.26.0: плашки
    # «Данные» нет), поступление 3 000, оплата 1 200
    # (v0.22.1: ещё аренда 250 от 15.10 — она уже в остатке на 27.10,
    # а в «Оплаты за день» не входит).
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-26", Decimal("50000"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-27", Decimal("3000"))
    add_supplier_payment(store.data, supplier.id, "2026-10-27", Decimal("1200"))

    # Задолженность: срок сегодня (27.10), просрочка (20.10) и план на
    # вторую неделю вперёд (09.11–15.11).
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-10-27", Decimal("500"))
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("700"))
    add_delivery(store.data, supplier.id, "2026-10-05", "2026-11-10", Decimal("900"))
    # Прочий вид оплаты с начала месяца.
    set_daily_other_payment(
        store.data, "Аренда", "2026-10-15", Decimal("250")
    )

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    view.refresh()
    qt_app.processEvents()

    # Блок «Средства».
    assert view.caption_text("funds") == "Остаток на счёте"
    # Остаток на начало 27.10: 50 000 − аренда 250 (15.10).
    assert view.value_text("funds") == "49 750,00"
    assert view.value_text("income") == "3 000,00"
    assert view.value_text("payments") == "1 200,00"
    assert view.value_text("balance") == "51 550,00"

    # Блок «Задолженность поставщикам».
    # v0.26.5: «Всего задолженность» — как строка «Всего», столбец
    # «Итого» таблицы поставщиков: остаток долга после оплат. Из 2 100
    # (500 + 700 + 900) оплата 1 200 гасит 700 и 500, остаётся 900.
    assert view.value_text("debt_total") == "900,00"
    # «Задолженность на сегодня» удалена. Оплата поставщику (1 200)
    # гасит сначала просроченное (700 со сроком 20.10), остаток 500 —
    # долги недели (v0.15.0): и «на неделе», и «Просрочено» пусты.
    assert view.value_text("debt_week") == "0,00"
    # v0.22.1: строки «Задолженность на сегодня» в сводке нет.
    assert "debt_overdue" not in view._values
    assert view.caption_text("plan_1") == "Оплата 02.11 – 08.11"
    assert view.value_text("plan_1") == "0,00"
    assert view.caption_text("plan_2") == "Оплата 09.11 – 15.11"
    assert view.value_text("plan_2") == "900,00"
    assert view.caption_text("plan_3") == "Оплата 16.11 – 22.11"
    assert view.value_text("plan_3") == "0,00"
    assert view.caption_text("plan_4") == "Оплата 23.11 – 29.11"
    assert view.value_text("plan_4") == "0,00"

    # Блок «Месяц» (v0.43.0/0.44.0): не приход и оплаты, а итоги
    # реестра «Отчеты ККМ» за октябрь целиком. Записей ККМ в этой базе
    # нет, поэтому все три строки нулевые.
    assert view.value_text("month_kkm_total") == "0,00"
    assert view.value_text("month_sale_invoiced") == "0,00"
    assert view.value_text("month_total") == "0,00"
    # v0.44.0: строки «Итого Возврат покупателя» и зелёного квадратика
    # у «Реализации» больше нет; подпись стоит в одну строчку.
    assert "month_kkm_returns" not in view._values
    assert not view._captions["month_sale_invoiced"].wordWrap()
    assert "month_income" not in view._values
    assert "month_payments" not in view._values

    # Все значения построчно: подписи и числа лежат в сетке блоками.
    assert view.panel.isVisible()
    # Блок поставщиков: всего, на неделе, просроченный платёж
    # (v0.16.0) + четыре недели оплаты (v0.22.1: без «на сегодня»).
    # v0.44.0: в блоке «Месяц» три строки (ККМ за вычетом возвратов,
    # реализация и общий итог).
    assert len(view._values) == len(view._captions) == 4 + 7 + 1 + 3
    # Блок «Задолженность покупателей» пока пуст (покупателей нет).
    assert view.value_text("cust_total") == "0,00"
    window.close()


def test_summary_calc_helpers_are_pure(qt_app, store):
    """Расчёты сводки — простые суммы по сохранённым данным."""
    from datetime import date
    from decimal import Decimal

    from oplata import calc
    from oplata.models import (
        KIND_PAYMENT,
        add_delivery,
        set_daily_operation,
    )

    supplier = store.data.suppliers[0]
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("700"))
    # v0.26.0: плашка «Данные» удалена — долг со сроком 28.10 задаём
    # обычной поставкой, а не переносом остатков.
    add_delivery(store.data, supplier.id, "2026-10-26", "2026-10-28", Decimal("300"))

    schedule = calc.debt_schedule(store.data)
    assert schedule == {date(2026, 10, 20): Decimal("700"), date(2026, 10, 28): Decimal("300")}

    totals = calc.debt_totals(store.data, date(2026, 10, 28))
    assert totals.total == Decimal("1000")
    assert totals.today == Decimal("300")
    # Неделя 26.10–01.11: срок 20.10 уже в прошлой неделе. Оплат нет,
    # поэтому неделя и просроченное считаются по графику.
    assert totals.week == Decimal("300")
    # «Просрочено» = столбец «на сегодня»: долги со сроком по дату
    # сводки включительно — 700 (срок 20.10) и 300 (перенос, срок 28.10).
    assert totals.overdue == Decimal("1000")

    # Оплата 500 гасит сначала просроченное (срок 20.10), остаток 0.
    set_daily_operation(
        store.data, supplier.id, "2026-10-27", KIND_PAYMENT, Decimal("500")
    )
    paid = calc.debt_totals(store.data, date(2026, 10, 27))
    assert paid.overdue == Decimal("200")  # 700 − 500: оплата в просрочку
    # Долг со сроком 28.10 внутри недели 26.10–01.11 не тронут:
    # оплата приоритетно ушла в просроченное (v0.15.0).
    assert paid.week == Decimal("300")

    plan = calc.weekly_payment_plan(store.data, date(2026, 10, 28))
    assert [week.amount for week in plan] == [Decimal("0")] * 4
    assert [week.monday.isoformat() for week in plan] == [
        "2026-11-02",
        "2026-11-09",
        "2026-11-16",
        "2026-11-23",
    ]

    # v0.22.1: блок «Средства» считается на дату.
    funds = calc.funds_totals(store.data, date(2026, 10, 27))
    assert funds.funds == Decimal("0")   # «текущие средства» не задавали
    assert funds.balance == funds.funds + funds.income - funds.payments


def test_summary_customer_debt_block_counts_sales_minus_payments(qt_app, store):
    """Блок «Задолженность покупателей»: Реализация − Оплата."""
    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import (
        Counterparty,
        add_customer_receipt,
        add_realization,
    )

    buyer = Counterparty(name="Покупатель К")
    store.data.customers.append(buyer)
    add_realization(store.data, buyer.id, "2026-10-27", Decimal("5000"))
    add_customer_receipt(store.data, buyer.id, "2026-10-27", Decimal("1500"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    view.refresh()

    assert view.caption_text("cust_total") == "Задолженность покупателей"
    assert view.value_text("cust_total") == "3 500,00"

    # v0.35.0: деньги, пришедшие от покупателя, — поступление, а не
    # оплата: 1 500 стоит в «Поступление за день», а не в «Оплаты за
    # день». Реализация в поступления по-прежнему не входит (v0.15.0).
    assert view.value_text("income") == "1 500,00"
    assert view.value_text("payments") == "0,00"
    window.close()


def test_funds_movement_dialog_fills_screen_height(qt_app, store):
    """v0.23.4: журнал движения средств — на всю высоту экрана, со скроллом."""

    from datetime import date

    from PySide6.QtWidgets import QApplication

    from oplata.views.funds_view import FundsMovementDialog

    dialog = FundsMovementDialog(store, date(2026, 10, 28))
    # На экране меньше минимальной высоты окно не сжимается ниже 320.
    available = QApplication.primaryScreen().availableGeometry().height()
    assert dialog.height() == max(320, available - 24)
    assert (
        dialog.table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    assert "QScrollBar:vertical" in dialog.table.styleSheet()
    dialog.close()


def test_funds_movement_journal_shows_one_day(qt_app, store):
    """v0.23.1: журнал движения средств — за один день.

    Порядок строк: «Остаток на счёте» (на начало дня), все поступления
    дня, все оплаты дня и «Итого остаток». Над таблицей — дата со
    стрелками, как в блоках «Поступление»/«Оплата».
    """

    from datetime import date
    from decimal import Decimal

    from oplata import calc
    from oplata.models import (
        CASH_SOURCE,
        add_cash_receipt,
        add_supplier_payment,
    )
    from oplata.views.funds_view import MOVEMENT_DAYS, FundsMovementDialog

    supplier = store.data.suppliers[0]
    # v0.26.0: начальный остаток — поступлением 26.10 (плашки «Данные»
    # больше нет).
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-26", Decimal("100"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-27", Decimal("500"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-28", Decimal("700"))
    add_supplier_payment(store.data, supplier.id, "2026-10-28", Decimal("200"))

    on_date = date(2026, 10, 28)
    movement = calc.funds_movement(store.data, on_date)
    assert MOVEMENT_DAYS == 1
    assert movement.start == on_date == movement.end
    # Остаток на начало 28.10: 100 + 500 (поступления 26 и 27.10).
    assert movement.start_funds == Decimal("600")
    assert movement.income == Decimal("700")
    assert movement.payments == Decimal("200")
    assert movement.balance == Decimal("1100")

    dialog = FundsMovementDialog(store, on_date)
    table = dialog.table
    # Остаток на счёте, поступление, оплата, итог остаток.
    assert table.rowCount() == 4
    assert table.item(0, 1).text() == "Остаток на счёте"
    assert table.item(0, 4).text() == "600,00"
    assert table.item(1, 1).text() == "Поступление наличными"
    assert table.item(1, 2).text() == "700,00"
    assert table.item(2, 3).text() == "200,00"
    assert table.item(3, 1).text() == "Итого остаток"
    assert table.item(3, 2).text() == "700,00"
    assert table.item(3, 3).text() == "200,00"
    assert table.item(3, 4).text() == "1 100,00"

    # Стрелки и «Сегодня» переставляют дату журнала.
    assert dialog.current_date() == on_date
    dialog.shift_date(1)
    assert dialog.current_date() == date(2026, 10, 29)
    # 29.10: поступлений и оплат нет — только перенесённый остаток.
    assert table.rowCount() == 2
    assert table.item(0, 1).text() == "Остаток на счёте"
    assert table.item(0, 4).text() == "1 100,00"
    assert table.item(1, 1).text() == "Итого остаток"
    assert table.item(1, 4).text() == "1 100,00"
    dialog.close()


def test_funds_block_carries_balance_to_next_day(qt_app, store):
    """v0.22.1: вчерашний «Итого остаток» = сегодняшний «Остаток на счету».

    Правило пользователя: «сегодня после всех поступлений и оплат
    итого остаток 350 руб, на следующий день эта сумма переходит в
    остаток на счету, к ней добавляются поступления и отнимаются
    оплаты».
    """

    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import CASH_SOURCE, add_cash_receipt

    # v0.26.0: плашка «Данные» удалена — начальный остаток задаём
    # поступлением раньше расчётной даты.
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-26", Decimal("100"))
    add_cash_receipt(store.data, CASH_SOURCE, "2026-10-27", Decimal("500"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    qt_app.processEvents()

    # 27.10: остаток на начало дня 100, поступление 500, итого 600.
    assert view.value_text("funds") == "100,00"
    assert view.value_text("income") == "500,00"
    assert view.value_text("payments") == "0,00"
    assert view.value_text("balance") == "600,00"

    # 28.10: 600 перешло в «Остаток на счету», за день пока ничего.
    view.date_edit.setDate(QDate(2026, 10, 28))
    qt_app.processEvents()
    assert view.value_text("funds") == "600,00"
    assert view.value_text("income") == "0,00"
    assert view.value_text("payments") == "0,00"
    assert view.value_text("balance") == "600,00"

    # v0.27.6: даты плашек независимы — «Сводка» не тянет за собой
    # блоки «Поступление»/«Оплата».
    window.receipts_view.incoming.set_date(date(2026, 10, 20))
    window.receipts_view.outgoing.set_date(date(2026, 10, 20))
    view.date_edit.setDate(QDate(2026, 10, 28))
    qt_app.processEvents()
    assert view.current_date() == date(2026, 10, 28)
    assert window.receipts_view.incoming.current_date() == date(2026, 10, 20)
    assert window.receipts_view.outgoing.current_date() == date(2026, 10, 20)

    # И наоборот: смена даты в блоке не переставляет дату «Сводки».
    window.receipts_view.incoming.set_date(date(2026, 10, 29))
    qt_app.processEvents()
    assert view.current_date() == date(2026, 10, 28)
    assert view.value_text("funds") == "600,00"
    window.close()


def test_summary_refreshes_right_after_typing_amount(qt_app, store):
    """Сводка пересчитывается сразу после ввода суммы в ячейку."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.views.receipts_view import FIRST_DAY_COL

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    summary = window.summary_view

    # Дату задаём явно: сегодня может быть воскресенье, а его в
    # таблице нет (шесть дней, Пн–Сб). «Сводка» держит свою дату,
    # поэтому её переводим на тот же день.
    view.set_date(date(2026, 10, 27))
    summary.set_date(date(2026, 10, 27))
    qt_app.processEvents()
    today_column = _DAY_COL(view.incoming, date(2026, 10, 27))
    assert today_column >= FIRST_DAY_COL
    assert summary.value_text("income") == "0,00"
    view.incoming.table.item(0, today_column).setText("2 500")
    qt_app.processEvents()
    assert summary.value_text("income") == "2 500,00"
    assert summary.value_text("balance") == "2 500,00"

    # Убрали сумму — сводка снова пересчиталась.
    view.incoming.table.item(0, today_column).setText("")
    qt_app.processEvents()
    assert summary.value_text("income") == "0,00"
    window.close()


def test_week_customer_debt_column_is_sales_minus_payments(qt_app, store):
    """У покупателей «Всего» = Реализация − Оплата (v0.29.0: три колонки)."""
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import (
        Counterparty,
        add_customer_receipt,
        add_realization,
    )

    buyer = Counterparty(name="Покупатель Л")
    store.data.customers.append(buyer)
    add_realization(store.data, buyer.id, "2026-10-27", Decimal("4000"))
    add_customer_receipt(store.data, buyer.id, "2026-10-28", Decimal("1000"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.customers_view
    view.goto_date(date(2026, 10, 27))
    table = view.table

    assert table.item(1, 1).text() == "Задолженность"
    assert table.item(2, 1).text() == "Всего"
    assert table.item(2, 2).text() == "Выписано"
    assert table.item(2, 3).text() == "В работе"
    # Столбца «просрочено» у покупателей нет: дальше идут дни.
    assert table.item(2, _DAY(view, 0, 0)).text() == "Реализация"
    assert table.item(4, 1).text() == "3 000,00"
    assert table.item(3, 1).text() == "3 000,00"  # строка «ВСЕГО»
    window.close()


def _DAY_COL(block, day):
    """Номер колонки дня в блоке «Поступление»/«Оплата» (v0.22.0)."""
    from oplata.views.receipts_view import FIRST_DAY_COL

    return FIRST_DAY_COL + block.days.index(day)


def _DAY(view, day_index, subcolumn):
    """Номер колонки дня: считается от числа итоговых столбцов таблицы."""
    return view._day_column(day_index, subcolumn)


def test_supplier_click_opens_operations_journal(qt_app, store, monkeypatch):
    """v0.23.1: клик по поставщику открывает журнал операций.

    Раньше клик открывал окно «Поставка» — по просьбе пользователя
    поставщик теперь ведёт себя так же, как покупатель: клик — журнал
    операций. v0.27.3: двойной клик убран, окно «Поставка» открывает
    правый клик по строке.
    """

    from datetime import date

    from oplata.app import MainWindow

    # Подменяем методы до создания окна: WeekView получает ссылки на них
    # в конструкторе, поэтому подмена атрибута готового окна не поможет.
    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(
        MainWindow,
        "open_operations_journal",
        lambda self, kind, cid: opened.append((kind, cid)),
    )
    deliveries: list[str] = []
    monkeypatch.setattr(
        MainWindow,
        "open_delivery_dialog",
        lambda self, cid, _op=None: deliveries.append(cid),
    )

    window = MainWindow(store)
    view = window.suppliers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table
    row = 4  # первая строка контрагента

    # v0.55.0: первая строка таблицы — первый контрагент **по алфавиту**.
    supplier_id = view.counterparties()[0].id
    view._on_item_clicked(table.item(row, 0))
    assert opened == [("supplier", supplier_id)]
    assert deliveries == []

    # v0.27.3: двойной клик больше ничего не открывает, а окно
    # «Поставка» — правый клик по строке контрагента.
    from PySide6.QtCore import QPoint

    view._on_item_double_clicked(table.item(row, 0))
    assert deliveries == []
    view._on_context_menu(
        QPoint(
            table.columnViewportPosition(0) + 5,
            table.rowViewportPosition(row) + 5,
        )
    )
    assert deliveries == [supplier_id]
    window.close()


def test_tab_switch_resets_date_to_today(qt_app, store):
    """v0.23.1: переход на вкладку возвращает дату на сегодняшний день."""

    from datetime import date

    from oplata.app import IDX_CUSTOMERS, IDX_RECEIPTS, IDX_SUPPLIERS, MainWindow
    from oplata.dates import week_monday

    window = MainWindow(store)
    today = date.today()

    # Ушли на другую неделю у поставщиков и в блоках ввода.
    window.suppliers_view.goto_date(date(2026, 10, 26))
    window.receipts_view.set_date(date(2026, 10, 26))
    assert window.suppliers_view.week_start == week_monday(date(2026, 10, 26))

    window.show_page(IDX_CUSTOMERS)
    assert window.customers_view.week_start == week_monday(today)

    window.show_page(IDX_SUPPLIERS)
    assert window.suppliers_view.week_start == week_monday(today)

    window.show_page(IDX_RECEIPTS)
    assert window.receipts_view.incoming.current_date() == today
    assert window.receipts_view.outgoing.current_date() == today
    window.close()


def test_wide_scrollbar_keeps_white_background(qt_app, store):
    """v0.23.1: широкий скроллбар не делает фон серым.

    Виджет со своей таблицей стилей становится корнем новой области
    стилей Qt: правила APP_STYLESHEET для него и его детей перестают
    действовать, и без повторного «background:#ffffff» вместо белого
    проглядывает серый фон (так было в окнах «Покупатели»/«Поставщики»
    после появления вертикальной прокрутки).
    """

    from PySide6.QtGui import QColor, QPalette

    from oplata import styles

    from oplata.app import MainWindow
    from oplata.views.receipts_view import AmountPopup

    white = QColor(styles.PANEL_BG)

    entries = [(f"id{index}", f"Контрагент {index}") for index in range(12)]
    popup = AmountPopup("Покупатели", entries, lambda values: None)
    popup.show()
    qt_app.processEvents()
    scroll = popup.scroll_area
    # v0.38.0: тёплая тема — белый стал тёплым (styles.PANEL_BG).
    assert f"background:{styles.PANEL_BG}" in scroll.styleSheet()
    viewport = scroll.viewport()
    viewport.ensurePolished()
    assert viewport.palette().color(QPalette.ColorRole.Window) == white
    popup.close()

    # Недельные таблицы и таблица блока «Оплата» — с тем же скроллбаром.
    window = MainWindow(store)
    for table in (
        window.suppliers_view.table,
        window.suppliers_view.frozen_table,
        window.receipts_view.outgoing.table,
    ):
        table.ensurePolished()
        assert table.palette().color(QPalette.ColorRole.Window) == white
    window.close()


def test_journal_period_three_and_twelve_months(qt_app, store, monkeypatch):
    """v0.23.2: журнал поставщика — 3 месяца, покупателя — 12 месяцев."""

    from datetime import date

    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog
    from oplata.models import Counterparty, Operation
    from oplata.views import operations_view as operations_view_module

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 29)

    monkeypatch.setattr(operations_view_module, "date", _FixedDate)

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    # В демо-данных покупателей нет — заводим своего.
    customer = Counterparty(name="Покупатель Тест")
    store.data.customers.append(customer)
    # 2 месяца назад — попадает и в 3, и в 12; 5 месяцев — только в 12;
    # 13 месяцев — никуда.
    for months_ago in (2, 5, 13):
        day = operations_view_module._months_back(months_ago, _FixedDate.today())
        store.data.operations.append(
            Operation(
                kind="Приход",
                counterparty_id=supplier.id,
                date=day.isoformat(),
                due_date=day.isoformat(),
                amount="100.00",
            )
        )
        store.data.operations.append(
            Operation(
                kind="Реализация",
                counterparty_id=customer.id,
                date=day.isoformat(),
                amount="100.00",
            )
        )

    supplier_journal = OperationsJournalDialog(
        store, "supplier", counterparty_id=supplier.id
    )
    assert supplier_journal.view.period_months == 3
    assert supplier_journal.view.table.rowCount() == 1  # только 2 месяца назад
    supplier_journal.close()

    customer_journal = OperationsJournalDialog(
        store, "customer", counterparty_id=customer.id
    )
    assert customer_journal.view.period_months == 12
    assert customer_journal.view.table.rowCount() == 2  # 2 и 5 месяцев назад
    customer_journal.close()


def test_journals_are_same_width_and_centered(qt_app, store):
    """v0.23.9: журналы одной ширины, без горизонтального скроллинга,
    шапка и значения — по центру."""

    from PySide6.QtCore import Qt

    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog
    from oplata.models import Counterparty, Operation

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    customer = Counterparty(name="Покупатель Тест")
    store.data.customers.append(customer)
    for counterparty in (supplier, customer):
        store.data.operations.append(
            Operation(
                kind="Приход",
                counterparty_id=counterparty.id,
                date="2026-10-20",
                due_date="2026-11-03",
                amount="100.00",
            )
        )

    journals = [
        OperationsJournalDialog(store, kind, counterparty_id=person.id)
        for kind, person in (("supplier", supplier), ("customer", customer))
    ]
    # Оба журнала одной ширины (v0.23.9).
    assert journals[0].width() == journals[1].width()
    for journal in journals:
        table = journal.view.table
        header = table.horizontalHeader()
        # Горизонтального скроллинга нет, колонки занимают всю ширину.
        assert (
            table.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert sum(
            header.sectionSize(column) for column in range(table.columnCount())
        ) == table.viewport().width()
        # Шапка и значения — по центру.
        assert header.defaultAlignment() & Qt.AlignmentFlag.AlignHCenter
        for column in range(table.columnCount()):
            assert table.item(0, column).textAlignment() & (
                Qt.AlignmentFlag.AlignHCenter
            ), column
        journal.close()


def test_summary_panel_font_is_larger_others_unchanged(qt_app, store):
    """v0.23.6: шрифт увеличен только в панели «Сводка».

    v0.31.0: панель уменьшена на 20% по просьбе пользователя — кегли
    считаются от ``SUMMARY_FONT_SCALE`` (1,5 → 1,2), а остальная
    программа по-прежнему при обычном шрифте.

    v0.43.0: в блоке «Месяц» стало три строки вместо двух, и плашка
    перестала влезать на экран целиком — кегль уменьшили ещё
    (1,2 → 1,05), заголовок панели и строки стали мельче.
    """

    from oplata import styles
    from oplata.app import MainWindow

    window = MainWindow(store)
    summary = window.summary_view
    # Кегли блоков «Сводки» — крупнее обычных.
    assert summary.panel.toggle.font().pixelSize() == styles.fs_summary(15)
    caption = next(iter(summary._captions.values()))
    value = next(iter(summary._values.values()))
    assert caption.font().pixelSize() == styles.fs_summary(12)
    assert value.font().pixelSize() == styles.fs_summary(12)
    assert styles.SUMMARY_FONT_SCALE == 1.2
    assert styles.fs_summary(12) == int(
        round(12 * styles.FONT_SCALE * styles.SUMMARY_FONT_SCALE)
    )
    # Панель осталась крупнее остальной программы, но не слишком.
    assert styles.fs_summary(12) > styles.fs(12)
    # v0.44.0: кегль снова 1,2 (в v0.43.0 временно был 1,05 — там в
    # блоке «Месяц» было на строку больше).
    assert styles.fs_summary(15) == 23
    assert styles.fs_summary(12) == 19

    # Остальная программа осталась при обычном шрифте (общее +50% из
    # v0.23.2 отменено).
    assert styles.FONT_SCALE == 1.3
    assert window.suppliers_view.week_label.font().pixelSize() == styles.fs(13)
    window.close()


def test_money_block_paints_weekends_and_today(qt_app, store, monkeypatch):
    """v0.23.3: столбец выходного — розовый, сегодняшняя дата — салатовая."""

    from datetime import date

    from PySide6.QtGui import QColor

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.views import receipts_view as receipts_view_module

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 29)

    monkeypatch.setattr(receipts_view_module, "date", _FixedDate)

    window = MainWindow(store)
    block = window.receipts_view.incoming
    block.set_date(date(2026, 10, 26))  # неделя 26.10 — 31.10
    green = QColor(styles.WEEK_TABLE_TODAY_GREEN)
    assert _FixedDate.today() in block.days
    pink = QColor(styles.WEEK_TABLE_WEEKEND_PINK)
    # Фон секции рисует сама шапка: в Windows BackgroundRole ячейки
    # шапки Qt игнорирует (v0.23.7).
    assert isinstance(block.day_header, receipts_view_module.DayHeaderView)
    for index, day in enumerate(block.days):
        column = receipts_view_module.FIRST_DAY_COL + index
        is_weekend = day.weekday() >= 5
        color = block.day_header.section_color(column)
        if day == _FixedDate.today():
            assert color == green, day
        else:
            assert (color == pink) == is_weekend, day
        # Розовый красит весь столбец, а не только шапку.
        for row in range(block.table.rowCount()):
            item = block.table.item(row, column)
            if item is None:
                continue
            assert (item.background().color() == pink) == is_weekend, (row, day)
    # Сетка таблицы (вертикальная и горизонтальная разметка) на месте
    # в обоих блоках: стиль с gridline-color задаётся каждой таблице
    # отдельно, иначе в «Поступлении» сетка не рисовалась (v0.23.8).
    outgoing = window.receipts_view.outgoing
    for table in (block.table, outgoing.table):
        assert table.showGrid() is True
        assert f"gridline-color:{styles.TABLE_GRID}" in table.styleSheet()
        assert f"border:1px solid {styles.BORDER}" in table.styleSheet()


def test_week_buttons_use_same_font_as_today(qt_app, store):
    """v0.25.1: подпись «Неделя» набрана тем же шрифтом, что кнопка
    «Сегодня» — без своего кегля и жирности."""

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    for view in (window.suppliers_view, window.customers_view):
        for button in (view.prev_week_btn, view.next_week_btn):
            assert button.styleSheet() == "", button.text()
            assert button.font().toString() == view.today_btn.font().toString()
    window.close()


def test_payment_card_opens_by_right_click(qt_app, store):
    """v0.27.3: карточку вида оплаты открывает правый клик.

    Двойной клик левой клавишей убран по просьбе пользователя —
    карточка осталась на правой кнопке (v0.26.3). Проверка идёт
    настоящими событиями мыши: при AllEditTriggers сигнал
    cellDoubleClicked до обработчика не доходил, и по нему нельзя
    судить, открылась карточка или нет.
    """

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    add_other_payment(
        store.data, "Аренда", view.outgoing.current_date().isoformat(), OUTGOING
    )
    view.refresh_rows()
    view.refresh()
    qt_app.processEvents()

    opened: list[str] = []
    repaints: list[int] = []
    popups: list[str] = []
    view.outgoing.on_edit_card = opened.append
    view.outgoing._repaint_table = lambda: repaints.append(1)
    # v0.39.0: обработчик получает и дату нажатой ячейки.
    view.outgoing.on_open_popup = (
        lambda key, widget, row, day=None: popups.append(key)
    )
    block = view.outgoing
    table = block.table

    def point(row: int, column: int) -> QPoint:
        return QPoint(
            table.columnViewportPosition(column) + 10,
            table.rowViewportPosition(row) + table.rowHeight(row) // 2,
        )

    row = block.row_keys.index("other:Аренда")
    # Клик по названию, по сумме и по дню: везде карточка.
    for column in (0, 1, 2):
        opened.clear()
        # v0.27.3: двойной клик больше не открывает карточку.
        QTest.mouseDClick(
            table.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            point(row, column),
        )
        qt_app.processEvents()
        assert opened == [], f"двойной клик, колонка {column}"
        opened.clear()
        # v0.26.3: правый клик открывает ту же карточку.
        # v0.26.3: правый клик открывает ту же карточку.
        QTest.mouseClick(
            table.viewport(),
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
            point(row, column),
        )
        qt_app.processEvents()
        assert opened == ["other:Аренда"], f"правый клик, колонка {column}"
        # v0.26.3: после клика таблица перерисовывается заново.
        assert repaints, f"перерисовка после клика, колонка {column}"
        repaints.clear()

    # Один клик остаётся вводом в ячейке, карточку не открывает.
    opened.clear()
    QTest.mouseClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point(row, 1),
    )
    qt_app.processEvents()
    assert opened == []

    # Журнал своему виду не мешает: двойной клик его не открывает.
    QTest.qWait(700)
    qt_app.processEvents()
    assert popups == []

    # v0.27.3: журнал открывается только по названию (первый столбец) —
    # клик по сумме и по дню остаётся вводом, журнал не показывается.
    for column in (1, 2):
        popups.clear()
        QTest.mouseClick(
            table.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            point(row, column),
        )
        QTest.qWait(700)
        qt_app.processEvents()
        assert popups == [], f"журнал по клику на колонку {column}"
    popups.clear()

    # Один клик по названию — журнал открывается, но с задержкой
    # (v0.26.2): первый клик двойного больше не показывает журнал.
    QTest.mouseClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point(row, 0),
    )
    qt_app.processEvents()
    assert popups == []
    QTest.qWait(700)
    qt_app.processEvents()
    assert popups == ["other:Аренда"]

    # Служебная строка «Поставщики» — не свой вид: карточки нет.
    opened.clear()
    service = block.row_keys.index("suppliers")
    QTest.mouseDClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point(service, 0),
    )
    QTest.mouseClick(
        table.viewport(),
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
        point(service, 0),
    )
    qt_app.processEvents()
    assert opened == []
    window.close()


def test_table_stylesheets_contain_cell_rules(qt_app):
    """v0.26.6: правила ячейки есть и в APP_STYLESHEET, и в QSS,
    который таблица получает отдельно.

    Виджет со своей таблицей стилей становится корнем новой области
    стилей Qt: правила APP_STYLESHEET на него не действуют. Из-за этого
    с v0.23.1 (WIDE_SCROLLBAR_ON_TABLE) на таблицах «Оплата»,
    «Поступление», «Покупатели», «Поставщики» после клика появлялась
    системная рамка на нажатой ячейке.
    """

    from oplata import styles

    for name in (
        "APP_STYLESHEET",
        "WIDE_SCROLLBAR_ON_TABLE",
        "WIDE_SCROLLBAR_ON_AREA",
    ):
        sheet = getattr(styles, name)
        assert "item:focus" in sheet, f"{name}: нет правила для ячейки"
        assert "item:selected" in sheet, f"{name}: нет правила для выделения"
        assert "QLineEdit" in sheet, f"{name}: нет правила для ввода"
        assert "outline" in sheet, f"{name}: не снят контур фокуса"


def test_tables_selection_color_is_white(qt_app, store):
    """v0.27.1: цвет выделения у таблиц — белый, а не системный синий.

    Qt рисует линии и фон на текущей строке цветом ``QPalette.Highlight``.
    Замер показал: QSS, заданный на самом виджете (``WIDE_SCROLLBAR_ON_TABLE``),
    палитру не меняет — цвет оставался системным (``#308cc6``), и на
    строке появлялись синие штрихи. Поэтому цвет задаётся напрямую.

    v0.38.0: белый стал тёплым белым темы «Тёплый песок».
    """

    from oplata import styles


    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QAbstractItemView

    from oplata.app import MainWindow
    from oplata.widgets import disable_selection_color

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    disable_selection_color(window)
    tables = window.findChildren(QAbstractItemView)
    assert tables, "в окне должны быть таблицы"
    for table in tables:
        color = table.palette().color(QPalette.ColorRole.Highlight)
        # v0.38.0: белый фон таблиц — тёплый белый темы «Тёплый песок».
        assert color.name() == styles.PANEL_BG, (
            f"{type(table).__name__}: {color.name()}"
        )
    window.close()


def test_click_does_not_paint_selection_over_row(qt_app, store):
    """v0.27.0: клик по ячейке не меняет вид строки.

    Проверка по пикселям: после клика в блоке «Оплата» не должно
    появиться ни одного пикселя цвета выделения (``#dbeafe``). Раньше
    вся строка заливалась этим цветом и «переезжала» за каждым кликом —
    пользователь называл это артефактом.
    """

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.resize(1400, 900)
    window.show()
    qt_app.processEvents()
    window.receipts_view.refresh_rows()
    window.receipts_view.refresh()
    qt_app.processEvents()

    table = window.receipts_view.outgoing.table
    table.viewport().grab()  # первый снимок: раскладка доезжает
    qt_app.processEvents()
    before = table.viewport().grab().toImage()
    point = QPoint(
        table.columnViewportPosition(1) + 30,
        table.rowViewportPosition(0) + table.rowHeight(0) // 2,
    )
    QTest.mouseClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        point,
    )
    qt_app.processEvents()
    after = table.viewport().grab().toImage()
    assert before.size() == after.size()

    selection = QColor("#dbeafe").rgb()
    painted = sum(
        1
        for y in range(after.height())
        for x in range(after.width())
        if after.pixel(x, y) == selection
    )
    assert painted == 0, f"после клика строка залита выделением: {painted}"
    window.close()


def test_focus_frame_is_not_drawn_on_table_cells(qt_app):
    """v0.26.4: рамка фокуса на нажатой ячейке таблицы не рисуется.

    Qt рисует её (``PE_FrameFocusRect``) на текущей ячейке после клика:
    это и был «артефакт», который переезжал на каждую следующую нажатую
    ячейку. Делегат тут не помогает — рамку рисует стиль.
    """

    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap
    from PySide6.QtWidgets import QStyle, QStyleOption, QTableWidget

    from oplata.styles import NoFocusFrameStyle

    style = NoFocusFrameStyle()
    pixmap = QPixmap(60, 30)
    pixmap.fill(Qt.GlobalColor.white)
    option = QStyleOption()
    option.rect = QRect(2, 2, 56, 26)
    option.state |= QStyle.StateFlag.State_HasFocus
    table = QTableWidget(2, 2)
    painter = QPainter(pixmap)
    try:
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_FrameFocusRect,
            option,
            painter,
            table,
        )
    finally:
        painter.end()
    image = pixmap.toImage()
    white = QColor(Qt.GlobalColor.white).rgb()
    painted = [
        (x, y)
        for x in range(60)
        for y in range(30)
        if image.pixel(x, y) != white
    ]
    assert not painted, f"рамка фокуса на ячейке нарисована: {painted[:3]}"

    # Ячейку рисует делегат: он вызывает стиль без виджета (None) —
    # именно этот случай и давал артефакт на нажатой ячейке.
    pixmap.fill(Qt.GlobalColor.white)
    painter = QPainter(pixmap)
    try:
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_FrameFocusRect,
            option,
            painter,
            None,
        )
    finally:
        painter.end()
    image = pixmap.toImage()
    painted = [
        (x, y)
        for x in range(60)
        for y in range(30)
        if image.pixel(x, y) != white
    ]
    assert not painted, f"рамка фокуса из делегата нарисована: {painted[:3]}"


def test_tables_have_no_focus_after_disable_item_focus(qt_app, store):
    """v0.26.5: у таблиц снят фокус — рамка на нажатой ячейке не
    рисуется (ввод по клику при этом работает)."""

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QAbstractItemView

    from oplata.app import MainWindow
    from oplata.widgets import disable_item_focus

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    disable_item_focus(window)
    tables = window.findChildren(QAbstractItemView)
    assert tables, "в окне должны быть таблицы"
    for table in tables:
        assert table.focusPolicy() == Qt.FocusPolicy.NoFocus, type(table).__name__
    window.close()


def test_cell_editor_opens_without_frame_and_centered(qt_app, store):
    """v0.26.1: поле ввода в ячейке открывается без рамки и по центру.

    Рамка редактора на Windows выглядела как синие вертикальные штрихи
    по краям ячейки, а само число при клике «прыгало» влево.
    """

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment
    from oplata.views.receipts_view import FIRST_DAY_COL

    # В стиле приложения у редактора нет рамки и синего выделения.
    assert "QTableWidget QLineEdit" in styles.APP_STYLESHEET
    assert "border: none" in styles.APP_STYLESHEET

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    add_other_payment(
        store.data, "Бензин", view.outgoing.current_date().isoformat(), OUTGOING
    )
    view.refresh_rows()
    view.refresh()
    qt_app.processEvents()

    block = view.outgoing
    table = block.table
    row = block.row_keys.index("other:Бензин")
    QTest.mouseClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(
            # v0.38.0: сумма вводится в ячейку дня (первая колонка дней).
            table.columnViewportPosition(FIRST_DAY_COL) + 20,
            table.rowViewportPosition(row) + table.rowHeight(row) // 2,
        ),
    )
    qt_app.processEvents()
    editor = table.viewport().findChild(QLineEdit)
    assert editor is not None, "клик по ячейке должен открыть ввод"
    assert not editor.hasFrame()
    assert editor.alignment() & Qt.AlignmentFlag.AlignHCenter
    window.close()


def test_duplicate_payment_creates_copy_with_dates(qt_app, store):
    """v0.25.1: правая кнопка → «Дублировать»: копия вида с новым именем
    и теми же отмеченными датами."""

    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    add_other_payment(
        store.data,
        "Аренда",
        view.outgoing.current_date().isoformat(),
        OUTGOING,
        dates=["2026-10-27", "2026-10-30"],
    )
    view.refresh_rows()
    view.refresh()
    qt_app.processEvents()

    # v0.26.3: правый клик открывает карточку, поэтому дублирование
    # проверяется вызовом метода напрямую.
    view.duplicate_payment("other:Аренда", OUTGOING)
    qt_app.processEvents()
    names = [item.name for item in store.data.other_payments]
    assert names == ["Аренда", "Аренда (копия)"]
    copy = store.data.other_payments[-1]
    assert copy.dates == ["2026-10-27", "2026-10-30"]
    assert "other:Аренда (копия)" in view.outgoing.row_keys

    # Вторая копия получает уникальное имя.
    view.duplicate_payment("other:Аренда", OUTGOING)
    qt_app.processEvents()
    assert [i.name for i in store.data.other_payments] == [
        "Аренда",
        "Аренда (копия)",
        "Аренда (копия) 2",
    ]
    window.close()


def test_edit_payment_card_renames_and_updates_dates(qt_app, store, monkeypatch):
    """v0.25.1: карточка вида оплаты правит название и отмеченные даты."""

    from PySide6.QtWidgets import QDialog

    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment
    from oplata.views import receipts_view as receipts_view_module

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    add_other_payment(
        store.data,
        "Аренда",
        view.outgoing.current_date().isoformat(),
        OUTGOING,
        dates=["2026-10-27"],
    )
    view.refresh_rows()
    view.refresh()
    qt_app.processEvents()

    class _FakeEdit:
        def __init__(
            self,
            direction,
            used,
            day,
            parent=None,
            name="",
            dates=(),
            vat_rate="",
            unp="",
            note="",
        ):
            self.direction = direction
            self.name_value = "Аренда офиса"
            self._dates = ["2026-10-28"]
            # v0.33.0: ставку НДС карточка тоже правит.
            self.vat_value = "20"
            # v0.55.0: УНП вида оплаты.
            self.unp_value = "100325912"
            # v0.60.0: примечание вида оплаты.
            self.note_value = "аренда по второму договору"

        def exec(self):
            return QDialog.DialogCode.Accepted

        @property
        def marked_dates(self):
            return self._dates

    monkeypatch.setattr(
        receipts_view_module, "OtherPaymentCardDialog", _FakeEdit
    )
    view.edit_payment_card("other:Аренда", OUTGOING)
    qt_app.processEvents()
    payment = store.data.other_payments[0]
    assert payment.name == "Аренда офиса"
    assert payment.dates == ["2026-10-28"]
    assert "other:Аренда офиса" in view.outgoing.row_keys
    # v0.33.0: ставка НДС сохраняется карточкой и видна в колонке.
    assert payment.vat_rate == "20"
    assert view.outgoing.amount_text("other:Аренда офиса", COL_VAT) == "20 %"
    # v0.55.0: УНП вида оплаты сохраняется карточкой.
    assert payment.unp == "100325912"
    window.close()


def test_payment_card_calendar_marks_dates(qt_app, store):
    """v0.24.0: в карточке нового вида оплаты есть календарь — клик по
    дате отмечает её, повторный клик снимает отметку."""

    from datetime import date

    from PySide6.QtWidgets import QPushButton

    from oplata.models import OUTGOING
    from oplata.views.receipts_view import (
        OtherPaymentCardDialog,
        PaymentDatesCalendar,
    )

    calendar = PaymentDatesCalendar(date(2026, 10, 29))
    buttons = calendar.findChildren(QPushButton)
    # Кнопки дней есть, месяц открыт на октябре 2026.
    assert calendar.month_label.text() == "Октябрь 2026"
    for day_number in ("27", "30"):
        for button in buttons:
            if button.text() == day_number:
                button.click()
                break
    assert calendar.marked_dates() == ["2026-10-27", "2026-10-30"]
    # Повторный клик снимает отметку.
    for button in buttons:
        if button.text() == "27":
            button.click()
            break
    assert calendar.marked_dates() == ["2026-10-30"]

    # Карточка отдаёт отмеченные даты наружу.
    dialog = OtherPaymentCardDialog(OUTGOING, set(), date(2026, 10, 29))
    assert dialog.marked_dates == []
    dialog.calendar._marked.add("2026-11-02")
    assert dialog.marked_dates == ["2026-11-02"]
    dialog.calendar.deleteLater()


def test_marked_dates_of_payment_paint_cells_red(qt_app, store):
    """v0.24.0: даты, отмеченные в карточке вида оплаты, — красные
    ячейки в таблице блока «Оплата»."""

    from datetime import date

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment
    from oplata.views.receipts_view import FIRST_DAY_COL

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    block = view.outgoing
    marked = [block.days[0].isoformat(), block.days[1].isoformat()]
    add_other_payment(
        store.data,
        "Тестовая оплата",
        block.current_date().isoformat(),
        OUTGOING,
        dates=marked,
    )
    view.refresh_rows()
    view.refresh()
    qt_app.processEvents()

    row = block.row_keys.index("other:Тестовая оплата")
    red = styles.WEEK_TABLE_MARKED_RED
    for index, day in enumerate(block.days):
        item = block.table.item(row, FIRST_DAY_COL + index)
        assert item is not None
        color = item.background().color().name()
        if day.isoformat() in marked:
            assert color == red, day
        elif day == date.today():
            # v0.38.0: сегодняшний день зелёный, даже если это выходной.
            assert color == styles.WEEK_TABLE_TODAY_GREEN_BODY, day
        elif day.weekday() >= 5:
            assert color == styles.WEEK_TABLE_WEEKEND_PINK, day
        else:
            assert color != red, day
    window.close()


def test_table_has_no_focus_frame_artifacts(qt_app, store):
    """v0.24.3: после клика Qt не рисует рамку фокуса — иначе по таблице
    шли вертикальные «штришки»-артефакты."""

    from PySide6.QtCore import QModelIndex, QRect, Qt
    from PySide6.QtWidgets import (
        QStyle,
        QStyleOptionViewItem,
        QStyledItemDelegate,
    )

    from oplata.app import MainWindow
    from oplata.styles import WIDE_SCROLLBAR_ON_TABLE
    from oplata.widgets import RowHoverMixin

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    # В стиле таблицы явно выключена рамка фокуса виджета.
    assert "outline:none" in WIDE_SCROLLBAR_ON_TABLE.replace(" ", "")
    # У таблиц блоков есть делегат с подсветкой строки — он же и снимает
    # флаг фокуса перед отрисовкой ячейки.
    assert window.receipts_view.incoming._hover_delegate is not None
    window.close()

    # Проверяем сам миксин: в базовую отрисовку состояние уходит уже без
    # флага State_HasFocus, поэтому пунктирной рамки не будет.
    class _Spy(QStyledItemDelegate):
        def __init__(self) -> None:
            super().__init__()
            self.states: list[object] = []

        def paint(self, painter, option, index):  # noqa: N802 - Qt API
            self.states.append(option.state)

    class _Probe(RowHoverMixin, _Spy):
        pass

    probe = _Probe()
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 20, 10)
    option.state |= QStyle.StateFlag.State_HasFocus
    probe.paint(None, option, QModelIndex())
    assert probe.states, "базовая отрисовка не вызывалась"
    assert not (probe.states[0] & QStyle.StateFlag.State_HasFocus)
    # Исходный option не испорчен: флаг снимается с копии.
    assert option.state & QStyle.StateFlag.State_HasFocus


def test_money_block_first_column_left_others_centered(qt_app, store):
    """v0.23.9/v0.24.2: в таблицах «Поступление» и «Оплата» шапка и
    значения — по центру, кроме первого столбца: названия — слева."""

    from PySide6.QtCore import Qt

    from oplata.app import MainWindow
    from oplata.views.receipts_view import COL_NAME, COL_NOTE

    window = MainWindow(store)
    center = Qt.AlignmentFlag.AlignHCenter
    left = Qt.AlignmentFlag.AlignLeft
    for block in (window.receipts_view.incoming, window.receipts_view.outgoing):
        table = block.table
        header = table.horizontalHeader()
        assert header.defaultAlignment() & center
        for column in range(table.columnCount()):
            # v0.60.0: примечание, как и название, — по левому краю:
            # это текст, его читают слева.
            expected = left if column in (COL_NAME, COL_NOTE) else center
            header_item = table.horizontalHeaderItem(column)
            assert header_item is not None
            assert header_item.textAlignment() & expected, column
            for row in range(table.rowCount()):
                item = table.item(row, column)
                if item is not None:
                    assert item.textAlignment() & expected, (row, column)
    window.close()
    window.close()


def test_supplier_journal_shows_delivery_columns(qt_app, store):
    """v0.23.1: журнал поставщика — дата поставки, сумма, отсрочка, оплата."""

    from datetime import date

    from PySide6.QtCore import QDate

    from oplata.dialogs.operation_dialog import OperationDialog
    from oplata.dialogs.operations_journal_dialog import OperationsJournalDialog

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    dialog = OperationDialog(store, initial_counterparty_id=supplier.id)
    dialog.date_edit.setDate(QDate(2026, 10, 26))
    dialog.amount_edit.setText("1 000,50")
    dialog._save()
    dialog.close()

    # Оплата в журнал поставщика не попадает: это журнал поставок.
    payment = OperationDialog(
        store, initial_counterparty_id=supplier.id, initial_kind="Оплата"
    )
    payment.date_edit.setDate(QDate(2026, 10, 28))
    payment.amount_edit.setText("200")
    payment._save()
    payment.close()

    window = OperationsJournalDialog(store, "supplier", counterparty_id=supplier.id)
    table = window.view.table
    assert table.rowCount() == 1
    assert [
        table.item(0, col).text() for col in range(table.columnCount())
    ] == ["26.10.2026", "1 000,50", "10", "05.11.2026"]

    # Журнал поставщика — той же ширины, что и журнал покупателя
    # (v0.23.9), и с широким скроллбаром (v0.23.2).
    assert window.width() == 800
    assert (
        table.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )

    # У покупателя журнал без «Документа» и «Комментария»: 4 колонки.
    from oplata.views.operations_view import OperationsView

    cust_view = OperationsView(store, counterparty_kind="customer")
    # v0.27.3: колонка «Контрагент» убрана — осталось три.
    assert cust_view.table.columnCount() == 3
    assert [
        cust_view.table.horizontalHeaderItem(col).text() for col in range(3)
    ] == ["Тип", "Дата", "Сумма"]
    window.close()


def test_supplier_total_column_shows_whole_debt(qt_app, store, monkeypatch):
    """Столбец «Итого» — вся текущая задолженность поставщика (v0.22.1).

    «Итого» не зависит от показанной недели и от сегодняшней даты: это
    весь остаток долга по всем срокам. Просроченное лежит в столбце
    «Просрочено» (срок раньше даты), а «Неделя» — сроки внутри
    показанной недели. Дата в тесте зафиксирована, иначе расчёт
    менялся бы вместе с календарём.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import add_delivery
    from oplata.views import week_view as week_view_module

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 27)

    monkeypatch.setattr(week_view_module, "date", _FixedDate)

    supplier = store.data.suppliers[0]
    # Срок 27.10 (700), 05.11 (900 — следующая неделя), 20.10 (150 —
    # просрочка). Всего задолженность 1 750.
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-10-27", Decimal("700"))
    add_delivery(store.data, supplier.id, "2026-10-20", "2026-11-05", Decimal("900"))
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("150"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    # v0.55.0: строки таблицы — по алфавиту, поэтому строку поставщика
    # ищем в представлении.
    row = week_row(view, supplier.name)
    table = view.table

    offset = view.lead_offset  # v0.27.3: узкая колонка условий
    view.goto_date(date(2026, 10, 26))
    # v0.28.3: шапка — «Задолженность» целиком; подписи тоже полные.
    assert table.item(1, 1 + offset).text() == "Задолженность"
    assert table.item(2, 1 + offset).text() == "Итого"
    assert table.item(2, 2 + offset).text() == "Просрочено"
    assert table.item(2, 3 + offset).text() == "Неделя"
    # Итого — 700 + 900 + 150; просрочка — 150 (срок 20.10); за неделю
    # (26.10–01.11) — 700.
    assert table.item(row, 1 + offset).text() == "1 750,00"
    assert table.item(row, 2 + offset).text() == "150,00"
    assert table.item(row, 3 + offset).text() == "700,00"

    # Следующая неделя: «Итого» то же, «за неделю» — срок 05.11.
    view.goto_date(date(2026, 10, 26) + timedelta(days=7))
    assert table.item(row, 1 + offset).text() == "1 750,00"
    assert table.item(row, 2 + offset).text() == "150,00"
    assert table.item(row, 3 + offset).text() == "900,00"

    # Наступил следующий день: просрочка выросла (150 + 700), «Итого»
    # не изменилось — это весь долг, а не только просроченный.
    class _NextDay(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 28)

    monkeypatch.setattr(week_view_module, "date", _NextDay)
    view.refresh()
    assert table.item(row, 1 + offset).text() == "1 750,00"
    assert table.item(row, 2 + offset).text() == "850,00"  # 150 + 700 просрочены
    window.close()


def test_supplier_payment_reduces_overdue_first_then_week(qt_app, store, monkeypatch):
    """Оплата гасит сначала просрочку, потом долг недели.

    Сроки в тесте фиксированные (20.10 и 29.10), поэтому «сегодня»
    закрепляем на 27.10: иначе расчёт менялся бы вместе с календарём.
    Столбец «Итого» (v0.22.1) показывает весь остаток долга
    поставщику: до оплаты 1 200, после оплаты 900 — 300.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from datetime import date as real_date

    from oplata.app import MainWindow
    from oplata.models import add_delivery, set_daily_operation
    from oplata.views import week_view as week_view_module

    class _FixedDate(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 10, 27)

    monkeypatch.setattr(week_view_module, "date", _FixedDate)

    supplier = store.data.suppliers[0]
    # Просрочка 700 (срок 20.10) + долг недели 500 (срок 29.10).
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-20", Decimal("700"))
    add_delivery(store.data, supplier.id, "2026-10-10", "2026-10-29", Decimal("500"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    row = week_row(view, supplier.name)  # v0.55.0: строки — по алфавиту
    table = view.table
    view.goto_date(date(2026, 10, 26))

    offset = view.lead_offset  # v0.27.3: узкая колонка условий
    assert table.item(row, 1 + offset).text() == "1 200,00"  # Итого (700+500)
    assert table.item(row, 2 + offset).text() == "700,00"  # просрочено
    assert table.item(row, 3 + offset).text() == "500,00"  # за неделю

    # Оплата 900: сначала гасит просрочку 700, остаток 200 — неделю.
    set_daily_operation(
        store.data, supplier.id, "2026-10-27", "Оплата", Decimal("900")
    )
    view.refresh()
    assert table.item(row, 1 + offset).text() == "300,00"  # Итого: 1 200−900
    assert table.item(row, 2 + offset).text() == ""  # просрочка погашена
    assert table.item(row, 3 + offset).text() == "300,00"  # 500 − 200

    # Недоплата: оплата 100 вместо 900 — остаток просрочки переносится.
    store.data.operations[:] = [
        op for op in store.data.operations if op.kind != "Оплата"
    ]
    set_daily_operation(
        store.data, supplier.id, "2026-10-20", "Оплата", Decimal("100")
    )
    view.refresh()
    assert table.item(row, 1 + offset).text() == "1 100,00"  # Итого: 1 200 − 100
    assert table.item(row, 2 + offset).text() == "600,00"  # 700 − 100
    assert table.item(row, 3 + offset).text() == "500,00"  # оплата ушла в просрочку
    # Следующая неделя (02.11–08.11): просрочка всё та же 600, а долг со
    # сроком 29.10 в эту неделю не попадает («за неделю» пусто).
    view.goto_date(date(2026, 10, 26) + timedelta(days=7))
    assert table.item(row, 1 + offset).text() == "1 100,00"
    assert table.item(row, 2 + offset).text() == "600,00"
    assert table.item(row, 3 + offset).text() == ""
    window.close()


def test_supplier_with_realization_never_shows_overdue(qt_app, store):
    """Поставщик «Реализация»: срока нет, «просрочено» пусто, «Итого» — долг."""
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.suppliers.append(Counterparty(name="Реализация", realization=True))
    store.save()
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table
    names = [s.name for s in view.counterparties()]
    row = 4 + names.index("Реализация")

    from oplata.models import add_delivery

    partner = view.counterparties()[names.index("Реализация")]
    # Поставка со сроком в прошлом — но у «Реализации» срока нет.
    add_delivery(store.data, partner.id, "2026-10-01", "2026-10-05", Decimal("400"))
    view.refresh()
    # v0.22.1: «Итого» — вся задолженность, «просрочено» у «Реализации»
    # не бывает, срок 05.10 вне недели 26.10–01.11.
    offset = view.lead_offset  # v0.27.3: узкая колонка условий
    assert table.item(row, 1 + offset).text() == "400,00"
    assert table.item(row, 2 + offset).text() == ""
    assert table.item(row, 3 + offset).text() == ""
    # v0.27.3: в узкой колонке у «Реализации» стоит «Р».
    assert table.item(row, 1).text() == "Р"
    window.close()


def test_week_table_fonts_are_uniform(qt_app, store):
    """v0.16.0: в недельных таблицах один размер шрифта (был разнобой)."""
    from oplata import styles
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    # v0.16.0: разнобоя 13/14/16 px больше нет. v0.55.0: три размера по
    # ролям — основной fs(12), названия контрагентов мельче (fs(10),
    # просьба пользователя), подписи итоговых колонок — fs(11).
    from oplata.views.week_view import LEAD_FONT_SIZE, NAME_FONT_SIZE

    expected = {styles.fs(12), LEAD_FONT_SIZE, NAME_FONT_SIZE}
    for view in (window.suppliers_view, window.customers_view):
        table = view.table
        sizes = set()
        for row in range(table.rowCount()):
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item is not None and item.text():
                    sizes.add(item.font().pixelSize())
        assert sizes <= expected, f"лишние размеры шрифтов: {sorted(sizes)}"
        # Названия — мельче, подписи итоговых — средние. В демо-данных
        # покупателей нет, поэтому строку названия проверяем, если она есть.
        if view.counterparties():
            assert table.item(4, 0).font().pixelSize() == NAME_FONT_SIZE
        offset = view.lead_offset
        for column in range(1 + offset, 4 + offset):
            assert table.item(2, column).font().pixelSize() == LEAD_FONT_SIZE
        # Заголовок недели и итог справа — одного размера.
        # v0.23.1: шрифт заголовка «Неделя: …» — как у кнопки «Сегодня»
        # (просьба пользователя): базовый кегль и начертание кнопки из
        # APP_STYLESHEET (fs(13) = 17px, weight 600), без отдельного
        # увеличения до fs(14).
        assert view.week_label.font().pixelSize() == styles.fs(13)
        assert view.week_label.font().weight() == 600
        assert view.right_value_label.font().pixelSize() == styles.fs(14)
    window.close()


def test_base_switch_button_changes_database_and_header(qt_app, store, monkeypatch, tmp_path):
    """v0.17.0: кнопка МИР/КОРЕЛИЧИ — две не связанные базы."""
    from oplata import storage, styles
    from oplata.app import MainWindow
    from oplata.models import Counterparty

    # Данные теста целиком во временном каталоге: переключение базы не
    # должно трогать настоящий каталог данных пользователя.
    monkeypatch.setattr(storage, "data_dir", lambda: tmp_path)

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    # Кнопка стоит в шапке и вдвое крупнее вкладок («Конфигурация»).
    nav_font = window.nav_buttons[-1].font().pixelSize()
    assert window.base_button.text() == storage.BASE_TITLES[store.base_key]
    assert window.base_button.font().pixelSize() == nav_font * 2

    mir_path = store.path
    store.data.suppliers.append(Counterparty(name="Поставщик МИР"))
    store.save()

    window.base_button.click()
    qt_app.processEvents()
    assert window.base_button.text() == "КОРЕЛИЧИ"
    assert styles.BASE_MAROON in window.header.styleSheet()  # шапка вишнёвая
    assert store.path == tmp_path / "data-korelichi.json"
    assert not any(s.name == "Поставщик МИР" for s in store.data.suppliers)
    # Выбор базы запоминается для следующего запуска.
    assert storage.load_active_base() == storage.BASE_KORELICHI

    window.base_button.click()
    qt_app.processEvents()
    assert window.base_button.text() == "МИР"
    assert styles.BASE_GREEN in window.header.styleSheet()  # шапка зелёная
    assert store.path == mir_path
    assert any(s.name == "Поставщик МИР" for s in store.data.suppliers)
    # Таблица поставщиков перерисована по активной базе.
    names = [
        window.suppliers_view.table.item(row, 0).text()
        for row in range(4, window.suppliers_view.table.rowCount())
    ]
    assert "Поставщик МИР" in names
    window.close()


def test_user_rights_limit_pages_base_and_input(qt_app, store, monkeypatch, tmp_path):
    """v0.18.0: права пользователя — вкладки, база, ввод данных."""
    from oplata import access as access_module
    from oplata.app import MainWindow

    # Файлы пользователей теста — во временном каталоге.
    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)

    admin, manager, user_mir, user_kor = access_module.default_users()

    window = MainWindow(store, access_module.Access(admin))
    window.show()
    qt_app.processEvents()
    assert [b.text() for b in window.nav_buttons if not b.isHidden()] == [
        "Поступление и оплаты",
        "Отчеты ККМ",  # v0.42.0
        "Поставщики",
        "Покупатели",
        "Конфигурация",
    ]
    assert window.suppliers_view.read_only is False
    assert window.kkm_view.read_only is False
    assert not window.data_view.admin_panel.isHidden()
    window.close()

    window = MainWindow(store, access_module.Access(manager))
    window.show()
    qt_app.processEvents()
    # Руководитель видит все вкладки, но без права ввода.
    assert all(not b.isHidden() for b in window.nav_buttons)
    assert window.suppliers_view.read_only is True
    assert window.kkm_view.read_only is True
    assert window.receipts_view.incoming.read_only is True
    # v0.23.1: «Администрирование» видно всем, кто открыл вкладку
    # «Конфигурация» — сама вкладка закрыта паролем (просьба
    # пользователя: кнопка «Очистить базу» должна быть доступна).
    assert not window.data_view.admin_panel.isHidden()
    window.close()

    window = MainWindow(store, access_module.Access(user_mir))
    window.show()
    qt_app.processEvents()
    # Пользователь МИР: только «Покупатели», база МИР, только просмотр.
    # v0.50.0: кнопка «Конфигурация» видна и у него (просьба
    # пользователя) — вкладка по-прежнему за паролем.
    assert [b.text() for b in window.nav_buttons if not b.isHidden()] == [
        "Покупатели",
        "Конфигурация",
    ]
    assert window.base_button.isHidden()
    assert store.base_key == "mir"
    assert window.customers_view.read_only is True
    window.close()

    window = MainWindow(store, access_module.Access(user_kor))
    window.show()
    qt_app.processEvents()
    assert store.base_key == "korelichi"
    assert [b.text() for b in window.nav_buttons if not b.isHidden()] == [
        "Покупатели",
        "Конфигурация",  # v0.50.0: видна всегда
    ]
    window.close()


def test_users_are_listed_in_configuration_and_password_gate(
    qt_app, store, monkeypatch, tmp_path
):
    """v0.18.0: четыре пользователя в «Конфигурации» и пароль на раздел."""
    from oplata import access as access_module
    from oplata.app import MainWindow

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)

    window = MainWindow(store, access_module.Access(access_module.default_users()[0]))
    window.show()
    qt_app.processEvents()

    # Плашка свёрнута — раскрываем, иначе её кнопки невидимы.
    window.data_view.users_panel.set_expanded(True)
    table = window.data_view.users_table
    assert table.rowCount() == 4
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert names == [
        "Администратор",
        "Руководитель",
        "Пользователь МИР",
        "Пользователь КОРЕЛИЧИ",
    ]
    # У администратора отмечены все вкладки и право ввода.
    assert table.item(0, 1).checkState() is not None
    # Вкладка «Конфигурация» не открыта, поэтому проверяем не «видна ли
    # кнопка», а «не скрыта ли она правами».
    assert not window.data_view.users_save_btn.isHidden()

    # Пароль: придумываем, проверяем верный и неверный.
    assert not access_module.has_password()
    access_module.set_password("1234")
    assert access_module.has_password()
    assert access_module.check_password("1234")
    assert not access_module.check_password("0000")

    # Добавление пользователя и сохранение списка.
    window.data_view.users_add_btn.click()
    qt_app.processEvents()
    assert table.rowCount() == 5
    table.item(4, 0).setText("Кладовщик")
    window.data_view.users_save_btn.click()
    qt_app.processEvents()
    assert "Кладовщик" in [user.name for user in access_module.load_users()]
    window.close()


def test_amount_font_is_chosen_to_fit_day_column(qt_app, store):
    """v0.18.0: «88 888,88» влезает в колонку дня при любой её ширине."""
    from PySide6.QtGui import QFont, QFontMetrics

    from oplata.app import MainWindow
    from oplata.views.week_view import MAX_AMOUNT_TEXT, TABLE_DAYS

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    view = window.suppliers_view
    # Воскресенье из таблицы убрали: дней шесть.
    assert view.table_days == TABLE_DAYS == 6
    assert view.table.columnCount() == (
        1
        + view.lead_offset
        + view.lead_columns
        + TABLE_DAYS * view.columns_per_day
    )

    def advance_for(pixel_size: int) -> int:
        font = QFont(view.table.font())
        font.setPixelSize(pixel_size)
        font.setBold(True)
        return QFontMetrics(font).horizontalAdvance(MAX_AMOUNT_TEXT)

    # Подбор идёт по самой узкой колонке дня (в v0.18.0 и ранее бралась
    # ширина итоговой колонки, из-за чего сумма вылезала за ячейку).
    captured: list[int] = []
    original = view._fit_amount_font
    view._fit_amount_font = lambda width: captured.append(width)
    try:
        view._fit_columns_to_width()
    finally:
        view._fit_amount_font = original
    assert captured, "подгонка шрифта не вызывалась"
    day_widths = [
        view.table.columnWidth(view._day_column(day, sub))
        for day in range(view.table_days)
        for sub in range(view.columns_per_day)
    ]
    assert captured[-1] == min(day_widths)

    # Метрики шрифта в headless-среде без системных шрифтов могут быть
    # недоступны — тогда проверку ширины текста пропускаем.
    if advance_for(8) > 60 - 8:
        window.close()
        return

    for width in (60, 72, 80, 100, 160):
        view._fit_amount_font(width)
        item = view._amount_items[0] if view._amount_items else None
        assert item is not None
        advance = advance_for(item.font().pixelSize())
        assert advance <= width - 8, (
            f"колонка {width} px: «{MAX_AMOUNT_TEXT}» занимает {advance} px"
        )
    window.close()


def test_clear_database_confirms_and_exports(qt_app, store, monkeypatch, tmp_path):
    """v0.23.1: «Очистить базу» — подтверждение, копии в «Загрузки»."""

    from pathlib import Path

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QMessageBox

    from oplata.models import Operation
    from oplata.views.data_view import DataView

    view = DataView(store, on_calendar_saved=lambda: None)
    # Настоящий путь — внутри папки «Загрузки» системной учётной записи.
    assert DataView.BACKUP_DIR_NAME == "База поставщики оплата"
    real_dir = view._backup_dir()
    downloads = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DownloadLocation
    )
    assert real_dir.name == "База поставщики оплата"
    assert not downloads or real_dir.parent == Path(downloads)
    # Дальше копии ищем в подменённой папке: путь к «Загрузкам» на
    # машине сборки другой, а проверять надо сам факт копий.
    backup_dir = tmp_path / "Загрузки" / "База поставщики оплата"
    monkeypatch.setattr(DataView, "_backup_dir", lambda self: backup_dir)

    supplier = next(s for s in store.data.suppliers if s.name == "ггг")
    store.data.operations.append(
        Operation(kind="Приход", counterparty_id=supplier.id)
    )
    store.save()

    # «Нет» — база цела, копий нет.
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
    view._clear_database()
    assert store.data.suppliers and store.data.operations
    assert not backup_dir.exists()

    # «Да» — выгрузка в Excel и копия data.json лежат в папке Загрузок,
    # база пуста.
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    view._clear_database()
    assert list(backup_dir.glob("oplata-базы-*.xlsx"))
    assert list(backup_dir.glob("data-backup-*.json"))
    assert store.data.suppliers == []
    assert store.data.customers == []
    assert store.data.operations == []
    assert store.data.cash_receipts == []
    assert store.data.other_payments == []
    assert store.data.day_overrides == {}
    assert "База очищена" in view.admin_status_label.text()

    # Кнопка видна любому пользователю: вкладка «Конфигурация» за паролем.
    # (isHidden, а не isVisible: само окно в тесте не показано.)
    view.set_read_only(True, can_admin=False)
    assert not view.admin_panel.isHidden()
    assert not view.clear_button.isHidden()
    view.close()


def test_base_exchange_buttons_are_admin_only(qt_app, store):
    """«Выгрузить базы»/«Загрузить базы» видит только администратор."""
    from oplata.access import ALL_PAGES, Access, User, save_users
    from oplata.app import MainWindow

    admin = User(
        id="admin",
        name="Администратор",
        pages=list(ALL_PAGES),
        can_edit=True,
        can_switch_base=True,
    )
    viewer = User(id="viewer", name="Наблюдатель", pages=[0])
    save_users([admin, viewer])

    window = MainWindow(store, access=Access(admin))
    window.show()  # без показа isVisible() у кнопок был бы False
    qt_app.processEvents()
    assert window.export_button.isVisible()
    assert window.import_button.isVisible()
    # Кнопки работают с обеими базами сразу — подписи во множественном числе.
    assert window.export_button.text() == "Выгрузить базы"
    assert window.import_button.text() == "Загрузить базы"

    # Сменили текущего пользователя в «Конфигурации» — кнопки скрылись.
    window.apply_current_user(viewer)
    assert not window.export_button.isVisible()
    assert not window.import_button.isVisible()

    window.apply_current_user(admin)
    assert window.export_button.isVisible()
    assert window.import_button.isVisible()
    window.close()


def test_base_exchange_export_and_import_both_bases(
    qt_app, store, tmp_path, monkeypatch
):
    """Кнопки шапки: обе базы — в один Excel и обратно (v0.20.0)."""
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    window = MainWindow(store)
    window._config_unlocked = True
    # Данные только этого прогона: пустая база с одной записью.
    store.data.suppliers = [
        Counterparty(name="Тестовый поставщик", deferment_days=7)
    ]
    store.save()

    xlsx = tmp_path / "bases.xlsx"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(xlsx), "")),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(xlsx), "")),
    )
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: messages.append(str(args[-1]))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: messages.append("ОШИБКА: " + str(args[-1]))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )

    window.export_bases_to_excel()
    assert xlsx.exists()
    names = load_workbook(xlsx).sheetnames
    assert any(name.startswith("МИР.") for name in names)
    assert any(name.startswith("КОРЕЛИЧИ.") for name in names)

    # Стираем данные активной базы и загружаем их обратно из файла.
    store.data.suppliers = []
    window.import_bases_from_excel()
    assert [item.name for item in store.data.suppliers] == ["Тестовый поставщик"]
    assert store.data.suppliers[0].deferment_days == 7
    assert not any(message.startswith("ОШИБКА") for message in messages)
    window.close()


def test_receipts_source_row_click_opens_journal(qt_app, store):
    """Клик по виду поступления открывает журнал операций (v0.20.0)."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import CASH_SOURCE, CashReceipt
    from oplata.views.receipts_view import COL_NAME
    from oplata.views.receipts_view import JournalPopup

    window = MainWindow(store)
    view = window.receipts_view
    view.incoming.set_date(date(2026, 10, 27))
    store.data.cash_receipts = [
        CashReceipt(
            date="2026-10-27",
            source=CASH_SOURCE,
            amount="1000.00",
            comment="прогон",
        )
    ]
    store.save()
    view.refresh()

    row = view.incoming.row_keys.index("cash")
    # Клик по названию вида — журнал; клик по «Сумме» остаётся вводом.
    view.incoming.table.cellClicked.emit(row, COL_NAME)
    # События не прокручиваем: всплывающее окно закрылось бы «кликом вне»
    # от хвоста событий предыдущего теста.
    popup = view._popup
    assert isinstance(popup, JournalPopup)
    assert popup.table.item(0, 0).text() == "27.10.2026"
    assert popup.table.item(0, 1).text() == "1 000,00"
    assert popup.table.item(0, 2).text() == "прогон"
    popup.close()
    window.close()


def test_other_payment_card_asks_name_only(qt_app, monkeypatch):
    """Карточка нового вида оплаты: название, календарь и «Сохранить».

    Полей «Дата» и «Сумма» в карточке нет: дата берётся из блока
    «Оплата», сумма вводится в ячейку таблицы. С v0.24.1 в карточке
    есть календарь: отмеченные даты красят ячейки красным, поэтому
    окно больше не делаем вдвое шире содержимого — иначе на ноутбуке
    карточка не влезала в экран (v0.24.2).
    """
    from datetime import date

    from PySide6.QtWidgets import QApplication, QDialog

    from oplata.views.receipts_view import OtherPaymentCardDialog

    card = OtherPaymentCardDialog("out", {"Налоги"}, date(2026, 10, 27))
    # В карточке одно поле ввода — название; даты и суммы нет.
    assert not hasattr(card, "date_edit")
    assert not hasattr(card, "amount_edit")
    # Календарь есть и он не шире экрана (v0.24.2).
    assert card.calendar is not None
    assert card.width() <= max(QApplication.primaryScreen().availableGeometry().width() - 80, 420)

    card.name_edit.setText("Аренда")
    card._try_accept()
    assert card.result() == QDialog.DialogCode.Accepted
    assert card.name_value == "Аренда"
    card.close()

    # Пустое имя и занятое имя не сохраняются.
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *args, **kwargs: None)
    )
    empty = OtherPaymentCardDialog("out", set(), date(2026, 10, 27))
    empty._try_accept()
    assert not empty.isVisible() or empty.result() != QDialog.DialogCode.Accepted
    empty.close()

    # v0.63.0: занятое имя — не тупик. Программа предлагает свободное
    # имя «аренда (2)» (УНП у видов повторяется, имя — нет), а решение
    # остаётся за пользователем.
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
    busy = OtherPaymentCardDialog("out", {"Аренда"}, date(2026, 10, 27))
    busy.name_edit.setText("аренда")  # занято без учёта регистра
    busy._try_accept()
    assert busy.result() != QDialog.DialogCode.Accepted
    busy.close()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    spare = OtherPaymentCardDialog("out", {"Аренда"}, date(2026, 10, 27))
    spare.name_edit.setText("аренда")
    spare._try_accept()
    assert spare.result() == QDialog.DialogCode.Accepted
    assert spare.name_value == "аренда (2)"
    spare.close()


def test_users_table_current_column_applies_user(qt_app, store):
    """Столбец «Текущий»: выбор пользователя применяется сразу (v0.20.0)."""
    from oplata.access import (
        ALL_PAGES,
        IDX_CUSTOMERS,
        Access,
        User,
        load_current_user,
        save_current_user,
        save_users,
    )
    from oplata.app import MainWindow
    from oplata.storage import BASE_MIR

    admin = User(
        id="admin",
        name="Администратор",
        pages=list(ALL_PAGES),
        can_edit=True,
        can_switch_base=True,
    )
    user_mir = User(
        id="mir", name="Пользователь МИР", pages=[IDX_CUSTOMERS], base=BASE_MIR
    )
    save_users([admin, user_mir])
    save_current_user(admin)

    window = MainWindow(store, access=Access(admin))
    window._config_unlocked = True
    table = window.data_view.users_table
    headers = [
        table.horizontalHeaderItem(col).text() for col in range(table.columnCount())
    ]
    assert headers[-1] == "Текущий"
    current_col = window.data_view.users_col_current
    # Галочка стоит у текущего пользователя (администратора).
    assert (
        table.item(0, current_col).checkState() == Qt.CheckState.Checked
    )
    assert table.item(1, current_col).checkState() == Qt.CheckState.Unchecked

    # Отмечаем второго — галочка переезжает, и права применяются сразу.
    table.item(1, current_col).setCheckState(Qt.CheckState.Checked)
    assert table.item(0, current_col).checkState() == Qt.CheckState.Unchecked
    window.data_view._save_users()

    assert load_current_user().name == "Пользователь МИР"
    assert window.access.title == "Пользователь МИР"
    # У пользователя без права ввода кнопок выгрузки быть не должно.
    assert not window.export_button.isVisible()
    assert not window.nav_buttons[1].isVisible()  # вкладка «Поставщики»
    window.close()


def test_date_edit_arrow_image_is_shipped(qt_app):
    """v0.28.2: широкая кнопка календаря не должна убирать стрелку.

    Как только в QSS появляется правило для ``QDateEdit::drop-down``,
    Qt перестаёт рисовать нативную стрелку — именно она и пропадала на
    Windows в v0.8.0 (ADR-015). Поэтому стрелку задаём своей
    картинкой: правило и файл должны существовать вместе.
    """

    import re
    from pathlib import Path

    from oplata import styles

    qss = styles.APP_STYLESHEET
    assert "QDateEdit::drop-down" in qss

    match = re.search(
        r"QDateEdit::down-arrow\s*\{.*?image:\s*url\(\"([^\"]+)\"\)",
        qss,
        re.DOTALL,
    )
    assert match, "в QSS нет картинки стрелки — поле даты останется без стрелки"
    assert Path(match.group(1)).is_file(), "файл стрелки не найден"
    assert str(styles.DATE_ARROW_FILE).endswith("arrow-down.png")


def test_date_edit_arrow_image_is_in_bundle(qt_app):
    """v0.28.2: стрелка лежит в ресурсах, которые попадают в сборку."""

    from oplata import styles

    assert styles.DATE_ARROW_FILE.parent.name == "assets"
    assert styles.DATE_ARROW_FILE.is_file()

def test_debt_headers_are_written_in_full(qt_app, store):
    """v0.28.3: подписи итоговых колонок — целиком, без переноса.

    «Задолженность» стоит посередине объединённой ячейки, а
    «Просрочено» и «Неделя» написаны полностью: раньше колонка была
    слишком узкой, и слово обрезалось.
    """

    from PySide6.QtGui import QFont, QFontMetrics

    from oplata import styles
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.suppliers_view
    offset = view.lead_offset

    debt_header = view.table.item(1, 1 + offset)
    assert debt_header.text() == "Задолженность"
    assert "\\n" not in debt_header.text(), "шапку снова разорвали на две строки"
    assert debt_header.textAlignment() == Qt.AlignmentFlag.AlignCenter

    assert view.table.item(2, 2 + offset).text() == "Просрочено"
    assert view.table.item(2, 3 + offset).text() == "Неделя"

    # Метрики шрифта в headless-среде без системных шрифтов могут быть
    # недоступны — тогда проверку ширины пропускаем (как в
    # test_amount_font_fits_day_columns).
    probe = QFont()
    probe.setPixelSize(8)
    probe.setBold(True)
    if QFontMetrics(probe).horizontalAdvance("88 888,88") > 52:
        window.close()
        return

    # v0.55.0: подписи итоговых колонок набраны своим шрифтом
    # (LEAD_FONT_SIZE), поэтому мерим по фактическому шрифту ячейки,
    # а не по базовому кеглю таблицы.
    for column, text in (
        (2 + offset, "Просрочено"),
        (3 + offset, "Неделя"),
        (1 + offset, "Итого"),
    ):
        metrics = QFontMetrics(view.table.item(2, column).font())
        advance = metrics.horizontalAdvance(text)
        assert advance <= view.min_widths[column] - 8, (
            f"подпись «{text}» ({advance} px) обрежется в колонке "
            f"{view.min_widths[column]} px"
        )
    window.close()


def test_customer_invoice_mark_toggles_by_click(qt_app, store):
    """v0.29.0: узкая колонка метки — красная при вводе, клик переключает.

    Ввод реализации зажигает красную метку («в работе»); клик по ней
    делает метку зелёной («выписано»), повторный клик — снова красной.
    Очистка суммы убирает метку совсем.
    """

    from datetime import date

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import Counterparty
    from oplata.views.week_view import FIRST_DATA_ROW

    buyer = Counterparty(name="Покупатель М")
    store.data.customers.append(buyer)
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.customers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table
    row = FIRST_DATA_ROW
    day = view._days[0]
    sale_column = _DAY(view, 0, 0)
    mark_column = _DAY(view, 0, 1)

    def mark_color():
        brush = table.item(row, mark_column).background()
        return brush.color().name()

    # Без реализации метки нет — ячейка пустая, как обычная ячейка дня.
    assert view._mark_color(buyer.id, day) is None

    table.item(row, sale_column).setText("1 000")
    assert view._mark_color(buyer.id, day) == styles.MARK_IN_WORK
    assert mark_color() == styles.MARK_IN_WORK
    assert store.data.operations[-1].invoiced is False

    view._on_item_clicked(table.item(row, mark_column))
    assert view._mark_color(buyer.id, day) == styles.MARK_INVOICED
    assert mark_color() == styles.MARK_INVOICED
    assert store.data.operations[-1].invoiced is True

    view._on_item_clicked(table.item(row, mark_column))  # реверс
    assert view._mark_color(buyer.id, day) == styles.MARK_IN_WORK
    assert store.data.operations[-1].invoiced is False

    table.item(row, sale_column).setText("")
    assert view._mark_color(buyer.id, day) is None
    # v0.38.0: фон ячейки метки — как у колонки дня (сегодня зелёный).
    assert mark_color() == (
        styles.WEEK_TABLE_TODAY_GREEN_BODY
        if day == date.today()
        else styles.PANEL_BG
    )
    window.close()


def test_customer_totals_split_invoiced_and_work(qt_app, store):
    """v0.29.0: «Всего» = «Выписано» + «В работе».

    «Выписано» — реализация с зелёной меткой минус оплаты; «В работе» —
    реализация с красной меткой, оплаты из неё не вычитаются.
    """

    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import (
        Counterparty,
        add_customer_receipt,
        add_realization,
    )
    from oplata.views.week_view import FIRST_DATA_ROW, TOTAL_ROW

    buyer = Counterparty(name="Покупатель Н")
    store.data.customers.append(buyer)
    store.save()
    # Реализация 500 (новая — красная) и 1 000 (помечена выписанной).
    add_realization(store.data, buyer.id, "2026-10-26", Decimal("500"))
    add_realization(store.data, buyer.id, "2026-10-27", Decimal("1000"))
    add_customer_receipt(store.data, buyer.id, "2026-10-28", Decimal("300"))
    for op in store.data.operations:
        if op.date == "2026-10-27":
            op.invoiced = True
    store.save()

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.customers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table
    row = FIRST_DATA_ROW

    # Выписано: 1 000 − 300 = 700. В работе: 500. Всего: 1 200.
    assert table.item(row, 1).text() == "1 200,00"
    assert table.item(row, 2).text() == "700,00"
    assert table.item(row, 3).text() == "500,00"
    assert table.item(TOTAL_ROW, 2).text() == "700,00"
    assert table.item(TOTAL_ROW, 3).text() == "500,00"

    # Переключаем метку 27.10 на красную: вся реализация в работе.
    view._on_item_clicked(table.item(row, _DAY(view, 1, 1)))
    assert table.item(row, 2).text() == "-300,00"
    assert table.item(row, 3).text() == "1 500,00"
    assert table.item(row, 1).text() == "1 200,00"
    window.close()


def test_old_sales_without_mark_are_invoiced(qt_app, store):
    """v0.29.0: старая реализация (без отметки) считается выписанной."""

    import json
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import Counterparty
    from oplata.storage import DataStore
    from oplata.views.week_view import FIRST_DATA_ROW

    buyer = Counterparty(name="Покупатель О")
    store.data.customers.append(buyer)
    buyer_id = buyer.id
    store.save()

    # Сохраняем данные так, как они выглядели до v0.29.0: поля
    # «invoiced» в операциях нет вовсе.
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["operations"] = [
        {
            "kind": "Реализация",
            "counterparty_id": buyer_id,
            "date": "2026-10-27",
            "amount": "800.00",
            "document": "",
            "comment": "",
            "due_date": "",
            "id": "old-sale-1",
        }
    ]
    store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    reloaded = DataStore(store.path)
    (old_sale,) = reloaded.data.operations
    assert old_sale.invoiced is True, "старая реализация должна быть выписанной"

    window = MainWindow(reloaded)
    window.show()
    qt_app.processEvents()
    view = window.customers_view
    view.goto_date(date(2026, 10, 26))
    table = view.table
    row = FIRST_DATA_ROW
    assert table.item(row, 2).text() == "800,00"   # «Выписано»
    assert table.item(row, 3).text() == ""         # «В работе» — пусто
    assert table.item(row, 1).text() == "800,00"   # «Всего» не изменилось
    window.close()


def test_customer_debt_header_is_written_in_full(qt_app, store):
    """v0.29.0: в шапке покупателей «Задолженность», а не «ЗАДОЛЖ.»."""

    from PySide6.QtGui import QFont, QFontMetrics

    from oplata import styles
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.customers_view

    header = view.table.item(1, 1)
    assert header.text() == "Задолженность"
    assert header.textAlignment() == Qt.AlignmentFlag.AlignCenter
    assert view.table.columnSpan(1, 1) == 3
    assert view.table.item(2, 1).text() == "Всего"
    assert view.table.item(2, 2).text() == "Выписано"
    assert view.table.item(2, 3).text() == "В работе"

    probe = QFont()
    probe.setPixelSize(8)
    probe.setBold(True)
    if QFontMetrics(probe).horizontalAdvance("88 888,88") > 52:
        window.close()
        return

    font = QFont()
    font.setPixelSize(styles.fs(12))
    font.setBold(True)
    metrics = QFontMetrics(font)
    for column, text in ((1, "Всего"), (2, "Выписано"), (3, "В работе")):
        advance = metrics.horizontalAdvance(text)
        assert advance <= view.min_widths[column] - 8, (
            f"подпись «{text}» ({advance} px) обрежется в колонке "
            f"{view.min_widths[column]} px"
        )
    window.close()


def test_excel_round_trip_keeps_invoice_mark(store, tmp_path):
    """v0.29.0: отметка «выписано» переживает выгрузку в Excel и обратно."""

    pytest.importorskip("openpyxl")

    from decimal import Decimal

    from oplata.excelio import export_bases, import_bases
    from oplata.models import Counterparty, add_realization
    from oplata.storage import BASE_MIR

    buyer = Counterparty(name="Покупатель П")
    store.data.customers.append(buyer)
    add_realization(store.data, buyer.id, "2026-10-26", Decimal("100"))
    add_realization(store.data, buyer.id, "2026-10-27", Decimal("200"))
    for op in store.data.operations:
        if op.date == "2026-10-27":
            op.invoiced = True
    store.save()

    path = export_bases({BASE_MIR: store.data}, tmp_path / "oplata.xlsx")
    (imported,) = import_bases(path).values()

    marks = {
        op.date: op.invoiced
        for op in imported.operations
        if op.counterparty_id == buyer.id
    }
    assert marks == {"2026-10-26": False, "2026-10-27": True}


def test_customer_totals_are_cumulative_across_weeks(qt_app, store):
    """v0.29.1: итоги покупателя считаются за всё время, а не за неделю.

    Раньше колонки «Всего»/«Выписано»/«В работе» считались только в
    границах отображаемой недели, и при переходе на следующую неделю
    долг «пропадал» — колонки становились пустыми. Теперь долг
    накапливается, как «Итого» у поставщиков и как задолженность
    покупателей в «Сводке».
    """

    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import (
        Counterparty,
        add_customer_receipt,
        add_realization,
    )
    from oplata.views.week_view import FIRST_DATA_ROW, TOTAL_ROW

    buyer = Counterparty(name="Покупатель Р")
    store.data.customers.append(buyer)
    # Неделя 26.10–01.11: реализация 1 000 (выписана) и оплата 200.
    add_realization(store.data, buyer.id, "2026-10-27", Decimal("1000"))
    add_customer_receipt(store.data, buyer.id, "2026-10-29", Decimal("200"))
    for op in store.data.operations:
        op.invoiced = True
    # Неделя 02.11–08.11: реализация 5 000 (ещё в работе).
    add_realization(store.data, buyer.id, "2026-11-03", Decimal("5000"))
    store.save()

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.customers_view
    table = view.table
    row = FIRST_DATA_ROW

    for monday, label in (
        (date(2026, 10, 26), "неделя с первой реализацией"),
        (date(2026, 11, 2), "неделя со второй реализацией"),
        (date(2026, 11, 9), "неделя без операций"),
    ):
        view.goto_date(monday)
        qt_app.processEvents()
        # Выписано: 1 000 − 200 = 800. В работе: 5 000. Всего: 5 800.
        assert table.item(row, 1).text() == "5 800,00", label
        assert table.item(row, 2).text() == "800,00", label
        assert table.item(row, 3).text() == "5 000,00", label
        assert table.item(TOTAL_ROW, 1).text() == "5 800,00", label

    # Ввод на новой неделе сразу виден в итогах (накопительно).
    view.goto_date(date(2026, 11, 9))
    table.item(row, _DAY(view, 0, 0)).setText("250")
    assert table.item(row, 1).text() == "6 050,00"
    assert table.item(row, 3).text() == "5 250,00"
    window.close()


def test_hover_rule_keeps_cell_background():
    """v0.29.1: при наведении ячейка не теряет свой фон.

    Раньше правило ``::item:hover`` заливало ячейку фоном панели, и под
    курсором гасла метка «выписано» (красная/зелёная), розовые
    выходные и салатовая текущая дата. Смотрим именно правило: в
    headless-среде наведение не смоделировать — ``underMouse()`` у
    таблицы остаётся False даже после QTest.mouseMove.
    """

    import re

    from oplata import styles

    rules = styles.TABLE_CELL_RULES

    hover = re.search(r"::item:hover[^{]*\{([^}]*)\}", rules)
    assert hover is not None, "правило для :hover пропало из стилей"
    assert "background" not in hover.group(1), (
        "у :hover снова задан фон — метка погаснет под курсором: "
        f"{hover.group(1)}"
    )
    assert "outline:none" in hover.group(1), "рамка фокуса вернётся при наведении"

    # Защита от артефакта v0.26.8 остаётся: у выделенной и нажатой
    # ячейки по-прежнему свой фон.
    for selector in (":selected", ":focus"):
        match = re.search(r"::item" + selector + r"[^{]*\{([^}]*)\}", rules)
        assert match is not None, f"правило для {selector} пропало"
        assert "background:" in match.group(0), (
            f"у {selector} больше нет своего фона — вернётся синий артефакт"
        )


def test_local_base_paths_sit_next_to_program(tmp_path, monkeypatch):
    """v0.30.0: выгрузки живут в «Оплата база» рядом с программой (ADR-082)."""

    from datetime import date
    from pathlib import Path

    from oplata import storage

    monkeypatch.setenv("OPLATA_PROGRAM_DIR", str(tmp_path / "program"))

    assert storage.program_dir() == tmp_path / "program"
    assert storage.local_base_dir() == tmp_path / "program" / "Оплата база"
    # Имя файла — «Оплата <сегодня>.xlsx»: в один день файл перезаписывается.
    assert storage.local_base_file(date(2026, 11, 2)).name == "Оплата 02.11.2026.xlsx"
    assert storage.local_base_file().name == (
        f"Оплата {date.today().strftime('%d.%m.%Y')}.xlsx"
    )
    # Дату из имени вынимаем только у своих файлов.
    assert storage.local_base_file_day(Path("Оплата 02.11.2026.xlsx")) == date(
        2026, 11, 2
    )
    assert storage.local_base_file_day(Path("отчёт за ноябрь.xlsx")) is None
    assert storage.local_base_file_day(Path("Оплата 31.02.2026.xlsx")) is None

    # Папки нет — самой свежей выгрузки тоже нет.
    assert storage.latest_local_base_file() is None

    folder = storage.local_base_dir()
    folder.mkdir(parents=True)
    (folder / "Оплата 01.11.2026.xlsx").write_bytes(b"1")
    newest = folder / "Оплата 03.11.2026.xlsx"
    newest.write_bytes(b"1")
    (folder / "отчёт за ноябрь.xlsx").write_bytes(b"1")
    assert storage.latest_local_base_file() == newest


def test_export_dialog_offers_local_base_folder(qt_app, store, monkeypatch):
    """v0.30.0: «Выгрузить базы» открывается сразу в «Оплата база»."""

    from datetime import date

    from pathlib import Path

    from oplata import storage
    from oplata.app import MainWindow

    captured: dict[str, str] = {}

    def fake_save(*args, **kwargs):
        captured["dir"] = args[2]
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_save))

    window = MainWindow(store)
    window.export_bases_to_excel()

    expected = storage.local_base_file()
    assert Path(captured["dir"]) == expected
    assert expected.name == f"Оплата {date.today().strftime('%d.%m.%Y')}.xlsx"
    assert expected.parent.is_dir(), "папка «Оплата база» не создана заранее"
    window.close()


def test_import_dialog_opens_latest_export(qt_app, store, monkeypatch):
    """v0.30.0: «Загрузить базы» подставляет самую свежую выгрузку."""

    from pathlib import Path

    from oplata import storage
    from oplata.app import MainWindow

    folder = storage.local_base_dir()
    folder.mkdir(parents=True)
    (folder / "Оплата 01.11.2026.xlsx").write_bytes(b"1")
    newest = folder / "Оплата 05.11.2026.xlsx"
    newest.write_bytes(b"1")

    captured: dict[str, str] = {}

    def fake_open(*args, **kwargs):
        captured["dir"] = args[2]
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(fake_open))

    window = MainWindow(store)
    window.import_bases_from_excel()
    assert Path(captured["dir"]) == newest

    # Пустая папка — открываем саму папку; нет и папки — домашний каталог,
    # иначе Qt показала бы несуществующий путь.
    for file in folder.iterdir():
        file.unlink()
    assert Path(window._import_start_path()) == folder
    monkeypatch.setenv("OPLATA_PROGRAM_DIR", str(folder / "нет такой папки"))
    assert Path(window._import_start_path()) == Path.home()
    window.close()


def test_closing_window_autosaves_both_bases(qt_app, store, monkeypatch):
    """v0.30.0: при закрытии окна обе базы сами выгружаются в «Оплата база»."""

    pytest.importorskip("openpyxl")
    from datetime import date

    from openpyxl import load_workbook

    from oplata import storage
    from oplata.app import MainWindow
    from oplata.models import Counterparty

    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: messages.append(str(args[-1]))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: messages.append(str(args[-1]))),
    )

    window = MainWindow(store)
    store.data.suppliers = [Counterparty(name="Тестовый поставщик")]
    store.save()
    window.show()
    qt_app.processEvents()

    window.close()
    qt_app.processEvents()

    path = storage.local_base_file()
    assert path.is_file(), "при закрытии окна выгрузка не появилась"
    assert path.name == f"Оплата {date.today().strftime('%d.%m.%Y')}.xlsx"
    names = load_workbook(path).sheetnames
    assert any(name.startswith("МИР.") for name in names)
    assert any(name.startswith("КОРЕЛИЧИ.") for name in names)

    book = load_workbook(path)
    written = [
        str(row[0])
        for title in names
        if title.startswith("МИР.")
        for row in book[title].iter_rows(values_only=True)
        if row and row[0] is not None
    ]
    assert "Тестовый поставщик" in written, "в выгрузку не попали данные базы"
    assert not messages, f"при выходе программа не должна ничего спрашивать: {messages}"


def test_autosave_failure_warns_without_blocking_exit(qt_app, store, monkeypatch):
    """v0.30.0: неудачная выгрузка при выходе — предупреждение, а не запрет."""

    from oplata import app as app_module
    from oplata.app import MainWindow
    from oplata.excelio import ExcelError

    def broken_export(bases, path):
        raise ExcelError("файл открыт в Excel")

    monkeypatch.setattr(app_module, "export_bases", broken_export)

    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args, **kwargs: messages.append(str(args[-1]))),
    )

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    assert window.close() is True, "окно не закрылось — выгрузка заблокировала выход"
    assert len(messages) == 1, f"предупреждение должно быть одно: {messages}"
    assert "Оплата база" in messages[0]
    assert not window.isVisible()


def test_autosave_runs_once_per_exit(qt_app, store, monkeypatch):
    """v0.30.0: и по закрытию окна, и по aboutToQuit выгружаем один раз."""

    from oplata import app as app_module
    from oplata.app import MainWindow

    calls: list[str] = []
    monkeypatch.setattr(
        app_module, "export_bases", lambda bases, path: calls.append(str(path))
    )

    window = MainWindow(store)
    window.close()
    # aboutToQuit приходит уже после закрытия окна — второй раз не пишем.
    window.autosave_bases_to_excel()
    assert len(calls) == 1, f"выгрузка повторилась: {calls}"

    # В smoke-прогоне собранного exe (--self-test) выгрузка отключена.
    fresh = MainWindow(store)
    fresh.autosave_on_exit = False
    fresh.close()
    assert len(calls) == 1, "выгрузка всё равно состоялась при autosave_on_exit=False"


def test_payments_summary_button_opens_monthly_table(qt_app, store, monkeypatch):
    """v0.33.0: кнопки — внизу панели, столбиком; таблица по месяцам."""

    from datetime import date
    from decimal import Decimal

    from PySide6.QtWidgets import QHeaderView

    from oplata.app import MainWindow
    from oplata.models import (
        KIND_PAYMENT,
        OUTGOING,
        Counterparty,
        Operation,
        OtherPayment,
        add_supplier_payment,
    )

    supplier = store.data.suppliers[0]
    add_supplier_payment(store.data, supplier.id, "2026-01-15", Decimal("1000"))
    add_supplier_payment(store.data, supplier.id, "2026-02-05", Decimal("500.50"))
    store.data.other_payments.append(
        OtherPayment(
            date="2026-01-20", name="Аренда", amount="3000.00", direction=OUTGOING
        )
    )
    # v0.32.0: оплата покупателя — это поступление, в оплаты не идёт.
    # В демо-данных покупателей нет, добавляем своего.
    customer = Counterparty(name="Покупатель тест")
    store.data.customers.append(customer)
    store.data.operations.append(
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=customer.id,
            date="2026-02-09",
            amount="700.00",
        )
    )

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    summary = window.summary_view
    assert summary.incoming_summary_button.text() == "Сводка поступлений"
    assert summary.payments_summary_button.text() == "Сводка оплат"
    # v0.33.0: три кнопки — внизу панели, одна под другой: сводка
    # поступлений, сводка оплат, журнал движения средств.
    assert summary.incoming_summary_button.isVisible()
    assert summary.payments_summary_button.isVisible()
    assert summary.movement_button.isVisible()
    assert (
        summary.incoming_summary_button.pos().y()
        < summary.payments_summary_button.pos().y()
        < summary.movement_button.pos().y()
    )
    # Все три — под плашкой «Сводка» (v0.32.0 было наоборот).
    assert summary.incoming_summary_button.pos().y() > summary.panel.pos().y()
    # Переключатель архивных остался в шапке.
    assert summary.show_archived.pos().y() < summary.panel.pos().y()

    opened: list[object] = []
    monkeypatch.setattr(
        "oplata.views.payments_summary_view.PaymentsSummaryDialog.exec",
        lambda self, *args, **kwargs: opened.append(self) or True,
    )
    summary.payments_summary_button.click()

    assert len(opened) == 1, "кнопка не открыла окно сводки оплат"
    dialog = opened[0]
    table = dialog.table
    headers = [
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
    ]
    # v0.34.0: вид оплаты + НДС + 12 месяцев + 4 квартала + итог «Год».
    assert headers[0] == "Вид оплаты"
    assert headers[1] == "НДС"
    assert headers[2:14] == [
        "янв",
        "фев",
        "мар",
        "апр",
        "май",
        "июн",
        "июл",
        "авг",
        "сен",
        "окт",
        "ноя",
        "дек",
    ]
    assert headers[14:18] == ["I кв.", "II кв.", "III кв.", "IV кв."]
    assert headers[18] == "Год"

    # Строки: «Поставщики», «Аренда», «Всего».
    assert table.rowCount() == 3
    assert table.item(0, 0).text() == "Поставщики"
    assert table.item(0, 1).text() == ""           # у поставщиков НДС нет
    assert table.item(0, 2).text() == "1 000,00"   # январь
    assert table.item(0, 3).text() == "500,50"     # февраль
    assert table.item(0, 4).text() == ""           # март: пусто вместо нуля
    assert table.item(0, 14).text() == "1 500,50"  # I квартал
    assert table.item(0, 18).text() == "1 500,50"  # итог за год
    assert table.item(1, 0).text() == "Аренда"
    assert table.item(1, 2).text() == "3 000,00"

    # Итог — последняя строка, жирная. Оплата покупателя (700,00 в
    # феврале) в оплаты не попала: февраль — только 500,50.
    last = table.rowCount() - 1
    assert table.item(last, 0).text() == "Всего"
    assert table.item(last, 2).text() == "4 000,00"
    assert table.item(last, 3).text() == "500,50"
    assert table.item(last, 2).font().bold()
    assert table.item(0, 2).font().bold() is False
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [
        "Поставщики",
        "Аренда",
        "Всего",
    ]

    # v0.32.0: окно на весь экран; колонка вида — узкая по содержимому,
    # v0.34.0: колонка «НДС» — тоже по содержимому, а все числовые
    # колонки делят остаток ширины поровну.
    assert dialog.windowState() & Qt.WindowState.WindowMaximized
    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.ResizeToContents
    for column in range(2, table.columnCount()):
        assert header.sectionResizeMode(column) == QHeaderView.ResizeMode.Stretch

    dialog.close()
    window.close()


def test_payments_summary_dialog_switches_year(qt_app, store):
    """v0.31.0: в окне сводки оплат год переключается."""

    from datetime import date
    from decimal import Decimal

    from oplata.models import add_supplier_payment
    from oplata.views.payments_summary_view import PaymentsSummaryDialog

    supplier = store.data.suppliers[0]
    add_supplier_payment(store.data, supplier.id, "2026-03-10", Decimal("1200"))

    dialog = PaymentsSummaryDialog(store, date(2026, 11, 3))
    assert dialog.current_year() == 2026
    # В подписи виден год и база: таблица считается по активной базе.
    assert dialog.period_label.text().startswith("Оплаты за 2026 год")
    assert "МИР" in dialog.period_label.text()
    # v0.34.0: вторая колонка — «НДС», поэтому март — четвёртая.
    assert dialog.table.item(0, 4).text() == "1 200,00"  # март

    # В году без оплат ячейки пустые, но виды оплат остаются на месте.
    dialog.set_year(2027)
    assert dialog.period_label.text().startswith("Оплаты за 2027 год")
    assert dialog.table.item(0, 4).text() == ""
    assert dialog.table.item(0, 18).text() == ""

    dialog.go_this_year()
    assert dialog.current_year() == date.today().year
    dialog.close()


def test_incoming_summary_button_opens_monthly_table(qt_app, store, monkeypatch):
    """v0.32.0: «Сводка поступлений» — те же месяцы, кварталы и год."""

    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import (
        CARD_SOURCE,
        CASH_SOURCE,
        INCOMING,
        KIND_PAYMENT,
        CashReceipt,
        Counterparty,
        Operation,
        OtherPayment,
        add_supplier_payment,
    )

    supplier = store.data.suppliers[0]
    customer = Counterparty(name="Покупатель тест")
    store.data.customers.append(customer)
    add_supplier_payment(store.data, supplier.id, "2026-01-15", Decimal("1000"))
    store.data.operations.append(
        Operation(
            kind=KIND_PAYMENT,
            counterparty_id=customer.id,
            date="2026-02-05",
            amount="700.00",
        )
    )
    store.data.cash_receipts = [
        CashReceipt(
            date="2026-01-10", source=CASH_SOURCE, amount="5000.00", comment=""
        ),
        CashReceipt(
            date="2026-03-10", source=CARD_SOURCE, amount="2500.00", comment=""
        ),
    ]
    store.data.other_payments.append(
        OtherPayment(
            date="2026-04-20", name="Возврат", amount="900.00", direction=INCOMING
        )
    )

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    opened: list[object] = []
    monkeypatch.setattr(
        "oplata.views.incoming_summary_view.IncomingSummaryDialog.exec",
        lambda self, *args, **kwargs: opened.append(self) or True,
    )
    window.summary_view.incoming_summary_button.click()

    assert len(opened) == 1, "кнопка не открыла окно сводки поступлений"
    dialog = opened[0]
    table = dialog.table
    assert dialog.windowTitle().startswith("Сводка поступлений")
    assert dialog.period_label.text().startswith("Поступления за 2026 год")
    assert table.horizontalHeaderItem(0).text() == "Вид поступления"
    # v0.34.0: итоговая колонка называется «Год»; колонки «НДС»
    # в сводке поступлений нет.
    assert table.horizontalHeaderItem(1).text() == "янв"
    assert table.horizontalHeaderItem(17).text() == "Год"

    # v0.33.0: строки повторяют блок «Поступление» — наличные, карты,
    # покупатели, «Прочие поступления» и «Всего». Посторонние записи
    # направления INCOMING (например, «Возврат») в сводку не идут:
    # в блоке такой строки нет.
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    assert names == [
        "Поступление наличными",
        "Поступление по картам",
        "Покупатели",
        "Прочие поступления",
        # «Возврат» в сводку не попадает (v0.33.0).
        "Всего",
    ]
    assert table.item(0, 1).text() == "5 000,00"   # наличные в январе
    assert table.item(1, 3).text() == "2 500,00"   # карты в марте
    assert table.item(2, 2).text() == "700,00"     # покупатель в феврале
    # v0.33.0: 900 р. записи «Возврат» за апрель в сводку не идут:
    # в блоке «Поступление» такой строки нет.
    assert table.item(3, 4).text() == ""
    assert table.item(4, 4).text() == ""

    last = table.rowCount() - 1
    assert table.item(last, 1).text() == "5 000,00"   # январь
    assert table.item(last, 2).text() == "700,00"     # февраль
    assert table.item(last, 13).text() == "8 200,00"  # I квартал
    assert table.item(last, 17).text() == "8 200,00"  # год без возврата

    # Оплата поставщику (1 000 в январе) в поступления не попала.
    assert "Поставщики" not in names

    # Год переключается: в пустом году ячейки пустые.
    dialog.set_year(2027)
    assert dialog.table.item(0, 1).text() == ""
    assert dialog.table.item(last, 17).text() == ""

    dialog.close()
    window.close()


def test_outgoing_block_has_vat_column(qt_app, store, monkeypatch):
    """v0.33.0: колонка «НДС» — вторая, между названием и суммой."""
    from datetime import date

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    block = view.outgoing
    headers = [
        block.table.horizontalHeaderItem(column).text()
        for column in range(block.table.columnCount())
    ]
    # v0.38.0: колонки «Сумма» нет: название, НДС и шесть дней.
    # v0.60.0: добавлена колонка «Примечание» — название, примечание,
    # НДС и шесть дней.
    assert block.table.columnCount() == 9
    assert headers[:3] == ["", "Примечание", "НДС"]
    # Дней по-прежнему шесть (Пн–Сб), колонок — девять.
    assert len(block.days) == 6

    # В блоке «Поступление» колонки «НДС» нет совсем.
    assert view.incoming.table.isColumnHidden(COL_VAT)
    window.close()


def test_vat_rate_comes_from_payment_card(qt_app, store, monkeypatch):
    """v0.33.0: ставка из карточки вида оплаты видна в колонке «НДС»."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.outgoing.set_date(date(2026, 10, 27))

    # Вид со ставкой 20 % и вид без ставки.
    patch_other_payment_card(monkeypatch, ["Бензин (НДС 20)"], vat_rate="20")
    view.add_other_row(OUTGOING)
    add_other_payment(store.data, "Услуги", "2026-10-27", OUTGOING)
    view.refresh_rows()
    # Ставки видны и после обычного пересчёта (например, при старте).
    view.refresh()
    qt_app.processEvents()

    block = view.outgoing
    assert block.amount_text("other:Бензин (НДС 20)", COL_VAT) == "20 %"
    assert block.amount_text("other:Услуги", COL_VAT) == ""
    # У строки «Поставщики» ставки нет.
    assert block.amount_text("suppliers", COL_VAT) == ""

    # Колонка только для чтения: ставка правится карточкой, не ячейкой.
    vat_item = block.table.item(block.row_keys.index("other:Бензин (НДС 20)"), COL_VAT)
    assert not (vat_item.flags() & Qt.ItemFlag.ItemIsEditable)

    # Ставка доезжает до файла и читается обратно.
    saved = [p for p in store.data.other_payments if p.name == "Бензин (НДС 20)"]
    assert saved[0].vat_rate == "20"
    window.close()


def test_vat_rate_kept_on_duplicate(qt_app, store, monkeypatch):
    """v0.33.0: копия вида оплаты сохраняет ставку НДС."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.outgoing.set_date(date(2026, 10, 27))
    add_other_payment(
        store.data, "Аренда", "2026-10-27", OUTGOING, vat_rate="10.5"
    )
    view.refresh_rows()
    qt_app.processEvents()
    assert view.outgoing.amount_text("other:Аренда", COL_VAT) == "10.5 %"

    view.duplicate_payment("other:Аренда", OUTGOING)
    qt_app.processEvents()
    assert "other:Аренда (копия)" in view.outgoing.row_keys
    assert view.outgoing.amount_text("other:Аренда (копия)", COL_VAT) == "10.5 %"
    window.close()


def test_summary_buttons_stay_under_the_panel(qt_app, store):
    """v0.33.0: три кнопки — внизу панели «Сводка», в столбик."""
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    summary = window.summary_view

    # Кнопка журнала переехала из шапки вниз: в шапке остался только
    # переключатель архивных.
    assert summary.show_archived.pos().y() < summary.panel.pos().y()
    assert summary.movement_button.pos().y() > summary.panel.pos().y()
    assert (
        summary.incoming_summary_button.pos().y()
        < summary.payments_summary_button.pos().y()
        < summary.movement_button.pos().y()
    )
    assert summary.incoming_summary_button.pos().y() > summary.panel.pos().y()
    # Все кнопки — в одной колонке (один и тот же левый край).
    assert (
        summary.incoming_summary_button.pos().x()
        == summary.payments_summary_button.pos().x()
        == summary.movement_button.pos().x()
    )
    window.close()


def test_payments_summary_without_vat_button(qt_app, store):
    """v0.34.0: кнопка «Без НДС» показывает суммы за минусом НДС."""
    from datetime import date
    from decimal import Decimal

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.models import (
        OUTGOING,
        OtherPayment,
        add_supplier_payment,
    )
    from oplata.views.payments_summary_view import PaymentsSummaryDialog

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    supplier = store.data.suppliers[0]
    add_supplier_payment(store.data, supplier.id, "2026-01-15", Decimal("1200"))
    store.data.other_payments = [
        # 240 при ставке 20 % -> 200.
        OtherPayment(
            date="2026-01-20",
            name="Аренда",
            amount="240.00",
            direction=OUTGOING,
            vat_rate="20",
        ),
        # Ставка 0 — сумма остаётся прежней.
        OtherPayment(
            date="2026-02-20",
            name="Услуги",
            amount="100.00",
            direction=OUTGOING,
            vat_rate="0",
        ),
        # Вид без ставки — тоже без изменений.
        OtherPayment(
            date="2026-03-20", name="Прочее", amount="50.00", direction=OUTGOING
        ),
    ]

    dialog = PaymentsSummaryDialog(store, date(2026, 1, 1))
    table = dialog.table
    # v0.34.0: колонка «НДС» — вторая, после «Вид оплаты».
    assert table.columnCount() == 19
    assert table.horizontalHeaderItem(1).text() == "НДС"
    assert table.horizontalHeaderItem(18).text() == "Год"

    def cell(name: str, column: int) -> str:
        row = [
            index
            for index in range(table.rowCount())
            if table.item(index, 0).text() == name
        ][0]
        return table.item(row, column).text()

    # Показываем с НДС: как ввели.
    assert cell("Аренда", 1) == "20 %"
    assert cell("Услуги", 1) == "0 %"
    assert cell("Поставщики", 1) == ""
    assert cell("Аренда", 2) == "240,00"
    assert cell("Всего", 2) == "1 440,00"

    button = dialog.without_vat_button
    assert button.text() == "Без НДС"
    assert not button.isChecked()  # по умолчанию — оплаты с НДС
    assert styles.GREEN in button.styleSheet()

    # Нажали: красная кнопка и суммы за минусом НДС.
    button.setChecked(True)
    qt_app.processEvents()
    assert styles.RED in button.styleSheet()
    assert cell("Аренда", 2) == "200,00"
    assert cell("Услуги", 3) == "100,00"     # ставка 0 — без изменений
    assert cell("Прочее", 4) == "50,00"      # без ставки — без изменений
    assert cell("Поставщики", 2) == "1 200,00"
    # Итог считается по показанным суммам.
    assert cell("Всего", 2) == "1 400,00"
    assert cell("Всего", 18) == "1 550,00"

    # Отжали — всё вернулось.
    button.setChecked(False)
    qt_app.processEvents()
    assert styles.GREEN in button.styleSheet()
    assert cell("Аренда", 2) == "240,00"
    assert cell("Всего", 2) == "1 440,00"

    dialog.close()
    window.close()


def test_incoming_summary_has_no_vat_column(qt_app, store):
    """v0.34.0: колонка «НДС» и кнопка «Без НДС» — только в «Сводке оплат»."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.views.incoming_summary_view import IncomingSummaryDialog

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    dialog = IncomingSummaryDialog(store, date(2026, 1, 1))
    assert dialog.table.columnCount() == 18
    assert dialog.table.horizontalHeaderItem(1).text() == "янв"
    assert not hasattr(dialog, "without_vat_button")
    assert dialog.without_vat is False
    dialog.close()
    window.close()


def test_customer_income_grows_day_and_month_totals(qt_app, store):
    """v0.35.0: поступление покупателя идёт плюсом в «Средства».

    v0.43.0: в блоке «Месяц» вместо прихода и оплат — итоги реестра
    «Отчеты ККМ», поэтому деньги покупателя там не показываются вовсе.
    """
    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import (
        KIND_PAYMENT,
        Counterparty,
        add_realization,
        set_daily_operation,
    )

    buyer = Counterparty(name="Покупатель С")
    store.data.customers.append(buyer)
    add_realization(store.data, buyer.id, "2026-10-20", Decimal("5000"))
    supplier = store.data.suppliers[0]
    # Поставщику — оплата (минус), покупатель внёс деньги (плюс).
    set_daily_operation(
        store.data, supplier.id, "2026-10-27", KIND_PAYMENT, Decimal("1000")
    )
    set_daily_operation(
        store.data, buyer.id, "2026-10-27", KIND_PAYMENT, Decimal("1500")
    )

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 27))
    view.refresh()

    # Блок «Средства»: покупатель — в поступлениях, поставщик — в оплатах.
    assert view.value_text("income") == "1 500,00"
    assert view.value_text("payments") == "1 000,00"
    assert view.value_text("balance") == "500,00"
    # Блок «Месяц» (v0.43.0/0.44.0): прихода и оплат там больше нет —
    # только итоги реестра ККМ за октябрь и выписанная реализация;
    # денег покупателя в блоке не видно вовсе.
    assert view.value_text("month_kkm_total") == "0,00"
    assert view.value_text("month_sale_invoiced") == "0,00"
    assert view.value_text("month_total") == "0,00"
    # Задолженность покупателя уменьшилась: 5 000 − 1 500.
    assert view.value_text("cust_total") == "3 500,00"
    window.close()


def test_summary_week_debt_matches_suppliers_table(qt_app, store):
    """v0.36.0: «Задолженность на неделе» в Сводке = колонка «Неделя».

    Раньше (v0.27.3) в «Сводку» прибавлялась просрочка, и цифра
    расходилась с подписью «Задолженность за неделю» в таблице
    поставщиков. Теперь считаются только долги со сроком внутри
    недели; просрочка — отдельная справочная строка.
    """
    from datetime import date

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import KIND_INCOME, Counterparty, Operation

    supplier = Counterparty(name="Просрочка недели")
    store.data.suppliers.append(supplier)
    # Срок 20.10 уже прошёл (просрочка), срок 29.10 — внутри недели.
    for day, amount in (("2026-10-20", "200"), ("2026-10-29", "500")):
        store.data.operations.append(
            Operation(
                kind=KIND_INCOME,
                counterparty_id=supplier.id,
                date=day,
                due_date=day,
                amount=amount,
            )
        )
    store.save()

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    # Обе недели — одна и та же: 26.10–01.11.
    suppliers = window.suppliers_view
    suppliers.goto_date(date(2026, 10, 27))
    qt_app.processEvents()
    summary = window.summary_view
    summary.date_edit.setDate(QDate(2026, 10, 27))
    summary.refresh()
    qt_app.processEvents()

    # В тулбаре таблицы сумма показывается со словом «руб.».
    table_week = suppliers.right_value_label.text().replace(" руб.", "")
    assert table_week == "500,00"           # только долги недели
    assert summary.value_text("debt_week") == table_week
    # Просрочка в «Задолженность на неделе» не входит: 500 + 200 больше
    # не складываются (как было в v0.27.3) — это отдельная строка.
    assert summary.value_text("debt_late") == "200,00"
    window.close()


def test_receipts_arrows_switch_week(qt_app, store):
    """v0.38.0: стрелки «←»/«→» листают недели, а не дни.

    И в самой таблице (клавиши), и в шапке блока (кнопки). Обычный
    переход по ячейкам оставлен на Ctrl + «←»/«→».
    """
    from datetime import date

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))
    block = view.incoming
    assert block.days[0] == date(2026, 10, 26)

    def press(key, modifier=Qt.KeyboardModifier.NoModifier) -> None:
        QApplication.sendEvent(
            block.table,
            QKeyEvent(QEvent.Type.KeyPress, key, modifier),
        )
        qt_app.processEvents()

    press(Qt.Key.Key_Right)
    assert block.current_date() == date(2026, 11, 3)
    assert block.days[0] == date(2026, 11, 2)     # и колонки дней другие
    press(Qt.Key.Key_Left)
    press(Qt.Key.Key_Left)
    assert block.current_date() == date(2026, 10, 20)
    assert block.days[0] == date(2026, 10, 19)

    # Ctrl + «→» — обычный переход по ячейкам, неделя остаётся прежней.
    block.table.setCurrentCell(0, 2)
    press(Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
    assert block.current_date() == date(2026, 10, 20)

    # Кнопки «←»/«→» в шапке блока — тоже недели.
    block.next_btn.click()
    assert block.current_date() == date(2026, 10, 27)
    block.prev_btn.click()
    assert block.current_date() == date(2026, 10, 20)
    window.close()


def test_receipts_today_column_is_painted_full_height(qt_app, store):
    """v0.38.0: в блоках «Поступление»/«Оплата» сегодня — весь столбец."""
    from datetime import date

    from PySide6.QtGui import QColor

    from oplata import styles
    from oplata.app import MainWindow
    from oplata.views.receipts_view import FIRST_DAY_COL

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    today = date.today()
    view.set_date(today)
    block = view.incoming
    if today not in block.days:
        # Воскресенье в таблице не показывается (шесть дней, Пн–Сб).
        qt_app.processEvents()
        window.close()
        return
    column = FIRST_DAY_COL + block.days.index(today)
    body = QColor(styles.WEEK_TABLE_TODAY_GREEN_BODY).name()
    for row in range(len(block.row_keys)):
        item = block.table.item(row, column)
        assert item is not None
        assert item.background().color().name() == body, (row, column)
    # Соседний день этой же недели так не красим.
    other = FIRST_DAY_COL + ((block.days.index(today) + 1) % len(block.days))
    assert block.table.item(0, other).background().color().name() != body
    window.close()


def test_popup_row_saves_into_clicked_day_cell(qt_app, store):
    """v0.39.0: сумма контрагента попадает в день **нажатой** ячейки.

    Раньше окно со списком открывалось всегда на дату, выбранную в
    блоке, и введённая сумма уезжала на неё, даже когда кликнули по
    другой ячейке недели.
    """
    from datetime import date
    from decimal import Decimal

    from oplata.app import MainWindow
    from oplata.models import Counterparty, KIND_PAYMENT, Operation

    buyer = Counterparty(name="Покупатель дня")
    supplier = Counterparty(name="Поставщик дня")
    store.data.customers.append(buyer)
    store.data.suppliers.append(supplier)

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    # В блоке выбрана среда 28.10, а кликаем мы по пятнице 30.10.
    view.set_date(date(2026, 10, 28))
    qt_app.processEvents()

    def day_column(block, day):
        from oplata.views.receipts_view import FIRST_DAY_COL

        return FIRST_DAY_COL + block.days.index(day)

    friday = date(2026, 10, 30)

    # --- Покупатели: клик по ячейке пятницы, сумма пишется за пятницу.
    incoming = view.incoming
    row_customers = incoming.row_keys.index("customers")
    incoming.table.cellClicked.emit(row_customers, day_column(incoming, friday))
    qt_app.processEvents()
    popup = view._popup
    assert popup is not None
    assert "30.10" in popup.title_label.text()  # окно — за нажатый день
    popup.edits[buyer.id].setText("1 200")
    popup._save()
    qt_app.processEvents()
    assert [
        (op.date, op.amount, op.counterparty_id)
        for op in store.data.operations
        if op.kind == KIND_PAYMENT and op.counterparty_id == buyer.id
    ] == [("2026-10-30", "1200.00", buyer.id)]
    assert incoming.amount_text("customers", day_column(incoming, friday)) == (
        "1 200,00"
    )
    # В колонке выбранного дня (28.10) этой суммы нет.
    assert incoming.amount_text("customers", day_column(incoming, date(2026, 10, 28))) == ""

    # --- Поставщики: то же самое в блоке «Оплата».
    outgoing = view.outgoing
    row_suppliers = outgoing.row_keys.index("suppliers")
    outgoing.table.cellClicked.emit(row_suppliers, day_column(outgoing, friday))
    qt_app.processEvents()
    popup = view._popup
    assert popup is not None
    popup.edits[supplier.id].setText("3 400")
    popup._save()
    qt_app.processEvents()
    assert [
        (op.date, op.amount)
        for op in store.data.operations
        if op.kind == KIND_PAYMENT and op.counterparty_id == supplier.id
    ] == [("2026-10-30", "3400.00")]
    assert outgoing.amount_text("suppliers", day_column(outgoing, friday)) == (
        "3 400,00"
    )
    window.close()


def test_popup_falls_back_to_block_date_on_name_column(qt_app, store):
    """v0.39.0: клик по названию строки — дата блока (дня у колонки нет)."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.customers.append(Counterparty(name="Покупатель В"))
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 28))
    qt_app.processEvents()

    incoming = view.incoming
    row_customers = incoming.row_keys.index("customers")
    incoming.table.cellClicked.emit(row_customers, 0)  # колонка названия
    qt_app.processEvents()
    popup = view._popup
    assert popup is not None
    assert "28.10" in popup.title_label.text()
    window.close()


def test_week_tables_fit_width_without_horizontal_scroll(qt_app, store):
    """v0.40.0: горизонтальной прокрутки в таблицах нет — колонки влезают."""
    from oplata.app import MainWindow

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    for view in (window.suppliers_view, window.customers_view):
        table = view.table
        assert table.horizontalScrollBarPolicy() is (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        view._fit_columns_to_width()
        qt_app.processEvents()
        widths = [table.columnWidth(col) for col in range(view.column_count)]
        # Сумма ширин ровно в ширину окна: полосы прокрутки нет.
        assert sum(widths) == table.viewport().width()
        assert table.horizontalScrollBar().maximum() == 0
        # Вертикальная прокрутка осталась.
        assert table.verticalScrollBarPolicy() is (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        # Закреплённая шапка повторяет те же ширины.
        assert [
            view.frozen_table.columnWidth(col) for col in range(view.column_count)
        ] == widths
        # Узкое окно: колонки сжимаются ниже минимума, но всё равно
        # укладываются в любую ширину.
        for available in (120, 400, 900):
            widths = view._column_widths(available)
            assert sum(widths) == available
        narrow = view._column_widths(400)
        assert all(w <= m for w, m in zip(narrow, view.min_widths))
    window.close()


def test_base_tools_use_nav_font_and_clear_button_is_admin_only(qt_app, store):
    """v0.40.0: шрифт кнопок базы — как у вкладок; «Очистить базу» — админу."""
    from oplata.access import ALL_PAGES, Access, User, save_users
    from oplata.app import MainWindow

    admin = User(
        id="admin",
        name="Администратор",
        pages=list(ALL_PAGES),
        can_edit=True,
        can_switch_base=True,
    )
    viewer = User(id="viewer", name="Наблюдатель", pages=[0])
    save_users([admin, viewer])

    window = MainWindow(store, access=Access(admin))
    window.show()
    qt_app.processEvents()

    def font_size(button) -> str:
        for chunk in button.styleSheet().split("}"):
            if "font-size:" in chunk and "QPushButton{" in chunk:
                return chunk.split("font-size:")[1]
        return ""

    nav_size = font_size(window.nav_buttons[0])
    assert nav_size.endswith("px")
    # Одинаковый шрифт у вкладок («Поставщики», «Покупатели») и у всех
    # трёх кнопок базы.
    for button in (window.export_button, window.import_button, window.clear_button):
        assert font_size(button) == nav_size

    assert window.clear_button.text() == "Очистить базу"
    assert window.clear_button.isVisible()
    window.apply_current_user(viewer)
    assert not window.clear_button.isVisible()
    window.close()


def test_base_tools_ask_password_before_work(qt_app, store, monkeypatch, tmp_path):
    """v0.40.0: «Выгрузить»/«Загрузить»/«Очистить» — по паролю администратора."""
    from PySide6.QtWidgets import QInputDialog

    from oplata import access as access_module
    from oplata.app import MainWindow

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    access_module.set_password("1234")

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    calls: list[str] = []
    monkeypatch.setattr(
        window, "export_bases_to_excel", lambda: calls.append("export")
    )
    monkeypatch.setattr(
        window, "import_bases_from_excel", lambda: calls.append("import")
    )

    answers = {"текст": "0000", "ок": True}
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: (answers["текст"], answers["ок"])),
    )
    window.export_button.click()
    window.import_button.click()
    qt_app.processEvents()
    assert calls == [], "с неверным паролем кнопки работать не должны"

    answers["текст"] = "1234"
    window.export_button.click()
    window.import_button.click()
    qt_app.processEvents()
    assert calls == ["export", "import"]
    window.close()


def _ask_password_as(monkeypatch, answer):
    """Подменить запрос пароля: вернуть ``answer`` («текст», «ок»)."""
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: answer),
    )


def _click_message_box_button(monkeypatch, title_substring):
    """«Нажать» в диалоге подтверждения кнопку с нужной подписью."""
    from PySide6.QtWidgets import QMessageBox

    def fake_exec(self):
        for button in self.buttons():
            if title_substring in button.text():
                monkeypatch.setattr(
                    QMessageBox,
                    "clickedButton",
                    lambda _self, btn=button: btn,
                )
                return 0
        raise AssertionError(f"кнопка «{title_substring}» не найдена")

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


def test_clear_base_keeps_partners_and_removes_money(
    qt_app, store, monkeypatch, tmp_path
):
    """v0.40.0: «Очистить базу» стирает операции и суммы, справочники — нет."""
    from PySide6.QtWidgets import QMessageBox

    from oplata import access as access_module
    from oplata.app import MainWindow
    from oplata.models import (
        CashReceipt,
        Operation,
        OtherPayment,
        YearStartCustomerDebt,
        YearStartDebt,
    )

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    access_module.set_password("1234")

    data = store.data
    customer = data.customers[0] if data.customers else None
    if customer is None:
        from oplata.models import Counterparty

        customer = Counterparty(name="Покупатель В")
        data.customers.append(customer)
    supplier = data.suppliers[0]
    data.operations.append(
        Operation(kind="Приход", counterparty_id=supplier.id, amount="100.00")
    )
    data.cash_receipts.append(CashReceipt(date="2026-10-27", amount="50.00"))
    data.other_payments.append(
        OtherPayment(date="2026-10-27", name="Аренда", amount="30.00")
    )
    data.year_start.supplier_debts.append(
        YearStartDebt(counterparty_id=supplier.id, amount="10.00")
    )
    data.year_start.customer_debts.append(
        YearStartCustomerDebt(counterparty_id=customer.id, amount="20.00")
    )
    data.year_start.funds = "777.00"
    data.day_overrides["2026-10-27"] = "work"
    store.save()

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    # 1. Неверный пароль — ничего не трогаем.
    _ask_password_as(monkeypatch, ("0000", True))
    window.clear_button.click()
    qt_app.processEvents()
    assert data.operations, "с неверным паролем база не очищается"

    # 2. Верный пароль, но «Отмена» — тоже ничего.
    _ask_password_as(monkeypatch, ("1234", True))
    _click_message_box_button(monkeypatch, "Отмена")
    window.clear_button.click()
    qt_app.processEvents()
    assert data.operations

    # 3. «Выгрузить и очистить», но выгрузку отменили — не очищаем:
    #    файл — единственный шанс вернуть данные.
    def cancelled_export() -> None:
        window._last_export_path = None

    monkeypatch.setattr(window, "export_bases_to_excel", cancelled_export)
    _click_message_box_button(monkeypatch, "Выгрузить и очистить")
    window.clear_button.click()
    qt_app.processEvents()
    assert data.operations, "без выгрузки очистка не делается"

    # 4. «Очистить» — операции и суммы удалены, справочники целы.
    _click_message_box_button(monkeypatch, "Очистить")
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *args, **kwargs: None)
    )
    window.clear_button.click()
    qt_app.processEvents()
    assert data.operations == []
    assert data.cash_receipts == []
    assert data.other_payments == []
    assert data.year_start.supplier_debts == []
    assert data.year_start.customer_debts == []
    assert data.year_start.funds == "0.00"
    assert data.suppliers, "поставщики остаются"
    assert data.customers, "покупатели остаются"
    assert data.day_overrides == {"2026-10-27": "work"}, "календарь не тронут"

    # 5. То же самое, но через «Выгрузить и очистить»: выгрузка удалась —
    #    база очищается.
    data.operations.append(
        Operation(kind="Приход", counterparty_id=supplier.id, amount="100.00")
    )

    def done_export() -> None:
        window._last_export_path = str(tmp_path / "базы.xlsx")

    monkeypatch.setattr(window, "export_bases_to_excel", done_export)
    _click_message_box_button(monkeypatch, "Выгрузить и очистить")
    window.clear_button.click()
    qt_app.processEvents()
    assert data.operations == []
    assert data.suppliers, "справочники и в этом пути остаются"
    window.close()


def test_clear_base_says_when_nothing_to_clear(qt_app, store, monkeypatch, tmp_path):
    """v0.40.0: в пустой базе «Очистить базу» только сообщает об этом."""
    from PySide6.QtWidgets import QMessageBox

    from oplata import access as access_module
    from oplata.app import MainWindow

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    access_module.set_password("1234")

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    _ask_password_as(monkeypatch, ("1234", True))
    _click_message_box_button(monkeypatch, "Очистить")
    notices: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: notices.append(str(args[-1]))),
    )
    window.clear_button.click()
    qt_app.processEvents()
    assert store.data.suppliers, "справочники остаются и без операций"
    assert notices and "нечего" in notices[-1]
    window.close()


def test_kkm_table_shows_month_with_repeating_days(qt_app, store):
    """v0.42.0: таблица на месяц, МИР — три строки на дату, КОРЕЛИЧИ — две.

    v0.43.0: месяц стоит **тремя вертикальными рядами по 10 дней**
    (1–10, 11–20, 21–…), ряды разделены узким вертикальным полем,
    прокрутки нет.
    """
    from datetime import date

    from oplata.app import IDX_KKM, MainWindow
    from oplata.models import KKM_DAY_KINDS
    from oplata.views.kkm_view import (
        AMOUNT_MIN_WIDTH,
        BLOCKS,
        COLUMNS_PER_BLOCK,
        COL_AMOUNT,
        COL_DATE,
        COL_KIND,
        DAYS_PER_BLOCK,
        HEADERS,
        SEPARATOR_COLUMNS,
        SEPARATOR_WIDTH,
        _AmountDelegate,
        _block_first_column,
    )

    window = MainWindow(store)
    # Размер как у рабочего окна: три ряда по 10 дней должны влезать
    # по высоте без прокрутки.
    window.resize(1600, 900)
    window.show()
    window.show_page(IDX_KKM)
    qt_app.processEvents()
    view = window.kkm_view
    view.set_month(date(2026, 11, 1))
    qt_app.processEvents()
    table = view.table

    # Подписи повторяются в каждом ряду; у разделителей их нет.
    for block in range(BLOCKS):
        first = _block_first_column(block)
        assert [
            table.horizontalHeaderItem(first + shift).text()
            for shift in range(COLUMNS_PER_BLOCK)
        ] == list(HEADERS)
    assert all(table.horizontalHeaderItem(c).text() == "" for c in SEPARATOR_COLUMNS)
    # v0.45.0: поле между рядами — 16 точек и **не растягивается** на
    # всю ширину окна (в v0.44.0 было 80 и всё свободное место уходило
    # в поля, из-за чего таблица расползалась).
    assert all(table.columnWidth(c) == SEPARATOR_WIDTH for c in SEPARATOR_COLUMNS)
    assert sum(table.columnWidth(c) for c in range(table.columnCount())) < (
        table.viewport().width()
    )
    # v0.45.0: поле между рядами **не закрашено** — кисти у ячейки нет.
    assert all(
        table.item(0, c).background().style() is Qt.BrushStyle.NoBrush
        for c in SEPARATOR_COLUMNS
    )
    # v0.45.0: «Сумма» в полтора раза у́же своей доли — она у́же колонки
    # «Отчет», но не у́же разумного минимума.
    assert table.columnWidth(COL_AMOUNT) < table.columnWidth(COL_KIND)
    assert table.columnWidth(COL_AMOUNT) >= AMOUNT_MIN_WIDTH
    # Обводку каждого ряда рисует делегат таблицы.
    assert isinstance(table.itemDelegate(), _AmountDelegate)

    # В ноябре 30 дней; МИР — по три строки на каждый день. Высота
    # таблицы = самому длинному ряду (10 дней), а не всему месяцу.
    assert table.rowCount() == DAYS_PER_BLOCK * len(KKM_DAY_KINDS["mir"])
    # Первый ряд — 1–10, второй — 11–20, третий — 21–30.
    for block, first_day in enumerate((1, 11, 21)):
        first = _block_first_column(block)
        assert table.item(0, first + COL_DATE).text() == f"{first_day:02d}.11.2026"
        assert table.item(3, first + COL_DATE).text() == f"{first_day + 1:02d}.11.2026"

    # Порядок строк внутри дня: ККМ, ККМ, Возврат покупателя.
    assert [table.item(row, COL_KIND).text() for row in range(3)] == [
        "Отчет ККМ",
        "Отчет ККМ",
        "Возврат покупателя",
    ]
    # Одна и та же дата повторяется три раза подряд.
    assert [table.item(row, COL_DATE).text() for row in range(3)] == ["01.11.2026"] * 3
    # Горизонтальной прокрутки нет (просьба v0.43.0). По вертикали —
    # v0.47.0: полоса включается по необходимости, а не всегда выключена,
    # иначе в окне в половину экрана конец месяца пропадал бы совсем.
    # Здесь месяц влезает целиком, поэтому полосы не видно.
    assert table.verticalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert table.verticalScrollBar().maximum() == 0
    assert table.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert table.horizontalScrollBar().maximum() == 0

    # В базе КОРЕЛИЧИ на день приходится две строки: ККМ и возврат.
    window.base_button.click()
    qt_app.processEvents()
    view.refresh()
    assert view.table.rowCount() == DAYS_PER_BLOCK * len(KKM_DAY_KINDS["korelichi"])
    assert [view.table.item(row, COL_KIND).text() for row in range(2)] == [
        "Отчет ККМ",
        "Возврат покупателя",
    ]
    assert view.table.item(2, COL_DATE).text() == "02.11.2026"
    # Реестр каждой базы лежит в её файле: суммы не пересекаются.
    window.base_button.click()
    qt_app.processEvents()
    window.close()


def test_kkm_month_totals_count_reports_returns_and_diff(qt_app, store):
    """v0.42.0: ввод суммы сохраняется, итог месяца — ККМ, возвраты, разница."""
    from datetime import date

    from oplata.app import MainWindow
    from oplata.views.kkm_view import COL_AMOUNT

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.kkm_view
    view.set_month(date(2026, 11, 1))
    qt_app.processEvents()
    table = view.table

    def enter(row: int, text: str) -> None:
        table.item(row, COL_AMOUNT).setText(text)
        qt_app.processEvents()

    enter(0, "1 000")     # 01.11, первый «Отчет ККМ»
    enter(1, "500")       # 01.11, второй «Отчет ККМ»
    enter(2, "300")       # 01.11, «Возврат покупателя»
    enter(5, "100")       # 02.11, возврат

    # В ячейке стоит отформатированная сумма, в файле — запись.
    assert table.item(0, COL_AMOUNT).text() == "1 000,00"
    assert view.reports_label.text() == "Отчеты ККМ: 1 500,00 руб."
    assert view.returns_label.text() == "Возвраты: 400,00 руб."
    assert view.total_label.text() == "Итого по Отчетам ККМ: 1 100,00 руб."
    saved = {(r.date, r.slot, r.kind, r.amount) for r in store.data.kkm_reports}
    assert ("2026-11-01", 0, "report", "1000.00") in saved
    assert ("2026-11-01", 2, "return", "300.00") in saved

    # Пустая ячейка удаляет запись и пересчитывает итог.
    enter(0, "")
    assert table.item(0, COL_AMOUNT).text() == ""
    assert view.reports_label.text() == "Отчеты ККМ: 500,00 руб."
    assert view.total_label.text() == "Итого по Отчетам ККМ: 100,00 руб."
    assert len(store.data.kkm_reports) == 3

    # Переход по месяцам: другой месяц — свои записи и свой итог.
    view._next_month()
    qt_app.processEvents()
    assert "Декабрь 2026" in view.month_label.text()
    assert view.reports_label.text() == "Отчеты ККМ: 0,00 руб."
    # v0.43.0: три ряда по 10 дней, в декабре третий ряд — 11 дней.
    assert view.table.rowCount() == 11 * 3
    # Сумма, введённая в третьем ряду, попадает в свои даты: первая
    # строка третьего ряда — 21.12 (колонка 10 — «Сумма» этого ряда).
    view.table.item(0, 10).setText("250")
    qt_app.processEvents()
    assert ("2026-12-21", 0, "report", "250.00") in {
        (r.date, r.slot, r.kind, r.amount) for r in store.data.kkm_reports
    }
    assert view.reports_label.text() == "Отчеты ККМ: 250,00 руб."
    view.table.item(0, 10).setText("")
    qt_app.processEvents()
    view._prev_month()
    qt_app.processEvents()
    assert "Ноябрь 2026" in view.month_label.text()
    assert view.reports_label.text() == "Отчеты ККМ: 500,00 руб."
    window.close()


def test_summary_month_block_counts_kkm_and_invoiced_realization(qt_app, store):
    """v0.43.0: блок «Месяц» — итоги ККМ и выписанная реализация.

    Считается **весь календарный месяц** (с 1-го по последнее число),
    а у реализации — только суммы с зелёной меткой «Выписано».
    """
    from datetime import date
    from decimal import Decimal

    from PySide6.QtCore import QDate

    from oplata.app import MainWindow
    from oplata.models import Counterparty, add_realization, set_kkm_amount

    buyer = Counterparty(name="Покупатель Д")
    store.data.customers.append(buyer)
    # Выписанная реализация (зелёная метка) — 1-е и последнее число.
    invoiced_first = add_realization(store.data, buyer.id, "2026-10-01", Decimal("4000"))
    invoiced_first.invoiced = True
    invoiced_last = add_realization(store.data, buyer.id, "2026-10-31", Decimal("600"))
    invoiced_last.invoiced = True
    # Реализация «в работе» (без метки) в строку не входит.
    in_work = add_realization(store.data, buyer.id, "2026-10-15", Decimal("900"))
    in_work.invoiced = False
    # Соседний месяц не считается.
    other = add_realization(store.data, buyer.id, "2026-11-05", Decimal("777"))
    other.invoiced = True

    # Отчеты ККМ и возврат за октябрь.
    set_kkm_amount(store.data, "2026-10-01", 0, "report", Decimal("1500"))
    set_kkm_amount(store.data, "2026-10-31", 2, "return", Decimal("250"))

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.summary_view
    view.date_edit.setDate(QDate(2026, 10, 20))
    view.refresh()

    # v0.44.0: «Итого по Отчетам ККМ» — за вычетом возвратов: 1 500 − 250.
    assert view.value_text("month_kkm_total") == "1 250,00"
    # 4 000 + 600: выписанная реализация за весь месяц; 900 «в работе»
    # и ноябрьские 777 в строку не входят.
    assert view.value_text("month_sale_invoiced") == "4 600,00"
    # «Итого за месяц» = ККМ за вычетом возвратов + реализация.
    assert view.value_text("month_total") == "5 850,00"
    # Зелёного квадратика у строки нет (v0.44.0).
    assert not hasattr(view, "has_mark")
    # Другой месяц — свои цифры.
    view.date_edit.setDate(QDate(2026, 11, 10))
    view.refresh()
    assert view.value_text("month_sale_invoiced") == "777,00"
    assert view.value_text("month_kkm_total") == "0,00"
    assert view.value_text("month_total") == "777,00"
    window.close()


def test_kkm_table_zebra_paints_whole_day_one_color(qt_app, store):
    """v0.44.0: зебра по дням — один день, один цвет (все строки дня)."""
    from datetime import date

    from oplata.app import IDX_KKM, MainWindow
    from oplata.views.kkm_view import (
        COLUMNS_PER_BLOCK,
        ZEBRA_EVEN,
        ZEBRA_ODD,
        _block_first_column,
    )

    window = MainWindow(store)
    window.resize(1600, 900)
    window.show()
    window.show_page(IDX_KKM)
    qt_app.processEvents()
    view = window.kkm_view
    # Февраль 2026: 28 дней, сегодня (09.11) в месяц не попадает —
    # значит, весь месяц раскрашен зеброй, без «сегодняшней» зелёной.
    view.set_month(date(2026, 2, 1))
    qt_app.processEvents()
    table = view.table

    def day_color(row: int) -> str:
        return table.item(row, 0).background().color().name()

    # У МИР на день три строки — все одного цвета.
    first_day = {day_color(row) for row in range(3)}
    second_day = {day_color(row) for row in range(3, 6)}
    assert len(first_day) == 1 and len(second_day) == 1
    # Соседние дни — разные цвета; тон светлый (из палитры программы).
    assert first_day != second_day
    assert (first_day | second_day) <= {ZEBRA_EVEN, ZEBRA_ODD}
    # Цвет меняется по дням: через день — тот же.
    assert day_color(0) == day_color(6)
    assert day_color(0) != day_color(3)
    # Заливка стоит и во втором, и в третьем ряду (колонки 4 и 8).
    for block in (1, 2):
        first = _block_first_column(block)
        assert table.item(0, first).background().color().name() == day_color(0)
        assert table.item(0, first + COLUMNS_PER_BLOCK - 1).background().color().name() == day_color(0)
    # Сегодняшний день остаётся светло-зелёным, а не зеброй.
    from oplata import styles

    view.set_month(date.today())
    qt_app.processEvents()
    today = date.today()
    # Строка дня — внутри своего ряда (1–10, 11–20, 21+), поэтому и
    # столбец берём из того же ряда: иначе с 11-го по 30-е число тест
    # смотрел бы в чужой ряд и не видел зелёную заливку.
    today_block = (today.day - 1) // 10
    today_row = (today.day - 1) % 10 * 3
    assert (
        table.item(
            today_row, _block_first_column(today_block)
        ).background().color().name()
        == styles.WEEK_TABLE_TODAY_GREEN_BODY
    )
    window.close()


def test_receipts_page_scrolls_in_half_screen(qt_app, store):
    """v0.48.0: страница «Поступление и оплата» прокручивается по вертикали.

    Замечание пользователя: в половине экрана «вертикальной прокрутки
    нет и всё сбивается в кучу». Раньше страница не прокручивалась
    вовсе: рамка «Поступления» сжималась (235 точек при содержимом
    249) и его таблица с фиксированной высотой вылезала наружу,
    наезжая на блок «Оплата», а «Оплата» схлопывалась до двух строк.
    """

    from oplata.app import IDX_RECEIPTS, MainWindow
    from oplata.views.receipts_view import MIN_STRETCH_ROWS, ROW_HEIGHT

    window = MainWindow(store)
    window.resize(1920, 1040)
    window.show()
    window.show_page(IDX_RECEIPTS)
    qt_app.processEvents()
    view = window.receipts_view
    scroll = view.scroll_area
    # Полоса — по необходимости: в полном экране её не видно.
    assert scroll.verticalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert scroll.verticalScrollBar().maximum() == 0
    # Горизонтальной прокрутки нет (общее правило таблиц).
    assert scroll.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert scroll.horizontalScrollBar().maximum() == 0

    # Половина экрана: место кончилось — появляется вертикальная прокрутка.
    window.resize(1920, 520)
    qt_app.processEvents()
    assert scroll.verticalScrollBar().maximum() > 0
    incoming, outgoing = view.blocks
    # Блоки стоят целиком: рамка не сжимается ниже своего содержимого,
    # поэтому таблица из неё не вылезает и на «Оплату» не наезжает.
    for block in (incoming, outgoing):
        assert block.height() >= block.minimumHeight()
        assert block.minimumHeight() >= block.layout().minimumSize().height()
    # «Оплата» не схлопывается до двух строк.
    assert outgoing.table.height() >= (
        outgoing.table.horizontalHeader().height() + MIN_STRETCH_ROWS * ROW_HEIGHT
    )
    for table in (incoming.table, outgoing.table):
        assert table.horizontalScrollBar().maximum() == 0
    window.close()


def test_money_block_content_stays_inside_its_frame(qt_app, store):
    """v0.48.0: содержимое блока не вылезает за его рамку в тесном окне.

    У «Поступления» высота таблицы фиксирована (v0.14.0), поэтому
    раньше при нехватке места сжималась только рамка: таблица
    выполняла наружу и наезжала на блок «Оплата».
    """

    from oplata.app import MainWindow
    from oplata.models import OUTGOING, add_other_payment

    for name in ("Аренда", "Зарплата", "Налоги", "Связь", "Транспорт"):
        add_other_payment(store.data, name, "2026-11-10", OUTGOING)
    window = MainWindow(store)
    window.resize(1920, 520)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    incoming, outgoing = view.blocks
    # «Поставщики» + пять своих видов оплаты.
    assert outgoing.table.rowCount() == 6
    # Прокрутка страницы подбирает то, что не влезло по высоте.
    assert view.scroll_area.verticalScrollBar().maximum() > 0
    for block in (incoming, outgoing):
        table = block.table
        margins = block.contentsMargins()
        needed = margins.top() + table.y() + table.height() + margins.bottom()
        assert block.height() >= needed - 1
    window.close()



def test_kkm_table_blocks_have_their_own_frame(qt_app, store):
    """v0.45.0: у каждого ряда дней своя обводка, поле между ними пустое."""
    from PySide6.QtGui import QColor

    from oplata.app import IDX_KKM, MainWindow
    from oplata.views.kkm_view import (
        BLOCK_FRAME,
        COLUMNS_PER_BLOCK,
        SEPARATOR_COLUMNS,
        _block_first_column,
    )

    window = MainWindow(store)
    window.resize(1600, 900)
    window.show()
    window.show_page(IDX_KKM)
    qt_app.processEvents()
    table = window.kkm_view.table

    image = table.grab().toImage()
    widths = [table.columnWidth(c) for c in range(table.columnCount())]
    y = table.rowViewportPosition(2) + table.rowHeight(2) // 2
    border = QColor(BLOCK_FRAME).name()

    def color(x: int) -> str:
        return image.pixelColor(x, y).name()

    # Левая граница первого ряда и правая граница каждого ряда — рамка.
    assert color(0) == border
    right_edges = [
        sum(widths[: _block_first_column(block) + COLUMNS_PER_BLOCK]) - 2
        for block in range(3)
    ]
    for x in right_edges:
        assert color(x) == border
    # Поле между рядами не закрашено: там фон панели, а не рамка.
    gap_start = sum(widths[: _block_first_column(1)]) - len(SEPARATOR_COLUMNS) * 0
    middle_of_gap = sum(widths[: SEPARATOR_COLUMNS[0]]) + widths[SEPARATOR_COLUMNS[0]] // 2
    assert color(middle_of_gap) != border
    assert gap_start > 0
    window.close()


def test_window_opens_at_half_of_the_screen(qt_app):
    """v0.47.0: окно — **половина экрана по горизонтали**.

    Экран делится линией по горизонтали: программа занимает **всю
    ширину** экрана и **половину его высоты**. На экране 1920×1040 это
    1920×520. (В v0.46.0 я перепутал направление и делил экран по
    вертикали — половина ширины и вся высота.)
    """
    from PySide6.QtCore import QRect

    from oplata.app import MIN_WINDOW_HEIGHT, half_screen_geometry

    rect = half_screen_geometry(QRect(0, 0, 1920, 1040))
    assert (rect.width(), rect.height()) == (1920, 520)
    assert rect.left() == 0
    # v0.49.0: половина — **нижняя** (просьба пользователя): низ окна
    # стоит у нижнего края доступной области.
    assert rect.top() == 520
    assert rect.bottom() == 1039


def test_window_stays_in_the_bottom_half_when_area_is_shifted(qt_app):
    """v0.49.0: окно стоит в нижней половине при любой доступной области.

    Панель задач может быть и сверху, и снизу — окно всё равно
    занимает нижнюю половину **доступной** области и не вылезает за её
    пределы.
    """
    from PySide6.QtCore import QRect

    from oplata.app import MIN_WINDOW_HEIGHT, half_screen_geometry

    # Доступная область сдвинута вниз (панель задач сверху): окно — в
    # нижней половине этой области, а не экрана.
    shifted = half_screen_geometry(QRect(0, 40, 1920, 1000))
    assert (shifted.top(), shifted.height()) == (540, 500)
    assert shifted.bottom() == 1039
    # Панель задач снизу: доступная область укорочена, окно над ней.
    taskbar = half_screen_geometry(QRect(0, 0, 1920, 1000))
    assert (taskbar.top(), taskbar.height()) == (500, 500)
    assert taskbar.bottom() == 999
    # Ниже разумного минимума высоту не роняем: окно прижато к низу и
    # занимает минимум (400 точек), а не половину (250).
    low = half_screen_geometry(QRect(0, 0, 1920, 500))
    assert low.height() == max(500 // 2, MIN_WINDOW_HEIGHT)
    assert low.top() == 100
    assert low.bottom() == 499


def test_main_window_uses_half_screen_geometry(qt_app, store, monkeypatch):
    """v0.47.0: MainWindow ставит себя в половину экрана при запуске."""
    from PySide6.QtCore import QRect

    from oplata import app as app_module
    from oplata.app import MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH, MainWindow

    monkeypatch.setattr(
        app_module, "half_screen_geometry", lambda: QRect(0, 0, 1920, 520)
    )
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    assert (window.width(), window.height()) == (1920, 520)
    assert (window.minimumWidth(), window.minimumHeight()) == (
        MIN_WINDOW_WIDTH,
        MIN_WINDOW_HEIGHT,
    )
    window.close()


def test_kkm_amount_column_is_half_of_v045_width(qt_app, store):
    """v0.47.0: «Сумма» ровно вдвое у́же, чем была в v0.45.0.

    В v0.45.0 колонка занимала 1/1,5 доли ряда (при окне 1920 — 236
    точек), теперь — 1/3 доли (118 точек). Пол ``AMOUNT_MIN_WIDTH``
    страхует только узкие окна: там точная половина обрезала бы сумму.
    """
    from datetime import date

    from oplata.app import IDX_KKM, MainWindow
    from oplata.views.kkm_view import (
        AMOUNT_MIN_WIDTH,
        AMOUNT_WIDTH_RATIO,
        BLOCKS,
        COL_AMOUNT,
        COL_DATE,
        COL_KIND,
        _block_first_column,
    )

    window = MainWindow(store)
    window.resize(1920, 1040)
    window.show()
    window.show_page(IDX_KKM)
    qt_app.processEvents()
    view = window.kkm_view
    view.set_month(date(2026, 11, 1))
    qt_app.processEvents()
    table = view.table

    fixed = 0
    for block in range(BLOCKS):
        first = _block_first_column(block)
        fixed += table.columnWidth(first + COL_DATE)
        fixed += table.columnWidth(first + COL_KIND)
    share = (table.viewport().width() - fixed) / BLOCKS
    # Половина от ширины v0.45.0 (доля / 1,5) — это доля / 3.
    expected = max(int(share / 1.5 / 2), AMOUNT_MIN_WIDTH)
    assert AMOUNT_WIDTH_RATIO == 1 / 3
    for block in range(BLOCKS):
        assert table.columnWidth(_block_first_column(block) + COL_AMOUNT) == expected
    assert expected == 118  # при окне 1920: было 236, стало 118
    assert expected < table.columnWidth(COL_KIND)
    # Горизонтальной прокрутки нет.
    assert sum(table.columnWidth(c) for c in range(table.columnCount())) <= (
        table.viewport().width()
    )
    window.close()


def test_kkm_table_scrolls_vertically_in_half_screen(qt_app, store):
    """v0.47.0: в половину экрана месяц не влезает — появляется прокрутка.

    В полном экране всё как прежде: 30 строк растягиваются по высоте,
    прокрутки нет. В окне в половину экрана строки остаются читаемыми
    (не ниже ``MIN_ROW_HEIGHT``), а до конца месяца можно докрутить.
    """
    from datetime import date

    from oplata.app import IDX_KKM, MainWindow
    from oplata.views.kkm_view import MIN_ROW_HEIGHT

    window = MainWindow(store)
    window.resize(1920, 1040)
    window.show()
    window.show_page(IDX_KKM)
    qt_app.processEvents()
    view = window.kkm_view
    view.set_month(date(2026, 11, 1))
    qt_app.processEvents()
    table = view.table
    assert table.rowCount() == 30
    # Полный экран: месяц целиком, прокрутки нет (как было и раньше).
    assert table.verticalScrollBar().isVisible() is False
    assert table.rowHeight(0) >= MIN_ROW_HEIGHT

    # Половина экрана: строки встают по минимуму, появляется прокрутка.
    window.resize(1920, 520)
    qt_app.processEvents()
    assert table.rowHeight(0) >= MIN_ROW_HEIGHT
    assert table.verticalScrollBar().isVisible() is True
    assert table.verticalScrollBar().maximum() > 0
    # По горизонтали прокрутки по-прежнему нет.
    assert sum(table.columnWidth(c) for c in range(table.columnCount())) <= (
        table.viewport().width()
    )
    window.close()


def test_kkm_tab_is_in_rights_table_and_migrates_old_files(
    qt_app, store, monkeypatch, tmp_path
):
    """v0.42.0: вкладку видно в правах; старые файлы пользователей читаются."""
    from oplata import access as access_module
    from oplata.app import MainWindow
    from oplata.views.data_view import PAGE_TITLES

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)

    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    window.data_view.users_panel.set_expanded(True)
    qt_app.processEvents()

    # В «Права и пользователи» для новой вкладки своя галочка.
    table = window.data_view.users_table
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert "Отчеты ККМ" in headers
    # У администратора и руководителя она отмечена, у пользователей баз — нет.
    kkm_col = headers.index("Отчеты ККМ")
    for row in (0, 1):  # Администратор, Руководитель
        assert table.item(row, kkm_col).checkState() is Qt.CheckState.Checked
    for row in (2, 3):  # Пользователь МИР / КОРЕЛИЧИ
        assert table.item(row, kkm_col).checkState() is Qt.CheckState.Unchecked
    window.close()

    # Прежний файл (номера вкладок 0..3) читается: «Покупатели» остаются
    # «Покупателями», у полного доступа добавляется «Отчеты ККМ».
    (tmp_path / "users.json").write_text(
        '{"users": ['
        '{"id": "old-full", "name": "Прежний администратор", '
        '"pages": [0, 1, 2, 3], "base": null, "can_edit": true, '
        '"can_switch_base": true},'
        '{"id": "old-mir", "name": "Прежний пользователь МИР", '
        '"pages": [2], "base": "mir", "can_edit": false, "can_switch_base": false}'
        "]}",
        encoding="utf-8",
    )
    users = access_module.load_users()
    full = next(u for u in users if u.id == "old-full")
    mir_user = next(u for u in users if u.id == "old-mir")
    titles = {i: t for i, t in enumerate(PAGE_TITLES)}
    assert [titles[p] for p in mir_user.pages] == ["Покупатели"]
    assert "Отчеты ККМ" in [titles[p] for p in full.pages]
    assert "Покупатели" in [titles[p] for p in full.pages]
    # Теперь права пишутся именами вкладок: вставка новой не сдвинет их.
    access_module.save_users(users)
    import json

    written = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    saved = {u["id"]: u["pages"] for u in written["users"]}
    assert saved["old-mir"] == ["customers"]
    assert saved["old-full"] == ["receipts", "kkm", "suppliers", "customers", "data"]


def test_config_tab_button_is_always_on_the_top_bar(qt_app, store, monkeypatch, tmp_path):
    """v0.50.0: кнопка «Конфигурация» видна на плашке у любого пользователя.

    Просьба пользователя: «независимо от того кто пользователь кнопка
    конфигурация должна быть всегда на верхней плашке». Роли это не
    отменяет: вкладка по-прежнему открывается паролем, и что можно
    править внутри, решают права пользователя.
    """
    from oplata import access as access_module
    from oplata.app import IDX_DATA, MainWindow

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    access_module.set_password("1234")
    _admin, _manager, user_mir, _user_kor = access_module.default_users()

    window = MainWindow(store, access_module.Access(user_mir))
    window.show()
    qt_app.processEvents()
    # У пользователя МИР из вкладок только «Покупатели», но кнопка
    # «Конфигурация» на плашке есть.
    assert window.nav_buttons[IDX_DATA].isVisible()
    assert window.stack.currentIndex() != IDX_DATA
    # Пароль спрашивают по-прежнему: с неверным вкладка не открывается.
    _ask_password_as(monkeypatch, ("0000", True))
    window.nav_buttons[IDX_DATA].click()
    qt_app.processEvents()
    assert window.stack.currentIndex() != IDX_DATA
    # С верным паролем вкладка открывается и у пользователя МИР.
    _ask_password_as(monkeypatch, ("1234", True))
    window.nav_buttons[IDX_DATA].click()
    qt_app.processEvents()
    assert window.stack.currentIndex() == IDX_DATA
    window.close()


def test_config_password_gives_full_rights_inside_the_tab(qt_app, store, monkeypatch, tmp_path):
    """v0.51.0: внутри «Конфигурации» права даёт пароль, а не только роль.

    Замечание пользователя: «В конфигурации пользователи и права
    пропала кнопка "сохранить изменения"». Раньше права внутри раздела
    считала роль, и у Руководителя, Пользователя МИР и Пользователя
    КОРЕЛИЧИ пропадали «Сохранить пользователей», «Добавить»,
    «Удалить», «Сменить пароль» и кнопки календаря: раздел открывался,
    а править в нём было нельзя.
    """
    from oplata import access as access_module
    from oplata.app import IDX_CUSTOMERS, IDX_DATA, MainWindow

    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    access_module.set_password("1234")
    _admin, manager, user_mir, _user_kor = access_module.default_users()

    for user in (manager, user_mir):
        window = MainWindow(store, access_module.Access(user))
        window.show()
        qt_app.processEvents()
        view = window.data_view
        # Без пароля раздел для правки закрыт.
        assert view.users_save_btn.isHidden()
        assert view.save_button.isHidden()
        # Пароль принят — внутри «Конфигурации» работаем полностью.
        _ask_password_as(monkeypatch, ("1234", True))
        window.nav_buttons[IDX_DATA].click()
        qt_app.processEvents()
        assert window.stack.currentIndex() == IDX_DATA  # v0.50.0: пускает любого
        assert not view.users_save_btn.isHidden()
        assert not view.users_add_btn.isHidden()
        assert not view.users_remove_btn.isHidden()
        assert not view.users_password_btn.isHidden()
        # Календарь праздников на этой же вкладке — тоже доступен.
        assert not view.save_button.isHidden()
        assert not view.cancel_button.isHidden()
        # Остальные вкладки по-прежнему только для просмотра: право
        # ввода данных даёт роль, а не пароль на раздел.
        assert window.suppliers_view.read_only is True
        assert window.receipts_view.incoming.read_only is True
        # Ушли с вкладки — пароль снят, права снова по роли.
        window.show_page(IDX_CUSTOMERS)
        qt_app.processEvents()
        assert view.users_save_btn.isHidden()
        window.close()


def test_operations_journal_is_open_for_users_without_edit_rights(qt_app, store):
    """v0.52.0: журнал операций доступен всем, у кого стоит галочка вкладки.

    Просьба пользователя: «В режиме "покупатель" режим Журнал операций
    должен быть доступен всегда для всех пользователей для кого
    доступен (стоит галочка). В режиме "поставщик" — так же». Раньше
    ``_on_item_clicked`` при ``read_only`` просто выходил, и журнал не
    открывался ни кликом, ни правой кнопкой: у Руководителя,
    Пользователя МИР и Пользователя КОРЕЛИЧИ доступа к нему не было.

    Журнал только показывает операции: окно открывается без кнопок
    «Добавить/Изменить/Удалить» (v0.23.1).
    """
    from oplata import access as access_module
    from oplata.app import IDX_CUSTOMERS, MainWindow
    from oplata.models import Counterparty
    from oplata.views.week_view import FIRST_DATA_ROW

    store.data.customers[:] = [Counterparty(name="Покупатель 01")]
    admin, manager, user_mir, _user_kor = access_module.default_users()

    for user in (manager, user_mir):
        window = MainWindow(store, access_module.Access(user))
        window.show()
        qt_app.processEvents()
        opened: list[str] = []
        window.open_operations_journal = lambda kind, _cid: opened.append(kind)
        window.show_page(IDX_CUSTOMERS)
        qt_app.processEvents()
        view = window.customers_view
        assert view.read_only is True
        item = view.table.item(FIRST_DATA_ROW, 0)
        # Клик по имени открывает журнал.
        view._on_item_clicked(item)  # noqa: SLF001 - вызываем обработчик
        qt_app.processEvents()
        assert opened == ["customer"]
        # Правый клик — тоже журнал: «Реализация» меняет данные и без
        # права ввода недоступна.
        opened.clear()
        view.on_open_realization = lambda _cid, _d=None: opened.append("реализация")
        view._on_context_menu(  # noqa: SLF001 - вызываем обработчик
            view.table.visualItemRect(item).center()
        )
        qt_app.processEvents()
        assert opened == ["customer"]
        window.close()

    # У администратора как прежде: клик — журнал, правый клик — окно
    # «Реализация».
    window = MainWindow(store, access_module.Access(admin))
    window.show()
    qt_app.processEvents()
    opened = []
    window.open_operations_journal = lambda kind, _cid: opened.append(kind)
    window.show_page(IDX_CUSTOMERS)
    qt_app.processEvents()
    view = window.customers_view
    assert view.read_only is False
    item = view.table.item(FIRST_DATA_ROW, 0)
    view._on_item_clicked(item)  # noqa: SLF001 - вызываем обработчик
    qt_app.processEvents()
    assert opened == ["customer"]
    opened.clear()
    view.on_open_realization = lambda _cid, _d=None: opened.append("реализация")
    view._on_context_menu(  # noqa: SLF001 - вызываем обработчик
        view.table.visualItemRect(item).center()
    )
    qt_app.processEvents()
    assert opened == ["реализация"]
    window.close()


def test_amount_input_is_closed_without_edit_rights(qt_app, store):
    """v0.52.0: без права ввода на вкладке «Поступление и оплата» ввод закрыт.

    Замечание пользователя: «На вкладке поступления и оплата для
    пользователя у которого нет доступа для ввода данных всё равно
    сохранилась возможность ввода данных». Окно со списком
    контрагентов и полями сумм открывалось по клику по строке и
    сохраняло введённое.
    """
    from oplata import access as access_module
    from oplata.app import MainWindow
    from oplata.models import OUTGOING, Counterparty, add_other_payment
    from oplata.views.receipts_view import CUSTOMERS_ROW

    # Покупатель нужен, чтобы окно ввода (у администратора) открылось,
    # а не показало сообщение «Нет покупателей».
    store.data.customers[:] = [Counterparty(name="Покупатель 01")]
    add_other_payment(store.data, "Аренда", "2026-11-10", OUTGOING)
    admin, manager, user_mir, _user_kor = access_module.default_users()

    for user in (manager, user_mir):
        window = MainWindow(store, access_module.Access(user))
        window.show()
        qt_app.processEvents()
        view = window.receipts_view
        block = view.incoming
        assert block.read_only is True
        # Окно ввода сумм по строке «Покупатели» не открывается.
        block._on_cell_clicked(  # noqa: SLF001 - вызываем обработчик
            block.row_keys.index(CUSTOMERS_ROW), 0
        )
        qt_app.processEvents()
        assert view._popup is None  # noqa: SLF001 - окно справа от строки
        # Журнал строки (касса) — окно только для просмотра, он доступен.
        block._on_cell_clicked(0, 0)  # noqa: SLF001 - вызываем обработчик
        qt_app.processEvents()
        assert view._popup is not None  # noqa: SLF001 - окно справа от строки
        assert type(view._popup).__name__ == "JournalPopup"  # noqa: SLF001
        # И сохранение суммы закрыто, откуда бы ввод ни пришёл.
        before = len(store.data.operations)
        view.save_direct_amount(CUSTOMERS_ROW, "1500")
        qt_app.processEvents()
        assert len(store.data.operations) == before
        window.close()

    # У администратора окно ввода открывается, как и раньше.
    window = MainWindow(store, access_module.Access(admin))
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    block = view.incoming
    assert block.read_only is False
    block._on_cell_clicked(  # noqa: SLF001 - вызываем обработчик
        block.row_keys.index(CUSTOMERS_ROW), 0
    )
    qt_app.processEvents()
    assert view._popup is not None  # noqa: SLF001 - окно справа от строки
    window.close()


def test_counterparty_card_saves_unp(qt_app, store):
    """v0.53.0: УНП вводится в карточке поставщика и покупателя.

    Просьба пользователя: «сделай в карточках поставщиков и покупателей
    возможность ввода УНП». Поле одно и то же для обоих видов карточек
    (структура контрагента общая), пустым его оставлять можно.
    """

    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    for kind, items in (
        ("supplier", store.data.suppliers),
        ("customer", store.data.customers),
    ):
        dialog = CounterpartyCardDialog(store, kind)
        dialog.name_edit.setText("Контрагент с УНП")
        dialog.unp_edit.setText("123456789")
        dialog._add()
        added = items[-1]
        assert added.unp == "123456789"

        # Повторное открытие показывает сохранённый УНП.
        reopened = CounterpartyCardDialog(store, kind, selected_id=added.id)
        assert reopened.unp_edit.text() == "123456789"
        reopened.close()

        # Пустой УНП — допустимое значение (не заполняли).
        dialog.name_edit.setText("Без УНП")
        dialog.unp_edit.clear()
        dialog._add()
        assert items[-1].unp == ""
        dialog.close()


def test_counterparty_card_rejects_incomplete_unp(qt_app, store, monkeypatch):
    """v0.53.0: неполный УНП не сохраняется, карточка остаётся открытой."""

    from PySide6.QtWidgets import QMessageBox

    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: warnings.append(str(args[-1])),
    )

    dialog = CounterpartyCardDialog(store, "supplier")
    before = len(store.data.suppliers)
    dialog.name_edit.setText("Короткий УНП")
    dialog.unp_edit.setText("12345")
    dialog._save_and_close()

    assert len(warnings) == 1
    assert "9 цифр" in warnings[0]
    assert len(store.data.suppliers) == before  # ничего не сохранилось
    assert dialog.result() != dialog.DialogCode.Accepted

    # Девять цифр — сохраняется и закрывается.
    dialog.unp_edit.setText("987654321")
    dialog._save_and_close()
    assert store.data.suppliers[-1].unp == "987654321"
    dialog.close()


def test_unp_survives_excel_round_trip(store, tmp_path):
    """v0.53.0: УНП переживает выгрузку баз в Excel и загрузку обратно."""

    pytest.importorskip("openpyxl")

    from oplata.excelio import export_bases, import_bases
    from oplata.models import Counterparty
    from oplata.storage import BASE_MIR

    store.data.suppliers.append(Counterparty(name="Поставщик У", unp="111222333"))
    store.data.customers.append(Counterparty(name="Покупатель У", unp="444555666"))
    store.save()

    path = export_bases({BASE_MIR: store.data}, tmp_path / "oplata.xlsx")
    (imported,) = import_bases(path).values()

    assert [s.unp for s in imported.suppliers if s.name == "Поставщик У"] == [
        "111222333"
    ]
    assert [c.unp for c in imported.customers if c.name == "Покупатель У"] == [
        "444555666"
    ]


def test_old_excel_without_unp_column_imports(store, tmp_path):
    """v0.53.0: выгрузка старой версии (без колонки УНП) читается как прежде."""

    pytest.importorskip("openpyxl")

    from openpyxl import Workbook

    from oplata.excelio import (
        SHEET_CUSTOMERS,
        SHEET_SUPPLIERS,
        _sheet_title,
        import_bases,
    )
    from oplata.storage import BASE_MIR

    book = Workbook()
    book.remove(book.active)
    for suffix in (SHEET_SUPPLIERS, SHEET_CUSTOMERS):
        sheet = book.create_sheet(_sheet_title(BASE_MIR, suffix))
        sheet.append(
            [
                "Название",
                "Идентификатор",
                "Отсрочка (дней)",
                "Предоплата",
                "Реализация",
                "В архиве",
            ]
        )
        sheet.append(["Старый контрагент", "id-1", 0, "нет", "нет", "нет"])
    path = tmp_path / "old.xlsx"
    book.save(path)

    (imported,) = import_bases(path).values()

    assert [s.name for s in imported.suppliers] == ["Старый контрагент"]
    assert [s.unp for s in imported.suppliers] == [""]  # пусто, а не мусор
    assert [c.unp for c in imported.customers] == [""]


SAMPLE_STATEMENT = "tests/fixtures/statement-mir.pdf"
# v0.64.0: выписка с платежами в бюджет — у них перед суммой стоит
# код платежа («00101 300,00 0,00»).
BUDGET_STATEMENT = "tests/fixtures/statement-budget.pdf"


def test_statement_parser_reads_bank_statement(store):
    """v0.54.0: выписка PDF разбирается в операции с УНП и суммами.

    Образец — выписка ОАО «АСБ Беларусбанк» за 05.11.2026: две оплаты
    (дебет) и четыре поступления (кредит).
    """

    from decimal import Decimal

    from oplata.statement import parse_statement

    statement = parse_statement(SAMPLE_STATEMENT)

    assert statement.statement_date == "05.11.2026"
    assert statement.opening_balance == Decimal("29719.47")
    assert statement.closing_balance == Decimal("20873.12")
    assert statement.turnover_outgoing == Decimal("13958.50")  # дебет
    assert statement.turnover_incoming == Decimal("5112.15")  # кредит

    # Дата берётся из «Фактическая дата и время совершения операции»,
    # а не из даты документа (в образце они разные: 03.11 против 05.11).
    assert [row.date for row in statement.rows] == ["05.11.2026"] * 6
    assert [row.unp for row in statement.rows] == [
        "100005713",
        "192775574",
        "590959404",
        "500021189",
        "100325912",
        "100325912",
    ]
    assert statement.rows[0].name == 'ООО "ОМА"'
    assert statement.rows[0].outgoing == Decimal("5549.97")  # оплата — дебет
    assert statement.rows[0].incoming == Decimal("0")
    assert statement.rows[2].incoming == Decimal("8.40")  # поступление — кредит
    assert statement.rows[2].outgoing == Decimal("0")
    # Многострочное «Назначение платежа» не ломает разбор имён.
    assert statement.rows[4].name == 'ОАО "АСБ БЕЛАРУСБАНК"'
    assert "платежными карточками" in statement.rows[4].purpose

    # Итоги строк совпадают с оборотами файла, сальдо сходится.
    # v0.55.0: комиссия банка попадает и в оплату, и в поступление,
    # поэтому итог строк больше оборота файла ровно на неё.
    assert statement.rows[4].commission == Decimal("18.02")
    assert statement.total_commission == Decimal("18.02")
    assert statement.total_outgoing == statement.turnover_outgoing + Decimal(
        "18.02"
    )
    assert statement.total_incoming == statement.turnover_incoming + Decimal(
        "18.02"
    )
    assert (
        statement.opening_balance
        - statement.total_outgoing
        + statement.total_incoming
        == statement.closing_balance
    )


def test_statement_parser_rejects_foreign_file():
    """v0.54.0: файл не по образцу выписки — понятная ошибка, а не пустая таблица."""

    from oplata.statement import StatementError, parse_statement_text

    with pytest.raises(StatementError) as error:
        parse_statement_text("Счёт-фактура № 17 от 05.11.2026\nИтого: 100,00")
    assert "не похож" in str(error.value)


def test_statement_dialog_shows_operations_table(qt_app, store):
    """v0.54.0: окно выписки — таблица «Дата · УНП · Наименование · Оплата · Поступление».

    Порядок столбцов такой, как просил пользователь: **оплата перед
    поступлением**.
    """

    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import parse_statement

    statement = parse_statement(SAMPLE_STATEMENT)
    dialog = StatementDialog(statement, "вм3.pdf")
    table = dialog.table

    assert [
        table.horizontalHeaderItem(column).text() for column in range(5)
    ] == ["Дата", "УНП", "Наименование организации", "Оплата", "Поступление"]

    assert table.rowCount() == len(statement.rows) + 1  # + строка «ИТОГО»
    assert table.item(0, 0).text() == "05.11.2026"
    assert table.item(0, 1).text() == "100005713"
    assert table.item(0, 2).text() == 'ООО "ОМА"'
    assert table.item(0, 3).text() == "5 549,97"  # оплата
    assert table.item(0, 4).text() == ""  # поступления нет — пусто, не «0,00»
    assert table.item(2, 4).text() == "8,40"
    assert table.item(2, 3).text() == ""

    # v0.55.0: комиссия банка — в оплате и добавлена к поступлению.
    assert table.item(4, 3).text() == "18,02"
    assert table.item(4, 4).text() == "1 221,77"  # 1 203,75 + 18,02

    total_row = len(statement.rows)
    assert table.item(total_row, 0).text() == "ИТОГО"
    assert table.item(total_row, 3).text() == "13 976,52"  # 13 958,50 + 18,02
    assert table.item(total_row, 4).text() == "5 130,17"  # 5 112,15 + 18,02
    dialog.close()


def test_statement_dialog_marks_bank_rows(qt_app, store):
    """v0.54.0: строки банка из «Конфигурации» отмечаются — он не контрагент.

    Замечание пользователя: «ОАО "АСБ БЕЛАРУСБАНК" является ни продавцом
    ни покупателем». УНП банка задаётся карточкой в «Конфигурации», по
    нему его строки и узнаются.
    """

    from PySide6.QtGui import QColor

    from oplata import styles
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import parse_statement

    statement = parse_statement(SAMPLE_STATEMENT)
    store.data.bank.unp = "100325912"
    dialog = StatementDialog(
        statement, "вм3.pdf", bank_unp=store.data.bank.unp
    )
    table = dialog.table

    assert [dialog._is_bank(row) for row in statement.rows] == [
        False, False, False, False, True, True,
    ]
    assert table.item(0, 2).background().color() != QColor(styles.WORKDAY_GREY)
    assert table.item(5, 2).background().color() == QColor(styles.WORKDAY_GREY)
    assert "Строк банка (не поставщик и не покупатель): 2" in dialog._caption(
        "вм3.pdf"
    )
    # Суммы банка в «ИТОГО» остаются: деньги-то настоящие (включая
    # комиссию, v0.55.0).
    assert table.item(len(statement.rows), 4).text() == "5 130,17"
    dialog.close()


def test_bank_card_is_saved_in_configuration(qt_app, store, monkeypatch):
    """v0.54.0: карточка банка сохраняется из «Конфигурации» и читается из файла."""

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from oplata.app import MainWindow
    from oplata.models import Bank
    from oplata.storage import DataStore

    window = MainWindow(store)
    view = window.data_view

    # По умолчанию карточка банка пустая по УНП, наименование подставлено.
    assert isinstance(store.data.bank, Bank)

    view.bank_unp_edit.setText("100325912")
    view.bank_name_edit.setText('ОАО "АСБ БЕЛАРУСБАНК"')
    assert view.save_bank() is True
    assert store.data.bank.unp == "100325912"

    # Прочитанное из файла совпадает с сохранённым.
    reread = DataStore(store.path)
    assert reread.data.bank.unp == "100325912"
    assert reread.data.bank.name == 'ОАО "АСБ БЕЛАРУСБАНК"'

    # Неполный УНП не сохраняется: программа не сможет узнать банк.
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *args: warnings.append(str(args[-1]))),
    )
    view.bank_unp_edit.setText("12345")
    assert view.save_bank() is False
    assert warnings and "9 цифр" in warnings[0]
    assert store.data.bank.unp == "100325912"  # прежнее значение не затерли

    # Кнопка выписки есть на плашке и открывает таблицу.
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (SAMPLE_STATEMENT, "")),
    )
    opened: list[object] = []
    from oplata.dialogs.statement_dialog import StatementDialog

    monkeypatch.setattr(
        StatementDialog,
        "exec",
        lambda self: opened.append(self) or 1,
    )
    window.statement_button.click()
    assert len(opened) == 1
    assert opened[0].table.rowCount() == 7  # 6 операций + «ИТОГО»
    assert opened[0].bank_unp == "100325912"
    window.close()


def test_bank_commission_is_added_to_incoming(qt_app, store):
    """v0.55.0: «Комиссия банка - 18.02» — в оплате и сверх поступления.

    Пояснение пользователя: если в тексте платежа есть такая запись, то
    в столбце «Поступление» к сумме прибавляется комиссия (1 203,75 +
    18,02 = 1 221,77), а в столбце «Оплата» стоит сама комиссия.
    """

    from decimal import Decimal

    from oplata.statement import parse_statement, parse_statement_text

    statement = parse_statement(SAMPLE_STATEMENT)
    row = statement.rows[4]  # зачисление выручки по карточкам
    assert row.incoming == Decimal("1203.75")  # как в файле
    assert row.commission == Decimal("18.02")
    assert row.incoming_shown == Decimal("1221.77")
    assert row.outgoing_shown == Decimal("18.02")
    # Сальдо от комиссии не меняется: она проходит с двух сторон.
    assert (
        statement.opening_balance
        - statement.total_outgoing
        + statement.total_incoming
        == statement.closing_balance
    )

    # Комиссию узнаём и с точкой, и с запятой в сумме.
    text = (
        "05.11.2026 1 1 Счет: BY00\n"
        "БИК: AKBBBY2X УНП: 100325912\n"
        "0,00 100,00\n"
        "ООО Тест\n"
        "Назначение платежа: ВЫРУЧКА. Комиссия банка - 1,50\n"
        "Фактическая дата и время совершения операции: 05.11.2026 10:00:00\n"
    )
    parsed = parse_statement_text(text)
    assert parsed.rows[0].commission == Decimal("1.50")
    assert parsed.rows[0].incoming_shown == Decimal("101.50")
    assert parsed.rows[0].outgoing_shown == Decimal("1.50")


def test_other_payment_card_saves_unp(qt_app, store):
    """v0.55.0: в карточке вида оплаты есть УНП, и он сохраняется."""

    from datetime import date

    from oplata.views.receipts_view import OtherPaymentCardDialog

    dialog = OtherPaymentCardDialog(
        "outgoing", set(), date(2026, 11, 12), None, unp="100325912"
    )
    assert dialog.unp_edit.text() == "100325912"
    # Буквы и знаки в УНП не вводятся, лишние разряды не набираются.
    dialog.unp_edit.clear()
    for char in "ab12-3 4567890123":
        dialog.unp_edit.insert(char)
    assert dialog.unp_edit.text() == "123456789"
    dialog.close()


def test_week_tables_are_sorted_alphabetically(qt_app, store):
    """v0.55.0: поставщики и покупатели в таблице стоят по алфавиту."""

    from oplata.app import MainWindow
    from oplata.models import Counterparty

    store.data.suppliers = [
        Counterparty(name=name) for name in ("Ясень", "ёлка", "Акация", "Берёза")
    ]
    store.data.customers = [
        Counterparty(name=name) for name in ("Юпитер", "Альфа", "ёж", "Вега")
    ]
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()

    for view in (window.suppliers_view, window.customers_view):
        table = view.table
        names = [
            table.item(row, 0).text()
            for row in range(4, 4 + len(view.counterparties()))
        ]
        assert names == [c.name for c in view.counterparties()]
        assert names == sorted(names, key=lambda name: name.casefold().replace(
            "ё", "е"
        ))
    # «ё» идёт как «е»: Берёза → ёлка, а не в конец списка.
    suppliers = [c.name for c in window.suppliers_view.counterparties()]
    assert suppliers == ["Акация", "Берёза", "ёлка", "Ясень"]
    customers = [c.name for c in window.customers_view.counterparties()]
    assert customers == ["Альфа", "Вега", "ёж", "Юпитер"]
    window.close()


def test_statement_table_rows_are_narrow(qt_app, store):
    """v0.55.0: строки таблицы выписки узкие — по шрифту, без «воздуха»."""

    from oplata.statement import parse_statement
    from oplata.dialogs.statement_dialog import StatementDialog

    statement = parse_statement(SAMPLE_STATEMENT)
    dialog = StatementDialog(statement, "вм3.pdf")
    dialog.show()
    qt_app.processEvents()
    table = dialog.table

    # Высота задана одна на все строки: одна строка текста по шрифту,
    # без запаса. Обычная таблица Qt даёт 30 px на строку.
    assert table.rowHeight(0) == table.verticalHeader().defaultSectionSize()
    assert table.rowHeight(0) < 30
    assert table.wordWrap() is False  # иначе длинное имя займёт две строки
    dialog.close()


# --------------------------------------------------------------- v0.56.0
def test_statement_account_name_defines_base(qt_app):
    """v0.56.0: база определяется по «Наименованию счета» из шапки."""

    from oplata.statement import parse_statement
    from oplata.statement_match import account_base_key
    from oplata.storage import BASE_KORELICHI, BASE_MIR

    statement = parse_statement(SAMPLE_STATEMENT)
    assert statement.account_name  # шапка прочитана
    assert account_base_key(statement.account_name) == BASE_MIR
    assert account_base_key("ООО Мирсититорг") == BASE_KORELICHI
    assert account_base_key("") is None
    assert account_base_key("ООО Чужое") is None


def test_statement_purpose_survives_split_header(qt_app):
    """v0.56.0: перенос строки внутри «Назначение платежа:» не теряет его.

    Разные версии pypdf разбивают текст по-разному: в 5.9 «Назначение» и
    «платежа: …» печатаются на разных строках. Без склейки назначение
    пропадало, а по нему определяются выручка наличными и эквайринг.
    """

    from oplata.statement import extract_text, parse_statement_text

    text = extract_text(SAMPLE_STATEMENT)
    split = re.sub(r"Назначение\s+платежа:", "Назначение\n платежа:", text)
    assert split != text  # в образце есть что разрывать
    statement = parse_statement_text(split)
    assert any(row.is_cash for row in statement.rows)
    assert any(row.is_cards for row in statement.rows)


def test_statement_notes_column_shows_where_row_goes(qt_app, store):
    """v0.56.0: столбец «Примечание» — куда строка попадёт при разнесении."""

    from oplata.models import Counterparty
    from oplata.statement import parse_statement
    from oplata.dialogs.statement_dialog import StatementDialog

    statement = parse_statement(SAMPLE_STATEMENT)
    unp_income = next(
        row.unp for row in statement.rows
        if row.is_incoming and not row.is_cards and not row.is_cash
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store)
    dialog.show()
    qt_app.processEvents()
    headers = [
        dialog.table.horizontalHeaderItem(column).text()
        for column in range(dialog.table.columnCount())
    ]
    assert headers[-1] == "Примечание"  # последняя колонка, после сумм

    notes = [
        dialog.table.item(row, headers.index("Примечание")).text()
        for row in range(len(statement.rows))
    ]
    # v0.63.0: примечание строки показывает и комиссию банка, поэтому
    # ищем вхождение, а не точное равенство.
    assert any("Поступление по картам" in note for note in notes)
    assert any("Поступление наличными" in note for note in notes)
    # Покупателя с таким УНП в базе нет — строка ждёт контрагента.
    assert any("не найден" in note for note in notes)

    # Появился покупатель — примечание пересчиталось (кнопка «Обновить»).
    store.data.customers.append(
        Counterparty(name="Новый покупатель", unp=unp_income)
    )
    dialog._refresh_notes()
    notes = [
        dialog.table.item(row, headers.index("Примечание")).text()
        for row in range(len(statement.rows))
    ]
    assert f"Покупатель: Новый покупатель" in notes
    dialog.close()


def test_distribution_writes_rows_on_operation_date(qt_app, store):
    """v0.56.0: разнос идёт на дату фактической операции, по УНП."""

    from oplata.models import (
        CARD_SOURCE,
        CASH_SOURCE,
        KIND_PAYMENT,
        Counterparty,
        OtherPayment,
        OUTGOING,
    )
    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import distribute

    customer = Counterparty(name="Покупатель", unp="111111111")
    supplier = Counterparty(name="Поставщик", unp="222222222")
    store.data.customers.append(customer)
    store.data.suppliers.append(supplier)
    store.data.other_payments.append(
        OtherPayment(name="Налоги", unp="333333333", direction=OUTGOING)
    )

    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="100325912", name="БАНК",
                incoming=100, purpose="Перечисление денежных средств "
                "по операциям с платежными карточками",
            ),
            StatementRow(
                date="06.11.2026", unp="100325912", name="БАНК",
                incoming=200, purpose="ВЫРУЧКА ОТ РЕАЛИЗАЦИИ за 06.11",
            ),
            StatementRow(
                date="07.11.2026", unp="111111111", name="Покупатель",
                incoming=300,
            ),
            StatementRow(
                date="08.11.2026", unp="222222222", name="Поставщик",
                outgoing=400,
            ),
            StatementRow(
                date="09.11.2026", unp="333333333", name="Налоговая",
                outgoing=500,
            ),
            StatementRow(
                date="10.11.2026", unp="444444444", name="Неизвестный",
                incoming=600,
            ),
        ]
    )
    result = distribute(statement, store.data)

    # Суммы легли на даты операций, а не на дату загрузки файла.
    assert [
        (receipt.source, receipt.date, str(receipt.amount))
        for receipt in store.data.cash_receipts
    ] == [(CARD_SOURCE, "2026-11-05", "100.00"),
          (CASH_SOURCE, "2026-11-06", "200.00")]
    # Деньги покупателя и оплата поставщику — операции «Оплата»
    # своих дней (v0.58.0: поступление от покупателя — его оплата).
    assert [
        (operation.counterparty_id, operation.kind, operation.date,
         str(operation.amount))
        for operation in store.data.operations
    ] == [
        (customer.id, KIND_PAYMENT, "2026-11-07", "300.00"),
        (supplier.id, KIND_PAYMENT, "2026-11-08", "400.00"),
    ]
    assert [
        (payment.name, payment.date, str(payment.amount))
        for payment in store.data.other_payments
        if payment.date  # карточки видов (без даты) не считаем
    ] == [("Прочие поступления", "2026-11-10", "600.00"),
          ("Налоги", "2026-11-09", "500.00")]
    assert result.applied_count == 6


def test_distribution_skips_already_imported_rows(qt_app, store):
    """v0.56.0: повторная загрузка файла не задваивает суммы."""

    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import distribute

    rows = [
        StatementRow(
            date="05.11.2026", unp="100325912", name="БАНК", incoming=100,
            purpose="Перечисление денежных средств по операциям "
            "с платежными карточками",
        )
    ]
    first = distribute(Statement(rows=list(rows)), store.data)
    assert first.applied_count == 1

    second = distribute(Statement(rows=list(rows)), store.data)
    assert second.applied_count == 0
    assert second.already  # строка отмечена как уже разнесённая
    assert len(store.data.cash_receipts) == 1
    # Реестр разнесённых строк переживает сохранение и чтение файла.
    assert store.data.to_dict()["imported_statement_rows"]


def _two_row_statement():
    """Выписка из двух строк: карты и поступление от покупателя."""

    from oplata.statement import Statement, StatementRow

    return Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="100325912", name="БАНК",
                incoming=100,
                purpose="Перечисление денежных средств по операциям "
                "с платежными карточками",
            ),
            StatementRow(
                date="07.11.2026", unp="111111111", name="Покупатель",
                incoming=300,
            ),
        ]
    )


def test_distribution_redo_replaces_previous_writes(qt_app, store):
    """v0.59.0: «Переразнести» снимает прежние записи и пишет заново.

    Без переразноса повторная загрузка файла не писала ничего и молча
    выходила — пользователь думал, что разнос сломался.
    """

    from oplata.models import CARD_SOURCE, Counterparty, KIND_PAYMENT
    from oplata.statement_match import distribute

    store.data.customers.append(
        Counterparty(name="Покупатель", unp="111111111")
    )
    first = distribute(_two_row_statement(), store.data)
    assert first.applied_count == 2

    redo = distribute(_two_row_statement(), store.data, redo=True)
    assert redo.applied_count == 2
    assert redo.replaced  # прежние записи сняты, а не удвоены
    # Суммы не задвоились: по картам за день по-прежнему одна запись.
    assert [
        (receipt.source, receipt.date, str(receipt.amount))
        for receipt in store.data.cash_receipts
        if receipt.source == CARD_SOURCE
    ] == [(CARD_SOURCE, "2026-11-05", "100.00")]
    assert [
        (operation.kind, str(operation.amount))
        for operation in store.data.operations
    ] == [(KIND_PAYMENT, "300.00")]
    # Реестр помнит, что именно записал разнос.
    assert store.data.to_dict()["imported_statement_writes"]


def test_distribution_redo_overwrites_manually_edited_amount(qt_app, store):
    """v0.67.0: сумму дня правили вручную — переразнос пишет поверх.

    Прежде (v0.59.0) в этом случае запись не снималась, а новая сумма
    для того же получателя и дня и вовсе не писалась. Человек стирал
    неверные суммы руками, нажимал «переразнести» — и не получал
    ничего: программа принимала его же правку за «чужой ввод, который
    не смеем трогать».
    """

    from oplata.models import CARD_SOURCE, Counterparty
    from oplata.statement_match import distribute

    store.data.customers.append(
        Counterparty(name="Покупатель", unp="111111111")
    )
    distribute(_two_row_statement(), store.data)
    for receipt in store.data.cash_receipts:
        if receipt.source == CARD_SOURCE:
            receipt.amount = "150.00"      # ручная правка

    redo = distribute(_two_row_statement(), store.data, redo=True)
    assert any("правили вручную" in line for line in redo.overwritten)
    # Сумма дня стала той, что в файле, — ручная правка перезаписана.
    assert [
        str(receipt.amount)
        for receipt in store.data.cash_receipts
        if receipt.source == CARD_SOURCE
    ] == ["100.00"]


def test_distribution_writes_over_amounts_erased_by_hand(qt_app, store):
    """v0.67.0: суммы стёрли руками — разнос пишет их заново.

    Живой случай: неверные миллионы удалили из блока «Оплата» руками,
    затем заново загрузили выписку. Программа видела в базе 0 вместо
    записанных ею же сумм, решала, что день правили вручную, — и не
    писала **ничего**: цифры не появлялись в таблице вовсе.
    """

    from decimal import Decimal

    from oplata.models import CARD_SOURCE, Counterparty, KIND_PAYMENT
    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import distribute

    store.data.suppliers.append(
        Counterparty(name="ООО \"ОМА\"", unp="100005713")
    )
    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="100005713", name="ООО \"ОМА\"",
                outgoing=Decimal("300.00"),
            ),
            StatementRow(
                date="05.11.2026", unp="100325912", name="БАНК",
                incoming=Decimal("100.00"),
                purpose="Перечисление денежных средств по операциям "
                "с платежными карточками",
            ),
        ]
    )
    distribute(statement, store.data)
    assert store.data.operations
    assert store.data.cash_receipts

    # Стираем всё, что записал разнос: как будто почистили руками.
    store.data.operations.clear()
    store.data.cash_receipts.clear()

    again = distribute(statement, store.data, redo=True)
    assert again.applied_count == 2
    assert any("правили вручную" in line for line in again.overwritten)
    assert [
        (operation.kind, str(operation.amount))
        for operation in store.data.operations
    ] == [(KIND_PAYMENT, "300.00")]
    assert [
        str(receipt.amount)
        for receipt in store.data.cash_receipts
        if receipt.source == CARD_SOURCE
    ] == ["100.00"]


def test_distribution_writes_over_erased_amount_without_redo(qt_app, store):
    """v0.67.0: то же самое без «переразнести» — файл прочитан заново.

    Суммы строк изменились (исправлен разбор файла), поэтому в реестре
    эти строки **не узнаются** по старому ключу: срабатывает мягкий
    ключ «дата · УНП · направление» (v0.65.0). Прежде и здесь ручная
    правка запирала день: новая сумма не писалась.
    """

    from decimal import Decimal

    from oplata.models import Counterparty
    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import distribute

    store.data.suppliers.append(
        Counterparty(name="ООО \"ОМА\"", unp="100005713")
    )

    def statement_with(amount):
        return Statement(
            rows=[
                StatementRow(
                    date="05.11.2026", unp="100005713", name="ООО \"ОМА\"",
                    outgoing=Decimal(amount),
                )
            ]
        )

    distribute(statement_with("101300"), store.data)
    assert store.data.operations
    # Стираем записанное руками.
    store.data.operations.clear()

    # Файл прочитан заново и с другими суммами: redo=False, но строка
    # прежняя — по мягкому ключу.
    result = distribute(statement_with("300"), store.data)
    assert result.restated
    assert [str(op.amount) for op in store.data.operations] == ["300.00"]
    assert result.applied_count == 1


def test_distribution_redo_finds_stale_realizations(qt_app, store):
    """v0.59.0: находим «реализации», созданные версиями 0.56–0.57."""

    from oplata.models import Counterparty, add_realization
    from oplata.statement_match import (
        find_stale_realizations,
        remove_operations,
    )

    buyer = Counterparty(name="Покупатель", unp="111111111")
    store.data.customers.append(buyer)
    add_realization(store.data, buyer.id, "2026-11-07", Decimal("300"))
    # Чужая реализация: та же дата, но другая сумма — трогать не надо.
    add_realization(store.data, buyer.id, "2026-11-07", Decimal("999"))

    stale = find_stale_realizations(store.data, _two_row_statement())
    assert [str(operation.amount) for operation in stale] == ["300.00"]
    remove_operations(store.data, stale)
    assert [str(operation.amount) for operation in store.data.operations] == [
        "999.00"
    ]


def test_distribution_redo_warns_about_old_registry(qt_app, store):
    """v0.59.0: у старого реестра нет подробностей — предупреждаем."""

    from oplata.models import Counterparty
    from oplata.statement_match import distribute

    store.data.customers.append(
        Counterparty(name="Покупатель", unp="111111111")
    )
    statement = _two_row_statement()
    distribute(statement, store.data)
    # База, которую разносила версия до 0.59.0: строки помечены,
    # подробных записей нет.
    store.data.imported_statement_writes = []

    redo = distribute(statement, store.data, redo=True)
    assert any("проверьте дни" in line for line in redo.warnings)


def _press_message_button(monkeypatch, label, calls=None):
    """«Нажать» кнопку модального сообщения (v0.59.0)."""

    def fake_exec(self):
        button = next(b for b in self.buttons() if b.text() == label)
        self.clickedButton = lambda: button     # type: ignore[method-assign]
        if calls is not None:
            calls.append(self.windowTitle())

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


def test_statement_dialog_asks_before_redo(qt_app, store, monkeypatch):
    """v0.59.0: повторный файл — вопрос, а не молчаливый выход."""

    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.models import Counterparty
    from oplata.statement_match import distribute

    store.data.customers.append(
        Counterparty(name="Покупатель", unp="111111111")
    )
    statement = _two_row_statement()

    calls: list[str] = []
    fresh = StatementDialog(statement, "выписка.pdf", None, store=store)
    _press_message_button(monkeypatch, "Переразнести", calls)
    assert fresh._ask_about_repeat() is False   # новый файл — без вопросов
    assert not calls

    distribute(statement, store.data)
    again = StatementDialog(statement, "выписка.pdf", None, store=store)
    assert again._ask_about_repeat() is True    # нажали «Переразнести»
    assert calls == ["Выписка уже разносилась"]

    _press_message_button(monkeypatch, "Отмена", calls)
    assert again._ask_about_repeat() is None    # нажали «Отмена»


def test_statement_dialog_reports_when_nothing_new(qt_app, store, monkeypatch):
    """v0.59.0: повторный разнос объясняет, почему писать нечего.

    Раньше программа молча закрывала окно, и это выглядело как
    «выписка не разносится».
    """

    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.models import Counterparty
    from oplata.statement_match import distribute

    store.data.customers.append(
        Counterparty(name="Покупатель", unp="111111111")
    )
    statement = _two_row_statement()
    distribute(statement, store.data)

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args: shown.append(args[-1])),
    )
    # v0.63.0: разнос делается в самой таблице выписки — второго окна
    # больше нет, кнопка «Разнести» пишет сразу.
    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    dialog = StatementDialog(
        statement, "выписка.pdf", None, bank_unp="100325912", store=store
    )
    dialog._continue()
    assert shown and "Новых данных нет" in shown[-1]


def test_counterparty_card_saves_note(qt_app, store):
    """v0.60.0: примечание контрагента сохраняется карточкой."""

    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    card = CounterpartyCardDialog(store, "supplier")
    card.name_edit.setText("Тестовый поставщик")
    card.unp_edit.setText("123456789")
    card.note_edit.setText("оплату присылает со второго счёта")
    card._save_and_close()

    item = next(
        one for one in store.data.suppliers if one.name == "Тестовый поставщик"
    )
    assert item.unp == "123456789"
    assert item.note == "оплату присылает со второго счёта"
    # Примечание переживает сохранение и чтение файла.
    saved = next(
        one
        for one in DataStore(store.path).data.suppliers
        if one.name == "Тестовый поставщик"
    )
    assert saved.note == item.note


def test_two_counterparties_may_share_unp(qt_app, store):
    """v0.60.0: одинаковый УНП у двух контрагентов — не ошибка.

    Одна организация бывает и поставщиком, и покупателем; карточки
    различают примечанием.
    """

    from oplata.dialogs.counterparty_card import CounterpartyCardDialog

    for name, note in (
        ("Оптовик", "поставка товара"),
        ("Оптовик-покупатель", "покупает упаковку"),
    ):
        card = CounterpartyCardDialog(store, "customer")
        card.name_edit.setText(name)
        card.unp_edit.setText("123456789")     # тот же УНП
        card.note_edit.setText(note)
        card._add()
        card.close()

    unps = [item.unp for item in store.data.customers]
    assert unps == ["123456789", "123456789"]
    assert [item.note for item in store.data.customers] == [
        "поставка товара",
        "покупает упаковку",
    ]


from oplata.storage import DataStore


def test_payment_note_shows_in_block_table(qt_app, store):
    """v0.60.0: примечание вида оплаты — своей колонкой в таблице."""

    from datetime import date

    from oplata.app import MainWindow
    from oplata.models import add_other_payment
    from oplata.views.receipts_view import COL_NAME, COL_NOTE, COL_VAT

    add_other_payment(
        store.data, "Аренда", "2026-10-27", note="второй договор"
    )
    # Второй вид с тем же УНП, но другим примечанием (v0.60.0).
    add_other_payment(
        store.data,
        "Аренда гаража",
        "2026-10-27",
        unp="100325912",
        note="гараж под офисом",
    )
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))

    block = view.outgoing
    headers = [
        block.table.horizontalHeaderItem(column).text()
        for column in range(block.table.columnCount())
    ]
    # Примечание стоит между названием и НДС.
    assert headers[:3] == ["", "Примечание", "НДС"]

    def note_of(name: str) -> str:
        row = block.row_keys.index(f"other:{name}")
        item = block.table.item(row, COL_NOTE)
        return item.text() if item is not None else ""

    assert note_of("Аренда") == "второй договор"
    assert note_of("Аренда гаража") == "гараж под офисом"
    # Колонка примечания только для чтения: правится карточкой.
    for name in ("Аренда", "Аренда гаража"):
        row = block.row_keys.index(f"other:{name}")
        assert not (
            block.table.item(row, COL_NOTE).flags()
            & Qt.ItemFlag.ItemIsEditable
        )
    # v0.70.0: заглавными, как и все названия строк блока.
    assert block.table.item(0, COL_NAME).text() == "ПОСТАВЩИКИ"
    assert COL_NOTE < COL_VAT
    window.close()


def test_payment_card_saves_note(qt_app, store, monkeypatch):
    """v0.60.0: примечание вида оплаты приходит из карточки."""

    from datetime import date

    from oplata.app import MainWindow

    patch_other_payment_card(
        monkeypatch, names=("Вид с примечанием",),
        unp="100325912", note="комиссия за эквайринг",
    )
    window = MainWindow(store)
    window.show()
    qt_app.processEvents()
    view = window.receipts_view
    view.set_date(date(2026, 10, 27))
    view.add_other_row()
    qt_app.processEvents()

    payment = next(
        one
        for one in store.data.other_payments
        if one.name == "Вид с примечанием"
    )
    assert payment.unp == "100325912"
    assert payment.note == "комиссия за эквайринг"
    # Примечание переживает сохранение и чтение файла.
    saved = next(
        one
        for one in DataStore(store.path).data.other_payments
        if one.name == "Вид с примечанием"
    )
    assert saved.note == payment.note
    # И показываетcя в своей колонке таблицы блока.
    view.refresh()
    qt_app.processEvents()
    row = view.outgoing.row_keys.index("other:Вид с примечанием")
    from oplata.views.receipts_view import COL_NOTE

    assert view.outgoing.table.item(row, COL_NOTE).text() == (
        "комиссия за эквайринг"
    )
    window.close()


def test_counterparty_note_shows_in_week_table_tooltip(qt_app, store):
    """v0.60.0: примечание контрагента видно в подсказке строки."""

    from oplata.models import Counterparty
    from oplata.views.week_view import WeekView

    store.data.suppliers.append(
        Counterparty(
            name="Оптовик", unp="123456789",
            note="оплату присылает со второго счёта",
        )
    )
    view = WeekView(store, "supplier", lambda *_a: None)
    view.show()
    qt_app.processEvents()
    rows = [
        row
        for row in range(view.table.rowCount())
        if view.table.item(row, 0) is not None
        and view.table.item(row, 0).text() == "Оптовик"
    ]
    assert rows, "строка поставщика не найдена"
    item = view.table.item(rows[0], 0)
    assert item is not None and item.text() == "Оптовик"
    assert "Примечание: оплату присылает со второго счёта" in item.toolTip()
    view.close()


def test_long_partner_name_wraps_to_two_lines(qt_app, store):
    """v0.60.0: длинное наименование переносится, а не обрезается.

    Расширять колонку названия некуда — шесть дней по три колонки
    съедают всю ширину окна, а сужать итоговые ниже 100 px нельзя
    (подпись «Просрочено» занимает 91 px). Поэтому строка с длинным
    наименованием становится выше.
    """

    from oplata.models import Counterparty
    from oplata.views.week_view import (
        FIRST_DATA_ROW,
        NAME_ROW_HEIGHT,
        WeekView,
    )

    store.data.suppliers.append(Counterparty(name='ЧПУП "МАВ"', unp="1"))
    store.data.suppliers.append(
        Counterparty(name='ООО "КУЛЬТБЫТХОЗТОРГПЛЮС"', unp="2")
    )
    view = WeekView(store, "supplier", lambda *_a: None)
    view.resize(1366, 520)
    view.show()
    qt_app.processEvents()

    def row_of(name: str) -> int:
        for row in range(FIRST_DATA_ROW, view.table.rowCount()):
            item = view.table.item(row, 0)
            if item is not None and item.text() == name:
                return row
        raise AssertionError(f"нет строки «{name}»")

    short = row_of('ЧПУП "МАВ"')
    long_row = row_of('ООО "КУЛЬТБЫТХОЗТОРГПЛЮС"')
    # Короткое наименование — одна строка, длинное занимает две.
    assert view.table.rowHeight(short) == NAME_ROW_HEIGHT
    assert view.table.rowHeight(long_row) > NAME_ROW_HEIGHT
    assert view.table.wordWrap() is True
    view.close()


def _ambiguous_statement(unp: str = "111222333", incoming: bool = False):
    """Строка выписки с заданным УНП (для тестов выбора карточки)."""

    from decimal import Decimal

    from oplata.statement import Statement, StatementRow

    kwargs = (
        {"incoming": Decimal("300")} if incoming
        else {"outgoing": Decimal("1500")}
    )
    return Statement(
        rows=[
            StatementRow(
                date="10.11.2026", unp=unp, name="Организация", **kwargs
            )
        ]
    )


def test_classify_asks_to_choose_when_unp_has_several_cards(qt_app, store):
    """v0.61.0: несколько карточек с одним УНП — выбор, а не догадка."""

    from oplata.models import Counterparty, add_other_payment
    from oplata.statement_match import (
        TARGET_CHOOSE,
        TARGET_CUSTOMERS,
        TARGET_OTHER_PAYMENT,
        TARGET_SUPPLIERS,
        classify,
        row_is_blocked,
    )

    first = Counterparty(name="Опт поставка", unp="111222333", note="товар")
    second = Counterparty(name="Опт аренда", unp="111222333", note="аренда")
    store.data.suppliers += [first, second]
    buyer_one = Counterparty(name="Покупатель раз", unp="444555666",
                             note="розница")
    buyer_two = Counterparty(name="Покупатель два", unp="444555666",
                             note="опт")
    store.data.customers += [buyer_one, buyer_two]
    add_other_payment(store.data, "Комиссия эквайринг", "2026-11-10",
                      unp="999888777", note="эквайринг")
    add_other_payment(store.data, "Обслуживание счёта", "2026-11-10",
                      unp="999888777", note="обслуживание")

    def row_for(unp: str, incoming: bool = False):
        return _ambiguous_statement(unp, incoming).rows[0]

    # Поставщики: два кандидата, оба в подписи с УНП и примечанием.
    match = classify(row_for("111222333"), store.data)
    assert match.target == TARGET_CHOOSE
    assert [option[0] for option in match.options] == [
        TARGET_SUPPLIERS,
        TARGET_SUPPLIERS,
    ]
    labels = [option[3] for option in match.options]
    assert labels == [
        "Опт поставка · УНП 111222333 · товар",
        "Опт аренда · УНП 111222333 · аренда",
    ]
    assert "Уточните" in match.note
    # Пока получатель не выбран, строку не разносим.
    assert row_is_blocked(match, type(match)("", ""))

    # Покупатели — так же.
    match = classify(row_for("444555666", incoming=True), store.data)
    assert match.target == TARGET_CHOOSE
    assert [option[0] for option in match.options] == [
        TARGET_CUSTOMERS,
        TARGET_CUSTOMERS,
    ]
    assert "розница" in match.options[0][3]

    # Виды оплаты с одинаковым УНП — тоже выбор.
    match = classify(row_for("999888777"), store.data)
    assert match.target == TARGET_CHOOSE
    assert [option[2] for option in match.options] == [
        "Комиссия эквайринг",
        "Обслуживание счёта",
    ]
    assert match.options[0][0] == TARGET_OTHER_PAYMENT


def test_ambiguous_row_is_written_after_choice(qt_app, store):
    """v0.61.0: после выбора кандидата строка пишется именно ему."""

    from oplata.models import Counterparty
    from oplata.statement_match import (
        TARGET_CHOOSE,
        classify,
        classify_commission,
        distribute,
    )

    first = Counterparty(name="Опт поставка", unp="111222333", note="товар")
    second = Counterparty(name="Опт аренда", unp="111222333", note="аренда")
    store.data.suppliers += [first, second]

    statement = _ambiguous_statement()
    matches = [
        classify(row, store.data) for row in statement.rows
    ]
    commissions = [
        classify_commission(row, store.data) for row in statement.rows
    ]
    assert matches[0].target == TARGET_CHOOSE
    # Пока не выбрали — ничего не пишем.
    assert distribute(statement, store.data, matches, commissions).applied == []
    assert not store.data.operations

    # Выбираем второго поставщика — его опции и подставляем.
    _target, counterparty_id, payment_name, _label = matches[0].options[1]
    matches[0].target = _target
    matches[0].counterparty_id = counterparty_id
    matches[0].payment_name = payment_name
    matches[0].options = []
    result = distribute(
        statement, store.data, matches, commissions
    )
    assert result.applied_count == 1
    assert [operation.counterparty_id for operation in store.data.operations] == [
        second.id
    ]


def test_statement_table_offers_all_candidates(qt_app, store):
    """v0.61.0/0.63.0: список «Куда» содержит всех кандидатов.

    Список живёт в самой таблице выписки (v0.63.0): второе окно для
    разноса больше не открывается, поэтому выбор делается здесь.
    """

    from oplata.models import Counterparty
    from oplata.dialogs.statement_dialog import COL_NOTE, StatementDialog
    from oplata.statement_match import classify, classify_commission

    first = Counterparty(name="Опт поставка", unp="111222333", note="товар")
    second = Counterparty(name="Опт аренда", unp="111222333", note="аренда")
    store.data.suppliers += [first, second]
    statement = _ambiguous_statement()

    dialog = StatementDialog(statement, "выписка.pdf", store=store)
    dialog.show()
    qt_app.processEvents()
    dialog.matches = [classify(row, store.data) for row in statement.rows]
    dialog.commissions = [
        classify_commission(row, store.data) for row in statement.rows
    ]
    dialog._fill_table()

    combo = dialog._target_combo(0)
    texts = [combo.itemText(index) for index in range(combo.count())]
    assert "Опт поставка · УНП 111222333 · товар" in texts
    assert "Опт аренда · УНП 111222333 · аренда" in texts
    # Первым стоит «выберите» — программа не угадывает получателя.
    assert combo.currentText().startswith("— выберите")
    # Примечание строки объясняет, что нужно выбрать.
    assert "Уточните" in dialog.table.item(0, COL_NOTE).text()

    # Выбор кандидата подставляет поставщика и его подпись.
    for index in range(combo.count()):
        if "аренда" in combo.itemText(index):
            combo.setCurrentIndex(index)
            break
    match = dialog.matches[0]
    assert match.counterparty_id == second.id
    assert match.note == "Поставщик: Опт аренда"
    assert dialog.table.item(0, COL_NOTE).text() == "Поставщик: Опт аренда"
    dialog.close()


def test_single_card_by_unp_is_still_classified_straight(qt_app, store):
    """v0.61.0: один кандидат — как прежде, выбора не требуется."""

    from oplata.models import Counterparty
    from oplata.statement_match import TARGET_SUPPLIERS, classify

    store.data.suppliers.append(
        Counterparty(name="Единственный", unp="111222333", note="товар")
    )
    match = classify(_ambiguous_statement().rows[0], store.data)
    assert match.target == TARGET_SUPPLIERS
    assert not match.options
    assert match.note == "Поставщик: Единственный"


def test_week_lead_columns_are_narrower_and_smaller(qt_app, store):
    """v0.56.0: итоговые колонки уже, шрифт в их ячейках меньше.

    Сравниваем с величинами модуля, а не с измеренным шрифтом колонок
    дней: тот подбирается по ширине окна (``_fit_amount_font``) и на
    другой машине может оказаться любым.
    """

    from oplata import styles
    from oplata.models import Counterparty
    from oplata.views import week_view
    from oplata.views.week_view import WeekView

    # Было: ширина 118/106 и шрифт fs(11) — как у всей таблицы.
    assert week_view.LEAD_WIDTH_BASE == 110
    assert week_view.LEAD_WIDTH_MIN == 100
    assert week_view.LEAD_FONT_SIZE == styles.fs(10)
    assert week_view.LEAD_FONT_SIZE < styles.fs(11)

    store.data.suppliers.append(Counterparty(name="Поставщик", unp="1"))
    store.data.customers.append(Counterparty(name="Покупатель", unp="2"))
    for kind in ("supplier", "customer"):
        view = WeekView(store, kind, lambda *_a: None)
        view.show()
        qt_app.processEvents()
        offset = 1 if kind == "supplier" else 0
        lead = view.table.columnWidth(1 + offset)
        lead_item = view.table.item(2, 1 + offset)
        assert lead_item is not None
        assert lead <= week_view.LEAD_WIDTH_BASE
        # Шрифт значения в итоговой колонке — тот же, что у её подписи.
        assert lead_item.font().pixelSize() == week_view.LEAD_FONT_SIZE
        view.close()


# --------------------------------------------------------------- v0.57.0
def test_statement_table_shows_base_amounts_before_writing(qt_app, store):
    """v0.57.0/0.63.0: колонка «Уже в базе» — в таблице выписки.

    Раньше колонка жила во втором окне разноса; с v0.63.0 она стоит
    рядом с колонкой «Куда» в самой выписке.
    """

    from oplata.models import CARD_SOURCE, CashReceipt, Counterparty
    from oplata.statement import Statement, StatementRow
    from oplata.dialogs.statement_dialog import (
        COL_BASE,
        COL_NAME,
        StatementDialog,
    )

    store.data.bank.unp = "100325912"
    store.data.customers.append(Counterparty(name="Покупатель", unp="1"))
    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="100325912", name="БАНК",
                incoming=1000, purpose="Перечисление денежных средств "
                "по операциям с платежными карточками",
            ),
            StatementRow(
                date="05.11.2026", unp="1", name="Покупатель", incoming=50,
            ),
        ]
    )
    # За этот день по картам уже лежит 300,00 — день будет заменён.
    store.data.cash_receipts.append(
        CashReceipt(source=CARD_SOURCE, date="2026-11-05", amount="300.00")
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    cards = dialog.table.item(0, COL_BASE).text()
    customers = dialog.table.item(1, COL_BASE).text()
    assert cards == "300,00 → 1 000,00"  # прежняя сумма заменяется
    assert customers == "0,00 → 50,00"
    assert dialog.table.item(0, COL_NAME).text() == "БАНК"
    dialog.close()


def test_statement_table_creates_payment_card_for_row(
    qt_app, store, monkeypatch
):
    """v0.63.0: вид оплаты создаётся карточкой из таблицы выписки.

    То же, что раньше делалось во втором окне разноса: УНП и название
    берутся из строки выписки, примечание — из назначения платежа.
    """

    from PySide6.QtWidgets import QDialog
    from oplata.models import OUTGOING, other_payment_names
    from oplata.statement import Statement, StatementRow
    from oplata.dialogs.statement_dialog import (
        COL_NOTE,
        StatementDialog,
        NEW_PAYMENT_CHOICE,
    )
    from oplata.statement_match import TARGET_NEW_PAYMENT, TARGET_OTHER_PAYMENT
    from oplata.views.receipts_view import OtherPaymentCardDialog

    monkeypatch.setattr(
        OtherPaymentCardDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="192775574",
                name='ООО "ТД КОМПЛЕКТ"', outgoing=Decimal("8408.53"),
            )
        ]
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    row_index = next(
        index
        for index, match in enumerate(dialog.matches)
        if match.target == TARGET_NEW_PAYMENT
    )
    row = row_index  # в таблице выписки строка = строка файла
    combo = dialog._target_combo(row)
    assert combo.currentText() == NEW_PAYMENT_CHOICE

    assert dialog._create_payment(row, row_index) is True
    # Карточка создана один раз, с УНП и названием из строки выписки.
    assert other_payment_names(store.data, OUTGOING) == ['ООО "ТД КОМПЛЕКТ"']
    payments = [
        payment
        for payment in store.data.other_payments
        if payment.unp == "192775574"
    ]
    assert len(payments) == 1
    assert payments[0].date == "2026-11-05"

    # Строка стала «Вид оплаты», сумма попала в план разноса.
    match = dialog.matches[row_index]
    assert match.target == TARGET_OTHER_PAYMENT
    assert match.payment_name == 'ООО "ТД КОМПЛЕКТ"'
    assert dialog.table.item(row, COL_NOTE).text() == (
        'Вид оплаты: ООО "ТД КОМПЛЕКТ"'
    )
    assert dialog._target_combo(row).currentText() == 'ООО "ТД КОМПЛЕКТ"'
    assert str(dialog.planned[next(iter(dialog.planned))]) == "8408.53"

    # И сумма уходит именно в созданный вид оплаты, той же датой.
    dialog._distribute()
    written = [
        payment
        for payment in store.data.other_payments
        if payment.date == "2026-11-05" and payment.amount
    ]
    assert [(item.name, str(item.amount)) for item in written] == [
        ('ООО "ТД КОМПЛЕКТ"', "8408.53")
    ]
    dialog.close()


# --------------------------------------------------------------- v0.58.0
def test_incoming_from_customer_is_payment_not_realization(qt_app, store):
    """v0.58.0: деньги от покупателя — оплата его долга, не реализация.

    Раньше выписка создавала «Реализацию», и долг покупателя от
    поступления только рос (замечание пользователя по файлу вм1.pdf).
    """

    from oplata.models import KIND_PAYMENT, KIND_SALE, Counterparty
    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import distribute

    customer = Counterparty(name="СПК ЖУХОВИЧИ", unp="500021189")
    store.data.customers.append(customer)
    statement = Statement(
        rows=[
            StatementRow(
                date="10.11.2026", unp="500021189", name="СПК ЖУХОВИЧИ",
                incoming=Decimal("314.76"),
            )
        ]
    )
    distribute(statement, store.data)

    assert [
        (op.kind, op.date, str(op.amount)) for op in store.data.operations
    ] == [(KIND_PAYMENT, "2026-11-10", "314.76")]
    assert not [
        op for op in store.data.operations if op.kind == KIND_SALE
    ]  # реализацию выписка не создаёт


def test_bank_row_is_distributed_to_payment_by_unp(qt_app, store):
    """v0.58.0: комиссия банка уходит в вид оплаты с УНП банка."""

    from oplata.models import OUTGOING, OtherPayment
    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import (
        TARGET_OTHER_PAYMENT,
        classify,
        distribute,
    )

    store.data.bank.unp = "100325912"  # карточка банка из «Конфигурации»
    store.data.other_payments.append(
        OtherPayment(name="Комиссия банка", unp="100325912",
                     direction=OUTGOING)
    )
    statement = Statement(
        rows=[
            StatementRow(
                date="10.11.2026", unp="100325912",
                name='ОАО "АСБ БЕЛАРУСБАНК"', outgoing=Decimal("44.05"),
                incoming=Decimal("2298.65"), commission=Decimal("44.05"),
                purpose="Перечисление денежных средств по операциям с "
                "платежными карточками согласно отчету(ам) N1. "
                "Комиссия банка - 44.05",
            ),
            StatementRow(
                date="10.11.2026", unp="100325912",
                name='ОАО "АСБ БЕЛАРУСБАНК"', outgoing=Decimal("5.80"),
                purpose="Вознаграждение банку за прием наличных денежных "
                "средств",
            ),
        ]
    )
    # Строка эквайринга — поступление по картам, комиссия — вид оплаты.
    notes = [
        classify(row, store.data, store.data.bank.unp).note
        for row in statement.rows
    ]
    assert notes[0] == "Поступление по картам"
    assert notes[1] == "Вид оплаты: Комиссия банка"

    distribute(statement, store.data)
    # Обе комиссии дня (44,05 и 5,80) легли в вид оплаты банка.
    assert [
        (payment.name, payment.date, str(payment.amount))
        for payment in store.data.other_payments
        if payment.date
    ] == [("Комиссия банка", "2026-11-10", "49.85")]
    # Поступление по картам — показуемая сумма: 2298,65 + 44,05.
    assert [
        (receipt.source, str(receipt.amount))
        for receipt in store.data.cash_receipts
    ] == [("card", "2342.70")]


def test_bank_row_without_payment_asks_for_card(qt_app, store):
    """v0.58.0: без вида оплаты строка банка ждёт карточку, а не пропуск."""

    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import TARGET_NEW_PAYMENT, classify

    store.data.bank.unp = "100325912"
    row = StatementRow(
        date="10.11.2026", unp="100325912", name='ОАО "АСБ БЕЛАРУСБАНК"',
        outgoing=Decimal("44.05"),
    )
    match = classify(row, store.data, store.data.bank.unp)
    assert match.target == TARGET_NEW_PAYMENT  # разносим, вида нет
    assert "банка" in match.note


# --------------------------------------------------------------- v0.62.0
def test_unp_split_by_line_break_is_read(qt_app):
    """v0.62.0: «УНП» и цифры на разных строках — УНП всё равно читается.

    В выписке за 05.11.2026 pypdf разорвал «БИК: AKBBBY2X УНП» и
    «: 100325912». УНП в операции оставался пустым, комиссию банка было
    некуда записать, и строка не разносилась целиком — 979,72 по картам
    и 13,17 комиссией оставались стоять.
    """

    from oplata.statement import parse_statement_text

    template = (
        "05.11.2026 1805 6 Счет: BY13 AKBB 3812 0000 0028 1000 0000\n"
        "БИК: AKBBBY2X %s\n"
        "0,00 979,72\n"
        'ОАО "АСБ БЕЛАРУСБАНК"\n'
        "Назначение платежа: Перечисление денежных средств по операциям с "
        "платежными карточками. Комиссия банка - 13.17\n"
        "Фактическая дата и время совершения операции: 05.11.2026 10:06:07\n"
    )
    # Три вида разрыва: после «УНП», после «УНП:» и без двоеточия вовсе.
    variants = (
        "УНП\n: 100325912",
        "УНП:\n100325912",
        "УНП\n 100325912",
    )
    for variant in variants:
        statement = parse_statement_text(template % variant)
        assert [row.unp for row in statement.rows] == ["100325912"], variant

    # Целая строка читается как прежде — склейка ничего не ломает.
    statement = parse_statement_text(template % "УНП: 100325912")
    assert [row.unp for row in statement.rows] == ["100325912"]


def test_bank_row_with_split_unp_is_distributed(qt_app, store):
    """v0.62.0: строка банка с разорванным УНП разносится обеими суммами.

    Строка из выписки за 05.11.2026: поступление 979,72 по картам и
    комиссия банка 13,17 — комиссия идёт в вид оплаты с УНП банка.
    """

    from decimal import Decimal

    from oplata.models import add_other_payment
    from oplata.statement import parse_statement_text
    from oplata.statement_match import distribute, row_is_blocked

    store.data.bank.unp = "100325912"
    add_other_payment(
        store.data, "Комиссия банка", "2026-11-05", unp="100325912"
    )
    text = (
        "ВЫПИСКА ИЗ ЛИЦЕВОГО СЧЕТА ЗА 05.11.2026\n"
        "05.11.2026 1805 6 Счет: BY13 AKBB 3812 0000 0028 1000 0000\n"
        "БИК: AKBBBY2X УНП\n"
        ": 100325912\n"
        "0,00 979,72\n"
        'ОАО "АСБ БЕЛАРУСБАНК"\n'
        "Назначение платежа: Перечисление денежных средств по операциям с "
        "платежными карточками согласно отчету(ам) N000000322 от "
        "05.11.2026. Комиссия банка - 13.17\n"
        "Фактическая дата и время совершения операции: 05.11.2026 10:06:07\n"
    )
    statement = parse_statement_text(text)
    row = statement.rows[0]
    assert row.unp == "100325912"  # УНП прочитан, строка не стоит
    assert row.commission == Decimal("13.17")

    from oplata.statement_match import classify, classify_commission

    commission = classify_commission(row, store.data, store.data.bank.unp)
    assert row_is_blocked(
        classify(row, store.data, store.data.bank.unp), commission
    ) is False

    distribute(statement, store.data)
    # Комиссия легла в вид оплаты банка, поступление — по картам
    # (979,72 + 13,17 = 992,89: комиссия проходит с двух сторон).
    assert [
        (payment.name, str(payment.amount))
        for payment in store.data.other_payments
        if payment.date
    ] == [("Комиссия банка", "13.17")]
    assert [
        (receipt.source, str(receipt.amount))
        for receipt in store.data.cash_receipts
    ] == [("card", "992.89")]


def test_commission_without_payment_note_names_amount_and_unp(qt_app, store):
    """v0.62.0: без вида оплаты примечание говорит, сколько и по кому."""

    from decimal import Decimal

    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import classify_commission

    def commission_of(unp: str) -> str:
        row = StatementRow(
            date="05.11.2026", unp=unp, name='ОАО "АСБ БЕЛАРУСБАНК"',
            incoming=Decimal("979.72"), commission=Decimal("13.17"),
        )
        return classify_commission(row, store.data, "100325912").note

    # Вид оплаты не заведён: видно и сумму комиссии, и УНП, по которому
    # её искали, — понятно, какую карточку создать.
    assert commission_of("100325912") == (
        "Комиссия 13,17: нужен вид оплаты (УНП 100325912)"
    )
    # Пустой УНП тоже объясняем: молчаливого «нужен вид оплаты» мало.
    assert commission_of("") == (
        "Комиссия 13,17: нужен вид оплаты (УНП в строке не указан)"
    )


# --------------------------------------------------------------- v0.63.0
def test_payment_card_for_second_row_gets_free_name(
    qt_app, store, monkeypatch
):
    """v0.63.0: второй вид оплаты той же организации создаётся.

    У ГУ МФ по Гродненской области один УНП и разные назначения
    платежей (подоходный налог, страховые взносы). Раньше вторую
    карточку нельзя было сохранить: «Вид «…» уже есть в этом блоке»
    — и разнос вставал. Теперь предлагается свободное имя, а
    примечание берётся из назначения платежа.
    """

    from PySide6.QtWidgets import QDialog
    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import free_payment_name, purpose_note

    # Имя, которого нет в блоке: «ГУ МФ …», затем «ГУ МФ … (2)».
    assert free_payment_name(set(), "ГУ МФ") == "ГУ МФ"
    assert free_payment_name({"гу мф"}, "ГУ МФ") == "ГУ МФ (2)"
    assert free_payment_name({"гу мф", "гу мф (2)"}, "ГУ МФ") == "ГУ МФ (3)"

    # Примечание — смысл назначения платежа, без реквизитов из хвоста.
    assert purpose_note(
        "ПОДОХОДНЫЙ НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ ЗА ПЕРВУЮ ПОЛОВИНУ "
        "ОКТЯБРЯ 2026Г. ПО СРОКУ ПЛАТЕЖА 21.10.26Г. УНП ФБ: 500"
    ) == "ПОДОХОДНЫЙ НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ"
    assert purpose_note(
        "ОПЛАТА СТРАХОВЫХ ВЗНОСОВ ЗА 4 КВАРТАЛ 2026Г. "
        "ПО СРОКУ ПЛАТЕЖА 21.10.26Г."
    ) == "ОПЛАТА СТРАХОВЫХ ВЗНОСОВ"
    assert purpose_note("") == ""

    def row(purpose: str, amount: str):
        return StatementRow(
            date="21.10.2026", unp="500563252",
            name="ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ",
            outgoing=Decimal(amount), purpose=purpose,
        )

    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.views.receipts_view import OtherPaymentCardDialog

    monkeypatch.setattr(
        OtherPaymentCardDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    statement = Statement(
        rows=[
            row("ПОДОХОДНЫЙ НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ ЗА ОКТЯБРЬ", "101300"),
            row("ОПЛАТА СТРАХОВЫХ ВЗНОСОВ ЗА 4 КВАРТАЛ", "3511800"),
        ]
    )
    dialog = StatementDialog(statement, "в7.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    assert dialog._create_payment(0, 0) is True
    assert dialog._create_payment(1, 1) is True

    # Оба вида с одним УНП — различаются примечанием.
    payments = [
        payment
        for payment in store.data.other_payments
        if payment.unp == "500563252"
    ]
    assert [(item.name, item.note) for item in payments] == [
        (
            "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ",
            "ПОДОХОДНЫЙ НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ",
        ),
        ("ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)", "ОПЛАТА СТРАХОВЫХ ВЗНОСОВ"),
    ]
    # Сразу после создания каждая строка пишет в «свой» вид: вид
    # подставлен в строку, разнос не стоит.
    assert [match.payment_name for match in dialog.matches] == [
        "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ",
        "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)",
    ]
    # После пересчёта (кнопка «Обновить») УНП стал неоднозначным: по
    # правилу v0.61.0 программа не угадывает, получателя выбирают
    # списком — кандидаты подписаны примечанием.
    dialog._recalculate()
    assert [match.target for match in dialog.matches] == ["choose", "choose"]
    labels = [option[3] for option in dialog.matches[0].options]
    assert labels == [
        "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ · УНП 500563252 · "
        "ПОДОХОДНЫЙ НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ",
        "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2) · УНП 500563252 · "
        "ОПЛАТА СТРАХОВЫХ ВЗНОСОВ",
    ]
    dialog.close()


def test_statement_table_has_target_column_and_distributes(
    qt_app, store, monkeypatch
):
    """v0.63.0: колонка «Куда» — в самой таблице выписки, разнос здесь же.

    Раньше кнопка «Разнести» открывала второе окно; теперь назначение
    выбирается в первой таблице (после «Поступления»), а вторая таблица
    не открывается вовсе.
    """

    from PySide6.QtWidgets import QMessageBox
    from oplata.models import Counterparty
    from oplata.dialogs.statement_dialog import (
        COL_IN,
        COL_NOTE,
        COL_TARGET,
        StatementDialog,
    )
    from oplata.statement import Statement, StatementRow

    store.data.customers.append(Counterparty(name="Покупатель", unp="1"))
    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="1", name="Покупатель", incoming=50,
            )
        ]
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    headers = [
        dialog.table.horizontalHeaderItem(column).text()
        for column in range(dialog.table.columnCount())
    ]
    # «Куда» стоит сразу после «Поступления» (просьба пользователя).
    assert headers[headers.index("Поступление") + 1] == "Куда"
    assert headers.index("Куда") == COL_TARGET
    # В списке можно выбрать назначение — не просто текст в ячейке.
    combo = dialog._target_combo(0)
    assert combo is not None
    assert "Покупатели" in [
        combo.itemText(index) for index in range(combo.count())
    ]

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args: shown.append(args[-1])),
    )
    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    dialog._continue()

    # Разнос прошёл в этой же таблице: второго окна нет, сумма записана.
    assert [str(operation.amount) for operation in store.data.operations] == [
        "50.00"
    ]
    # v0.68.0: всё разнеслось — отчёт не показываем. Раньше он вставал
    # после каждого разноса и мешал вернуться к работе.
    assert shown == []
    assert dialog.table.item(0, COL_IN).text() == "50,00"
    assert "Покупатель" in dialog.table.item(0, COL_NOTE).text()


def test_distribution_reports_rows_left_unresolved(qt_app, store, monkeypatch):
    """v0.63.0: что не разнеслось, видно в отчёте, а не молча."""

    from PySide6.QtWidgets import QMessageBox
    from oplata.models import Counterparty
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import Statement, StatementRow

    store.data.suppliers += [
        Counterparty(name="Опт поставка", unp="111222333", note="товар"),
        Counterparty(name="Опт аренда", unp="111222333", note="аренда"),
    ]
    statement = _ambiguous_statement()
    dialog = StatementDialog(statement, "в7.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args: shown.append(args[-1])),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    # Получатель не выбран: двух карточек с одним УНП достаточно, чтобы
    # строка ждала выбора, — и она не должна потеряться молча.
    assert dialog.matches[0].target == "choose"
    dialog._continue()

    assert store.data.operations == []  # писать нечего
    assert shown
    report = shown[-1]
    # v0.65.0: «надо выбрать получателя» — отдельный случай, его
    # говорят своим словами, а не общим «не разнесено».
    assert "Надо выбрать получателя" in report
    assert "УНП 111222333" in report


# --------------------------------------------------------------- v0.64.0
def test_budget_payment_code_is_not_read_as_amount(qt_app):
    """v0.64.0: код платежа в бюджет — не разряды суммы.

    В выписке за 21.10.2026 у платежей в бюджет перед суммой стоит код
    (в шапке файла есть колонка «Код пл. в бюдж»): «00101 300,00 0,00»
    — это код 00101, оплата 300,00, поступление 0,00. Код принимался за
    старшие разряды, и в базу уходило 101 300,00 вместо 300,00.
    """

    from oplata.statement import parse_statement_text

    template = (
        "05.11.2026 1805 6 Счет: BY13 AKBB 3812 0000 0028 1000 0000\n"
        "БИК: AKBBBY2X УНП: 500563252\n"
        "%s\n"
        "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ\n"
        "Назначение платежа: ПОДОХОДНЫЙ НАЛОГ ЗА ОКТЯБРЬ 2026Г.\n"
        "Фактическая дата и время совершения операции: 05.11.2026 10:06:07\n"
    )

    def amounts_of(line: str) -> tuple[str, str]:
        row = parse_statement_text(template % line).rows[0]
        return str(row.outgoing), str(row.incoming)

    # Код платежа в бюджет отбрасывается.
    assert amounts_of("00101 300,00 0,00") == ("300.00", "0.00")
    assert amounts_of("03511 800,00 0,00") == ("800.00", "0.00")
    assert amounts_of("03511 1 800,00 0,00") == ("1800.00", "0.00")
    # Обычные суммы читаются как прежде: разряды разделены пробелами
    # группами по три, поэтому первая группа короче кода.
    assert amounts_of("2 300,00 0,00") == ("2300.00", "0.00")
    assert amounts_of("1,00 0,00") == ("1.00", "0.00")
    assert amounts_of("0,00 1 505,90") == ("0.00", "1505.90")
    assert amounts_of("1 234 567,89 0,00") == ("1234567.89", "0.00")


def test_budget_statement_totals_match_file_turnover(qt_app):
    """v0.64.0: выписка с кодом платежа в бюджет читается без расхождений.

    Проверка по самому файлу: суммы строк должны сойтись с оборотами и
    сальдо, которые банк печатает в шапке и в итоге выписки.
    """

    from oplata.statement import parse_statement

    statement = parse_statement(BUDGET_STATEMENT)
    rows = {(row.unp, str(row.amount)): row for row in statement.rows}
    # ГУ МФ: 300,00 — подоходный налог (код 00101), 800,00 — взносы
    # (код 03511). Прежде читалось 101 300,00 и 3 511 800,00.
    assert ("500563252", "300.00") in rows
    assert ("500563252", "800.00") in rows
    assert [str(row.amount) for row in statement.rows] == [
        "1.00",
        "100.00",
        "300.00",
        "800.00",
        "2300.00",
        "10.36",
        "1527.37",  # 1 505,90 + комиссия 21,47 (v0.55.0)
    ]
    # Итог строк сошёлся с оборотами файла (с учётом комиссии банка).
    commission = statement.total_commission
    assert statement.total_outgoing == statement.turnover_outgoing + commission
    assert statement.total_incoming == statement.turnover_incoming + commission
    # И сальдо сошлось: 2 094,47 − 3 522,47 + 1 537,73 = 109,73.
    assert str(statement.opening_balance) == "2094.47"
    assert (
        statement.opening_balance
        - statement.total_outgoing
        + statement.total_incoming
        == statement.closing_balance
    )


def test_statement_warns_when_totals_miss_turnover(qt_app, store, monkeypatch):
    """v0.64.0: расхождение с оборотами файла — красным.

    Иначе его легко не заметить: именно так выглядела выписка, где
    код платежа в бюджет прочитался как разряды суммы.
    """

    from PySide6.QtWidgets import QLabel

    from oplata import styles
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import Statement, StatementRow

    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="500563252", name="ГУ МФ",
                outgoing=Decimal("101300"),
            )
        ]
    )
    # Обороты файла про сумму в 101 300,00 ничего не знают.
    statement.turnover_outgoing = Decimal("300.00")
    dialog = StatementDialog(statement, "в7.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    texts = [
        child.text()
        for child in dialog.findChildren(QLabel)
        if "Обороты" in child.text() or "Внимание" in child.text()
    ]
    assert texts, "подпись с итогами не найдена"
    assert "Внимание" in texts[0]
    assert f"color:{styles.RED}" in texts[0]  # предупреждение красным
    assert "<br>" in texts[0]  # переносы видны и в rich text
    dialog.close()


# --------------------------------------------------------------- v0.65.0
def _store_with_millions(store):
    """База, где выписку уже разнесли неверными суммами (v0.65.0).

    Точная копия состояния после версии до 0.64.0: код платежа в
    бюджет попадал в сумму, поэтому ГУ МФ разнеслись как 101 300,00 и
    3 511 800,00. Реестр разноса заполнен так же, как его пишет
    программа.
    """

    from decimal import Decimal

    from oplata.models import (
        OUTGOING,
        add_other_payment,
        set_daily_other_payment,
    )

    store.data.bank.unp = "100325912"
    add_other_payment(
        store.data, "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ", "2026-10-21", OUTGOING,
        unp="500563252", note="ПОДОХОДНЫЙ НАЛОГ ИЗ ЗАРАБОТНОЙ ПЛАТЫ",
    )
    add_other_payment(
        store.data, "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)", "2026-10-21", OUTGOING,
        unp="500563252", note="ОПЛАТА СТРАХОВЫХ ВЗНОСОВ",
    )
    set_daily_other_payment(
        store.data, "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ", "2026-10-21",
        Decimal("101300.00"), OUTGOING,
    )
    set_daily_other_payment(
        store.data, "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)", "2026-10-21",
        Decimal("3511800.00"), OUTGOING,
    )
    store.data.imported_statement_rows = [
        "21.10.2026|500563252|101300.00|out",
        "21.10.2026|500563252|3511800.00|out",
    ]
    store.data.imported_statement_writes = [
        {
            "keys": ["21.10.2026|500563252|101300.00|out"],
            "target": "other_payment", "counterparty_id": "",
            "payment_name": "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ",
            "date": "2026-10-21", "amount": "101300.00",
        },
        {
            "keys": ["21.10.2026|500563252|3511800.00|out"],
            "target": "other_payment", "counterparty_id": "",
            "payment_name": "ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)",
            "date": "2026-10-21", "amount": "3511800.00",
        },
    ]
    return store


def test_redo_removes_rows_written_with_other_amount(
    qt_app, store, monkeypatch
):
    """v0.65.0: прежняя запись снимается, даже если сумма строки изменилась.

    Ключ строки раньше включал сумму (v0.56.0), поэтому после
    исправления разбора (код платежа в бюджет, v0.64.0) программа не
    узнавала свою же строку: 101 300,00 висела в базе, а 300,00
    писалась рядом. Теперь строка ищется по дате, УНП и направлению.
    """

    from PySide6.QtWidgets import QMessageBox
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import parse_statement

    store = _store_with_millions(store)
    statement = parse_statement(BUDGET_STATEMENT)
    dialog = StatementDialog(
        statement, "в7.pdf", store=store, bank_unp="100325912"
    )
    dialog.show()
    qt_app.processEvents()

    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *args, **kwargs: None)
    )

    # Получателя не выбираем: программа всё равно должна снять миллионы,
    # иначе неверная сумма останется в базе навсегда.
    dialog._continue()
    result = dialog.result_distribution
    assert [item for item in result.replaced if "101 300,00" in item]
    assert [item for item in result.replaced if "3 511 800,00" in item]
    assert len(result.restated) == 2
    assert len(result.rolled_unwritten) == 2  # снято, но не записано
    amounts = {
        item.name: str(item.amount) for item in store.data.other_payments
    }
    assert amounts["ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ"] == "0"
    assert amounts["ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)"] == "0"
    # Окно не закрылось: получателя надо выбрать здесь же.
    assert dialog.result() == 0

    # Выбираем получателей — и суммы встают на место.
    for row, want in ((2, "ПОДОХОДНЫЙ"), (3, "СТРАХОВЫХ")):
        combo = dialog._target_combo(row)
        for index in range(combo.count()):
            if want in combo.itemText(index):
                combo.setCurrentIndex(index)
                break
    dialog._continue()
    amounts = {
        item.name: str(item.amount) for item in store.data.other_payments
    }
    assert amounts["ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ"] == "300.00"
    assert amounts["ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)"] == "800.00"
    dialog.close()


def test_redo_keeps_card_details_when_clearing_amount(qt_app, store):
    """v0.65.0: обнуление суммы дня не выбрасывает карточку вида.

    Раньше запись удалялась и создавалась заново — без УНП и
    примечания. Вид переставал узнаваться в выписке, и переразнос
    ломался: сняв сумму, программа «забывала», кому её писала.
    """

    from decimal import Decimal

    from oplata.models import (
        OUTGOING,
        add_other_payment,
        set_daily_other_payment,
    )

    add_other_payment(
        store.data, "ГУ МФ", "2026-10-21", OUTGOING, unp="500563252",
        note="ПОДОХОДНЫЙ НАЛОГ", vat_rate="20",
    )
    set_daily_other_payment(
        store.data, "ГУ МФ", "2026-10-21", Decimal("101300.00"), OUTGOING
    )
    set_daily_other_payment(
        store.data, "ГУ МФ", "2026-10-22", Decimal("500.00"), OUTGOING
    )
    # Снимаем сумму дня — карточка вида должна уцелеть целиком.
    set_daily_other_payment(store.data, "ГУ МФ", "2026-10-21", None, OUTGOING)

    rows = [
        item
        for item in store.data.other_payments
        if item.name == "ГУ МФ" and item.date == "2026-10-21"
    ]
    assert len(rows) == 1
    assert str(rows[0].amount) == "0"
    assert rows[0].unp == "500563252"
    assert rows[0].note == "ПОДОХОДНЫЙ НАЛОГ"
    assert rows[0].vat_rate == "20"
    # Сумма другого дня не пострадала.
    assert str(
        next(
            item.amount
            for item in store.data.other_payments
            if item.name == "ГУ МФ" and item.date == "2026-10-22"
        )
    ) == "500.00"


def test_redo_keeps_hands_off_when_rows_and_writes_disagree(
    qt_app, store
):
    """v0.65.0: неоднозначность — прежние записи не трогаем.

    Одному УНП в один день бывает несколько платежей. Если строк файла
    меньше, чем разнесённых записей, снимать их нельзя: сняли бы
    чужую строку, которой в этом файле нет.
    """

    from decimal import Decimal

    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import distribute

    store = _store_with_millions(store)
    statement = Statement(
        rows=[
            StatementRow(
                date="21.10.2026", unp="500563252",
                name="ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ",
                outgoing=Decimal("300.00"),
            )
        ]
    )
    result = distribute(statement, store.data)
    assert result.replaced == []          # ничего не снимали
    assert result.restated == []
    amounts = {
        item.name: str(item.amount) for item in store.data.other_payments
    }
    assert amounts["ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ"] == "101300.00"
    assert amounts["ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ (2)"] == "3511800.00"


# --------------------------------------------------------------- v0.66.0
def test_statement_parses_operation_counts(qt_app):
    """v0.66.0: «Кол-во операций 5 2» читается из файла.

    Число операций — вторая проверка разбора после сумм: потерянную
    или слипшуюся строку по одним суммам не поймать.
    """

    from oplata.statement import parse_statement

    statement = parse_statement(BUDGET_STATEMENT)
    assert statement.operations_outgoing == 5  # дебет — оплата
    assert statement.operations_incoming == 2  # кредит — поступление


def test_reconciliation_ok_on_real_files(qt_app):
    """v0.66.0: оба образца сходятся с файлом — ложной тревоги нет."""

    from oplata.statement import parse_statement, reconcile

    for path in (SAMPLE_STATEMENT, BUDGET_STATEMENT):
        check = reconcile(parse_statement(path))
        assert check.ok, f"{path}: {check.problems}"


def test_reconciliation_catches_wrong_amount_with_numbers(qt_app):
    """v0.66.0: расхождение называет обе суммы и разницу.

    Так выглядел дефект, из-за которого appeared жалоба «переразнести
    не работает»: код платежа в бюджет (00101) читался как разряды
    суммы, и вместо 300,00 в базу уходило 101 300,00. Прежде подпись
    говорила лишь «не совпало» — сравнивать числа приходилось в уме.
    """

    from oplata.statement import parse_statement, reconcile

    statement = parse_statement(BUDGET_STATEMENT)
    statement.rows[2].outgoing = Decimal("101300")  # 00101 300,00
    check = reconcile(statement)
    assert not check.ok
    text = check.problems[0]
    assert "3 501,00" in text  # что напечатано в файле
    assert "104 522,47" in text  # что прочитано (с комиссией банка)
    assert "разница +101 000,00" in text


def test_reconciliation_catches_lost_row_by_count(qt_app):
    """v0.66.0: слипшиеся строки ловит число операций, а не суммы.

    Две строки по 300,00 и 800,00 превратились в одну на 1 100,00:
    сумма та же, и проверка по деньгам молчит.
    """

    from oplata.statement import parse_statement, reconcile

    statement = parse_statement(BUDGET_STATEMENT)
    statement.rows[2].outgoing = Decimal("1100")
    statement.rows.pop(3)
    check = reconcile(statement)
    assert not check.ok
    assert any(
        "Оплат в файле 5, прочитано 4" == text for text in check.problems
    )
    # Суммы при этом сошлись — иначе проверка не про ту беду.
    assert statement.total_outgoing == check.expected_outgoing


def test_reconciliation_catches_bad_closing_balance(qt_app):
    """v0.66.0: сальдо файла сверяется само с собой, без строк.

    Входящее + поступление − оплата должно дать исходящее. Проверка не
    зависит от того, как прочитались строки: она ловит неверно
    прочитанные сами итоги файла.
    """

    from oplata.statement import parse_statement, reconcile

    statement = parse_statement(BUDGET_STATEMENT)
    statement.closing_balance = Decimal("999.99")
    check = reconcile(statement)
    assert not check.ok
    assert any(
        text.startswith("Сальдо файла не сходится")
        for text in check.problems
    )


def test_reconciliation_silent_when_file_has_no_totals(qt_app):
    """v0.66.0: не с чем сверить — молчим, а не пугаем.

    Выписка, собранная вручную (например, в тесте), не печатает
    оборотов. Поднимать тревогу на пустых итогах нельзя.
    """

    from oplata.statement import Statement, StatementRow, reconcile

    statement = Statement(
        rows=[
            StatementRow(
                date="21.10.2026", unp="500563252", name="ГУ МФ",
                outgoing=Decimal("300.00"),
            )
        ]
    )
    assert reconcile(statement).ok


def test_statement_table_shows_file_totals_when_mismatch(qt_app, store):
    """v0.66.0: при расхождении в таблице появляется строка «ПО ФАЙЛУ».

    Итог и ожидание по файлу стосят рядом — разницу видно, не
    перечитывая подпись под заголовком. Когда всё сходится, лишней
    строки нет: незачем дублировать числа.
    """

    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import Statement, StatementRow

    def build(outgoing):
        statement = Statement(
            rows=[
                StatementRow(
                    date="21.10.2026", unp="500563252", name="ГУ МФ",
                    outgoing=Decimal(outgoing),
                )
            ]
        )
        statement.turnover_outgoing = Decimal("300.00")
        return StatementDialog(statement, "в7.pdf", store=store)

    ok_dialog = build("300.00")
    ok_dialog.show()
    qt_app.processEvents()
    assert ok_dialog.table.rowCount() == 2  # строка + «ИТОГО»
    labels = [
        ok_dialog.table.item(row, 0).text()
        for row in range(ok_dialog.table.rowCount())
    ]
    assert "ПО ФАЙЛУ" not in labels
    ok_dialog.close()

    bad_dialog = build("101300.00")
    bad_dialog.show()
    qt_app.processEvents()
    assert bad_dialog.table.rowCount() == 3
    labels = [
        bad_dialog.table.item(row, 0).text()
        for row in range(bad_dialog.table.rowCount())
    ]
    assert labels[-2:] == ["ИТОГО", "ПО ФАЙЛУ"]
    assert bad_dialog.table.item(2, 3).text() == "300,00"
    bad_dialog.close()


def test_statement_caption_always_shows_file_turnover(qt_app, store):
    """v0.66.0: итоги файла видны и тогда, когда всё сошлось.

    Иначе «не совпало» заставляет искать, с чем именно сравнение.
    """

    from PySide6.QtWidgets import QLabel

    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import parse_statement

    statement = parse_statement(BUDGET_STATEMENT)
    dialog = StatementDialog(statement, "в7.pdf", store=store)
    dialog.show()
    qt_app.processEvents()
    caption = next(
        child.text()
        for child in dialog.findChildren(QLabel)
        if "Обороты файла" in child.text()
    )
    assert "3 501,00" in caption  # оплата по файлу
    assert "1 516,26" in caption  # поступление по файлу
    assert "операций 5 / 2" in caption
    assert "комиссия банка 21,47" in caption
    assert "Внимание" not in caption
    dialog.close()


def test_distribution_report_warns_about_file_mismatch(
    qt_app, store, monkeypatch
):
    """v0.66.0: расхождение повторяется и в отчёте разноса.

    Отчёт — последнее, что видит человек перед записью в базу:
    подпись под заголовком легко пропустить.
    """

    from PySide6.QtWidgets import QMessageBox
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import Statement, StatementRow

    statement = Statement(
        rows=[
            StatementRow(
                date="21.10.2026", unp="500563252", name="ГУ МФ",
                outgoing=Decimal("101300"),
            )
        ]
    )
    statement.turnover_outgoing = Decimal("300.00")
    dialog = StatementDialog(statement, "в7.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: shown.append(args[-1])),
    )
    # В offscreen настоящий QMessageBox.question ждёт ввода — подменяем.
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    dialog._continue()
    assert shown
    assert "не совпал с итогами файла" in shown[-1]
    assert "в файле 300,00" in shown[-1]  # обе суммы названы
    dialog.close()


# --------------------------------------------------------------- v0.68.0
def test_payment_cards_counted_once_not_by_days(qt_app, store):
    """v0.68.0: вид оплаты — одна карточка, сколько бы дней в ней ни было.

    ``other_payments`` хранит запись **за каждый день**. Из-за этого
    программа требовала выбора там, где карточка одна: у ООО
    «МИРСЕРВИССИТИ» одна карточка, а в списке значилось две; у ГУ МФ
    три вида оплаты, а программа называла пять. Там, где выбор всё же
    нужен, число карточек должно быть верным.
    """

    from oplata.models import (
        OUTGOING,
        add_other_payment,
        set_daily_other_payment,
    )
    from oplata.statement_match import find_all_payments_by_unp

    for name, note in (
        ("ГУ МФ", "ПОДОХОДНЫЙ НАЛОГ"),
        ("ГУ МФ (2)", "СТРАХОВЫЕ ВЗНОСЫ"),
        ("ГУ МФ (3)", "ЗЕМЕЛЬНЫЙ НАЛОГ"),
    ):
        add_other_payment(
            store.data, name, "2026-10-21", OUTGOING,
            unp="500563252", note=note,
        )
    # Те же виды с суммами за другие дни — записей становится больше.
    set_daily_other_payment(
        store.data, "ГУ МФ", "2026-10-15", Decimal("500")
    )
    set_daily_other_payment(
        store.data, "ГУ МФ (2)", "2026-10-15", Decimal("700")
    )
    add_other_payment(
        store.data, "МИРСЕРВИССИТИ", "2026-10-21", OUTGOING,
        unp="590205641", note="КОМ.УСЛУГИ",
    )
    set_daily_other_payment(
        store.data, "МИРСЕРВИССИТИ", "2026-10-14", Decimal("420")
    )

    assert len(find_all_payments_by_unp(store.data, "500563252")) == 3
    # Одна карточка — и разнос идёт сам, без вопроса.
    assert len(find_all_payments_by_unp(store.data, "590205641")) == 1


def test_statement_asks_choice_only_for_rows_with_several_cards(qt_app, store):
    """v0.68.0: выбор нужен только там, где карточек и правда несколько.

    В выписке в7.pdf просить выбор должны ровно две строки — 300,00 и
    800,00 (УНП ГУ МФ, три карточки). Строка ООО «МИРСЕРВИССИТИ» с
    одной карточкой разносится сама.
    """

    from oplata.models import (
        OUTGOING,
        add_other_payment,
        set_daily_other_payment,
    )
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import parse_statement
    from oplata.statement_match import TARGET_CHOOSE

    for name, note in (
        ("ГУ МФ", "ПОДОХОДНЫЙ НАЛОГ"),
        ("ГУ МФ (2)", "СТРАХОВЫЕ ВЗНОСЫ"),
        ("ГУ МФ (3)", "ЗЕМЕЛЬНЫЙ НАЛОГ"),
    ):
        add_other_payment(
            store.data, name, "2026-10-21", OUTGOING,
            unp="500563252", note=note,
        )
    set_daily_other_payment(
        store.data, "ГУ МФ", "2026-10-15", Decimal("500")
    )
    set_daily_other_payment(
        store.data, "ГУ МФ (2)", "2026-10-15", Decimal("700")
    )
    add_other_payment(
        store.data, "МИРСЕРВИССИТИ", "2026-10-21", OUTGOING,
        unp="590205641", note="КОМ.УСЛУГИ",
    )
    set_daily_other_payment(
        store.data, "МИРСЕРВИССИТИ", "2026-10-14", Decimal("420")
    )

    statement = parse_statement(BUDGET_STATEMENT)
    dialog = StatementDialog(
        statement, "в7.pdf", store=store, bank_unp="100325912"
    )
    dialog.show()
    qt_app.processEvents()

    choosing = [
        row.amount
        for row, match in zip(statement.rows, dialog.matches)
        if match.target == TARGET_CHOOSE
    ]
    assert choosing == [Decimal("300.00"), Decimal("800.00")], choosing
    dialog.close()


def test_target_popup_is_wider_than_column(qt_app, store):
    """v0.68.0: раскрывающийся список «Куда» читается целиком.

    Пункт подписан как «Наименование · УНП · Примечание»: в ширину
    колонки такая строка не влезала и обрезалась, а по обрезанному
    началу карточки не различить.
    """

    from oplata.dialogs.statement_dialog import (
        COLUMN_WIDTHS,
        StatementDialog,
        TARGET_POPUP_WIDTH,
    )
    from oplata.statement import Statement, StatementRow

    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="111222333", name="Организация",
                incoming=Decimal("50"),
            )
        ]
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store)
    dialog.show()
    qt_app.processEvents()
    combo = dialog._target_combo(0)
    assert combo is not None
    column_width = dict(COLUMN_WIDTHS)
    # Список шире колонки — иначе длинную подпись не прочитать.
    assert combo.view().minimumWidth() >= TARGET_POPUP_WIDTH
    assert TARGET_POPUP_WIDTH > 220  # прежняя ширина колонки
    dialog.close()


def test_successful_distribution_shows_no_report(qt_app, store, monkeypatch):
    """v0.68.0: всё разнеслось — отчёт не показываем.

    Раньше окно вставало после каждого разноса, и его приходилось
    закрывать, чтобы вернуться к работе.
    """

    from PySide6.QtWidgets import QMessageBox
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.models import Counterparty
    from oplata.statement import Statement, StatementRow

    store.data.customers.append(Counterparty(name="Покупатель", unp="1"))
    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="1", name="Покупатель",
                incoming=Decimal("50"),
            )
        ]
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args: shown.append(args[-1])),
    )
    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    dialog._continue()

    assert [str(op.amount) for op in store.data.operations] == ["50.00"]
    assert shown == []
    dialog.close()


def test_distribution_report_shown_when_rows_unresolved(
    qt_app, store, monkeypatch
):
    """v0.68.0: есть на что посмотреть — отчёт остаётся.

    Молчать можно, только когда всё сошлось. Неразнесённые строки —
    повод показать отчёт.
    """

    from PySide6.QtWidgets import QMessageBox
    from oplata.dialogs.statement_dialog import StatementDialog
    from oplata.statement import Statement, StatementRow

    statement = Statement(
        rows=[
            StatementRow(
                date="05.11.2026", unp="999888777", name="Незнакомец",
                outgoing=Decimal("1500"),
            )
        ]
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store)
    dialog.show()
    qt_app.processEvents()

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *args: shown.append(args[-1])),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    dialog._continue()

    assert shown
    assert "Незнакомец" in shown[-1]
    dialog.close()


def test_customer_journal_title_shows_note(qt_app, store):
    """v0.68.0: в шапке журнала покупателя — его примечание.

    Колонок в журнале три и имени среди них нет, поэтому заметку о
    покупателе показывать больше негде. Новых колонок не добавляем.
    """

    from PySide6.QtWidgets import QLabel
    from oplata.dialogs.operations_journal_dialog import (
        OperationsJournalDialog,
    )
    from oplata.models import Counterparty

    store.data.customers.append(
        Counterparty(
            name="ООО \"ОМА\"", unp="100005713",
            note="Договор 43/2026, отсрочка 5 дней",
        )
    )
    person = store.data.customers[-1]
    dialog = OperationsJournalDialog(store, "customer", person.id)
    dialog.show()
    qt_app.processEvents()
    titles = [child.text() for child in dialog.findChildren(QLabel)]
    assert 'ООО "ОМА" · Договор 43/2026, отсрочка 5 дней' in titles
    # Колонок по-прежнему три: примечание в шапке, а не в таблице.
    assert dialog.view.table.columnCount() == 3
    dialog.close()

# =====================================================================
# v0.69.0 — «Свойства контрагента»: путь разноса для задвоенных УНП
# =====================================================================

def test_routing_rule_matches_all_words_anywhere(qt_app, store):
    """v0.69.0: фраза разбирается на слова, ищется везде и вся целиком.

    Буквально фразу «ОПЛАТА СТРАХОВЫХ ВЗНОСОВ» искать нельзя: в выписке
    она перенесена по строкам и стоит в обратном порядке слов. Ищем
    каждое слово правила отдельно в имени и назначении вместе.
    """

    from oplata.models import OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import StatementRow
    from oplata.statement_match import (
        TARGET_OTHER_PAYMENT,
        classify,
        rule_text,
        rule_words_found,
    )

    add_other_payment(
        store.data, "ГУ МФ (2)", "2026-11-06", OUTGOING,
        unp="500563252", note="СТРАХОВЫЕ ВЗНОСЫ",
    )
    store.data.routing_rules = [
        RoutingRule(
            unp="500563252", name="ГУ МФ",
            words=["СТРАХОВЫХ", "ВЗНОСОВ"], target="other_payment",
            payment_name="ГУ МФ (2)",
        )
    ]
    row = StatementRow(
        date="06.11.2026", unp="500563252",
        name="ГУ МФ ПО ГРОДНЕНСКОЙ ОБЛАСТИ",
        purpose="ОПЛАТА СТРАХОВЫХ\nВЗНОСОВ",
        outgoing=Decimal("3900"),
    )
    assert rule_words_found(["СТРАХОВЫХ", "ВЗНОСОВ"], rule_text(row)) is True
    match = classify(row, store.data)
    assert match.target == TARGET_OTHER_PAYMENT
    assert match.payment_name == "ГУ МФ (2)"


def test_routing_rule_does_not_cross_unp(qt_app, store):
    """v0.69.0: правило не забирает чужой УНП.

    У страхового взноса в Белгосстрах своя организация и свой УНП:
    правило, написанное для ГУ МФ, к ней не относится.
    """

    from oplata.models import OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import StatementRow
    from oplata.statement_match import TARGET_OTHER_PAYMENT, classify

    add_other_payment(
        store.data, "ГУ МФ (2)", "2026-11-06", OUTGOING,
        unp="500563252", note="СТРАХОВЫЕ ВЗНОСЫ",
    )
    store.data.routing_rules = [
        RoutingRule(
            unp="500563252", name="ГУ МФ",
            words=["СТРАХОВЫХ", "ВЗНОСОВ"], target="other_payment",
            payment_name="ГУ МФ (2)",
        )
    ]
    row = StatementRow(
        date="06.11.2026", unp="500008110", name="БРУСП БЕЛГОССТРАХ",
        purpose="СТРАХОВЫЕ ВЗНОСЫ ЗА 2026 ГОД",
        outgoing=Decimal("86"),
    )
    assert classify(row, store.data).target != TARGET_OTHER_PAYMENT


def test_routing_rule_empty_never_matches(qt_app, store):
    """v0.69.0: правило без слов — не правило.

    С пустым набором слов подошло бы всё подряд, и весь файл уехал бы
    в одну карточку. Такое правило не срабатывает никогда.
    """

    from oplata.models import OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import StatementRow
    from oplata.statement_match import TARGET_CHOOSE, classify

    # Две карточки с одним УНП: без правила здесь всё равно выбор, и
    # пустое правило этот выбор не должно разрешать.
    add_other_payment(
        store.data, "Услуги банка", "2026-11-06", OUTGOING,
        unp="100325912", note="комиссия",
    )
    add_other_payment(
        store.data, "Прочее", "2026-11-06", OUTGOING,
        unp="100325912", note="вознаграждение",
    )
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=[],
                    target="other_payment", payment_name="Услуги банка")
    ]
    row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose="ЧТО УГОДНО", outgoing=Decimal("10"),
    )
    assert classify(row, store.data).target == TARGET_CHOOSE


def test_routing_rule_sends_commission_to_bank_services(qt_app, store):
    """v0.69.0: комиссия банка уходит в услуги банка по правилу.

    Дефект: строка с комиссией упиралась в «уточните получателя», а
    выбора для неё в окне не было — разнос останавливался.
    """

    from oplata.models import OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import StatementRow
    from oplata.statement_match import (
        TARGET_OTHER_PAYMENT,
        classify_commission,
    )

    add_other_payment(
        store.data, "Услуги банка", "2026-11-06", OUTGOING,
        unp="100325912", note="",
    )
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["КОМИССИЯ"],
                    target="other_payment", payment_name="Услуги банка")
    ]
    row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose="ПЛАТЕЖИ В СИСТЕМЕ РАСЧЕТОВ ПО КАРТОЧКАМ",
        incoming=Decimal("1136.74"), commission=Decimal("15.92"),
    )
    match = classify_commission(row, store.data)
    assert match.target == TARGET_OTHER_PAYMENT
    assert match.payment_name == "Услуги банка"


def test_incoming_rule_waits_for_cards_and_cash(qt_app, store):
    """v0.69.0: приходное правило не перехватывает строку по картам.

    В назначении строки по картам написано «Комиссия банка», поэтому
    правило для комиссии обязано уступать дорогу картам — иначе вся
    выручка уедет в услуги банка.
    """

    from oplata.models import (
        INCOMING,
        OUTGOING,
        RoutingRule,
        add_other_payment,
    )
    from oplata.statement import StatementRow
    from oplata.statement_match import TARGET_CARD, classify

    add_other_payment(
        store.data, "Услуги банка", "2026-11-06", OUTGOING,
        unp="100325912", note="",
    )
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["КОМИССИЯ"],
                    target="other_payment", payment_name="Услуги банка",
                    direction=INCOMING)
    ]
    row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose=(
            "Комиссия банка - 15.92 ПЕРЕЧИСЛЕНИЕ ДЕНЕЖНЫХ СРЕДСТВ "
            "ПО ОПЕРАЦИЯМ С ПЛАТЕЖНЫМИ КАРТОЧКАМИ"
        ),
        incoming=Decimal("1136.74"),
    )
    assert classify(row, store.data).target == TARGET_CARD


def test_manual_choice_beats_rule(qt_app, store):
    """v0.69.0: ручной выбор в «Куда» старше любого правила.

    Правило подсказывает, но не принуждает: что человек выбрал в
    списке «Куда», то и записывается.
    """

    from PySide6.QtWidgets import QComboBox
    from oplata.dialogs.statement_dialog import COL_TARGET, StatementDialog
    from oplata.models import (
        OUTGOING,
        Counterparty,
        RoutingRule,
        add_other_payment,
    )
    from oplata.statement import Statement, StatementRow
    from oplata.statement_match import TARGET_SUPPLIERS

    add_other_payment(
        store.data, "Услуги банка", "2026-11-06", OUTGOING,
        unp="100325912", note="",
    )
    store.data.suppliers.append(
        Counterparty(name="ОАО АСБ БЕЛАРУСБАНК", unp="100325912", note="сборы")
    )
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["КОМИССИЯ"],
                    target="other_payment", payment_name="Услуги банка")
    ]
    store.data.bank.unp = "100325912"
    statement = Statement(
        rows=[StatementRow(
            date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
            purpose="КОМИССИЯ ЗА ОБСЛУЖИВАНИЕ",
            outgoing=Decimal("50"),
        )]
    )
    dialog = StatementDialog(statement, "вм3.pdf", store=store,
                             bank_unp="100325912")
    dialog.show()
    qt_app.processEvents()

    # Правило своё дело сделало: список стоит на «Услуги банка».
    combo = dialog.table.cellWidget(0, COL_TARGET).findChild(QComboBox)
    assert "Услуги банка" in combo.currentText()

    # Человек выбрал другое — слушаем человека, а не правило.
    combo.setCurrentIndex(combo.findData(TARGET_SUPPLIERS))
    qt_app.processEvents()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        StatementDialog, "_ask_about_repeat", lambda self: False
    )
    dialog._continue()
    monkeypatch.undo()
    assert any(
        "Поставщик: ОАО АСБ БЕЛАРУСБАНК" in line
        for line in dialog.result_distribution.applied
    )
    assert not any(
        "Услуги банка" in line
        for line in dialog.result_distribution.applied
    )
    dialog.close()


def test_no_rule_match_says_so_in_note(qt_app, store):
    """v0.69.0: о неразобранной строке пишем в «Примечании», не молчим.

    Строка без карточки и без правила не исчезает бесследно: в
    примечании сказано, что правило не подошло и путь надо выбрать.
    """

    from oplata.models import OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import StatementRow
    from oplata.statement_match import TARGET_CHOOSE, classify

    # УНП общий у двух карточек — по нему их не различить.
    for name, note in (
        ("Услуги банка", "комиссия"),
        ("Прочее", "зарплата"),
    ):
        add_other_payment(store.data, name, "2026-11-06", OUTGOING,
                          unp="100325912", note=note)
    row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose="ПЕРЕЧИСЛЕНИЕ ЗАРАБОТНОЙ ПЛАТЫ",
        outgoing=Decimal("8586.99"),
    )
    # Правил для этого УНП ещё нет — подсказываем, где их завести.
    match = classify(row, store.data)
    assert match.target == TARGET_CHOOSE
    assert "100325912" in match.note
    assert "Конфигурации" in match.note

    # Правило есть, но его фраза в этой строке не встречается: писать
    # «подошло правило» нельзя, а молчать — тем более.
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["КОМИССИЯ"],
                    target="other_payment", payment_name="Услуги банка")
    ]
    match = classify(row, store.data)
    assert match.target == TARGET_CHOOSE
    assert "УНП 100325912" in match.note
    assert "не подошло ни одно правило" in match.note
    assert "Куда" in match.note


def test_data_view_has_routing_panel(qt_app, store):
    """v0.69.0: в «Конфигурации» есть плашка «Свойства контрагента»."""

    from oplata.views.data_view import DataView
    from oplata.widgets import CollapsiblePanel

    view = DataView(store)
    view.show()
    qt_app.processEvents()
    titles = [
        panel.toggle.text()
        for panel in view.findChildren(CollapsiblePanel)
    ]
    assert any("Свойства контрагента" in title for title in titles)


def test_routing_panel_lists_only_cards_of_that_unp(qt_app, store):
    """v0.69.0: в списке «Куда» — только карточки этого УНП.

    УНП 500563252 — общий у налогов и взносов. Показываем только эти
    две карточки, а не весь список: выбирать не из чего лишнего.
    """

    from oplata.models import OUTGOING, add_other_payment
    from oplata.views.data_view import DataView

    for name, note in (
        ("ГУ МФ", "ПОДОХОДНЫЙ НАЛОГ"),
        ("ГУ МФ (2)", "СТРАХОВЫЕ ВЗНОСЫ"),
    ):
        add_other_payment(store.data, name, "2026-11-06", OUTGOING,
                          unp="500563252", note=note)
    add_other_payment(store.data, "Услуги банка", "2026-11-06", OUTGOING,
                      unp="100325912", note="")
    view = DataView(store)
    view.show()
    row = view._add_rule_row()
    row["unp"].setText("500563252")
    labels = [
        row["target"].itemText(i) for i in range(row["target"].count())
    ]
    assert len(labels) == 2
    assert all("ГУ МФ" in label for label in labels)
    assert not any("Услуги банка" in label for label in labels)


def test_routing_panel_saves_and_reloads_rules(qt_app, store):
    """v0.69.0: правила сохраняются и встают на место при перезагрузке."""

    from oplata.models import OUTGOING, add_other_payment
    from oplata.views.data_view import DataView

    add_other_payment(store.data, "Услуги банка", "2026-11-06", OUTGOING,
                      unp="100325912", note="")
    view = DataView(store)
    view.show()
    row = view._add_rule_row()
    row["name"].setText("ОАО АСБ БЕЛАРУСБАНК")
    row["unp"].setText("100325912")
    row["words"].setText("Комиссия банка")
    row["target"].setCurrentIndex(0)
    assert view.save_routing_rules() is True
    assert len(store.data.routing_rules) == 1
    rule = store.data.routing_rules[0]
    assert rule.unp == "100325912"
    assert rule.words == ["Комиссия", "банка"]
    assert rule.payment_name == "Услуги банка"

    view._load_routing_rules()
    assert len(view._rule_rows) == 1
    assert view._rule_rows[0]["words"].text() == "Комиссия банка"
    assert "Услуги банка" in view._rule_rows[0]["target"].currentText()


def test_routing_panel_rejects_bad_rule(qt_app, store):
    """v0.69.0: незаполненное правило не сохраняется молча.

    Короткий УНП, пустая фраза и невыбранная карточка — это ошибка, а
    не «пусть будет правило без адреса».
    """

    from oplata.views.data_view import DataView

    view = DataView(store)
    view.show()
    row = view._add_rule_row()
    row["unp"].setText("12345")
    row["words"].setText("ЧТО-ТО")
    monkeypatch = pytest.MonkeyPatch()
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args: shown.append(args[-1])),
    )
    ok = view.save_routing_rules()
    monkeypatch.undo()
    assert ok is False
    assert shown
    assert "9 цифр" in shown[-1]
    assert store.data.routing_rules == []


def test_routing_panel_has_no_clutter_text(qt_app, store):
    """v0.70.0: в «Свойствах контрагента» нет пояснений и предупреждений.

    Под заголовком «Путь разноса сумм» шла длинная справка, а под
    таблицей — строка с жёлтым треугольником про задвоенные УНП и
    слабые слова. Обе только отъедали место: состав строки правила
    понятен по подписям полей. Остаются заголовок, строки правил и
    кнопки.
    """

    from PySide6.QtWidgets import QLabel
    from oplata.models import Counterparty
    from oplata.views.data_view import DataView

    store.data.suppliers.append(
        Counterparty(name='ООО "ОМА"', unp="100005713", note="Стройматериалы")
    )
    store.data.suppliers.append(
        Counterparty(name='ООО "ОМА" (2)', unp="100005713", note="")
    )
    view = DataView(store)
    view.show()
    texts = [label.text() for label in view.routing_panel.findChildren(QLabel)]
    # Ни справки, ни предупреждения: только заголовок, статус и подписи.
    assert not any("УНП на несколько" in text for text in texts)
    assert not any(text.startswith("\u26a0") for text in texts)
    assert not any("Примечание" in text for text in texts)
    assert not any(len(text) > 120 for text in texts)


def test_routing_panel_font_is_smaller(qt_app, store):
    """v0.71.0: кегль панели на 30% меньше обычного — **у самих полей**.

    Строка правила — пять полей в ряд, и при обычном кегле УНП
    обрезалось.

    Проверка идёт по полям, а не по контейнеру строки: в v0.70.0
    setFont() менял шрифт только у контейнера, а ``QLineEdit`` внутри
    оставался на 17 px — глобальное правило «* { font-size }» из QSS
    старше QWidget::font(). Тест тогда проверял контейнер и был зелёным.
    """

    from oplata import styles
    from oplata.views.data_view import DataView

    view = DataView(store)
    view.show()
    row = view._add_rule_row()
    expected = int(round(styles.fs(13) * 0.7))
    assert view._routing_px == expected
    assert expected < styles.fs(13)
    # Кегль дошёл до самих полей — это и было сломано в v0.70.0.
    for key in ("name", "unp", "words", "direction", "target"):
        assert row[key].font().pixelSize() == expected, key


def test_rule_unp_field_fits_nine_digits(qt_app, store):
    """v0.71.0: поле УНП вмещает девять цифр целиком — при любом кегле.

    Ширина считается по метрикам того шрифта, которым поле набрано, а
    не константой: в v0.70.0 стояло 92 px, и на части экранов девять
    цифр (108 px обычным кеглем) обрезались.
    """

    from PySide6.QtGui import QFontMetrics

    from oplata.views.data_view import DataView

    view = DataView(store)
    view.show()
    row = view._add_rule_row()
    field = row["unp"]
    metrics = QFontMetrics(field.font())
    # 16 px — внутренние поля ввода и рамка.
    assert metrics.horizontalAdvance("100325912") + 16 <= field.width()


def test_rule_phrase_is_found_in_account_field(qt_app, store):
    """v0.71.0: ключевая фраза ищется и в счёте контрагента.

    Фраза вида «Счет: BY85 AKBB 3602 9440 1000 0000 0000» печатается в
    шапке операции, рядом с УНП, — и именно по ней правило находится
    надёжнее всего. До v0.71.0 счёт из выписки не читался вовсе.
    """

    from decimal import Decimal

    from oplata.models import OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import parse_statement_text
    from oplata.statement_match import TARGET_OTHER_PAYMENT, classify

    add_other_payment(store.data, "Услуги банка", "2026-11-06", OUTGOING,
                      unp="100325912", note="")
    store.data.routing_rules = [
        RoutingRule(
            unp="100005713",
            name='ООО "ОМА"',
            words="Счет: BY85 AKBB 3602 9440 1000 0000 0000".split(),
            target="other_payment",
            payment_name="Услуги банка",
        ),
    ]
    text = (
        "ВЫПИСКА ИЗ ЛИЦЕВОГО СЧЕТА ЗА 03.11.2026\n"
        "Счет клиента: BY13 AKBB 3012 0000 0000 0000 0000\n"
        "03.11.2026 839 1 Счет: BY85 AKBB 3602 9440 1000 0000 0000\n"
        "БИК: AKBBBY2X УНП: 100005713\n"
        "5 549,97 0,00\n"
        'ООО "ОМА"\n'
        "Назначение платежа: ЗА СТРОЙМАТЕРИАЛЫ\n"
        "Фактическая дата и время совершения операции: "
        "03.11.2026 11:44:06\n"
    )
    statement = parse_statement_text(text)
    row = statement.rows[0]
    # счёт контрагента прочитан — раньше его не было в строке вовсе
    assert row.account == "BY85 AKBB 3602 9440 1000 0000 0000"
    match = classify(row, store.data)
    assert match.target == TARGET_OTHER_PAYMENT
    assert match.payment_name == "Услуги банка"


def test_summary_header_does_not_overlap(qt_app, store):
    """v0.71.0: шапка «Сводки» влезает в плашку или прячет стрелки.

    Подпись, две стрелки и поле даты требуют ~306 px. На экране 1366
    плашка «Сводка» — всего 276 px, и в v0.70.0 раскладка сжимала
    виджеты ниже минимума: они наползали друг на друга. Теперь дата
    стоит отдельной строкой, а стрелки на узкой плашке прячутся.

    Ширина окна в проверке **не** задаётся: в CI и на машине
    разработчика экраны разные, и привязка к 1366/1920 делала бы тест
    нестабильным. Проверяется сам механизм.
    """

    from oplata.views.summary_view import SUMMARY_ARROW_WIDTH, SummaryView

    view = SummaryView(store, lambda _id: None)
    view.show()
    view._fit_date_row()

    def needed() -> int:
        width = view.date_edit.minimumSizeHint().width()
        if view.prev_btn.isVisible():
            width += 2 * SUMMARY_ARROW_WIDTH
        return width + 2 * view._date_row.spacing()

    # Главное: шапка влезает в плашку — значит, наложения нет.
    assert needed() <= view.width()

    # Правило само по себе: просторно — стрелки есть, тесно — нет.
    assert view._arrows_fit(1000)
    assert not view._arrows_fit(view.date_edit.minimumSizeHint().width())

    # И механизм действительно их прячет, а не просто считает.
    # Ширины хватает на дату, но не на обе стрелки.
    tight = (
        view.date_edit.minimumSizeHint().width()
        + 2 * view._date_row.spacing()
        + SUMMARY_ARROW_WIDTH
    )
    real_width = view.width
    try:
        view.width = lambda: tight
        view._fit_date_row()
        assert not view.prev_btn.isVisible()
        assert needed() <= view.width()
    finally:
        view.width = real_width



def test_rule_and_routing_survive_save_load(qt_app, store):
    """v0.69.0: правила переживают запись и чтение файла данных."""

    from oplata.models import RoutingRule
    from oplata.storage import DataStore

    store.data.routing_rules = [
        RoutingRule(
            unp="500563252", name="ГУ МФ",
            words=["ПОДОХОДНЫЙ", "НАЛОГ"], target="other_payment",
            payment_name="ГУ МФ",
        )
    ]
    store.save()
    back = DataStore(store.path)
    assert len(back.data.routing_rules) == 1
    rule = back.data.routing_rules[0]
    assert rule.unp == "500563252"
    assert rule.words == ["ПОДОХОДНЫЙ", "НАЛОГ"]
    assert rule.payment_name == "ГУ МФ"
    assert "ПОДОХОДНЫЙ" in rule.label()


def test_routing_rules_empty_in_old_data_file(qt_app, store):
    """v0.69.0: старый файл без правил читается без ошибок."""

    import json

    from oplata.storage import DataStore

    store.data.routing_rules = []
    store.save()
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw.pop("routing_rules", None)
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    back = DataStore(store.path)
    assert back.data.routing_rules == []



def test_rule_of_other_direction_never_matches(qt_app, store):
    """v0.69.0: правило «Оплата» не трогает приход, и наоборот.

    У ОАО «АСБ БЕЛАРУСБАНК» в одном файле и приход по картам, и
    комиссия за него. Правило про комиссию помечено «Оплата» — значит
    приходную строку оно обязано пропустить, даже если слово
    «КОМИССИЯ» в её назначении стоит.
    """

    from oplata.models import (
        INCOMING,
        OUTGOING,
        RoutingRule,
        add_other_payment,
    )
    from oplata.statement import StatementRow
    from oplata.statement_match import (
        TARGET_CARD,
        TARGET_CHOOSE,
        TARGET_OTHER_INCOME,
        TARGET_OTHER_PAYMENT,
        classify,
    )

    # Две карточки с одним УНП: без правила строку не различить,
    # поэтому успех означает именно сработавшее правило.
    for name in ("Услуги банка", "Прочее"):
        add_other_payment(
            store.data, name, "2026-11-06", OUTGOING,
            unp="100325912", note="",
        )
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["КОМИССИЯ"],
                    target="other_payment", payment_name="Услуги банка",
                    direction=OUTGOING),
        RoutingRule(unp="100325912", name="БАНК", words=["ПРОЦЕНТЫ"],
                    target="other_income", direction=INCOMING),
    ]
    # Приход по картам: в назначении есть и «Комиссия банка». Правило
    # оплаты его не трогает — строка остаётся поступлением по картам.
    incoming_row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose=(
            "Комиссия банка - 15.92 ПЕРЕЧИСЛЕНИЕ ДЕНЕЖНЫХ СРЕДСТВ "
            "ПО ОПЕРАЦИЯМ С ПЛАТЕЖНЫМИ КАРТОЧКАМИ"
        ),
        incoming=Decimal("1136.74"),
    )
    assert classify(incoming_row, store.data).target == TARGET_CARD

    # Расход — правило поступления его не трогает.
    outgoing_row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose="ПРОЦЕНТЫ ЗА ОБСЛУЖИВАНИЕ", outgoing=Decimal("40"),
    )
    assert classify(outgoing_row, store.data).target == TARGET_CHOOSE

    # А приход со своим словом правило поступления берёт.
    percent_row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose="ПРОЦЕНТЫ НА ОСТАТОК ПО СЧЕТУ", incoming=Decimal("12.50"),
    )
    match = classify(percent_row, store.data)
    assert match.target == TARGET_OTHER_INCOME
    assert "ПРОЦЕНТЫ" in match.note


def test_commission_uses_outgoing_rule_only(qt_app, store):
    """v0.69.0: комиссию разбирают только правила «Оплата».

    Комиссия банка — всегда расход, даже когда висит на приходной
    строке. Правило «Поступление» её не касается.
    """

    from oplata.models import INCOMING, OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import StatementRow
    from oplata.statement_match import (
        TARGET_CHOOSE,
        classify_commission,
    )

    add_other_payment(
        store.data, "Услуги банка", "2026-11-06", OUTGOING,
        unp="100325912", note="комиссия",
    )
    add_other_payment(
        store.data, "Прочее", "2026-11-06", OUTGOING,
        unp="100325912", note="вознаграждение",
    )
    # Правило про комиссию, но помеченное «Поступление».
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["КОМИССИЯ"],
                    target="other_payment", payment_name="Услуги банка",
                    direction=INCOMING)
    ]
    row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose="ПЛАТЕЖИ ПО КАРТОЧКАМ. Комиссия банка",
        incoming=Decimal("1136.74"), commission=Decimal("15.92"),
    )
    # Комиссия — расход, правило прихода к ней не относится: двух
    # карточек с одним УНП правило не различает, остаётся выбор.
    assert classify_commission(row, store.data).target == TARGET_CHOOSE

    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["КОМИССИЯ"],
                    target="other_payment", payment_name="Услуги банка",
                    direction=OUTGOING)
    ]
    match = classify_commission(row, store.data)
    assert match.target != TARGET_CHOOSE
    assert match.payment_name == "Услуги банка"


def test_routing_panel_direction_column(qt_app, store):
    """v0.69.0: колонка «Направление» стоит после фразы, по умолчанию
    «Оплата», и сохраняется вместе с правилом.
    """

    from oplata.models import (
        INCOMING,
        OUTGOING,
        Counterparty,
        add_other_payment,
    )
    from oplata.views.data_view import DataView

    add_other_payment(store.data, "Услуги банка", "2026-11-06", OUTGOING,
                      unp="100325912", note="")
    view = DataView(store)
    view.show()
    row = view._add_rule_row()
    labels = [
        row["direction"].itemText(i)
        for i in range(row["direction"].count())
    ]
    # Пока УНП пуст, список «Куда» собран по направлению «Оплата».
    assert row["target"].count() >= 1
    assert labels == ["Оплата", "Поступление"]
    assert row["direction"].currentData() == OUTGOING  # по умолчанию

    # Список «Куда» зависит от направления: у приходной строки вида
    # оплаты не бывает, зато есть покупатели и прочие поступления.
    store.data.customers.append(
        Counterparty(name="ООО \"Ромашка\"", unp="100325912", note="розница")
    )
    row["unp"].setText("100325912")
    row["words"].setText("Комиссия банка")
    row["direction"].setCurrentIndex(row["direction"].findData(OUTGOING))
    assert row["target"].count() == 1  # только вид оплаты «Услуги банка»
    assert "Услуги банка" in row["target"].itemText(0)
    row["direction"].setCurrentIndex(row["direction"].findData(INCOMING))
    labels = [
        row["target"].itemText(i) for i in range(row["target"].count())
    ]
    assert "Ромашка" in labels[0]
    assert labels[-1] == "Прочие поступления"
    assert not any("Услуги банка" in label for label in labels)

    row["target"].setCurrentIndex(0)
    assert view.save_routing_rules() is True
    assert store.data.routing_rules[0].direction == INCOMING

    view._load_routing_rules()
    assert len(view._rule_rows) == 1
    assert view._rule_rows[0]["direction"].currentData() == INCOMING


def test_direction_survives_save_load(qt_app, store):
    """v0.69.0: направление переживает запись и чтение файла данных."""

    from oplata.models import INCOMING, RoutingRule
    from oplata.storage import DataStore

    store.data.routing_rules = [
        RoutingRule(
            unp="100325912", name="БАНК", words=["ПРОЦЕНТЫ"],
            target="other_payment", payment_name="Проценты банка",
            direction=INCOMING,
        )
    ]
    store.save()
    back = DataStore(store.path)
    assert back.data.routing_rules[0].direction == INCOMING
    assert back.data.routing_rules[0].direction_label() == "Поступление"


def test_old_rule_without_direction_is_payment(qt_app, store):
    """v0.69.0: файл без поля «direction» читается как правило оплаты.

    Колонка «Направление» появилась следом за первым выпуском панели:
    правила, сохранённые до неё, обязаны работать как прежде.
    """

    import json

    from oplata.models import OUTGOING
    from oplata.storage import DataStore

    store.data.routing_rules = []
    store.save()
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    raw["routing_rules"] = [
        {
            "unp": "100325912",
            "name": "БАНК",
            "words": ["КОМИССИЯ"],
            "target": "other_payment",
            "counterparty_id": "",
            "payment_name": "Услуги банка",
        }
    ]
    store.path.write_text(json.dumps(raw), encoding="utf-8")
    back = DataStore(store.path)
    assert back.data.routing_rules[0].direction == OUTGOING


# =====================================================================
# v0.70.0 — экран «Поступление и оплата»: компактнее и читаемее
# =====================================================================

def test_row_titles_are_uppercase(qt_app, store):
    """v0.70.0: названия строк набраны заглавными буквами.

    «Поставщики», «Поступление наличными» и прочие — заглавными: в
    узкой таблице так читается быстрее. Ключи строк от этого не
    меняются, иначе суммы перестали бы находиться в базе.
    """

    from oplata.views.receipts_view import (
        CARD_ROW,
        CASH_ROW,
        CUSTOMERS_ROW,
        ROW_TITLES,
        SUPPLIERS_ROW,
        MoneyBlock,
        other_row_key,
    )

    assert ROW_TITLES[CASH_ROW] == "ПОСТУПЛЕНИЕ НАЛИЧНЫМИ"
    assert ROW_TITLES[CARD_ROW] == "ПОСТУПЛЕНИЕ ПО КАРТАМ"
    assert ROW_TITLES[CUSTOMERS_ROW] == "ПОКУПАТЕЛИ"
    assert ROW_TITLES[SUPPLIERS_ROW] == "ПОСТАВЩИКИ"
    # Свой вид оплаты тоже подписывается заглавными, а ключ — нет.
    key = other_row_key("Услуги банка")
    assert MoneyBlock._row_title(key) == "УСЛУГИ БАНКА"
    assert key == "other:Услуги банка"


def test_outgoing_rows_sorted_alphabetically(qt_app, store):
    """v0.70.0: свои виды оплаты — по алфавиту, «Поставщики» сверху.

    Раньше порядок был порядком добавления, и нужный вид приходилось
    искать глазами по всему списку. «Поставщики» остаются первой
    строкой всегда: это основная строка блока.
    """

    from oplata.models import OUTGOING, add_other_payment
    from oplata.views.receipts_view import (
        SUPPLIERS_ROW,
        ReceiptsView,
        other_row_name,
    )

    # Добавляем в «неправильном» порядке.
    for name in ("Ярлыки", "Комиссия банка", "Аренда"):
        add_other_payment(store.data, name, "2026-11-06", OUTGOING)
    view = ReceiptsView(store)
    view.show()
    keys = view._outgoing_rows()
    assert keys[0] == SUPPLIERS_ROW
    names = [other_row_name(key) for key in keys[1:]]
    assert names == ["Аренда", "Комиссия банка", "Ярлыки"]


def test_day_column_wide_enough_for_big_amount(qt_app, store):
    """v0.70.0: в ячейке дня влезает сумма с разрядами.

    Было 96 px — хватало на «88 888,88», а «3 511 800,00» обрезалось.
    """

    from oplata.views.receipts_view import DAY_COLUMN_WIDTH

    assert DAY_COLUMN_WIDTH >= 112


def test_note_column_wider_than_before(qt_app, store):
    """v0.70.0: колонка «Примечание» шире, название вида — у́же.

    Название растягивается по остатку, поэтому widening примечания и
    дней как раз и сжимает колонку видов оплат.
    """

    from oplata.views.receipts_view import COL_NOTE, COLUMN_WIDTHS

    assert COLUMN_WIDTHS[COL_NOTE] >= 200


def test_summary_panel_share_is_21_percent(qt_app, store):
    """v0.71.0: плашка «Сводка» занимает 21% ширины (в v0.70.0 — 20%).

    Доли 79 : 21. Место берётся у левых блоков, а в них — у
    растягиваемой колонки «Наименование» видов оплат.
    """

    from oplata.views.receipts_view import ReceiptsView
    from oplata.views.summary_view import SummaryView

    summary = SummaryView(store, lambda _id: None)
    view = ReceiptsView(store, summary_widget=summary)
    view.show()
    qt_app.processEvents()
    # Доли раскладки живут в корневой раскладке содержимого (сама
    # страница завёрнута в область прокрутки).
    root = view.left.parentWidget().layout()
    left, right = root.stretch(0), root.stretch(1)
    assert left + right == 100
    assert right == 21  # плашка «Сводка» — 21% ширины
    assert left == 79  # блоки «Поступление» и «Оплата»


def test_summary_has_date_arrows(qt_app, store):
    """v0.70.0: у «Сводки на дату» есть стрелки «←» и «→»."""

    from PySide6.QtWidgets import QPushButton
    from oplata.views.summary_view import SummaryView

    view = SummaryView(store, lambda _id: None)
    view.show()
    captions = [
        button.text() for button in view.findChildren(QPushButton)
    ]
    assert "←" in captions
    assert "→" in captions


def test_summary_arrows_shift_date(qt_app, store):
    """v0.70.0: стрелки «Сводки» листают дату на день назад и вперёд."""

    from datetime import date

    from PySide6.QtCore import QDate
    from oplata.views.summary_view import SummaryView
    from oplata.widgets import date_edit_value

    view = SummaryView(store, lambda _id: None)
    view.show()
    view.date_edit.setDate(QDate(2026, 11, 14))
    view._shift_date(1)
    assert date_edit_value(view.date_edit) == date(2026, 11, 15)
    view._shift_date(-1)
    assert date_edit_value(view.date_edit) == date(2026, 11, 14)


def test_rule_phrase_is_found_in_unp_field(qt_app, store):
    """v0.70.0: ключевая фраза ищется и в поле выписки с УНП.

    Фраза из выписки бывает набрана рядом с УНП организации, поэтому
    ищем и там, где в таблице стоит УНП, — не только в наименовании и
    назначении платежа.
    """

    from decimal import Decimal

    from oplata.models import OUTGOING, RoutingRule, add_other_payment
    from oplata.statement import StatementRow
    from oplata.statement_match import (
        TARGET_OTHER_PAYMENT,
        classify,
        rule_text,
    )

    add_other_payment(
        store.data, "Услуги банка", "2026-11-06", OUTGOING,
        unp="100325912", note="",
    )
    # Фраза — сам УНП: иначе её в строке не найти нигде, кроме поля УНП.
    store.data.routing_rules = [
        RoutingRule(unp="100325912", name="БАНК", words=["100325912"],
                    target="other_payment", payment_name="Услуги банка")
    ]
    row = StatementRow(
        date="06.11.2026", unp="100325912", name="ОАО АСБ БЕЛАРУСБАНК",
        purpose="ПЛАТЕЖИ ПО КАРТОЧКАМ", outgoing=Decimal("40"),
    )
    assert "100325912" in rule_text(row)
    match = classify(row, store.data)
    assert match.target == TARGET_OTHER_PAYMENT
    assert match.payment_name == "Услуги банка"


def test_statement_reads_amount_joined_to_document_line(store):
    """v0.72.0: сумма, приклеенная к заголовку операции.

    На стыке страниц pypdf склеивает строку операции со строкой сумм:

        14.10.2026 789 1 Счет: BY79 ALFA … 0000 11 765,34 0,00

    Раньше счёт разбирался как «все буквы и цифры подряд» и съедал
    хвост суммы, отдельной строки с суммами не было, и операция
    приходила с нулевой суммой и пустым наименованием. Проверено на
    реальном файле вм10.pdf: не хватало 11 765,34 из 23 807,89.
    """

    from decimal import Decimal

    from oplata.statement import parse_statement_text

    text = (
        "ВЫПИСКА ИЗ ЛИЦЕВОГО СЧЕТА ЗА 14.10.2026\n"
        "Счет клиента: BY47 AKBB 3012 1994 5068 3420 0000 Код валюты: BYN\n"
        "Наименование счета: ООО МирСтройМаркет\n"
        "Входящее сальдо: 48 623,65BYN\n"
        #_sk: суммы оказались в хвосте заголовка
        "14.10.2026 789 1 Счет: BY79 ALFA 3012 2399 3600 5027 0000 "
        "11 765,34 0,00\n"
        "БИК: ALFABY2X УНП: 192775574\n"
        'ООО "ТД КОМПЛЕКТ"\n'
        "Назначение платежа: ЗА ИНСТРУМ. СОГЛ.АКТА СВЕРКИ\n"
        "Фактическая дата и время совершения операции: 14.10.2026 08:35:27\n"
        "ОБОРОТЫ 11 765,34 0,00\n"
    )
    statement = parse_statement_text(text)
    row = statement.rows[0]
    # Сумма прочитана — раньше здесь был ноль.
    assert row.outgoing == Decimal("11765.34")
    assert row.incoming == Decimal("0")
    # Счёт не «съел» разряды суммы.
    assert row.account == "BY79 ALFA 3012 2399 3600 5027 0000"
    assert row.unp == "192775574"
    # Наименование берётся только когда сумма уже прочитана.
    assert row.name == 'ООО "ТД КОМПЛЕКТ"'


def test_statement_ignores_page_header_inside_operation(store):
    """v0.72.0: колонтитул страницы не попадает в наименование.

    Ссылку вида «https://icb.asb.by/…» pypdf вставляет и внутрь
    текста выписки. Если разрыв страницы придётся на операцию, ссылка
    шла бы первой строкой после сумм — и стала бы названием
    контрагента.
    """

    from decimal import Decimal

    from oplata.statement import parse_statement_text

    text = (
        "ВЫПИСКА ИЗ ЛИЦЕВОГО СЧЕТА ЗА 14.10.2026\n"
        "Счет клиента: BY47 AKBB 3012 1994 5068 3420 0000\n"
        "Наименование счета: ООО МирСтройМаркет\n"
        "Входящее сальдо: 48 623,65BYN\n"
        "14.10.2026 789 1 Счет: BY79 ALFA 3012 2399 3600 5027 0000\n"
        "БИК: ALFABY2X УНП: 192775574\n"
        "160,95 0,00\n"
        "https://icb.asb.by/Temp/812a9080-350e-4112-b20a.html"
        "[13.11.2026 11:29:48]\n"
        'ООО "ТД КОМПЛЕКТ"\n'
        "Назначение платежа: ЗА ИНСТРУМ. СОГЛ.АКТА СВЕРКИ\n"
        "Фактическая дата и время совершения операции: 14.10.2026 08:35:27\n"
    )
    statement = parse_statement_text(text)
    row = statement.rows[0]
    assert row.outgoing == Decimal("160.95")
    assert row.name == 'ООО "ТД КОМПЛЕКТ"'
    assert "http" not in row.name
