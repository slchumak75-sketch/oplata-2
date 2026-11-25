"""Настройки **этого компьютера** для обновлений (v0.87.0).

Раньше локальным был только выбор пользователя (``current-user.json``,
v0.28.0). Для обновлений нужны ещё два значения, и оба — про компьютер,
а не про человека:

* **тестовый ли это компьютер** — на нём разрешена раздача обновлений
  (просьба пользователя: галочка ставится в таблице «Пользователи и
  права», но распространяется **только на тот компьютер**, где стоит);
* **папка обновлений на Google Диске** — путь, найденный bat-файлом или
  указанный вручную. Храним, чтобы второй раз не искать.

Почему не в общем ``users.json``: он лежит на Диске и один на все
компьютеры. Галочка, положенная туда, включила бы раздачу сразу у всех
— ровно та дыра, из-за которой выбор пользователя сделали локальным.
Внешне всё как просил пользователь: столбец в таблице пользователей,
флажок у нужной строки; просто рядом подписано «на этом компьютере».

Файл — ``OPLATA_LOCAL_DIR`` или ``%APPDATA%/OplataStroymarket/
local-settings.json``. Формат простой: ``{"test_computer": true,
"test_user": "manager", "updates_dir": "G:/Мой диск/Oplata"}``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

FILE_NAME = "local-settings.json"

# Ключи файла.
KEY_TEST = "test_computer"      # True — с этого компьютера можно раздавать
KEY_TEST_USER = "test_user"     # id пользователя, за которым закреплена галочка
KEY_UPDATES_DIR = "updates_dir"  # папка базы на Диске (её выбирает bat)


def settings_path() -> Path:
    """Файл локальных настроек этого компьютера."""

    override = os.environ.get("OPLATA_LOCAL_DIR")
    if override:
        base = Path(override)
    else:
        appdata = os.environ.get("APPDATA")
        if appdata:  # Windows
            base = Path(appdata) / "OplataStroymarket"
        else:
            base = Path.home() / ".local" / "share" / "OplataStroymarket"
    return base / FILE_NAME


def _read() -> dict:
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(payload: dict) -> None:
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # Незаписанная настройка не должна ронять программу.
        pass


def is_test_computer() -> bool:
    """Разрешена ли раздача обновлений с этого компьютера."""

    return bool(_read().get(KEY_TEST))


def test_user_id() -> str:
    """Пользователь, за которым закреплена галочка (пусто — любой)."""

    return str(_read().get(KEY_TEST_USER) or "")


def set_test_computer(enabled: bool, user_id: str = "") -> None:
    """Включить или выключить раздачу обновлений с этого компьютера."""

    payload = _read()
    payload[KEY_TEST] = bool(enabled)
    if enabled and user_id:
        payload[KEY_TEST_USER] = str(user_id)
    elif not enabled:
        payload.pop(KEY_TEST_USER, None)
    _write(payload)


def can_publish(current_user_id: str) -> bool:
    """Может ли **сейчас** этот компьютер раздавать обновления.

    Нужны оба условия: галочка на компьютере и тот же пользователь,
    который отметил её (или галочка «ни за кем не закреплена» — так
    бывает после ручной правки файла).
    """

    if not is_test_computer():
        return False
    owner = test_user_id()
    return not owner or owner == str(current_user_id)


def updates_dir_hint() -> str:
    """Где лежит папка обновлений на Диске (для подсказок в окне)."""

    return str(_read().get(KEY_UPDATES_DIR) or "")


def set_updates_dir(path: str | Path | None) -> None:
    """Запомнить папку базы на Диске (передаёт bat-файл или выбор вручную)."""

    payload = _read()
    if path:
        payload[KEY_UPDATES_DIR] = str(path)
    else:
        payload.pop(KEY_UPDATES_DIR, None)
    _write(payload)
