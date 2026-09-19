"""tsk-1005: история выдачи ДЗ ученику для кабинета методиста.

Данные существуют с tsk-741 (`homework_assignment`/`homework_item`), витрины
не было: `get_current`/эндпоинт `GET .../homework` отдают только ДЕЙСТВУЮЩУЮ
выдачу. Проверяется:

- `homework_service.get_history` возвращает ВСЕ выдачи (включая отменённые
  переизданием), новые сверху, с `cancelled_at` и полным составом каждой;
- `volume_details` каждой записи сохраняется как есть — тюнинг движка смотрит
  именно на него;
- эндпоинт `GET /teacher/students/{id}/homework/history` — методист видит
  историю ЛЮБОГО ученика (тот же ACL, что у остальных ручек ДЗ), посторонний
  преподаватель без связи с учеником получает 403.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import homework_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

UTC = timezone.utc
_TAG = "tsk1005hw"


async def _new_user(db, *, role: str | None = "student", name: str = "student") -> tuple[int, str]:
    user = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", user.email)
    token, _, _ = await create_session(db, user_id=user.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "role": role},
        )
    await db.commit()
    return user.id, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _new_course(db, title: str) -> int:
    return (
        await db.execute(
            text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
            {"t": f"{_TAG}-{title}"},
        )
    ).scalar()


async def _enroll(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _new_task(db, *, course_id: int, order_position: int) -> int:
    import json

    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, difficulty_id, "
                "  external_uid, max_score, order_position, created_at) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, :pos, "
                "  now() - interval '400 days') "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA", "stem": f"{_TAG} задача {order_position}"}),
                "sr": json.dumps({"max_score": 10, "accepted_answers": ["42"]}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{random.randint(10**8, 10**10)}",
                "pos": order_position,
            },
        )
    ).scalar()


async def _student_with_program(db, *, tasks: int = 10) -> tuple[int, int]:
    student_id, _ = await _new_user(db)
    course_id = await _new_course(db, "program")
    await _enroll(db, student_id=student_id, course_id=course_id)
    for pos in range(1, tasks + 1):
        await _new_task(db, course_id=course_id, order_position=pos)
    await db.execute(
        text("UPDATE users SET category = 'school_student', school_grade = 11 WHERE id = :u"),
        {"u": student_id},
    )
    await db.commit()
    return student_id, course_id


# ============================ Сервисный слой ============================


async def test_history_lists_current_and_cancelled_newest_first(db):
    student_id, _ = await _student_with_program(db)
    due = datetime.now(UTC) + timedelta(days=7)

    first = await homework_service.issue(
        db, student_id=student_id, due_at=due, source="teacher", volume_override=2,
    )
    await db.commit()
    second = await homework_service.issue(
        db, student_id=student_id, due_at=due, source="teacher", volume_override=3,
    )
    await db.commit()

    history = await homework_service.get_history(db, student_id=student_id)
    assert [h["id"] for h in history] == [second["id"], first["id"]], (
        "новые сверху, действующая — первая"
    )
    assert history[0]["cancelled_at"] is None
    assert history[1]["cancelled_at"] is not None, "первая выдача погашена второй"
    assert history[1]["total"] == 2
    assert history[0]["total"] == 3


async def test_history_preserves_volume_details_per_entry(db):
    """`volume_details` — тот самый материал для тюнинга формулы, сохраняется как снимок."""
    student_id, _ = await _student_with_program(db)
    await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=7),
        source="teacher", volume_override=2,
    )
    await db.commit()

    history = await homework_service.get_history(db, student_id=student_id)
    assert len(history) == 1
    details = history[0]["volume_details"]
    assert isinstance(details, dict)
    assert details["volume_override"] == 2


async def test_history_item_composition_is_clickable(db):
    """Каждый пункт состава несёт course_uid/external_uid для ссылки на задание."""
    student_id, course_id = await _student_with_program(db, tasks=3)
    await db.execute(
        text("UPDATE courses SET course_uid = :uid WHERE id = :c"),
        {"uid": f"{_TAG}-course-uid", "c": course_id},
    )
    await db.commit()

    await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=7),
        source="teacher", volume_override=2,
    )
    await db.commit()

    history = await homework_service.get_history(db, student_id=student_id)
    items = history[0]["items"]
    assert len(items) == 2
    for item in items:
        assert item["kind"] == "task"
        assert item["course_uid"] == f"{_TAG}-course-uid"
        assert item["external_uid"] is not None


async def test_history_empty_for_student_never_assigned(db):
    student_id, _ = await _new_user(db)
    history = await homework_service.get_history(db, student_id=student_id)
    assert history == []


async def test_history_respects_limit(db):
    student_id, _ = await _student_with_program(db, tasks=20)
    due = datetime.now(UTC) + timedelta(days=7)
    for _ in range(3):
        await homework_service.issue(
            db, student_id=student_id, due_at=due, source="teacher", volume_override=1,
        )
        await db.commit()

    history = await homework_service.get_history(db, student_id=student_id, limit=2)
    assert len(history) == 2


# ============================== HTTP-эндпоинт ==============================


async def test_endpoint_methodist_sees_any_student_history(db, client):
    student_id, _ = await _student_with_program(db)
    methodist_id, m_token = await _new_user(db, role="methodist", name="method")
    await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=7),
        source="teacher", volume_override=2,
    )
    await db.commit()

    resp = await client.get(
        f"/api/v1/teacher/students/{student_id}/homework/history",
        headers=_bearer(m_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["cancelled_at"] is None
    assert len(body["items"][0]["items"]) == 2


async def test_endpoint_unrelated_teacher_gets_403(db, client):
    student_id, _ = await _student_with_program(db)
    stranger_id, s_token = await _new_user(db, role="teacher", name="stranger")
    await homework_service.issue(
        db, student_id=student_id, due_at=datetime.now(UTC) + timedelta(days=7),
        source="teacher", volume_override=2,
    )
    await db.commit()

    resp = await client.get(
        f"/api/v1/teacher/students/{student_id}/homework/history",
        headers=_bearer(s_token),
    )
    assert resp.status_code == 403


async def test_endpoint_no_history_returns_empty_list(db, client):
    student_id, _ = await _new_user(db)
    methodist_id, m_token = await _new_user(db, role="methodist", name="method2")

    resp = await client.get(
        f"/api/v1/teacher/students/{student_id}/homework/history",
        headers=_bearer(m_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "total": 0}
