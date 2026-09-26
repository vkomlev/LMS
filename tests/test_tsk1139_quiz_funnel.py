"""tsk-1139: квиз-воронка сайта через API — на копии реального контента.

Проверяем на настоящей БД:
- путь: развилка, параметры ссылки (пропуск и предзаполнение), чужой вопрос — 404;
- метки первого касания пишутся один раз, чужие ключи отбрасываются;
- итог: видимая часть, кнопки; у ветки «родитель» регистрация закрыта;
- мини-проверка возвращает разбор;
- claim: сессия привязана, заявка с учеником, веткой, итогом и метками,
  самозапись на бесплатный курс итога, повтор не плодит заявок; отказы;
- бот: старт по токену, напоминания, отписка чужим tg, запись на пробное;
- замеры по веткам.
"""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.api.v1 import guest_funnel as funnel_module
from app.api.v1 import learning_guest as learning_guest_module
from app.api.v1 import me_quiz_funnel as claim_module
from app.models.users import Users
from app.services import quiz_funnel_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from scripts.tsk1139_import_funnel_quiz import question_rows

_TAG = "tsk1139"
_QUIZ_UID = "pytest:tsk1139-funnel"
_FREE_UID = "pytest:tsk1139-free"
_BASE = f"/api/v1/learning/guest/funnel/{_QUIZ_UID}"

_SPEC = json.loads(
    (Path(__file__).parent / "fixtures" / "tsk1139_quiz_razvilka.json").read_text(encoding="utf-8")
)
_STATE: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _funnel_on(monkeypatch):
    """Воронка включена, лимиты частоты не мешают, ник бота задан."""

    async def _never(*_args, **_kwargs) -> bool:
        return False

    for module in (funnel_module, learning_guest_module, claim_module):
        monkeypatch.setattr(module, "is_rate_limited", _never)
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_enabled", True)
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_bot_username", "test_bot")


