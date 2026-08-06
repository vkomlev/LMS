"""tsk-504 (дашборд родителя: цветовая подсветка метрик относительно
сверстников по курсу).

Проверяем на настоящей БД (по образцу test_tsk494_student_dashboard.py):
- Порог когорты `>= 5` (Settings.student_dashboard_cohort_min_size):
  4 других активных ученика курса — `insufficient_data`, 6 — классификация.
- Терциль по 3 метрикам: `pace_level` (percent_complete), `missed_level`
  (доля пропусков, шкала ИНВЕРТИРОВАНА — больше пропусков хуже),
  `between_lessons_activity_level` (активность между занятиями).
- Когорта missed/activity — ОБЪЕДИНЕНИЕ активных учеников ВСЕХ курсов
  ребёнка (не одного курса, в отличие от pace_level).
- Собственное значение неопределено (пустой курс / нет занятий) —
  `insufficient_data` даже при полной когорте.
- Минимизация данных (tsk-460): в ответе нет ни email/ФИО пиров, ни их
  количества/сырых значений — только уровень (`worse`/`average`/`better`/
  `insufficient_data`) у своего ребёнка.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.models.users import Users

UTC = timezone.utc
_TAG = "tsk504"


# ============================== Helpers ==============================
# (дублируют паттерн test_tsk494_student_dashboard.py — cross-file импорт
# приватных helper'ов не принят в этом репозитории)


async def _new_user(db, *, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**9, 10**10)}@example.com",
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


def _dt_params(period_from: datetime, period_to: datetime) -> dict[str, str]:
    return {"from": period_from.isoformat(), "to": period_to.isoformat()}


async def _complete_n_tasks(db, *, student_id: int, course_id: int, task_ids: list[int], n: int, when: datetime) -> None:
    for task_id in task_ids[:n]:
        await _insert_task_result(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=True, submitted_at=when,
        )


async def _dashboard(client, token: str, student_id: int, period_from: datetime, period_to: datetime) -> dict:
    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(period_from, period_to),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ============================== pace_level (percent_complete) ==============================


@pytest.mark.asyncio
async def test_pace_level_insufficient_data_below_cohort_threshold(db, client):
    """4 других активных ученика курса (< порога 5) — insufficient_data,
    даже если у самого ученика прогресс есть."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    course_id = await _new_course(db, f"{_TAG} course-below")
    tasks = [await _new_task(db, course_id=course_id, uid=f"t{i}") for i in range(4)]
    await _enroll_student(db, student_id=student_id, course_id=course_id)

    for i in range(4):
        peer_id, _ = await _new_user(db, role="student", name=f"peer{i}")
        await _enroll_student(db, student_id=peer_id, course_id=course_id)

    now = datetime.now(UTC)
    body = await _dashboard(client, token, student_id, now - timedelta(days=7), now)
    course = next(c for c in body["courses"] if c["course_id"] == course_id)
    assert course["pace_level"] == "insufficient_data"


@pytest.mark.asyncio
async def test_pace_level_tercile_worse_average_better(db, client):
    """6 пиров курса (>= порога): percent_complete [25,25,50,75,100,100].
    Собственное значение 0/50/100 -> worse/average/better детерминированно
    (без пограничных долей — см. docstring теста).

    КАЖДЫЙ под-кейс — СВОЙ курс с СВОИМ набором пиров: если переиспользовать
    один курс и просто добавлять нового self-ученика на каждой итерации,
    предыдущий self сам становится пиром следующего (загрязняет распределение
    когорты) — реалистичный риск false-negative на пограничных ranked-долях.
    """
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    now = datetime.now(UTC)
    peer_done_counts = [1, 1, 2, 3, 4, 4]  # -> 25,25,50,75,100,100 %

    async def _self_percent_level(done: int) -> str:
        course_id = await _new_course(db, f"{_TAG} course-tercile-{done}")
        tasks = [await _new_task(db, course_id=course_id, uid=f"t{i}") for i in range(4)]
        for i, n in enumerate(peer_done_counts):
            peer_id, _ = await _new_user(db, role="student", name=f"peer{done}-{i}")
            await _enroll_student(db, student_id=peer_id, course_id=course_id)
            await _complete_n_tasks(
                db, student_id=peer_id, course_id=course_id, task_ids=tasks, n=n, when=now - timedelta(days=1),
            )

        student_id, _ = await _new_user(db, role="student", name=f"self{done}")
        await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
        await _enroll_student(db, student_id=student_id, course_id=course_id)
        if done:
            await _complete_n_tasks(
                db, student_id=student_id, course_id=course_id, task_ids=tasks, n=done, when=now - timedelta(days=1),
            )
        body = await _dashboard(client, token, student_id, now - timedelta(days=7), now)
        course = next(c for c in body["courses"] if c["course_id"] == course_id)
        assert course["percent_complete"] == round(done / 4 * 100)
        return course["pace_level"]

    assert await _self_percent_level(0) == "worse"
    assert await _self_percent_level(2) == "average"
    assert await _self_percent_level(4) == "better"


