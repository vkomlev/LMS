"""Integration HTTP-тесты Y-4.2 R-3 fix: фильтр claim_next_review по типу
задачи + is_correct.

После Y-4.2 endpoint /api/v1/teacher/reviews/claim-next возвращает только
SA_COM/TA с `is_correct IS NULL`. Авто-проверенные MC/SC/SA исключены —
это предотвращает data corruption через переоценку автопроверки.
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
    """Создать teacher с methodist-ролью (REVIEW_ACL_SQL bypass).

    Это даёт виден весь pending pool без зависимости от teacher_courses,
    что упрощает тесты на фильтр типа задачи.
    """
    u = Users(
        email=f"y42-mth-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y42-methodist", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name='methodist' "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _create_task(db, *, course_id: int, type_: str) -> int:
    """Создать synthetic task с нужным task_content->>'type'."""
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), :cid, 1) RETURNING id"
        ),
        {
            "ext": f"y42-test-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": type_, "stem": "test"}),
            "cid": course_id,
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_student(db) -> int:
    u = Users(
        email=f"y42-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="y42-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_task_result(
    db, *, user_id: int, task_id: int,
    is_correct: bool | None, score: int = 0,
) -> int:
    """Создать task_result с заданным is_correct (None = pending)."""
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct) "
            "VALUES (:s, :u, :t, :now, 0, :now, 10, 'spw', :ic) RETURNING id"
        ),
        {"s": score, "u": user_id, "t": task_id, "now": now, "ic": is_correct},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _cleanup(db, *, methodist_id: int, student_id: int, task_ids: list[int], rids: list[int]):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
    await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": methodist_id})
    await db.execute(text("DELETE FROM user_session WHERE user_id IN (:m,:s)"), {"m": methodist_id, "s": student_id})
    await db.execute(text("DELETE FROM identity_link WHERE user_id IN (:m,:s)"), {"m": methodist_id, "s": student_id})
    await db.commit()


async def _claim_next(
    client, *, teacher_id: int, token: str, idem: str, user_id: int | None = None,
) -> dict:
    body = {"teacher_id": teacher_id, "ttl_sec": 60, "idempotency_key": idem}
    if user_id is not None:
        # Изолируем pool до конкретного ученика (фильтр в claim_next_review).
        body["user_id"] = user_id
    resp = await client.post(
        "/api/v1/teacher/reviews/claim-next",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ─── 1-3: skip auto-checked MC / SC / SA ────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["MC", "SC", "SA"])
async def test_claim_next_skips_auto_checked(db, client, task_type):
    """Y-4.2 R-3: автопроверенный MC/SC/SA с is_correct IS NOT NULL не выдаётся."""
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    # course_id=1 — root PY, есть; methodist-bypass ACL обходит teacher_courses.
    task_id = await _create_task(db, course_id=1, type_=task_type)
    rid = await _create_task_result(
        db, user_id=student_id, task_id=task_id, is_correct=False, score=0
    )
    try:
        # Изолируем по user_id (свежий student) → пул содержит только наш rid
        # или пуст (если фильтр отбросил его — что и хотим проверить).
        body = await _claim_next(
            client, teacher_id=methodist_id, token=token, user_id=student_id,
            idem=f"y42-skip-{task_type}-{rid}",
        )
        # auto-checked → empty=true (rid отброшен фильтром)
        assert body.get("empty") is True, (
            f"claim_next должен вернуть empty для auto-checked {task_type} "
            f"rid={rid}, но вернул item={body.get('item')}"
        )
    finally:
        await _cleanup(
            db, methodist_id=methodist_id, student_id=student_id,
            task_ids=[task_id], rids=[rid],
        )


# ─── 4-5: positive — первично-верные pending SA_COM / TA выдаются ───────────

@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["SA_COM", "TA"])
async def test_claim_next_includes_pending_manual(db, client, task_type):
    """Первично-верные pending SA_COM/TA (is_correct=TRUE, checked_at NULL)
    остаются в очереди на ВТОРИЧНУЮ проверку учителя.

    tsk-210: под Y-6 реальные pending-записи имеют is_correct=TRUE — TA через
    optimistic-PASSED, SA_COM через успешную сверку с эталоном. Раньше тест
    подавал is_correct=None (стар. семантика Y-4.2 «pending = is_correct IS
    NULL»); после tsk-210 очередь фильтруется `is_correct IS TRUE`, поэтому
    для проверки положительного пути используем is_correct=True.
    """
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, course_id=1, type_=task_type)
    rid = await _create_task_result(
        db, user_id=student_id, task_id=task_id, is_correct=True, score=10
    )
    try:
        # Изолируем по user_id (свежий student) → в пуле ровно один rid.
        body = await _claim_next(
            client, teacher_id=methodist_id, token=token, user_id=student_id,
            idem=f"y42-pos-{task_type}-{rid}",
        )
        assert body.get("empty") is False, (
            f"pending {task_type} rid={rid} должен быть достижим через claim_next"
        )
        assert body["item"]["id"] == rid, (
            f"claim_next должен выдать наш rid={rid}, но вернул {body['item']['id']}"
        )
        # Release для cleanup
        await client.post(
            f"/api/v1/teacher/reviews/{rid}/release",
            json={"teacher_id": methodist_id, "lock_token": body["lock_token"]},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        await _cleanup(
            db, methodist_id=methodist_id, student_id=student_id,
            task_ids=[task_id], rids=[rid],
        )


# ─── 6: tsk-210 — первично-неверный SA_COM НЕ попадает учителю ──────────────

@pytest.mark.asyncio
async def test_claim_next_excludes_primary_wrong_sa_com(db, client):
    """tsk-210: SA_COM с is_correct=FALSE (не совпал с эталоном) — честный
    FAILED, НЕ pending. Учителю на вторичную проверку он не выдаётся: ученик
    уже видит «Неверно» и может пробовать снова.

    Регрессия на баг P0 из обратной связи QA: раньше optimistic-PASSED ставил
    любому SA_COM is_correct=TRUE, и неверный ответ и проходил как верный, и
    засорял очередь учителя.
    """
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, course_id=1, type_="SA_COM")
    rid = await _create_task_result(
        db, user_id=student_id, task_id=task_id, is_correct=False, score=0
    )
    try:
        body = await _claim_next(
            client, teacher_id=methodist_id, token=token, user_id=student_id,
            idem=f"y42-wrong-sacom-{rid}",
        )
        assert body.get("empty") is True, (
            f"первично-неверный SA_COM rid={rid} не должен попадать учителю, "
            f"но claim_next вернул item={body.get('item')}"
        )
    finally:
        await _cleanup(
            db, methodist_id=methodist_id, student_id=student_id,
            task_ids=[task_id], rids=[rid],
        )
