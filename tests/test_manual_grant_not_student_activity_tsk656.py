"""
Ручная отметка преподавателя — не активность ученика (tsk-656).

На проде 74% строк `task_results` — `source_system='manual_teacher'`: преподаватель
закрывает пройденное офлайн (`manual_progress_service.grant_task`, tsk-297).
Задание при этом действительно пройдено, поэтому в прогрессе, доступе и лимитах
попыток такая строка учитывается — и это НЕ дефект. Но аналитика, отвечающая на
вопрос «что ученик делал сам», считала её наравне с настоящей сдачей: отметки
ставятся пачками до 473 штук в минуту, и метрики уезжали в разы.

Покрывает четыре пути, разъехавшихся с правилом:
- (а) прогноз окончания курса: ручные зачёты не создают темпа;
- (б) серия дней: ручная отметка не делает день активным;
- (в) «продолжить с места»: ручная отметка не двигает точку возврата;
- (г) датчик простоя на занятии: ручная отметка не считается признаком жизни.

Плюс контроль обратной стороны: настоящая сдача (`spw_web`) во всех четырёх
путях по-прежнему работает — фильтр не должен глушить реальную работу.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import me_service
from app.services.lesson_idle_cron_service import lesson_idle_cron_tick

pytestmark = pytest.mark.asyncio

UTC = timezone.utc
MANUAL = "manual_teacher"
REAL = "spw_web"


async def _new_user(db, name: str) -> int:
    email = f"tsk656_{uuid.uuid4().hex[:8]}@example.com"
    row = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, :n) RETURNING id"),
        {"e": email, "n": f"tsk656 {name}"},
    )
    uid = int(row.scalar())
    await db.commit()
    return uid


async def _new_course(db) -> int:
    row = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"tsk656 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(row.scalar())
    await db.commit()
    return cid


async def _new_task(db, course_id: int, uid: str) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    # JSON идёт ПАРАМЕТРАМИ, а не литералом в тексте запроса: двоеточие внутри
    # `{"type":"SA"}` SQLAlchemy принимает заbind-параметр и падает на компиляции.
    row = await db.execute(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, external_uid, task_content, solution_rules) "
            "VALUES (:c, :d, :u, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) RETURNING id"
        ),
        {
            "c": course_id, "d": diff, "u": f"tsk656-{uid}-{uuid.uuid4().hex[:6]}",
            "tc": '{"type": "SA", "stem": "tsk656"}',
            "sr": '{"max_score": 10}',
        },
    )
    tid = int(row.scalar())
    await db.commit()
    return tid


async def _result(
    db, *, student_id: int, task_id: int, course_id: int, source: str, when: datetime
) -> None:
    """Строка результата с явным источником — ровно то, чем эти тесты и различаются."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, :src) RETURNING id"
            ),
            {"u": student_id, "c": course_id, "src": source},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, 10, 10, true, :ts, :ts, 0, :ts, :src)"
        ),
        {"u": student_id, "t": task_id, "a": attempt_id, "ts": when, "src": source},
    )
    await db.commit()


async def _cleanup(db, *, course_id: int, student_id: int) -> None:
    await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": student_id})
    await db.commit()


# ── (б) серия дней ──────────────────────────────────────────────────────────


async def _msk_days_ago(db, days: int) -> datetime:
    """Полдень по Москве N дней назад — как в тестах серии, без пограничной полуночи."""
    return (
        await db.execute(
            text(
                "SELECT (((now() AT TIME ZONE 'Europe/Moscow')::date "
                "         - (:d || ' days')::interval + INTERVAL '12 hours') "
                "        AT TIME ZONE 'Europe/Moscow')"
            ),
            {"d": str(days)},
        )
    ).scalar()


