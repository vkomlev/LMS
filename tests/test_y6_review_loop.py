"""Phase Y-6 review-loop integration tests (LMS-only).

Покрывает:
- M12 миграцию (idempotency check + индекс существует)
- optimistic-PASSED для TA на submit_attempt_answers (tsk-210: SA_COM больше
  НЕ optimistic — у него первичная сверка с эталоном; см. attempts.py 2.3c и
  test_claim_next_pending_filter_y42.py::test_claim_next_excludes_primary_wrong_sa_com)
- Stage 2 derived `is_correct` в /grade (3-input formula trace + edge)
- Stage 3 /regrade flow (positive→negative + history append)
- Stage 4 escalation_cron_tick smoke (idempotent + advisory lock)
- Stage 4 course-completion event-driven escalation

Объединено в один файл для компактности (LMS Y-6 scope).
"""
from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session


# ============================== Helpers ==============================


async def _create_user(db, *, role: str | None = None, prefix: str = "y6") -> int:
    """Создать user (+ опц. role)."""
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    if role:
        # Find role id
        r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
        row = r.fetchone()
        if row is None:
            # Создаём роль если её нет (уж лучше так чем skip)
            await db.execute(
                text("INSERT INTO roles (name) VALUES (:n) ON CONFLICT DO NOTHING"),
                {"n": role},
            )
            r = await db.execute(text("SELECT id FROM roles WHERE name=:n"), {"n": role})
            row = r.fetchone()
        role_id = int(row[0])
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "VALUES (:u, :r) ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "r": role_id},
        )
    await db.commit()
    return u.id


async def _setup_teacher_with_course(db, *, course_id: int) -> tuple[int, str]:
    """Teacher + session + teacher_courses привязка."""
    teacher_id = await _create_user(db, role="teacher", prefix="y6-teacher")
    access_token, _, _ = await create_session(db, user_id=teacher_id)
    await db.execute(
        text(
            "INSERT INTO teacher_courses (teacher_id, course_id, linked_at) "
            "VALUES (:t, :c, now()) ON CONFLICT DO NOTHING"
        ),
        {"t": teacher_id, "c": course_id},
    )
    await db.commit()
    return teacher_id, access_token


async def _pick_root_task(db) -> tuple[int, int, str]:
    """Найти задачу TA или SA_COM в root-курсе. Возвращает (task_id, course_id, type)."""
    row = (
        await db.execute(
            text(
                "SELECT t.id, t.course_id, t.task_content->>'type' AS type "
                "FROM tasks t "
                "WHERE t.course_id IS NOT NULL "
                "  AND t.course_id NOT IN (SELECT course_id FROM course_parents) "
                "  AND t.task_content->>'type' IN ('SA_COM','TA') "
                "LIMIT 1"
            )
        )
    ).fetchone()
    if row is None:
        pytest.skip("Нет TA/SA_COM задач в root-курсе для Y-6 теста")
    return int(row[0]), int(row[1]), str(row[2])


async def _create_pending_tr(
    db, *, student_id: int, task_id: int,
    teacher_id: int | None = None,
    is_correct: bool | None = True,  # Y-6 default: optimistic-PASSED
    score: int = 10,
    max_score: int = 10,
    submitted_at: datetime | None = None,
    lock_token: str | None = None,
) -> tuple[int, str | None, datetime]:
    """Создать pending task_result (checked_at=NULL).

    Y-6: по умолчанию `is_correct=TRUE` (optimistic-PASSED семантика).
    Передай `is_correct=None` чтобы воспроизвести pre-Y-6 legacy state.
    """
    if submitted_at is None:
        submitted_at = datetime.now(timezone.utc)
    expires_at = submitted_at + timedelta(minutes=5)
    params = {
        "u": student_id, "t": task_id,
        "now": submitted_at,
        "score": score, "ms": max_score, "ic": is_correct,
        "tid": teacher_id, "tok": lock_token, "exp": expires_at if teacher_id else None,
    }
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, submitted_at, count_retry, received_at, "
            " max_score, source_system, is_correct, "
            " review_claimed_by, review_claim_token, review_claim_expires_at) "
            "VALUES (:score, :u, :t, :now, 0, :now, :ms, 'spw', :ic, :tid, :tok, :exp) "
            "RETURNING id"
        ),
        params,
    )
    rid = res.scalar_one()
    await db.commit()
    return rid, lock_token, expires_at


