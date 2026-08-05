# app/services/turtle_sandbox/guard.py
"""
Статический AST-страж для кода ученика (tsk-412).

Первый рубеж защиты песочницы: отклоняет код ДО исполнения, если он содержит
конструкции, недопустимые для программы «нарисуй фигуру черепахой» —
импорт чего-либо кроме узкого списка модулей, обращение к dunder-атрибутам
(классический путь эскейпа `().__class__.__bases__[0].__subclasses__()`) и
вызовы заведомо опасных имён по названию (exec/eval/compile/open/...).

Второй рубеж — `stub_turtle.build_restricted_globals` (урезанные builtins на
рантайме): даже если что-то просочится мимо этого стража, опасное имя просто
не будет определено при исполнении (NameError, а не побег из песочницы).

ТРЕТИЙ РУБЕЖ (найден независимым ревью tsk-412, экспериментально
подтверждённый эксплойт): dunder-блэклист на ИМЕНАХ АТРИБУТОВ не закрывает
доступ к живым объектам фрейма/трейсбека/генератора/корутины через
НЕ-dunder имена (`tb_frame`, `f_back`, `f_globals`, `gi_frame`, `cr_frame`,
`ag_frame` и т.п.) — а получив такой фрейм, код читает `f_globals['__builtins__']`
(настоящий модуль builtins вызывающего, не урезанный словарь) и получает
безусловный `exec/eval/open/__import__`. Демонстрационный PoC: контекстный
менеджер, чей `__exit__` ловит исключение и читает `exc_tb.tb_frame.f_back`.

Закрывать это точечным расширением блэклиста — забег за бесконечным списком
имён (`co_consts`, `cell_contents`, `im_func`, ...). Вместо этого для ЭТОГО
узкого домена (рисование черепахой: циклы, функции, рекурсия, арифметика,
вызовы turtle/math/random/colorsys) построен ЗАПРЕТ САМИХ КОНСТРУКЦИЙ, что
единственным образом порождают объекты, из которых достижим живой фрейм:
`try/except` (даёт `exc.__traceback__.tb_frame`), `with` (даёт `__exit__`
такой же путь через пойманное исключение), генераторы и генераторные
выражения (`.gi_frame`), `async def`/`await`/`async with`/`async for`
(`.cr_frame`/`.ag_frame`). Ни один из 10 эталонных решений (материал 314) не
использует ни одну из этих конструкций — ограничение не сужает реальный
охват задач, но убирает целый класс атаки, а не один конкретный PoC.
"""

from __future__ import annotations

import ast
import re
from typing import List

ALLOWED_IMPORT_MODULES = frozenset({"turtle", "math", "random", "colorsys"})

FORBIDDEN_CALL_NAMES = frozenset({
    "exec", "eval", "compile", "open", "input", "__import__",
    "getattr", "setattr", "delattr", "vars", "globals", "locals",
    "dir", "help", "breakpoint", "exit", "quit", "memoryview",
})

# Belt-and-suspenders поверх статьи о фреймах выше: если конструкции ниже
# всё же где-то просочатся (баг в этом же страже), эти имена ловятся отдельно.
_FORBIDDEN_ATTR_NAMES = frozenset({
    "tb_frame", "tb_next", "f_back", "f_globals", "f_locals", "f_code",
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await",
    "ag_frame", "ag_code", "cell_contents", "co_consts", "co_names",
    "co_code", "co_freevars", "co_cellvars", "func_globals", "func_code",
    "im_func", "im_self", "im_class",
})

_FORBIDDEN_STATEMENT_TYPES = tuple(
    t for t in (
        ast.Try,
        getattr(ast, "TryStar", None),  # 3.11+ (exception groups)
        ast.With,
        ast.AsyncWith,
        ast.AsyncFunctionDef,
        ast.AsyncFor,
        ast.Await,
    ) if t is not None
)

_DUNDER_RE = re.compile(r"^__[^_]([A-Za-z0-9_]*[^_])?__$")


class GuardViolation(ValueError):
    """Код ученика содержит запрещённую конструкцию — исполнение не начиналось."""


def _is_dunder(name: str) -> bool:
    return bool(_DUNDER_RE.match(name))


def check_code_is_safe(source: str) -> None:
    """
    Разбирает `source` в AST и проверяет на запрещённые конструкции.

    Raises:
        GuardViolation: обнаружена запрещённая конструкция (сообщение —
            для ученика/лога, без внутренних деталей песочницы).
        SyntaxError: код не парсится как Python (пробрасывается как есть,
            вызывающий код (runner.py) отличает этот случай от GuardViolation).
    """
    tree = ast.parse(source, mode="exec")
    violations: List[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module not in ALLOWED_IMPORT_MODULES:
                    violations.append(f"import '{alias.name}' запрещён")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module not in ALLOWED_IMPORT_MODULES:
                violations.append(f"from '{node.module}' import ... запрещён")
        elif isinstance(node, ast.Attribute):
            if _is_dunder(node.attr) or node.attr in _FORBIDDEN_ATTR_NAMES:
                violations.append(f"обращение к атрибуту '{node.attr}' запрещено")
        elif isinstance(node, ast.Name):
            if _is_dunder(node.id):
                violations.append(f"обращение к имени '{node.id}' запрещено")
            elif node.id in FORBIDDEN_CALL_NAMES:
                violations.append(f"использование '{node.id}' запрещено")
        elif isinstance(node, _FORBIDDEN_STATEMENT_TYPES):
            violations.append(
                f"конструкция '{type(node).__name__}' запрещена (try/with/async — "
                "источник живых объектов фрейма/трейсбека/корутины)"
            )
        elif isinstance(node, (ast.Yield, ast.YieldFrom, ast.GeneratorExp)):
            violations.append(
                f"конструкция '{type(node).__name__}' запрещена (генератор даёт "
                "доступ к живому фрейму через .gi_frame)"
            )

    if violations:
        unique = sorted(set(violations))
        raise GuardViolation("; ".join(unique))
