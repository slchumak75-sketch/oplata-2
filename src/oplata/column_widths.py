"""Ширины колонок, которые настроил пользователь (v0.75.0).

Пользователь тянет границы столбцов мышью прямо в таблице и нажимает
«Сохранить поля» в «Конфигурации». Ширины пишутся в свой файл рядом с
данными — ``%APPDATA%\\OplataStroymarket\\column-widths.json`` — потому
что это настройка **этого** компьютера: у коллег другие экраны, и
общая копия на Диске им мешала бы (как и ``current-user.json``).

Файл хранит ширины в пикселях вместе с шириной таблицы, при которой
их подобрали: на другом окне (или после переключения базы) ширины
масштабируются пропорционально.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import storage

FILE_NAME = "column-widths.json"


def path() -> Path:
    """Путь к файлу сохранённых ширин."""

    return storage.data_dir() / FILE_NAME


def load_all() -> dict:
    """Прочитать все сохранённые ширины; пустой словарь, если файла нет."""

    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_all(data: dict) -> None:
    """Записать все ширины разом (вызывается кнопкой «Сохранить поля»)."""

    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load(key: str, columns: int) -> list[int] | None:
    """Ширины таблицы ``key``; ``None``, если их не настраивали.

    Число колонок сверяем: у таблицы поставщиков и покупателей оно
    разное, а после смены версии могло измениться и у той же самой.
    """

    saved = load_all().get(key)
    if not isinstance(saved, dict):
        return None
    widths = saved.get("columns")
    viewport = saved.get("viewport")
    if not isinstance(widths, list) or not isinstance(viewport, int):
        return None
    if len(widths) != columns or viewport <= 0:
        return None
    # Ширина 0 допустима: так Qt хранит скрытую колонку (например,
    # «НДС» в блоке «Поступление»). Важно только, чтобы что-то было.
    if not all(isinstance(item, int) and item >= 0 for item in widths):
        return None
    if sum(widths) <= 0:
        return None
    return list(widths)


def load_entry(key: str, columns: int) -> tuple[int, list[int]] | None:
    """Сохранённые ширины и ширина таблицы, при которой их подобрали."""

    saved = load_all().get(key)
    if not isinstance(saved, dict):
        return None
    viewport = saved.get("viewport")
    if not isinstance(viewport, int) or viewport <= 0:
        return None
    widths = load(key, columns)
    if widths is None:
        return None
    return viewport, widths


def save(key: str, viewport: int, widths: list[int]) -> None:
    """Записать ширины одной таблицы, остальные оставить как были."""

    data = load_all()
    data[key] = {"viewport": viewport, "columns": list(widths)}
    save_all(data)


def drop(key: str) -> None:
    """Убрать сохранённые ширины таблицы (вернуть расчёт по умолчанию)."""

    data = load_all()
    if key in data:
        del data[key]
        save_all(data)
