# AGENTS.md — инструкция для любого агента/разработчика, продолжающего проект

> Прочитай этот файл первым. Дальше по порядку: `docs/SPEC.md` →
> `docs/ARCHITECTURE.md` → `docs/ROADMAP.md` → `docs/DECISIONS.md` →
> `CHANGELOG.md`. После этого ты знаешь о проекте всё.

## Что это

«Оплаты — Строймаркет» — настольная Windows-программа учёта оплат
поставщикам/покупателям. Интерфейс повторяет макеты пользователя
(`docs/mockups/*.png`, описание — `docs/SPEC.md`).

## ⛔ Главное правило: формулы — только те, что дал пользователь

Сначала была «только оболочка», 27.10.2026 пользователь начал давать
формулы по частям. Статус:

- ✅ Реализовано по ТЗ (v0.3.0, таблица «Поставщики»): срок оплаты
  (дата + отсрочка, режимы, перенос с выходного), «Долг», «на сегодня»,
  «за неделю» — всё в `src/oplata/calc.py`, правила расписаны в его
  шапке и в `docs/SPEC.md` п. 2/6. Не откатывать в заглушки!
- ✅ Ручной CRUD операций в журнале (v0.4.0, ADR-009): формулы при этом
  не менялись; сохранённый срок поставки при мелких правках не пересчитывать.
- 🔶 По-прежнему заглушки: вся «Сводка», долг покупателей.
- **Не придумывай недостающие формулы сам** — открытые вопросы в
  `docs/ROADMAP.md` (этап 1) и `SPEC.md` (Q1–Q9); предположения
  (например, «оплата пока не уменьшает долг» из ADR-008) — в коде и ADR.

## Правило документирования (требование пользователя)

Каждое изменение обязано оставлять след, чтобы следующий агент мог продолжить:

1. `CHANGELOG.md` — запись в `[Unreleased]` (что/зачем), при релизе — новая версия.
2. Изменилось поведение экрана → поправь `docs/SPEC.md`.
3. Принял архитектурное решение → новый ADR в `docs/DECISIONS.md`.
4. Закрыл/добавил задачу → обнови `docs/ROADMAP.md`.
5. Поднял версию → синхронно в `src/oplata/__init__.py` **и** `pyproject.toml`.

## Стек в двух словах

Python 3.11+ + PySide6-Essentials (Qt) → PyInstaller собирает два
Windows-варианта: основной папкой (`Oplata\Oplata.exe` + DLL, ADR-010)
и запасной одним файлом. Сборка автоматически на GitHub Actions
(`.github/workflows/build-windows.yml`, детали — `docs/BUILD.md`).
Обоснование стека — ADR-001 в `docs/DECISIONS.md`.

## Команды

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\pip

QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q   # тесты (Linux/CI)
.venv/bin/python run.py                                    # запуск из исходников
.venv/bin/python run.py --self-test                        # открыться и закрыться (код 0)
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/make_screenshots.py  # переснять скриншоты docs/
.venv/bin/python scripts/write_version_info.py             # Windows-метаданные EXE
.venv/bin/pyinstaller --noconfirm --clean oplata.spec      # -> dist/Oplata/Oplata.exe (на Windows; режим folder по умолчанию)
```

На Linux для Qt нужны системные библиотеки (`libegl1 libgl1 libxkbcommon0
libfontconfig1`) — в CI они ставятся шагом workflow.

## Карта кода («куда смотреть, если…»)

| Задача | Файл |
|---|---|
| Формулы (когда даст пользователь) | `src/oplata/calc.py` — и только он |
| Поля поставщика/операции | `src/oplata/models.py` (+ SPEC.md!) |
| Файл данных, формат JSON | `src/oplata/storage.py` |
| Даты/недели/формат денег | `src/oplata/dates.py` |
| Цвета/стили | `src/oplata/styles.py` |
| Шапка окна, навигация | `src/oplata/app.py` |
| Экран «Сводка» | `src/oplata/views/summary_view.py` |
| Экраны «Поставщики»/«Покупатели» | `src/oplata/views/week_view.py` (один класс, параметр `kind`) |
| Экран «Операции» | `src/oplata/views/operations_view.py` |
| Экран «Данные» (календарь) | `src/oplata/views/data_view.py` |
| Карточки поставщика/покупателя | `src/oplata/dialogs/counterparty_card.py` |
| Окно «Поставка» (ввод поставки, срок) | `src/oplata/dialogs/delivery_dialog.py` |
| Окно операции (ручной ввод/правка в журнале) | `src/oplata/dialogs/operation_dialog.py` (политика срока — ADR-009) |
| Сборка exe / CI | `oplata.spec`, `.github/workflows/build-windows.yml`, `docs/BUILD.md` |

## Соглашения

- UI-тексты — по-русски (как на макетах), идентификаторы и имена файлов — по-английски.
- Деньги в будущем считать только `Decimal`, никогда `float`. В хранилище суммы — строками.
- Ядро (`models`, `storage`, `calc`, `dates`) **без импортов Qt** — чтобы тесты были быстрыми без GUI.
- Даты в JSON — ISO `YYYY-MM-DD`; отображение пользователю — `дд.мм.гггг`.
- Не коммитить `build/`, `dist/`, `.venv/`, реальный `data.json` (см. `.gitignore`).

## Работа с GitHub

- Работай в ветке своей сессии (Arena: `arena/...`), не пушь в `main` напрямую.
- После пуша проверь сборку: `gh run list --branch <ветка>`, при падении — `gh run view <id> --log-failed`.
- Готовая программа для пользователя: основной артефакт
  `Oplata-windows-folder-v…` запуска Actions (запасной —
  `Oplata-windows-single-file-v…`) либо соответствующие ZIP в Release
  (тег `v*`).

## Definition of Done для любой задачи

- [ ] Тесты зелёные (`pytest -q`), `--self-test` возвращает 0.
- [ ] Если менялся UI — пересняты скриншоты (`scripts/make_screenshots.py`) и сверены с макетами.
- [ ] `CHANGELOG.md` (+ `SPEC/ROADMAP/DECISIONS` по ситуации) обновлены.
- [ ] Пуш в ветку сессии, GitHub Actions зелёный, пользователю сказано,
      где скачать основной `Oplata-windows-folder-v…`.
