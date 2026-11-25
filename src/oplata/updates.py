"""Обновление программы файлом через общую папку (v0.87.0).

Запрос пользователя: программы стоят на разных компьютерах в разных
местах, доступа к GitHub у них нет. Файл обновления присылается
администратору, он проверяет доработку у себя и **сам** кладёт файл в
общую папку Google Диска — оттуда остальные компьютеры его видят.

Устройство папки на Диске (рядом с базой)::

    <папка базы>\\Обновления\\
        Тест\\      файл для проверки — видят только тестовые компьютеры
        Выпуск\\    отсюда обновляются все
        Архив\\     прежние версии (для возврата назад)

Почему две папки, а не одна: администратор сначала получил файл и
хочет убедиться, что доработка правильная. Если бы файл сразу ложился
в общую папку, сырая сборка разлетелась бы по всем компьютерам. Поэтому
файл сначала попадает в ``Тест`` (его видят только компьютеры с
отметкой «Тестовый компьютер») и переезжает в ``Выпуск`` отдельным
сознательным нажатием «Выпустить».

Формат файла обновления — обычный ZIP::

    Oplata-0.87.0-Update.zip
        oplata-update.json     <- манифест (его читают все)
        Oplata-0.87.0-Setup.exe <- установщик (40+ МБ; читается только
                                   при нажатии «Установить»)

Манифест читается первым и стоит копейки, поэтому проверка «есть ли
обновление» не тянет сорок мегабайт из облака.

Модуль не использует Qt: всё, что можно проверить без окна, проверяется
здесь и покрывается обычными тестами.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Имена файлов и папок.
MANIFEST_NAME = "oplata-update.json"
UPDATES_DIR_NAME = "Обновления"
TEST_DIR_NAME = "Тест"
RELEASE_DIR_NAME = "Выпуск"
ARCHIVE_DIR_NAME = "Архив"
LOG_FILE_NAME = "update.log"
# Сколько прежних версий держим в «Архив» (каждая ~40 МБ).
ARCHIVE_KEEP = 3

# Ключи манифеста.
MANIFEST_VERSION = "version"
MANIFEST_SETUP = "setup"
MANIFEST_SHA256 = "sha256"
MANIFEST_BUILT = "built"
MANIFEST_NOTES = "notes"
MANIFEST_FORMAT = 1


class UpdateError(Exception):
    """Файл обновления не годится: текст показывается пользователю."""


def version_tuple(text: str) -> tuple[int, ...]:
    """Версия строкой -> кортеж чисел для сравнения.

    ``"0.87.0"`` -> ``(0, 87, 0)``. Хвостовые нули отбрасываются, чтобы
    ``0.87`` и ``0.87.0`` считались одной версией. Мусор даёт пустой
    кортеж: такой файл обновлением не считается.
    """

    parts: list[int] = []
    for piece in str(text or "").strip().split("."):
        piece = piece.strip()
        if not piece.isdigit():
            return ()
        parts.append(int(piece))
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """Новее ли версия файла, чем установленная."""

    left = version_tuple(candidate)
    right = version_tuple(current)
    if not left:
        return False
    return left > right


# --------------------------------------------------------------- манифест


@dataclass(frozen=True)
class UpdateInfo:
    """Что известно о файле обновления до его установки."""

    version: str
    setup: str
    sha256: str
    built: str = ""
    notes: str = ""
    path: Path | None = None

    @property
    def size_bytes(self) -> int:
        """Размер установщика в архиве (0 — не посчитать)."""

        if self.path is None:
            return 0
        try:
            with zipfile.ZipFile(self.path) as archive:
                return archive.getinfo(self.setup).file_size
        except (OSError, KeyError, zipfile.BadZipFile):
            return 0


def read_manifest(archive_path: Path | str) -> UpdateInfo:
    """Прочитать манифест из ZIP с обновлением.

    Бросает :class:`UpdateError` с понятным текстом, если это не файл
    обновления, манифест битый или версия не указана.
    """

    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise UpdateError(f"Файл не найден: {archive_path}")
    if not zipfile.is_zipfile(archive_path):
        raise UpdateError(
            "Это не файл обновления. Нужен архив вида "
            "«Oplata-0.87.0-Update.zip» — голый .exe не подходит."
        )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            try:
                raw = archive.read(MANIFEST_NAME)
            except KeyError as error:
                raise UpdateError(
                    "В архиве нет описания обновления "
                    f"({MANIFEST_NAME}) — файл повреждён или это не "
                    "обновление программы."
                ) from error
    except zipfile.BadZipFile as error:
        raise UpdateError("Архив обновления повреждён.") from error
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateError("Описание обновления испорчено.") from error
    if not isinstance(manifest, dict):
        raise UpdateError("Описание обновления испорчено.")

    version = str(manifest.get(MANIFEST_VERSION) or "").strip()
    if not version_tuple(version):
        raise UpdateError("В описании обновления нет версии.")
    setup = str(manifest.get(MANIFEST_SETUP) or "").strip()
    if not setup:
        raise UpdateError("В описании обновления не указан установщик.")
    return UpdateInfo(
        version=version,
        setup=setup,
        sha256=str(manifest.get(MANIFEST_SHA256) or "").strip().lower(),
        built=str(manifest.get(MANIFEST_BUILT) or "").strip(),
        notes=str(manifest.get(MANIFEST_NOTES) or "").strip(),
        path=archive_path,
    )


def file_sha256(path: Path | str, chunk: int = 1024 * 1024) -> str:
    """SHA-256 файла (порциями — установщик большой)."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def check_installer(info: UpdateInfo) -> Path:
    """Распаковать установщик и сверить SHA-256.

    Возвращает путь к распакованному установщику. Бросает
    :class:`UpdateError`, если архив без установщика или сумма не
    совпала: ставить непроверенный файл нельзя — он меняет программу
    у всех.
    """

    if info.path is None:
        raise UpdateError("Неизвестен файл обновления.")
    target_dir = Path(info.path).parent / "_update_unpacked"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(info.setup).name
    try:
        with zipfile.ZipFile(info.path) as archive:
            with archive.open(info.setup) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)
    except KeyError as error:
        raise UpdateError(
            f"В архиве нет установщика «{info.setup}»."
        ) from error
    except (OSError, zipfile.BadZipFile) as error:
        raise UpdateError(f"Не удалось распаковать обновление: {error}") from error

    actual = file_sha256(target)
    if info.sha256 and actual != info.sha256:
        raise UpdateError(
            "Контрольная сумма установщика не совпала — файл скачан "
            "неполностью или испорчен. Обновление отменено."
        )
    return target


