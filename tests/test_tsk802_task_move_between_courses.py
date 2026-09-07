"""tsk-802: перенос задания между курсами не портит позиции соседей.

Дефект (найден в tsk-801): `trg_set_task_order_position` на UPDATE сравнивал
`OLD.order_position` с `NEW.order_position`, не глядя на смену `course_id`, —
и сдвигал задания ЦЕЛЕВОГО курса по диапазону, вычисленному из позиции в
ИСХОДНОМ. Соседнее задание, которого никто не трогал, получало чужую позицию
и дубликат. Молча, без ошибки.

Стратегия — как в test_tasks_order_position.py: временные курсы и задачи в
транзакции фикстуры `db` (rollback после теста, в БД ничего не остаётся),
`flush()` вместо `commit()` — триггеры срабатывают всё равно.
"""
from __future__ import annotations

from typing import List, Tuple

import pytest
from sqlalchemy import text


_TASK_CONTENT = '{"type": "SC", "stem": "x", "options": [{"id": "a", "label": "1"}]}'
_SOLUTION_RULES = '{"type": "SC", "correct_options": ["a"], "max_score": 1}'


async def _new_course(db, title: str = "test_tsk802_move") -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO courses (title, description, access_level, is_required)
                VALUES (:title, 'test', 'self_guided', false)
                RETURNING id
                """
            ),
            {"title": title},
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _insert_task(db, course_id: int, order_position: int | None = None) -> int:
    row = (
        await db.execute(
            text(
                """
                INSERT INTO tasks (task_content, course_id, difficulty_id, solution_rules,
                                   max_score, order_position)
                VALUES (CAST(:tc AS jsonb), :cid, 1, CAST(:sr AS jsonb), 1, :pos)
                RETURNING id
                """
            ),
            {
                "tc": _TASK_CONTENT,
                "cid": course_id,
                "sr": _SOLUTION_RULES,
                "pos": order_position,
            },
        )
    ).first()
    await db.flush()
    return int(row.id)


async def _positions(db, course_id: int) -> List[Tuple[int, int | None]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT id, order_position
                FROM tasks
                WHERE course_id = :cid
                ORDER BY order_position NULLS LAST, id
                """
            ),
            {"cid": course_id},
        )
    ).all()
    return [
        (int(r.id), int(r.order_position) if r.order_position is not None else None)
        for r in rows
    ]


def _assert_dense_and_unique(rows: List[Tuple[int, int | None]], where: str) -> None:
    """Позиции курса — 1..N без дыр и дублей."""
    positions = [pos for _, pos in rows]
    assert None not in positions, f"{where}: есть задание без позиции: {rows}"
    assert len(set(positions)) == len(positions), f"{where}: дубликаты позиций: {rows}"
    assert positions == list(range(1, len(positions) + 1)), (
        f"{where}: позиции не уплотнены 1..N: {rows}"
    )


# ---------- Регрессия исходного инцидента ----------


@pytest.mark.asyncio
async def test_move_does_not_shift_strangers_in_target_course(db):
    """Регрессия tsk-802: перенос 108/41 -> 111/43 не двигает чужие задания.

    Воспроизводит сценарий tsk-801 в миниатюре: в целевом курсе есть задание
    на позиции 41 (аналог задания 190). До починки оно уезжало на 40, где уже
    стояло другое, — дубликат в курсе, которого никто не трогал.
    """
    src = await _new_course(db, "test_tsk802_src")
    dst = await _new_course(db, "test_tsk802_dst")

    # Исходный курс: 5 заданий, переезжает четвёртое (позиция 4).
    src_tasks = [await _insert_task(db, src) for _ in range(5)]
    moving = src_tasks[3]

    # Целевой курс: 5 заданий, встраиваемся между 2-м и 3-м.
    dst_tasks = [await _insert_task(db, dst) for _ in range(5)]
    dst_before = await _positions(db, dst)

    await db.execute(
        text("UPDATE tasks SET course_id = :dst, order_position = 3 WHERE id = :i"),
        {"dst": dst, "i": moving},
    )
    await db.flush()

    dst_after = await _positions(db, dst)
    _assert_dense_and_unique(dst_after, "целевой курс")

    # Порядок чужих заданий целевого курса сохранён, вставка ровно на 3-е место.
    assert [tid for tid, _ in dst_after] == [
        dst_tasks[0],
        dst_tasks[1],
        moving,
        dst_tasks[2],
        dst_tasks[3],
        dst_tasks[4],
    ]
    # Ни одно чужое задание не потеряло относительный порядок.
    assert [tid for tid, _ in dst_before] == [
        tid for tid, _ in dst_after if tid != moving
    ]

    # Исходный курс уплотнён — дыры на месте уехавшего задания нет.
    src_after = await _positions(db, src)
    _assert_dense_and_unique(src_after, "исходный курс")
    assert [tid for tid, _ in src_after] == [
        src_tasks[0],
        src_tasks[1],
        src_tasks[2],
        src_tasks[4],
    ]


