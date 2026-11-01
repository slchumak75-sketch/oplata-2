"""Общие настройки прогона UI-тестов.

Окна Qt тяжёлые: за сессию набирается много C++-объектов, и сборка
мусора в один приём падала на уже удалённых объектах (``none_dealloc``
в headless-окружении со stub-библиотеками). Собирать мусор после
каждого теста спокойнее, чем один раз в конце прогона.
"""

from __future__ import annotations

import gc

import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Каждый тест работает в своём каталоге данных (v0.18.0).

    Иначе тесты, которые переключают базу или правят пользователей,
    пишут ``state.json``/``users.json`` в настоящий каталог данных
    машины и влияют друг на друга.
    """

    from oplata import access as access_module
    from oplata import storage

    monkeypatch.setattr(storage, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(access_module, "data_dir", lambda: tmp_path)
    yield


@pytest.fixture(autouse=True)
def _collect_after_test():
    yield
    gc.collect()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Заморозить объекты перед выходом из процесса.

    В headless-окружении (offscreen + stub-библиотеки) PySide6 иногда
    роняет интерпретатор уже ПОСЛЕ успешного прогона — ``Fatal Python
    error: none_dealloc`` на сборке мусора при завершении. ``gc.freeze``
    переводит дожившие до конца объекты в постоянное поколение, и
    финальная сборка их не трогает: прогон завершается штатно.
    """

    _close_leftover_windows()
    gc.collect()
    gc.freeze()


def _close_leftover_windows() -> None:
    """Закрыть и удалить все окна Qt, дожившие до конца прогона.

    Окно, которое тест не закрыл, остаётся владельцем C++-объекта до
    самого выхода из процесса — именно такие «хвосты» и роняют
    интерпретатор на финальной сборке мусора.
    """

    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # Qt не установлен — нечего закрывать
        return

    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:  # C++-объект уже удалён
            pass
    app.processEvents()
