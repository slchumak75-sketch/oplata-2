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
from collections.abc import Callable
from pathlib import Path

from .models import AppData, seed_data

APP_DIR_NAME = "OplataStroymarket"
DATA_FILE_NAME = "data.json"
STATE_FILE_NAME = "state.json"

# v0.17.0: две не связанные между собой базы — МИР и КОРЕЛИЧИ. У каждой
# свой файл данных: поставщики, покупатели, операции, поступления,
# оплаты, кассы и календарь не пересекаются. База МИР — прежний
# data.json, поэтому уже введённые данные остаются на месте.
BASE_MIR = "mir"
BASE_KORELICHI = "korelichi"
BASE_TITLES = {BASE_MIR: "МИР", BASE_KORELICHI: "КОРЕЛИЧИ"}
BASE_FILES = {BASE_MIR: "data.json", BASE_KORELICHI: "data-korelichi.json"}
# Переключатель ровно двух баз: нажатие всегда даёт «другую».
OTHER_BASE = {BASE_MIR: BASE_KORELICHI, BASE_KORELICHI: BASE_MIR}
DEFAULT_BASE = BASE_MIR


def data_dir() -> Path:
    """Каталог данных (общий для всех баз)."""
    override = os.environ.get("OPLATA_DATA_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:  # Windows
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def data_path_for(base_key: str) -> Path:
    """Путь к файлу данных выбранной базы."""
    return data_dir() / BASE_FILES.get(base_key, BASE_FILES[DEFAULT_BASE])


def default_data_path() -> Path:
    return data_path_for(DEFAULT_BASE)


def state_path() -> Path:
    return data_dir() / STATE_FILE_NAME


def load_active_base() -> str:
    """Какая база была активна при последнем запуске (по умолчанию МИР)."""
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return DEFAULT_BASE
    key = raw.get("active_base") if isinstance(raw, dict) else None
    return key if key in BASE_FILES else DEFAULT_BASE


def save_active_base(base_key: str) -> None:
    """Запомнить активную базу, чтобы при следующем запуске открыть её."""
    try:
        state_path().parent.mkdir(parents=True, exist_ok=True)
        state_path().write_text(
            json.dumps({"active_base": base_key}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        # Незаписанный выбор базы не должен ронять программу.
        pass


class DataStore:
    """Загрузка/сохранение AppData. При первом запуске — демо-данные."""

    def __init__(self, path: Path | None = None, base_key: str | None = None) -> None:
        # v0.17.0: активная база запоминается между запусками; явный path
        # (тесты) важнее remembered-выбора.
        self.base_key = base_key or load_active_base()
        self.path = path or data_path_for(self.base_key)
        # Подписчики на сохранение (v0.15.0): «Сводка» должна
        # пересчитываться сразу после ввода, а не только при смене даты.
        self.on_saved: list[Callable[[], None]] = []
        self.data: AppData = self._load()

    def switch_base(self, base_key: str) -> None:
        """Переключить активную базу: сохранить текущую, загрузить другую.

        Объект остаётся тем же, поэтому все экраны, держащие ссылку на
        ``store``, увидят новые данные после перерисовки (v0.17.0).
        """
        if base_key not in BASE_FILES or base_key == self.base_key:
            return
        self.save()
        self.base_key = base_key
        self.path = data_path_for(base_key)
        self.data = self._load()
        save_active_base(base_key)
        # «Сводка» и прочие подписчики пересчитываются по новой базе.
        for callback in list(self.on_saved):
            callback()

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
        # «Сводка» пересчитывается сразу после любого сохранения данных.
        for callback in list(self.on_saved):
            callback()
