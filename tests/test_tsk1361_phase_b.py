"""tsk-1361 фаза Б: единый источник уровня, порядок разрешения, свежесть.

Порядок (решение оператора 15.10): решение человека главнее правила на любом
уровне дерева; внутри одного источника — ближайшее к элементу. Прошёл тему в
одном курсе — необязательно везде. Экран отражает зачёт сразу: быстрый тик
находит несвежих учеников по данным.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.content_grace_service import compute_graced_items, invalidate_grace_cache
from app.services.requirement_override_service import (
    resolve_levels,
    set_human_override,
    stale_students,
)
from tests.test_tsk1261_grace_by_position import _link, _pass, _task
from tests.test_tsk1361_requirement_override import INSERTED, LONG_AGO, _scope, _sync
from tests.test_tsk692_content_grace import _enroll, _new_course, _new_student


async def _graced_topic(db):
    """Тема, где ученик ушёл вперёд, а позади вставили задание (правило его прощает)."""
    topic = await _new_course(db, "tsk1361Б тема")
    done = await _task(db, course_id=topic, position=5, created_at=None)
    student = await _new_student(db, "Б")
    await _enroll(db, student, topic)
    await _pass(db, student, done, topic, topic, LONG_AGO)
    inserted = await _task(db, course_id=topic, position=2, created_at=INSERTED)
    await db.commit()
    await _sync(db, student)
    return topic, student, inserted


@pytest.mark.asyncio
async def test_human_on_node_beats_rule_on_element(db):
    """Учитель сделал тему обязательной целиком — правило на задании внутри не побеждает."""
    topic, student, inserted = await _graced_topic(db)
    assert ("task", inserted) in (await resolve_levels(db, student)).demoted

    teacher = await _new_student(db, "учитель Б")
    await set_human_override(
        db, student_id=student, target_kind="course", target_id=topic,
        level="required", by_user_id=teacher,
    )
    await db.commit()
    await _sync(db, student)  # правило на элементе остаётся в таблице
    resolved = await resolve_levels(db, student)
    assert ("task", inserted) not in resolved.demoted


@pytest.mark.asyncio
async def test_program_trim_node_expands_to_nested_subtree(db):
    """Выпавший номер понижает и задания вложенных подкурсов."""
    root = await _new_course(db, "tsk1361Б ЕГЭ")
    number = await _new_course(db, "tsk1361Б номер")
    nested = await _new_course(db, "tsk1361Б подтема")
    await _link(db, root, number, 1)
    await _link(db, number, nested, 1)
    deep = await _task(db, course_id=nested, position=1, created_at=None)
    student = await _new_student(db, "сокращение Б")
    await _enroll(db, student, root)
    await _scope(db, student, [number])
    await db.commit()
    await _sync(db, student)

    resolved = await resolve_levels(db, student)
    assert ("task", deep) in resolved.demoted
    assert resolved.reasons[("task", deep)].reason_code == "program_trim"


@pytest.mark.asyncio
async def test_teacher_promotes_recommended_task(db):
    """Учитель делает необязательное по курсу задание обязательным ученику."""
    topic = await _new_course(db, "tsk1361Б повышение")
    task = await _task(db, course_id=topic, position=1, created_at=None)
    await db.execute(
        text("UPDATE tasks SET requirement_level = 'recommended' WHERE id = :t"), {"t": task}
    )
    student = await _new_student(db, "повышение")
    teacher = await _new_student(db, "учитель повышение")
    await _enroll(db, student, topic)
    await set_human_override(
        db, student_id=student, target_kind="task", target_id=task,
        level="required", by_user_id=teacher, reason_text="к пробнику",
    )
    await db.commit()

    resolved = await resolve_levels(db, student)
    assert ("task", task) in resolved.promoted
    assert resolved.reasons[("task", task)].reason_text == "к пробнику"


@pytest.mark.asyncio
async def test_read_switch_off_and_on_give_same_grace(db, monkeypatch):
    """Рубильник Б2: выключен — расчёт на лету, включён — таблица; результат один."""
    topic, student, inserted = await _graced_topic(db)

    monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "false")
    invalidate_grace_cache(db)
    off = await compute_graced_items(db, student, topic)

    monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "true")
    invalidate_grace_cache(db)
    on = await compute_graced_items(db, student, topic)
    invalidate_grace_cache(db)

    assert off.tasks == on.tasks == frozenset({inserted})


@pytest.mark.asyncio
async def test_stale_detection_by_submission_and_content_change(db):
    """После пересчёта ученик свеж; новая сдача или правка его курса — снова к пересчёту."""
    topic, student, _ = await _graced_topic(db)
    # Отметка свежести — начало транзакции; отодвигаем её в прошлое явно,
    # чтобы данные «после отметки» были однозначными.
    mark = datetime.now(timezone.utc) - timedelta(minutes=10)
    await db.execute(
        text("UPDATE student_requirement_sync SET synced_at = :m WHERE student_id = :s"),
        {"m": mark, "s": student},
    )
    await db.execute(
        text(
            "UPDATE task_results SET submitted_at = :old, checked_at = NULL WHERE user_id = :s"
        ),
        {"old": mark - timedelta(days=1), "s": student},
    )
    await db.execute(
        text("UPDATE tasks SET created_at = :old, updated_at = :old WHERE course_id = :c"),
        {"old": mark - timedelta(days=1), "c": topic},
    )
    await db.execute(
        text("UPDATE user_courses SET added_at = :old WHERE user_id = :s"),
        {"old": mark - timedelta(days=1), "s": student},
    )
    await db.commit()
    assert student not in await stale_students(db)

    later = await _task(db, course_id=topic, position=9, created_at=datetime.now(timezone.utc))
    await db.commit()
    assert student in await stale_students(db), "Правка курса делает ученика несвежим"

    await db.execute(
        text("UPDATE tasks SET created_at = :old, updated_at = :old WHERE id = :t"),
        {"old": mark - timedelta(days=1), "t": later},
    )
    await _pass(db, student, later, topic, topic, datetime.now(timezone.utc))
    await db.commit()
    assert student in await stale_students(db), "Новая сдача делает ученика несвежим"


@pytest.mark.asyncio
async def test_tie_between_two_parents_latest_decision_wins(db):
    """Подкурс у двух родителей с разными решениями на одной глубине — побеждает позднее."""
    parent_a = await _new_course(db, "tsk1361Б родитель А")
    parent_b = await _new_course(db, "tsk1361Б родитель Б")
    shared = await _new_course(db, "tsk1361Б общий подкурс")
    await _link(db, parent_a, shared, 1)
    await _link(db, parent_b, shared, 1)
    task = await _task(db, course_id=shared, position=1, created_at=None)
    student = await _new_student(db, "ничья")
    teacher = await _new_student(db, "учитель ничья")
    await _enroll(db, student, parent_a)
    await _enroll(db, student, parent_b)
    await set_human_override(
        db, student_id=student, target_kind="course", target_id=parent_a,
        level="recommended", by_user_id=teacher,
    )
    await db.execute(
        text("UPDATE student_requirement_override SET created_at = now() - interval '1 day' "
             " WHERE student_id = :s"),
        {"s": student},
    )
    await set_human_override(
        db, student_id=student, target_kind="course", target_id=parent_b,
        level="required", by_user_id=teacher,
    )
    await db.commit()

    for _ in range(3):  # результат не должен «плавать» от прогона к прогону
        assert ("task", task) not in (await resolve_levels(db, student)).demoted


@pytest.mark.asyncio
async def test_switch_on_scopes_demotion_to_root_tree(db, monkeypatch):
    """Понижение в одном курсе не попадает в набор соседнего курса ученика."""
    topic_a, student, inserted = await _graced_topic(db)
    other = await _new_course(db, "tsk1361Б соседний курс")
    await _task(db, course_id=other, position=1, created_at=None)
    await _enroll(db, student, other)
    await db.commit()

    monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "true")
    invalidate_grace_cache(db)
    assert (await compute_graced_items(db, student, other)).tasks == frozenset()
    assert inserted in (await compute_graced_items(db, student, topic_a)).tasks
    invalidate_grace_cache(db)


@pytest.mark.asyncio
async def test_switch_on_individual_course_gets_no_demotion(db, monkeypatch):
    """Индивидуальный курс — без понижений, даже если общий подкурс понижен (tsk-1276)."""
    shared = await _new_course(db, "tsk1361Б общая тема")
    task = await _task(db, course_id=shared, position=1, created_at=None)
    ind_root = await _new_course(db, "tsk1361Б индивидуальный")
    await db.execute(
        text("UPDATE courses SET course_uid = :u WHERE id = :c"),
        {"u": f"ind:tsk1361-{ind_root}", "c": ind_root},
    )
    await _link(db, ind_root, shared, 1)
    student = await _new_student(db, "инд")
    teacher = await _new_student(db, "учитель инд")
    await _enroll(db, student, ind_root)
    await set_human_override(
        db, student_id=student, target_kind="task", target_id=task,
        level="recommended", by_user_id=teacher,
    )
    await db.commit()

    monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "true")
    invalidate_grace_cache(db)
    assert (await compute_graced_items(db, student, ind_root)).tasks == frozenset()
    assert (await compute_graced_items(db, student, shared)).tasks == frozenset({task})
    invalidate_grace_cache(db)


@pytest.mark.asyncio
async def test_switch_on_card_done_never_exceeds_total(db, monkeypatch):
    """Учитель понизил уже решённое задание — на карточке «сделано» не больше «всего»."""
    from app.services.me_service import get_courses_with_progress

    topic = await _new_course(db, "tsk1361Б карточка")
    solved = await _task(db, course_id=topic, position=1, created_at=None)
    await _task(db, course_id=topic, position=2, created_at=None)
    student = await _new_student(db, "карточка")
    teacher = await _new_student(db, "учитель карточка")
    await _enroll(db, student, topic)
    await _pass(db, student, solved, topic, topic, LONG_AGO)
    await set_human_override(
        db, student_id=student, target_kind="task", target_id=solved,
        level="recommended", by_user_id=teacher,
    )
    await db.commit()

    monkeypatch.setenv("REQUIREMENT_OVERRIDE_READ_ENABLED", "true")
    invalidate_grace_cache(db)
    card = next(c for c in await get_courses_with_progress(db, student) if c["course_id"] == topic)
    invalidate_grace_cache(db)
    assert card["progress"]["tasks_total"] == 1
    assert card["progress"]["tasks_done"] == 0
    assert card["progress"]["percent"] == 0
