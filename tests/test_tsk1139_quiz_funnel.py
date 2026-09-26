"""tsk-1139: воронка сайта — развилка квиза, метки, регистрация из итога.

Проверяем на настоящей БД:
- входной квиз отдаёт ветки только при включённой воронке;
- метки первого касания пишутся один раз, чужие ключи отбрасываются;
- итог ветки: гостю часть (без шкал и описания), блок воронки, ссылка в бот;
- claim: сессия привязана, заявка с учеником и метками, самозапись на
  бесплатный курс, повтор не плодит заявок; закрытая ветка, недопройденный
  квиз и выключенная воронка — отказ.
"""
from __future__ import annotations

import json
import random

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.api.v1 import guest_quiz as guest_quiz_module
from app.api.v1 import learning_guest as learning_guest_module
from app.api.v1 import me_quiz_funnel as claim_module
from app.models.users import Users
from app.services import quiz_funnel_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session

_TAG = "tsk1139"
_ENTRY_UID = "pytest:tsk1139-entry"
_BRANCH_UID = "pytest:tsk1139-adult"
_TARGET_UID = "pytest:tsk1139-free-course"

_STATE: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _funnel_on(monkeypatch):
    """Воронка включена, лимиты частоты не мешают, ник бота задан."""

    async def _never(*_args, **_kwargs) -> bool:
        return False

    for module in (guest_quiz_module, learning_guest_module, claim_module):
        monkeypatch.setattr(module, "is_rate_limited", _never)
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_enabled", True)
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_bot_username", "test_bot")


async def _insert_course(db, uid: str, title: str, public: bool) -> int:
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO courses (title, access_level, course_uid, is_public_demo, "
                    "description) VALUES (:t, 'self_guided', :u, :p, 'Полное описание') "
                    "RETURNING id"
                ),
                {"t": f"{_TAG}-{title}", "u": uid, "p": public},
            )
        ).scalar_one()
    )


async def _insert_question(db, course_id: int, order: int, options: list[dict]) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    content = {"type": "SC_Qw", "stem": f"Вопрос {order}", "scales": ["x"], "options": options}
    rules = {"max_score": 1, "quiz": {"scales": ["x"], "mode": "single"}}
    return int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                    "difficulty_id, solution_rules, order_position) VALUES (:uid, 1, "
                    "CAST(:c AS jsonb), :course, :d, CAST(:r AS jsonb), :o) RETURNING id"
                ),
                {
                    "uid": f"pytest:{_TAG}:{course_id}:{order}",
                    "c": json.dumps(content, ensure_ascii=False),
                    "course": course_id,
                    "d": difficulty_id,
                    "r": json.dumps(rules),
                    "o": order,
                },
            )
        ).scalar_one()
    )


@pytest_asyncio.fixture(autouse=True)
async def _seed(db):
    """Вход с одним вопросом → ветка adult (1 вопрос) → бесплатный курс."""
    entry_id = await _insert_course(db, _ENTRY_UID, "вход", True)
    branch_id = await _insert_course(db, _BRANCH_UID, "взрослый", True)
    target_id = await _insert_course(db, _TARGET_UID, "бесплатный", False)
    await db.execute(
        text("INSERT INTO course_pricing (course_id, sale_status) VALUES (:c, 'free')"),
        {"c": target_id},
    )
    _STATE["entry_q"] = await _insert_question(
        db, entry_id, 1,
        [
            {"id": "A", "text": "Для ребёнка", "scores": {"x": 0}},
            {"id": "D", "text": "Для себя, взрослый", "scores": {"x": 1}},
        ],
    )
    _STATE["branch_q"] = await _insert_question(
        db, branch_id, 1, [{"id": "A", "text": "Да", "scores": {"x": 1}}, {"id": "B", "text": "Нет", "scores": {"x": 0}}]
    )
    await db.execute(
        text(
            "INSERT INTO assignment_rule (code, title, course_id, trigger_event, condition, "
            "target_course_uid, is_active) VALUES (:code, 't', :c, 'quiz_scale', "
            "CAST(:cond AS jsonb), :t, true)"
        ),
        {
            "code": f"pytest-{_TAG}",
            "c": branch_id,
            "cond": json.dumps({"scale": "x", "mode": "argmax"}),
            "t": _TARGET_UID,
        },
    )
    await db.execute(
        text(
            "INSERT INTO quiz_funnel_branch (quiz_course_id, entry_course_id, branch_code, "
            "entry_option_id, pdf_url) VALUES (:b, :e, 'adult', 'D', '/media/funnel/adult.pdf')"
        ),
        {"b": branch_id, "e": entry_id},
    )
    _STATE.update(entry_id=entry_id, branch_id=branch_id, target_id=target_id)
    await db.commit()
    yield
    await db.execute(text("DELETE FROM leads WHERE quiz_course_id = :c"), {"c": branch_id})
    await db.execute(text("DELETE FROM assignment_rule WHERE course_id = :c"), {"c": branch_id})
    await db.execute(
        text("DELETE FROM courses WHERE course_uid IN (:a, :b, :c)"),
        {"a": _ENTRY_UID, "b": _BRANCH_UID, "c": _TARGET_UID},
    )
    for tbl in ("user_session", "identity_link"):
        await db.execute(
            text(f"DELETE FROM {tbl} WHERE user_id IN (SELECT id FROM users WHERE email LIKE :p)"),
            {"p": f"{_TAG}-%"},
        )
    await db.commit()


