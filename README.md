# Оплаты — Строймаркет

Настольная программа под **Windows** для учёта оплат поставщикам и покупателям:
сводка задолженности, недельные таблицы «Приход / Долг / Оплата», журнал
операций, календарь рабочих дней. Интерфейс — по макетам в `docs/mockups/`.

> **Статус: v0.3.0 — поставки, срок оплаты и долг поставщиков.** Клик по
> поставщику открывает окно «Поставка» (дата, сумма с НДС): программа
> считает срок оплаты (дата + отсрочка, перенос с выходного) и ставит
> сумму в «Долг» на этот срок; работают колонки «просрочено на сегодня»
> и «задолженность за неделю». «Оплата» вводится в ячейках, всё видно в
> журнале операций. Пока заглушки: цифры «Сводки» и долг покупателей.

![Сводка](docs/screenshots/summary.png)

## Скачать готовую программу (Windows)

1. GitHub → вкладка **Actions** → последний зелёный запуск **Build Windows**.
2. Блок **Artifacts** внизу → `Oplata-windows-v…` → распаковать → `Oplata.exe`.

Подробнее (включая окно SmartScreen при первом запуске): [docs/BUILD.md](docs/BUILD.md).

## Экраны

| Экран | Макет пользователя | Текущий вид |
|---|---|---|
| Сводка | [01-summary.png](docs/mockups/01-summary.png) | [summary.png](docs/screenshots/summary.png) |
| Поставщики | макет из 1-го сообщения (см. SPEC §2) | [suppliers.png](docs/screenshots/suppliers.png) |
| Покупатели | [03-customers-week.png](docs/mockups/03-customers-week.png) | [customers.png](docs/screenshots/customers.png) |
| Операции | [04-operations.png](docs/mockups/04-operations.png) | [operations.png](docs/screenshots/operations.png) |
| Данные | [05-data-calendar.png](docs/mockups/05-data-calendar.png) | [data.png](docs/screenshots/data.png) |
| Карточка поставщика | [06-supplier-card.png](docs/mockups/06-supplier-card.png) | [supplier-card.png](docs/screenshots/supplier-card.png) |
| Окно «Поставка» | [07-delivery-dialog.png](docs/mockups/07-delivery-dialog.png) | [delivery-dialog.png](docs/screenshots/delivery-dialog.png) |

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

Стек: Python 3.11+ · PySide6 (Qt) · PyInstaller → один `Oplata.exe`,
собирается автоматически в GitHub Actions на `windows-latest`.
Данные: `%APPDATA%\OplataStroymarket\data.json`.
