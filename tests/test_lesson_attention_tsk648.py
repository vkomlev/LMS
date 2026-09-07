"""tsk-648: очерёдность внимания — к кому преподаватель подойдёт первым.

Проверяем на НАСТОЯЩЕЙ БД, как и остальная сводка занятия (tsk-022/410), поле
`attention` и порядок участников в ответе
`GET /teacher/lesson-occurrences/{id}/summary`.

Главное свойство, за которым тут следим: список привязан к ЭТОМУ занятию и
устаревает вместе с ним. Простой, случившийся не на прошлом занятии ученика, а
раньше, поводом не является — иначе получился бы вечный рейтинг, а задача
просила ровно обратного.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from tests.test_teacher_lesson_summary_tsk022_410 import (
    _insert_task_result,
    _new_course,
    _new_task,
    _new_user,
)

UTC = timezone.utc


async def _occurrence(
    db, *, teacher_id: int, scheduled_at: datetime, duration_minutes: int = 60,
) -> int:
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occ)
    await db.flush()
    occ_id = occ.id
    await db.commit()
    return occ_id


async def _join(db, *, occurrence_id: int, student_id: int, status: str = "scheduled") -> None:
    db.add(
        LessonOccurrenceParticipant(
            occurrence_id=occurrence_id, student_id=student_id, status=status
        )
    )
    await db.commit()


async def _idle_episode(
    db, *, occurrence_id: int, student_id: int, silent_since: datetime,
    resolved_at: datetime, kind: str = "idle",
) -> None:
    await db.execute(
        text(
            "INSERT INTO lesson_idle_episode "
            "  (occurrence_id, student_id, kind, silent_since, detected_at, resolved_at) "
            "VALUES (:o, :s, :k, :since, :since, :resolved)"
        ),
        {
            "o": occurrence_id, "s": student_id, "k": kind,
            "since": silent_since, "resolved": resolved_at,
        },
    )
    await db.commit()


async def _limit_reached(db, *, student_id: int, task_id: int, created_at: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO learning_events (student_id, event_type, payload, created_at) "
            "VALUES (:s, 'attempt_limit_reached', CAST(:p AS jsonb), :ts)"
        ),
        {"s": student_id, "p": json.dumps({"task_id": task_id}), "ts": created_at},
    )
    await db.commit()


async def _summary(client, *, occ_id: int, teacher_id: int, token: str) -> list[dict]:
    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{occ_id}/summary",
        params={"teacher_id": teacher_id, "include_progress": "false"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["participants"]


# ============================== Поводы ==============================


@pytest.mark.asyncio
async def test_idle_on_previous_lesson_is_first_reason(db, client):
    """Молчал на прошлом занятии — повод номер один, с минутами в подписи."""
    teacher_id, token = await _new_user(db, role="teacher", name="t648a")
    student_id, _ = await _new_user(db, role="student", name="s648a")
    now = datetime.now(UTC)

    prev_at = now - timedelta(days=3)
    prev = await _occurrence(db, teacher_id=teacher_id, scheduled_at=prev_at)
    await _join(db, occurrence_id=prev, student_id=student_id, status="confirmed")
    await _idle_episode(
        db, occurrence_id=prev, student_id=student_id,
        silent_since=prev_at + timedelta(minutes=10),
        resolved_at=prev_at + timedelta(minutes=23),
    )
    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=student_id)

    (p,) = await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    assert p["attention"] is not None
    assert p["attention"]["reason"] == "idle_last_lesson"
    assert p["attention"]["rank"] == 1
    assert "13 мин" in p["attention"]["detail"]


@pytest.mark.asyncio
async def test_idle_on_older_lesson_is_not_a_reason(db, client):
    """Простой на ПОЗАПРОШЛОМ занятии поводом не считается: список привязан к
    ближайшему занятию и обязан устаревать, а не копиться."""
    teacher_id, token = await _new_user(db, role="teacher", name="t648b")
    student_id, _ = await _new_user(db, role="student", name="s648b")
    now = datetime.now(UTC)

    older_at = now - timedelta(days=9)
    older = await _occurrence(db, teacher_id=teacher_id, scheduled_at=older_at)
    await _join(db, occurrence_id=older, student_id=student_id, status="confirmed")
    await _idle_episode(
        db, occurrence_id=older, student_id=student_id,
        silent_since=older_at + timedelta(minutes=5),
        resolved_at=older_at + timedelta(minutes=25),
    )
    prev = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=2))
    await _join(db, occurrence_id=prev, student_id=student_id, status="confirmed")
    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=student_id)

    (p,) = await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    assert p["attention"] is None


@pytest.mark.asyncio
async def test_stuck_task_reason_with_wrong_attempts(db, client):
    """Три неверные попытки по одному заданию, задание не решено — «стоит»."""
    teacher_id, token = await _new_user(db, role="teacher", name="t648c")
    student_id, _ = await _new_user(db, role="student", name="s648c")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk648 курс")
    task_id = await _new_task(db, course_id=course_id, uid="stuck")

    for i in range(3):
        await _insert_task_result(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=False, submitted_at=now - timedelta(days=1, minutes=i),
        )

    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=student_id)

    (p,) = await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    assert p["attention"]["reason"] == "stuck"
    assert p["attention"]["rank"] == 2
    assert p["attention"]["task_id"] == task_id
    assert "3 неверные попытки" in p["attention"]["detail"]


@pytest.mark.asyncio
async def test_attempt_limit_counts_only_while_task_unsolved(db, client):
    """Упёрся в лимит попыток — повод; решил после продления — повода нет.

    На боевой базе за 7 дней в лимит упёрлись 50 пар «ученик + задание» у 24
    человек, а нерешёнными остались 8 у 8: без этого условия очерёдность звала
    бы к трети школы разом.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t648d")
    stuck_id, _ = await _new_user(db, role="student", name="s648d1")
    solved_id, _ = await _new_user(db, role="student", name="s648d2")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk648 лимит")
    task_id = await _new_task(db, course_id=course_id, uid="limit")

    await _limit_reached(
        db, student_id=stuck_id, task_id=task_id, created_at=now - timedelta(days=1)
    )
    await _limit_reached(
        db, student_id=solved_id, task_id=task_id, created_at=now - timedelta(days=1)
    )
    await _insert_task_result(
        db, student_id=solved_id, task_id=task_id, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=20),
    )

    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=stuck_id)
    await _join(db, occurrence_id=today, student_id=solved_id)

    rows = {
        p["student_id"]: p
        for p in await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    }
    assert rows[stuck_id]["attention"]["reason"] == "stuck"
    assert "попытки" in rows[stuck_id]["attention"]["detail"]
    assert rows[solved_id]["attention"] is None