async def _cleanup(db, *, user_ids: list[int], result_ids: list[int]):
    """Локальная очистка результатов теста.

    NB: session-scoped autouse фикстура `_cleanup_test_artifacts` в
    `tests/conftest.py` снимает users и каскадно всё остальное в конце
    прогона. Этот helper оставлен для случаев, когда внутри теста
    нужно явно убрать task_results/notifications до создания следующих
    (например, чтобы не ловить unique-constraint конфликт). Удалять
    users отсюда не обязательно — каскад фикстуры заберёт их сам.
    """
    if result_ids:
        await db.execute(
            text("DELETE FROM notifications WHERE (payload->>'result_id')::int = ANY(:ids)"),
            {"ids": result_ids},
        )
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:ids)"), {"ids": result_ids})
    if user_ids:
        await db.execute(text("DELETE FROM notifications WHERE user_id = ANY(:ids)"), {"ids": user_ids})
        await db.execute(text("DELETE FROM teacher_courses WHERE teacher_id = ANY(:ids)"), {"ids": user_ids})
        await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:ids)"), {"ids": user_ids})
        await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:ids)"), {"ids": user_ids})
        await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:ids)"), {"ids": user_ids})
    await db.commit()


# ============================== M12 ==============================


@pytest.mark.asyncio
async def test_m12_index_exists(db):
    """M12: партиал-индекс idx_task_results_pending_review создан."""
    res = await db.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname='idx_task_results_pending_review'"
        )
    )
    row = res.fetchone()
    assert row is not None, "idx_task_results_pending_review должен существовать после M12"
    indexdef = str(row[0])
    assert "checked_at IS NULL" in indexdef
    assert "submitted_at" in indexdef


@pytest.mark.asyncio
async def test_m12_idempotent_repeat_marker_count(db):
    """M12: повторный SELECT по metrics-маркеру стабилен."""
    res = await db.execute(
        text(
            "SELECT COUNT(*) FROM task_results "
            "WHERE metrics ? 'backfill_y6_optimistic'"
        )
    )
    cnt1 = int(res.scalar() or 0)
    res = await db.execute(
        text(
            "SELECT COUNT(*) FROM task_results "
            "WHERE metrics ? 'backfill_y6_optimistic'"
        )
    )
    cnt2 = int(res.scalar() or 0)
    assert cnt1 == cnt2  # без побочных side-effects между SELECT


# ============================== Stage 2: derived is_correct ==============================


@pytest.mark.asyncio
async def test_y6_grade_derived_negative_threshold(db, client):
    """Stage 2: score=2/15 → ratio 0.133 < 0.2 → derived is_correct=FALSE."""
    task_id, course_id, _t = await _pick_root_task(db)
    teacher_id, token = await _setup_teacher_with_course(db, course_id=course_id)
    student_id = await _create_user(db, prefix="y6-stud")
    lock_token = secrets.token_hex(32)
    rid, _, _ = await _create_pending_tr(
        db, student_id=student_id, task_id=task_id,
        teacher_id=teacher_id, lock_token=lock_token,
        is_correct=True,  # optimistic-PASSED
        score=15, max_score=15,
    )
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={"teacher_id": teacher_id, "lock_token": lock_token, "score": 2},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_correct"] is False, body
        assert body["score"] == 2

        # Notification kind
        nrow = (await db.execute(
            text(
                "SELECT kind FROM notifications WHERE user_id=:s "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"s": student_id},
        )).fetchone()
        assert nrow is not None and nrow[0] == "task_returned_for_rework"

        # Audit teacher.review.rejected
        arow = (await db.execute(
            text(
                "SELECT COUNT(*) FROM audit_event "
                "WHERE event_type='teacher.review.rejected' AND user_id=:t"
            ),
            {"t": teacher_id},
        )).scalar()
        assert int(arow or 0) >= 1
    finally:
        await _cleanup(db, user_ids=[teacher_id, student_id], result_ids=[rid])


