"""tsk-924: закрытые заявки помощи — сортировка по дате закрытия, поле closed_at.

Экран преподавателя показывал закрытые заявки в том же порядке, что и открытые
(`sort=priority`, зашито на клиенте), и списку было нечем отдать дату закрытия —
`HelpRequestListItem` её не нёс вовсе. Оператор попросил свежие сверху и поиск
по ученику (поиск — на клиенте, объём у одного преподавателя мал).
"""
from __future__ import annotations

import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.help_requests_service import close_help_request


async def _setup_teacher(db):
    teacher = Users(
        email=f"tsk924-tch-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk924-tch", tg_id=None,
    )
    db.add(teacher)
    await db.flush()
    from app.services.auth import identity_link_service
    from app.services.auth.session_service import create_session

    await identity_link_service.upsert_identity(db, teacher.id, "email", teacher.email)
    token, _, _ = await create_session(db, user_id=teacher.id)
    await db.commit()
    return teacher.id, token


async def _create_student(db, name: str) -> int:
    u = Users(
        email=f"tsk924-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=name, tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _pick_task(db) -> int:
    row = (await db.execute(text("SELECT id FROM tasks LIMIT 1"))).fetchone()
    if row is None:
        pytest.skip("Нет задач в БД")
    return int(row[0])


async def _seed_help_request(db, *, teacher_id: int, student_id: int, task_id: int) -> int:
    r = await db.execute(
        text("""
            INSERT INTO help_requests
            (status, request_type, auto_created, context_json, student_id, task_id,
             assigned_teacher_id, created_at, updated_at, priority)
            VALUES ('open', 'manual_help', false, '{}'::jsonb, :student_id, :task_id,
                    :teacher_id, now(), now(), 100)
            RETURNING id
        """),
        {"student_id": student_id, "task_id": task_id, "teacher_id": teacher_id},
    )
    rid = r.scalar_one()
    await db.commit()
    return int(rid)


async def _cleanup(db, *, teacher_id: int, student_ids: list[int], request_ids: list[int]):
    # Учётки НЕ удаляем (как в test_help_requests_pending_count_tsk348.py):
    # у users есть аудит-триггер на append-only audit_event, каскад в него
    # роняет DELETE. Оставшиеся тестовые пользователи безвредны для прод-БД.
    if request_ids:
        await db.execute(text("DELETE FROM help_request_replies WHERE request_id = ANY(:r)"), {"r": request_ids})
        await db.execute(text("DELETE FROM help_requests WHERE id = ANY(:r)"), {"r": request_ids})
    users = [teacher_id, *student_ids]
    await db.execute(text("DELETE FROM notifications WHERE user_id = ANY(:u)"), {"u": users})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": users})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": [teacher_id]})
    await db.commit()


@pytest.mark.asyncio
async def test_closed_requests_sort_by_closed_at_desc(db, client):
    """sort=closed_at отдаёт самые недавние закрытия первыми."""
    teacher_id, token = await _setup_teacher(db)
    student_id = await _create_student(db, "tsk924-student")
    task_id = await _pick_task(db)
    rid_old = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id)
    rid_new = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id)
    try:
        # Закрываем в обратном порядке дат: старый получает более раннюю
        # closed_at, хотя создан раньше нового — сортировка обязана идти по
        # ДАТЕ ЗАКРЫТИЯ, а не по порядку создания или id.
        await db.execute(
            text("UPDATE help_requests SET status='closed', closed_at = now() - interval '2 days' WHERE id = :r"),
            {"r": rid_old},
        )
        await db.execute(
            text("UPDATE help_requests SET status='closed', closed_at = now() - interval '1 hour' WHERE id = :r"),
            {"r": rid_new},
        )
        await db.commit()

        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&status=closed&sort=closed_at",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        ids_in_order = [it["request_id"] for it in items]
        assert ids_in_order.index(rid_new) < ids_in_order.index(rid_old), (
            "недавно закрытая заявка должна идти выше более старой"
        )
        by_id = {it["request_id"]: it for it in items}
        assert by_id[rid_new]["closed_at"] is not None
        assert by_id[rid_old]["closed_at"] is not None
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_ids=[student_id], request_ids=[rid_old, rid_new])


@pytest.mark.asyncio
async def test_invalid_sort_still_rejected(db, client):
    """Неизвестное значение sort — 422, как и раньше (closed_at не открыл дыру)."""
    teacher_id, token = await _setup_teacher(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/help-requests?teacher_id={teacher_id}&sort=not_a_sort",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_ids=[], request_ids=[])


@pytest.mark.asyncio
async def test_close_help_request_still_sets_closed_at(db, client):
    """Регресс: close_help_request продолжает писать closed_at (используется сортировкой)."""
    teacher_id, token = await _setup_teacher(db)
    student_id = await _create_student(db, "tsk924-close-student")
    task_id = await _pick_task(db)
    rid = await _seed_help_request(db, teacher_id=teacher_id, student_id=student_id, task_id=task_id)
    try:
        data, already, lock_err = await close_help_request(db, rid, teacher_id, "Готово")
        await db.commit()
        assert lock_err is None
        assert already is False
        assert data["closed_at"] is not None
    finally:
        await _cleanup(db, teacher_id=teacher_id, student_ids=[student_id], request_ids=[rid])
