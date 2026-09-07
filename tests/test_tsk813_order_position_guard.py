"""tsk-813: частичный реордер не создаёт коллизий, а БД не даёт закоммитить дубль.

Дефект (найден при разборе tsk-810): `reorder_tasks` глушит триггер порядка и
писал только переданные позиции, а непереданные оставались на своих числах.
Кабинет методиста нумерует от единицы ОТФИЛЬТРОВАННЫЙ список — под фильтром
«Активные» активные получали 1..N поверх чисел выключенных. Так в курсах 146,
147 и 1397 появились 112 дублирующихся позиций.

Здесь проверяются оба рубежа: сервис достраивает полный порядок курса, а
отложенное ограничение `tasks_course_order_unique` ловит то, что прошло мимо
сервиса (ad-hoc правки с заглушённым триггером).

Стратегия — как в test_tasks_order_position.py: временный курс и задания в
транзакции фикстуры `db`.
"""
from __future__ import annotations

from typing import List, Tuple

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services.tasks_service import TasksService

_TASK_CONTENT = '{"type": "SC", "stem": "x", "options": [{"id": "a", "label": "1"}]}'
_SOLUTION_RULES = '{"type": "SC", "correct_options": ["a"], "max_score": 1}'


async def _new_course(db, title: str = "test_tsk813") -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES (:t, 'test', 'self_guided', false)
                RETURNING id
                """
            ),
            {"t": title},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _insert_task(db, course_id: int, *, is_active: bool = True) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO tasks (task_content, course_id, difficulty_id, solution_rules,
                                   max_score, is_active)
                VALUES (CAST(:tc AS jsonb), :cid, 1, CAST(:sr AS jsonb), 1, :act)
                RETURNING id
                """
            ),
            {"tc": _TASK_CONTENT, "cid": course_id, "sr": _SOLUTION_RULES, "act": is_active},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _positions(db, course_id: int) -> List[Tuple[int, int | None]]:
    rows = (
        await db.execute(
            text(
                "SELECT id, order_position FROM tasks WHERE course_id = :c "
                "ORDER BY order_position NULLS LAST, id"
            ),
            {"c": course_id},
        )
    ).all()
    return [(int(r.id), r.order_position) for r in rows]


def _assert_dense_and_unique(rows: List[Tuple[int, int | None]], where: str) -> None:
    positions = [p for _, p in rows]
    assert None not in positions, f"{where}: задание без позиции: {rows}"
    assert len(set(positions)) == len(positions), f"{where}: дубликаты позиций: {rows}"
    assert positions == list(range(1, len(positions) + 1)), (
        f"{where}: позиции не уплотнены 1..N: {rows}"
    )


# ---------- Рубеж 1: сервис достраивает порядок ----------


@pytest.mark.asyncio
async def test_partial_reorder_of_active_does_not_collide_with_inactive(db):
    """Регрессия tsk-810: перестановка под фильтром «Активные».

    Кабинет присылает 1..N только для активных заданий. Раньше выключенные
    оставались на тех же числах — получались дубли ровно как в курсах 146/147.
    """
    course_id = await _new_course(db, "test_tsk813_filter")
    active = [await _insert_task(db, course_id) for _ in range(3)]
    inactive = [await _insert_task(db, course_id, is_active=False) for _ in range(2)]

    # Клиент видит только активные и нумерует их от единицы, поменяв местами
    # первое и второе.
    service = TasksService()
    await service.reorder_tasks(
        db,
        course_id,
        [
            {"task_id": active[1], "order_position": 1},
            {"task_id": active[0], "order_position": 2},
            {"task_id": active[2], "order_position": 3},
        ],
    )

    rows = await _positions(db, course_id)
    _assert_dense_and_unique(rows, "курс после частичного реордера")
    # Активные встали как просил клиент, выключенные заполнили хвост,
    # сохранив взаимный порядок.
    assert [tid for tid, _ in rows] == [
        active[1], active[0], active[2], inactive[0], inactive[1],
    ]