async def _student(db) -> tuple[int, dict[str, str]]:
    email = f"{_TAG}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name="Тестов Ученик", tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, {"Authorization": f"Bearer {token}"}


async def _pass_branch(client) -> None:
    assert (await client.post("/api/v1/learning/guest/session")).status_code == 201
    resp = await client.post(
        "/api/v1/learning/guest/quiz/answers",
        json={"task_id": _STATE["branch_q"], "selected_option_ids": ["A"]},
    )
    assert resp.status_code == 201


# ─── развилка и метки ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_quiz_lists_branches(client):
    body = (await client.get(f"/api/v1/learning/guest/quiz/{_ENTRY_UID}")).json()
    assert body["branches"] == [
        {"branch_code": "adult", "option_id": "D", "quiz_uid": _BRANCH_UID}
    ]


@pytest.mark.asyncio
async def test_entry_quiz_without_funnel_has_no_branches(client, monkeypatch):
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_enabled", False)
    body = (await client.get(f"/api/v1/learning/guest/quiz/{_ENTRY_UID}")).json()
    assert body["branches"] == []


@pytest.mark.asyncio
async def test_attribution_first_touch_and_whitelist(client, db):
    assert (await client.post("/api/v1/learning/guest/session")).status_code == 201
    first = await client.post(
        f"/api/v1/learning/guest/quiz/{_ENTRY_UID}/attribution",
        json={"attribution": {"utm_source": "yandex", "evil": "x", "page": "/ege"}},
    )
    assert first.status_code == 204
    # Переход в ветку — наш же переход, источник не перетирается.
    await client.post(
        f"/api/v1/learning/guest/quiz/{_BRANCH_UID}/attribution",
        json={"attribution": {"utm_source": "internal"}},
    )
    gs = client.cookies.get("guest_session")
    stored = (
        await db.execute(
            text("SELECT attribution FROM guest_session WHERE id = CAST(:g AS uuid)"), {"g": gs}
        )
    ).scalar_one()
    assert stored == {"utm_source": "yandex", "page": "/ege", "entry_uid": _ENTRY_UID}


# ─── итог ветки ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_branch_result_is_partial_with_bot_link(client):
    await _pass_branch(client)
    body = (await client.get(f"/api/v1/learning/guest/quiz/{_BRANCH_UID}/result")).json()
    assert body["recommendation"]["course_uid"] == _TARGET_UID
    assert body["recommendation"]["description"] is None
    assert body["scales"] == {}
    assert body["funnel"]["branch_code"] == "adult"
    assert body["funnel"]["registration_enabled"] is True
    assert body["funnel"]["bot_start_url"].startswith("https://t.me/test_bot?start=q_")
    # Повторный запрос — тот же токен, а не новый гость бота.
    again = (await client.get(f"/api/v1/learning/guest/quiz/{_BRANCH_UID}/result")).json()
    assert again["funnel"]["bot_start_url"] == body["funnel"]["bot_start_url"]


