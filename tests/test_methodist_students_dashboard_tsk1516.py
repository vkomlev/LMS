"""tsk-1516: дашборд методиста по ученикам — формулы показателей и гейт роли.

Проверяем по формулам, зафиксированным в задаче tsk-1516: посещаемость
(attended/planned по статусам), трудности (help_requests, hint_open,
first_try_rate по первому верному результату), скорость (avg_task_seconds,
solved_per_week + вердикт темпа), дисциплина ДЗ (on_time_rate по required-items
со сдачей не позже due_at). Отклонения — к прошлому периоду и среднему по группе.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import methodist_students_dashboard_service as svc
from tests.test_teacher_lesson_summary_tsk022_410 import (
    _create_occurrence_with_participant,
    _insert_help_request,
    _insert_task_result,
    _new_course,
    _new_task,
    _new_user,
)

UTC = timezone.utc
MSK = timezone(timedelta(hours=3))

_PERIOD_DAYS = 7


def _period_dates(now: datetime) -> tuple[str, str]:
    to_d = now.astimezone(MSK).date()
    from_d = to_d - timedelta(days=_PERIOD_DAYS - 1)
    return from_d.isoformat(), to_d.isoformat()


async def _insert_hint_open(db, *, student_id: int, task_id: int, at: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO learning_events (student_id, event_type, payload, created_at) "
            "VALUES (:s, 'hint_open', CAST(:p AS jsonb), :ts)"
        ),
        {"s": student_id, "p": json.dumps({"task_id": task_id}), "ts": at},
    )
    await db.commit()


async def _insert_task_session(
    db, *, student_id: int, task_id: int, seconds: int, at: datetime,
) -> None:
    await db.execute(
        text(
            "INSERT INTO learning_time_session "
            "  (student_id, item_type, task_id, started_at, ended_at, seconds, beats, "
            "   interactions, source) "
            "VALUES (:s, 'task', :t, :a, :b, :sec, 1, 1, 'presence')"
        ),
        {
            "s": student_id, "t": task_id, "a": at,
            "b": at + timedelta(seconds=seconds), "sec": seconds,
        },
    )
    await db.commit()


async def _insert_homework(
    db, *, student_id: int, task_id: int, issued_at: datetime, due_at: datetime,
) -> int:
    hw_id = (
        await db.execute(
            text(
                "INSERT INTO homework_assignment (student_id, issued_at, due_at, source, "
                "  planned_volume) VALUES (:s, :i, :d, 'auto', 1) RETURNING id"
            ),
            {"s": student_id, "i": issued_at, "d": due_at},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO homework_item (homework_id, kind, task_id, position, tier) "
            "VALUES (:h, 'task', :t, 1, 'required')"
        ),
        {"h": hw_id, "t": task_id},
    )
    await db.commit()
    return hw_id


async def _insert_retry_result(
    db, *, student_id: int, task_id: int, course_id: int,
    is_correct: bool, count_retry: int, submitted_at: datetime,
) -> None:
    """Результат с заданным count_retry (штатный helper всегда пишет 0)."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'test') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, :cr, :ts, 'test')"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct,
            "cr": count_retry, "ts": submitted_at,
        },
    )
    await db.commit()


@pytest.mark.asyncio
async def test_attendance_formula_completed_and_no_show(db):
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516a")
    student_id, _ = await _new_user(db, role="student", name="a1516a")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="completed",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=2), status="no_show",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["attendance"]["planned"] == 2
    assert row["attendance"]["attended"] == 1
    assert row["attendance"]["rate"] == 0.5


