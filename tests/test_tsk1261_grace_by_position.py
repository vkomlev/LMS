"""tsk-1261: элемент, появившийся позади ученика, не становится долгом.

Правило узла (tsk-692) прощает новое, только если ученик закрыл узел целиком.
Два живых случая, где оно молчало:

  1. Новый подкурс подвешен прямо к корню (2104 «Дотренировка», 05.10): в
     корне у любого ученика есть старое незакрытое — правило узла молчит, хотя
     ученик давно решает темы ПОСЛЕ позиции подкурса.
  2. Задание вставлено в середину темы, а ученик когда-то пропустил одно
     старое задание: «всё или ничего» отключало прощение всей темы.

Критерий «ушёл вперёд»: к моменту появления элемента ученик уже закрыл хоть
один элемент, стоящий после него в порядке обхода ТОГО ЖЕ УЗЛА. Случай 1 по
tsk-1294 перевёрнут: зачёт в дальней теме тему не прощает (2104 стал
рекомендуемым, нужда в прощении по корню отпала).

Обратные случаи проверяются отдельно — это та половина, что ломается молча:
  3. Ученик не дошёл до позиции нового элемента — оно остаётся обязательным.
  4. Ученик прошёл дальше уже ПОСЛЕ появления элемента — значит, шёл мимо
     него по порядку, и прощать нечего.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.services.content_grace_service import compute_graced_items
from tests.test_tsk692_content_grace import (
    _complete_material,
    _enroll,
    _new_course,
    _new_material,
    _new_student,
    _pass_task,
)

LONG_AGO = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
INSERTED = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)


async def _link(db, parent_id: int, child_id: int, order_number: int) -> None:
    await db.execute(
        text(
            "INSERT INTO course_parents (course_id, parent_course_id, order_number) "
            "VALUES (:c, :p, :o)"
        ),
        {"c": child_id, "p": parent_id, "o": order_number},
    )


async def _task(db, *, course_id: int, position: int, created_at: datetime | None) -> int:
    difficulty_id = int(
        (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar_one()
    )
    res = await db.execute(
        text(
            "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid, "
            "created_at, order_position) "
            "VALUES (CAST(:tc AS jsonb), :c, :d, :uid, :ts, :pos) RETURNING id"
        ),
        {
            "tc": '{"type": "SA", "question": "tsk1261"}',
            "c": course_id,
            "d": difficulty_id,
            "uid": f"tsk1261-{uuid4().hex[:10]}",
            "ts": created_at,
            "pos": position,
        },
    )
    return int(res.scalar_one())


async def _pass(db, student: int, task_id: int, course_id: int, root_id: int, at: datetime) -> None:
    await _pass_task(
        db, user_id=student, task_id=task_id, course_id=course_id,
        root_course_id=root_id, at=at,
    )


@pytest.mark.asyncio
async def test_far_topic_pass_does_not_grace_unvisited_topic(db):
    """tsk-1294 (ученик 4507): зачёт в дальней теме не прощает непройденную.

    Корень → тема 23 (подразделы «Читаем файл», «Кратчайший путь») → тема 24.
    Ученик давно (массовый зачёт 22.07) имеет решённое в теме 24; подразделы
    темы 23 созданы позже. Он решил «Читаем файл» — «Кратчайший путь» обязан
    остаться обязательным, иначе next-item уводит в тему 24.
    """
    root = await _new_course(db, "tsk1294 корень")
    topic23 = await _new_course(db, "tsk1294 задание 23")
    topic24 = await _new_course(db, "tsk1294 задание 24")
    await _link(db, root, topic23, 1)
    await _link(db, root, topic24, 2)
    far_task = await _task(db, course_id=topic24, position=1, created_at=LONG_AGO)

    student = await _new_student(db, "Селин-сценарий")
    await _enroll(db, student, root)
    await _pass(db, student, far_task, topic24, root, LONG_AGO + timedelta(days=3))

    read_file = await _new_course(db, "tsk1294 читаем файл")
    shortest = await _new_course(db, "tsk1294 кратчайший путь")
    await _link(db, topic23, read_file, 1)
    await _link(db, topic23, shortest, 2)
    rf_task = await _task(db, course_id=read_file, position=1, created_at=INSERTED)
    sp_task = await _task(db, course_id=shortest, position=1, created_at=INSERTED)
    await _pass(db, student, rf_task, read_file, root, INSERTED + timedelta(days=30))
    await db.commit()

    graced = await compute_graced_items(db, student, root)
    assert sp_task not in graced.tasks, (
        "Тема, которую ученик не проходил, остаётся обязательной, даже если "
        "у него есть решения из дальних тем"
    )


@pytest.mark.asyncio
async def test_task_inserted_mid_topic_graced_despite_old_skip(db):
    """Случай 2: вставка в середину темы при одном пропущенном старом задании."""
    topic = await _new_course(db, "tsk1261 тема")
    skipped_old = await _task(db, course_id=topic, position=1, created_at=None)
    done_old = await _task(db, course_id=topic, position=2, created_at=None)
    tail_old = await _task(db, course_id=topic, position=4, created_at=None)

    student = await _new_student(db, "пропустил одно")
    await _enroll(db, student, topic)
    await _pass(db, student, done_old, topic, topic, LONG_AGO + timedelta(days=1))
    await _pass(db, student, tail_old, topic, topic, LONG_AGO + timedelta(days=2))

    inserted = await _task(db, course_id=topic, position=3, created_at=INSERTED)
    await db.commit()

    graced = await compute_graced_items(db, student, topic)
    assert inserted in graced.tasks, (
        "Задание, вставленное перед уже решённым, не должно становиться долгом "
        "из-за одного старого пропуска"
    )
    assert skipped_old not in graced.tasks


@pytest.mark.asyncio
async def test_student_behind_insertion_still_owes_it(db):
    """Случай 3 (обратный): ученик не дошёл до позиции — новое обязательно."""
    topic = await _new_course(db, "tsk1261 тема позади")
    first = await _task(db, course_id=topic, position=1, created_at=LONG_AGO)
    await _task(db, course_id=topic, position=4, created_at=LONG_AGO)
    material = await _new_material(db, course_id=topic, created_at=LONG_AGO, title="Ввод")

    student = await _new_student(db, "не дошёл")
    await _enroll(db, student, topic)
    await _complete_material(db, user_id=student, material_id=material, at=LONG_AGO + timedelta(days=1))
    await _pass(db, student, first, topic, topic, LONG_AGO + timedelta(days=2))

    inserted = await _task(db, course_id=topic, position=3, created_at=INSERTED)
    await db.commit()

    graced = await compute_graced_items(db, student, topic)
    assert inserted not in graced.tasks, (
        "Ученику, не дошедшему до места вставки, новое задание обязано "
        "оставаться обязательным"
    )


@pytest.mark.asyncio
async def test_going_ahead_after_insertion_does_not_grace(db):
    """Случай 4 (обратный): дальше ушёл уже ПОСЛЕ вставки — прощать нечего.

    Так выглядят ученики из лога 04–06.10: движок привёл их к новому заданию
    по порядку, и задания за ним они решали позже.
    """
    topic = await _new_course(db, "tsk1261 тема по порядку")
    first = await _task(db, course_id=topic, position=1, created_at=LONG_AGO)
    tail = await _task(db, course_id=topic, position=4, created_at=LONG_AGO)
    inserted = await _task(db, course_id=topic, position=3, created_at=INSERTED)

    student = await _new_student(db, "по порядку")
    await _enroll(db, student, topic)
    await _pass(db, student, first, topic, topic, LONG_AGO + timedelta(days=1))
    await _pass(db, student, tail, topic, topic, INSERTED + timedelta(days=1))
    await db.commit()

    graced = await compute_graced_items(db, student, topic)
    assert inserted not in graced.tasks, (
        "Элемент, мимо которого ученик прошёл уже после его появления, "
        "прощать нельзя"
    )
