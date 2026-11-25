"""Тесты обновления программы файлом через Google Диск (v0.87.0).

Просьба пользователя: у компьютеров нет доступа к GitHub, файл
обновления администратор получает сам и кладёт на общий Диск; сырая
сборка не должна разлетаться по всем компьютерам.

Здесь проверяется то, что можно проверить без окна: чтение манифеста,
сравнение версий, папки «Тест»/«Выпуск»/«Архив», контрольная сумма и
права на раздачу.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from oplata import local_settings, updates


def make_update_zip(
    folder: Path,
    version: str = "0.87.0",
    *,
    name: str | None = None,
    manifest: dict | None = None,
    payload: bytes = b"installer-bytes",
) -> Path:
    """Собрать файл обновления в формате программы."""

    folder.mkdir(parents=True, exist_ok=True)
    setup_name = f"Oplata-{version}-Setup.exe"
    data = manifest if manifest is not None else {
        "version": version,
        "setup": setup_name,
        "sha256": __import__("hashlib").sha256(payload).hexdigest(),
        "built": "2026-11-25",
        "format": 1,
    }
    path = folder / (name or f"Oplata-{version}-Update.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(updates.MANIFEST_NAME, json.dumps(data, ensure_ascii=False))
        archive.writestr(setup_name, payload)
    return path


# --------------------------------------------------------------- версии


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("0.87.0", "0.86.0", True),
        ("0.86.1", "0.86.0", True),
        ("0.86.0", "0.86.0", False),
        ("0.87", "0.87.0", False),      # «0.87» и «0.87.0» — одна версия
        ("0.85.9", "0.86.0", False),
        ("мусор", "0.86.0", False),
        ("", "0.86.0", False),
    ],
)
def test_is_newer_compares_versions(left, right, expected):
    assert updates.is_newer(left, right) is expected


def test_version_tuple_ignores_trailing_zeros():
    assert updates.version_tuple("0.87.0") == (0, 87)
    assert updates.version_tuple("1.0.0.0") == (1,)
    assert updates.version_tuple("abc") == ()


# ------------------------------------------------------------- манифест


def test_read_manifest_reads_version_and_setup(tmp_path):
    path = make_update_zip(tmp_path, "0.87.0")
    info = updates.read_manifest(path)

    assert info.version == "0.87.0"
    assert info.setup == "Oplata-0.87.0-Setup.exe"
    assert info.built == "2026-11-25"
    assert len(info.sha256) == 64
    assert info.size_bytes == len(b"installer-bytes")


def test_read_manifest_rejects_foreign_files(tmp_path):
    not_zip = tmp_path / "Oplata-0.87.0-Update.zip"
    not_zip.write_bytes(b"this is not a zip")
    with pytest.raises(updates.UpdateError, match="не файл обновления"):
        updates.read_manifest(not_zip)

    # ZIP без манифеста (например, обычный архив программы).
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(updates.UpdateError, match="нет описания"):
        updates.read_manifest(plain)

    missing = tmp_path / "нет-такого.zip"
    with pytest.raises(updates.UpdateError, match="не найден"):
        updates.read_manifest(missing)


def test_read_manifest_requires_version_and_setup(tmp_path):
    bad = make_update_zip(tmp_path, "0.87.0", manifest={"setup": "x.exe"})
    with pytest.raises(updates.UpdateError, match="нет версии"):
        updates.read_manifest(bad)
    bad2 = make_update_zip(
        tmp_path, "0.88.0", manifest={"version": "0.88.0"}
    )
    with pytest.raises(updates.UpdateError, match="не указан установщик"):
        updates.read_manifest(bad2)


# -------------------------------------------------- проверка установщика


def test_check_installer_unpacks_and_verifies(tmp_path):
    path = make_update_zip(tmp_path, "0.87.0", payload=b"setup payload")
    info = updates.read_manifest(path)

    installer = updates.check_installer(info)

    assert installer.exists()
    assert installer.read_bytes() == b"setup payload"


def test_check_installer_stops_on_checksum_mismatch(tmp_path):
    path = make_update_zip(
        tmp_path,
        "0.87.0",
        manifest={
            "version": "0.87.0",
            "setup": "Oplata-0.87.0-Setup.exe",
            "sha256": "0" * 64,
        },
    )
    info = updates.read_manifest(path)
    with pytest.raises(updates.UpdateError, match="Контрольная сумма"):
        updates.check_installer(info)


# ------------------------------------------------------- папки на Диске


def test_folders_are_named_and_created(tmp_path):
    assert updates.updates_dir(tmp_path).name == "Обновления"
    assert updates.test_dir(tmp_path).name == "Тест"
    assert updates.release_dir(tmp_path).name == "Выпуск"
    assert updates.archive_dir(tmp_path).name == "Архив"


def test_find_update_prefers_release_and_ignores_old(tmp_path):
    make_update_zip(updates.release_dir(tmp_path), "0.87.0")

    found = updates.find_update(tmp_path, "0.86.0")
    assert found is not None and found.version == "0.87.0"
    # Уже установлено — предложения нет.
    assert updates.find_update(tmp_path, "0.87.0") is None


def test_test_folder_is_hidden_from_ordinary_computers(tmp_path):
    """Сырая сборка в «Тест» не должна быть видна обычным компьютерам."""

    make_update_zip(updates.test_dir(tmp_path), "0.88.0")

    # Обычный компьютер: «Тест» не смотрит — обновлений нет.
    assert updates.find_update(tmp_path, "0.86.0") is None
    # Тестовый компьютер — видит.
    allowed = updates.find_update(tmp_path, "0.86.0", allow_test=True)
    assert allowed is not None and allowed.version == "0.88.0"


def test_newest_wins_between_test_and_release(tmp_path):
    make_update_zip(updates.test_dir(tmp_path), "0.88.0")
    make_update_zip(updates.release_dir(tmp_path), "0.87.0")

    best = updates.find_update(tmp_path, "0.86.0", allow_test=True)
    assert best is not None and best.version == "0.88.0"


def test_broken_file_in_folder_does_not_block_others(tmp_path):
    folder = updates.release_dir(tmp_path)
    folder.mkdir(parents=True)
    (folder / "битый.zip").write_bytes(b"not a zip")
    make_update_zip(folder, "0.87.0")

    found = updates.find_update(tmp_path, "0.86.0")
    assert found is not None and found.version == "0.87.0"


# ------------------------------------------------------ публикация


def test_copy_to_test_keeps_others_unaware(tmp_path):
    source = make_update_zip(tmp_path / "downloads", "0.87.0")

    target = updates.copy_to_test(source, tmp_path)

    assert target.parent == updates.test_dir(tmp_path)
    assert target.exists()
    # Обычный компьютер о файле не знает.
    assert updates.find_update(tmp_path, "0.86.0") is None


def test_copy_to_test_rejects_foreign_file(tmp_path):
    junk = tmp_path / "junk.zip"
    with zipfile.ZipFile(junk, "w") as archive:
        archive.writestr("x.txt", "y")
    with pytest.raises(updates.UpdateError):
        updates.copy_to_test(junk, tmp_path)


def test_publish_moves_to_release_and_archives_previous(tmp_path):
    old = make_update_zip(updates.release_dir(tmp_path), "0.86.0")
    source = make_update_zip(tmp_path / "downloads", "0.87.0")

    target = updates.publish(source, tmp_path)

    assert target == updates.release_dir(tmp_path) / source.name
    assert target.exists()
    # Прежний выпуск уехал в «Архив», а не исчез.
    assert not old.exists()
    archived = [info.version for info in updates.archives(tmp_path)]
    assert archived == ["0.86.0"]
    # Теперь обновление видят все.
    found = updates.find_update(tmp_path, "0.86.0")
    assert found is not None and found.version == "0.87.0"


def test_publish_removes_file_from_test(tmp_path):
    source = make_update_zip(updates.test_dir(tmp_path), "0.87.0")

    updates.publish(source, tmp_path)

    assert not source.exists(), "после выпуска файл не должен остаться в «Тест»"
    assert (updates.release_dir(tmp_path) / source.name).exists()


def test_retract_hides_update_from_everyone(tmp_path):
    source = make_update_zip(tmp_path / "downloads", "0.87.0")
    updates.publish(source, tmp_path)
    assert updates.find_update(tmp_path, "0.86.0") is not None

    moved = updates.retract(tmp_path)

    assert len(moved) == 1
    assert updates.find_update(tmp_path, "0.86.0") is None
    # Но вернуться на неё можно: она в «Архив».
    assert [info.version for info in updates.archives(tmp_path)] == ["0.87.0"]


def test_archive_keeps_only_last_three(tmp_path):
    folder = updates.release_dir(tmp_path)
    folder.mkdir(parents=True)
    for minor in range(1, 6):
        version = f"0.8{minor}.0"
        source = make_update_zip(tmp_path / f"dl{minor}", version)
        updates.publish(source, tmp_path)

    kept = updates.archives(tmp_path)
    assert len(kept) == updates.ARCHIVE_KEEP
    # Остались самые новые.
    assert [info.version for info in kept] == ["0.84.0", "0.83.0", "0.82.0"]


# ------------------------------------------------------------- журнал


def test_log_event_appends_lines(tmp_path):
    updates.log_event(tmp_path, "выпущено: Oplata-0.87.0-Update.zip", user="Иван")
    updates.log_event(tmp_path, "отозвано файлов: 1")

    text = updates.log_path(tmp_path).read_text(encoding="utf-8")
    assert "выпущено" in text and "[Иван]" in text
    assert "отозвано файлов: 1" in text
    assert text.count("\n") == 2


def test_log_write_failure_does_not_raise(tmp_path):
    """Журнал не должен ломать обновление, если папка недоступна."""

    updates.log_event(tmp_path / "нет-доступа" / "глубже", "тест")


def test_describe_shows_version_size_and_date(tmp_path):
    path = make_update_zip(tmp_path, "0.87.0", payload=b"x" * 2048)
    info = updates.read_manifest(path)

    text = updates.describe(info)

    assert "версия 0.87.0" in text
    assert "2026-11-25" in text
    assert "МБ" in text


# ------------------------------------------------- права на раздачу


def test_test_computer_flag_lives_locally(tmp_path, monkeypatch):
    """Галочка «Тестовый компьютер» — настройка компьютера, не общей базы."""

    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path))

    assert local_settings.is_test_computer() is False
    assert local_settings.can_publish("admin") is False

    local_settings.set_test_computer(True, "manager")
    assert local_settings.is_test_computer() is True
    # Раздавать может только тот, за кем закреплена галочка.
    assert local_settings.can_publish("manager") is True
    assert local_settings.can_publish("user-mir") is False

    local_settings.set_test_computer(False)
    assert local_settings.is_test_computer() is False
    assert local_settings.can_publish("manager") is False


def test_updates_dir_hint_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path))
    assert local_settings.updates_dir_hint() == ""

    local_settings.set_updates_dir(r"G:\Мой диск\Oplata")
    assert local_settings.updates_dir_hint() == r"G:\Мой диск\Oplata"

    local_settings.set_updates_dir(None)
    assert local_settings.updates_dir_hint() == ""


def test_local_settings_survive_broken_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path))
    path = local_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ это не json", encoding="utf-8")

    # Битый файл — просто нет настроек, программа работает.
    assert local_settings.is_test_computer() is False
    local_settings.set_test_computer(True, "admin")
    assert local_settings.is_test_computer() is True


# ------------------------------------- синхронизация всех файлов (v0.87.0)


def test_shared_signature_covers_all_shared_files(tmp_path, monkeypatch):
    """v0.87.0: синхронизируется всё, что лежит в папке базы.

    Просьба пользователя: «Синхронизация данных через облако должна
    быть по всем параметрам ... Абсолютно всё». Раньше проверялся
    только файл активной базы, поэтому изменения пользователей, ширин
    столбцов и второй базы оставались незамеченными.
    """

    import json

    from oplata import access, storage

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("OPLATA_DATA_DIR", str(shared))
    monkeypatch.setenv("OPLATA_LOCAL_DIR", str(tmp_path / "local"))

    # Файлы появляются по одному — каждый должен попасть в подпись.
    marks = []
    for name, payload in (
        ("data.json", {"suppliers": []}),
        ("data-korelichi.json", {"suppliers": []}),
        ("users.json", {"users": []}),
        ("column-widths.json", {"org_report": {}}),
        ("state.json", {"active_base": "mir"}),
    ):
        (shared / name).write_text(json.dumps(payload), encoding="utf-8")
        marks.append(name)

    # Повторяем то, что делает MainWindow: собираем подпись по папке.
    from pathlib import Path

    names = list(storage.BASE_FILES.values()) + [
        "users.json",
        "column-widths.json",
        "state.json",
    ]
    present = [name for name in names if (Path(shared) / name).exists()]
    assert present == marks, "в подписи должны быть все файлы папки"
    # users.json читается из той же общей папки (conftest подменяет её
    # на временный каталог — сверяем через data_dir, а не жёстким путём).
    assert access.users_path().parent == storage.data_dir()