# ---------- Направление переезда ----------


@pytest.mark.asyncio
async def test_move_forward_by_position(db):
    """Переезд «вперёд»: новая позиция БОЛЬШЕ старой (ветка, сломавшая tsk-801)."""
    src = await _new_course(db, "test_tsk802_fwd_src")
    dst = await _new_course(db, "test_tsk802_fwd_dst")

    src_tasks = [await _insert_task(db, src) for _ in range(3)]
    dst_tasks = [await _insert_task(db, dst) for _ in range(5)]
    moving = src_tasks[0]  # позиция 1 -> позиция 4

    await db.execute(
        text("UPDATE tasks SET course_id = :dst, order_position = 4 WHERE id = :i"),
        {"dst": dst, "i": moving},
    )
    await db.flush()

    dst_after = await _positions(db, dst)
    _assert_dense_and_unique(dst_after, "целевой курс")
    assert [tid for tid, _ in dst_after] == [
        dst_tasks[0],
        dst_tasks[1],
        dst_tasks[2],
        moving,
        dst_tasks[3],
        dst_tasks[4],
    ]
    _assert_dense_and_unique(await _positions(db, src), "исходный курс")


@pytest.mark.asyncio
async def test_move_backward_by_position(db):
    """Переезд «назад»: новая позиция МЕНЬШЕ старой."""
    src = await _new_course(db, "test_tsk802_bwd_src")
    dst = await _new_course(db, "test_tsk802_bwd_dst")

    src_tasks = [await _insert_task(db, src) for _ in range(5)]
    dst_tasks = [await _insert_task(db, dst) for _ in range(3)]
    moving = src_tasks[4]  # позиция 5 -> позиция 1

    await db.execute(
        text("UPDATE tasks SET course_id = :dst, order_position = 1 WHERE id = :i"),
        {"dst": dst, "i": moving},
    )
    await db.flush()

    dst_after = await _positions(db, dst)
    _assert_dense_and_unique(dst_after, "целевой курс")
    assert [tid for tid, _ in dst_after] == [moving, *dst_tasks]
    _assert_dense_and_unique(await _positions(db, src), "исходный курс")


# ---------- Позиция не задана ----------


@pytest.mark.asyncio
async def test_move_without_position_out_of_range_goes_to_end(db):
    """`UPDATE ... SET course_id` без позиции: цифра из исходного курса
    недостижима в целевом -> задание встаёт в конец, дыр не остаётся.

    Триггер не видит списка колонок в SET: такой UPDATE доезжает до него как
    `NEW.order_position = OLD.order_position` — унаследованная цифра из чужого
    курса. Контракт (миграция tsk802_task_move_courses): позиция зажимается в
    диапазон целевого курса. Это типовой перенос из большого курса в
    маленький — прежде он оставлял дыру на всём диапазоне между.
    """
    src = await _new_course(db, "test_tsk802_end_src")
    dst = await _new_course(db, "test_tsk802_end_dst")

    src_tasks = [await _insert_task(db, src) for _ in range(6)]
    dst_tasks = [await _insert_task(db, dst) for _ in range(2)]
    moving = src_tasks[5]  # позиция 6; в целевом курсе всего 2 задания

    await db.execute(
        text("UPDATE tasks SET course_id = :dst WHERE id = :i"),
        {"dst": dst, "i": moving},
    )
    await db.flush()

    dst_after = await _positions(db, dst)
    _assert_dense_and_unique(dst_after, "целевой курс")
    assert [tid for tid, _ in dst_after] == [*dst_tasks, moving]
    _assert_dense_and_unique(await _positions(db, src), "исходный курс")


@pytest.mark.asyncio
async def test_move_with_position_equal_to_old_is_respected(db):
    """Позиция, совпавшая со старой, — обычный перенос «на то же место».

    Проверяет обратную сторону контракта: догадка «совпало -> значит позицию
    не задавали» была бы неверной. Задание переезжает со 2-й позиции
    исходного курса на 2-ю позицию целевого — и встаёт именно туда.
    """
    src = await _new_course(db, "test_tsk802_same_src")
    dst = await _new_course(db, "test_tsk802_same_dst")

    src_tasks = [await _insert_task(db, src) for _ in range(4)]
    dst_tasks = [await _insert_task(db, dst) for _ in range(4)]
    moving = src_tasks[1]  # позиция 2 -> позиция 2, но в другом курсе

    await db.execute(
        text("UPDATE tasks SET course_id = :dst, order_position = 2 WHERE id = :i"),
        {"dst": dst, "i": moving},
    )
    await db.flush()

    dst_after = await _positions(db, dst)
    _assert_dense_and_unique(dst_after, "целевой курс")
    assert [tid for tid, _ in dst_after] == [
        dst_tasks[0],
        moving,
        dst_tasks[1],
        dst_tasks[2],
        dst_tasks[3],
    ]
    _assert_dense_and_unique(await _positions(db, src), "исходный курс")


