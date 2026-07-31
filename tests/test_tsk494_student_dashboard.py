"""tsk-494 (дашборд ученика: данные/API для будущего кабинета родителя tsk-478).

Проверяем на НАСТОЯЩЕЙ БД (не на моках), по образцу
test_teacher_lesson_summary_tsk022_410.py.

Покрывает:
- `GET /students/{id}/dashboard?from=&to=`: ACL (403 чужому учителю), 422 на
  naive datetime / to<=from.
- Свойство `period_total == in_class_hours + between_lessons` по каждой
  метрике (не хардкод конкретных чисел).
- Пустой период — все метрики 0.
- Граница occurrence (BETWEEN инклюзивен с обеих сторон).
- `first_try` не портится при разнесении на "в часы"/"между занятиями".
- Прогноз окончания курса: pace=0 → None; remaining=0 → is_completed=True,
  forecast_date=None; нормальный случай — конкретная дата.
- Посещение за период: missed_total/missed_unresolved по всем статусам
  участия, `rescheduled` уже закрыт по построению.
- Минимизация данных: сырой JSON не содержит `solution_rules`/`message`/
  `resolution_comment`.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

UTC = timezone.utc
_TAG = "tsk494"


# ============================== Helpers ==============================


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.commit()
    return u.id, token


async def _link_student_teacher(db, *, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id) "
            "VALUES (:s, :t) ON CONFLICT DO NOTHING"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


async def _create_occurrence(
    db, *, student_id: int, teacher_id: int, scheduled_at: datetime,
    status: str = "scheduled", duration_minutes: int = 60,
) -> int:
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occ)
    await db.flush()
    db.add(LessonOccurrenceParticipant(occurrence_id=occ.id, student_id=student_id, status=status))
    occ_id = occ.id
    await db.commit()
    return occ_id


async def _new_course(db, title: str) -> int:
    return (
        await db.execute(
            text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
            {"t": title},
        )
    ).scalar()


async def _enroll_student(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _new_task(db, *, course_id: int, uid: str) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    assert difficulty_id is not None, "нет difficulties — граф не собрать"
    content = {"type": "SA", "stem": f"{_TAG} условие {uid}"}
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps(content),
                "sr": json.dumps({"max_score": 10, "accepted_answers": [f"{_TAG}-secret-{uid}"]}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()


async def _insert_task_result(
    db, *, student_id: int, task_id: int, course_id: int, is_correct: bool, submitted_at: datetime,
) -> None:
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
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, 0, :ts, 'test')"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct, "ts": submitted_at,
        },
    )
    await db.commit()


async def _new_material(db, *, course_id: int, title: str) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO materials (course_id, title, type, content, order_position) "
                "VALUES (:c, :t, 'text', CAST(:content AS jsonb), 1) RETURNING id"
            ),
            {"c": course_id, "t": title, "content": json.dumps({"body": "x"})},
        )
    ).scalar()


async def _insert_material_progress(
    db, *, student_id: int, material_id: int, completed_at: datetime,
) -> None:
    await db.execute(
        text(
            "INSERT INTO student_material_progress "
            "  (student_id, material_id, status, completed_at, source) "
            "VALUES (:s, :m, 'completed', :ts, 'system')"
        ),
        {"s": student_id, "m": material_id, "ts": completed_at},
    )
    await db.commit()


async def _insert_help_request(
    db, *, student_id: int, task_id: int, course_id: int, teacher_id: int, created_at: datetime,
) -> None:
    await db.execute(
        text(
            "INSERT INTO help_requests "
            "(status, request_type, student_id, task_id, course_id, assigned_teacher_id, "
            " message, created_at, updated_at) "
            "VALUES ('open', 'manual_help', :s, :t, :c, :teach, :msg, :ts, :ts)"
        ),
        {
            "s": student_id, "t": task_id, "c": course_id, "teach": teacher_id,
            "msg": f"{_TAG} секретный текст переписки", "ts": created_at,
        },
    )
    await db.commit()


def _dt_params(period_from: datetime, period_to: datetime) -> dict[str, str]:
    return {"from": period_from.isoformat(), "to": period_to.isoformat()}


# ============================== ACL / input validation ==============================


@pytest.mark.asyncio
async def test_dashboard_403_for_unrelated_teacher(db, client):
    teacher_a, _ = await _new_user(db, role="teacher", name="teachA")
    teacher_b, token_b = await _new_user(db, role="teacher", name="teachB")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_a)

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=7), now),
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_dashboard_422_naive_datetime(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params={"from": "2026-07-01T00:00:00", "to": "2026-08-01T00:00:00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_dashboard_422_to_before_from(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now, now - timedelta(days=1)),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_dashboard_empty_period_all_metrics_zero(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now - timedelta(hours=23)),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for bucket in ("period_total", "in_class_hours", "between_lessons"):
        assert body[bucket] == {
            "tasks_completed": 0, "theory_completed": 0, "first_try": 0, "help_requested_count": 0,
        }, bucket
    assert body["attendance"] == {"total_occurrences": 0, "missed_total": 0, "missed_unresolved": 0}
    assert body["courses"] == []


# ============================== period_total == in_class_hours + between_lessons ==============================


@pytest.mark.asyncio
async def test_metrics_split_without_double_counting_or_loss(db, client):
    """Одно задание сдано ВНУТРИ occurrence-окна, другое — вне (между
    занятиями). Свойство: period_total == in_class_hours + between_lessons
    по каждой метрике — не хардкод конкретных чисел."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    task_in_class = await _new_task(db, course_id=course_id, uid="in")
    task_between = await _new_task(db, course_id=course_id, uid="btw")
    material_id = await _new_material(db, course_id=course_id, title=f"{_TAG}-material")

    now = datetime.now(UTC)
    period_from = now - timedelta(days=3)
    period_to = now

    occ_start = now - timedelta(days=1)
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=occ_start, status="completed", duration_minutes=60,
    )

    # Внутри occurrence-окна (occ_start .. occ_start+60min).
    await _insert_task_result(
        db, student_id=student_id, task_id=task_in_class, course_id=course_id,
        is_correct=True, submitted_at=occ_start + timedelta(minutes=30),
    )
    await _insert_help_request(
        db, student_id=student_id, task_id=task_in_class, course_id=course_id,
        teacher_id=teacher_id, created_at=occ_start + timedelta(minutes=10),
    )
    # Вне occurrence-окна (между занятиями), но внутри периода.
    await _insert_task_result(
        db, student_id=student_id, task_id=task_between, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=2),
    )
    await _insert_material_progress(
        db, student_id=student_id, material_id=material_id,
        completed_at=now - timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    total, in_class, between = body["period_total"], body["in_class_hours"], body["between_lessons"]
    for key in ("tasks_completed", "theory_completed", "first_try", "help_requested_count"):
        assert total[key] == in_class[key] + between[key], key

    assert total["tasks_completed"] == 2
    assert in_class["tasks_completed"] == 1
    assert between["tasks_completed"] == 1
    assert total["theory_completed"] == 1
    assert between["theory_completed"] == 1
    assert in_class["theory_completed"] == 0
    assert total["help_requested_count"] == 1
    assert in_class["help_requested_count"] == 1
    assert between["help_requested_count"] == 0
    assert total["first_try"] == 2
    assert in_class["first_try"] == 1
    assert between["first_try"] == 1


@pytest.mark.asyncio
async def test_occurrence_boundary_is_inclusive(db, client):
    """Событие РОВНО на границе occurrence-окна (scheduled_at и
    scheduled_at+duration) считается "в часы занятий" — BETWEEN инклюзивен."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    task_start = await _new_task(db, course_id=course_id, uid="start")
    task_end = await _new_task(db, course_id=course_id, uid="end")

    now = datetime.now(UTC)
    occ_start = now - timedelta(days=1)
    duration = 60
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=occ_start, status="completed", duration_minutes=duration,
    )

    await _insert_task_result(
        db, student_id=student_id, task_id=task_start, course_id=course_id,
        is_correct=True, submitted_at=occ_start,
    )
    await _insert_task_result(
        db, student_id=student_id, task_id=task_end, course_id=course_id,
        is_correct=True, submitted_at=occ_start + timedelta(minutes=duration),
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=3), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["in_class_hours"]["tasks_completed"] == 2
    assert body["between_lessons"]["tasks_completed"] == 0


# ============================== Attendance ==============================


@pytest.mark.asyncio
async def test_attendance_missed_total_and_unresolved(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now

    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=9), status="completed",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=8), status="confirmed",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=7), status="scheduled",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=6), status="no_show",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=5), status="declined",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=4), status="rescheduled",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    attendance = resp.json()["attendance"]
    assert attendance["total_occurrences"] == 6
    assert attendance["missed_total"] == 3  # no_show + declined + rescheduled
    assert attendance["missed_unresolved"] == 2  # no_show + declined


# ============================== Forecast ==============================


@pytest.mark.asyncio
async def test_forecast_none_when_pace_weeks_misconfigured_to_zero(db, client, monkeypatch):
    """`STUDENT_FORECAST_PACE_WEEKS=0` (некорректная конфигурация) не должно
    ронять запрос делением на ноль — прогноз просто недоступен."""
    monkeypatch.setenv("STUDENT_FORECAST_PACE_WEEKS", "0")
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course-zeroweeks")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    await _new_task(db, course_id=course_id, uid="a")
    await _new_task(db, course_id=course_id, uid="b")

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    course = next(c for c in resp.json()["courses"] if c["course_id"] == course_id)
    assert course["forecast_completion_date"] is None
    assert course["is_completed"] is False


@pytest.mark.asyncio
async def test_forecast_none_when_no_recent_pace(db, client):
    """Курс не пройден, но за последние N недель — ноль активности:
    forecast_completion_date=None, is_completed=False (не деление на ноль)."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course-nopace")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    await _new_task(db, course_id=course_id, uid="a")
    await _new_task(db, course_id=course_id, uid="b")

    now = datetime.now(UTC)
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    course = next(c for c in resp.json()["courses"] if c["course_id"] == course_id)
    assert course["forecast_completion_date"] is None
    assert course["is_completed"] is False


