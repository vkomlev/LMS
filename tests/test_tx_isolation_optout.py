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

import ast
import re
from pathlib import Path

from tests.conftest import OTHER_OPTOUT_MODULES, SELF_MANAGED_CONNECTION_MODULES

_TESTS_DIR = Path(__file__).resolve().parent
_ENGINE_CALL = re.compile(r"\bcreate_async_engine\b")
_INSERT_TASKS = re.compile(r"INSERT\s+INTO\s+tasks\s*\(", re.I)


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


# --- Сторож фиксированных external_uid вне изоляции (tsk-333, рецидив 25.08) ---
#
# Модуль вне изоляции коммитит по-настоящему, а убирает за собой только в
# teardown фикстуры. Прогон, оборванный до teardown (падение в setup, Ctrl+C),
# оставляет строку в dev-БД — и ВСЕ следующие прогоны файла падают в setup с
# UniqueViolation по `tasks_external_uid_key`. Лечится уникальным uid на прогон:
#     f"tsk264-reused-{uuid.uuid4().hex[:12]}"


def _balanced(sql: str, start: int) -> tuple[str | None, int]:
    """Содержимое скобки, открытой на позиции `start`, и индекс за её закрытием."""
    depth = 0
    for i in range(start, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[start + 1:i], i + 1
    return None, len(sql)


def _split_top(chunk: str) -> list[str]:
    """Разбить список через запятую, не заходя внутрь вложенных скобок."""
    parts: list[str] = []
    depth, current = 0, ""
    for ch in chunk:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _external_uid_slot(sql: str) -> tuple[str, str] | None:
    """Что попадает в `external_uid` у `INSERT INTO tasks`.

    Возвращает ("inline", выражение) — значение вшито прямо в SQL,
    ("param", имя) — значение приходит bind-параметром, либо None,
    если разобрать вставку не удалось (тогда сторож молчит).
    """
    match = _INSERT_TASKS.search(sql)
    if not match:
        return None
    columns_raw, after_columns = _balanced(sql, match.end() - 1)
    if columns_raw is None:
        return None
    values_match = re.search(r"VALUES\s*\(", sql[after_columns:], re.I)
    if not values_match:
        return None
    values_raw, _ = _balanced(sql, after_columns + values_match.end() - 1)
    if values_raw is None:
        return None
    columns = [c.strip().strip('"') for c in _split_top(columns_raw)]
    values = _split_top(values_raw)
    if "external_uid" not in columns or len(columns) != len(values):
        return None
    value = values[columns.index("external_uid")]
    bind = re.search(r":(\w+)", value)
    return ("param", bind.group(1)) if bind else ("inline", value)


def _sql_literal(node: ast.AST) -> str | None:
    """Строковый литерал SQL из `text("...")` или голой строки, иначе None."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "text" and node.args:
        node = node.args[0]
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _origins(tree: ast.Module, name: str, scope: ast.AST | None) -> list[ast.AST]:
    """Откуда берётся значение переменной `name`: присваивания и фактические
    аргументы вызовов, если это параметр локальной функции-помощника.

    Литерал у tsk264 сидел не в самом `execute`, а на вызове помощника
    (`new_task(ids["reused"], "tsk264-reused")`) — без этого шага сторож
    прошёл бы мимо ровно того дефекта, ради которого написан.
    """
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                found.append(node.value)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        params = [a.arg for a in scope.args.args]
        if name in params:
            index = params.index(name)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == scope.name
                ):
                    if len(node.args) > index:
                        found.append(node.args[index])
                    for kw in node.keywords:
                        if kw.arg == name:
                            found.append(kw.value)
    return found


def _enclosing_function(tree: ast.Module, target: ast.AST) -> ast.AST | None:
    """Ближайшая функция, внутри которой лежит узел (нужны её параметры)."""
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(child is target for child in ast.walk(node))
    ]
    return max(candidates, key=lambda n: n.lineno) if candidates else None


def _fixed_uid_inserts(path: Path) -> list[str]:
    """Строки модуля, где `external_uid` задан постоянным значением."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
        ):
            continue
        sql = _sql_literal(node.args[0])
        if sql is None:
            continue
        slot = _external_uid_slot(sql)
        if slot is None:
            continue
        kind, payload = slot
        if kind == "inline":
            hits.append(f"{path.name}:{node.lineno} — uid вшит в SQL: {payload}")
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Dict):
            continue
        for key, value in zip(node.args[1].keys, node.args[1].values):
            if not (isinstance(key, ast.Constant) and key.value == payload):
                continue
            if isinstance(value, ast.Constant):
                hits.append(f"{path.name}:{node.lineno} — uid = {value.value!r}")
            elif isinstance(value, ast.Name):
                scope = _enclosing_function(tree, node)
                for origin in _origins(tree, value.id, scope):
                    if isinstance(origin, ast.Constant):
                        hits.append(
                            f"{path.name}:{origin.lineno} — uid = {origin.value!r} "
                            f"(приходит в вставку строки {node.lineno})"
                        )
            break
    return hits


def test_optout_modules_use_unique_external_uid():
    """Вне изоляции задания вставляются с уникальным на прогон external_uid."""
    offenders: list[str] = []
    for name in sorted(set(SELF_MANAGED_CONNECTION_MODULES) | set(OTHER_OPTOUT_MODULES)):
        path = _TESTS_DIR / name
        if path.exists():
            offenders.extend(_fixed_uid_inserts(path))

    assert not offenders, (
        "Постоянный external_uid в модуле вне транзакционной изоляции:\n"
        + "\n".join(offenders)
        + "\nПрогон, оборванный до teardown, оставит строку в dev-БД, и следующий "
        "прогон файла упадёт в setup с UniqueViolation по tasks_external_uid_key "
        "(так было 25.08 с tsk264-reused / tsk264-plain — понадобилась ручная "
        "уборка).\nЛечится уникальным uid на прогон: "
        'f"мой-тег-{uuid.uuid4().hex[:12]}".'
    )
