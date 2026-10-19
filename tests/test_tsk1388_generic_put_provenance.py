"""tsk-1388: правка содержимого через общий `PUT /tasks/{id}` ставит пометку ручной правки.

Без пометки переиздание из ContentBackbone затирает правку, а чек cb-drift
показывает её как «правку без пометки» (tsk-1385). Решение tsk-808 (пометку
ставит только кабинет методиста) отменено оператором 19.10.2026.
Служебная правка без содержимого (например, `is_active`) пометку не ставит.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.core.config import Settings

_settings = Settings()
EASY = 2


def _key() -> dict[str, str]:
    return {"api_key": next(iter(_settings.valid_api_keys))}


async def _new_course(db) -> int:
    row = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, is_required, course_uid) "
                "VALUES ('test_tsk1388', 'tsk-1388', 'self_guided', false, :uid) RETURNING id"
            ),
            {"uid": f"lms:test:t1388:{uuid.uuid4().hex[:12]}"},
        )
    ).first()
    await db.commit()
    return int(row.id)


def _content() -> dict[str, Any]:
    return {"type": "SA", "stem": "Сколько будет 1/2?"}


def _rules(answers: list[str]) -> dict[str, Any]:
    return {
        "max_score": 1,
        "short_answer": {
            "normalization": ["trim", "lower"],
            "accepted_answers": [{"value": a, "score": 1} for a in answers],
        },
    }


async def _seed(db, client) -> tuple[int, str]:
    course_id = await _new_course(db)
    uid = f"t1388-{uuid.uuid4().hex[:8]}"
    r = await client.post(
        "/api/v1/tasks/bulk-upsert",
        params=_key(),
        json={"items": [{
            "external_uid": uid, "course_id": course_id, "difficulty_id": EASY,
            "task_content": _content(), "solution_rules": _rules(["0.5"]), "max_score": 1,
        }]},
    )
    assert r.status_code == 200, r.text
    row = (await db.execute(text("SELECT id FROM tasks WHERE external_uid=:u"), {"u": uid})).first()
    return int(row.id), uid


async def _prov(db, uid: str) -> Any:
    db.expire_all()
    row = (
        await db.execute(text("SELECT content_provenance FROM tasks WHERE external_uid=:u"), {"u": uid})
    ).first()
    return row.content_provenance


@pytest.mark.asyncio
async def test_put_content_marks_manual_edit(db, client):
    task_id, uid = await _seed(db, client)
    r = await client.put(
        f"/api/v1/tasks/{task_id}", params=_key(),
        json={"solution_rules": _rules(["0.5", "0,5"])},
    )
    assert r.status_code == 200, r.text
    prov = await _prov(db, uid)
    assert prov is not None, "правка правила проверки — ручная правка"
    assert prov["source"] == "manual_web"
    assert prov["fields"] == ["solution_rules", "task_content"]
    assert prov["reason"] == "generic-put"
    assert r.json().get("content_provenance") is not None


@pytest.mark.asyncio
async def test_put_service_field_does_not_mark(db, client):
    task_id, uid = await _seed(db, client)
    r = await client.put(f"/api/v1/tasks/{task_id}", params=_key(), json={"is_active": False})
    assert r.status_code == 200, r.text
    assert await _prov(db, uid) is None, "служебная правка не должна замораживать содержимое"