async def test_manual_grant_does_not_build_streak(db):
    """Пачка ручных отметок за три дня подряд не даёт ученику серию."""
    student_id = await _new_user(db, "streak-manual")
    course_id = await _new_course(db)
    try:
        for day in (0, 1, 2):
            task_id = await _new_task(db, course_id, f"streak-{day}")
            await _result(
                db, student_id=student_id, task_id=task_id, course_id=course_id,
                source=MANUAL, when=await _msk_days_ago(db, day),
            )

        streak = await me_service.get_streak(db, student_id)
        assert streak["streak_days"] == 0
        assert streak["today_active"] is False
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_real_submission_still_builds_streak(db):
    """Обратная сторона: настоящая сдача серию по-прежнему даёт."""
    student_id = await _new_user(db, "streak-real")
    course_id = await _new_course(db)
    try:
        task_id = await _new_task(db, course_id, "streak-real")
        await _result(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            source=REAL, when=await _msk_days_ago(db, 0),
        )

        streak = await me_service.get_streak(db, student_id)
        assert streak["streak_days"] == 1
        assert streak["today_active"] is True
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_manual_grant_does_not_extend_streak_over_gap(db):
    """Ручная отметка в разрыве не склеивает серию: вчера сдал сам, сегодня отметили."""
    student_id = await _new_user(db, "streak-mixed")
    course_id = await _new_course(db)
    try:
        real_task = await _new_task(db, course_id, "mixed-real")
        manual_task = await _new_task(db, course_id, "mixed-manual")
        await _result(
            db, student_id=student_id, task_id=real_task, course_id=course_id,
            source=REAL, when=await _msk_days_ago(db, 1),
        )
        await _result(
            db, student_id=student_id, task_id=manual_task, course_id=course_id,
            source=MANUAL, when=await _msk_days_ago(db, 0),
        )

        streak = await me_service.get_streak(db, student_id)
        # Активен только вчерашний день — сегодня ученик ничего не делал сам.
        assert streak["streak_days"] == 1
        assert streak["today_active"] is False
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (в) «продолжить с места» ────────────────────────────────────────────────


async def test_manual_grant_does_not_move_last_position(db):
    """Точка возврата остаётся на задании, которое ученик открывал сам."""
    student_id = await _new_user(db, "last-position")
    course_id = await _new_course(db)
    try:
        own_task = await _new_task(db, course_id, "own")
        granted_task = await _new_task(db, course_id, "granted")
        now = datetime.now(UTC)
        await _result(
            db, student_id=student_id, task_id=own_task, course_id=course_id,
            source=REAL, when=now - timedelta(hours=2),
        )
        # Отметка свежее — раньше именно она и становилась «последним местом».
        await _result(
            db, student_id=student_id, task_id=granted_task, course_id=course_id,
            source=MANUAL, when=now,
        )

        # Проверяем САМО правило «последняя активность» (публичный ответ
        # `get_last_position` — это уже следующий шаг движка, а не то задание,
        # где ученик был): точка отсчёта обязана указывать на его собственную
        # работу, иначе движок поведёт его от чужой отметки.
        row = (
            await db.execute(text(me_service._LAST_ACTIVITY_SQL), {"user_id": student_id})
        ).mappings().first()
        assert row is not None
        assert row["task_id"] == own_task
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_last_position_empty_when_only_manual_grants(db):
    """Ученику, которому только проставили зачёты, возвращаться некуда."""
    student_id = await _new_user(db, "last-position-manual")
    course_id = await _new_course(db)
    try:
        task_id = await _new_task(db, course_id, "granted-only")
        await _result(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            source=MANUAL, when=datetime.now(UTC),
        )

        row = (
            await db.execute(text(me_service._LAST_ACTIVITY_SQL), {"user_id": student_id})
        ).mappings().first()
        assert row is None
        assert await me_service.get_last_position(db, student_id) is None
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── (г) датчик простоя на занятии ───────────────────────────────────────────


async def _lesson_with_student(db) -> dict:
    """Идущее занятие получасом ранее и один подтверждённый участник."""
    teacher_id = await _new_user(db, "idle-teacher")
    student_id = await _new_user(db, "idle-student")
    course_id = await _new_course(db)
    occurrence_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, duration_minutes) "
                    "VALUES (NULL, :t, now() - interval '30 minutes', 90) RETURNING id"
                ),
                {"t": teacher_id},
            )
        ).scalar()
    )
    # Ведущий занятия — отдельной строкой: датчик зовёт именно активных ведущих.
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id, is_active) "
            "VALUES (:o, :t, true)"
        ),
        {"o": occurrence_id, "t": teacher_id},
    )
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
            "VALUES (:o, :s, 'confirmed')"
        ),
        {"o": occurrence_id, "s": student_id},
    )
    await db.execute(
        text(
            "INSERT INTO student_presence (student_id, last_seen_at, last_interaction_at, context) "
            "VALUES (:s, now(), now() - interval '15 minutes', 'task')"
        ),
        {"s": student_id},
    )
    await db.commit()
    return {
        "teacher_id": teacher_id,
        "student_id": student_id,
        "course_id": course_id,
        "occurrence_id": occurrence_id,
    }


async def _worked_on_lesson(db, student_id: int, *, minutes_ago: int) -> None:
    """Содержательное действие ученика — открытие задания N минут назад."""
    await db.execute(
        text(
            "INSERT INTO learning_events (student_id, event_type, payload, created_at) "
            "VALUES (:s, 'task_opened', CAST(:p AS jsonb), "
            "        now() - make_interval(mins => CAST(:ago AS int)))"
        ),
        {"s": student_id, "p": '{"task_id": 1}', "ago": minutes_ago},
    )
    await db.commit()


