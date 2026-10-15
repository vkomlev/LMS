"""tsk-1361: правила записывают персональную обязательность с причиной.

Три сценария оператора (15.10):
  1. сокращение программы, чтобы успеть до экзамена (tsk-798);
  2. ученик ушёл вперёд по теме, а в неё добавили новое (tsk-1261);
  3. смена курса ЕГЭ: добавили задачи, перенумеровали старые — решается
     правилом новизны (решение оператора), без карты соответствия.
Плюс регрессии tsk-1294 и tsk-1261, инвариант «человек главнее правила» и
совпадение записанного с прежним расчётом на лету.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.content_grace_service import (
    REASON_AHEAD,
    REASON_NEW_CONTENT,
    compute_graced_items,
    invalidate_grace_cache,
)
from app.services.requirement_override_service import (
    REASON_PROGRAM_TRIM,
    REASON_TEACHER,
    active_overrides,
    revoke_human_override,
    set_human_override,
    sync_rule_overrides,
)
from tests.test_tsk1261_grace_by_position import _link, _pass, _task
from tests.test_tsk692_content_grace import _enroll, _new_course, _new_student

LONG_AGO = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
INSERTED = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)


async def _sync(db, student: int):
    invalidate_grace_cache(db)
    res = await sync_rule_overrides(db, student)
    await db.commit()
    return res


async def _scope(db, student: int, excluded: list[int]) -> None:
    await db.execute(
        text("DELETE FROM student_program_scope WHERE student_id = :s"), {"s": student}
    )
    await db.execute(
        text(
            "INSERT INTO student_program_scope (student_id, program_kind, deadline, "
            " planned_pace, core_total, drill_total, drill_allowed, core_trimmed, "
            " excluded_courses) "
            "VALUES (:s, 'ege', :d, 10, 100, 50, 10, :trim, CAST(:ex AS jsonb))"
        ),
        {"s": student, "d": date(2027, 6, 1), "trim": bool(excluded), "ex": str(excluded)},
    )


@pytest.mark.asyncio
async def test_scenario1_program_trim_writes_and_revokes_course_override(db):
    """Сокращение программы — строка на узел с причиной; вернули номер — отменена."""
    root = await _new_course(db, "tsk1361 ЕГЭ")
    dropped = await _new_course(db, "tsk1361 номер 27")
    await _link(db, root, dropped, 1)
    student = await _new_student(db, "сокращение")
    await _enroll(db, student, root)
    await _scope(db, student, [dropped])
    await db.commit()

    await _sync(db, student)
    view = (await active_overrides(db, student))[("course", dropped)]
    assert view.level == "recommended"
    assert view.reason_code == REASON_PROGRAM_TRIM
    assert view.source == "rule"
    assert "программу" in view.reason_text

    await _scope(db, student, [])
    await db.commit()
    res = await _sync(db, student)
    assert res.revoked == 1
    assert ("course", dropped) not in await active_overrides(db, student)
    history = (
        await db.execute(
            text(
                "SELECT count(*) FROM student_requirement_override "
                " WHERE student_id = :s AND revoked_at IS NOT NULL"
            ),
            {"s": student},
        )
    ).scalar_one()
    assert history == 1, "Отмена мягкая — причина остаётся в истории"


@pytest.mark.asyncio
async def test_scenario2_went_ahead_new_task_gets_ahead_reason(db):
    """Ушёл вперёд по теме, вставили задание позади — строка «ушёл вперёд»."""
    topic = await _new_course(db, "tsk1361 тема")
    await _task(db, course_id=topic, position=1, created_at=None)  # пропущено
    done = await _task(db, course_id=topic, position=4, created_at=None)
    student = await _new_student(db, "вперёд")
    await _enroll(db, student, topic)
    await _pass(db, student, done, topic, topic, LONG_AGO)
    inserted = await _task(db, course_id=topic, position=3, created_at=INSERTED)
    await db.commit()

    await _sync(db, student)
    overrides = await active_overrides(db, student)
    assert overrides[("task", inserted)].reason_code == REASON_AHEAD
    assert len(overrides) == 1, "Старое пропущенное задание остаётся долгом"


@pytest.mark.asyncio
async def test_scenario3_course_change_new_tasks_graced_renumbering_harmless(db):
    """Смена курса ЕГЭ: новое в пройденной теме — необязательно; перенумерация не влияет."""
    root = await _new_course(db, "tsk1361 ЕГЭ корень")
    topic = await _new_course(db, "tsk1361 номер 5")
    await _link(db, root, topic, 1)
    old1 = await _task(db, course_id=topic, position=1, created_at=None)
    old2 = await _task(db, course_id=topic, position=2, created_at=None)

    passed = await _new_student(db, "прошёл тему")
    fresh = await _new_student(db, "не начинал")
    for s in (passed, fresh):
        await _enroll(db, s, root)
    await _pass(db, passed, old1, topic, root, LONG_AGO)
    await _pass(db, passed, old2, topic, root, LONG_AGO + timedelta(hours=1))

    # Новая редакция: две новые задачи, старые перенумерованы в конец.
    new1 = await _task(db, course_id=topic, position=1, created_at=INSERTED)
    new2 = await _task(db, course_id=topic, position=2, created_at=INSERTED)
    # По одному: триггер порядка не пускает групповой сдвиг одним UPDATE.
    for old in (old2, old1):
        await db.execute(
            text("UPDATE tasks SET order_position = order_position + 10 WHERE id = :id"),
            {"id": old},
        )
    await db.commit()

    await _sync(db, passed)
    await _sync(db, fresh)
    got = await active_overrides(db, passed)
    assert {t for t in got} == {("task", new1), ("task", new2)}
    assert all(v.reason_code == REASON_NEW_CONTENT for v in got.values())
    assert await active_overrides(db, fresh) == {}, "Для не проходившего новое обязательно"


@pytest.mark.asyncio
async def test_regression_tsk1294_far_topic_does_not_write_override(db):
    """Зачёт в дальней теме не делает непройденную тему необязательной.

    Сценарий ученика 4507 из теста tsk-1294: «Читаем файл» решён, «Кратчайший
    путь» той же темы — нет; решённое в дальней теме 24 его не прощает.
    """
    root = await _new_course(db, "tsk1361/1294 корень")
    t23 = await _new_course(db, "tsk1361/1294 тема 23")
    t24 = await _new_course(db, "tsk1361/1294 тема 24")
    await _link(db, root, t23, 1)
    await _link(db, root, t24, 2)
    far = await _task(db, course_id=t24, position=1, created_at=LONG_AGO)
    student = await _new_student(db, "1294")
    await _enroll(db, student, root)
    await _pass(db, student, far, t24, root, LONG_AGO + timedelta(days=3))
    read_file = await _new_course(db, "tsk1361/1294 читаем файл")
    shortest = await _new_course(db, "tsk1361/1294 кратчайший путь")
    await _link(db, t23, read_file, 1)
    await _link(db, t23, shortest, 2)
    rf = await _task(db, course_id=read_file, position=1, created_at=INSERTED)
    unvisited = await _task(db, course_id=shortest, position=1, created_at=INSERTED)
    await _pass(db, student, rf, read_file, root, INSERTED + timedelta(days=30))
    await db.commit()

    await _sync(db, student)
    assert ("task", unvisited) not in await active_overrides(db, student)


@pytest.mark.asyncio
async def test_written_overrides_match_on_the_fly_grace_and_sync_is_idempotent(db):
    """Записанное совпадает с прежним расчётом; повтор сверки ничего не меняет."""
    topic = await _new_course(db, "tsk1361 сверка")
    done = await _task(db, course_id=topic, position=5, created_at=None)
    student = await _new_student(db, "сверка")
    await _enroll(db, student, topic)
    await _pass(db, student, done, topic, topic, LONG_AGO)
    ins = [await _task(db, course_id=topic, position=p, created_at=INSERTED) for p in (2, 3)]
    await db.commit()

    first = await _sync(db, student)
    invalidate_grace_cache(db)
    on_the_fly = await compute_graced_items(db, student, topic)
    written = {i for k, i in await active_overrides(db, student) if k == "task"}
    assert written == set(on_the_fly.tasks) == set(ins)
    assert first.inserted == 2

    again = await _sync(db, student)
    assert (again.inserted, again.revoked, again.kept) == (0, 0, 2)


@pytest.mark.asyncio
async def test_human_override_beats_rule_and_rule_returns_after_revoke(db):
    """Учитель вернул задание в обязательные — правило не перебивает; сняли — правило вернулось."""
    topic = await _new_course(db, "tsk1361 учитель")
    done = await _task(db, course_id=topic, position=5, created_at=None)
    teacher = await _new_student(db, "учитель")
    student = await _new_student(db, "ученик")
    await _enroll(db, student, topic)
    await _pass(db, student, done, topic, topic, LONG_AGO)
    ins = await _task(db, course_id=topic, position=2, created_at=INSERTED)
    await db.commit()
    await _sync(db, student)

    await set_human_override(
        db, student_id=student, target_kind="task", target_id=ins,
        level="required", by_user_id=teacher, reason_text="нужно к пробнику",
    )
    await db.commit()
    res = await _sync(db, student)
    view = (await active_overrides(db, student))[("task", ins)]
    assert (view.level, view.source, view.reason_code, view.reason_text) == (
        "required", "human", REASON_TEACHER, "нужно к пробнику",
    )
    assert res.skipped_human == 1 and res.inserted == 0

    assert await revoke_human_override(
        db, student_id=student, target_kind="task", target_id=ins, by_user_id=teacher
    )
    await db.commit()
    await _sync(db, student)
    assert (await active_overrides(db, student))[("task", ins)].source == "rule"


@pytest.mark.asyncio
async def test_set_human_override_rejects_unknown_values(db):
    """Неизвестный уровень или вид цели — ошибка, а не тихая запись."""
    with pytest.raises(ValueError):
        await set_human_override(
            db, student_id=1, target_kind="task", target_id=1, level="optional", by_user_id=1
        )
    with pytest.raises(ValueError):
        await set_human_override(
            db, student_id=1, target_kind="lesson", target_id=1, level="required", by_user_id=1
        )
