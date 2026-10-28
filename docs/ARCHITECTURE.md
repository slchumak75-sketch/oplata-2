# ARCHITECTURE.md — как устроена программа

## Стек

| Слой | Выбор | Почему (подробно: `DECISIONS.md`) |
|---|---|---|
| Язык | Python 3.11+ | Читаемость для любого агента, разработка и тесты без Windows |
| GUI | PySide6-Essentials (Qt 6) | Современный вид «как на макетах», нативный Windows-рендер, шрифт Segoe UI |
| Упаковка | PyInstaller (`oplata.spec`) | Основная папка `Oplata` (onedir) без установки Python; запасной один `Oplata.exe` (onefile); ADR-010 |
| Данные (этап 0) | Один JSON-файл | Просто; объёмов пока нет (ADR-002, потом вероятен SQLite) |
| CI/CD | GitHub Actions, `windows-latest` | Exe собирается сам при каждом пуше (`BUILD.md`) |
| Тесты | pytest (ядро без Qt + offscreen-smoke UI) | Быстрые, запускаются в Linux-джобе |

## Структура репозитория

```text
oplata-2/
  run.py                        # вход: python run.py [--self-test]; его же собирает PyInstaller
  oplata.spec                   # PyInstaller: folder/onedir (основной) и single/onefile (запасной), windowed
  pyproject.toml                # метаданные, зависимости, настройки pytest
  requirements.txt / -dev.txt   # runtime / + pytest, pyinstaller
  AGENTS.md                     # правила для продолжающих (читать первым)
  CHANGELOG.md                  # журнал ВСЕХ изменений
  docs/
    SPEC.md  ARCHITECTURE.md  BUILD.md  ROADMAP.md  DECISIONS.md
    mockups/                    # исходные макеты пользователя
    screenshots/                # рендеры текущей оболочки (scripts/make_screenshots.py)
  packaging/
    START_HERE_RU.txt           # инструкция первого запуска в обоих Windows-пакетах
  scripts/
    build_windows.bat           # локальная сборка exe на Windows (folder по умолчанию, single — аргументом)
    write_version_info.py       # VS_VERSION_INFO для Oplata.exe из версии приложения
    make_screenshots.py         # переснять docs/screenshots
  src/oplata/
    __init__.py                 # __version__ (синхронно с pyproject.toml!)
    main.py                     # QApplication, --self-test
    app.py                      # MainWindow: шапка + QStackedWidget из 5 экранов
    styles.py                   # цвета по макетам + QSS
    widgets.py                  # панели, цветные кнопки, поля даты/сумм, KPI-карточки
    dates.py       [без Qt]     # недели Пн–Вс, русские месяцы, формат сумм
    models.py      [без Qt]     # Counterparty, Operation, CashReceipt, AppData, YearStart, seed_data()
    storage.py     [без Qt]     # DataStore: JSON в %APPDATA%\OplataStroymarket\data.json
    styles.py      [без Qt]     # цвета и QSS; FONT_SCALE — шрифты интерфейса +30% (v0.12.0)
    widgets.py                 # AmountLineEdit, CollapsiblePanel, RowHoverMixin (подсветка строки v0.13.0), arrow_button
    calc.py        [без Qt]     # срок/долг поставщиков; блоки «Сводки» (средства, долги, месяц)
    reports.py     [без Qt]     # HTML печатной формы «Сводки» из экранного снимка
    views/
      week_view.py              # Поставщики и Покупатели (kind="supplier"/"customer")
      receipts_view.py          # Поступление и оплаты: слева блоки-таблицы (ввод в ячейки, окно справа), справа плашка «Сводка»
                                 # свои строки блока «Оплата», «Прочие поступления», колонка «Квартал»
      operations_view.py        # Журнал операций (окно из карточки контрагента)
      summary_view.py            # Плашка «Сводка»: три блока построчно (средства, долги, месяц) + печать
      data_view.py              # Конфигурация: «Настройка календаря» + «Данные»
                              # (перенос остатков на 1 января, v0.14.0) + «Администрирование»
    dialogs/
      counterparty_card.py      # Карточки поставщика/покупателя
      operations_journal_dialog.py  # Окно «Операции контрагента» (журнал)
      delivery_dialog.py        # Окно «Поставка» (дата, сумма с НДС, срок оплаты)
      realization_dialog.py     # Окно «Реализация» покупателя (без срока)
      operation_dialog.py       # Добавление/редактирование операции/оплаты
  tests/
    test_core.py                # даты, модели, storage, срок и архивный долг
    test_ui_smoke.py            # 5 экранов, поставка, ввод ячеек, поле суммы, блоки поступлений,
                                # плашки «Данных», календарь, CRUD журнала, печать PDF
    test_reports.py             # HTML печатной «Сводки»: KPI, таблица, escaping, пустое состояние
    test_packaging.py           # Windows VS_VERSION_INFO и синхронность версий
  .github/workflows/build-windows.yml
```

