"""tsk-588: часовой пояс ученика виден там, где договариваются о времени.

Расписание школы ведётся по Москве (`operating_hours.timezone = Europe/Moscow`),
а два ученика в июле-августе 2026 пришли на занятие мимо ровно на своё смещение.
Преподаватель должен видеть пояс ученика рядом с его работой, а не искать его в
карточке профиля.

Здесь проверяется очередь проверки (`GET /teacher/reviews/pending`) — сводка
занятия покрыта своим набором (`test_teacher_lesson_summary_tsk022_410.py`),
куда поле добавлено тем же способом.

Образец подъёма данных — как в test_pending_filters_tsk539.py.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_methodist(db) -> tuple[int, str]:
    u = Users(
        email=f"t588-met-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t588-met", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'methodist' ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _create_student(db, full_name: str, timezone_value: str | None) -> int:
    u = Users(
        email=f"t588-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=full_name, tg_id=None,
        timezone=timezone_value,
        timezone_source="manual" if timezone_value else None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_task(db) -> int:
    rules: dict = {"max_score": 10, "manual_review_required": True}
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), CAST(:rules AS jsonb), 1, 1) RETURNING id"
        ),
        {
            "ext": f"t588-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": "SA_COM", "stem": "test"}),
            "rules": json.dumps(rules),
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_tr(db, *, user_id: int, task_id: int, submitted_at: datetime) -> int:
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct) "
            "VALUES (0, :u, :t, :sub, 0, :sub, 10, 'spw', NULL) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "sub": submitted_at},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _cleanup(db, *, user_ids, task_ids=(), rids=()):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": list(rids)})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": list(task_ids)})
    for uid in user_ids:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    await db.commit()


@pytest.mark.asyncio
async def test_pending_review_item_carries_student_timezone(db, client):
    """Очередь отдаёт пояс ученика; у незаполненного профиля — None, не ошибка."""
    met_id, token = await _setup_methodist(db)
    tag = random.randint(10**8, 10**10)
    stud_orsk = await _create_student(db, f"t588 Орск {tag}", "Asia/Yekaterinburg")
    stud_empty = await _create_student(db, f"t588 Без пояса {tag}", None)
    task_id = await _create_task(db)
    now = datetime.now(timezone.utc)
    rid_orsk = await _create_tr(db, user_id=stud_orsk, task_id=task_id, submitted_at=now)
    rid_empty = await _create_tr(db, user_id=stud_empty, task_id=task_id, submitted_at=now)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&limit=200",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        by_id = {it["id"]: it for it in resp.json()["items"]}

        assert by_id[rid_orsk]["user_timezone"] == "Asia/Yekaterinburg"
        assert by_id[rid_empty]["user_timezone"] is None
    finally:
        await _cleanup(db, user_ids=[met_id, stud_orsk, stud_empty], task_ids=[task_id],
                       rids=[rid_orsk, rid_empty])
