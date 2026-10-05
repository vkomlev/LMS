"""Черновики подсказок из ответов преподавателей (tsk-1220).

Покрывает:
- (а) отбор ответов: только текст, без ссылок, без служебных тестовых ответов;
  задание уходит из кандидатов, когда его ответы вошли в черновик, и
  возвращается с новым ответом;
- (б) генерация: промпт содержит условие, подсказки и ответы; пропуск модели;
  локальный линтер ловит код и эталон;
- (в) запись: черновик с метками линтера в очередь не принимается;
- (г) вычитка: approve дописывает в конец `hints_text`, не трогая прежние;
  сервисный ключ не подтверждает; повторная обработка — 409;
- (д) роль: ученик очередь не видит.
"""
from __future__ import annotations

import json
import random
import uuid

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services import hint_drafts_service as svc
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.llm.contracts import LLMResult

pytestmark = pytest.mark.asyncio

_settings = Settings()
_TAG = "tsk1220"
_OLD_HINT = "Разряды двоичного числа справа налево — 1, 2, 4, 8."


def _key() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _user(db, role: str | None = None) -> tuple[int, str]:
    user = Users(
        email=f"{_TAG}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None, full_name=f"{_TAG}-{role or 'student'}", tg_id=None,
    )
    db.add(user)
    await db.flush()
    await identity_link_service.upsert_identity(db, user.id, "email", user.email)
    token, _, _ = await create_session(db, user_id=user.id)
    if role:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) SELECT :u, r.id FROM roles r "
                "WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": user.id, "role": role},
        )
    await db.commit()
    return user.id, token


async def _task(db, *, hints: list[str] | None = None, answer: str = "1099") -> int:
    course_id = await db.scalar(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'auto_check') RETURNING id"),
        {"t": f"{_TAG} {uuid.uuid4().hex[:8]}"},
    )
    content = {
        "type": "SA", "title": f"{_TAG} задание",
        "stem": "Переведи число 10001001011 из двоичной системы в десятичную.",
        "hints_text": hints if hints is not None else [_OLD_HINT],
        "has_hints": bool(hints if hints is not None else [_OLD_HINT]),
    }
    rules = {"max_score": 1, "short_answer": {"accepted_answers": [{"value": answer}]}}
    task_id = await db.scalar(
        text(
            "INSERT INTO tasks (course_id, difficulty_id, external_uid, task_content, solution_rules, "
            " max_score, order_position, is_active) VALUES (:c, (SELECT id FROM difficulties LIMIT 1), "
            " :u, CAST(:tc AS jsonb), CAST(:sr AS jsonb), 1, 1, true) RETURNING id"
        ),
        {"c": course_id, "u": f"{_TAG}-{uuid.uuid4().hex[:10]}",
         "tc": json.dumps(content), "sr": json.dumps(rules)},
    )
    await db.commit()
    return int(task_id)


async def _reply(db, *, task_id: int, sid: int, tid: int, body: str, kind: str = "text") -> int:
    rid = await db.scalar(
        text(
            "INSERT INTO help_requests (status, student_id, task_id, request_type, assigned_teacher_id, "
            " auto_created, context_json, priority, created_at, updated_at) "
            "VALUES ('closed', :s, :t, 'manual_help', :at, false, '{}'::jsonb, 100, now(), now()) RETURNING id"
        ),
        {"s": sid, "t": task_id, "at": tid},
    )
    mid = await db.scalar(
        text(
            "INSERT INTO messages (message_type, content, sender_id, recipient_id) "
            "VALUES ('help_reply', CAST(:b AS jsonb), :t, :s) RETURNING id"
        ),
        {"t": tid, "s": sid, "b": json.dumps({"text": body})},
    )
    reply_id = await db.scalar(
        text(
            "INSERT INTO help_request_replies (request_id, teacher_id, message_id, body, reply_kind) "
            "VALUES (:r, :t, :m, :b, :k) RETURNING id"
        ),
        {"r": rid, "t": tid, "m": mid, "b": body, "k": kind},
    )
    await db.commit()
    return int(reply_id)


_GOOD = "Распиши разряды справа налево и подумай, какие степени двойки стоят под единицами — их и складывай."


