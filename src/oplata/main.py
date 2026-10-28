"""Точка входа приложения.

Запуск из исходников:  ``python run.py``  (см. docs/BUILD.md)
Собранный exe:          ``Oplata.exe``

Флаг ``--self-test``: открыть окно и сразу корректно закрыться с кодом 0.
Используется в GitHub Actions, чтобы проверить, что собранный exe
вообще запускается.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import __app_name__, __version__, styles
    from .access import (
        Access,
        load_current_user,
        load_users,
        save_current_user,
    )
    from .app import MainWindow
    from .storage import DataStore

    app = QApplication(sys.argv[:1] + [a for a in argv if a != "--self-test"])
    app.setApplicationName(__app_name__)
    app.setOrganizationName("Stroymarket")
    app.setStyleSheet(styles.APP_STYLESHEET)

    # v0.18.0: на компьютере один раз выбирается, кто здесь работает, —
    # дальше действуют права этого пользователя. В режиме самотеста
    # (--self-test) диалог не показываем.
    user = load_current_user()
    if user is None:
        user = load_users()[0] if "--self-test" in argv else _ask_user()
        save_current_user(user)

    access = Access(user)
    store = DataStore()
    window = MainWindow(store, access)
    # В заголовке окна видно роль — иначе непонятно, почему часть
    # вкладок и кнопок недоступна (v0.18.0).
    window.setWindowTitle(f"{__app_name__}  v{__version__} · {access.title}")
    # Программа открывается развёрнутой на весь экран, чтобы недельные
    # таблицы сразу были во всю ширину (запрос пользователя, v0.2.0).
    window.showMaximized()

    if "--self-test" in argv:
        QTimer.singleShot(400, app.quit)
    return app.exec()


def _ask_user():
    """Один раз спросить, кто работает на этом компьютере (v0.18.0).

    Список берётся из «Конфигурации» (файл ``users.json``): сначала
    там заводят пользователей, потом на каждом компьютере выбирают,
    кто из них здесь работает.
    """

    from PySide6.QtWidgets import QInputDialog

    users = load_users()
    names = [user.name for user in users]
    name, ok = QInputDialog.getItem(
        None,
        "Оплаты — Строймаркет",
        "Кто работает на этом компьютере?",
        names,
        0,
        False,  # нельзя ввести имя вручную — только из списка
    )
    if not ok:
        return users[0]
    for user in users:
        if user.name == name:
            return user
    return users[0]


if __name__ == "__main__":
    raise SystemExit(main())
