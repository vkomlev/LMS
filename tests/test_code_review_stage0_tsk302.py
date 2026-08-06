# tests/test_code_review_stage0_tsk302.py
"""
tsk-302 этап 0: машинная оценка работы доезжает до преподавателя и переживает
ручную проверку.

Закрывает два дефекта, найденных 2026-08-06:

1. `POST /task-results/{id}/manual-check` затирал `metrics`, потому что передавал
   поле в `TaskResultUpdate` ЯВНО — `exclude_unset=True` такое поле не отбрасывает.
   Первая же ручная дооценка обнуляла комментарий предыдущей проверки.
2. Машинная оценка писалась в `metrics` и не отдавалась НИ ОДНИМ эндпоинтом
   кабинета преподавателя (`PendingReviewItem`/`ReviewClaimItem` его не содержали) —
   то есть копилась в БД и никуда не показывалась.
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

_API_KEY = (os.environ.get("VALID_API_KEYS") or "").split(",")[0].strip()

_SAMPLE_REPORT = {
    "code_quality": {
        "pylint": {"score": 8.75, "messages": [{"symbol": "magic-value-comparison", "line": 4}]},
        "radon": {"maintainability_index": 75.16, "complexity": []},
    }
}


async def _create_methodist(db) -> tuple[int, str]:
    """Методист видит весь pool проверок (REVIEW_ACL_SQL bypass) — не зависим от teacher_courses."""
    u = Users(
        email=f"tsk302-mth-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk302-methodist", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT :u, id FROM roles WHERE name='methodist' ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _create_student(db) -> int:
    u = Users(
        email=f"tsk302-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk302-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _create_task(db, *, course_id: int = 1) -> int:
    """SA_COM с manual_review_required — попадает в обязательную очередь проверки."""
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, course_id, difficulty_id) "
            "VALUES (:ext, 10, CAST(:content AS jsonb), CAST(:rules AS jsonb), :cid, 1) RETURNING id"
        ),
        {
            "ext": f"tsk302-stage0-{random.randint(10**8, 10**10)}",
            "content": json.dumps({"type": "SA_COM", "stem": "напиши программу"}),
            "rules": json.dumps({"max_score": 10, "manual_review_required": True}),
            "cid": course_id,
        },
    )
    tid = res.scalar_one()
    await db.commit()
    return tid


async def _create_result(
    db, *, user_id: int, task_id: int, code_review: dict | None, metrics: dict | None = None,
) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        text(
            "INSERT INTO task_results "
            "(score, user_id, task_id, submitted_at, count_retry, received_at, max_score, "
            " source_system, is_correct, code_review, metrics) "
            "VALUES (:s, :u, :t, :now, 0, :now, 10, 'spw', NULL, "
            "        CAST(:cr AS jsonb), CAST(:m AS jsonb)) RETURNING id"
        ),
        {
            "s": 0, "u": user_id, "t": task_id, "now": now,
            "cr": json.dumps(code_review) if code_review is not None else None,
            "m": json.dumps(metrics) if metrics is not None else None,
        },
    )
    rid = res.scalar_one()
    await db.commit()
    return rid


async def _cleanup(db, *, ids: list[int], task_ids: list[int], rids: list[int]) -> None:
    if rids:
        await db.execute(text("DELETE FROM task_results WHERE id = ANY(:r)"), {"r": rids})
    if task_ids:
        await db.execute(text("DELETE FROM tasks WHERE id = ANY(:t)"), {"t": task_ids})
    await db.execute(text("DELETE FROM user_roles WHERE user_id = ANY(:u)"), {"u": ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:u)"), {"u": ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:u)"), {"u": ids})
    await db.commit()


# ---------- Дефект 1: ручная проверка не должна затирать чужие поля ----------

@pytest.mark.skipif(not _API_KEY, reason="нужен VALID_API_KEYS для legacy-эндпоинта manual-check")
async def test_manual_check_without_metrics_keeps_existing_metrics(db, client) -> None:
    """Тело без `metrics` НЕ обнуляет уже записанный комментарий предыдущей проверки."""
    student_id = await _create_student(db)
    task_id = await _create_task(db)
    existing = {"comment": "комментарий первой проверки", "manual_grant": True}
    rid = await _create_result(db, user_id=student_id, task_id=task_id, code_review=None, metrics=existing)
    try:
        resp = await client.post(
            f"/api/v1/task-results/{rid}/manual-check?api_key={_API_KEY}",
            json={"score": 7, "checked_by": student_id},  # metrics НЕ передан
        )
        assert resp.status_code == 200, resp.text

        row = (await db.execute(
            text("SELECT metrics FROM task_results WHERE id = :r"), {"r": rid}
        )).scalar_one()
        assert row == existing, "ручная проверка без metrics не должна затирать существующие"
    finally:
        await _cleanup(db, ids=[student_id], task_ids=[task_id], rids=[rid])


@pytest.mark.skipif(not _API_KEY, reason="нужен VALID_API_KEYS для legacy-эндпоинта manual-check")
async def test_manual_check_with_metrics_still_overwrites(db, client) -> None:
    """Явно переданный `metrics` по-прежнему записывается — прежнее поведение сохранено."""
    student_id = await _create_student(db)
    task_id = await _create_task(db)
    rid = await _create_result(
        db, user_id=student_id, task_id=task_id, code_review=None, metrics={"comment": "старое"},
    )
    try:
        resp = await client.post(
            f"/api/v1/task-results/{rid}/manual-check?api_key={_API_KEY}",
            json={"score": 9, "checked_by": student_id, "metrics": {"comment": "новое"}},
        )
        assert resp.status_code == 200, resp.text

        row = (await db.execute(
            text("SELECT metrics FROM task_results WHERE id = :r"), {"r": rid}
        )).scalar_one()
        assert row == {"comment": "новое"}
    finally:
        await _cleanup(db, ids=[student_id], task_ids=[task_id], rids=[rid])


@pytest.mark.skipif(not _API_KEY, reason="нужен VALID_API_KEYS для legacy-эндпоинта manual-check")
async def test_manual_check_never_touches_code_review(db, client) -> None:
    """
    Машинная оценка переживает ручную проверку — ради этого она и вынесена из `metrics`.

    Это главный регрессионный страж этапа 0: пока отчёт лежал в `metrics`, любая
    дооценка преподавателем стирала его безвозвратно.
    """
    student_id = await _create_student(db)
    task_id = await _create_task(db)
    rid = await _create_result(db, user_id=student_id, task_id=task_id, code_review=_SAMPLE_REPORT)
    try:
        resp = await client.post(
            f"/api/v1/task-results/{rid}/manual-check?api_key={_API_KEY}",
            json={"score": 10, "checked_by": student_id, "is_correct": True,
                  "metrics": {"comment": "проверено"}},
        )
        assert resp.status_code == 200, resp.text

        row = (await db.execute(
            text("SELECT code_review FROM task_results WHERE id = :r"), {"r": rid}
        )).scalar_one()
        assert row == _SAMPLE_REPORT, "ручная проверка не должна трогать машинную оценку"
    finally:
        await _cleanup(db, ids=[student_id], task_ids=[task_id], rids=[rid])


# ---------- Дефект 2: оценка обязана доезжать до преподавателя ----------

async def test_claim_returns_code_review(db, client) -> None:
    """`POST /teacher/reviews/{id}/claim` отдаёт машинную оценку — раньше поля не было вовсе."""
    methodist_id, token = await _create_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db)
    rid = await _create_result(db, user_id=student_id, task_id=task_id, code_review=_SAMPLE_REPORT)
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/claim",
            json={"teacher_id": methodist_id, "ttl_sec": 60},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["item"]
        assert item["code_review"] == _SAMPLE_REPORT
        assert item["code_review"]["code_quality"]["pylint"]["score"] == 8.75
    finally:
        await _cleanup(db, ids=[methodist_id, student_id], task_ids=[task_id], rids=[rid])


async def test_claim_without_report_returns_null_not_error(db, client) -> None:
    """
    Работа без машинной оценки (не код, старая сдача, сбой анализа) отдаётся с null.

    Поле аддитивное и опциональное: клиент просто не рисует блок, а не падает.
    """
    methodist_id, token = await _create_methodist(db)
    student_id = await _create_student(db)
    task_id = await _create_task(db)
    rid = await _create_result(db, user_id=student_id, task_id=task_id, code_review=None)
    try:
        resp = await client.post(
            f"/api/v1/teacher/reviews/{rid}/claim",
            json={"teacher_id": methodist_id, "ttl_sec": 60},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"]["code_review"] is None
    finally:
        await _cleanup(db, ids=[methodist_id, student_id], task_ids=[task_id], rids=[rid])