# ------------------------------------------------------------ папки на Диске


def updates_dir(base_dir: Path | str) -> Path:
    """Папка обновлений рядом с базой: ``<база>\\Обновления``."""

    return Path(base_dir) / UPDATES_DIR_NAME


def test_dir(base_dir: Path | str) -> Path:
    return updates_dir(base_dir) / TEST_DIR_NAME


def release_dir(base_dir: Path | str) -> Path:
    return updates_dir(base_dir) / RELEASE_DIR_NAME


def archive_dir(base_dir: Path | str) -> Path:
    return updates_dir(base_dir) / ARCHIVE_DIR_NAME


def _newest_archive(folder: Path) -> UpdateInfo | None:
    """Самое новое обновление в папке (None — папки нет или она пуста)."""

    if not folder.is_dir():
        return None
    newest: UpdateInfo | None = None
    try:
        archives = sorted(folder.glob("*.zip"), key=lambda p: p.name)
    except OSError:
        return None
    for path in archives:
        try:
            info = read_manifest(path)
        except UpdateError:
            # Битый файл в папке не должен мешать остальным.
            continue
        if newest is None or is_newer(info.version, newest.version):
            newest = info
    return newest


def find_update(
    base_dir: Path | str,
    current_version: str,
    *,
    allow_test: bool = False,
) -> UpdateInfo | None:
    """Обновление новее установленного (None — обновлений нет).

    ``allow_test`` — компьютер помечен как тестовый: он видит и
    ``Тест``, и ``Выпуск``. Остальные смотрят только ``Выпуск``.
    """

    candidates = [release_dir(base_dir)]
    if allow_test:
        candidates.append(test_dir(base_dir))
    best: UpdateInfo | None = None
    for folder in candidates:
        info = _newest_archive(folder)
        if info is None or not is_newer(info.version, current_version):
            continue
        if best is None or is_newer(info.version, best.version):
            best = info
    return best


