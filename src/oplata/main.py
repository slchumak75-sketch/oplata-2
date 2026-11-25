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


def _offer_shared_base_transfer() -> None:
    """Предложить перенести наработанную базу в общую папку (v0.87.0).

    Спрашиваем один раз при запуске через Start-With-Google-Drive.bat,
    если папка Диска пустая (или база в ней ещё не создана) и на
    компьютере есть локальная база. Копия, не перенос: локальные файлы
    остаются на месте.
    """

    from .storage import pending_promotion, promote_to_shared

    files = pending_promotion()
    if not files:
        return
    from PySide6.QtWidgets import QMessageBox

    names = ", ".join(path.name for path in files)
    answer = QMessageBox.question(
        None,
        "База на Google Диске",
        "Папка Google Диска подключена первый раз, базы в ней ещё нет.\n\n"
        f"На компьютере найдена база: {names}.\n\n"
        "Перенести её на Диск, чтобы данные были видны на всех "
        "компьютерах? Копия на этом компьютере останется на месте.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    made = promote_to_shared()
    QMessageBox.information(
        None,
        "База на Google Диске",
        (
            f"Перенесено файлов: {len(made)}. Теперь данные читаются "
            "с общего Диска."
            if made
            else "Перенести не удалось: проверьте доступ к папке Диска."
        ),
    )


QT_LOAD_HELP = (
    "Программа не смогла загрузить Qt (библиотеки окна).\n\n"
    "Обычная причина: в папке программы остались файлы от прежней\n"
    "версии — например, папку распаковали из архива ZIP поверх старой,\n"
    "и рядом оказались несовместимые DLL.\n\n"
    "Что сделать:\n"
    "1. Поставьте программу заново установщиком\n"
    "   Oplata-<версия>-Setup.exe — он заменяет файлы полностью.\n"
    "2. Если установщик уже применяли: удалите программу\n"
    "   («Оплаты — Строймаркет» в списке программ Windows)\n"
    "   и поставьте заново тем же установщиком.\n"
    "3. Данные при этом не пропадут: они лежат отдельно, в\n"
    "   %APPDATA%\\OplataStroymarket или на Google Диске.\n\n"
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
    from .storage import DataStore
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

    access = resolve_user(argv)
    # v0.87.0: первый запуск с Google Диском. Папка на Диске пустая, а
    # база на компьютере наработана — предлагаем перенести её, иначе
    # программа показала бы новую пустую базу с демо-поставщиками
    # (замечание пользователя: «оплаты на гугл драйв не появились»).
    _offer_shared_base_transfer()
    store = DataStore()
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
