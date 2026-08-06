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
- Посещение за период по НОРМАТИВУ (tsk-556): planned/attended/missed/upcoming,
  инвариант `planned == attended + missed + upcoming`; прошедшее по факту
  заведённых занятий, хвост за горизонтом генератора — по расписанию;
  переносы и перерывы в норматив не входят.
- Минимизация данных: сырой JSON не содержит `solution_rules`/`message`/
  `resolution_comment`.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, time, timedelta, timezone

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


async def _reschedule(
    db, *, student_id: int, teacher_id: int, origin_occurrence_id: int,
    new_scheduled_at: datetime, new_status: str = "scheduled", duration_minutes: int = 60,
) -> int:
    """tsk-503: имитация реального `reschedule_occurrence` напрямую через
    БД (без вызова сервиса) — старая запись участника получает
    `status='rescheduled'` + `rescheduled_to_occurrence_id`, создаётся НОВЫЙ
    occurrence с новой строкой участника в статусе ``new_status``."""
    new_occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=new_scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(new_occ)
    await db.flush()
    db.add(
        LessonOccurrenceParticipant(
            occurrence_id=new_occ.id, student_id=student_id, status=new_status,
        )
    )
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant "
            "SET status = 'rescheduled', rescheduled_to_occurrence_id = :new_occ_id "
            "WHERE occurrence_id = :origin_occ_id AND student_id = :student_id"
        ),
        {"new_occ_id": new_occ.id, "origin_occ_id": origin_occurrence_id, "student_id": student_id},
    )
    new_occ_id = new_occ.id
    await db.commit()
    return new_occ_id


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
    assert body["attendance"] == {
        "planned": 0, "attended": 0, "missed": 0, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }
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


async def _create_slot(
    db, *, teacher_id: int, student_id: int, weekday: int, start_hour: int = 10,
) -> int:
    """tsk-556: постоянный слот расписания — источник норматива за хвост
    периода, до которого генератор занятий ещё не дошёл."""
    slot_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_slot (teacher_id, weekday, start_time, duration_minutes) "
                "VALUES (:t, :wd, :st, 60) RETURNING id"
            ),
            {"t": teacher_id, "wd": weekday, "st": time(hour=start_hour)},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO lesson_slot_student (slot_id, student_id, is_active) "
            "VALUES (:s, :u, true)"
        ),
        {"s": slot_id, "u": student_id},
    )
    await db.commit()
    return int(slot_id)


