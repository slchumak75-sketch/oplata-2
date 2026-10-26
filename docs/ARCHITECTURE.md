# ARCHITECTURE.md — как устроена программа

## Стек

| Слой | Выбор | Почему (подробно: `DECISIONS.md`) |
|---|---|---|
| Язык | Python 3.11+ | Читаемость для любого агента, разработка и тесты без Windows |
| GUI | PySide6-Essentials (Qt 6) | Современный вид «как на макетах», нативный Windows-рендер, шрифт Segoe UI |
| Упаковка | PyInstaller (`oplata.spec`) | Один `Oplata.exe` без установки Python |
| Данные (этап 0) | Один JSON-файл | Просто; объёмов пока нет (ADR-002, потом вероятен SQLite) |
| CI/CD | GitHub Actions, `windows-latest` | Exe собирается сам при каждом пуше (`BUILD.md`) |
| Тесты | pytest (ядро без Qt + offscreen-smoke UI) | Быстрые, запускаются в Linux-джобе |

## Структура репозитория

```text
oplata-2/
  run.py                        # вход: python run.py [--self-test]; его же собирает PyInstaller
  oplata.spec                   # рецепт сборки Oplata.exe (onefile, windowed)
  pyproject.toml                # метаданные, зависимости, настройки pytest
  requirements.txt / -dev.txt   # runtime / + pytest, pyinstaller
  AGENTS.md                     # правила для продолжающих (читать первым)
  CHANGELOG.md                  # журнал ВСЕХ изменений
  docs/
    SPEC.md  ARCHITECTURE.md  BUILD.md  ROADMAP.md  DECISIONS.md
    mockups/                    # исходные макеты пользователя
    screenshots/                # рендеры текущей оболочки (scripts/make_screenshots.py)
  scripts/
    build_windows.bat           # локальная сборка exe на Windows
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
    calc.py        [без Qt]     # ⚠️ ЗАГЛУШКИ формул (нули) — сюда писать формулы позже
    views/
      summary_view.py           # Сводка
      week_view.py              # Поставщики и Покупатели (kind="supplier"/"customer")
      operations_view.py        # Операции (журнал + фильтры)
      data_view.py              # Данные (календарь выходных на год)
    dialogs/
      counterparty_card.py      # Карточки поставщика/покупателя
  tests/
    test_core.py                # даты, модели, storage, calc-заглушки
    test_ui_smoke.py            # окно строится, 5 экранов, карточка добавляет
  .github/workflows/build-windows.yml
```

## Поток данных

```text
data.json ──DataStore──> AppData ──> экраны (views) ──клики/ввод──> диалоги
    ^                                                    │
    └────────────────── DataStore.save() <───────────────┘
                             
экраны ──спрашивают суммы──> calc.py ──(этап 0: нули)──> отображение "0,00 руб."
                             (этап формул: здесь появятся расчёты,
                              сигнатуры функций уже зафиксированы)
```

- `MainWindow.refresh_all()` перестраивает экраны после правок в карточках.
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
  "operations": [],
  "day_overrides": {"2026-07-18": "off"}
}
```

Путь: Windows `%APPDATA%\OplataStroymarket\data.json`; переопределение —
переменная `OPLATA_DATA_DIR` (используется в тестах/отладке).

## Контракт calc.py (зафиксирован на этапе 0)

`summary_totals(on_date)`, `supplier_debt_row(supplier_id, on_date)`,
`overdue_payments(on_date)`, `week_debt(week_monday)`,
`week_cell_value(counterparty_id, day, "income"|"debt"|"payment")`.
Экраны менять при внедрении формул не придётся — только этот модуль
и, вероятно, сигнатуры дополнятся параметром `operations`/`data`.

## Принципы

1. **Ядро без Qt** (`dates/models/storage/calc`) — тесты ядра не требуют GUI.
2. **Деньги — `Decimal`**, в JSON — строками; `float` для сумм запрещён.
3. **Один источник поведения экранов — макеты**; отклонения фиксируются в SPEC.md.
4. **Всё документируется** в том же коммите, что и код (см. AGENTS.md).
