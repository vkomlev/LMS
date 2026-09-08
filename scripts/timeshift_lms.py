"""Обёртка над tools.timeshift_pytest из Root под LMS.

Штатный плагин сдвигает «сегодня» только в пакетах tools/bot/tests. В LMS код
лежит в `app`, а тестовые модули импортируются верхним уровнем (нет
`tests/__init__.py`) — то есть штатный плагин не подменил бы НИЧЕГО и любой
прогон был бы ложно-зелёным. Здесь список модулей собирается по факту.

Путь к Root добавляется в sys.path ТОЛЬКО на время импорта и сразу убирается:
у Root есть свой пакет `tests`, и пока его каталог стоит первым, `from
tests.test_x import ...` в тестах LMS резолвится в чужой пакет. Это давало
19 «падений» на нулевом сдвиге — то есть ложную тревогу от самого инструмента.
"""
from __future__ import annotations

import sys
from datetime import timedelta

_ROOT = r"D:\Work\Root"
sys.path.insert(0, _ROOT)
try:
    from tools.timeshift_pytest import install_shift
finally:
    if sys.path and sys.path[0] == _ROOT:
        sys.path.pop(0)


def pytest_addoption(parser) -> None:
    parser.addoption("--timeshift-days", action="store", default="0")


def pytest_collection_finish(session) -> None:
    days = int(session.config.getoption("--timeshift-days"))
    if not days:
        return
    targets = tuple(
        name for name in list(sys.modules)
        if name == "app" or name.startswith("app.")
        or name.startswith("test_") or name == "conftest"
    )
    install_shift(timedelta(days=days), prefixes=targets)
    print(f"[timeshift-lms] сдвиг +{days}д, модулей-кандидатов: {len(targets)}")
