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
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .models import AppData, seed_data

APP_DIR_NAME = "OplataStroymarket"
DATA_FILE_NAME = "data.json"
STATE_FILE_NAME = "state.json"
# v0.79.0: автокопии перед необратимыми действиями (очистка базы,
# очистка таблиц, загрузка базы из файла). Копия — точная копия
# data.json, поэтому в ней сохраняется всё: примечания, правила
# разноса, карточка банка — то, что выгрузка в Excel не несёт.
BACKUP_DIR_NAME = "Копии"
# Сколько последних копий держать для каждой базы: место не резиновое,
# но и удалять всё нельзя — пропажу замечают не сразу.
BACKUP_KEEP = 30

# v0.30.0: папка «Оплата база» лежит рядом с программой (ADR-082). В ней
# — выгрузки обеих баз в Excel: своя копия на каждом компьютере, чтобы
# данные были под рукой и их можно было перенести флешкой. Считаем путь
# от самой программы, а не от данных: данные могут жить в общей папке на
# Google Диске, а выгрузка — локальная.
LOCAL_BASE_DIR_NAME = "Оплата база"
LOCAL_BASE_NAME_TEMPLATE = "Оплата {day}.xlsx"
# Переопределение для тестов: без него выгрузки падали бы в репозиторий.
PROGRAM_DIR_ENV = "OPLATA_PROGRAM_DIR"
# v0.79.0: то же для локальных копий (Загрузки) — иначе тесты писали бы
# копии в настоящие Загрузки машины.
DOWNLOADS_DIR_ENV = "OPLATA_DOWNLOADS_DIR"
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


# v0.87.0: перенос уже наработанной базы в общую папку Google Диска.
#
# Замечание пользователя: «распаковал файл ... оплаты на гугл драйв не
# появились». Причина была в самом устройстве программы: при запуске
# через Start-With-Google-Drive.bat каталог данных меняется на папку
# Диска, и если она пустая, программа создавала там **новую** базу с
# демо-поставщиками. Старая база при этом цела, но никто её не
# показывает: данные будто пропали.
#
# Теперь так: если папка на Диске пустая, а локальная база есть —
# программа спрашивает и по согласию копирует файлы. Локальные файлы
# **не удаляются** (человек должен видеть прежнюю базу на месте, пока
# не убедится, что с Диском всё в порядке).
PROMOTE_FILES = (
    "data.json",
    "data-korelichi.json",
    "users.json",
    "state.json",
    "column-widths.json",
)


def local_data_dir() -> Path:
    """Каталог данных **этого компьютера** (без OPLATA_DATA_DIR).

    Именно оттуда берётся база, если программа раньше запускалась без
    Google Диска.
    """

    appdata = os.environ.get("APPDATA")
    if appdata:  # Windows
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def shared_dir_candidate() -> str:
    """Каталог данных, заданный bat-файлом Google Диска (пусто — нет)."""

    return os.environ.get("OPLATA_DATA_DIR_GOOGLE") or ""


def pending_promotion() -> list[Path]:
    """Какие файлы можно перенести в общую папку (v0.87.0).

    Пустой список — переносить нечего или не нужно: папка базы не
    переопределена, в ней уже что-то есть либо локальных файлов нет.
    """

    shared = shared_dir_candidate()
    if not shared:
        return []
    target = Path(shared)
    local = local_data_dir()
    try:
        if target.resolve() == local.resolve():
            return []
    except OSError:
        pass
    # Не только МИР: КОРЕЛИЧИ, пользователи или настройки тоже могут
    # уже быть в папке. В таком случае не смешиваем их с локальным
    # набором и не предлагаем автоматическое копирование.
    if any((target / name).exists() for name in PROMOTE_FILES):
        return []
    found: list[Path] = []
    for name in PROMOTE_FILES:
        source = local / name
        if source.exists():
            found.append(source)
    return found


def _publish_new_file(temporary: Path, target: Path) -> None:
    """Опубликовать готовый файл, только если цели ещё нет на этом ПК."""

    # Windows rename отказывает, если цель существует; в POSIX это
    # свойство обеспечивает атомарный link (rename может заменить цель).
    if os.name == "nt":
        os.rename(temporary, target)
    else:
        os.link(temporary, target)