@pytest.mark.asyncio
async def test_y6_grade_derived_boundary_inclusive(db, client):
    """Stage 2: score=3/15 → 0.2 == 0.2 (inclusive) → derived is_correct=TRUE."""
    task_id, course_id, _t = await _pick_root_task(db)
    teacher_id, token = await _setup_teacher_with_course(db, course_id=course_id)
    student_id = await _create_user(db, prefix="y6-stud")
    lock_token = secrets.token_hex(32)
    rid, _, _ = await _create_pending_tr(
        db, student_id=student_id, task_id=task_id,
        teacher_id=teacher_id, lock_token=lock_token,
        is_correct=True, score=15, max_score=15,
    )
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={"teacher_id": teacher_id, "lock_token": lock_token, "score": 3},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_correct"] is True, body  # 3/15 = 0.2 >= 0.2
    finally:
        await _cleanup(db, user_ids=[teacher_id, student_id], result_ids=[rid])


@pytest.mark.asyncio
async def test_y6_grade_409_already_checked_pivot(db, client):
    """Stage 2: idempotency теперь по `checked_at IS NOT NULL`.

    Создаём task_result с уже выставленным `checked_at` — повторный grade → 409,
    даже если is_correct у нас TRUE (после optimistic-PASSED + grade).
    """
    task_id, course_id, _t = await _pick_root_task(db)
    teacher_id, token = await _setup_teacher_with_course(db, course_id=course_id)
    student_id = await _create_user(db, prefix="y6-stud")
    lock_token = secrets.token_hex(32)
    rid, _, _ = await _create_pending_tr(
        db, student_id=student_id, task_id=task_id,
        teacher_id=teacher_id, lock_token=lock_token,
        is_correct=True, score=15, max_score=15,
    )
    # Set checked_at вручную — имитируем уже-graded
    await db.execute(
        text("UPDATE task_results SET checked_at=now(), checked_by=:t WHERE id=:r"),
        {"t": teacher_id, "r": rid},
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/grade",
            json={"teacher_id": teacher_id, "lock_token": lock_token, "score": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409, resp.text
        assert "уже оценена" in resp.json().get("detail", "").lower()
    finally:
        await _cleanup(db, user_ids=[teacher_id, student_id], result_ids=[rid])


# ============================== Stage 3: regrade ==============================


@pytest.mark.asyncio
async def test_y6_regrade_positive_to_negative_history(db, client):
    """Stage 3: regrade с TRUE → FALSE возвращает задачу в очередь."""
    task_id, course_id, _t = await _pick_root_task(db)
    teacher_id, token = await _setup_teacher_with_course(db, course_id=course_id)
    student_id = await _create_user(db, prefix="y6-stud")
    rid, _, _ = await _create_pending_tr(
        db, student_id=student_id, task_id=task_id,
        is_correct=True, score=15, max_score=15,
    )
    # Помечаем как graded (checked_at set)
    await db.execute(
        text("UPDATE task_results SET checked_at=now(), checked_by=:t WHERE id=:r"),
        {"t": teacher_id, "r": rid},
    )
    await db.commit()
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/regrade",
            json={"score": 1, "comment": "после повторного просмотра"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["old"]["score"] == 15 and body["old"]["is_correct"] is True
        assert body["new"]["score"] == 1 and body["new"]["is_correct"] is False

        # regrade_history append
        mrow = (await db.execute(
            text("SELECT metrics->'regrade_history' FROM task_results WHERE id=:r"),
            {"r": rid},
        )).scalar()
        # asyncpg/jsonb: list-of-dict
        assert mrow is not None
        history = mrow if isinstance(mrow, list) else []
        assert len(history) == 1
        h = history[0]
        assert h["old_score"] == 15 and h["new_score"] == 1
        assert h["old_is_correct"] is True and h["new_is_correct"] is False

        # Notification kind = task_returned_for_rework
        nrow = (await db.execute(
            text("SELECT kind FROM notifications WHERE user_id=:s ORDER BY id DESC LIMIT 1"),
            {"s": student_id},
        )).fetchone()
        assert nrow is not None and nrow[0] == "task_returned_for_rework"
    finally:
        await _cleanup(db, user_ids=[teacher_id, student_id], result_ids=[rid])


@pytest.mark.asyncio
async def test_y6_regrade_409_not_yet_graded(db, client):
    """Stage 3: regrade на pending (checked_at=NULL) → 409."""
    task_id, course_id, _t = await _pick_root_task(db)
    teacher_id, token = await _setup_teacher_with_course(db, course_id=course_id)
    student_id = await _create_user(db, prefix="y6-stud")
    rid, _, _ = await _create_pending_tr(
        db, student_id=student_id, task_id=task_id,
        is_correct=True, score=15, max_score=15,
    )
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/regrade",
            json={"score": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409, resp.text
    finally:
        await _cleanup(db, user_ids=[teacher_id, student_id], result_ids=[rid])


# ============================== Stage 4: escalation cron ==============================


@pytest.mark.asyncio
async def test_y6_escalation_cron_tick_idempotent(db, db_session_factory):
    """Stage 4: escalation_cron_tick идемпотентный."""
    from app.services import escalation_service

    # Фабрика из conftest: привязана к тому же соединению, что и `db`.
    # Глобальная фабрика держит QueuePool (соединение из прошлого event loop
    # ломает asyncpg, tsk-330), а отдельный движок не видит данные теста,
    # лежащие в незакоммиченной транзакции (tsk-333).
    tick_factory = db_session_factory

    # Создаём pending-record старше 48h без escalated_at marker
    task_id, _course_id, _t = await _pick_root_task(db)
    student_id = await _create_user(db, prefix="y6-stud")
    methodist_id = await _create_user(db, role="methodist", prefix="y6-meth")

    old_ts = datetime.now(timezone.utc) - timedelta(hours=72)
    rid, _, _ = await _create_pending_tr(
        db, student_id=student_id, task_id=task_id,
        is_correct=True, score=10, max_score=10,
        submitted_at=old_ts,
    )

    try:
        # Tick #1
        summary1 = await escalation_service.escalation_cron_tick(tick_factory)
        assert summary1["locked"] is True

        # Tick #2 сразу после — escalated_at marker уже стоит, candidate=0
        summary2 = await escalation_service.escalation_cron_tick(tick_factory)
        assert summary2["locked"] is True

        # task_result имеет escalated_at marker
        m = (await db.execute(
            text("SELECT metrics ? 'escalated_at' FROM task_results WHERE id=:r"),
            {"r": rid},
        )).scalar()
        assert m is True

        # methodist получил inbox review_escalated
        nrow = (await db.execute(
            text(
                "SELECT COUNT(*) FROM notifications "
                "WHERE user_id=:m AND kind='review_escalated' "
                "  AND (payload->>'result_id')::int = :r"
            ),
            {"m": methodist_id, "r": rid},
        )).scalar()
        assert int(nrow or 0) == 1, f"Expected 1 notification, got {nrow}"
    finally:
        await _cleanup(db, user_ids=[student_id, methodist_id], result_ids=[rid])


# ============================== Stage 4.4: methodist endpoint ==============================


@pytest.mark.asyncio
async def test_y6_methodist_escalations_endpoint_acl(db, client):
    """Stage 4.4: GET /methodist/escalations/pending — 403 для не-methodist, 200 для methodist."""
    # 1) non-methodist — 403
    other_id = await _create_user(db, prefix="y6-other")
    other_token, _, _ = await create_session(db, user_id=other_id)
    await db.commit()

    methodist_id = await _create_user(db, role="methodist", prefix="y6-meth-acl")
    meth_token, _, _ = await create_session(db, user_id=methodist_id)
    await db.commit()
    try:
        # other → 403
        resp = await client.get(
            "/api/v1/methodist/escalations/pending",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert resp.status_code == 403, resp.text

        # methodist → 200
        resp = await client.get(
            "/api/v1/methodist/escalations/pending",
            headers={"Authorization": f"Bearer {meth_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body and "count" in body
        assert isinstance(body["items"], list)
    finally:
        await _cleanup(db, user_ids=[methodist_id, other_id], result_ids=[])
