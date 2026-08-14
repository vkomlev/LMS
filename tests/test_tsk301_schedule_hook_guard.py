"""tsk-301: сторож путей «ученик получил занятие в расписании».

Правило «появилось занятие → тариф base» держится на вызове
`_recalculate_money_for` (внутри него живёт `upgrade_on_schedule`). Вызов этот
надо не забыть в КАЖДОМ месте, где ученик привязывается к слоту, — а это ровно
тот класс ошибки, который уже дважды стрелял в этой задаче:

* Грабовский: слоты приехали слиянием учёток, календарь их не трогал, правило о
  событии не узнало — человек две недели ходил на demo, невидимый для денег;
* при написании самого этого стража нашлись ещё два непокрытых пути —
  создание слота СРАЗУ с учениками и перевод ученика между слотами.

Ни один из трёх случаев не падал и не жаловался. Поэтому список путей
сверяется разбором исходника, а не памятью.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICE = (
    Path(__file__).resolve().parents[1]
    / "app" / "services" / "lesson_calendar_service.py"
)

#: Вызовы, которыми ученик привязывается к слоту.
_ATTACH_CALLS = {"_attach_student_to_slot", "create"}

#: Вызов, через который срабатывает и пересчёт денег, и повышение тарифа.
_HOOK_CALL = "_recalculate_money_for"

#: Функции, которым пересчёт не нужен, с причиной. Пустой список означал бы
#: «все обязаны», а это неверно: приватная привязка — кирпич, а не путь.
EXEMPT: dict[str, str] = {
    "_attach_student_to_slot": (
        "приватный кирпич без commit — пересчёт зовёт вызывающий, иначе он "
        "случился бы внутри чужой незавершённой транзакции"
    ),
}


def _calls_in(node: ast.AST) -> set[str]:
    """Имена вызванных функций внутри узла (по последнему сегменту)."""
    found: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            found.add(func.id)
        elif isinstance(func, ast.Attribute):
            found.add(func.attr)
    return found


def _attaching_functions() -> dict[str, set[str]]:
    """Функции модуля, которые привязывают ученика к слоту → их вызовы."""
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    result: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        calls = _calls_in(node)
        # `create` слишком общее имя: засчитываем только привязку участника слота.
        source = ast.get_source_segment(SERVICE.read_text(encoding="utf-8"), node) or ""
        attaches = (
            "_attach_student_to_slot" in calls
            or "_slot_student_repo.create" in source
        )
        if attaches:
            result[node.name] = calls
    return result


def test_guard_finds_the_paths() -> None:
    """Сам сторож видит хотя бы один путь.

    Без этой проверки опечатка в разборе исходника превратила бы сторож в
    вечнозелёный: пустой список путей проходит любую проверку ниже.
    """
    assert _attaching_functions(), (
        "сторож не нашёл ни одного пути привязки — разбор исходника сломан"
    )


@pytest.mark.parametrize("func_name", sorted(_attaching_functions()))
def test_every_attach_path_recalculates(func_name: str) -> None:
    """Каждый путь привязки зовёт пересчёт — или объявлен исключением с причиной."""
    if func_name in EXEMPT:
        pytest.skip(f"{func_name}: {EXEMPT[func_name]}")

    calls = _attaching_functions()[func_name]
    assert _HOOK_CALL in calls, (
        f"{func_name} привязывает ученика к слоту, но не зовёт {_HOOK_CALL}. "
        f"Значит тариф не повысится и месяц не пересчитается — молча, как это "
        f"уже случилось с Грабовским. Либо добавьте вызов, либо внесите функцию "
        f"в EXEMPT с причиной."
    )


def test_exempt_entries_still_exist() -> None:
    """В EXEMPT нет записей о функциях, которых больше нет.

    Мёртвое исключение опаснее отсутствующего: оно выглядит как продуманное
    решение, а прикрывает пустоту.
    """
    phantom = set(EXEMPT) - set(_attaching_functions())
    assert not phantom, f"в EXEMPT числятся несуществующие функции: {sorted(phantom)}"


def test_hook_actually_upgrades() -> None:
    """Пересчёт действительно зовёт повышение тарифа, а не только деньги.

    Сторож выше проверяет вызов `_recalculate_money_for`. Если из него однажды
    уберут `upgrade_on_schedule`, все проверки останутся зелёными, а правило
    перестанет работать — эту связку и закрепляем.
    """
    source = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    hook = next(
        node for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == _HOOK_CALL
    )
    assert "upgrade_on_schedule" in _calls_in(hook), (
        f"{_HOOK_CALL} больше не зовёт upgrade_on_schedule — правило «появилось "
        f"занятие → тариф base» перестало действовать"
    )
