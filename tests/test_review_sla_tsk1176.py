"""tsk-1176: срок ручной проверки 48 ч — возраст и просрочка в очереди,
счётчик залежавшихся работ для напоминания в боте, срок ученику.

Покрывает:
- функции срока: aware/naive/None/не-datetime (обязательные негативные кейсы);
- `GET /teacher/reviews/pending`: age_hours / review_due_at / is_overdue;
- сводка внимания: reviews_stale растёт только от работ старше порога,
  захваченная работа не считается;
- `POST /attempts/{id}/answers`: у TA review_due_at заполнен, у SA — нет;
- `/me/history`-выборка: review_due_at у ждущей работы и null после проверки.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.services import me_service, review_sla, teacher_attention_service
from app.services.auth.session_service import create_session

_settings = Settings()


# ── функции срока ────────────────────────────────────────────────────────────

def test_due_at_is_submitted_plus_sla() -> None:
    sub = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    assert review_sla.review_due_at(sub) == sub + timedelta(hours=_settings.review_sla_hours)


def test_none_and_wrong_type_give_no_deadline() -> None:
    assert review_sla.review_due_at(None) is None
    assert review_sla.review_age_hours(None) is None
    assert review_sla.is_review_overdue(None) is False
    assert review_sla.review_due_at("2026-10-01T10:00:00Z") is None  # type: ignore[arg-type]
    assert review_sla.is_review_overdue("2026-10-01") is False  # type: ignore[arg-type]


def test_naive_is_treated_as_utc() -> None:
    naive = datetime(2026, 10, 1, 10, 0)
    aware = naive.replace(tzinfo=timezone.utc)
    assert review_sla.review_due_at(naive) == review_sla.review_due_at(aware)
    now_naive = datetime(2026, 10, 1, 13, 0)
    assert review_sla.review_age_hours(aware, now_naive) == 3.0


def test_overdue_boundary_and_future_submit() -> None:
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    h = _settings.review_sla_hours
    assert review_sla.is_review_overdue(now - timedelta(hours=h + 1), now) is True
    assert review_sla.is_review_overdue(now - timedelta(hours=h - 1), now) is False
    # Часы клиента впереди — возраст не уходит в минус.
    assert review_sla.review_age_hours(now + timedelta(hours=1), now) == 0.0


# ── фикстуры ─────────────────────────────────────────────────────────────────

async def _user(db, tag: str, *, methodist: bool = False) -> tuple[int, str]:
    r = await db.execute(
        text("INSERT INTO users (email, full_name) VALUES (:e, :n) RETURNING id"),
        {"e": f"t1176-{tag}-{uuid.uuid4().hex[:8]}@example.com", "n": f"t1176 {tag}"},
    )
    uid = int(r.scalar())
    if methodist:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = 'methodist' ON CONFLICT DO NOTHING"
            ),
            {"u": uid},
        )
    token, _, _ = await create_session(db, user_id=uid)
    await db.commit()
    return uid, token


async def _course(db) -> int:
    r = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"t1176 {uuid.uuid4().hex[:8]}"},
    )
    cid = int(r.scalar())
    await db.commit()
    return cid


async def _task(db, course_id: int, type_: str) -> int:
    diff = (await db.execute(text("SELECT id FROM difficulties LIMIT 1"))).scalar()
    if type_ == "TA":
        sr = {"max_score": 6, "penalties": {"missing_answer": 0}}
    else:
        sr = {
            "max_score": 6,
            "short_answer": {"normalization": ["trim"], "accepted_answers": [{"value": "42", "score": 6}]},
        }
    r = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, course_id, difficulty_id, task_content, solution_rules) "
            "VALUES (:ext, :cid, :did, CAST(:tc AS jsonb), CAST(:sr AS jsonb)) RETURNING id"
        ),
        {
            "ext": f"t1176-{random.randint(10**8, 10**10)}",
            "cid": course_id, "did": diff,
            "tc": json.dumps({"type": type_, "stem": "Вопрос"}),
            "sr": json.dumps(sr),
        },
    )
    tid = int(r.scalar())
    await db.commit()
    return tid


async def _result(db, *, user_id: int, task_id: int, age_hours: float, claimed: bool = False) -> int:
    sub = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    claim = datetime.now(timezone.utc) + timedelta(minutes=10) if claimed else None
    r = await db.execute(
        text(
            "INSERT INTO task_results (score, user_id, task_id, submitted_at, count_retry, "
            "received_at, max_score, source_system, is_correct, review_claim_expires_at) "
            "VALUES (6, :u, :t, :sub, 0, :sub, 6, 'spw_web', TRUE, :claim) RETURNING id"
        ),
        {"u": user_id, "t": task_id, "sub": sub, "claim": claim},
    )
    rid = int(r.scalar())
    await db.commit()
    return rid


async def _cleanup(db, *, course_id: int, user_ids: list[int]) -> None:
    await db.execute(
        text("DELETE FROM task_results WHERE task_id IN (SELECT id FROM tasks WHERE course_id = :c)"),
        {"c": course_id},
    )
    await db.execute(text("DELETE FROM attempts WHERE course_id = :c"), {"c": course_id})
    await db.execute(text("DELETE FROM tasks WHERE course_id = :c"), {"c": course_id})
    await db.execute(text("DELETE FROM courses WHERE id = :c"), {"c": course_id})
    for uid in user_ids:
        await db.execute(text("DELETE FROM user_session WHERE user_id = :u"), {"u": uid})
        await db.execute(text("DELETE FROM user_roles WHERE user_id = :u"), {"u": uid})
        await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    await db.commit()


# ── очередь преподавателя ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_list_has_age_and_overdue(db, client) -> None:
    met_id, token = await _user(db, "met", methodist=True)
    stud_id, _ = await _user(db, "stud")
    cid = await _course(db)
    tid = await _task(db, cid, "TA")
    fresh = await _result(db, user_id=stud_id, task_id=tid, age_hours=2)
    old = await _result(db, user_id=stud_id, task_id=tid, age_hours=50)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&course_id={cid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        items = {it["id"]: it for it in resp.json()["items"]}
        assert items[fresh]["is_overdue"] is False
        assert 1.9 <= items[fresh]["age_hours"] <= 2.2
        assert items[old]["is_overdue"] is True
        assert items[old]["age_hours"] >= 49.9
        due = datetime.fromisoformat(items[old]["review_due_at"].replace("Z", "+00:00"))
        assert due < datetime.now(timezone.utc)
    finally:
        await _cleanup(db, course_id=cid, user_ids=[met_id, stud_id])


@pytest.mark.asyncio
async def test_optional_review_has_no_deadline(db, client) -> None:
    """Авто-проверенный SA в очереди review_kind=all срока не получает."""
    met_id, token = await _user(db, "met-opt", methodist=True)
    stud_id, _ = await _user(db, "stud-opt")
    cid = await _course(db)
    sa = await _task(db, cid, "SA")  # manual_review_required не задан → опциональная
    rid = await _result(db, user_id=stud_id, task_id=sa, age_hours=60)
    try:
        resp = await client.get(
            f"/api/v1/teacher/reviews/pending?teacher_id={met_id}&course_id={cid}&review_kind=all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        item = next(it for it in resp.json()["items"] if it["id"] == rid)
        assert item["review_due_at"] is None
        assert item["is_overdue"] is False
        assert item["age_hours"] >= 59.9
    finally:
        await _cleanup(db, course_id=cid, user_ids=[met_id, stud_id])


# ── сводка внимания (напоминание в боте) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_attention_counts_only_stale_unclaimed(db) -> None:
    met_id, _ = await _user(db, "met2", methodist=True)
    stud_id, _ = await _user(db, "stud2")
    cid = await _course(db)
    tid = await _task(db, cid, "TA")
    try:
        before = await teacher_attention_service.get_summary(db, teacher_id=met_id)
        assert before["review_reminder_hours"] == _settings.review_reminder_hours
        await _result(db, user_id=stud_id, task_id=tid, age_hours=2)  # свежая
        await _result(db, user_id=stud_id, task_id=tid, age_hours=40, claimed=True)  # уже проверяют
        await _result(db, user_id=stud_id, task_id=tid, age_hours=40)  # залежалась
        after = await teacher_attention_service.get_summary(db, teacher_id=met_id)
        assert after["reviews_stale"] - before["reviews_stale"] == 1
        assert after["reviews_stale_oldest_at"] is not None
    finally:
        await _cleanup(db, course_id=cid, user_ids=[met_id, stud_id])


@pytest.mark.asyncio
async def test_attention_ignores_courses_outside_acl(db) -> None:
    """Обычный преподаватель без привязки к курсу не видит чужую залежавшуюся работу."""
    teacher_id, _ = await _user(db, "teacher")
    stud_id, _ = await _user(db, "stud3")
    cid = await _course(db)
    tid = await _task(db, cid, "TA")
    try:
        await _result(db, user_id=stud_id, task_id=tid, age_hours=40)
        summary = await teacher_attention_service.get_summary(db, teacher_id=teacher_id)
        assert summary["reviews_stale"] == 0
    finally:
        await _cleanup(db, course_id=cid, user_ids=[teacher_id, stud_id])


# ── ученик: ответ сдачи и история ────────────────────────────────────────────

def _api_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


@pytest.mark.asyncio
async def test_submit_ta_returns_deadline_sa_does_not(db, client) -> None:
    stud_id, _ = await _user(db, "stud4")
    cid = await _course(db)
    ta = await _task(db, cid, "TA")
    sa = await _task(db, cid, "SA")
    try:
        resp = await client.post(
            "/api/v1/attempts",
            json={"user_id": stud_id, "course_id": cid, "source_system": "test"},
            headers=_api_headers(),
        )
        assert resp.status_code == 201, resp.text
        aid = int(resp.json()["id"])

        r_ta = await client.post(
            f"/api/v1/attempts/{aid}/answers",
            json={"items": [{"task_id": ta, "answer": {"type": "TA", "response": {"text": "Мой ответ"}}}]},
            headers=_api_headers(),
        )
        assert r_ta.status_code == 200, r_ta.text
        cr = r_ta.json()["results"][0]["check_result"]
        assert cr["is_correct"] is True  # оптимистичный зачёт TA
        due = datetime.fromisoformat(cr["manual_check_due_at"].replace("Z", "+00:00"))
        expected = datetime.now(timezone.utc) + timedelta(hours=_settings.review_sla_hours)
        assert abs((due - expected).total_seconds()) < 120

        r_sa = await client.post(
            f"/api/v1/attempts/{aid}/answers",
            json={"items": [{"task_id": sa, "answer": {"type": "SA", "response": {"value": "42"}}}]},
            headers=_api_headers(),
        )
        assert r_sa.status_code == 200, r_sa.text
        assert r_sa.json()["results"][0]["check_result"]["manual_check_due_at"] is None
    finally:
        await _cleanup(db, course_id=cid, user_ids=[stud_id])


@pytest.mark.asyncio
async def test_history_deadline_until_checked(db) -> None:
    stud_id, _ = await _user(db, "stud5")
    cid = await _course(db)
    tid = await _task(db, cid, "TA")
    rid = await _result(db, user_id=stud_id, task_id=tid, age_hours=5)
    try:
        rows = await me_service.get_history(db, user_id=stud_id, filter_="all", limit=10, offset=0)
        row = next(r for r in rows if r["task_result_id"] == rid)
        assert row["review_due_at"] is not None
        assert "awaits_teacher" not in row

        await db.execute(text("UPDATE task_results SET checked_at = now() WHERE id = :r"), {"r": rid})
        await db.commit()
        rows = await me_service.get_history(db, user_id=stud_id, filter_="all", limit=10, offset=0)
        assert next(r for r in rows if r["task_result_id"] == rid)["review_due_at"] is None
    finally:
        await _cleanup(db, course_id=cid, user_ids=[stud_id])