# ─── claim ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_links_lead_and_enrolls(client, db):
    await _pass_branch(client)
    await client.post(
        f"/api/v1/learning/guest/quiz/{_ENTRY_UID}/attribution",
        json={"attribution": {"utm_campaign": "autumn"}},
    )
    user_id, headers = await _student(db)

    resp = await client.post(
        "/api/v1/me/quiz-funnel/claim", json={"quiz_uid": _BRANCH_UID}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enrolled"] is True
    assert body["course_id"] == _STATE["target_id"]
    assert body["scales"] == {"x": 1}
    assert body["recommendation"]["description"] == "Полное описание"

    lead = (
        await db.execute(
            text(
                "SELECT l.linked_student_id, l.attribution, s.code FROM leads l "
                "JOIN lead_source s ON s.id = l.source_id WHERE l.quiz_course_id = :c"
            ),
            {"c": _STATE["branch_id"]},
        )
    ).one()
    assert lead.linked_student_id == user_id
    assert lead.code == "quiz"
    assert lead.attribution["branch"] == "adult"
    assert lead.attribution["utm_campaign"] == "autumn"

    again = await client.post(
        "/api/v1/me/quiz-funnel/claim", json={"quiz_uid": _BRANCH_UID}, headers=headers
    )
    assert again.status_code == 200
    count = (
        await db.execute(
            text("SELECT count(*) FROM leads WHERE quiz_course_id = :c"),
            {"c": _STATE["branch_id"]},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_claim_closed_branch_409(client, db):
    await db.execute(
        text("UPDATE quiz_funnel_branch SET registration_enabled = false WHERE quiz_course_id = :b"),
        {"b": _STATE["branch_id"]},
    )
    await db.commit()
    await _pass_branch(client)
    _, headers = await _student(db)
    resp = await client.post(
        "/api/v1/me/quiz-funnel/claim", json={"quiz_uid": _BRANCH_UID}, headers=headers
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_claim_incomplete_409(client, db):
    assert (await client.post("/api/v1/learning/guest/session")).status_code == 201
    _, headers = await _student(db)
    resp = await client.post(
        "/api/v1/me/quiz-funnel/claim", json={"quiz_uid": _BRANCH_UID}, headers=headers
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_claim_funnel_off_404(client, db, monkeypatch):
    await _pass_branch(client)
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_enabled", False)
    _, headers = await _student(db)
    resp = await client.post(
        "/api/v1/me/quiz-funnel/claim", json={"quiz_uid": _BRANCH_UID}, headers=headers
    )
    assert resp.status_code == 404


# ─── бот: гость ветки ────────────────────────────────────────────────────────

def _api_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(quiz_funnel_service._settings.valid_api_keys))}


async def _bot_token(client) -> str:
    await _pass_branch(client)
    body = (await client.get(f"/api/v1/learning/guest/quiz/{_BRANCH_UID}/result")).json()
    return body["funnel"]["bot_start_url"].split("start=q_", 1)[1]


@pytest.mark.asyncio
async def test_bot_start_trial_and_reminders(client, db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_reminders_enabled", True)
    token = await _bot_token(client)
    tg_id = random.randint(10**8, 10**9)

    start = await client.post(
        "/api/v1/integrations/quiz-funnel/bot/start",
        json={"token": token, "tg_id": tg_id, "tg_username": "guest1"},
        headers=_api_headers(),
    )
    assert start.status_code == 200, start.text
    started = start.json()
    assert started["branch_code"] == "adult"
    assert started["pdf_url"] == "/media/funnel/adult.pdf"
    bot_lead_id = started["bot_lead_id"]

    # Первое напоминание назначено на +1 день; сдвигаем в прошлое — оно «пора».
    await db.execute(
        text("UPDATE quiz_funnel_bot_lead SET next_reminder_at = :t WHERE id = :i"),
        {"t": datetime.now(timezone.utc) - timedelta(minutes=1), "i": bot_lead_id},
    )
    await db.commit()
    due = (await client.get("/api/v1/integrations/quiz-funnel/bot/due", headers=_api_headers())).json()
    assert any(d["bot_lead_id"] == bot_lead_id and d["step"] == 0 for d in due)

    sent = await client.post(
        f"/api/v1/integrations/quiz-funnel/bot/{bot_lead_id}/sent",
        json={"tg_id": tg_id, "step": 0},
        headers=_api_headers(),
    )
    assert sent.status_code == 204
    step = (
        await db.execute(
            text("SELECT reminder_step FROM quiz_funnel_bot_lead WHERE id = :i"), {"i": bot_lead_id}
        )
    ).scalar_one()
    assert step == 1

    # Чужой tg не может записать гостя на пробное.
    alien = await client.post(
        f"/api/v1/integrations/quiz-funnel/bot/{bot_lead_id}/trial",
        json={"tg_id": tg_id + 1},
        headers=_api_headers(),
    )
    assert alien.status_code == 404

    trial = await client.post(
        f"/api/v1/integrations/quiz-funnel/bot/{bot_lead_id}/trial",
        json={"tg_id": tg_id},
        headers=_api_headers(),
    )
    assert trial.status_code == 200
    lead = (
        await db.execute(
            text("SELECT contact, attribution FROM leads WHERE id = :i"),
            {"i": trial.json()["lead_id"]},
        )
    ).one()
    assert lead.contact == "@guest1"
    assert lead.attribution["trial_requested"] is True
    # После записи на пробное напоминаний нет.
    due = (await client.get("/api/v1/integrations/quiz-funnel/bot/due", headers=_api_headers())).json()
    assert all(d["bot_lead_id"] != bot_lead_id for d in due)


@pytest.mark.asyncio
async def test_bot_unknown_token_404(client):
    resp = await client.post(
        "/api/v1/integrations/quiz-funnel/bot/start",
        json={"token": "nonexistent-token", "tg_id": 1},
        headers=_api_headers(),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bot_reminders_off_by_default(client, db, monkeypatch):
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_reminders_enabled", False)
    due = (await client.get("/api/v1/integrations/quiz-funnel/bot/due", headers=_api_headers())).json()
    assert due == []


# ─── замеры ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_site_funnel_counts_steps(client, db):
    await _pass_branch(client)
    _, headers = await _student(db)
    assert (
        await client.post(
            "/api/v1/me/quiz-funnel/claim", json={"quiz_uid": _BRANCH_UID}, headers=headers
        )
    ).status_code == 200

    rows = await quiz_funnel_service.get_site_funnel(db, _ENTRY_UID)
    assert len(rows) == 1
    row = rows[0]
    assert row["branch_code"] == "adult"
    assert (row["started"], row["completed"], row["registered"]) == (1, 1, 1)
    assert row["paid"] == 0
    assert await quiz_funnel_service.get_site_funnel(db, "no-such-entry") is None