@pytest.mark.asyncio
async def test_pace_level_insufficient_data_when_own_value_undefined(db, client):
    """Курс без единого задания/материала (total=0) — pace_level
    insufficient_data ДАЖЕ при полной когорте (own value не определена)."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    course_id = await _new_course(db, f"{_TAG} course-empty")
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    for i in range(6):
        peer_id, _ = await _new_user(db, role="student", name=f"peer{i}")
        await _enroll_student(db, student_id=peer_id, course_id=course_id)

    now = datetime.now(UTC)
    body = await _dashboard(client, token, student_id, now - timedelta(days=7), now)
    course = next(c for c in body["courses"] if c["course_id"] == course_id)
    assert course["percent_complete"] == 0
    assert course["pace_level"] == "insufficient_data"


# ============================== missed_level (attendance) ==============================


@pytest.mark.asyncio
async def test_missed_level_scale_is_inverted(db, client):
    """Больше пропусков — хуже: у ученика с высокой долей пропусков
    missed_level='worse', у ученика с низкой (относительно тех же пиров) —
    'better'. Когорта — 6 пиров с фиксированной долей пропусков 50%."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    course_id = await _new_course(db, f"{_TAG} course-attendance")
    now = datetime.now(UTC)
    period_from = now - timedelta(days=10)
    period_to = now

    async def _add_occurrences(uid: int, *, missed: int, attended: int) -> None:
        for i in range(attended):
            occ_id = (
                await db.execute(
                    text(
                        "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
                        "VALUES (:t, :ts, 60) RETURNING id"
                    ),
                    {"t": teacher_id, "ts": now - timedelta(days=9 - i)},
                )
            ).scalar()
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
                    "VALUES (:o, :s, 'completed')"
                ),
                {"o": occ_id, "s": uid},
            )
        for i in range(missed):
            occ_id = (
                await db.execute(
                    text(
                        "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
                        "VALUES (:t, :ts, 60) RETURNING id"
                    ),
                    {"t": teacher_id, "ts": now - timedelta(days=8 - i, hours=1)},
                )
            ).scalar()
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
                    "VALUES (:o, :s, 'no_show')"
                ),
                {"o": occ_id, "s": uid},
            )
        await db.commit()

    # 6 пиров: ровно 50% пропусков (2 attended + 2 missed = 4 planned) каждый.
    for i in range(6):
        peer_id, _ = await _new_user(db, role="student", name=f"peer{i}")
        await _enroll_student(db, student_id=peer_id, course_id=course_id)
        await _add_occurrences(peer_id, missed=2, attended=2)

    # Ученик "worse": 100% пропусков (0 attended, 4 missed) — доля 1.0 > всех пиров (0.5).
    worse_id, _ = await _new_user(db, role="student", name="worse")
    await _link_student_teacher(db, student_id=worse_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=worse_id, course_id=course_id)
    await _add_occurrences(worse_id, missed=4, attended=0)

    # Ученик "better": 0% пропусков (4 attended, 0 missed) — доля 0.0 < всех пиров (0.5).
    better_id, _ = await _new_user(db, role="student", name="better")
    await _link_student_teacher(db, student_id=better_id, teacher_id=teacher_id)
    await _enroll_student(db, student_id=better_id, course_id=course_id)
    await _add_occurrences(better_id, missed=0, attended=4)

    body_worse = await _dashboard(client, token, worse_id, period_from, period_to)
    body_better = await _dashboard(client, token, better_id, period_from, period_to)

    assert body_worse["attendance"]["missed_level"] == "worse"
    assert body_better["attendance"]["missed_level"] == "better"


