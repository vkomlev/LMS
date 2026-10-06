"""tsk-1252: диагностическое задание (solution_rules.diagnostic).

Пре-тест до и после урока: одна попытка, неверный ответ — это результат замера,
а не затруднение. Поэтому по исчерпанию попыток заявка помощи преподавателю НЕ
создаётся, а клиенту в состоянии задания приходит флаг `diagnostic`, чтобы он
написал «ответ принят» вместо «превышен лимит попыток».

Обычное задание ведёт себя как раньше: заявка blocked_limit создаётся.
"""

from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services.help_requests_service import get_or_create_blocked_limit_help_request
from app.services.task_form_flags import compute_task_form_flags


async def _create_student(db) -> int:
    u = Users(
        email=f"tsk1252-stud-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name="tsk1252-stud", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await db.commit()
    return u.id


async def _pick_task(db) -> tuple[int, object]:
    row = (await db.execute(text("SELECT id, solution_rules FROM tasks LIMIT 1"))).fetchone()
    if row is None:
        pytest.skip("Нет задач в БД")
    return int(row[0]), row[1]


async def _set_rules(db, task_id: int, rules: object) -> None:
    await db.execute(
        text("UPDATE tasks SET solution_rules = CAST(:r AS jsonb) WHERE id = :id"),
        {"r": json.dumps(rules) if rules is not None else None, "id": task_id},
    )
    await db.commit()


async def _open_requests(db, student_id: int, task_id: int) -> list[int]:
    rows = await db.execute(
        text(
            "SELECT id FROM help_requests WHERE student_id = :s AND task_id = :t "
            "AND request_type = 'blocked_limit'"
        ),
        {"s": student_id, "t": task_id},
    )
    return [int(r[0]) for r in rows.fetchall()]


async def _cleanup(db, student_id: int, task_id: int, rules: object) -> None:
    await db.execute(text("DELETE FROM help_requests WHERE student_id = :s"), {"s": student_id})
    await db.execute(text("DELETE FROM notifications WHERE user_id = :s"), {"s": student_id})
    await db.execute(text("DELETE FROM users WHERE id = :s"), {"s": student_id})
    await db.commit()
    await _set_rules(db, task_id, rules)


@pytest.mark.asyncio
async def test_diagnostic_task_creates_no_blocked_limit_request(db):
    """Диагностическое задание: заявка не создаётся, возвращается (0, False, False)."""
    student_id = await _create_student(db)
    task_id, original = await _pick_task(db)
    try:
        rules = dict(original or {"max_score": 1})
        rules["diagnostic"] = True
        await _set_rules(db, task_id, rules)

        result = await get_or_create_blocked_limit_help_request(
            db, student_id=student_id, task_id=task_id, attempts_used=1, attempts_limit_effective=1,
        )
        await db.commit()

        assert result == (0, False, False)
        assert await _open_requests(db, student_id, task_id) == []
    finally:
        await _cleanup(db, student_id, task_id, original)


@pytest.mark.asyncio
async def test_regular_task_still_creates_blocked_limit_request(db):
    """Обычное задание — прежнее поведение: заявка blocked_limit создаётся."""
    student_id = await _create_student(db)
    task_id, original = await _pick_task(db)
    try:
        rules = dict(original or {"max_score": 1})
        rules.pop("diagnostic", None)
        await _set_rules(db, task_id, rules)

        request_id, created, _ = await get_or_create_blocked_limit_help_request(
            db, student_id=student_id, task_id=task_id, attempts_used=3, attempts_limit_effective=3,
        )
        await db.commit()

        assert created is True
        assert await _open_requests(db, student_id, task_id) == [request_id]
    finally:
        await _cleanup(db, student_id, task_id, original)


def test_form_flag_follows_solution_rules():
    """Флаг формы повторяет правило; по умолчанию выключен."""
    content = {"type": "SC"}
    assert compute_task_form_flags({"max_score": 1, "diagnostic": True}, content).diagnostic is True
    assert compute_task_form_flags({"max_score": 1}, content).diagnostic is False
    assert compute_task_form_flags(None, content).diagnostic is False