## Поток данных

```text
data.json ──DataStore──> AppData ──> экраны (views) ──клики/ввод──> диалоги
    ^                                                    │
    └────────────────── DataStore.save() <───────────────┘
                             
экраны ──спрашивают суммы──> calc.py ──> срок оплаты и долг поставщиков (v0.3.0);
                             блоки «Сводки» считаются простыми суммами
```

- `MainWindow.refresh_all()` перестраивает экраны после правок в карточках.
- После CRUD в журнале `OperationsView` перечитывает себя и вызывает
  `MainWindow.refresh_after_operation_change()` (недельные таблицы +
  «Сводка» + блок «Оплата» на вкладке «Поступление и оплаты»).
- Экран «Данные» от контрагентов не зависит и при `refresh_all()` не трогается.
  Его календарные клики меняют только `_pending_overrides`; кнопка
  «Сохранить» пишет `day_overrides`, вызывает `store.save()` и через
  `MainWindow.refresh_after_calendar_change()` обновляет обе недельные
  таблицы и «Сводку».
- `WeekView` перестраивает таблицу целиком при смене недели/списка —
  объёмы этапа оболочки это позволяют.

## Формат data.json (schema 1)

```json
{
  "schema": 1,
  "suppliers": [
    {"name": "ггг", "id": "a1b2c3d4e5f6", "deferment_days": 10,
     "prepayment": false, "realization": false, "archived": false}
  ],
  "customers": [],
  "operations": [
    {"kind": "Приход", "counterparty_id": "a1b2c3d4e5f6", "date": "2026-10-26",
     "amount": "88888.88", "document": "", "comment": "", "due_date": "2026-11-05"}
  ],
  "day_overrides": {"2026-07-18": "off"}
}
```

Путь: Windows `%APPDATA%\OplataStroymarket\data.json`; переопределение —
переменная `OPLATA_DATA_DIR` (используется в тестах/отладке).

## Контракт calc.py

Заглушки «Сводки»: `summary_totals`, `supplier_debt_row`, `overdue_payments`.
Формулы поставщиков (v0.3.0): `payment_due_date(delivery, counterparty,
day_overrides)` (дата + отсрочка, режимы без отсрочки, перенос с выходного),
`debt_by_due_date(data, counterparty)` (архивные суммы поставок по срокам;
оплата их пока не уменьшает — ADR-008),
`week_debt(monday, data, counterparties)`, `overdue_amount(data, cp, today)`,
`week_cell_amount/value(..., operations, data)` (`debt` считается только
при переданном `data`).

Ввод: «Поставка» → `models.add_delivery` (+ `due_date`) → save →
`WeekView.goto_date(срок)`; ячейки «Приход»/«Оплата» →
`WeekView._on_item_changed` → `dates.parse_money` →
`models.set_daily_operation` (для «Прихода» срок даёт тот же
`calc.payment_due_date` по сохранённому календарю) → save →
`WeekView._update_calculated_cells` (Долг/«на сегодня»/«за неделю»/ВСЕГО
без перестройки таблицы) + `MainWindow.refresh_after_week_edit`.
Редактор денежных ячеек создаёт делегат `WeekView` на базе
`widgets.AmountLineEdit`: нулевое значение очищается при фокусе,
ненулевое выделяется целиком.
Ручная правка: `OperationsView` → `OperationDialog` → изменение того же
`Operation` (+ политика срока из ADR-009) → save → обновление журнала и
`MainWindow.refresh_after_operation_change`; удаление идёт тем же путём
после подтверждения.

## Принципы

1. **Ядро без Qt** (`dates/models/storage/calc`) — тесты ядра не требуют GUI.
2. **Деньги — `Decimal`**, в JSON — строками; `float` для сумм запрещён.
3. **Один источник поведения экранов — макеты**; отклонения фиксируются в SPEC.md.
4. **Всё документируется** в том же коммите, что и код (см. AGENTS.md).
