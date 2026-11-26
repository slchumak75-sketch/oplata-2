"""Точка входа приложения.

Запуск из исходников:  ``python run.py``  (см. docs/BUILD.md)
Собранный exe:          ``Oplata.exe``

Флаг ``--self-test``: открыть окно и сразу корректно закрыться с кодом 0.
Используется в GitHub Actions, чтобы проверить, что собранный exe
вообще запускается.
"""

from __future__ import annotations

import sys

# Импорт здесь, а не внутри main(): доступ нужен и функции
# resolve_user, которая живёт отдельно. Qt этот модуль не тянет —
# только stdlib.
from .access import Access, load_current_user


def _offer_shared_base_transfer() -> bool:
    """Предложить копирование в пустую папку, не заменяя её файлы.

    ``False`` — копирование оказалось неполным: запуск надо остановить,
    чтобы не работать случайно со смешанным набором баз и настроек.
    """

    from .storage import (
        local_data_dir,
        pending_promotion,
        promote_to_shared,
        shared_dir_candidate,
    )

    files = pending_promotion()
    if not files:
        return True
    from PySide6.QtWidgets import QMessageBox

    names = ", ".join(path.name for path in files)
    answer = QMessageBox.question(
        None,
        "База на Google Диске",
        "В папке на Диске пока не обнаружены файлы базы или настроек.\n\n"
        f"На компьютере найдены файлы: {names}.\n"
        f"Откуда: {local_data_dir()}\nКуда: {shared_dir_candidate()}\n\n"
        "Скопировать эти файлы на Диск? Выберите «Да» только после "
        "проверки, что это актуальные файлы и нужная общая папка. "
        "Другие компьютеры на время копирования должны быть закрыты. "
        "Локальные файлы останутся на месте. Если выбрать «Нет», "
        "программа закроется, ничего не копируя.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    made = promote_to_shared(files)
    if len(made) != len(files):
        QMessageBox.warning(
            None,
            "База на Google Диске",
            f"Скопировано файлов: {len(made)} из {len(files)}. "
            "Копирование не завершено: в папке мог появиться файл "
            "или отсутствует доступ.\n\n"
            "Программа сейчас закроется. Не загружайте и не очищайте "
            "базы наугад: сначала проверьте обе папки и сохраните "
            "независимые копии файлов.",
        )
        return False
    QMessageBox.information(
        None,
        "База на Google Диске",
        f"Скопировано файлов: {len(made)}. Локальные оригиналы сохранены.\n\n"
        "Проверьте обе базы и дождитесь окончания синхронизации Диска "
        "перед подключением других компьютеров.",
    )
    return True


def _prepare_shared_files() -> bool:
    """Не создавать незаметно демо-базу или пользователей в общей папке.

    Диск может ещё не синхронизировать файлы. Если чего-то не хватает,
    создание новых файлов допустимо только по явному согласию; если
    файл не читается, запуск прекращается без попытки его заменить.
    """

    import json
    from pathlib import Path

    from .access import default_users, users_from_payload
    from .models import AppData, seed_data
    from .storage import BASE_FILES, data_dir, shared_dir_candidate, write_new_json

    shared = shared_dir_candidate()
    if not shared:
        return True
    from PySide6.QtWidgets import QMessageBox

    target = Path(shared)
    try:
        same_folder = target.resolve() == data_dir().resolve()
    except OSError:
        same_folder = False
    if not same_folder:
        QMessageBox.warning(
            None, "База на Google Диске",
            "Путь общей папки не совпадает с путём данных программы. "
            "Программа закроется. Проверьте настройки запуска и папку Диска.",
        )
        return False

    missing: list[str] = []
    names = list(BASE_FILES.values()) + ["users.json"]
    for name in names:
        path = target / name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("ожидается объект JSON")
            if name == "users.json":
                if users_from_payload(raw) is None:
                    raise ValueError("не найден корректный список пользователей")
            else:
                AppData.from_dict(raw)
        except FileNotFoundError:
            missing.append(name)
        except (OSError, ValueError, TypeError, AttributeError, KeyError, OverflowError):
            QMessageBox.warning(
                None, "База на Google Диске",
                f"Не удалось прочитать файл:\n{path}\n\n"
                "Программа закроется и не будет его заменять. "
                "Проверьте подключение и синхронизацию Диска, сохраните "
                "независимые копии и проверьте файл; не загружайте и "
                "не очищайте базы наугад.",
            )
            return False

    if not missing:
        return True
    explanation = []
    if any(name in missing for name in BASE_FILES.values()):
        explanation.append("отсутствующие базы будут созданы с демо-данными")
    if "users.json" in missing:
        explanation.append("будут созданы стандартные пользователи без пароля конфигурации")
    answer = QMessageBox.question(
        None, "Не найдены файлы общей базы",
        f"В общей папке {target} не найдены файлы:\n"
        + "\n".join(missing)
        + "\n\nЕсли продолжить, " + "; ".join(explanation) + ". "
        "Это НЕ восстановление прежних данных. Если ожидали рабочие "
        "файлы или Диск ещё синхронизируется, выберите «Нет» и "
        "проверьте папку и независимые копии. Другие компьютеры "
        "должны быть закрыты.\n\nСоздать недостающие новые файлы? "
        "При отказе программа закроется.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    try:
        for name in missing:
            payload = (
                {"users": [user.to_dict() for user in default_users()]}
                if name == "users.json"
                else seed_data().to_dict()
            )
            write_new_json(target / name, payload)
    except OSError:
        QMessageBox.warning(
            None, "База на Google Диске",
            "Не удалось создать все недостающие файлы: файл мог "
            "появиться во время запуска или папка недоступна. "
            "Программа закроется, существующие файлы не заменены. "
            "Проверьте папку и сохраните независимые копии.",
        )
        return False
    return True


QT_LOAD_HELP = (
    "Программа не смогла загрузить Qt (библиотеки окна).\n\n"
    "Возможная причина: в папке программы остались несовместимые\n"
    "файлы от прежней версии, например после распаковки ZIP поверх неё.\n\n"
    "Что сделать:\n"
    "1. Сначала сохраните независимые копии ОБЕИХ баз и настроек.\n"
    "   Обычно они в %APPDATA%\\OplataStroymarket или на Google Диске;\n"
    "   проверьте, где лежат именно ваши актуальные файлы.\n"
    "2. Поставьте программу заново установщиком\n"
    "   Oplata-<версия>-Setup.exe — он заменяет файлы программы.\n"
    "3. Если установщик уже применяли: удалите программу\n"
    "   («Оплаты — Строймаркет» в списке программ Windows)\n"
    "   и поставьте заново тем же установщиком.\n\n"
    "Технические подробности: {error}"
)


def _qt_files_report() -> str:
    """Что за файлы Qt лежат рядом с программой (v0.88.1).

    Нужно для разбора на расстоянии: у программы четыре компьютера, и
    «пришлите скриншот» — единственный доступный способ понять, что
    случилось. Видно версии и размеры — по ним сразу ясно, смешаны ли
    файлы двух сборок.
    """

    import hashlib
    import os
    from pathlib import Path as _Path

    # В собранной программе библиотеки лежат в _internal рядом с exe.
    roots = []
    if getattr(sys, "frozen", False):
        exe_dir = _Path(sys.executable).resolve().parent
        roots = [exe_dir / "_internal", exe_dir]
    else:
        roots = [_Path(__file__).resolve().parents[2]]

    names = ("Qt6Core.dll", "pyside6.abi3.dll", "shiboken6.abi3.dll")
    lines = []
    try:
        import PySide6
        import shiboken6

        lines.append(f"PySide6 {PySide6.__version__}, shiboken6 {shiboken6.__version__}")
    except Exception:
        lines.append("PySide6: определить версию не удалось")

    for name in names:
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob(name):
                try:
                    size = path.stat().st_size
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
                except OSError:
                    continue
                lines.append(f"{name}: {size} байт, {digest}")
                break
            else:
                continue
            break
    return "\n".join(lines)


def _show_startup_error(error: BaseException) -> None:
    """Показать понятную ошибку вместо английского трейсбека (v0.88.1).

    Раньше при неудачной загрузке Qt пользователь видел окно PyInstaller
    «Unhandled exception in script» с текстом «DLL load failed while
    importing QtCore: Не найдена указанная процедура» — по нему нельзя
    понять ни что случилось, ни что делать. Самый частый случай —
    смешанные файлы от двух версий, и лечится он установщиком.
    """

    details = _qt_files_report()
    text = QT_LOAD_HELP.format(error=error) + "\n\nФайлы рядом с программой:\n" + details
    # Qt может не загрузиться вовсе — тогда показываем окно Windows.
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        QMessageBox.critical(None, "Оплаты — Строймаркет", text)
        del app
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                None, text, "Оплаты — Строймаркет", 0x10
            )
        except Exception:
            print(text)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # v0.88.1: Qt грузится в try — иначе любая беда с DLL показывалась
    # английским трейсбеком PyInstaller без единой подсказки.
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError as error:
        _show_startup_error(error)
        return 3

    from . import __app_name__, __version__, styles
    from .app import MainWindow
    from .resources import icon_path
    from .storage import (
        DataStore, DataUnavailableError, load_active_base, shared_dir_candidate,
    )
    from .widgets import disable_item_focus, disable_selection_color

    app = QApplication(sys.argv[:1] + [a for a in argv if a != "--self-test"])
    app.setApplicationName(__app_name__)
    app.setOrganizationName("Stroymarket")
    # v0.28.2: своя иконка в заголовке окна и на панели задач.
    # Если файла нет (например, ресурсы не положили в сборку), программа
    # просто работает со стандартной иконкой Qt — падать не должна.
    app.setWindowIcon(QIcon(str(icon_path())))
    # v0.26.4: стиль без рамки фокуса на ячейках таблиц (артефакт
    # после клика — две вертикальные линии на нажатой ячейке).
    app.setStyle(styles.NoFocusFrameStyle())
    app.setStyleSheet(styles.APP_STYLESHEET)

    # Сначала копирование, затем чтение роли. Иначе чтение пользователей
    # могло создать users.json с настройками по умолчанию в пустой общей
    # папке ещё до предложения перенести настоящий файл пользователей.
    if not _offer_shared_base_transfer() or not _prepare_shared_files():
        return 1
    shared = bool(shared_dir_candidate())
    if shared:
        from PySide6.QtWidgets import QMessageBox
        from .access import read_users_safely

        users = read_users_safely()
        if users is None:
            QMessageBox.warning(
                None, "Пользователи недоступны",
                "Не удалось прочитать пользователей общей базы. Программа "
                "закроется; проверьте синхронизацию Диска и копии файлов.",
            )
            return 1
        access = Access(load_current_user(users))
    else:
        access = resolve_user(argv)
    try:
        store = DataStore(
            base_key=(access.fixed_base or load_active_base()) if shared else None,
            require_existing=shared,
        )
    except DataUnavailableError as error:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(
            None, "База недоступна",
            f"{error}\n\nПрограмма закроется, новую базу не создаём. "
            "Проверьте синхронизацию Диска и независимые копии файлов.",
        )
        return 1
    window = MainWindow(store, access)
    # v0.26.5: без фокуса у таблиц Qt не рисует рамку на нажатой
    # ячейке (артефакт после клика). Ввод по клику при этом работает.
    disable_item_focus(window)
    # v0.27.1: цвет выделения таблиц задаётся напрямую — QSS на самом
    # виджете его палитру не меняет (синие штрихи на строке).
    disable_selection_color(window)
    # В заголовке окна видно роль — иначе непонятно, почему часть
    # вкладок и кнопок недоступна (v0.18.0).
    window.setWindowTitle(f"{__app_name__}  v{__version__} · {access.title}")
    # v0.30.0: перед выходом обе базы сами выгружаются в «Оплата база»
    # рядом с программой (ADR-082). В smoke-прогоне (--self-test) окно
    # закрывается сразу — выгрузку отключаем, чтобы exe не писал файлы
    # в каталог сборки.
    window.autosave_on_exit = "--self-test" not in argv
    app.aboutToQuit.connect(window.autosave_bases_to_excel)
    # Программа открывается развёрнутой на весь экран, чтобы недельные
    # таблицы сразу были во всю ширину (запрос пользователя, v0.2.0).
    window.showMaximized()

    if "--self-test" in argv:
        # Выгрузка в Excel — единственная внешняя зависимость помимо Qt:
        # в smoke-тесте сразу видно, попала ли она в сборку.
        from .excelio import OPENPYXL_MISSING_TEXT, Workbook

        if Workbook is None:
            print("SELF-TEST: " + OPENPYXL_MISSING_TEXT)
        else:
            print("SELF-TEST: openpyxl доступен, выгрузка в Excel работает")
        QTimer.singleShot(400, app.quit)
    return app.exec()


def resolve_user(argv: list[str] | None = None) -> Access:
    """Определить, кто работает на этом компьютере (v0.20.0).

    Никаких вопросов при запуске: берём сохранённый выбор, а если его
    нет — режим Администратора (полный доступ). Пользователя для
    компьютера выбирают в «Конфигурации» → «Пользователи и права»,
    столбец «Текущий»; после сохранения права применяются сразу.
    """

    return Access(load_current_user())


if __name__ == "__main__":
    raise SystemExit(main())