@pytest.mark.asyncio
async def test_attendance_normative_counts_and_invariant(db, client):
    """tsk-556: норматив/посетил/пропустил/впереди + инвариант
    `planned == attended + missed + upcoming`."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now + timedelta(days=3)

    # Прошедшие: 2 посещения + 2 пропуска.
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
        scheduled_at=now - timedelta(days=6), status="no_show",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=5), status="declined",
    )
    # Ещё впереди.
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now + timedelta(days=1), status="scheduled",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    assert a == {
        "planned": 5, "attended": 2, "missed": 2, "upcoming": 1,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }
    assert a["planned"] == a["attended"] + a["missed"] + a["upcoming"]


@pytest.mark.asyncio
async def test_attendance_teacher_marked_absent_then_present(db, client):
    """Сценарий оператора: ученик сам ничего не проставил, преподаватель
    поставил пропуск, а потом сам же исправил на явку. Считается итог, не
    история отметок."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    occ_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=2), status="no_show",
    )
    # Преподаватель исправляет вручную (тот же переход, что
    # `record_teacher_attendance` с action='manual_present').
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = 'confirmed' "
            "WHERE occurrence_id = :o AND student_id = :s"
        ),
        {"o": occ_id, "s": student_id},
    )
    await db.commit()

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=5), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    assert a == {
        "planned": 1, "attended": 1, "missed": 0, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_break_excluded_from_norm(db, client):
    """Перерыв не должен превращаться в пропуски: занятия со статусом
    `on_break` в норматив не входят."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=4), status="confirmed",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=3), status="on_break",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=2), status="on_break",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=6), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    assert a == {
        "planned": 1, "attended": 1, "missed": 0, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_future_tail_from_permanent_schedule(db, client):
    """Хвост периода за горизонтом генератора считается по ПОСТОЯННОМУ
    расписанию: занятий там ещё нет, но они уже обещаны ученику."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    # Занятие сгенерировано только одно, вчера; горизонт генератора — «сейчас».
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="confirmed",
    )
    # Слот на день недели, который придётся ровно на 3 и 10 день вперёд.
    target = now + timedelta(days=3)
    await _create_slot(
        db, teacher_id=teacher_id, student_id=student_id, weekday=target.weekday(),
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=2), now + timedelta(days=9)),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    # 1 фактическое (посещено) + 1 обещанное расписанием на 3-й день вперёд.
    assert a == {
        "planned": 2, "attended": 1, "missed": 0, "upcoming": 1,
        # Активный слот есть — норматив из цены не нужен (tsk-557): источник
        # "schedule", ручной цены у ученика нет, расхождения нет.
        "norm_source": "schedule", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_past_norm_survives_schedule_change(db, client):
    """Вопрос оператора: преподаватель среди периода добавил занятие в слот
    (было 1 в неделю, стало 2). Прошедшая часть обязана считаться по ФАКТУ,
    а не по новому расписанию — иначе появятся пропуски, которых не было."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    # Прошедшие две недели ученик ходил РАЗ в неделю, оба занятия посетил.
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=13), status="confirmed",
    )
    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=6), status="confirmed",
    )
    # А сейчас в расписании УЖЕ два слота в неделю (второй добавлен только что).
    await _create_slot(
        db, teacher_id=teacher_id, student_id=student_id,
        weekday=(now - timedelta(days=6)).weekday(), start_hour=10,
    )
    await _create_slot(
        db, teacher_id=teacher_id, student_id=student_id,
        weekday=(now - timedelta(days=4)).weekday(), start_hour=14,
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=14), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    # Ровно 2 занятия по факту, ни одного пропуска — новый слот прошлое не задевает.
    assert a == {
        "planned": 2, "attended": 2, "missed": 0, "upcoming": 0,
        "norm_source": "schedule", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_reschedule_inside_period_attended(db, client):
    """Перенос внутри периода, закрытый явкой: исходная строка в норматив не
    входит вовсе (её место заняла целевая), задвоения нет, пропуска нет."""
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
    origin_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=4), status="scheduled",
    )
    await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=origin_id,
        new_scheduled_at=now - timedelta(days=3),
        new_status="confirmed",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    # Норматив: completed + confirmed + scheduled(прошедшее, никто не отметил)
    # + no_show + declined + целевая строка переноса = 6. Исходная строка
    # переноса (`rescheduled`) не считается.
    assert a == {
        "planned": 6, "attended": 3, "missed": 3, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }
    assert a["planned"] == a["attended"] + a["missed"] + a["upcoming"]


@pytest.mark.asyncio
async def test_attendance_reschedule_to_missed_target_still_unresolved(db, client):
    """tsk-503, ключевой сценарий регрессии: перенос на дату, где ученик СНОВА
    не пришёл (`no_show`) — пропуск должен остаться НЕзакрытым, а не
    закрыться самим фактом переноса."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now

    origin_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=6), status="scheduled",
    )
    await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=origin_id,
        new_scheduled_at=now - timedelta(days=5),
        new_status="no_show",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    # Одно занятие, перенесённое на дату, где ученик снова не пришёл: норматив 1
    # (исходная строка не в счёт), пропуск 1 — и ровно один, а не два.
    assert a == {
        "planned": 1, "attended": 0, "missed": 1, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_reschedule_chain_of_two_resolved(db, client):
    """Перенос переноса (цепочка из 2 шагов), итог — фактическая явка:
    пропуск закрыт, обход не должен останавливаться на первом шаге."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now

    origin_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=9), status="scheduled",
    )
    hop1_id = await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=origin_id,
        new_scheduled_at=now - timedelta(days=7),
        new_status="scheduled",
    )
    await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=hop1_id,
        new_scheduled_at=now - timedelta(days=5),
        new_status="confirmed",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    # Цепочка из двух переносов, итог — явка. Обходить цепочку не нужно:
    # обе промежуточные строки `rescheduled` в норматив не входят.
    assert a == {
        "planned": 1, "attended": 1, "missed": 0, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_reschedule_chain_of_two_still_unresolved(db, client):
    """Цепочка из 2 переносов, на втором шаге снова пропуск (`declined`) —
    итог всё ещё открыт, обход правильно доходит до конца цепочки."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now

    origin_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=9), status="scheduled",
    )
    hop1_id = await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=origin_id,
        new_scheduled_at=now - timedelta(days=7),
        new_status="scheduled",
    )
    await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=hop1_id,
        new_scheduled_at=now - timedelta(days=5),
        new_status="declined",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    # Та же цепочка, но итог — отказ: ровно один пропуск, а не три.
    assert a == {
        "planned": 1, "attended": 0, "missed": 1, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_no_double_count_within_period(db, client):
    """Перенос ВНУТРИ одного периода не задваивает норматив — это одно
    занятие, просто на другую дату."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now

    await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=8), status="confirmed",
    )
    origin_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=6), status="scheduled",
    )
    await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=origin_id,
        new_scheduled_at=now - timedelta(days=5),  # тот же период
        new_status="confirmed",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    # 1 обычная запись + 1 перенесённая пара (считается один раз, не два)
    assert a == {
        "planned": 2, "attended": 2, "missed": 0, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


@pytest.mark.asyncio
async def test_attendance_reschedule_out_of_period_is_not_a_miss(db, client):
    """Перенос ЗА границу периода: занятие уехало из окна вместе со своим
    нормативом и пропуском в этом периоде не становится. Оно посчитается там,
    куда переехало."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now - timedelta(days=2)

    origin_id = await _create_occurrence(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=6), status="scheduled",
    )
    # Целевая дата ЗА пределами периода [period_from, period_to].
    await _reschedule(
        db, student_id=student_id, teacher_id=teacher_id,
        origin_occurrence_id=origin_id,
        new_scheduled_at=now - timedelta(days=1),
        new_status="confirmed",
    )

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    a = resp.json()["attendance"]
    assert a == {
        "planned": 0, "attended": 0, "missed": 0, "upcoming": 0,
        "norm_source": "unknown", "not_conducted": None, "discrepancy": False,
        "missed_level": "insufficient_data",
    }


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
