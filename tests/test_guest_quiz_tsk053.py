"""Гостевой квиз-лид-магнит (tsk-053, фаза 1).

Проверяется весь путь посетителя: увидел вопросы → ответил → получил рекомендацию →
оставил контакт. Плюс граничные случаи, из-за которых квиз молча показывал бы
человеку не ту программу: незавершённый квиз, ничья по шкалам, изменённый ответ.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.requires_redis

_QUIZ_UID = "pytest:tsk053-quiz"
_TARGET_INF_UID = "pytest:tsk053-target-inf"
_TARGET_PY_UID = "pytest:tsk053-target-py"

_STATE: dict[str, int] = {}


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _reset_quiz_rate_limits():
    """Сбросить лимиты — иначе повторный прогон встречает 429."""
    import os

    import redis.asyncio as aioredis

    redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/2"), decode_responses=True
    )
    patterns = [
        "guest_session:*",
        "quiz_read:*",
        "quiz_answer:*",
        "quiz_answer_session:*",
        "quiz_lead:*",
        "quiz_lead_session:*",
    ]
    try:
        for pat in patterns:
            async for key in redis.scan_iter(match=pat, count=200):
                await redis.delete(key)
        yield
    finally:
        await redis.aclose()


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _seed_quiz(db):
    """Курс-квиз из двух вопросов SC_Qw и две программы, между которыми он выбирает."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()

    quiz_course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid, is_public_demo) "
                "VALUES ('pytest квиз подбора', 'self_guided', :uid, TRUE) RETURNING id"
            ),
            {"uid": _QUIZ_UID},
        )
    ).scalar_one()
    for uid, title in ((_TARGET_INF_UID, "Информатика"), (_TARGET_PY_UID, "Python")):
        await db.execute(
            text(
                "INSERT INTO courses (title, access_level, course_uid, is_public_demo) "
                "VALUES (:t, 'self_guided', :uid, FALSE)"
            ),
            {"t": title, "uid": uid},
        )

    async def _add_question(order: int, stem: str) -> int:
        content = {
            "type": "SC_Qw",
            "stem": stem,
            "scales": ["информатика", "python"],
            "options": [
                {"id": "A", "text": "Ближе информатика", "scores": {"информатика": 2}},
                {"id": "B", "text": "Ближе программирование", "scores": {"python": 2}},
            ],
        }
        rules = {"max_score": 1, "quiz": {"scales": ["информатика", "python"], "mode": "single"}}
        return int(
            (
                await db.execute(
                    text(
                        "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                        "difficulty_id, solution_rules, order_position) "
                        "VALUES (:uid, 1, CAST(:c AS jsonb), :course, :diff, CAST(:r AS jsonb), :ord) "
                        "RETURNING id"
                    ),
                    {
                        "uid": f"{_QUIZ_UID}:q{order}",
                        "c": json.dumps(content, ensure_ascii=False),
                        "course": quiz_course_id,
                        "diff": difficulty_id,
                        "r": json.dumps(rules, ensure_ascii=False),
                        "ord": order,
                    },
                )
            ).scalar_one()
        )

    _STATE["course_id"] = quiz_course_id
    _STATE["q1"] = await _add_question(1, "Что тебе ближе?")
    _STATE["q2"] = await _add_question(2, "Чем хочется заняться?")

    for scale, target in (("информатика", _TARGET_INF_UID), ("python", _TARGET_PY_UID)):
        await db.execute(
            text(
                "INSERT INTO assignment_rule (code, title, course_id, trigger_event, "
                "condition, target_course_uid, is_active) "
                "VALUES (:code, :title, :c, 'quiz_scale', CAST(:cond AS jsonb), :t, true)"
            ),
            {
                "code": f"pytest-tsk053-{scale}",
                "title": f"pytest квиз → {scale}",
                "c": quiz_course_id,
                "cond": json.dumps({"scale": scale, "mode": "argmax"}, ensure_ascii=False),
                "t": target,
            },
        )
    await db.commit()

    try:
        yield
    finally:
        await db.execute(
            text("DELETE FROM leads WHERE quiz_course_id = :c"), {"c": quiz_course_id}
        )
        await db.execute(
            text("DELETE FROM assignment_rule WHERE course_id = :c"), {"c": quiz_course_id}
        )
        await db.execute(
            text("DELETE FROM courses WHERE course_uid IN (:a, :b, :c)"),
            {"a": _QUIZ_UID, "b": _TARGET_INF_UID, "c": _TARGET_PY_UID},
        )
        await db.commit()


async def _start_session(client) -> None:
    resp = await client.post("/api/v1/learning/guest/session")
    assert resp.status_code == 201


async def _answer(client, task_id: int, option_id: str):
    return await client.post(
        "/api/v1/learning/guest/quiz/answers",
        json={"task_id": task_id, "selected_option_ids": [option_id]},
    )