async def _cleanup_lesson(db, scene: dict) -> None:
    await db.execute(
        text("DELETE FROM lesson_occurrence WHERE id = :o"), {"o": scene["occurrence_id"]}
    )
    await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": scene["course_id"]})
    await db.execute(
        text("DELETE FROM users WHERE id = ANY(:ids)"),
        {"ids": [scene["student_id"], scene["teacher_id"]]},
    )
    await db.commit()


async def test_manual_grant_during_lesson_does_not_hide_idle(db, db_session_factory):
    """Преподаватель проставил зачёт во время занятия — простой всё равно виден.

    На проде 2209 ручных отметок попали внутрь окон идущих занятий: датчик читал
    их как «ученик работает» и молчал ровно тогда, когда должен звать.
    """
    scene = await _lesson_with_student(db)
    try:
        # Ученик поработал сам 15 минут назад и затих — это и есть простой.
        # Без первого содержательного действия датчик молчит по замыслу
        # («идёт объяснение»), и дефект на таком ученике не проявился бы.
        await _worked_on_lesson(db, scene["student_id"], minutes_ago=15)
        task_id = await _new_task(db, scene["course_id"], "idle-granted")
        # А теперь преподаватель проставляет зачёт — свежая строка результата.
        await _result(
            db, student_id=scene["student_id"], task_id=task_id,
            course_id=scene["course_id"], source=MANUAL, when=datetime.now(UTC),
        )

        summary = await lesson_idle_cron_tick(session_factory=db_session_factory)

        assert summary["locked"] is True
        episodes = (
            await db.execute(
                text("SELECT kind FROM lesson_idle_episode WHERE occurrence_id = :o"),
                {"o": scene["occurrence_id"]},
            )
        ).scalars().all()
        assert episodes, "простой должен быть замечен — ручная отметка его не закрывает"
    finally:
        await _cleanup_lesson(db, scene)


async def test_real_submission_during_lesson_keeps_student_alive(db, db_session_factory):
    """Обратная сторона: настоящая сдача только что — ученик работает, тревоги нет."""
    scene = await _lesson_with_student(db)
    try:
        await _worked_on_lesson(db, scene["student_id"], minutes_ago=15)
        task_id = await _new_task(db, scene["course_id"], "idle-real")
        await _result(
            db, student_id=scene["student_id"], task_id=task_id,
            course_id=scene["course_id"], source=REAL, when=datetime.now(UTC),
        )

        await lesson_idle_cron_tick(session_factory=db_session_factory)

        episodes = (
            await db.execute(
                text("SELECT kind FROM lesson_idle_episode WHERE occurrence_id = :o"),
                {"o": scene["occurrence_id"]},
            )
        ).scalars().all()
        assert not episodes, "только что сдал сам — простоя нет"
    finally:
        await _cleanup_lesson(db, scene)


# ── (а) прогноз окончания курса ─────────────────────────────────────────────


async def _forecast(db, *, student_id: int, course_id: int, items: list[dict], now: datetime):
    """Прогноз окончания курса напрямую — без обвязки портала преподавателя."""
    from app.services.student_dashboard_service import _load_course_pace_and_forecast

    return await _load_course_pace_and_forecast(
        db, student_id=student_id, course_id=course_id, items=items, now=now, pace_weeks=4,
    )


async def test_manual_grants_do_not_create_pace(db):
    """Ученик, которому только проставили зачёты, темпа не имеет — прогноза нет.

    Решение оператора (2026-08-23): честнее не показывать дату вовсе, чем
    считать её по чужим действиям. На проде у ученика 4526 за окно было 660
    ручных зачётов против 4 реальных сдач — прогноз обещал финиш почти завтра.
    """
    student_id = await _new_user(db, "pace-manual")
    course_id = await _new_course(db)
    try:
        now = datetime.now(UTC)
        done_task = await _new_task(db, course_id, "pace-done")
        await _result(
            db, student_id=student_id, task_id=done_task, course_id=course_id,
            source=MANUAL, when=now - timedelta(weeks=1),
        )
        items = [{"item_type": "task", "item_id": done_task, "status": "PASSED"}] + [
            {"item_type": "task", "item_id": await _new_task(db, course_id, f"pace-left-{i}"),
             "status": "AVAILABLE"}
            for i in range(4)
        ]

        forecast_date, completed = await _forecast(
            db, student_id=student_id, course_id=course_id, items=items, now=now
        )
        assert completed is False
        assert forecast_date is None
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_real_submissions_still_give_forecast(db):
    """Обратная сторона: по собственным сдачам ученика прогноз считается как раньше."""
    student_id = await _new_user(db, "pace-real")
    course_id = await _new_course(db)
    try:
        now = datetime.now(UTC)
        done_task = await _new_task(db, course_id, "pace-real-done")
        await _result(
            db, student_id=student_id, task_id=done_task, course_id=course_id,
            source=REAL, when=now - timedelta(weeks=1),
        )
        items = [{"item_type": "task", "item_id": done_task, "status": "PASSED"}] + [
            {"item_type": "task", "item_id": await _new_task(db, course_id, f"pace-real-left-{i}"),
             "status": "AVAILABLE"}
            for i in range(4)
        ]

        forecast_date, completed = await _forecast(
            db, student_id=student_id, course_id=course_id, items=items, now=now
        )
        assert completed is False
        assert forecast_date is not None
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