def write_new_json(path: Path, payload: dict) -> None:
    """Создать JSON без замены существующего файла и убрать временный."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    temporary = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        _publish_new_file(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def promote_to_shared(expected: list[Path] | None = None) -> list[Path]:
    """Скопировать локальную базу в общую папку (v0.87.0).

    Возвращает пути созданных файлов. Исходные файлы остаются на месте.
    Если перечень изменился после вопроса, копирование не начинается.
    """

    files = pending_promotion()
    if expected is not None and files != expected:
        return []
    made: list[Path] = []
    for source in files:
        target = Path(shared_dir_candidate()) / source.name
        tmp: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Отдельное случайное имя: два процесса не делят .part-файл.
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".part", dir=target.parent
            )
            os.close(fd)
            tmp = Path(tmp_name)
            shutil.copy2(source, tmp)
            # Во время копирования мог появиться и ДРУГОЙ файл набора.
            # Не добавляем локальные данные к чужой общей базе/настройкам.
            own_names = {path.name for path in made}
            if any(
                (target.parent / name).exists()
                for name in PROMOTE_FILES if name not in own_names
            ):
                raise FileExistsError("в общей папке появился файл")
            # Только создание нового файла, никогда не замена: между
            # вопросом и копированием файл мог появиться на этом ПК.
            _publish_new_file(tmp, target)
            made.append(target)
        except OSError:
            # Частичный набор не считаем успешным; вызывающая сторона
            # остановит запуск и попросит проверить папку.
            break
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
    return made


def data_path_for(base_key: str) -> Path:
    """Путь к файлу данных выбранной базы."""
    return data_dir() / BASE_FILES.get(base_key, BASE_FILES[DEFAULT_BASE])


def backup_dir() -> Path:
    """Каталог автокопий рядом с базой (v0.79.0)."""

    return data_dir() / BACKUP_DIR_NAME


# v0.79.0: второй каталог копий — на самом компьютере (обычно Загрузки;
# путь ищется средствами Qt, поэтому задаёт его окно программы). Копия
# рядом с базой уезжает в облако вместе с ней: испортится общая папка —
# пропадут обе, а локальная переживёт и облако (решение v0.23.1).
_extra_backup_dir: Path | None = None


def set_extra_backup_dir(path: Path | None) -> None:
    """Задать второй каталог копий (кроме «Копии» рядом с базой)."""

    global _extra_backup_dir
    _extra_backup_dir = Path(path) if path is not None else None


def backup_dirs() -> list[Path]:
    """Каталоги автокопий: рядом с базой и, если задан, локальный."""

    dirs = [backup_dir()]
    if _extra_backup_dir is not None and _extra_backup_dir not in dirs:
        dirs.append(_extra_backup_dir)
    return dirs


def make_backup(base_key: str, reason: str = "") -> list[Path]:
    """Снять копию файла базы перед необратимым действием (v0.79.0).

    Копия пишется в каждый из каталогов копий: рядом с базой (в общей
    папке она уезжает в облако вместе с самой базой) и на компьютер
    (останется, даже если общая папка испортится). Возвращает пути
    созданных копий; пустой список — снимать нечего (файла базы нет).
    """

    source = data_path_for(base_key)
    if not source.exists():
        return []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # Причина — в имени файла: по копии сразу видно, перед чем она.
    suffix = f"-{reason}" if reason else ""
    name = f"{source.stem}{suffix}-{stamp}.json"
    made: list[Path] = []
    for directory in backup_dirs():
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        target = directory / name
        try:
            shutil.copy2(source, target)
        except OSError:
            continue
        made.append(target)
        _trim_backups(directory, source.stem)
    return made


def _trim_backups(directory: Path, stem: str) -> None:
    """Оставить последние BACKUP_KEEP копий этой базы (v0.79.0)."""

    try:
        copies = sorted(
            path
            for path in directory.glob(f"{stem}-*.json")
            if path.is_file()
        )
    except OSError:
        return
    for path in copies[:-BACKUP_KEEP]:
        try:
            path.unlink()
        except OSError:
            pass


class BackupError(Exception):
    """Копию нельзя вернуть в базу (v0.79.0)."""


@dataclass(frozen=True)
class BackupInfo:
    """Копия файла базы: когда снята, перед чем и где лежит (v0.79.0)."""

    path: Path
    stamp: datetime | None = None
    reason: str = ""

    def title(self) -> str:
        when = self.stamp.strftime("%d.%m.%Y %H:%M") if self.stamp else "без даты"
        parts = [when]
        if self.reason:
            parts.append(self.reason)
        return f"{' · '.join(parts)} ({self.path.parent.name})"


def _backup_parts(stem: str, name: str) -> tuple[datetime | None, str]:
    """Разобрать имя копии: ``(время, причина)`` (v0.79.0).

    Понимает оба вида имён: новый (``data-перед очисткой-20261115-162706``,
    v0.79.0) и прежний, снятый кнопкой «Создать резервную копию»
    (``data-backup-2026-11-15_16-27-06``, v0.23.1): сохранённые раньше
    копии тоже должны возвращаться, а не лежать мёртвым грузом.
    """

    tail = name[len(stem):].lstrip("-")
    # Новый вид: метка времени — последние 15 знаков имени.
    if len(tail) >= 15:
        try:
            stamp = datetime.strptime(tail[-15:], "%Y%m%d-%H%M%S")
        except ValueError:
            pass
        else:
            return stamp, tail[:-16].strip("-")
    # Прежний вид: «backup-2026-11-15_16-27-06».
    old = tail[len("backup-"):] if tail.startswith("backup-") else ""
    if old:
        try:
            return datetime.strptime(old, "%Y-%m-%d_%H-%M-%S"), "резервная копия"
        except ValueError:
            pass
    return None, tail


def list_backups(base_key: str = DEFAULT_BASE) -> list[BackupInfo]:
    """Копии этой базы — свежие первыми (v0.79.0)."""

    stem = Path(BASE_FILES.get(base_key, BASE_FILES[DEFAULT_BASE])).stem
    items: list[BackupInfo] = []
    for directory in backup_dirs():
        try:
            paths = [path for path in directory.glob(f"{stem}-*.json")
                     if path.is_file()]
        except OSError:
            continue
        for path in paths:
            stamp, reason = _backup_parts(stem, path.stem)
            if stamp is None:  # имени не поняли — берём время файла
                try:
                    stamp = datetime.fromtimestamp(path.stat().st_mtime)
                except OSError:
                    stamp = None
            items.append(BackupInfo(path, stamp, reason))
    items.sort(
        key=lambda item: (item.stamp or datetime.min, item.path.name),
        reverse=True,
    )
    return items


def restore_backup(base_key: str, source: Path | str) -> list[Path]:
    """Вернуть базу из копии (v0.79.0).

    Копию сначала читаем и разбираем: битая базу не затрёт. Нынешнюю
    базу перед заменой тоже снимаем в копию — само восстановление
    можно откатить. Возвращает пути этой страховочной копии.
    """

    source = Path(source)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"Копию не прочитать: {source.name} ({exc})") from exc
    try:
        data = AppData.from_dict(raw)
    except (TypeError, ValueError, KeyError) as exc:
        raise BackupError(f"В копии нет базы: {source.name} ({exc})") from exc
    if not isinstance(data, AppData):
        raise BackupError(f"В копии нет базы: {source.name}")
    safety = make_backup(base_key, "перед восстановлением")
    target = data_path_for(base_key)
    # Атомарно, как DataStore.save: облачный клиент не должен успеть
    # синхронизировать недописанный файл.
    tmp_path = target.with_name(target.name + ".tmp")
    try:
        shutil.copy2(source, tmp_path)
        os.replace(tmp_path, target)
    except OSError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise BackupError(f"Не удалось вернуть копию: {exc}") from exc
    return safety


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


class DataUnavailableError(OSError):
    """Существующий файл базы отсутствует или не читается."""


def read_existing_data(path: Path) -> AppData | None:
    """Прочитать файл базы без создания и без записи при ошибке."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return AppData.from_dict(raw)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, OverflowError):
        return None