# ─── чтение квиза ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quiz_returns_questions_in_order(client):
    """Вопросы отдаются по порядку, с вариантами и без баллов по шкалам."""
    resp = await client.get(f"/api/v1/learning/guest/quiz/{_QUIZ_UID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 2
    assert body["answered_count"] == 0
    assert body["is_complete"] is False
    assert [q["order"] for q in body["questions"]] == [1, 2]
    assert body["questions"][0]["stem"] == "Что тебе ближе?"
    # Механика подбора наружу не утекает: посетитель не должен видеть, какой
    # вариант к какой программе ведёт, иначе квиз превращается в анкету «выбери ответ».
    assert "scores" not in json.dumps(body["questions"][0]["options"])


@pytest.mark.asyncio
async def test_quiz_404_for_non_demo_course(client):
    """Непубличный курс квизом не притворяется."""
    resp = await client.get("/api/v1/learning/guest/quiz/PY")
    assert resp.status_code == 404


# ─── прохождение ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_answer_requires_session(client):
    """Без гостевой сессии ответ отнести некуда — 400, а не молчаливая потеря."""
    resp = await _answer(client, _STATE["q1"], "A")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_full_pass_gives_recommendation(client):
    """Оба ответа в одну сторону → рекомендация той самой программы."""
    await _start_session(client)

    first = await _answer(client, _STATE["q1"], "A")
    assert first.status_code == 201
    assert first.json()["answered_count"] == 1
    assert first.json()["is_complete"] is False

    second = await _answer(client, _STATE["q2"], "A")
    assert second.json()["is_complete"] is True

    result = await client.get(f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["is_complete"] is True
    assert body["scales"] == {"информатика": 4, "python": 0}
    assert body["recommendation"]["course_uid"] == _TARGET_INF_UID
    assert body["lead_submitted"] is False
    # Ссылка на запись готова к переходу и несёт название программы.
    assert body["contact_url"].startswith("https://t.me/")
    assert "%" in body["contact_url"]


@pytest.mark.asyncio
async def test_incomplete_quiz_has_no_recommendation(client):
    """На половине ответов программу не показываем: argmax назовёт случайного лидера."""
    await _start_session(client)
    await _answer(client, _STATE["q1"], "B")

    body = (await client.get(f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/result")).json()
    assert body["is_complete"] is False
    assert body["answered_count"] == 1
    assert body["recommendation"] is None
    # Ссылка на разговор есть всегда — человека не бросаем и без рекомендации.
    assert body["contact_url"]


@pytest.mark.asyncio
async def test_tie_gives_no_recommendation(client):
    """Ничья по шкалам — честное «не определилась», а не случайный победитель."""
    await _start_session(client)
    await _answer(client, _STATE["q1"], "A")
    await _answer(client, _STATE["q2"], "B")

    body = (await client.get(f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/result")).json()
    assert body["is_complete"] is True
    assert body["scales"] == {"информатика": 2, "python": 2}
    assert body["recommendation"] is None


@pytest.mark.asyncio
async def test_changed_answer_is_not_double_counted(client):
    """Передумал — считается последний ответ, а не сумма обоих."""
    await _start_session(client)
    await _answer(client, _STATE["q1"], "A")
    await _answer(client, _STATE["q1"], "B")  # передумал
    await _answer(client, _STATE["q2"], "B")

    quiz = (await client.get(f"/api/v1/learning/guest/quiz/{_QUIZ_UID}")).json()
    assert quiz["answered_count"] == 2, "изменённый ответ не должен считаться новым вопросом"
    assert quiz["questions"][0]["selected_option_ids"] == ["B"]

    body = (await client.get(f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/result")).json()
    assert body["scales"] == {"информатика": 0, "python": 4}
    assert body["recommendation"]["course_uid"] == _TARGET_PY_UID


@pytest.mark.asyncio
async def test_answer_404_for_unknown_task(client):
    """Несуществующий вопрос — 404."""
    await _start_session(client)
    resp = await _answer(client, 10**9, "A")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_answer_rejects_plain_demo_task(client, db):
    """Обычная демо-задача через ручку квиза не проходит.

    Проверяется именно НЕ-квизовое задание в том же публичном курсе, а не
    отсутствующее: у этих двух случаев разные ветки в сервисе.
    """
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    plain_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                    "difficulty_id, solution_rules, order_position) "
                    "VALUES (:uid, 1, CAST(:c AS jsonb), :course, :diff, CAST(:r AS jsonb), 99) "
                    "RETURNING id"
                ),
                {
                    "uid": f"{_QUIZ_UID}:plain",
                    "c": json.dumps(
                        {
                            "type": "SC",
                            "stem": "Обычный вопрос с верным ответом",
                            "options": [
                                {"id": "A", "text": "Верный"},
                                {"id": "B", "text": "Неверный"},
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    "course": _STATE["course_id"],
                    "diff": difficulty_id,
                    "r": json.dumps({"max_score": 1, "correct_options": ["A"]}),
                },
            )
        ).scalar_one()
    )
    await db.commit()

    await _start_session(client)
    resp = await _answer(client, plain_id, "A")
    assert resp.status_code == 400
    assert "квиз" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_plain_task_answer_does_not_count_as_quiz_progress(client, db):
    """Ответ на обычную задачу курса не считается прогрессом по квизу.

    В курсе-квизе может лежать и обычная демо-задача. Если её ответ попадает в
    счёт, человек оказывается в «прошли квиз», не ответив ни на один вопрос, —
    и воронка показывает конверсию, которой не было.
    """
    from app.services import guest_quiz_service

    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    plain_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                    "difficulty_id, solution_rules, order_position) "
                    "VALUES (:uid, 1, CAST(:c AS jsonb), :course, :diff, CAST(:r AS jsonb), 99) "
                    "RETURNING id"
                ),
                {
                    "uid": f"{_QUIZ_UID}:plain2",
                    "c": json.dumps(
                        {
                            "type": "SC",
                            "stem": "Обычный вопрос",
                            "options": [
                                {"id": "A", "text": "Верный"},
                                {"id": "B", "text": "Неверный"},
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    "course": _STATE["course_id"],
                    "diff": difficulty_id,
                    "r": json.dumps({"max_score": 1, "correct_options": ["A"]}),
                },
            )
        ).scalar_one()
    )
    await db.commit()

    await _start_session(client)
    # Отвечаем на обычную задачу штатной гостевой ручкой и на ОДИН вопрос квиза.
    await client.post(
        "/api/v1/learning/guest/attempts",
        json={"task_id": plain_id, "answer": {"type": "SC", "response": {"selected_option_ids": ["A"]}}},
    )
    await _answer(client, _STATE["q1"], "A")

    rows = await guest_quiz_service.get_quiz_funnel(db)
    row = next(r for r in rows if r["course_uid"] == _QUIZ_UID)
    assert row["total_questions"] == 2, "обычная задача не должна считаться вопросом квиза"
    assert row["started"] == 1
    assert row["completed"] == 0, "один вопрос из двух — это не пройденный квиз"


# ─── заявка ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lead_is_created_with_quiz_source(client, db):
    """Контакт превращается в лид с каналом «квиз» и привязкой к квизу."""
    await _start_session(client)
    await _answer(client, _STATE["q1"], "A")
    await _answer(client, _STATE["q2"], "A")

    resp = await client.post(
        f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/lead",
        json={"contact": "+7 900 000-00-00", "full_name": "Тест Тестович"},
    )
    assert resp.status_code == 201
    assert resp.json()["already_submitted"] is False
    lead_id = resp.json()["lead_id"]

    row = (
        await db.execute(
            text(
                "SELECT l.contact, l.full_name, l.note, l.quiz_course_id, s.code "
                "FROM leads l JOIN lead_source s ON s.id = l.source_id WHERE l.id = :id"
            ),
            {"id": lead_id},
        )
    ).first()
    assert row is not None
    contact, full_name, note, quiz_course_id, source_code = row
    assert contact == "+7 900 000-00-00"
    assert full_name == "Тест Тестович"
    assert source_code == "quiz"
    assert quiz_course_id == _STATE["course_id"]
    # В заметке маркетолог сразу видит, что человеку подобралось.
    assert "Информатика" in note

    result = (await client.get(f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/result")).json()
    assert result["lead_submitted"] is True


@pytest.mark.asyncio
async def test_repeated_lead_updates_instead_of_duplicating(client, db):
    """Поправил телефон — это тот же человек, а не второй лид."""
    await _start_session(client)
    await _answer(client, _STATE["q1"], "A")
    await _answer(client, _STATE["q2"], "A")

    first = await client.post(
        f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/lead", json={"contact": "опечатка"}
    )
    second = await client.post(
        f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/lead", json={"contact": "+7 900 111-11-11"}
    )
    assert second.status_code == 201
    assert second.json()["already_submitted"] is True
    assert second.json()["lead_id"] == first.json()["lead_id"]

    count = (
        await db.execute(
            text("SELECT count(*) FROM leads WHERE quiz_course_id = :c"),
            {"c": _STATE["course_id"]},
        )
    ).scalar_one()
    assert count == 1

    contact = (
        await db.execute(
            text("SELECT contact FROM leads WHERE id = :id"), {"id": first.json()["lead_id"]}
        )
    ).scalar_one()
    assert contact == "+7 900 111-11-11"


@pytest.mark.asyncio
async def test_lead_requires_session(client):
    """Заявка без гостевой сессии не принимается — её нельзя связать с прохождением."""
    resp = await client.post(
        f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/lead", json={"contact": "+7 900 000-00-00"}
    )
    assert resp.status_code == 400


# ─── воронка ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_funnel_counts_started_completed_and_leads(client, db):
    """Воронка считает людей: начал, прошёл, оставил контакт."""
    from app.services import guest_quiz_service

    await _start_session(client)
    await _answer(client, _STATE["q1"], "A")
    await _answer(client, _STATE["q2"], "A")
    await client.post(
        f"/api/v1/learning/guest/quiz/{_QUIZ_UID}/lead", json={"contact": "+7 900 222-22-22"}
    )

    rows = await guest_quiz_service.get_quiz_funnel(db)
    row = next(r for r in rows if r["course_uid"] == _QUIZ_UID)
    assert row["total_questions"] == 2
    assert row["started"] == 1
    assert row["completed"] == 1
    assert row["leads"] == 1
    assert row["lead_rate"] == 1.0