@pytest.mark.asyncio
async def test_scheduled_future_lesson_not_in_denominator(db):
    """Будущее 'scheduled' в знаменатель не входит (формула tsk-1516)."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516b")
    student_id, _ = await _new_user(db, role="student", name="a1516b")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(days=1), status="scheduled",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    ids = {r["student_id"] for r in data["students"]}
    # 'scheduled' не входит ни в числитель, ни в знаменатель — ученик с одним
    # будущим занятием в данных периода вообще не появляется.
    assert student_id not in ids


@pytest.mark.asyncio
async def test_first_try_rate_uses_first_correct_result(db):
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516c")
    student_id, _ = await _new_user(db, role="student", name="a1516c")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk1516 first-try")
    t_retry = await _new_task(db, course_id=course_id, uid="a1516c1")
    t_clean = await _new_task(db, course_id=course_id, uid="a1516c2")

    # Задание 1: сначала неверно (retry=1), потом верно (retry=2) — не first-try.
    await _insert_retry_result(
        db, student_id=student_id, task_id=t_retry, course_id=course_id,
        is_correct=False, count_retry=0, submitted_at=now - timedelta(days=1, hours=2),
    )
    await _insert_retry_result(
        db, student_id=student_id, task_id=t_retry, course_id=course_id,
        is_correct=True, count_retry=2, submitted_at=now - timedelta(days=1),
    )
    # Задание 2: верно с первой попытки.
    await _insert_task_result(
        db, student_id=student_id, task_id=t_clean, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=2),
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["difficulty"]["solved"] == 2
    assert row["difficulty"]["solved_first_try"] == 1
    assert row["difficulty"]["first_try_rate"] == 0.5


@pytest.mark.asyncio
async def test_help_hints_and_pace_aggregates(db):
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516d")
    student_id, _ = await _new_user(db, role="student", name="a1516d")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk1516 темп")

    for i in range(3):
        t = await _new_task(db, course_id=course_id, uid=f"a1516d{i}")
        await _insert_task_result(
            db, student_id=student_id, task_id=t,
            course_id=course_id, is_correct=True,
            submitted_at=now - timedelta(days=6, minutes=i),
        )
        await _insert_help_request(
            db, student_id=student_id, task_id=t, course_id=course_id,
            teacher_id=teacher_id, created_at=now - timedelta(days=6, minutes=i),
        )
        await _insert_hint_open(
            db, student_id=student_id, task_id=t,
            at=now - timedelta(days=6, minutes=i),
        )
        await _insert_task_session(
            db, student_id=student_id, task_id=t,
            seconds=300, at=now - timedelta(days=6, minutes=i),
        )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["difficulty"]["help_requests"] == 3
    assert row["difficulty"]["hint_opens"] == 3
    # 3 задачи за полную неделю.
    assert row["pace"]["solved_per_week"] == 3.0
    assert row["pace"]["avg_task_seconds"] == 300.0
    # Прошлый период пуст — вердикта нет.
    assert row["pace"]["verdict"] is None


@pytest.mark.asyncio
async def test_pace_verdict_compares_with_previous_period(db):
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516e")
    student_id, _ = await _new_user(db, role="student", name="a1516e")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk1516 вердикт")

    # Прошлый период: 10 решено (темп высокий).
    for i in range(10):
        t = await _new_task(db, course_id=course_id, uid=f"a1516e{i}")
        await _insert_task_result(
            db, student_id=student_id, task_id=t, course_id=course_id,
            is_correct=True, submitted_at=now - timedelta(days=8, minutes=i),
        )
    # Текущий период: 2 решено — падение более чем вдвое.
    t1 = await _new_task(db, course_id=course_id, uid="a1516e10")
    t2 = await _new_task(db, course_id=course_id, uid="a1516e11")
    await _insert_task_result(
        db, student_id=student_id, task_id=t1, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(days=1),
    )
    await _insert_task_result(
        db, student_id=student_id, task_id=t2, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=1),
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["pace"]["solved_per_week_prev"] == 10.0
    assert row["pace"]["solved_per_week"] == 2.0
    assert row["pace"]["verdict"] == "falling"


@pytest.mark.asyncio
async def test_homework_on_time_rate(db):
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516f")
    student_id, _ = await _new_user(db, role="student", name="a1516f")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk1516 ДЗ")
    t_ontime = await _new_task(db, course_id=course_id, uid="a1516f1")
    t_late = await _new_task(db, course_id=course_id, uid="a1516f2")

    await _insert_homework(
        db, student_id=student_id, task_id=t_ontime,
        issued_at=now - timedelta(days=2), due_at=now - timedelta(hours=6),
    )
    await _insert_task_result(
        db, student_id=student_id, task_id=t_ontime, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=12),
    )
    await _insert_homework(
        db, student_id=student_id, task_id=t_late,
        issued_at=now - timedelta(days=2), due_at=now - timedelta(hours=20),
    )
    await _insert_task_result(
        db, student_id=student_id, task_id=t_late, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=1),
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["homework"]["assigned"] == 2
    assert row["homework"]["done_on_time"] == 1
    assert row["homework"]["on_time_rate"] == 0.5


@pytest.mark.asyncio
async def test_group_average_and_prev_period_window(db):
    """Среднее по группе считается по текущему периоду; границы прошлого
    периода — ровно та же длительность непосредственно перед текущим."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516g")
    s1, _ = await _new_user(db, role="student", name="a1516g1")
    s2, _ = await _new_user(db, role="student", name="a1516g2")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=s1, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="completed",
    )
    await _create_occurrence_with_participant(
        db, student_id=s2, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="no_show",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    assert data["group_average"]["attendance_rate"] == 0.5
    assert data["prev_from"] < from_d
    assert data["prev_to"] < from_d
    # Длительности периодов совпадают.
    cur_len = datetime.strptime(to_d, "%Y-%m-%d") - datetime.strptime(from_d, "%Y-%m-%d")
    prev_len = (
        datetime.strptime(data["prev_to"], "%Y-%m-%d")
        - datetime.strptime(data["prev_from"], "%Y-%m-%d")
    )
    assert cur_len == prev_len


@pytest.mark.asyncio
async def test_empty_period_returns_no_students(db):
    """Будущий пустой период (данных точно нет) — пустой список, не ошибка."""
    data = await svc.get_students_dashboard(
        db, from_date="2100-01-01", to_date="2100-01-07",
    )
    assert data["students"] == []
    assert data["group_average"]["attendance_rate"] is None


@pytest.mark.asyncio
async def test_student_id_filter(db):
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516h")
    s1, _ = await _new_user(db, role="student", name="a1516h1")
    s2, _ = await _new_user(db, role="student", name="a1516h2")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=s1, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="completed",
    )
    await _create_occurrence_with_participant(
        db, student_id=s2, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="completed",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(
        db, from_date=from_d, to_date=to_d, student_id=s1,
    )
    assert [r["student_id"] for r in data["students"]] == [s1]


# ============================ Эндпоинт (гейт) ============================


@pytest.mark.asyncio
async def test_endpoint_denies_teacher(client, db):
    _teacher_id, token = await _new_user(db, role="teacher", name="a1516k")
    resp = await client.get(
        "/api/v1/methodist/students-dashboard",
        params={"from": "2026-11-01", "to": "2026-11-16"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_endpoint_ok_for_methodist(client, db):
    _methodist_id, token = await _new_user(db, role="methodist", name="a1516l")
    resp = await client.get(
        "/api/v1/methodist/students-dashboard",
        params={"from": "2026-11-01", "to": "2026-11-16"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from"] == "2026-11-01"
    assert body["to"] == "2026-11-16"
    assert "students" in body and "group_average" in body


@pytest.mark.asyncio
async def test_student_only_in_previous_period_is_visible(db):
    """Ученик с активностью ТОЛЬКО в прошлом периоде (упал до нуля) — виден
    в дашборде, иначе самый важный случай для вердикта falling терялся."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516n")
    student_id, _ = await _new_user(db, role="student", name="a1516n")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=9), status="completed",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next((r for r in data["students"] if r["student_id"] == student_id), None)
    assert row is not None
    assert row["attendance"]["rate"] is None
    assert row["attendance"]["prev_rate"] == 1.0


@pytest.mark.asyncio
async def test_homework_result_before_issuance_not_counted(db):
    """Верное решение, сданное ДО выдачи ДЗ, «в срок» не засчитывается."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1516o")
    student_id, _ = await _new_user(db, role="student", name="a1516o")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk1516 ДЗ раньше выдачи")
    task_id = await _new_task(db, course_id=course_id, uid="a1516o1")

    await _insert_homework(
        db, student_id=student_id, task_id=task_id,
        issued_at=now - timedelta(days=2), due_at=now - timedelta(hours=6),
    )
    await _insert_task_result(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(days=5),
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["homework"]["assigned"] == 1
    assert row["homework"]["done_on_time"] == 0
    assert row["homework"]["on_time_rate"] == 0.0


@pytest.mark.asyncio
async def test_attendance_confirmed_counts_as_attended(db):
    """tsk-1533: платформа фиксирует явку статусом 'confirmed' — он входит
    и в числитель, и в знаменатель."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1533a")
    student_id, _ = await _new_user(db, role="student", name="a1533a")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="confirmed",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=2), status="confirmed",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=3), status="no_show",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["attendance"]["planned"] == 3
    assert row["attendance"]["attended"] == 2
    assert row["attendance"]["rate"] == round(2 / 3, 4)


@pytest.mark.asyncio
async def test_attendance_rescheduled_not_in_denominator(db):
    """tsk-1533: 'rescheduled' (перенос) исключён из planned — перенесённое
    занятие ≠ пропуск. Ученик с одним переносом не появляется в дашборде."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1533b")
    student_id, _ = await _new_user(db, role="student", name="a1533b")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="rescheduled",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next((r for r in data["students"] if r["student_id"] == student_id), None)
    assert row is None


@pytest.mark.asyncio
async def test_last_activity_is_max_of_sources(db):
    """tsk-1533: last_activity_at = max(submitted_at решений, created_at
    обращений, окончание учебных сессий) по ученику, без привязки к периоду."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1533c")
    student_id, _ = await _new_user(db, role="student", name="a1533c")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk1533 активность")
    task_id = await _new_task(db, course_id=course_id, uid="a1533c1")

    result_at = now - timedelta(days=2)
    await _insert_task_result(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        is_correct=True, submitted_at=result_at,
    )
    help_at = now - timedelta(hours=5)
    await _insert_help_request(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        teacher_id=teacher_id, created_at=help_at,
    )
    session_at = now - timedelta(days=1)
    await _insert_task_session(
        db, student_id=student_id, task_id=task_id,
        seconds=300, at=session_at,
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    # Максимум — обращение 5 часов назад.
    assert row["last_activity_at"] == help_at.isoformat()


@pytest.mark.asyncio
async def test_last_activity_none_without_any_activity(db):
    """Ученик с занятием, но без решений/обращений/сессий — null."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a1533d")
    student_id, _ = await _new_user(db, role="student", name="a1533d")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="confirmed",
    )

    from_d, to_d = _period_dates(now)
    data = await svc.get_students_dashboard(db, from_date=from_d, to_date=to_d)
    row = next(r for r in data["students"] if r["student_id"] == student_id)
    assert row["last_activity_at"] is None


@pytest.mark.asyncio
async def test_period_longer_than_366_days_rejected(client, db):
    _methodist_id, token = await _new_user(db, role="methodist", name="a1516p")
    resp = await client.get(
        "/api/v1/methodist/students-dashboard",
        params={"from": "2024-01-01", "to": "2026-11-16"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_endpoint_rejects_bad_date(client, db):
    _methodist_id, token = await _new_user(db, role="methodist", name="a1516m")
    resp = await client.get(
        "/api/v1/methodist/students-dashboard",
        params={"from": "16.11.2026", "to": "2026-11-16"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
