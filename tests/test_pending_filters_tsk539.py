"""tsk-539: фильтры очереди проверки — ученик (id/имя), даты сдачи, пагинация.

Продолжение tsk-372 (тот же эндпоинт `GET /teacher/reviews/pending`).
Оператор: «список обрывается» — SPW брал жёсткий limit=100 при 930 работах.
limit/offset в сервисе были и раньше; здесь проверяем, что они честно
режут выборку, а `total` при этом остаётся полным (не размером страницы).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


async def _setup_methodist(db) -> tuple[int, str]:
    u = Users(
        email=f"t539-met-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="t539-met", tg_id=None,
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


async def _create_student(db, full_name: str) -> int:
    u = Users(
        email=f"t539-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=full_name, tg_id=None,
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
            "ext": f"t539-{random.randint(10**8, 10**10)}",
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


# ── Фильтр по ученику ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filter_by_user_id(db, client):
    """user_id оставляет работы только этого ученика."""
    met_id, token = await _setup_methodist(db)
    tag = random.randint(10**8, 10**10)
    stud_a = await _create_student(db, f"t539 Алиса {tag}")
    stud_b = await _create_student(db, f"t539 Борис {tag}")
    task_id = await _create_task(db)
    now = datetime.now(timezone.utc)
    rid_a = await _create_tr(db, user_id=stud_a, task_id=task_id, submitted_at=now)
    rid_b = await _create_tr(db, user_id=stud_b, task_id=task_id, submitted_at=now)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&user_id={stud_a}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = [it["id"] for it in resp.json()["items"]]
        assert rid_a in ids
        assert rid_b not in ids
    finally:
        await _cleanup(db, user_ids=[met_id, stud_a, stud_b], task_ids=[task_id],
                       rids=[rid_a, rid_b])


@pytest.mark.asyncio
async def test_filter_by_student_name_ilike(db, client):
    """student_name ищет по части имени, регистронезависимо."""
    met_id, token = await _setup_methodist(db)
    tag = random.randint(10**8, 10**10)
    stud_a = await _create_student(db, f"t539 Алиса {tag}")
    stud_b = await _create_student(db, f"t539 Борис {tag}")
    task_id = await _create_task(db)
    now = datetime.now(timezone.utc)
    rid_a = await _create_tr(db, user_id=stud_a, task_id=task_id, submitted_at=now)
    rid_b = await _create_tr(db, user_id=stud_b, task_id=task_id, submitted_at=now)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&student_name=алиса",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = [it["id"] for it in resp.json()["items"]]
        assert rid_a in ids, "поиск по части имени в другом регистре должен находить"
        assert rid_b not in ids
    finally:
        await _cleanup(db, user_ids=[met_id, stud_a, stud_b], task_ids=[task_id],
                       rids=[rid_a, rid_b])


@pytest.mark.asyncio
async def test_student_name_too_short_rejected(db, client):
    """student_name короче 2 символов — 422 (не молчаливый полный список)."""
    met_id, token = await _setup_methodist(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&student_name=а",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[met_id])


# ── Фильтр по датам ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filter_by_submitted_date_range_inclusive(db, client):
    """Границы диапазона включительно: работа, сданная в день submitted_to, входит."""
    met_id, token = await _setup_methodist(db)
    tag = random.randint(10**8, 10**10)
    stud = await _create_student(db, f"t539 Дата {tag}")
    task_id = await _create_task(db)
    # Три работы: позавчера, вчера, сегодня (время внутри дня — не полночь,
    # чтобы поймать ошибку "верхняя граница обрезает свой же день").
    now = datetime.now(timezone.utc).replace(hour=15, minute=30, second=0, microsecond=0)
    day_before = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)
    rid_old = await _create_tr(db, user_id=stud, task_id=task_id, submitted_at=day_before)
    rid_mid = await _create_tr(db, user_id=stud, task_id=task_id, submitted_at=yesterday)
    rid_new = await _create_tr(db, user_id=stud, task_id=task_id, submitted_at=now)
    try:
        frm = yesterday.date().isoformat()
        to = now.date().isoformat()
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&user_id={stud}"
            f"&submitted_from={frm}&submitted_to={to}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        ids = [it["id"] for it in resp.json()["items"]]
        assert rid_mid in ids, "день submitted_from входит"
        assert rid_new in ids, "день submitted_to входит целиком, не только полночь"
        assert rid_old not in ids, "работа до диапазона не входит"
    finally:
        await _cleanup(db, user_ids=[met_id, stud], task_ids=[task_id],
                       rids=[rid_old, rid_mid, rid_new])


@pytest.mark.asyncio
async def test_inverted_date_range_rejected(db, client):
    """submitted_to < submitted_from — 422 (паттерн marketer_payments)."""
    met_id, token = await _setup_methodist(db)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}"
            "&submitted_from=2026-08-10&submitted_to=2026-08-01",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _cleanup(db, user_ids=[met_id])


# ── Пагинация ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pagination_limit_offset_with_full_total(db, client):
    """limit режет страницу, offset сдвигает, total остаётся полным.

    Мотив (оператор 2026-08-03): SPW брал жёсткий limit=100 и не подгружал
    остальное — при 930 работах 830 были недостижимы. Пагинация обязана
    отдавать полный total, иначе UI не знает, что есть ещё.
    """
    met_id, token = await _setup_methodist(db)
    tag = random.randint(10**8, 10**10)
    stud = await _create_student(db, f"t539 Пагинация {tag}")
    task_id = await _create_task(db)
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    rids = [
        await _create_tr(db, user_id=stud, task_id=task_id, submitted_at=base + timedelta(minutes=i))
        for i in range(5)
    ]
    try:
        base_url = (
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&user_id={stud}"
        )
        first = await client.get(
            f"{base_url}&limit=2&offset=0", headers={"Authorization": f"Bearer {token}"}
        )
        assert first.status_code == 200, first.text
        body1 = first.json()
        assert len(body1["items"]) == 2, "страница ровно в limit"
        assert body1["total"] == 5, "total — полный размер выборки, не размер страницы"

        second = await client.get(
            f"{base_url}&limit=2&offset=2", headers={"Authorization": f"Bearer {token}"}
        )
        assert second.status_code == 200, second.text
        body2 = second.json()
        assert body2["total"] == 5
        ids1 = [it["id"] for it in body1["items"]]
        ids2 = [it["id"] for it in body2["items"]]
        assert set(ids1).isdisjoint(ids2), "страницы не пересекаются"

        # Хвост: последняя страница короче limit, но total тот же.
        tail = await client.get(
            f"{base_url}&limit=2&offset=4", headers={"Authorization": f"Bearer {token}"}
        )
        assert tail.status_code == 200, tail.text
        assert len(tail.json()["items"]) == 1
        assert tail.json()["total"] == 5

        # FIFO-порядок сохранён между страницами (submitted_at ASC).
        all_ids = ids1 + ids2 + [it["id"] for it in tail.json()["items"]]
        assert all_ids == rids, "порядок страниц совпадает с FIFO по submitted_at"
    finally:
        await _cleanup(db, user_ids=[met_id, stud], task_ids=[task_id], rids=rids)


@pytest.mark.asyncio
async def test_filters_combine_with_review_kind(db, client):
    """Фильтры ученика/дат не ломают review_kind (ортогональны, AND)."""
    met_id, token = await _setup_methodist(db)
    tag = random.randint(10**8, 10**10)
    stud = await _create_student(db, f"t539 Комбо {tag}")
    task_id = await _create_task(db)  # mandatory (manual_review_required=true)
    now = datetime.now(timezone.utc)
    rid = await _create_tr(db, user_id=stud, task_id=task_id, submitted_at=now)
    try:
        today = now.date().isoformat()
        # mandatory + этот ученик + сегодняшний день — работа на месте.
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&review_kind=mandatory"
            f"&user_id={stud}&submitted_from={today}&submitted_to={today}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert rid in [it["id"] for it in resp.json()["items"]]

        # optional + тот же ученик — пусто (работа обязательная).
        resp2 = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&review_kind=optional"
            f"&user_id={stud}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200, resp2.text
        assert rid not in [it["id"] for it in resp2.json()["items"]]
    finally:
        await _cleanup(db, user_ids=[met_id, stud], task_ids=[task_id], rids=[rid])
