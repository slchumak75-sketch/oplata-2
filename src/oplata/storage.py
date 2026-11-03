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
import re
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from .models import AppData, seed_data

APP_DIR_NAME = "OplataStroymarket"
DATA_FILE_NAME = "data.json"
STATE_FILE_NAME = "state.json"

# v0.30.0: папка «Оплата база» лежит рядом с программой (ADR-082). В ней
# — выгрузки обеих баз в Excel: своя копия на каждом компьютере, чтобы
# данные были под рукой и их можно было перенести флешкой. Считаем путь
# от самой программы, а не от данных: данные могут жить в общей папке на
# Google Диске, а выгрузка — локальная.
LOCAL_BASE_DIR_NAME = "Оплата база"
LOCAL_BASE_NAME_TEMPLATE = "Оплата {day}.xlsx"
# Переопределение для тестов: без него выгрузки падали бы в репозиторий.
PROGRAM_DIR_ENV = "OPLATA_PROGRAM_DIR"
# «Оплата 02.11.2026.xlsx» — по дате в имени ищем самую свежую выгрузку.
LOCAL_BASE_NAME_RE = re.compile(r"^Оплата (\d{2})\.(\d{2})\.(\d{4})\.xlsx$")

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


def program_dir() -> Path:
    """Каталог самой программы (v0.30.0, ADR-082).

    Рядом с ним лежит папка «Оплата база» с выгрузками:

    * сборка папкой (``…\\Oplata\\Oplata.exe``) — каталог на уровень
      выше, то есть рядом с папкой ``Oplata``;
    * один EXE — каталог, где лежит сам файл;
    * запуск из исходников и тесты — переменная ``OPLATA_PROGRAM_DIR``,
      а без неё корень репозитория (на два уровня выше этого файла).
    """

    override = os.environ.get(PROGRAM_DIR_ENV)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):  # сборка PyInstaller
        exe = Path(sys.executable).resolve()
        # У сборки папкой sys._MEIPASS — каталог рядом с exe, у одного
        # файла — временный каталог распаковки. По этому признаку и
        # отличаем: для сборки папкой поднимаемся на уровень выше.
        raw_meipass = str(getattr(sys, "_MEIPASS", "") or "")
        if raw_meipass and Path(raw_meipass).resolve() == exe.parent:
            return exe.parent.parent
        return exe.parent
    return Path(__file__).resolve().parents[2]


def local_base_dir() -> Path:
    """Каталог «Оплата база» рядом с программой (v0.30.0)."""

    return program_dir() / LOCAL_BASE_DIR_NAME


def local_base_file(day: date | None = None) -> Path:
    """Путь выгрузки на дату: «Оплата 02.11.2026.xlsx» (v0.30.0).

    В один день имя одно и то же — файл перезаписывается, поэтому в
    папке лежит ровно одна выгрузка за день.
    """

    target = day or date.today()
    return local_base_dir() / LOCAL_BASE_NAME_TEMPLATE.format(
        day=target.strftime("%d.%m.%Y")
    )


def local_base_file_day(path: Path) -> date | None:
    """Дата из имени выгрузки; у посторонних файлов — ``None`` (v0.30.0)."""

    match = LOCAL_BASE_NAME_RE.match(path.name)
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:  # 31.02.2026 и прочие несуществующие даты
        return None


def latest_local_base_file() -> Path | None:
    """Самый свежий файл выгрузки в «Оплата база» (v0.30.0).

    Свежесть — по дате в имени, при равных датах — по времени
    изменения. Посторонние файлы (без даты в имени) не считаются:
    диалог загрузки не должен подставлять первый попавшийся xlsx.
    """

    try:
        entries = list(local_base_dir().iterdir())
    except OSError:  # папки ещё нет
        return None

    dated: list[tuple[date, float, Path]] = []
    for path in entries:
        if path.suffix.lower() != ".xlsx" or not path.is_file():
            continue
        day = local_base_file_day(path)
        if day is None:
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        dated.append((day, modified, path))

    if not dated:
        return None
    dated.sort(key=lambda item: (item[0], item[1]))
    return dated[-1][2]


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

    def signature(self) -> tuple[int, int] | None:
        """Подпись файла данных: по ней видно, что файл изменился.

        v0.28.0: нужна компьютерам, которые только смотрят данные, —
        они должны замечать, что файл в общей папке обновил другой
        компьютер (ADR-074). Размер и время изменения вместе надёжнее
        одного времени: у облачных клиентов оно округляется.
        """

        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def reload(self) -> None:
        """Перечитать файл с диска, не трогая выбранную базу (v0.28.0).

        Экраны держат ссылку на ``store``, поэтому после перечитывания
        им достаточно перерисоваться — подписчики ``on_saved``
        уведомляются так же, как при обычном сохранении.
        """

        self.data = self._load()
        for callback in list(self.on_saved):
            callback()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.data.to_dict(), ensure_ascii=False, indent=2)
        # v0.18.0: запись атомарная — сначала во временный файл, потом
        # подмена целиком (os.replace). Так облачный клиент (OneDrive,
        # Диск) не успевает синхронизировать недописанный файл и не
        # развозит повреждённые данные по остальным компьютерам.
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp_path.write_text(text, encoding="utf-8")
            os.replace(tmp_path, self.path)
        except OSError:
            # Не удалось записать рядом (например, каталог только для
            # чтения) — пишем напрямую, как раньше.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            self.path.write_text(text, encoding="utf-8")
        # «Сводка» пересчитывается сразу после любого сохранения данных.
        for callback in list(self.on_saved):
            callback()