@pytest.mark.asyncio
async def test_forecast_completed_course_no_date(db, client):
    """Курс пройден целиком: is_completed=True, forecast_completion_date=None."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course-done")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    task_id = await _new_task(db, course_id=course_id, uid="a")

    now = datetime.now(UTC)
    await _insert_task_result(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    course = next(c for c in resp.json()["courses"] if c["course_id"] == course_id)
    assert course["is_completed"] is True
    assert course["forecast_completion_date"] is None
    assert course["percent_complete"] == 100


@pytest.mark.asyncio
async def test_forecast_concrete_date_from_recent_pace(db, client):
    """Известный темп (1 задание в неделю за 4 недели, дефолт
    student_forecast_pace_weeks) и остаток 4 задания → прогноз ~4 недели вперёд."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course-pace")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=student_id, course_id=course_id)

    now = datetime.now(UTC)
    done_task = await _new_task(db, course_id=course_id, uid="done")
    await _insert_task_result(
        db, student_id=student_id, task_id=done_task, course_id=course_id,
        is_correct=True, submitted_at=now - timedelta(weeks=1),
    )
    for i in range(4):
        await _new_task(db, course_id=course_id, uid=f"remaining-{i}")

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    course = next(c for c in resp.json()["courses"] if c["course_id"] == course_id)
    assert course["is_completed"] is False
    assert course["forecast_completion_date"] is not None
    # pace = 1 задание / 4 недели = 0.25/нед; remaining = 4 → 16 недель вперёд.
    forecast = datetime.fromisoformat(course["forecast_completion_date"]).date()
    expected = (now + timedelta(weeks=16)).date()
    assert abs((forecast - expected).days) <= 1


# ============================== Data minimization ==============================


@pytest.mark.asyncio
async def test_dashboard_response_excludes_solution_rules_and_help_request_text(db, client):
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    course_id = await _new_course(db, f"{_TAG}-course-min")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    task_id = await _new_task(db, course_id=course_id, uid="a")

    now = datetime.now(UTC)
    await _insert_help_request(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        teacher_id=teacher_id, created_at=now - timedelta(hours=1),
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=1), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    raw = resp.text
    assert "solution_rules" not in raw
    assert "resolution_comment" not in raw
    assert f"{_TAG} секретный текст переписки" not in raw
    assert f"{_TAG}-secret-a" not in raw
