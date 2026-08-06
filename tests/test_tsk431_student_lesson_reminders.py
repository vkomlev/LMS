"""tsk-431 (Календарь LMS Фаза 4): GET /students/{student_id}/lesson-reminders/pending.

Покрывает identity-гейт (self / service-key / чужой) и фильтрацию по kind,
since, limit — тот же набор, что у сестринского эндпоинта
`methodist/escalations/pending` (test_y6_review_loop.py), но с явным
`student_id` вместо `current_user.id` (см. разведку tsk-431: у методиста
current_user.id=0 под сервисным ключом ломает фильтр, здесь student_id
передаётся явно и это единственный правильный путь для бота, читающего
за многих учеников одним ключом).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services import inbox_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _api_key_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _create_user(db, *, prefix: str = "tsk431") -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _seed_reminder(db, *, student_id: int, occurrence_id: int) -> int:
    item = await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind="lesson_reminder",
        title="Скоро занятие",
        content="Занятие начинается через 30 минут.",
        payload={"occurrence_id": occurrence_id, "teacher_id": 1, "role": "student"},
        created_by=None,
    )
    await db.commit()
    return item.id


@pytest.mark.asyncio
async def test_lesson_reminders_pending_acl(db, client):
    """401 без auth, 403 для другого ученика, 200 для self и для service-key."""
    student_id = await _create_user(db, prefix="tsk431-stud")
    other_id = await _create_user(db, prefix="tsk431-other")
    student_token, _, _ = await create_session(db, user_id=student_id)
    other_token, _, _ = await create_session(db, user_id=other_id)
    await db.commit()

    # 401 — без каких-либо credentials
    resp = await client.get(f"/api/v1/students/{student_id}/lesson-reminders/pending")
    assert resp.status_code == 401, resp.text

    # 403 — другой ученик пытается прочитать чужие напоминания
    resp = await client.get(
        f"/api/v1/students/{student_id}/lesson-reminders/pending",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403, resp.text

    # 200 — сам ученик
    resp = await client.get(
        f"/api/v1/students/{student_id}/lesson-reminders/pending",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body and "count" in body

    # 200 — сервисный токен (TG_LMS бот), читает ЗА ученика
    resp = await client.get(
        f"/api/v1/students/{student_id}/lesson-reminders/pending",
        headers=_api_key_headers(),
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_lesson_reminders_pending_kind_filter_and_since_and_limit(db, client):
    """Возвращаются только kind='lesson_reminder' этого ученика; since/limit работают."""
    student_id = await _create_user(db, prefix="tsk431-stud2")
    other_id = await _create_user(db, prefix="tsk431-other2")
    await db.commit()

    # Посторонний kind того же ученика — не должен попасть в ответ
    await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind="lesson_missed",
        title="Занятие пропущено",
        content="...",
        payload={"occurrence_id": 999},
        created_by=None,
    )
    # lesson_reminder ДРУГОГО ученика — не должен попасть
    await _seed_reminder(db, student_id=other_id, occurrence_id=1)
    await db.commit()

    id_1 = await _seed_reminder(db, student_id=student_id, occurrence_id=101)
    id_2 = await _seed_reminder(db, student_id=student_id, occurrence_id=102)
    # Обе строки созданы в рамках одной тестовой транзакции — Postgres `now()`
    # у обеих совпадает (транзакционное время, не время оператора), поэтому
    # ordering по modified_at явно бэкдейтится, а не берётся из wall-clock.
    old_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    await db.execute(
        text("UPDATE notifications SET modified_at = :ts WHERE id = :id"),
        {"ts": old_ts, "id": id_1},
    )
    await db.commit()

    resp = await client.get(
        f"/api/v1/students/{student_id}/lesson-reminders/pending",
        headers=_api_key_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [it["id"] for it in body["items"]]
    assert id_1 in ids and id_2 in ids
    assert all(it["kind"] == "lesson_reminder" for it in body["items"])
    other_occurrence_ids = {it["payload"].get("occurrence_id") for it in body["items"]}
    assert 999 not in other_occurrence_ids  # lesson_missed отфильтрован
    assert 1 not in other_occurrence_ids  # чужой ученик отфильтрован

    # since — только события ПОСЛЕ cutoff (т.е. только id_2)
    resp = await client.get(
        f"/api/v1/students/{student_id}/lesson-reminders/pending",
        params={"since": cutoff.isoformat()},
        headers=_api_key_headers(),
    )
    assert resp.status_code == 200, resp.text
    since_ids = [it["id"] for it in resp.json()["items"]]
    assert id_1 not in since_ids
    assert id_2 in since_ids

    # limit=1 — не больше одного элемента
    resp = await client.get(
        f"/api/v1/students/{student_id}/lesson-reminders/pending",
        params={"limit": 1},
        headers=_api_key_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["items"]) == 1
