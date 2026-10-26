# AGENTS.md — инструкция для любого агента/разработчика, продолжающего проект

> Прочитай этот файл первым. Дальше по порядку: `docs/SPEC.md` →
> `docs/ARCHITECTURE.md` → `docs/ROADMAP.md` → `docs/DECISIONS.md` →
> `CHANGELOG.md`. После этого ты знаешь о проекте всё.

## Что это

«Оплаты — Строймаркет» — настольная Windows-программа учёта оплат
поставщикам/покупателям. Интерфейс повторяет макеты пользователя
(`docs/mockups/*.png`, описание — `docs/SPEC.md`).

## ⛔ Главное правило: формул пока НЕТ

Пользователь (27.10.2026): «формулы будем писать позже, пока делаем
только оболочку». Поэтому:

- Все суммы на экранах (`0,00 руб.`) — заглушки из `src/oplata/calc.py`.
- **Не придумывай формулы задолженности/просрочки/сроков сам.**
  Когда пользователь даст формулы — реализуй их **только** в `calc.py`
  (экраны уже вызывают его функции) и замени тест
  `test_calc_is_stub_zeros` тестами формул.
- Открытые вопросы к будущим формулам собраны в `docs/ROADMAP.md`, этап 1.

## Правило документирования (требование пользователя)

Каждое изменение обязано оставлять след, чтобы следующий агент мог продолжить:

1. `CHANGELOG.md` — запись в `[Unreleased]` (что/зачем), при релизе — новая версия.
2. Изменилось поведение экрана → поправь `docs/SPEC.md`.
3. Принял архитектурное решение → новый ADR в `docs/DECISIONS.md`.
4. Закрыл/добавил задачу → обнови `docs/ROADMAP.md`.
5. Поднял версию → синхронно в `src/oplata/__init__.py` **и** `pyproject.toml`.

## Стек в двух словах

Python 3.11+ + PySide6-Essentials (Qt) → PyInstaller собирает один
`Oplata.exe`. Сборка автоматически на GitHub Actions
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
.venv/bin/pyinstaller --noconfirm oplata.spec              # -> dist/Oplata.exe (на Windows)
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
- Готовый exe для пользователя: артефакт запуска Actions либо Release (тег `v*`).

## Definition of Done для любой задачи

- [ ] Тесты зелёные (`pytest -q`), `--self-test` возвращает 0.
- [ ] Если менялся UI — пересняты скриншоты (`scripts/make_screenshots.py`) и сверены с макетами.
- [ ] `CHANGELOG.md` (+ `SPEC/ROADMAP/DECISIONS` по ситуации) обновлены.
- [ ] Пуш в ветку сессии, GitHub Actions зелёный, пользователю сказано, где скачать exe.
