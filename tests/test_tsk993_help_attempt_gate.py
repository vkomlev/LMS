"""tsk-993 (П4): помощь по заданию — после своей попытки или описания «где застрял».

Решение оператора Р1: заявка преподавателю и новый разговор с ИИ-наставником
открываются, если у ученика есть ≥1 своя сдача по заданию ИЛИ заметка «где
застрял» не короче 20 символов после обрезки пробелов. Ручной зачёт
преподавателя (`manual_teacher`) и сдачи в отменённой попытке — не попытка
ученика.

Главное свойство выката проверяется отдельно: при `HELP_REQUIRES_ATTEMPT=false`
(по умолчанию) поведение прежнее — ни отказа, ни `attempt_id`.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.models.users import Users
from app.services import entitlements_service as ent
from app.services import help_attempt_gate
from app.services.ai_tutor import session_service
from app.services.ai_tutor.prompt import (
    STUDENT_DATA_CLOSE,
    STUDENT_DATA_OPEN,
    build_opening_user_message,
)
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.learning_engine_service import LearningEngineService

pytestmark = pytest.mark.asyncio

_TAG = "tsk993"
_NOTE_OK = "Не понимаю, как перевести число в двоичную систему"
_NOTE_SHORT = "   не понимаю ничего     "  # 15 символов после обрезки


# ───────────────────────────── хелперы ──────────────────────────────────────


async def _user(db, name: str) -> tuple[int, str]:
    """Пользователь с живой сессией (bearer-токен)."""
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG} {name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _course(db) -> int:
    res = await db.execute(
        text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
        {"t": f"{_TAG}-course-{random.randint(10**8, 10**10)}"},
    )
    return res.scalar_one()


async def _task(db, course_id: int) -> int:
    difficulty_id = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    res = await db.execute(
        text(
            "INSERT INTO tasks (external_uid, max_score, task_content, solution_rules, "
            "course_id, difficulty_id, is_active) "
            "VALUES (:e, 10, CAST(:c AS jsonb), CAST(:r AS jsonb), :cid, :d, true) RETURNING id"
        ),
        {
            "e": f"{_TAG}-{random.randint(10**8, 10**10)}",
            "c": json.dumps({"type": "SA", "stem": "Переведи 5 в двоичную систему"}),
            "r": json.dumps({"max_score": 10, "correct_answer": "101"}),
            "cid": course_id,
            "d": difficulty_id,
        },
    )
    return res.scalar_one()


async def _submission(
    db,
    *,
    user_id: int,
    task_id: int,
    course_id: int,
    source_system: str = "spw_web",
    cancelled: bool = False,
) -> int:
    """Сдача по заданию в своей попытке. Возвращает ID попытки."""
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system, "
                "  cancelled_at) "
                "VALUES (:u, :c, :c, :src, CASE WHEN :cancel THEN now() ELSE NULL END) "
                "RETURNING id"
            ),
            {"u": user_id, "c": course_id, "src": source_system, "cancel": cancelled},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, source_system, answer_json) "
            "VALUES (:u, :t, :a, 0, 10, false, now(), now(), 0, :src, CAST(:ans AS jsonb))"
        ),
        {
            "u": user_id, "t": task_id, "a": attempt_id, "src": source_system,
            "ans": json.dumps({"type": "SA", "response": {"value": "110"}}),
        },
    )
    await db.commit()
    return int(attempt_id)


async def _link_teacher(db, student_id: int, teacher_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO student_teacher_links (student_id, teacher_id, linked_at) "
            "VALUES (:s, :t, now())"
        ),
        {"s": student_id, "t": teacher_id},
    )
    await db.commit()


@pytest.fixture
def gate_on(monkeypatch):
    """Рубильник П4 включён, подписной гейт выключен (он проверяется отдельно)."""
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")
    monkeypatch.setattr(help_attempt_gate.settings, "help_requires_attempt", True)


@pytest.fixture
def gate_off(monkeypatch):
    monkeypatch.setattr(ent.settings, "subscription_gate_mode", "off")
    monkeypatch.setattr(help_attempt_gate.settings, "help_requires_attempt", False)


async def _request_help(client, token: str, student_id: int, task_id: int, **extra):
    return await client.post(
        f"/api/v1/learning/tasks/{task_id}/request-help",
        headers=_bearer(token),
        json={"student_id": student_id, "message": "помогите", **extra},
    )


async def _help_row(db, request_id: int):
    return (
        await db.execute(
            text("SELECT attempt_id, stuck_note FROM help_requests WHERE id = :id"),
            {"id": request_id},
        )
    ).fetchone()


# ───────────────── 1. has_own_attempt: четыре случая брифа ───────────────────


async def test_default_is_off(monkeypatch) -> None:
    """По умолчанию гейт выключен — выкат кода поведение не меняет."""
    from app.core.config import Settings

    monkeypatch.delenv("HELP_REQUIRES_ATTEMPT", raising=False)
    assert Settings().help_requires_attempt is False


async def test_has_own_attempt_no_submissions(db) -> None:
    student_id, _ = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)
    engine = LearningEngineService()
    assert await engine.has_own_attempt(db, student_id, task_id, None) is False


async def test_has_own_attempt_with_submission(db) -> None:
    student_id, _ = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)
    attempt_id = await _submission(db, user_id=student_id, task_id=task_id, course_id=course_id)
    engine = LearningEngineService()
    assert await engine.has_own_attempt(db, student_id, task_id, None) is True
    assert await engine.has_own_attempt(db, student_id, task_id, course_id) is True
    assert await engine.last_own_attempt_id(db, student_id, task_id) == attempt_id


async def test_has_own_attempt_only_manual_teacher(db) -> None:
    """Ручной зачёт преподавателя — не попытка ученика."""
    student_id, _ = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)
    await _submission(
        db, user_id=student_id, task_id=task_id, course_id=course_id,
        source_system="manual_teacher",
    )
    engine = LearningEngineService()
    assert await engine.has_own_attempt(db, student_id, task_id, None) is False


async def test_has_own_attempt_cancelled_attempt(db) -> None:
    """Сдача в отменённой попытке не считается."""
    student_id, _ = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)
    await _submission(
        db, user_id=student_id, task_id=task_id, course_id=course_id, cancelled=True,
    )
    engine = LearningEngineService()
    assert await engine.has_own_attempt(db, student_id, task_id, None) is False


async def test_has_own_attempt_respects_root(db) -> None:
    """Заданный корень — только попытки этого корня (как счёт попыток tsk-264)."""
    student_id, _ = await _user(db, "s")
    course_id = await _course(db)
    other_root = await _course(db)
    task_id = await _task(db, course_id)
    await _submission(db, user_id=student_id, task_id=task_id, course_id=course_id)
    engine = LearningEngineService()
    assert await engine.has_own_attempt(db, student_id, task_id, other_root) is False


# ───────────────── 2. request-help: гейт и сохранение заметки ────────────────


async def test_request_help_409_without_attempt_and_note(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))

    resp = await _request_help(client, token, student_id, task_id)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["payload"]["code"] == "attempt_required"
    assert body["detail"] == (
        "Сначала попробуй решить сам — отправь ответ или опиши, где застрял"
    )
    count = (
        await db.execute(
            text("SELECT COUNT(*) FROM help_requests WHERE student_id = :s"), {"s": student_id}
        )
    ).scalar()
    assert count == 0


async def test_request_help_409_with_short_note(db, client, gate_on) -> None:
    """Порог — после обрезки пробелов: 15 символов в обёртке из пробелов мало."""
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))

    resp = await _request_help(client, token, student_id, task_id, stuck_note=_NOTE_SHORT)
    assert resp.status_code == 409, resp.text
    assert resp.json()["payload"]["code"] == "attempt_required"


async def test_request_help_409_with_only_manual_teacher(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)
    await _submission(
        db, user_id=student_id, task_id=task_id, course_id=course_id,
        source_system="manual_teacher",
    )
    resp = await _request_help(client, token, student_id, task_id)
    assert resp.status_code == 409, resp.text


async def test_request_help_with_note_creates_request_visible_to_teacher(
    db, client, gate_on
) -> None:
    student_id, s_token = await _user(db, "s")
    teacher_id, t_token = await _user(db, "t")
    await _link_teacher(db, student_id, teacher_id)
    task_id = await _task(db, await _course(db))

    resp = await _request_help(
        client, s_token, student_id, task_id, stuck_note=f"  {_NOTE_OK}  "
    )
    assert resp.status_code == 200, resp.text
    request_id = resp.json()["request_id"]

    row = await _help_row(db, request_id)
    assert row.stuck_note == _NOTE_OK
    assert row.attempt_id is None

    detail = await client.get(
        f"/api/v1/teacher/help-requests/{request_id}?teacher_id={teacher_id}",
        headers=_bearer(t_token),
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["stuck_note"] == _NOTE_OK


async def test_request_help_after_submission_fills_attempt_id(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)
    await _submission(db, user_id=student_id, task_id=task_id, course_id=course_id)
    last_attempt = await _submission(
        db, user_id=student_id, task_id=task_id, course_id=course_id
    )

    resp = await _request_help(client, token, student_id, task_id)
    assert resp.status_code == 200, resp.text
    row = await _help_row(db, resp.json()["request_id"])
    assert row.attempt_id == last_attempt
    assert row.stuck_note is None


async def test_request_help_gate_off_keeps_old_behavior(db, client, gate_off) -> None:
    """Рубильник выключен — заявка без сдачи и заметки создаётся, как раньше."""
    student_id, token = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)

    resp = await _request_help(client, token, student_id, task_id)
    assert resp.status_code == 200, resp.text
    row = await _help_row(db, resp.json()["request_id"])
    assert row.attempt_id is None


async def test_request_help_note_too_long_is_422(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    resp = await _request_help(client, token, student_id, task_id, stuck_note="х" * 1001)
    assert resp.status_code == 422, resp.text


# ───────────────── 3. ИИ-наставник: гейт на новом разговоре ──────────────────


async def test_tutor_new_session_409_without_attempt(db, client, gate_on) -> None:
    _sid, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))

    resp = await client.get(f"/api/v1/ai-tutor/tasks/{task_id}", headers=_bearer(token))
    assert resp.status_code == 409, resp.text
    assert resp.json()["payload"]["code"] == "attempt_required"


async def test_tutor_ask_without_session_is_gated(db, client, gate_on) -> None:
    """`ask` тоже открывает разговор — обход гейта мимо GET закрыт."""
    _sid, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))

    resp = await client.post(
        f"/api/v1/ai-tutor/tasks/{task_id}/ask",
        headers=_bearer(token),
        json={"message": "помоги"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["payload"]["code"] == "attempt_required"


async def test_tutor_new_session_with_note_goes_to_first_reply(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))

    resp = await client.get(
        f"/api/v1/ai-tutor/tasks/{task_id}",
        params={"stuck_note": _NOTE_OK},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text

    session, created = await session_service.get_or_create(
        db, student_id=student_id, task_id=task_id
    )
    assert created is False
    assert session.stuck_note == _NOTE_OK
    messages = await session_service.build_llm_messages(db, session, None)
    opening = messages[-1].content
    assert _NOTE_OK in opening
    assert STUDENT_DATA_OPEN in opening


async def test_tutor_new_session_after_submission(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)
    await _submission(db, user_id=student_id, task_id=task_id, course_id=course_id)

    resp = await client.get(f"/api/v1/ai-tutor/tasks/{task_id}", headers=_bearer(token))
    assert resp.status_code == 200, resp.text


async def test_tutor_existing_session_not_gated(db, client, gate_off, monkeypatch) -> None:
    """Начатый до включения гейта разговор открывается и после включения."""
    _sid, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    first = await client.get(f"/api/v1/ai-tutor/tasks/{task_id}", headers=_bearer(token))
    assert first.status_code == 200, first.text

    monkeypatch.setattr(help_attempt_gate.settings, "help_requires_attempt", True)
    again = await client.get(f"/api/v1/ai-tutor/tasks/{task_id}", headers=_bearer(token))
    assert again.status_code == 200, again.text
    assert again.json()["session_id"] == first.json()["session_id"]


async def test_tutor_gate_off_keeps_old_behavior(db, client, gate_off) -> None:
    _sid, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    resp = await client.get(f"/api/v1/ai-tutor/tasks/{task_id}", headers=_bearer(token))
    assert resp.status_code == 200, resp.text


# ───────────────── первая реплика наставника ─────────────────────────────────


def _view():
    from app.services.ai_tutor.prompt import TutorTaskView

    class _Shim:
        id = 1
        task_content = {"type": "SA", "stem": "Переведи 5 в двоичную систему"}

    return TutorTaskView.from_task(_Shim, course_title="Информатика")


def test_opening_without_note_unchanged() -> None:
    """Без заметки первая реплика та же, что до tsk-993."""
    view = _view()
    assert build_opening_user_message(view, None) == build_opening_user_message(
        view, None, None, stuck_note=None
    )
    assert STUDENT_DATA_OPEN not in build_opening_user_message(view, None)
    assert "где он застрял" in build_opening_user_message(view, None)


def test_opening_with_note_starts_from_it() -> None:
    view = _view()
    opening = build_opening_user_message(view, None, stuck_note=_NOTE_OK)
    assert f"{STUDENT_DATA_OPEN}\n{_NOTE_OK}\n{STUDENT_DATA_CLOSE}" in opening
    # На вопрос «где застрял» ученик уже ответил — не переспрашиваем.
    assert "спроси, что именно в задании непонятно" not in opening


# ───────────────── сигнал клиенту в состоянии задания ────────────────────────


async def _state(client, token: str, student_id: int, task_id: int) -> dict:
    resp = await client.get(
        f"/api/v1/learning/tasks/{task_id}/state",
        params={"student_id": student_id},
        headers=_bearer(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_task_state_signals_help_attempt_required(db, client, gate_on) -> None:
    student_id, token = await _user(db, "s")
    course_id = await _course(db)
    task_id = await _task(db, course_id)

    assert (await _state(client, token, student_id, task_id))["help_attempt_required"] is True
    await _submission(db, user_id=student_id, task_id=task_id, course_id=course_id)
    assert (await _state(client, token, student_id, task_id))["help_attempt_required"] is False


async def test_task_state_signal_false_when_gate_off(db, client, gate_off) -> None:
    student_id, token = await _user(db, "s")
    task_id = await _task(db, await _course(db))
    assert (await _state(client, token, student_id, task_id))["help_attempt_required"] is False