class DataStore:
    """Загрузка/сохранение AppData. При первом локальном запуске — демо."""

    def __init__(
        self, path: Path | None = None, base_key: str | None = None, *,
        require_existing: bool = False,
    ) -> None:
        # v0.17.0: активная база запоминается между запусками; явный path
        # (тесты) важнее remembered-выбора.
        self.base_key = base_key or load_active_base()
        self.path = path or data_path_for(self.base_key)
        # Подписчики на сохранение (v0.15.0): «Сводка» должна
        # пересчитываться сразу после ввода, а не только при смене даты.
        self.on_saved: list[Callable[[], None]] = []
        if require_existing:
            data = read_existing_data(self.path)
            if data is None:
                raise DataUnavailableError(f"База недоступна: {self.path}")
            self.data: AppData = data
        else:
            self.data = self._load()

    def switch_base(self, base_key: str) -> bool:
        """Переключить активную базу, не перезаписывая прежний файл.

        Изменения интерфейса сохраняются при вводе. Безусловное сохранение
        при переключении могло записать старый снимок поверх данных,
        которые с момента открытия изменил другой компьютер.
        Объект ``store`` остаётся тем же для всех экранов.
        """
        if base_key not in BASE_FILES:
            return False
        if base_key == self.base_key:
            return True
        new_path = data_path_for(base_key)
        if shared_dir_candidate():
            # В общей папке отсутствие файла может быть задержкой Диска.
            # Не создаём вместо него демо и не переключаемся на неё.
            data = read_existing_data(new_path)
            if data is None:
                return False
            self.base_key = base_key
            self.path = new_path
            self.data = data
        else:
            self.base_key = base_key
            self.path = new_path
            self.data = self._load()
        save_active_base(base_key)
        # «Сводка» и прочие подписчики пересчитываются по новой базе.
        for callback in list(self.on_saved):
            callback()
        return True

    def _load(self) -> AppData:
        if not self.path.exists():
            data = seed_data()
            try:
                # Между проверкой и созданием файл мог появиться.
                # Новая демо-база никогда не заменяет его на этом ПК.
                write_new_json(self.path, data.to_dict())
            except FileExistsError:
                pass  # кто-то уже создал файл — читаем его ниже
            else:
                self.data = data
                for callback in list(self.on_saved):
                    callback()
                return data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return AppData.from_dict(raw)
        except (OSError, ValueError, TypeError, AttributeError, KeyError, OverflowError):
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

    def reload(self) -> bool:
        """Перечитать существующий файл, не создавая демо при сбое Диска.

        Если файл временно пропал или не читается, прежний снимок остаётся
        в памяти, файл не записывается; вызывающая сторона сможет повторить
        попытку. ``True`` означает, что чтение удалось.
        """

        data = read_existing_data(self.path)
        if data is None:
            return False
        self.data = data
        for callback in list(self.on_saved):
            callback()
        return True

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
