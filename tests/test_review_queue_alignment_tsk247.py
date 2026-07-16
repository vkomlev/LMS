"""tsk-247: список обязательной очереди и claim-next обязаны сходиться.

Регрессия на баг из прода (2026-07-16): список требовал `is_correct IS NULL`
(Y-4.2), claim-next — `is_correct IS TRUE` (Y-6/tsk-210). Множества не
пересекались: работу, видимую в фильтре «Обязательные», невозможно было взять
через «Следующую проверку», и наоборот. Здесь мы проверяем именно СОГЛАСИЕ
двух ручек, а не каждую по отдельности — рассинхрон и был дефектом.

Плюс claim по result_id (POST /teacher/reviews/{id}/claim) — он даёт lock_token
для оценки ОПЦИОНАЛЬНОЙ работы, открытой из списка вручную.
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


def _api_key_qs() -> str:
    key = os.environ.get("VALID_API_KEYS", "").split(",")[0].strip()
    if not key:
        pytest.skip("VALID_API_KEYS не задан в .env — пропускаем")
    return f"api_key={key}"


async def _setup_methodist(db) -> tuple[int, str]:
    """Методист видит любые курсы (methodist-bypass в REVIEW_ACL_SQL)."""
    u = Users(
        email=f"t247-met-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t247-met", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, r.id FROM roles r WHERE r.name = 'methodist' "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _create_student(db) -> int:
    u = Users(
        email=f"t247-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t247-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_task(db, *, type_: str, manual: bool | None) -> int:
    rules: dict = {"max_score": 10}
    if manual is not None:
        rules["manual_review_required"] = manual
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), CAST(:rules AS jsonb), 1, 1) RETURNING id"
        ),
        {
            "ext": f"t247-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": type_, "stem": "test"}),
            "rules": json.dumps(rules),
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_tr(db, *, user_id: int, task_id: int, is_correct: bool | None,
                     score: int = 0) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct) "
            "VALUES (:s, :u, :t, :now, 0, :now, 10, 'spw', :ic) RETURNING id"
        ),
        {"s": score, "u": user_id, "t": task_id, "now": now, "ic": is_correct},
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _cleanup(db, *, user_ids: list[int], task_ids: list[int], rids: list[int]):
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
    for uid in user_ids:
        await db.execute(text("DELETE FROM user_session WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM identity_link WHERE user_id=:u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id=:u"), {"u": uid})
    await db.commit()


async def _mandatory_ids(client, *, student_id: int) -> list[int]:
    qs = _api_key_qs()
    resp = await client.get(
        f"/api/v1/task-results/by-pending-review?{qs}&user_id={student_id}"
        f"&review_kind=mandatory&limit=100"
    )
    assert resp.status_code == 200, resp.text
    return [r["id"] for r in resp.json()]


@pytest.mark.asyncio
async def test_mandatory_list_work_is_claimable(db, client):
    """Работа из списка «Обязательные» ДОЛЖНА выдаваться claim-next.

    Прод-случай result#2023: SA_COM с manual_review_required=true и
    is_correct=NULL — был виден в списке и недостижим через «Следующую проверку».
    """
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, type_="SA_COM", manual=True)
    rid = await _create_tr(db, user_id=student_id, task_id=task_id, is_correct=None)
    try:
        assert rid in await _mandatory_ids(client, student_id=student_id), (
            f"rid={rid} (SA_COM, manual_review_required=true) должен быть в обязательном списке"
        )
        resp = await client.post(
            "/api/v1/teacher/reviews/claim-next",
            json={"teacher_id": methodist_id, "user_id": student_id, "ttl_sec": 120},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("empty") is False, (
            f"работа rid={rid} видна в обязательном списке, но claim-next вернул empty — "
            f"это и есть рассинхрон tsk-247"
        )
        assert body["item"]["id"] == rid
    finally:
        await _cleanup(db, user_ids=[methodist_id, student_id],
                       task_ids=[task_id], rids=[rid])


@pytest.mark.asyncio
async def test_claim_next_never_returns_work_absent_from_mandatory_list(db, client):
    """Обратная сторона: claim-next не выдаёт то, чего нет в обязательном списке.

    Прод-случай result#2418: авто-проверенный SA_COM (manual_review_required
    отсутствует) выдавался «Следующей проверкой», хотя в обязательной очереди
    его нет — карточка бота показывала его как опциональный и прятала оценку.
    """
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, type_="SA_COM", manual=False)
    rid = await _create_tr(db, user_id=student_id, task_id=task_id,
                           is_correct=True, score=10)
    try:
        assert rid not in await _mandatory_ids(client, student_id=student_id), (
            "авто-проверенный SA_COM не относится к обязательной очереди"
        )
        resp = await client.post(
            "/api/v1/teacher/reviews/claim-next",
            json={"teacher_id": methodist_id, "user_id": student_id, "ttl_sec": 120},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("empty") is True, (
            f"claim-next выдал rid={rid}, которого нет в обязательном списке"
        )
    finally:
        await _cleanup(db, user_ids=[methodist_id, student_id],
                       task_ids=[task_id], rids=[rid])


@pytest.mark.asyncio
async def test_claim_by_id_enables_grading_optional_work(db, client):
    """Опциональную работу можно взять по id и оценить (tsk-247).

    До этого grade требовал lock_token, а взять его было негде: claim-next
    опциональные не выдаёт → оценить авто-проверенную работу было нельзя.
    """
    methodist_id, token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, type_="SA_COM", manual=False)
    rid = await _create_tr(db, user_id=student_id, task_id=task_id,
                           is_correct=True, score=10)
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/claim",
            json={"teacher_id": methodist_id, "ttl_sec": 120},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["item"]["id"] == rid
        lock_token = body["lock_token"]
        assert lock_token

        grade = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={
                "teacher_id": methodist_id,
                "lock_token": lock_token,
                "score": 8,
                "comment": "проверено вручную",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert grade.status_code == 200, grade.text
        row = (
            await db.execute(
                text("SELECT score, checked_at FROM task_results WHERE id=:r"),
                {"r": rid},
            )
        ).fetchone()
        assert row[0] == 8 and row[1] is not None, "оценка должна быть записана"
    finally:
        await _cleanup(db, user_ids=[methodist_id, student_id],
                       task_ids=[task_id], rids=[rid])


@pytest.mark.asyncio
async def test_claim_by_id_conflict_when_held_by_other_teacher(db, client):
    """Чужой активный захват → 409, а не молчаливый перехват работы."""
    methodist_id, token = await _setup_methodist(db)
    other_id, _other_token = await _setup_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db, type_="SA_COM", manual=True)
    rid = await _create_tr(db, user_id=student_id, task_id=task_id, is_correct=None)
    try:
        await db.execute(
            text(
                "UPDATE task_results SET review_claimed_by=:o, review_claim_token='x', "
                "review_claim_expires_at = now() + interval '10 minutes' WHERE id=:r"
            ),
            {"o": other_id, "r": rid},
        )
        await db.commit()
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/claim",
            json={"teacher_id": methodist_id, "ttl_sec": 120},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409, resp.text
    finally:
        await _cleanup(db, user_ids=[methodist_id, other_id, student_id],
                       task_ids=[task_id], rids=[rid])