@pytest.mark.asyncio
async def test_missed_level_insufficient_data_when_own_planned_is_zero(db, client):
    """У самого ученика нет ни одного занятия в периоде (planned=0) —
    missed_level insufficient_data, даже если когорта полная."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    course_id = await _new_course(db, f"{_TAG} course-no-occ")
    await _enroll_student(db, student_id=student_id, course_id=course_id)
    for i in range(6):
        peer_id, _ = await _new_user(db, role="student", name=f"peer{i}")
        await _enroll_student(db, student_id=peer_id, course_id=course_id)

    now = datetime.now(UTC)
    body = await _dashboard(client, token, student_id, now - timedelta(days=7), now)
    assert body["attendance"]["planned"] == 0
    assert body["attendance"]["missed_level"] == "insufficient_data"


# ============================== cohort union across multiple courses ==============================


@pytest.mark.asyncio
async def test_missed_level_cohort_is_union_of_own_courses(db, client):
    """Ребёнок записан на 2 курса, у каждого по 3 активных пира (< порога
    поодиночке) — но объединение даёт 6, порог пройден."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)

    course_a = await _new_course(db, f"{_TAG} course-union-a")
    course_b = await _new_course(db, f"{_TAG} course-union-b")
    await _enroll_student(db, student_id=student_id, course_id=course_a)
    await _enroll_student(db, student_id=student_id, course_id=course_b)

    now = datetime.now(UTC)

    async def _give_occurrence(uid: int, *, status: str) -> None:
        occ_id = (
            await db.execute(
                text(
                    "INSERT INTO lesson_occurrence (teacher_id, scheduled_at, duration_minutes) "
                    "VALUES (:t, :ts, 60) RETURNING id"
                ),
                {"t": teacher_id, "ts": now - timedelta(days=2)},
            )
        ).scalar()
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
                "VALUES (:o, :s, :st)"
            ),
            {"o": occ_id, "s": uid, "st": status},
        )
        await db.commit()

    for i in range(3):
        peer_id, _ = await _new_user(db, role="student", name=f"peerA{i}")
        await _enroll_student(db, student_id=peer_id, course_id=course_a)
        await _give_occurrence(peer_id, status="completed")
    for i in range(3):
        peer_id, _ = await _new_user(db, role="student", name=f"peerB{i}")
        await _enroll_student(db, student_id=peer_id, course_id=course_b)
        await _give_occurrence(peer_id, status="completed")

    await _give_occurrence(student_id, status="completed")

    body = await _dashboard(client, token, student_id, now - timedelta(days=7), now)
    # Полная явка при непустой когорте >=5 (объединение 3+3) — классифицируется,
    # не insufficient_data (порог пройден только объединением курсов).
    assert body["attendance"]["missed_level"] != "insufficient_data"


# ============================== data minimization (tsk-460 principle) ==============================


@pytest.mark.asyncio
async def test_response_does_not_leak_peer_identity_or_raw_values(db, client):
    """В ответе нет ни email/ФИО пиров, ни отдельного поля с их числом/
    сырыми значениями — только уровень (`CohortLevel`) своего ребёнка."""
    teacher_id, token = await _new_user(db, role="teacher", name="teach")
    student_id, _ = await _new_user(db, role="student", name="stud")
    await _link_student_teacher(db, student_id=student_id, teacher_id=teacher_id)
    course_id = await _new_course(db, f"{_TAG} course-minimize")
    tasks = [await _new_task(db, course_id=course_id, uid=f"t{i}") for i in range(4)]
    await _enroll_student(db, student_id=student_id, course_id=course_id)

    now = datetime.now(UTC)
    peer_emails: list[str] = []
    for i in range(6):
        peer_id, _ = await _new_user(db, role="student", name=f"secretpeer{i}")
        await _enroll_student(db, student_id=peer_id, course_id=course_id)
        await _complete_n_tasks(
            db, student_id=peer_id, course_id=course_id, task_ids=tasks, n=i % 4, when=now - timedelta(days=1),
        )
        row = (await db.execute(text("SELECT email FROM users WHERE id = :id"), {"id": peer_id})).mappings().fetchone()
        peer_emails.append(row["email"])

    resp = await client.get(
        f"/api/v1/students/{student_id}/dashboard",
        params=_dt_params(now - timedelta(days=7), now),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    raw_text = resp.text
    for email in peer_emails:
        assert email not in raw_text, "email пира просочился в ответ дашборда"

    body = resp.json()
    course = next(c for c in body["courses"] if c["course_id"] == course_id)
    assert course["pace_level"] in ("worse", "average", "better", "insufficient_data")
    # Схема курса — фиксированный набор ключей, без "cohort_size"/"peers"/etc.
    assert set(course.keys()) == {
        "course_id", "title", "percent_complete", "pace_level",
        "current_section_title", "current_item_title",
        "forecast_completion_date", "is_completed",
    }
    assert set(body["attendance"].keys()) == {
        "planned", "attended", "missed", "upcoming",
        "norm_source", "not_conducted", "discrepancy", "missed_level",
    }