@pytest.fixture
def fake_llm(monkeypatch):
    """Подменить модель: ответ задаёт тест."""
    box: dict = {"text": json.dumps({"skip": False, "hint": _GOOD, "reason": None}), "calls": []}

    async def _complete(messages, **kwargs):
        box["calls"].append(messages)
        return LLMResult(text=box["text"], model="fake/model", tokens_in=1, tokens_out=1)

    monkeypatch.setattr(svc.llm_client, "complete", _complete)
    return box


# ── (а) отбор ──────────────────────────────────────────────────────────────


async def test_candidates_select_only_text_replies_without_links(db):
    sid, _ = await _user(db)
    tid, _ = await _user(db, "teacher")
    task_id = await _task(db)
    good = await _reply(db, task_id=task_id, sid=sid, tid=tid,
                        body="Посмотри, какие степени двойки стоят под единицами, и сложи только их.")
    await _reply(db, task_id=task_id, sid=sid, tid=tid,
                 body="Давай созвонимся, вот ссылка https://telemost.yandex.ru/j/123456", kind="telemost")
    await _reply(db, task_id=task_id, sid=sid, tid=tid,
                 body="Посмотри разбор, там всё показано подробно: https://vk.com/video-1_2")
    await _reply(db, task_id=task_id, sid=sid, tid=tid,
                 body="tsk-348 follow-up: служебный ответ живого прогона, можно игнорировать")
    await _reply(db, task_id=task_id, sid=sid, tid=tid, body="ок, исправил")

    replies = await svc.source_replies(db, task_id)
    assert [r.reply_id for r in replies] == [good]
    cands = {c["task_id"]: c for c in await svc.candidates(db, limit=500)}
    assert cands[task_id]["new_reply_ids"] == [good]


async def test_task_returns_to_candidates_only_with_new_reply(db):
    sid, _ = await _user(db)
    tid, _ = await _user(db, "teacher")
    task_id = await _task(db)
    r1 = await _reply(db, task_id=task_id, sid=sid, tid=tid,
                      body="Сначала запиши под каждой цифрой её вес, потом сложи нужные веса.")
    await svc.store(db, task_id=task_id, status="skipped", text_=None,
                    source_reply_ids=[r1], model="m", note="нет общего приёма", lint_flags=[])
    assert task_id not in {c["task_id"] for c in await svc.candidates(db, limit=500)}

    r2 = await _reply(db, task_id=task_id, sid=sid, tid=tid,
                      body="Проверь себя: самый правый разряд весит единицу, а не двойку.")
    cands = {c["task_id"]: c for c in await svc.candidates(db, limit=500)}
    assert cands[task_id]["new_reply_ids"] == [r2]


# ── (б) генерация ──────────────────────────────────────────────────────────


async def test_generate_builds_prompt_and_returns_hint(db, fake_llm):
    sid, _ = await _user(db)
    tid, _ = await _user(db, "teacher")
    task_id = await _task(db)
    rid = await _reply(db, task_id=task_id, sid=sid, tid=tid,
                       body="Под каждой цифрой подпиши степень двойки, начиная с нулевой справа.")
    res = await svc.generate(db, task_id)
    assert (res.skip, res.text, res.lint_flags, res.source_reply_ids) == (False, _GOOD, [], [rid])
    assert res.accepted_answers == ["1099"]
    user_msg = fake_llm["calls"][0][1].content
    assert "10001001011" in user_msg and _OLD_HINT in user_msg and "нулевой справа" in user_msg


async def test_generate_skip(db, fake_llm):
    sid, _ = await _user(db)
    tid, _ = await _user(db, "teacher")
    task_id = await _task(db)
    await _reply(db, task_id=task_id, sid=sid, tid=tid,
                 body="x = 1051\nimport math\nprint(round(math.sqrt(x), 2)) — вот так правильно")
    fake_llm["text"] = '```json\n{"skip": true, "hint": null, "reason": "только готовый код"}\n```'
    res = await svc.generate(db, task_id)
    assert (res.skip, res.text, res.reason) == (True, None, "только готовый код")


