"""Сторож списка исключений из транзакционной изоляции (tsk-333).

Модуль, который сам открывает движок к БД (`create_async_engine`), нельзя
запускать внутри общей откатываемой транзакции: его уборка идёт отдельным
соединением и встаёт в блокировку на незакоммиченных строках теста —
прогон ВИСНЕТ без ошибки и без таймаута. Диагностировать это дорого:
падения нет, просто тишина.

Поэтому список таких модулей объявлен явно в `conftest.py`, а этот тест
сверяет его с фактическим содержимым каталога `tests/`.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import SELF_MANAGED_CONNECTION_MODULES

_TESTS_DIR = Path(__file__).resolve().parent
_ENGINE_CALL = re.compile(r"\bcreate_async_engine\b")


def _modules_creating_own_engine() -> set[str]:
    found: set[str] = set()
    for path in sorted(_TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        if _ENGINE_CALL.search(path.read_text(encoding="utf-8")):
            found.add(path.name)
    return found


def test_optout_list_matches_modules_with_own_engine():
    """Список исключений в conftest совпадает с модулями, держащими свой движок."""
    actual = _modules_creating_own_engine()
    declared = set(SELF_MANAGED_CONNECTION_MODULES)

    missing = actual - declared
    stale = declared - actual

    assert not missing, (
        "Эти модули вызывают create_async_engine, но не объявлены в "
        "SELF_MANAGED_CONNECTION_MODULES (tests/conftest.py): "
        f"{sorted(missing)}.\n"
        "Без этого прогон ЗАВИСНЕТ на них без сообщения об ошибке: уборка "
        "отдельным соединением ждёт незакоммиченную транзакцию теста.\n"
        "Варианты: (а) добавить модуль в список — он будет работать по-старому "
        "с реальными коммитами и уборкой за собой; (б) перевести его фикстуры "
        "на общую фикстуру `db`, тогда изоляция покроет и его."
    )
    assert not stale, (
        "Эти модули объявлены в SELF_MANAGED_CONNECTION_MODULES, но больше не "
        f"создают свой движок: {sorted(stale)}. Уберите их из списка — они "
        "могут работать в общей откатываемой транзакции."
    )