@pytest_asyncio.fixture(autouse=True)
async def _seed(db):
    """Квиз из контента; курсы итогов взрослой ветки → один бесплатный курс."""
    spec = copy.deepcopy(_SPEC)
    spec["quiz_uid"] = _QUIZ_UID
    for outcome in spec["outcomes"]:
        if outcome["branch"] == "adult":
            outcome["target_course_uid"] = _FREE_UID

    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    quiz_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid, is_public_demo) "
                "VALUES (:t, 'self_guided', :u, TRUE) RETURNING id"
            ),
            {"t": f"{_TAG}-квиз", "u": _QUIZ_UID},
        )
    ).scalar_one()
    free_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid, description) "
                "VALUES (:t, 'self_guided', :u, 'Бесплатный курс') RETURNING id"
            ),
            {"t": f"{_TAG}-бесплатный", "u": _FREE_UID},
        )
    ).scalar_one()
    await db.execute(
        text("INSERT INTO course_pricing (course_id, sale_status) VALUES (:c, 'free')"),
        {"c": free_id},
    )
    for order, row in enumerate(question_rows(spec), start=1):
        mode = "multi" if row["content"]["type"] == "MC_Qw" else "single"
        await db.execute(
            text(
                "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                "difficulty_id, solution_rules, order_position) VALUES (:uid, 1, "
                "CAST(:c AS jsonb), :course, :d, CAST(:r AS jsonb), :o)"
            ),
            {
                "uid": f"{_QUIZ_UID}:{row['code']}",
                "c": json.dumps(row["content"], ensure_ascii=False),
                "course": quiz_id,
                "d": difficulty_id,
                "r": json.dumps({"max_score": 1, "quiz": {"scales": ["route"], "mode": mode}}),
                "o": order,
            },
        )
    await db.execute(
        text("INSERT INTO quiz_funnel_spec (course_id, spec) VALUES (:c, CAST(:s AS jsonb))"),
        {"c": quiz_id, "s": json.dumps(spec, ensure_ascii=False)},
    )
    _STATE.update(quiz_id=quiz_id, free_id=free_id)
    await db.commit()
    yield
    await db.execute(text("DELETE FROM leads WHERE quiz_course_id = :c"), {"c": quiz_id})
    await db.execute(
        text("DELETE FROM courses WHERE course_uid IN (:a, :b)"), {"a": _QUIZ_UID, "b": _FREE_UID}
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


async def _begin(client, **params) -> None:
    assert (await client.post("/api/v1/learning/guest/session")).status_code == 201
    resp = await client.post(f"{_BASE}/start", json=params)
    assert resp.status_code == 204, resp.text


async def _answer_until(client, stop_code: str | None = None, pick: int = 0) -> dict:
    """Отвечать вариантом ``pick`` (0 — первый, -1 — последний) до конца пути
    (или до вопроса ``stop_code``)."""
    state = (await client.get(_BASE)).json()
    for _ in range(40):
        if state["is_complete"]:
            return state
        current = state["questions"][-1]
        if current["code"] == stop_code:
            return state
        resp = await client.post(
            f"{_BASE}/answer",
            json={"code": current["code"], "selected_option_ids": [current["options"][pick]["id"]]},
        )
        assert resp.status_code == 200, resp.text
        state = resp.json()
    raise AssertionError("путь не закончился")


async def _claim(client, headers):
    return await client.post(
        "/api/v1/me/quiz-funnel/claim", json={"quiz_uid": _QUIZ_UID}, headers=headers
    )


# ─── путь и метки ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_starts_with_who_question(client):
    assert (await client.post("/api/v1/learning/guest/session")).status_code == 201
    body = (await client.get(_BASE)).json()
    assert [q["code"] for q in body["questions"]] == ["Q0"]
    assert body["is_complete"] is False


@pytest.mark.asyncio
async def test_funnel_off_is_404(client, monkeypatch):
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_enabled", False)
    assert (await client.get(_BASE)).status_code == 404


@pytest.mark.asyncio
async def test_start_params_skip_and_prefill_and_attribution(client, db):
    await _begin(
        client, branch="adult", dir="qa",
        attribution={"utm_source": "yandex", "evil": "x", "page": "/testirovshik"},
    )
    body = (await client.get(_BASE)).json()
    codes = [q["code"] for q in body["questions"]]
    assert "Q0" not in codes and "A1" not in codes
    assert body["branch"] == "adult"

    # Повторный старт с другими метками источник не подменяет.
    await client.post(f"{_BASE}/start", json={"attribution": {"utm_source": "internal"}})
    gs = client.cookies.get("guest_session")
    stored = (
        await db.execute(
            text("SELECT attribution FROM guest_session WHERE id = CAST(:g AS uuid)"), {"g": gs}
        )
    ).scalar_one()
    assert stored == {
        "utm_source": "yandex", "page": "/testirovshik", "branch": "adult", "dir": "qa",
        "entry_uid": _QUIZ_UID,
    }


@pytest.mark.asyncio
async def test_answer_off_path_404_and_bad_option_400(client):
    await _begin(client)
    off = await client.post(f"{_BASE}/answer", json={"code": "P3", "selected_option_ids": ["x"]})
    assert off.status_code == 404
    bad = await client.post(f"{_BASE}/answer", json={"code": "Q0", "selected_option_ids": ["nope"]})
    assert bad.status_code == 400
    two = await client.post(
        f"{_BASE}/answer", json={"code": "Q0", "selected_option_ids": ["a_parent", "a_teen"]}
    )
    assert two.status_code == 400


@pytest.mark.asyncio
async def test_check_question_returns_feedback(client):
    await _begin(client, branch="adult")
    await _answer_until(client, stop_code="A4")
    resp = await client.post(f"{_BASE}/answer", json={"code": "A4", "selected_option_ids": ["a_xl_15"]})
    assert resp.status_code == 200
    assert resp.json()["feedback"]


# ─── итог ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adult_result_visible_part_and_buttons(client):
    await _begin(client, branch="adult")
    await _answer_until(client)
    body = (await client.get(f"{_BASE}/result")).json()
    assert body["is_complete"] is True
    assert body["branch"] == "adult"
    assert body["visible"]
    assert body["registration_enabled"] is True
    # Вход в кабинет у итогов взрослой ветки — «регистрация» или «демо».
    assert {"register", "demo"} & {b["kind"] for b in body["buttons"]}
    bot = next(b for b in body["buttons"] if b["kind"] == "telegram_bot")
    assert bot["url"].startswith("https://t.me/test_bot?start=q_")


@pytest.mark.asyncio
async def test_parent_result_has_no_registration(client):
    """Родитель ребёнка (не ЕГЭ): до текста согласия регистрации нет."""
    await _begin(client, branch="parent")
    await _answer_until(client, pick=-1)
    body = (await client.get(f"{_BASE}/result")).json()
    assert body["branch"] == "parent"
    assert body["registration_enabled"] is False
    assert not {b["kind"] for b in body["buttons"]} & {"register", "demo"}


@pytest.mark.asyncio
async def test_result_incomplete(client):
    await _begin(client)
    assert (await client.get(f"{_BASE}/result")).json()["is_complete"] is False


@pytest.mark.asyncio
async def test_parent_of_graduate_goes_to_ege_with_registration(client):
    """Цель «ЕГЭ» у родителя уводит в ветку ЕГЭ голосом родителя; выпускнику
    16–17 лет согласие родителя на данные младше 14 не нужно — вход открыт."""
    await _begin(client, branch="parent")
    state = await _answer_until(client)
    assert state["branch"] == "ege" and state["role"] == "parent"
    body = (await client.get(f"{_BASE}/result")).json()
    assert body["registration_enabled"] is True


@pytest.mark.asyncio
async def test_trial_lead_from_result_carries_branch_and_outcome(client, db):
    await _begin(client, branch="parent", attribution={"utm_source": "vk"})
    await _answer_until(client, pick=-1)
    early = await client.post(f"{_BASE}/lead", json={"contact": "+79000000000"})
    assert early.status_code == 201
    lead = (
        await db.execute(text("SELECT contact, attribution FROM leads WHERE id = :i"),
                         {"i": early.json()["lead_id"]})
    ).one()
    assert lead.contact == "+79000000000"
    assert lead.attribution["branch"] == "parent"
    assert lead.attribution["utm_source"] == "vk"
    assert lead.attribution["outcome"]


@pytest.mark.asyncio
async def test_waitlist_lead_marked_as_waitlist(client, db):
    await _begin(client, branch="parent")
    await _answer_until(client, pick=-1)
    resp = await client.post(f"{_BASE}/lead", json={"contact": "@parent", "kind": "waitlist"})
    assert resp.status_code == 201
    attribution = (
        await db.execute(text("SELECT attribution FROM leads WHERE id = :i"),
                         {"i": resp.json()["lead_id"]})
    ).scalar_one()
    assert attribution.get("waitlist") is True and "trial_requested" not in attribution


@pytest.mark.asyncio
async def test_trial_lead_before_finish_409(client):
    await _begin(client)
    resp = await client.post(f"{_BASE}/lead", json={"contact": "+79000000000"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_registration_closed_by_condition_turns_into_share(client, db):
    """`registration_closed_if` (подросток 11–13): вход закрыт, вместо кнопок
    регистрации — одна «Отправить ссылку родителям»; claim отказывает."""
    await db.execute(
        text(
            "UPDATE quiz_funnel_spec SET spec = spec || "
            "CAST(:p AS jsonb) WHERE course_id = :c"
        ),
        {"p": json.dumps({"registration_closed_if": {"role": ["teen"]}}), "c": _STATE["quiz_id"]},
    )
    await db.commit()
    await _begin(client, branch="teen")
    await _answer_until(client)
    body = (await client.get(f"{_BASE}/result")).json()
    assert body["registration_enabled"] is False
    kinds = [b["kind"] for b in body["buttons"]]
    assert "register" not in kinds and "demo" not in kinds
    assert kinds.count("share_parent_link") <= 1
    _, headers = await _student(db)
    assert (await _claim(client, headers)).status_code == 409


# ─── claim ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_links_lead_and_enrolls(client, db):
    await _begin(client, branch="adult", attribution={"utm_campaign": "autumn"})
    await _answer_until(client)
    user_id, headers = await _student(db)

    resp = await _claim(client, headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enrolled"] is True
    assert body["course_id"] == _STATE["free_id"]
    assert body["breakdown"]["visible"]
    assert all(b["kind"] not in ("register", "demo") for b in body["buttons"])
    trial = [b for b in body["buttons"] if b["kind"] == "lead_trial"]
    assert all(b["url"] and b["url"].startswith("https://t.me/") for b in trial)

    lead = (
        await db.execute(
            text(
                "SELECT l.linked_student_id, l.attribution, s.code FROM leads l "
                "JOIN lead_source s ON s.id = l.source_id WHERE l.quiz_course_id = :c"
            ),
            {"c": _STATE["quiz_id"]},
        )
    ).one()
    assert lead.linked_student_id == user_id
    assert lead.code == "quiz"
    assert lead.attribution["branch"] == "adult"
    assert lead.attribution["outcome"] == body["outcome_code"]
    assert lead.attribution["utm_campaign"] == "autumn"

    assert (await _claim(client, headers)).status_code == 200
    count = (
        await db.execute(
            text("SELECT count(*) FROM leads WHERE quiz_course_id = :c"), {"c": _STATE["quiz_id"]}
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_claim_parent_branch_409(client, db):
    await _begin(client, branch="parent")
    await _answer_until(client, pick=-1)
    _, headers = await _student(db)
    assert (await _claim(client, headers)).status_code == 409


@pytest.mark.asyncio
async def test_claim_incomplete_409(client, db):
    await _begin(client)
    _, headers = await _student(db)
    assert (await _claim(client, headers)).status_code == 409


@pytest.mark.asyncio
async def test_claim_funnel_off_404(client, db, monkeypatch):
    await _begin(client, branch="adult")
    await _answer_until(client)
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_enabled", False)
    _, headers = await _student(db)
    assert (await _claim(client, headers)).status_code == 404


# ─── бот ─────────────────────────────────────────────────────────────────────

def _api_headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(quiz_funnel_service._settings.valid_api_keys))}


async def _bot_token(client) -> str:
    await _begin(client, branch="adult")
    await _answer_until(client)
    body = (await client.get(f"{_BASE}/result")).json()
    return body["bot_start_url"].split("start=q_", 1)[1]


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
    assert started["pdf_url"] == "/media/funnel/04-vzroslyj-karta-vhoda.pdf"
    bot_lead_id = started["bot_lead_id"]

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
async def test_bot_reminders_off_by_default(client, monkeypatch):
    monkeypatch.setattr(quiz_funnel_service._settings, "quiz_funnel_reminders_enabled", False)
    due = (await client.get("/api/v1/integrations/quiz-funnel/bot/due", headers=_api_headers())).json()
    assert due == []


# ─── замеры ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_site_funnel_counts_steps(client, db):
    await _begin(client, branch="adult")
    await _answer_until(client)
    _, headers = await _student(db)
    assert (await _claim(client, headers)).status_code == 200

    rows = await quiz_funnel_service.get_site_funnel(db, _QUIZ_UID)
    adult = next(r for r in rows if r["branch"] == "adult")
    assert (adult["opened"], adult["started"], adult["completed"], adult["registered"]) == (1, 1, 1, 1)
    assert adult["paid"] == 0
    assert await quiz_funnel_service.get_site_funnel(db, "no-such-quiz") is None