@pytest.mark.parametrize(
    "hint, answers, flags",
    [
        (_GOOD, ["1099"], []),
        ("Ответ получится 1099, проверь себя.", ["1099"], ["answer-in-hint"]),
        ("Подели на 5 и округли.", ["5"], ["answer-in-hint"]),
        ("Округли до 1.5 знака.", ["5"], []),
        ("Сделай так:\nres = round(kor, 2)", [], ["code-in-hint"]),
        ("Используй print(res) в конце.", [], ["code-in-hint"]),
        ("Выведи результат функцией print().", [], []),
        ("Формула =СУММ(A1:F1) тебе поможет.", [], ["code-in-hint"]),
    ],
)
async def test_local_lint(hint, answers, flags):
    assert svc.local_lint(hint, answers) == flags


# ── (в) запись ─────────────────────────────────────────────────────────────


async def test_store_refuses_draft_with_lint_flags(db):
    task_id = await _task(db)
    with pytest.raises(svc.HintDraftError, match="линтер"):
        await svc.store(db, task_id=task_id, status="draft", text_="Ответ 1099",
                        source_reply_ids=[1], model="m", note=None, lint_flags=["answer-in-hint"])
    row = await svc.store(db, task_id=task_id, status="blocked", text_="Ответ 1099",
                          source_reply_ids=[1], model="m", note="утечка", lint_flags=["answer-in-hint"])
    assert row.status == "blocked"


# ── (г) вычитка через API ─────────────────────────────────────────────────


async def _draft_via_api(client, task_id: int, reply_id: int) -> int:
    resp = await client.post(
        f"/api/v1/tasks/{task_id}/hint-drafts", headers=_key(),
        json={"status": "draft", "text": _GOOD, "source_reply_ids": [reply_id], "model": "fake"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_approve_appends_to_hints_text(db, client):
    sid, _ = await _user(db)
    tid, _ = await _user(db, "teacher")
    _, mtoken = await _user(db, "methodist")
    task_id = await _task(db)
    rid = await _reply(db, task_id=task_id, sid=sid, tid=tid,
                       body="Под каждой цифрой подпиши степень двойки, начиная с нулевой справа.")
    draft_id = await _draft_via_api(client, task_id, rid)

    queue = (await client.get("/api/v1/tasks/hint-drafts/queue?limit=100", headers=_bearer(mtoken))).json()
    card = next(i for i in queue["items"] if i["id"] == draft_id)
    assert card["existing_hints"] == [_OLD_HINT] and card["accepted_answers"] == ["1099"]
    assert card["sources"][0]["reply_id"] == rid

    edited = _GOOD + " Начни с правого края."
    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review", headers=_bearer(mtoken),
        json={"action": "approve", "text": edited},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hints_text"] == [_OLD_HINT, edited]

    content = await db.scalar(text("SELECT task_content FROM tasks WHERE id=:t"), {"t": task_id})
    assert content["hints_text"] == [_OLD_HINT, edited] and content["has_hints"] is True
    prov = await db.scalar(text("SELECT content_provenance FROM tasks WHERE id=:t"), {"t": task_id})
    assert prov is None

    again = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review", headers=_bearer(mtoken), json={"action": "reject"}
    )
    assert again.status_code == 409


async def test_service_key_cannot_review(db, client):
    task_id = await _task(db)
    draft_id = await _draft_via_api(client, task_id, 1)
    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review", headers=_key(), json={"action": "approve"}
    )
    assert resp.status_code == 403


async def test_reject_leaves_hints_untouched(db, client):
    _, mtoken = await _user(db, "methodist")
    task_id = await _task(db, hints=[])
    draft_id = await _draft_via_api(client, task_id, 1)
    resp = await client.post(
        f"/api/v1/tasks/hint-drafts/{draft_id}/review", headers=_bearer(mtoken), json={"action": "reject"}
    )
    assert resp.status_code == 200 and resp.json()["draft"]["status"] == "rejected"
    content = await db.scalar(text("SELECT task_content FROM tasks WHERE id=:t"), {"t": task_id})
    assert content["hints_text"] == []


async def test_queue_requires_role(db, client):
    _, token = await _user(db)
    resp = await client.get("/api/v1/tasks/hint-drafts/queue", headers=_bearer(token))
    assert resp.status_code == 403