@pytest.mark.asyncio
async def test_partial_reorder_keeps_untouched_order(db):
    """Непереданные задания сохраняют взаимный порядок, заполняя свободные места."""
    course_id = await _new_course(db, "test_tsk813_keep")
    ids = [await _insert_task(db, course_id) for _ in range(5)]

    service = TasksService()
    # Двигаем только последнее задание в начало.
    await service.reorder_tasks(db, course_id, [{"task_id": ids[4], "order_position": 1}])

    rows = await _positions(db, course_id)
    _assert_dense_and_unique(rows, "курс")
    assert [tid for tid, _ in rows] == [ids[4], ids[0], ids[1], ids[2], ids[3]]


@pytest.mark.asyncio
async def test_reorder_returns_whole_course_order(db):
    """Ответ несёт весь порядок курса, а не только переданные задания."""
    course_id = await _new_course(db, "test_tsk813_answer")
    ids = [await _insert_task(db, course_id) for _ in range(4)]

    service = TasksService()
    result = await service.reorder_tasks(
        db, course_id, [{"task_id": ids[3], "order_position": 1}]
    )

    assert len(result) == len(ids)
    assert {t.id for t in result} == set(ids)


@pytest.mark.asyncio
async def test_full_reorder_still_works(db):
    """Полный порядок применяется как прежде — поведение не изменилось."""
    course_id = await _new_course(db, "test_tsk813_full")
    ids = [await _insert_task(db, course_id) for _ in range(4)]

    service = TasksService()
    await service.reorder_tasks(
        db,
        course_id,
        [
            {"task_id": ids[3], "order_position": 1},
            {"task_id": ids[2], "order_position": 2},
            {"task_id": ids[1], "order_position": 3},
            {"task_id": ids[0], "order_position": 4},
        ],
    )

    rows = await _positions(db, course_id)
    _assert_dense_and_unique(rows, "курс")
    assert [tid for tid, _ in rows] == [ids[3], ids[2], ids[1], ids[0]]


@pytest.mark.asyncio
async def test_sparse_positions_are_compacted(db):
    """Разрежённые позиции от клиента уплотняются, дыр не остаётся.

    Клиент вправе прислать 1 и 100 — курс всё равно получит плотный 1..N.
    """
    course_id = await _new_course(db, "test_tsk813_sparse")
    ids = [await _insert_task(db, course_id) for _ in range(4)]

    service = TasksService()
    await service.reorder_tasks(
        db,
        course_id,
        [
            {"task_id": ids[0], "order_position": 1},
            {"task_id": ids[1], "order_position": 100},
        ],
    )

    rows = await _positions(db, course_id)
    positions = [p for _, p in rows]
    assert len(set(positions)) == len(positions), f"дубликаты: {rows}"
    # Переданные позиции уважаются как есть (1 и 100), остальные занимают
    # свободные места по возрастанию — дублей нет ни при каком раскладе.
    assert dict(rows)[ids[0]] == 1
    assert dict(rows)[ids[1]] == 100


@pytest.mark.asyncio
async def test_reorder_is_idempotent(db):
    """Повторный тот же реордер ничего не меняет."""
    course_id = await _new_course(db, "test_tsk813_idem")
    ids = [await _insert_task(db, course_id) for _ in range(4)]

    service = TasksService()
    orders = [{"task_id": ids[2], "order_position": 1}]
    await service.reorder_tasks(db, course_id, orders)
    first = await _positions(db, course_id)
    await service.reorder_tasks(db, course_id, orders)
    assert await _positions(db, course_id) == first


# ---------- Рубеж 2: ограничение БД ----------


