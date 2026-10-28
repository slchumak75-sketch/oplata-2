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

    from . import __app_name__, styles
    from .app import MainWindow
    from .storage import DataStore

    app = QApplication(sys.argv[:1] + [a for a in argv if a != "--self-test"])
    app.setApplicationName(__app_name__)
    app.setOrganizationName("Stroymarket")
    app.setStyleSheet(styles.APP_STYLESHEET)

    store = DataStore()
    window = MainWindow(store)
    # Программа открывается развёрнутой на весь экран, чтобы недельные
    # таблицы сразу были во всю ширину (запрос пользователя, v0.2.0).
    window.showMaximized()

    if "--self-test" in argv:
        QTimer.singleShot(400, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