def copy_to_test(source: Path | str, base_dir: Path | str) -> Path:
    """Положить файл обновления в ``Тест`` (с проверкой манифеста)."""

    source = Path(source)
    read_manifest(source)  # файл негодный — дальше не идём
    folder = test_dir(base_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    _copy_atomic(source, target)
    return target


def publish(source: Path | str, base_dir: Path | str) -> Path:
    """Выпустить файл: переложить в ``Выпуск`` и убрать из ``Тест``.

    Сначала файл целиком пишется под временным именем и переименовывается
    — иначе другой компьютер мог бы подхватить недописанный архив.
    """

    source = Path(source)
    read_manifest(source)
    folder = release_dir(base_dir)
    folder.mkdir(parents=True, exist_ok=True)

    # Прежние выпуски уезжают в «Архив»: по ним можно вернуться назад.
    for old in folder.glob("*.zip"):
        if old.name != source.name:
            _move_to_archive(old, base_dir)

    target = folder / source.name
    _copy_atomic(source, target)
    try:
        if source.parent.resolve() == test_dir(base_dir).resolve():
            source.unlink()
    except OSError:
        pass
    return target


def retract(base_dir: Path | str) -> list[Path]:
    """Отозвать выпуск: убрать файлы из ``Выпуск`` в ``Архив``."""

    moved: list[Path] = []
    folder = release_dir(base_dir)
    if not folder.is_dir():
        return moved
    for old in folder.glob("*.zip"):
        target = _move_to_archive(old, base_dir)
        if target is not None:
            moved.append(target)
    return moved


def _move_to_archive(path: Path, base_dir: Path | str) -> Path | None:
    folder = archive_dir(base_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / path.name
        os.replace(path, target)
    except OSError:
        return None
    _trim_archive(base_dir)
    return target


def _trim_archive(base_dir: Path | str) -> None:
    """Оставить в «Архив» последние ``ARCHIVE_KEEP`` файлов."""

    folder = archive_dir(base_dir)
    try:
        archives = sorted(
            folder.glob("*.zip"), key=lambda p: p.stat().st_mtime
        )
    except OSError:
        return
    for old in archives[:-ARCHIVE_KEEP] if len(archives) > ARCHIVE_KEEP else []:
        try:
            old.unlink()
        except OSError:
            pass


def archives(base_dir: Path | str) -> list[UpdateInfo]:
    """Прежние версии из «Архив» — от новой к старой (для возврата)."""

    folder = archive_dir(base_dir)
    if not folder.is_dir():
        return []
    found: list[UpdateInfo] = []
    for path in folder.glob("*.zip"):
        try:
            found.append(read_manifest(path))
        except UpdateError:
            continue
    found.sort(key=lambda info: version_tuple(info.version), reverse=True)
    return found


def _copy_atomic(source: Path, target: Path) -> Path:
    """Скопировать через временное имя и переименовать."""

    tmp = target.with_name(target.name + ".part")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
    except OSError as error:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise UpdateError(f"Не удалось записать файл на Диск: {error}") from error
    return target


# ------------------------------------------------------------------- журнал


def log_path(base_dir: Path | str) -> Path:
    """Журнал обновлений рядом с базой."""

    return Path(base_dir) / LOG_FILE_NAME


def log_event(base_dir: Path | str, message: str, *, user: str = "") -> None:
    """Дописать строку в ``update.log`` (сбой записи делу не мешает)."""

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    who = f" [{user}]" if user else ""
    line = f"{stamp}{who} {message}\n"
    try:
        path = log_path(base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def describe(info: UpdateInfo) -> str:
    """Короткая подпись обновления для окна и сообщений."""

    parts = [f"версия {info.version}"]
    if info.built:
        parts.append(f"собрана {info.built}")
    size = info.size_bytes
    if size:
        parts.append(f"{size / (1024 * 1024):.1f} МБ")
    return " · ".join(parts)


@dataclass
class UpdatePlan:
    """Что делать по нажатию «Установить» (заполняет окно)."""

    info: UpdateInfo
    installer: Path | None = None
    problems: list[str] = field(default_factory=list)