@pytest.mark.asyncio
async def test_database_rejects_duplicate_position(db):
    """Прямая правка с заглушённым триггером не может оставить дубль.

    Так работают ad-hoc скрипты правки данных — сервис им не указ, поэтому
    последний рубеж на уровне БД обязателен.

    Проверка идёт через `SET CONSTRAINTS ... IMMEDIATE`, а не через `commit()`:
    тесты выполняются внутри внешней транзакции с SAVEPOINT (см. conftest),
    их `commit()` закрывает SAVEPOINT, а отложенное ограничение срабатывает на
    COMMIT настоящей транзакции — до теста эта ошибка просто не доехала бы.
    `SET CONSTRAINTS IMMEDIATE` требует проверить отложенное прямо сейчас, то
    есть моделирует момент коммита.
    """
    course_id = await _new_course(db, "test_tsk813_constraint")
    await _insert_task(db, course_id)
    second = await _insert_task(db, course_id)

    await db.execute(
        text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
    )
    await db.execute(
        text("UPDATE tasks SET order_position = 1 WHERE id = :i"), {"i": second}
    )
    # Внутри транзакции дубль допустим — ограничение отложенное.
    rows = await _positions(db, course_id)
    assert [p for _, p in rows].count(1) == 2, f"ожидался временный дубль: {rows}"

    # А вот «дожить до коммита» ему не дадут.
    with pytest.raises(IntegrityError, match="tasks_course_order_unique"):
        await db.execute(text("SET CONSTRAINTS tasks_course_order_unique IMMEDIATE"))
    await db.rollback()


@pytest.mark.asyncio
async def test_constraint_is_deferrable(db):
    """Ограничение обязано быть DEFERRABLE INITIALLY DEFERRED.

    Немедленное отвергало бы законные операции: и триггер, и реордер по дороге
    ставят двум заданиям одно число. Тест держит это свойство — без него
    перестановка заданий сломается на проде, а не в тестах.
    """
    row = (
        await db.execute(
            text(
                "SELECT condeferrable, condeferred FROM pg_constraint "
                "WHERE conname = 'tasks_course_order_unique' "
                "AND conrelid = 'tasks'::regclass"
            )
        )
    ).first()
    assert row is not None, "ограничение tasks_course_order_unique не найдено"
    assert row[0] is True, "ограничение должно быть DEFERRABLE"
    assert row[1] is True, "ограничение должно быть INITIALLY DEFERRED"


@pytest.mark.asyncio
async def test_trigger_shifts_still_pass_through_constraint(db):
    """Обычная вставка с явной позицией проходит: сдвиг соседей — законная операция.

    Ровно ради этого ограничение отложенное: триггер по дороге ставит двум
    заданиям одно число.
    """
    course_id = await _new_course(db, "test_tsk813_shift")
    ids = [await _insert_task(db, course_id) for _ in range(3)]

    row = (
        await db.execute(
            text(
                """
                INSERT INTO tasks (task_content, course_id, difficulty_id, solution_rules,
                                   max_score, order_position)
                VALUES (CAST(:tc AS jsonb), :cid, 1, CAST(:sr AS jsonb), 1, 2)
                RETURNING id
                """
            ),
            {"tc": _TASK_CONTENT, "cid": course_id, "sr": _SOLUTION_RULES},
        )
    ).first()
    await db.flush()
    inserted = int(row.id)

    rows = await _positions(db, course_id)
    _assert_dense_and_unique(rows, "курс после вставки в середину")
    assert [tid for tid, _ in rows] == [ids[0], inserted, ids[1], ids[2]]


@pytest.mark.asyncio
async def test_null_positions_do_not_conflict(db):
    """Задания без позиции ограничению не мешают: два NULL не противоречат друг другу.

    Это про курсы 1491–1494, где позиции не проставлены вовсе.
    """
    course_id = await _new_course(db, "test_tsk813_nulls")
    await db.execute(
        text("SELECT set_config('app.skip_task_order_trigger', 'true', true)")
    )
    first = await _insert_task(db, course_id)
    second = await _insert_task(db, course_id)
    await db.execute(
        text("UPDATE tasks SET order_position = NULL WHERE id = ANY(:ids)"),
        {"ids": [first, second]},
    )
    await db.flush()

    rows = await _positions(db, course_id)
    assert [p for _, p in rows] == [None, None]
