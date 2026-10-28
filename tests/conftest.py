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
def _collect_after_test():
    yield
    gc.collect()
