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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import __app_name__, __version__, styles
    from .app import MainWindow
    from .storage import DataStore

    app = QApplication(sys.argv[:1] + [a for a in argv if a != "--self-test"])
    app.setApplicationName(__app_name__)
    app.setOrganizationName("Stroymarket")
    app.setStyleSheet(styles.APP_STYLESHEET)

    access = resolve_user(argv)
    store = DataStore()
    window = MainWindow(store, access)
    # В заголовке окна видно роль — иначе непонятно, почему часть
    # вкладок и кнопок недоступна (v0.18.0).
    window.setWindowTitle(f"{__app_name__}  v{__version__} · {access.title}")
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
