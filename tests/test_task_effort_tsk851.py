"""tsk-851: вес задания в минутах работы — измеритель для объёма ДЗ и прогноза.

Объём домашней работы и прогноз окончания считались в ШТУКАХ, а элементы
разновесные: двадцать заданий с выбором ответа — это четыре минуты дома,
двадцать задач с решением — семьдесят девять. Этот модуль меряет вес, решения
по нему принимает `homework_volume_service`.

Тесты держат четыре свойства, каждое из которых при поломке молчит:

1. вес берётся из ПАРЫ (сложность, формат), а при нехватке выборки падает на
   формат — не на сложность: на боевых данных «сложное» с выбором ответа
   занимает столько же, сколько лёгкое с выбором;
2. в выборку идут только настоящие ученические сдачи;
3. полуторачасовое «решение» (ушёл и вернулся) не тянет медиану;
4. задание без типа не подменяет собой общую медиану платформы — дефект,
   найденный до тестов: строка `GROUPING SETS` с настоящим `NULL` неотличима
   от итоговой по значению.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.task_effort_service import (
    MIN_CELL_SAMPLES,
    EffortTable,
    effort_for_tasks,
    load_effort_table,
)

pytestmark = pytest.mark.asyncio

_EASY, _HARD = 2, 4


async def _student(db, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db) -> int:
    res = await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES ('tsk851','self_guided',false,:u) RETURNING id"
    ), {"u": f"tsk851-{random.randint(10**8, 10**10)}"})
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _task(db, course_id: int, *, difficulty_id: int, task_type: str | None) -> int:
    content = {"stem": "условие"}
    if task_type is not None:
        content["type"] = task_type
    res = await db.execute(text(
        "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
        "VALUES (CAST(:c AS jsonb), :course, :d, :u) RETURNING id"
    ), {"c": json.dumps(content), "course": course_id, "d": difficulty_id,
        "u": f"tsk851-{random.randint(10**8, 10**10)}"})
    tid = int(res.scalar_one())
    await db.commit()
    return tid


async def _pairs(
    db, *, user_id: int, task_id: int, course_id: int, count: int,
    seconds: int, source: str = "spw_web",
) -> None:
    """`count` пар «открыл → сдал», каждая длиной `seconds`."""
    res = await db.execute(text(
        "INSERT INTO attempts (user_id, course_id) VALUES (:u,:c) RETURNING id"
    ), {"u": user_id, "c": course_id})
    attempt_id = int(res.scalar_one())
    for i in range(count):
        submitted_ago = (i + 1) * 3600 * 2  # пары далеко друг от друга
        await db.execute(text("""
            INSERT INTO learning_events (student_id, event_type, payload, created_at)
            VALUES (:u, 'task_opened', CAST(:p AS jsonb),
                    now() - make_interval(secs => :off))
        """), {"u": user_id, "p": json.dumps({"task_id": task_id}),
               "off": submitted_ago + seconds})
        await db.execute(text("""
            INSERT INTO task_results (user_id, task_id, attempt_id, answer_json,
                                      score, max_score, is_correct,
                                      submitted_at, received_at, source_system)
            VALUES (:u,:t,:a, CAST('{"answer":"x"}' AS jsonb), 1, 1, true,
                    now() - make_interval(secs => :off),
                    now() - make_interval(secs => :off), :src)
        """), {"u": user_id, "t": task_id, "a": attempt_id,
               "off": submitted_ago, "src": source})
    await db.commit()


async def _cleanup(db, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM learning_events WHERE student_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


def _unique_type() -> str:
    """Формат, встречающийся только в этом тесте.

    Вес считается по ВСЕЙ базе — на общем формате результат зависел бы от
    данных соседних тестов, и объяснить расхождение было бы нечем.
    """
    return f"T851_{random.randint(10**8, 10**10)}"


async def test_weight_comes_from_difficulty_and_format_pair(db):
    """Ячейка с достаточной выборкой и есть вес задания."""
    ttype = _unique_type()
    course = await _course(db)
    task = await _task(db, course, difficulty_id=_EASY, task_type=ttype)
    student = await _student(db, "eff-a")
    try:
        await _pairs(db, user_id=student, task_id=task, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=30)
        table = await load_effort_table(db)
        weight = table.seconds_for(difficulty_id=_EASY, task_type=ttype)
        assert weight is not None
        assert 25 <= weight <= 35, weight
        assert table.minutes_for(difficulty_id=_EASY, task_type=ttype) == pytest.approx(
            weight / 60,
        )
    finally:
        await _cleanup(db, [student], [course])


async def test_thin_cell_falls_back_to_format_not_to_difficulty(db):
    """Не хватило выборки у пары — берём ФОРМАТ, а не соседнюю сложность.

    Порядок отступления решает всё: на проде «сложное» с выбором ответа
    занимает 14 секунд, ровно как лёгкое с выбором, а «лёгкая» задача с
    решением — 236. Формат ближе к правде, чем уровень сложности.
    """
    ttype = _unique_type()
    course = await _course(db)
    fat = await _task(db, course, difficulty_id=_EASY, task_type=ttype)
    thin = await _task(db, course, difficulty_id=_HARD, task_type=ttype)
    student = await _student(db, "eff-b")
    try:
        await _pairs(db, user_id=student, task_id=fat, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=40)
        await _pairs(db, user_id=student, task_id=thin, course_id=course,
                     count=3, seconds=900)
        table = await load_effort_table(db)

        assert (_HARD, ttype) not in table.by_cell, "ячейка из трёх наблюдений не вес"
        weight = table.seconds_for(difficulty_id=_HARD, task_type=ttype)
        # Медиана формата собрана из 20 наблюдений по 40 с и 3 по 900 с.
        assert weight == pytest.approx(table.by_type[ttype])
        assert weight < 100, "взяли не формат, а что-то другое"
    finally:
        await _cleanup(db, [student], [course])


async def test_unknown_format_falls_back_to_overall(db):
    """Формат, которого никто не решал, получает общий вес платформы."""
    ttype = _unique_type()
    course = await _course(db)
    task = await _task(db, course, difficulty_id=_EASY, task_type=ttype)
    student = await _student(db, "eff-g")
    try:
        await _pairs(db, user_id=student, task_id=task, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=30)
        table = await load_effort_table(db)
        assert table.overall is not None
        assert table.seconds_for(
            difficulty_id=None, task_type=_unique_type(),
        ) == table.overall
    finally:
        await _cleanup(db, [student], [course])


async def test_empty_table_says_none_instead_of_guessing():
    """Мерить нечем — так и говорим.

    Выдуманное число здесь опаснее пустоты: по нему посчитается объём ДЗ и
    прогноз окончания, и никто не узнает, что нормы никто не мерил.
    """
    empty = EffortTable(by_cell={}, by_type={}, overall=None, window_days=90, samples=0)
    assert empty.seconds_for(difficulty_id=1, task_type="SC") is None
    assert empty.minutes_for(difficulty_id=1, task_type="SC") is None


async def test_teacher_backfill_is_not_a_measurement(db):
    """Ручная простановка преподавателя весом не становится."""
    ttype = _unique_type()
    course = await _course(db)
    task = await _task(db, course, difficulty_id=_EASY, task_type=ttype)
    student = await _student(db, "eff-c")
    try:
        await _pairs(db, user_id=student, task_id=task, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=30, source="manual_teacher")
        table = await load_effort_table(db)
        assert (_EASY, ttype) not in table.by_cell
        assert ttype not in table.by_type
    finally:
        await _cleanup(db, [student], [course])


async def test_hour_long_gap_does_not_drag_the_median(db):
    """Ушёл и вернулся через два часа — это не время решения.

    Без обрезки один такой случай на двадцать честных превращает лёгкое
    задание в получасовое, а норму ДЗ — в три задания на неделю.
    """
    ttype = _unique_type()
    course = await _course(db)
    task = await _task(db, course, difficulty_id=_EASY, task_type=ttype)
    student = await _student(db, "eff-d")
    try:
        await _pairs(db, user_id=student, task_id=task, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=30)
        await _pairs(db, user_id=student, task_id=task, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=7200)
        table = await load_effort_table(db)
        weight = table.seconds_for(difficulty_id=_EASY, task_type=ttype)
        assert weight is not None and weight < 60, weight
    finally:
        await _cleanup(db, [student], [course])


async def test_task_without_type_does_not_become_the_overall_median(db):
    """Задание без формата не подменяет собой общий вес платформы.

    Дефект, найденный до тестов: в `GROUPING SETS` строка с настоящим `NULL`
    выглядит так же, как итоговая по всем данным. Спутать их — значит объявить
    вес одной ячейки нормой всей платформы.
    """
    ttype = _unique_type()
    course = await _course(db)
    typed = await _task(db, course, difficulty_id=_EASY, task_type=ttype)
    untyped = await _task(db, course, difficulty_id=_EASY, task_type=None)
    student = await _student(db, "eff-e")
    try:
        # Быстрых наблюдений вдвое больше, чем медленных без формата: медиана
        # обязана остаться у них. При путанице уровней общей медианой стало бы
        # значение ячейки без формата — полчаса на задание.
        await _pairs(db, user_id=student, task_id=typed, course_id=course,
                     count=MIN_CELL_SAMPLES * 2, seconds=30)
        await _pairs(db, user_id=student, task_id=untyped, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=1800)
        table = await load_effort_table(db)
        assert table.overall is not None
        assert table.overall < 100, table.overall
        # Наблюдения без формата не потерялись — они легли в свою ячейку.
        assert table.by_cell.get((_EASY, None)) == pytest.approx(1800, abs=60)
    finally:
        await _cleanup(db, [student], [course])


async def test_effort_for_tasks_answers_per_task(db):
    """Вес по списку заданий; несуществующее задание в ответ не попадает."""
    ttype = _unique_type()
    course = await _course(db)
    task = await _task(db, course, difficulty_id=_EASY, task_type=ttype)
    student = await _student(db, "eff-f")
    try:
        await _pairs(db, user_id=student, task_id=task, course_id=course,
                     count=MIN_CELL_SAMPLES, seconds=45)
        weights = await effort_for_tasks(db, task_ids=[task, -1])
        assert set(weights) == {task}
        assert weights[task] is not None and 40 <= weights[task] <= 50
        assert await effort_for_tasks(db, task_ids=[]) == {}
    finally:
        await _cleanup(db, [student], [course])