@pytest.mark.asyncio
async def test_missed_previous_lesson_reason(db, client):
    """Пропустил прошлое занятие — третий по срочности повод."""
    teacher_id, token = await _new_user(db, role="teacher", name="t648e")
    student_id, _ = await _new_user(db, role="student", name="s648e")
    now = datetime.now(UTC)

    prev = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=4))
    await _join(db, occurrence_id=prev, student_id=student_id, status="no_show")
    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=student_id)

    (p,) = await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    assert p["attention"]["reason"] == "missed_last_lesson"
    assert p["attention"]["rank"] == 3


@pytest.mark.asyncio
async def test_absence_reason_disappears_after_teacher_asked(db, client):
    """Про пропуск уже поговорили (отметка из плана занятия, tsk-743) — повод снят.

    Пропуск — самый частый повод (46 из 88 случаев за две недели боевой базы);
    без снятия он вытеснял бы остальные и висел бы на каждом занятии.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="t648i")
    student_id, _ = await _new_user(db, role="student", name="s648i")
    now = datetime.now(UTC)

    prev = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=4))
    await _join(db, occurrence_id=prev, student_id=student_id, status="no_show")
    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=student_id)

    (before,) = await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    assert before["attention"]["reason"] == "missed_last_lesson"

    await db.execute(
        text(
            "INSERT INTO lesson_absence_followup "
            "  (student_id, occurrence_id, asked_by, reason) "
            "VALUES (:s, :o, :t, 'illness')"
        ),
        {"s": student_id, "o": prev, "t": teacher_id},
    )
    await db.commit()

    (after,) = await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    assert after["attention"] is None
    # Само число пропусков подряд остаётся: разговор состоялся, занятие всё
    # равно пропущено, и в личной сводке это по-прежнему видно.
    assert after["missed_streak"] == 1


@pytest.mark.asyncio
async def test_no_reason_for_student_without_events(db, client):
    """Ученик без свежих событий приходит без повода — это не «плохая оценка»,
    а отсутствие причины подходить в первую очередь."""
    teacher_id, token = await _new_user(db, role="teacher", name="t648f")
    student_id, _ = await _new_user(db, role="student", name="s648f")
    now = datetime.now(UTC)

    prev = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=3))
    await _join(db, occurrence_id=prev, student_id=student_id, status="confirmed")
    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=student_id)

    (p,) = await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    assert p["attention"] is None


# ============================== Порядок ==============================


@pytest.mark.asyncio
async def test_participants_sorted_by_attention_rank(db, client):
    """Порядок в ответе — это и есть очерёдность: молчавший выше пропустившего,
    оба выше того, к кому повода подходить нет."""
    teacher_id, token = await _new_user(db, role="teacher", name="t648g")
    silent_id, _ = await _new_user(db, role="student", name="s648g1")
    missed_id, _ = await _new_user(db, role="student", name="s648g2")
    calm_id, _ = await _new_user(db, role="student", name="s648g3")
    now = datetime.now(UTC)

    prev_at = now - timedelta(days=2)
    prev = await _occurrence(db, teacher_id=teacher_id, scheduled_at=prev_at)
    await _join(db, occurrence_id=prev, student_id=silent_id, status="confirmed")
    await _join(db, occurrence_id=prev, student_id=missed_id, status="no_show")
    await _join(db, occurrence_id=prev, student_id=calm_id, status="confirmed")
    await _idle_episode(
        db, occurrence_id=prev, student_id=silent_id,
        silent_since=prev_at + timedelta(minutes=5),
        resolved_at=prev_at + timedelta(minutes=20),
    )

    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    for sid in (calm_id, missed_id, silent_id):
        await _join(db, occurrence_id=today, student_id=sid)

    order = [
        p["student_id"]
        for p in await _summary(client, occ_id=today, teacher_id=teacher_id, token=token)
    ]
    assert order == [silent_id, missed_id, calm_id]


@pytest.mark.asyncio
async def test_single_student_request_keeps_attention(db, client):
    """Подробности по клику (`student_id`) несут тот же повод, что и строка
    списка — иначе список и открытая карточка говорили бы разное."""
    teacher_id, token = await _new_user(db, role="teacher", name="t648h")
    student_id, _ = await _new_user(db, role="student", name="s648h")
    now = datetime.now(UTC)

    prev = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now - timedelta(days=3))
    await _join(db, occurrence_id=prev, student_id=student_id, status="no_show")
    today = await _occurrence(db, teacher_id=teacher_id, scheduled_at=now + timedelta(hours=1))
    await _join(db, occurrence_id=today, student_id=student_id)

    resp = await client.get(
        f"/api/v1/teacher/lesson-occurrences/{today}/summary",
        params={"teacher_id": teacher_id, "student_id": student_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    (p,) = resp.json()["participants"]
    assert p["attention"]["reason"] == "missed_last_lesson"
