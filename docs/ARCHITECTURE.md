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
    widgets.py                  # панели, цветные кнопки, поля даты, KPI-карточки
    dates.py       [без Qt]     # недели Пн–Вс, русские месяцы, формат сумм
    models.py      [без Qt]     # Counterparty, Operation, AppData, seed_data()
    storage.py     [без Qt]     # DataStore: JSON в %APPDATA%\OplataStroymarket\data.json
    calc.py        [без Qt]     # срок/долг поставщиков; «Сводка» пока заглушки
    views/
      summary_view.py           # Сводка
      week_view.py              # Поставщики и Покупатели (kind="supplier"/"customer")
      operations_view.py        # Операции (журнал, фильтры, CRUD)
      data_view.py              # Данные (календарь выходных на год)
    dialogs/
      counterparty_card.py      # Карточки поставщика/покупателя
      delivery_dialog.py        # Окно «Поставка» (дата, сумма с НДС, срок оплаты)
      operation_dialog.py       # Добавление/редактирование операции журнала
  tests/
    test_core.py                # даты, модели, storage, срок и архивный долг
    test_ui_smoke.py            # 5 экранов, поставка, ввод ячеек, CRUD журнала, карточки
    test_packaging.py           # Windows VS_VERSION_INFO и синхронность версий
  .github/workflows/build-windows.yml
```

## Поток данных

```text
data.json ──DataStore──> AppData ──> экраны (views) ──клики/ввод──> диалоги
    ^                                                    │
    └────────────────── DataStore.save() <───────────────┘
                             
экраны ──спрашивают суммы──> calc.py ──> срок оплаты и долг поставщиков (v0.3.0);
                             «Сводка» пока получает нули-заглушки
```

- `MainWindow.refresh_all()` перестраивает экраны после правок в карточках.
- После CRUD в журнале `OperationsView` перечитывает себя и вызывает
  `MainWindow.refresh_after_operation_change()` (недельные таблицы + «Сводка»).
- Экран «Данные» от контрагентов не зависит и при `refresh_all()` не трогается.
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
`WeekView.goto_date(срок)`; ячейка «Оплата» → `WeekView._on_item_changed` →
`dates.parse_money` → `models.set_daily_operation` → save →
`WeekView._update_calculated_cells` (Долг/«на сегодня»/«за неделю»/ВСЕГО
без перестройки таблицы) + `MainWindow.refresh_after_week_edit`.
Ручная правка: `OperationsView` → `OperationDialog` → изменение того же
`Operation` (+ политика срока из ADR-009) → save → обновление журнала и
`MainWindow.refresh_after_operation_change`; удаление идёт тем же путём
после подтверждения.

## Принципы

1. **Ядро без Qt** (`dates/models/storage/calc`) — тесты ядра не требуют GUI.
2. **Деньги — `Decimal`**, в JSON — строками; `float` для сумм запрещён.
3. **Один источник поведения экранов — макеты**; отклонения фиксируются в SPEC.md.
4. **Всё документируется** в том же коммите, что и код (см. AGENTS.md).
