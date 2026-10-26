"""Хранение данных оболочки: один JSON-файл (без Qt).

Решение этапа 0 (ADR-002 в docs/DECISIONS.md): пока нет формул и больших
объёмов, достаточно JSON. На этапе операций, скорее всего, переедем на
SQLite — тогда меняется только этот модуль, экраны работают с AppData.

Путь к файлу по умолчанию:
* Windows: ``%APPDATA%\\OplataStroymarket\\data.json``
* Linux/macOS (только для разработки): ``~/.local/share/OplataStroymarket/data.json``
* Переопределение для тестов/отладки: переменная окружения ``OPLATA_DATA_DIR``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import AppData, seed_data

APP_DIR_NAME = "OplataStroymarket"
DATA_FILE_NAME = "data.json"


def default_data_path() -> Path:
    override = os.environ.get("OPLATA_DATA_DIR")
    if override:
        return Path(override) / DATA_FILE_NAME
    appdata = os.environ.get("APPDATA")
    if appdata:  # Windows
        return Path(appdata) / APP_DIR_NAME / DATA_FILE_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME / DATA_FILE_NAME


class DataStore:
    """Загрузка/сохранение AppData. При первом запуске — демо-данные."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_data_path()
        self.data: AppData = self._load()

    def _load(self) -> AppData:
        if not self.path.exists():
            data = seed_data()
            self.data = data
            self.save()
            return data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return AppData.from_dict(raw)
        except (OSError, ValueError, TypeError):
            # Битый файл не должен ронять оболочку: начинаем с демо-данных,
            # старый файл не трогаем до первого save() из UI.
            # TODO(этап данных): показать пользователю предупреждение и бэкап.
            return seed_data()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.data.to_dict(), ensure_ascii=False, indent=2)
        self.path.write_text(text, encoding="utf-8")
