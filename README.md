# Оплаты — Строймаркет

Настольная программа под **Windows** для учёта оплат поставщикам и покупателям:
сводка задолженности, отдельный журнал оплат, недельные таблицы поставщиков
«Приход / Долг / Оплата» и покупателей «Реализация / Оплата», календарь
рабочих дней. Интерфейс — по макетам в `docs/mockups/`.

> **Статус: v0.8.0 — поставки и реализации, срок оплаты и долг поставщиков,
> экран «Оплаты», печать «Сводки» и настраиваемый рабочий календарь;
> Windows-сборка основным вариантом идёт папкой.**
> В шапке порядок вкладок: «Сводка», «Оплаты», «Поставщики»,
> «Покупатели», «Данные»; прежняя вкладка «Операции» удалена. Суммы
> вводятся прямо в ячейках: у поставщика «Приход» считает срок оплаты и
> ставит сумму в архивный «Долг», у покупателя «Реализация» срока не
> имеет. Клик по поставщику открывает «Поставку», клик по покупателю —
> аналогичное окно «Реализация» без поля срока. Во вкладке «Данные»
> выходные отмечаются красным, рабочие переносы — серым, а результат
> применяется кнопкой «Сохранить». В недельных таблицах выходные и
> праздники розовые; у текущей даты салатовой остаётся только верхняя
> ячейка вида «27 вт», остальные ячейки дня не красятся. На экране
> «Оплаты» оплату можно добавить, исправить или удалить. Пока заглушки:
> цифры «Сводки» и долг покупателей.

![Сводка](docs/screenshots/summary.png)

## Скачать готовую программу (Windows)

1. GitHub → вкладка **Actions** → последний зелёный запуск **Build Windows**.
2. В блоке **Artifacts** скачайте **`Oplata-windows-folder-v…`**
   (рекомендуемый вариант) → распаковать всю папку → запускать
   `Oplata\Oplata.exe`. Не вытаскивайте EXE из папки отдельно — рядом лежат
   нужные DLL; на рабочий стол можно вынести ярлык.
3. Если нужен именно один файл, запасной
   `Oplata-windows-single-file-v…` публикуется в Releases по тегам
   `v*` (в обычных запусках для ускорения собирается только folder).

Подробнее (включая блокировку загрузки браузером и SmartScreen):
[docs/BUILD.md](docs/BUILD.md).

## Экраны

| Экран | Макет пользователя | Текущий вид |
|---|---|---|
| Сводка | [01-summary.png](docs/mockups/01-summary.png) | [summary.png](docs/screenshots/summary.png) |
| Печатная «Сводка» | кнопка «Печать» на макете 01 | [summary-print.png](docs/screenshots/summary-print.png) |
| Поставщики | макет из 1-го сообщения (см. SPEC §2) | [suppliers.png](docs/screenshots/suppliers.png) |
| Покупатели | [03-customers-week.png](docs/mockups/03-customers-week.png) | [customers.png](docs/screenshots/customers.png) |
| Оплаты | на основе [04-operations.png](docs/mockups/04-operations.png) | [payments.png](docs/screenshots/payments.png) |
| Данные | [05-data-calendar.png](docs/mockups/05-data-calendar.png) | [data.png](docs/screenshots/data.png) |
| Карточка поставщика | [06-supplier-card.png](docs/mockups/06-supplier-card.png) | [supplier-card.png](docs/screenshots/supplier-card.png) |
| Окно «Поставка» | [07-delivery-dialog.png](docs/mockups/07-delivery-dialog.png) | [delivery-dialog.png](docs/screenshots/delivery-dialog.png) |
| Окно «Реализация» | по образцу окна «Поставка» | [realization-dialog.png](docs/screenshots/realization-dialog.png) |
| Окно операции | — | [operation-dialog.png](docs/screenshots/operation-dialog.png) |

## Разработчикам и агентам

**Начинайте с [AGENTS.md](AGENTS.md)** — там правила проекта, карта кода и
команды. Документация: [SPEC](docs/SPEC.md) · [Архитектура](docs/ARCHITECTURE.md) ·
[Сборка](docs/BUILD.md) · [План и открытые вопросы](docs/ROADMAP.md) ·
[Решения (ADR)](docs/DECISIONS.md) · [Журнал изменений](CHANGELOG.md).

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python run.py            # запуск из исходников
.venv/bin/python -m pytest -q      # тесты
```

Стек: Python 3.11+ · PySide6 (Qt) · PyInstaller → основная сборка папкой
(`Oplata\Oplata.exe` + DLL) и запасная одним файлом; обе собираются
автоматически в GitHub Actions на `windows-latest`.
Данные: `%APPDATA%\OplataStroymarket\data.json`.