@pytest.mark.asyncio
async def test_move_with_explicit_null_position_goes_to_end(db):
    """Явный `order_position = NULL` при переезде -> в конец целевого курса."""
    src = await _new_course(db, "test_tsk802_null_src")
    dst = await _new_course(db, "test_tsk802_null_dst")

    src_tasks = [await _insert_task(db, src) for _ in range(3)]
    dst_tasks = [await _insert_task(db, dst) for _ in range(4)]
    moving = src_tasks[2]

    await db.execute(
        text(
            "UPDATE tasks SET course_id = :dst, order_position = NULL WHERE id = :i"
        ),
        {"dst": dst, "i": moving},
    )
    await db.flush()

    dst_after = await _positions(db, dst)
    _assert_dense_and_unique(dst_after, "целевой курс")
    assert [tid for tid, _ in dst_after] == [*dst_tasks, moving]
    _assert_dense_and_unique(await _positions(db, src), "исходный курс")


@pytest.mark.asyncio
async def test_move_into_empty_course(db):
    """Переезд в пустой курс -> позиция 1, исходный курс уплотнён."""
    src = await _new_course(db, "test_tsk802_empty_src")
    dst = await _new_course(db, "test_tsk802_empty_dst")

    src_tasks = [await _insert_task(db, src) for _ in range(3)]
    moving = src_tasks[0]

    await db.execute(
        text("UPDATE tasks SET course_id = :dst WHERE id = :i"),
        {"dst": dst, "i": moving},
    )
    await db.flush()

    assert await _positions(db, dst) == [(moving, 1)]
    assert await _positions(db, src) == [(src_tasks[1], 1), (src_tasks[2], 2)]


# ---------- Пакетный перенос ----------


@pytest.mark.asyncio
async def test_move_two_tasks_one_after_another(db):
    """Перенос двух заданий подряд (сценарий tsk-801: задания 145 и 146).

    Каждое — отдельный UPDATE; после обоих в целевом курсе нет ни дыр, ни
    дублей, а исходный уплотнён.
    """
    src = await _new_course(db, "test_tsk802_pair_src")
    dst = await _new_course(db, "test_tsk802_pair_dst")

    src_tasks = [await _insert_task(db, src) for _ in range(4)]
    dst_tasks = [await _insert_task(db, dst) for _ in range(4)]
    first, second = src_tasks[1], src_tasks[2]

    await db.execute(
        text("UPDATE tasks SET course_id = :dst, order_position = 2 WHERE id = :i"),
        {"dst": dst, "i": first},
    )
    await db.flush()
    await db.execute(
        text("UPDATE tasks SET course_id = :dst, order_position = 3 WHERE id = :i"),
        {"dst": dst, "i": second},
    )
    await db.flush()

    dst_after = await _positions(db, dst)
    _assert_dense_and_unique(dst_after, "целевой курс")
    assert [tid for tid, _ in dst_after] == [
        dst_tasks[0],
        first,
        second,
        dst_tasks[1],
        dst_tasks[2],
        dst_tasks[3],
    ]

    src_after = await _positions(db, src)
    _assert_dense_and_unique(src_after, "исходный курс")
    assert [tid for tid, _ in src_after] == [src_tasks[0], src_tasks[3]]


# ---------- Соседние курсы не затронуты ----------


@pytest.mark.asyncio
async def test_move_does_not_touch_third_course(db):
    """Переезд между двумя курсами не задевает третий."""
    src = await _new_course(db, "test_tsk802_third_src")
    dst = await _new_course(db, "test_tsk802_third_dst")
    other = await _new_course(db, "test_tsk802_third_other")

    src_tasks = [await _insert_task(db, src) for _ in range(3)]
    await _insert_task(db, dst)
    other_tasks = [await _insert_task(db, other) for _ in range(3)]
    other_before = await _positions(db, other)

    await db.execute(
        text("UPDATE tasks SET course_id = :dst, order_position = 1 WHERE id = :i"),
        {"dst": dst, "i": src_tasks[1]},
    )
    await db.flush()

    assert await _positions(db, other) == other_before
    assert [tid for tid, _ in other_before] == other_tasks
