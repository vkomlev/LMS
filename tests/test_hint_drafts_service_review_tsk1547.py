"""Штатная вычитка черновиков подсказок сервисным ключом capability (tsk-1547).

Оператор 18.11 разрешил субагентам вычитку черновиков (tsk-1220) без
согласования. До задачи гейт review-эндпоинта отклонял любой is_service;
теперь отдельный секрет HINT_REVIEW_API_KEY даёт capability:

- обычный сервисный ключ (VALID_API_KEYS) → по-прежнему 403;
- ключ HINT_REVIEW_API_KEY → edit/approve/reject проходят: `reviewed_by`
  остаётся NULL (это FK на users — сервис туда записать нельзя), в note
  ставится маркер `[svc:hint-review]`, provenance.edited_by = 0
  (несуществующий пользователь — не спутать с человеком), факт вычитки —
  в audit_event (`hint.draft.reviewed`);
- svc-правка (action save) защищена от перезатирания повторной генерацией
  (маркер в note, guard в store).
"""
from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text

from app.auth import service_api_key
from app.core.config import Settings
from app.models.users import Users
from app.services import hint_drafts_service as svc
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

pytestmark = pytest.mark.asyncio

_settings = Settings()
_TAG = "tsk1547"
_HINT_KEY = "hint-review-test-key-1547"
_OLD_HINT = "Разряды двоичного числа справа налево — 1, 2, 4, 8."
_DRAFT_TEXT = "Подпиши под каждой цифрой её вес-степень двойки и сложи только нужные."


@pytest.fixture
def hint_review_key(monkeypatch):
    """Включить capability: привязать тестовый ключ к HINT_REVIEW_API_KEY."""
    monkeypatch.setattr(
        service_api_key._get_settings(), "hint_review_api_key", _HINT_KEY
    )
    return {"X-API-Key": _HINT_KEY}


def _plain_key() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


async def _methodist(db) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-m-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=f"{_TAG}-m", tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) SELECT :u, r.id FROM roles r "
            "WHERE r.name = 'methodist' ON CONFLICT DO NOTHING"
        ),
        {"u": u.id},
    )
    await db.commit()
    return u.id, token


async def _task(db) -> int:
    course_id = await db.scalar(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"{_TAG} {uuid.uuid4().hex[:8]}"},
    )
    content = {
        "type": "SA", "title": f"{_TAG} задание",
        "stem": "Переведи число 10001001011 из двоичной системы в десятичную.",
        "hints_text": [_OLD_HINT],
        "has_hints": True,
    }
    rules = {"max_score": 1, "short_answer": {"accepted_answers": [{"value": "1099"}]}}
    task_id = await db.scalar(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, external_uid, task_content, solution_rules, "
            " max_score, order_position, is_active) VALUES (:c, (SELECT id FROM difficulties LIMIT 1), "
            " :u, CAST(:tc AS jsonb), CAST(:sr AS jsonb), 1, 1, true) RETURNING id"
        ),
        {"c": course_id, "u": f"{_TAG}-{uuid.uuid4().hex[:10]}",
         "tc": __import__("json").dumps(content), "sr": __import__("json").dumps(rules)},
    )
    await db.commit()
    return int(task_id)


async def _draft(db, task_id: int, text: str = _DRAFT_TEXT) -> int:
    row = await svc.store(
        db, task_id=task_id, status="draft", text_=text,
        source_reply_ids=[random.randint(10**8, 10**10)], model="fake/model",
        note=None, lint_flags=[],
    )
    return row.id


async def _audit_events(db, draft_id: int) -> list:
    rows = (
        await db.execute(
            text(
                "SELECT event_type, user_id, details FROM audit_event "
                "WHERE event_type = 'hint.draft.reviewed' "
                "AND details->>'draft_id' = :id"
            ),
            {"id": str(draft_id)},
        )
    ).all()
    return rows


async def test_plain_service_key_still_cannot_review(db, client):
    """VALID_API_KEYS без capability → 403, как до задачи."""
    task_id = await _task(db)
    draft_id = await _draft(db, task_id)
    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review",
        headers=_plain_key(), json={"action": "approve"},
    )
    assert resp.status_code == 403


async def test_capability_key_approves_and_audits(db, client, hint_review_key):
    """HINT_REVIEW_API_KEY подтверждает: hints_text дописан, provenance и audit корректны."""
    task_id = await _task(db)
    draft_id = await _draft(db, task_id)

    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review",
        headers=hint_review_key, json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"]["status"] == "approved"
    # сервис ≠ человек: reviewed_by пуст, маркер в note
    assert body["draft"]["reviewed_by"] is None
    assert body["draft"]["note"] == "[svc:hint-review]"
    # подсказка дописана в конец, прежняя не тронута
    assert body["hints_text"] == [_OLD_HINT, _DRAFT_TEXT]

    prov = await db.scalar(text("SELECT content_provenance FROM tasks WHERE id=:t"), {"t": task_id})
    assert prov["source"] == "manual_web"
    assert prov["edited_by"] == 0  # не человек: пользователя 0 не существует
    assert prov["reason"] == f"hint-draft:{draft_id}"

    events = await _audit_events(db, draft_id)
    assert len(events) == 1
    assert events[0].user_id is None
    assert events[0].details["action"] == "approve"
    assert events[0].details["service"] == "svc:hint-review"
    assert events[0].details["task_id"] == task_id


async def test_capability_key_edit_marks_draft_and_blocks_overwrite(db, client, hint_review_key):
    """svc-правка (save) ставит маркер; повторная генерация её не затирает."""
    task_id = await _task(db)
    draft_id = await _draft(db, task_id)

    edited = "Правленный сервисом текст подсказки."
    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review",
        headers=hint_review_key, json={"action": "save", "text": edited},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["draft"]["status"] == "draft"
    assert resp.json()["draft"]["reviewed_by"] is None
    assert resp.json()["draft"]["note"] == "[svc:hint-review]"
    assert resp.json()["draft"]["text"] == edited

    # store с новым ответом: source_reply_ids допишутся, а svc-текст не перезатрётся
    await svc.store(
        db, task_id=task_id, status="draft", text_="Текст новой генерации",
        source_reply_ids=[random.randint(10**8, 10**10)], model="fake/model-v2",
        note=None, lint_flags=[],
    )
    row = await db.scalar(
        text("SELECT text FROM task_hint_drafts WHERE id=:id"), {"id": draft_id}
    )
    assert row == edited


async def test_capability_key_rejects(db, client, hint_review_key):
    """svc-reject работает и пометку ручной правки (provenance) не ставит."""
    task_id = await _task(db)
    draft_id = await _draft(db, task_id)

    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review",
        headers=hint_review_key, json={"action": "reject"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["draft"]["status"] == "rejected"
    prov = await db.scalar(text("SELECT content_provenance FROM tasks WHERE id=:t"), {"t": task_id})
    assert prov is None
    events = await _audit_events(db, draft_id)
    assert len(events) == 1 and events[0].details["action"] == "reject"


async def test_human_review_unchanged(db, client):
    """Человеческая вычитка не менялась: reviewed_by = id методиста, note без маркера."""
    mid, token = await _methodist(db)
    task_id = await _task(db)
    draft_id = await _draft(db, task_id)

    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review",
        headers={"Authorization": f"Bearer {token}"}, json={"action": "approve"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["draft"]["reviewed_by"] == mid
    assert resp.json()["draft"]["note"] is None
    prov = await db.scalar(text("SELECT content_provenance FROM tasks WHERE id=:t"), {"t": task_id})
    assert prov["edited_by"] == mid
    assert await _audit_events(db, draft_id) == []
