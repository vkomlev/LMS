"""tsk-1309: автопродление лимита попыток при первой блокировке.

Первая блокировка пары (ученик, задание) снимается сама: лимит +базовый,
закрытая заявка-след `trigger=auto_extend`, уведомление ученику. Вторая
блокировка той же пары — обычная заявка человеку. Квиз и диагностика —
никогда. Выключатель ATTEMPTS_AUTO_EXTEND_ENABLED=false возвращает прежнее.
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import attempt_auto_extend_service as svc
from app.services.teacher_activity_feed_service import _fetch_help_requested


async def _student(db) -> int:
    u = Users(
        email=f"tsk1309-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk1309-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    return u.id


async def _task(db, *, typ: str = "SA", rules: dict | None = None) -> int:
    row = (await db.execute(text("SELECT id FROM tasks LIMIT 1"))).fetchone()
    if row is None:
        pytest.skip("Нет задач в БД")
    await db.execute(
        text(
            "UPDATE tasks SET max_attempts = NULL, "
            "task_content = jsonb_set(coalesce(task_content, '{}'::jsonb), '{type}', to_jsonb(CAST(:typ AS text))), "
            "solution_rules = CAST(:rules AS jsonb) WHERE id = :id"
        ),
        {"typ": typ, "rules": json.dumps(rules or {"max_score": 1}), "id": int(row[0])},
    )
    return int(row[0])


async def _extend(db, student_id: int, task_id: int):
    return await svc.try_auto_extend(
        db, student_id=student_id, task_id=task_id, course_id=None,
        attempt_id=None, attempts_used=3, attempts_limit_effective=3,
    )


@pytest.mark.asyncio
async def test_first_block_extends_and_leaves_trace(db):
    student_id, task_id = await _student(db), await _task(db)

    assert await _extend(db, student_id, task_id) == 6

    limit = (await db.execute(text(
        "SELECT max_attempts_override, updated_by FROM student_task_limit_override "
        "WHERE student_id = :s AND task_id = :t"), {"s": student_id, "t": task_id})).fetchone()
    assert tuple(limit) == (6, None)
    hr = (await db.execute(text(
        "SELECT status, context_json->>'trigger' FROM help_requests "
        "WHERE student_id = :s AND task_id = :t"), {"s": student_id, "t": task_id})).fetchall()
    assert [tuple(r) for r in hr] == [("closed", "auto_extend")]
    note = (await db.execute(text(
        "SELECT count(*) FROM notifications WHERE user_id = :s"), {"s": student_id})).scalar()
    assert note == 1


@pytest.mark.asyncio
async def test_second_block_goes_to_human(db):
    student_id, task_id = await _student(db), await _task(db)
    assert await _extend(db, student_id, task_id) == 6
    assert await _extend(db, student_id, task_id) is None


@pytest.mark.asyncio
async def test_open_request_is_not_overridden(db):
    student_id, task_id = await _student(db), await _task(db)
    await db.execute(text(
        "INSERT INTO help_requests (status, request_type, auto_created, student_id, task_id, created_at, updated_at) "
        "VALUES ('open', 'blocked_limit', true, :s, :t, now(), now())"), {"s": student_id, "t": task_id})
    assert await _extend(db, student_id, task_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("typ,rules", [("SC_Qw", None), ("SA", {"max_score": 1, "diagnostic": True})])
async def test_quiz_and_diagnostic_never_extend(db, typ, rules):
    student_id, task_id = await _student(db), await _task(db, typ=typ, rules=rules)
    assert await _extend(db, student_id, task_id) is None


@pytest.mark.asyncio
async def test_switch_off_keeps_old_behaviour(db, monkeypatch):
    monkeypatch.setattr(svc, "Settings", lambda: SimpleNamespace(attempts_auto_extend_enabled=False))
    student_id, task_id = await _student(db), await _task(db)
    assert await _extend(db, student_id, task_id) is None


@pytest.mark.asyncio
async def test_feed_shows_auto_extension(db):
    student_id, task_id = await _student(db), await _task(db)
    await _extend(db, student_id, task_id)
    events = await _fetch_help_requested(db, teacher_id=0, elevated=True, limit=500, before=None)
    mine = [e for e in events if e["student_id"] == student_id]
    assert len(mine) == 1
    assert mine[0]["outcome"] == "auto_extended"
    assert "выданы автоматически (3 шт.)" in mine[0]["summary"]
