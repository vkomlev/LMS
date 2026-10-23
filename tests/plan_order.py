"""Помощник тестов «результат не зависит от плана запроса» (tsk-996).

Класс дефекта: запрос без полного ORDER BY (или DISTINCT/GROUP BY без него)
отдаёт строки в порядке, который выбрал план PostgreSQL. На маленькой тестовой
базе план почти всегда один и тот же, поэтому такой код зеленеет, а на живой
базе порядок «прыгает». Здесь план задаётся настройками планировщика
(`SET LOCAL`), а физический порядок кучи — перекладкой строки в её конец
(`UPDATE`), без расчёта на статистику.

Использование::

    await move_to_heap_end(db, "courses", min_id)
    seen = await run_under_plans(db, lambda: service.call(db, ...))
    assert_same(seen)

`run_under_plans` возвращает {план: результат}. Настройки действуют только
до конца вызова: снимаются после него (при ошибке — откатом транзакции теста).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Iterable, Optional

from sqlalchemy import text

# Каждый план ломает свою «привычку» порядка:
# * custom-sort — обычный план (Sort+Unique/GroupAggregate на маленьких таблицах);
# * hashagg — DISTINCT/GROUP BY через хеш: порядок по хешу ключа;
# * generic — общий план подготовленного запроса (как с 6-го исполнения);
# * heap-order — только чтение кучи: строки в физическом порядке;
# * index-order — только индексы: строки в порядке индекса, а не кучи.
PLANS: dict[str, tuple[str, ...]] = {
    "custom-sort": ("SET LOCAL enable_sort = on",),
    "hashagg": ("SET LOCAL enable_sort = off",),
    "generic": ("SET LOCAL plan_cache_mode = force_generic_plan",),
    "heap-order": (
        "SET LOCAL enable_indexscan = off", "SET LOCAL enable_bitmapscan = off",
        "SET LOCAL enable_indexonlyscan = off",
        "SET LOCAL enable_hashjoin = off", "SET LOCAL enable_mergejoin = off",
        "SET LOCAL enable_sort = off",
    ),
    "index-order": (
        "SET LOCAL enable_seqscan = off", "SET LOCAL enable_bitmapscan = off",
        "SET LOCAL enable_hashjoin = off", "SET LOCAL enable_hashagg = off",
    ),
}
_RESET = (
    "RESET enable_sort", "RESET plan_cache_mode", "RESET enable_indexscan",
    "RESET enable_bitmapscan", "RESET enable_indexonlyscan", "RESET enable_hashjoin",
    "RESET enable_mergejoin", "RESET enable_seqscan", "RESET enable_hashagg",
)


async def with_plan(
    db: Any,
    plan: str,
    call: Callable[[], Awaitable[Any]],
    *,
    before: Optional[Callable[[], None]] = None,
) -> Any:
    """Выполнить `call` при плане `plan`; `before` — сброс кешей перед вызовом.

    Настройки снимаются только при успехе: если вызов сломал транзакцию,
    RESET упал бы сам и спрятал исходную ошибку, а `SET LOCAL` и так уйдёт
    с откатом внешней транзакции теста.
    """
    for stmt in PLANS[plan]:
        await db.execute(text(stmt))
    if before is not None:
        before()
    result = await call()
    for stmt in _RESET:
        await db.execute(text(stmt))
    return result


async def run_under_plans(
    db: Any,
    call: Callable[[], Awaitable[Any]],
    *,
    plans: Iterable[str] = tuple(PLANS),
    before: Optional[Callable[[], None]] = None,
) -> dict[str, Any]:
    """Результат `call` под каждым планом: {план: результат}."""
    return {plan: await with_plan(db, plan, call, before=before) for plan in plans}


def assert_same(seen: dict[str, Any], expected: Any = None) -> None:
    """Все планы дали одно и то же (и это `expected`, если он задан)."""
    values = list(seen.values())
    if expected is None:
        expected = values[0]
    assert all(v == expected for v in values), seen


async def move_to_heap_end(db: Any, table: str, row_id: int, column: str = "id") -> None:
    """Переложить строку в конец кучи: новая версия строки пишется в конец.

    Так бывает на живой базе после любой правки строки: при чтении кучи она
    идёт последней, хотя по id — первая.

    Ограничение: `SET id = id` — HOT-обновление, запись в индексе остаётся
    прежней. Если план читает по индексу, перекладка порядок не меняет:
    тогда меняйте сам ключ индекса туда-обратно или вставляйте строки с
    заранее взятыми id (`nextval`) в обратном порядке.
    """
    await db.execute(
        text(f"UPDATE {table} SET {column} = {column} WHERE {column} = :v"), {"v": row_id}
    )