# ── материалы: та же история, обратная форма правила ────────────────────────


async def _new_material(db, course_id: int, title: str) -> int:
    row = await db.execute(
        text(
            # `content` — json-колонка, поэтому идёт параметром с валидным JSON.
            "INSERT INTO materials (course_id, type, content, title, order_position) "
            "VALUES (:c, 'text', CAST(:body AS json), :t, 1) RETURNING id"
        ),
        {"c": course_id, "t": f"tsk656 {title}", "body": '{"blocks": []}'},
    )
    mid = int(row.scalar())
    await db.commit()
    return mid


async def _material_done(db, *, student_id: int, material_id: int, source: str, when: datetime):
    """Прохождение материала. У материалов провенанс в `source`, и настоящее
    прохождение помечается `'system'` (tsk-297) — форма правила обратная той,
    что у заданий, поэтому проверяется отдельно."""
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "  (student_id, material_id, status, completed_at, source) "
            "VALUES (:s, :m, 'completed', :ts, :src)"
        ),
        {"s": student_id, "m": material_id, "ts": when, "src": source},
    )
    await db.commit()


async def test_manual_material_grant_does_not_move_last_position(db):
    """Отмеченный преподавателем материал не становится точкой возврата."""
    student_id = await _new_user(db, "material-position")
    course_id = await _new_course(db)
    try:
        own_task = await _new_task(db, course_id, "own-before-material")
        material_id = await _new_material(db, course_id, "granted")
        now = datetime.now(UTC)
        await _result(
            db, student_id=student_id, task_id=own_task, course_id=course_id,
            source=REAL, when=now - timedelta(hours=2),
        )
        await _material_done(
            db, student_id=student_id, material_id=material_id, source=MANUAL, when=now,
        )

        row = (
            await db.execute(text(me_service._LAST_ACTIVITY_SQL), {"user_id": student_id})
        ).mappings().first()
        assert row is not None
        assert row["kind"] == "task"
        assert row["task_id"] == own_task
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_real_material_progress_still_counts(db):
    """Обратная сторона: материал, пройденный самим учеником, точку возврата двигает."""
    student_id = await _new_user(db, "material-real")
    course_id = await _new_course(db)
    try:
        own_task = await _new_task(db, course_id, "own-before-real-material")
        material_id = await _new_material(db, course_id, "read")
        now = datetime.now(UTC)
        await _result(
            db, student_id=student_id, task_id=own_task, course_id=course_id,
            source=REAL, when=now - timedelta(hours=2),
        )
        await _material_done(
            db, student_id=student_id, material_id=material_id, source="system", when=now,
        )

        row = (
            await db.execute(text(me_service._LAST_ACTIVITY_SQL), {"user_id": student_id})
        ).mappings().first()
        assert row is not None
        assert row["kind"] == "material"
        assert row["material_id"] == material_id
    finally:
        await _cleanup(db, course_id=course_id, student_id=student_id)


async def test_manual_material_grant_during_lesson_does_not_hide_idle(db, db_session_factory):
    """Материал, отмеченный преподавателем на занятии, не гасит сигнал простоя."""
    scene = await _lesson_with_student(db)
    try:
        await _worked_on_lesson(db, scene["student_id"], minutes_ago=15)
        material_id = await _new_material(db, scene["course_id"], "idle-granted-material")
        await _material_done(
            db, student_id=scene["student_id"], material_id=material_id,
            source=MANUAL, when=datetime.now(UTC),
        )

        await lesson_idle_cron_tick(session_factory=db_session_factory)

        episodes = (
            await db.execute(
                text("SELECT kind FROM lesson_idle_episode WHERE occurrence_id = :o"),
                {"o": scene["occurrence_id"]},
            )
        ).scalars().all()
        assert episodes, "простой должен быть замечен — отметка материала его не закрывает"
    finally:
        await _cleanup_lesson(db, scene)
