"""Гостевая ЕГЭ-диагностика (tsk-053, фаза 2).

Проверяется путь посетителя: получил восемь задач по темам → решил → увидел разбор →
оставил контакт. Плюс то, из-за чего диагностика молча перестала бы измерять: набор,
меняющийся при обновлении страницы; правильность, подсказанная при приёме ответа;
слабые темы, показанные на середине; воронка, считающая зонды вместо шагов.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.requires_redis

_DIAG_UID = "pytest:tsk053-diagnostic"
_TOPIC_COURSE_UID = "pytest:tsk053-topic-course"

_STATE: dict[str, object] = {}

#: Две темы по три варианта: хватает, чтобы поймать и отбор варианта, и разбор по темам.
_TOPICS = [
    {"code": "t1", "title": "Тема 1. Двоичная запись", "answers": ["5", "8", "7"]},
    {"code": "t2", "title": "Тема 2. Комбинаторика", "answers": ["27", "16", "12"]},
]


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _reset_rate_limits():
    import os

    import redis.asyncio as aioredis

    redis = aioredis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/2"), decode_responses=True
    )
    patterns = [
        "guest_session:*",
        "diag_read:*",
        "diag_answer:*",
        "diag_answer_session:*",
        "diag_lead:*",
        "diag_lead_session:*",
    ]
    try:
        for pat in patterns:
            async for key in redis.scan_iter(match=pat, count=200):
                await redis.delete(key)
        yield
    finally:
        await redis.aclose()


@pytest_asyncio.fixture(autouse=True, scope="function")
async def _seed_diagnostic(db):
    """Курс диагностики с двумя темами по три зонда и курс-тема для рекомендации."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()

    course_id = (
        await db.execute(
            text(
                "INSERT INTO courses (title, description, access_level, course_uid, "
                "is_public_demo) VALUES ('pytest диагностика', 'описание', 'auto_check', "
                ":uid, TRUE) RETURNING id"
            ),
            {"uid": _DIAG_UID},
        )
    ).scalar_one()
    await db.execute(
        text(
            "INSERT INTO courses (title, access_level, course_uid, is_public_demo) "
            "VALUES ('Курс по теме', 'self_guided', :uid, FALSE)"
        ),
        {"uid": _TOPIC_COURSE_UID},
    )

    position = 0
    probes: dict[str, list[int]] = {}
    for topic in _TOPICS:
        for variant, answer in enumerate(topic["answers"], start=1):
            position += 1
            content = {
                "type": "SA",
                "stem": f"{topic['title']} — вариант {variant}",
                "lead_magnet": True,
                "diagnostic_topic": {
                    "code": topic["code"],
                    "title": topic["title"],
                    "course_uid": _TOPIC_COURSE_UID,
                },
            }
            rules = {
                "max_score": 1,
                "auto_check": True,
                "scoring_mode": "all_or_nothing",
                "manual_review_required": False,
                "penalties": {"wrong_answer": 0, "extra_wrong_mc": 0, "missing_answer": 0},
                "short_answer": {
                    "use_regex": False,
                    "regex": None,
                    "normalization": ["trim", "lower"],
                    "accepted_answers": [{"value": answer, "score": 1}],
                },
            }
            task_id = int(
                (
                    await db.execute(
                        text(
                            "INSERT INTO tasks (external_uid, max_score, task_content, "
                            "course_id, difficulty_id, solution_rules, order_position, "
                            "is_active) VALUES (:uid, 1, CAST(:c AS jsonb), :course, :diff, "
                            "CAST(:r AS jsonb), :ord, TRUE) RETURNING id"
                        ),
                        {
                            "uid": f"{_DIAG_UID}:{topic['code']}:v{variant}",
                            "c": json.dumps(content, ensure_ascii=False),
                            "course": course_id,
                            "diff": difficulty_id,
                            "r": json.dumps(rules, ensure_ascii=False),
                            "ord": position,
                        },
                    )
                ).scalar_one()
            )
            probes.setdefault(topic["code"], []).append(task_id)

    _STATE["course_id"] = course_id
    _STATE["probes"] = probes
    #: task_id → верный ответ, чтобы тесты решали задачи, какие бы им ни выпали.
    _STATE["answers"] = {
        probes[t["code"]][i]: t["answers"][i] for t in _TOPICS for i in range(3)
    }
    await db.commit()

    try:
        yield
    finally:
        await db.execute(
            text("DELETE FROM leads WHERE quiz_course_id = :c"), {"c": course_id}
        )
        await db.execute(
            text("DELETE FROM courses WHERE course_uid IN (:a, :b)"),
            {"a": _DIAG_UID, "b": _TOPIC_COURSE_UID},
        )
        await db.commit()


async def _start_session(client) -> None:
    assert (await client.post("/api/v1/learning/guest/session")).status_code == 201


async def _answer(client, task_id: int, value: str):
    return await client.post(
        "/api/v1/learning/guest/diagnostic/answers",
        json={"task_id": task_id, "value": value},
    )


async def _solve_all(client, *, correctly: bool = True):
    """Пройти диагностику целиком. Ответы берутся по фактически выпавшим задачам."""
    body = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    for q in body["questions"]:
        right = _STATE["answers"][q["task_id"]]  # type: ignore[index]
        await _answer(client, q["task_id"], right if correctly else "заведомо мимо")
    return body


# ─── набор задач ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_probe_per_topic(client):
    """На каждую тему выпадает ровно одна задача, а не все её варианты."""
    await _start_session(client)
    body = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()

    assert body["total_count"] == len(_TOPICS)
    assert [q["topic_code"] for q in body["questions"]] == [t["code"] for t in _TOPICS]
    assert body["answered_count"] == 0
    assert body["is_complete"] is False


@pytest.mark.asyncio
async def test_probe_set_is_stable_for_one_visitor(client):
    """Обновил страницу — задачи те же.

    Иначе ответы, данные до обновления, повисли бы: человек видел бы «отвечено 3»
    на задачах, которых больше нет.
    """
    await _start_session(client)
    first = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    second = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()

    assert [q["task_id"] for q in first["questions"]] == [
        q["task_id"] for q in second["questions"]
    ]


@pytest.mark.asyncio
async def test_answer_does_not_reveal_correctness(client):
    """Приём ответа отдаёт прогресс, но не «верно/неверно».

    Иначе диагностика превращается в угадайку: подобрал по отклику — и разбор в
    конце ничего уже не измеряет.
    """
    await _start_session(client)
    body = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    task_id = body["questions"][0]["task_id"]

    resp = await _answer(client, task_id, _STATE["answers"][task_id])  # type: ignore[index]
    assert resp.status_code == 201
    payload = resp.json()
    assert payload["answered_count"] == 1
    assert "is_correct" not in payload
    assert "correct_answer" not in payload


@pytest.mark.asyncio
async def test_answer_requires_session(client):
    """Без гостевой сессии ответ принять некуда: набор закреплён именно за ней."""
    task_id = _STATE["probes"]["t1"][0]  # type: ignore[index]
    assert (await _answer(client, task_id, "5")).status_code == 400


@pytest.mark.asyncio
async def test_answer_rejects_task_outside_diagnostic(client, db):
    """Обычная демо-задача через ручку диагностики не проходит."""
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar_one()
    plain_id = int(
        (
            await db.execute(
                text(
                    "INSERT INTO tasks (external_uid, max_score, task_content, course_id, "
                    "difficulty_id, solution_rules, order_position) VALUES (:uid, 1, "
                    "CAST(:c AS jsonb), :course, :diff, CAST(:r AS jsonb), 99) RETURNING id"
                ),
                {
                    "uid": f"{_DIAG_UID}:plain",
                    "c": json.dumps(
                        {"type": "SA", "stem": "Обычная задача без темы"}, ensure_ascii=False
                    ),
                    "course": _STATE["course_id"],
                    "diff": difficulty_id,
                    "r": json.dumps(
                        {
                            "max_score": 1,
                            "short_answer": {
                                "accepted_answers": [{"value": "1", "score": 1}],
                                "normalization": ["trim"],
                                "use_regex": False,
                            },
                        }
                    ),
                },
            )
        ).scalar_one()
    )
    await db.commit()

    await _start_session(client)
    resp = await _answer(client, plain_id, "1")
    assert resp.status_code == 400
    assert "диагностик" in resp.json()["detail"].lower()


# ─── итог ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_counts_and_shows_reference(client):
    """Всё решено верно — счёт полный, слабых тем нет, верные ответы показаны."""
    await _start_session(client)
    await _solve_all(client, correctly=True)

    body = (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/result")
    ).json()
    assert body["is_complete"] is True
    assert body["solved"] == len(_TOPICS)
    assert body["total"] == len(_TOPICS)
    assert body["weak_topics"] == []
    assert all(t["is_correct"] for t in body["topics"])
    # Верный ответ появляется только здесь — при приёме его не было.
    assert all(t["correct_answer"] for t in body["topics"])
    assert body["contact_url"].startswith("https://t.me/")


@pytest.mark.asyncio
async def test_wrong_answers_become_weak_topics(client):
    """Ошибся — тема попадает в «стоит подтянуть» вместе с курсом по ней."""
    await _start_session(client)
    await _solve_all(client, correctly=False)

    body = (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/result")
    ).json()
    assert body["solved"] == 0
    assert len(body["weak_topics"]) == len(_TOPICS)
    assert all(t["course_uid"] == _TOPIC_COURSE_UID for t in body["weak_topics"])
    assert all(t["your_answer"] == "заведомо мимо" for t in body["topics"])


@pytest.mark.asyncio
async def test_incomplete_result_hides_weak_topics(client):
    """На середине «просела тема» означало бы всего лишь «до неё не дошли»."""
    await _start_session(client)
    body = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    first = body["questions"][0]
    await _answer(client, first["task_id"], _STATE["answers"][first["task_id"]])  # type: ignore[index]

    result = (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/result")
    ).json()
    assert result["is_complete"] is False
    assert result["solved"] == 1
    assert result["weak_topics"] == []


@pytest.mark.asyncio
async def test_changed_answer_is_counted_once(client):
    """Передумал и ответил заново — считается последний ответ, тема не задваивается."""
    await _start_session(client)
    body = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    first = body["questions"][0]
    right = _STATE["answers"][first["task_id"]]  # type: ignore[index]

    await _answer(client, first["task_id"], "мимо")
    await _answer(client, first["task_id"], right)

    quiz = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    assert quiz["answered_count"] == 1

    result = (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/result")
    ).json()
    assert result["solved"] == 1


@pytest.mark.asyncio
async def test_result_hides_reference_for_unanswered(client):
    """Верные ответы не выдаются тому, кто ничего не решал.

    Иначе итог открывается сразу и работает как шпаргалка: зонды с ответами
    разлетаются по чатам, и диагностика перестаёт что-либо измерять.
    """
    await _start_session(client)
    body = (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/result")
    ).json()

    assert body["solved"] == 0
    assert all(t["correct_answer"] is None for t in body["topics"])

    # Ответил на одну — верный ответ появился только у неё.
    quiz = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    first = quiz["questions"][0]
    await _answer(client, first["task_id"], "мимо")

    body2 = (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/result")
    ).json()
    shown = [t for t in body2["topics"] if t["correct_answer"] is not None]
    assert len(shown) == 1
    assert shown[0]["topic_code"] == first["topic_code"]


@pytest.mark.asyncio
async def test_answer_rejects_probe_outside_own_set(client):
    """Ответ принимается только на задачу из своего набора.

    Иначе можно закрыть тему чужим вариантом: сам человек его не увидит, а воронка
    засчитает тему как пройденную по любому ответу в ней.
    """
    await _start_session(client)
    body = (await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}")).json()
    mine = {q["task_id"] for q in body["questions"]}

    all_probes = {tid for ids in _STATE["probes"].values() for tid in ids}  # type: ignore[union-attr]
    foreign = sorted(all_probes - mine)
    assert foreign, "в наборе должны остаться невыпавшие варианты"

    resp = await _answer(client, foreign[0], "5")
    assert resp.status_code == 400
    assert "набор" in resp.json()["detail"].lower()


# ─── заявка и воронка ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lead_carries_result_to_crm(client, db):
    """Заявка приходит с счётом и списком просевших тем."""
    await _start_session(client)
    await _solve_all(client, correctly=False)

    resp = await client.post(
        f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/lead",
        json={"contact": "+7 900 333-33-33", "full_name": "Тест Диагностика"},
    )
    assert resp.status_code == 201

    row = (
        await db.execute(
            text(
                "SELECT l.note, l.contact, s.code FROM leads l "
                "JOIN lead_source s ON s.id = l.source_id WHERE l.id = :id"
            ),
            {"id": resp.json()["lead_id"]},
        )
    ).first()
    note, contact, source_code = row
    assert source_code == "quiz"
    assert contact == "+7 900 333-33-33"
    assert "Решено 0 из 2" in note
    assert "Тема 1" in note

    result = (
        await client.get(f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/result")
    ).json()
    assert result["lead_submitted"] is True


@pytest.mark.asyncio
async def test_funnel_counts_topics_not_probes(client, db):
    """Воронка считает шаги (темы), а не заготовленные варианты.

    Зондов 6, показывается 2. Если считать задачами, «прошёл до конца» не наступит
    никогда, и конверсия будет вечным нулём.
    """
    from app.services import lead_magnet_service

    await _start_session(client)
    await _solve_all(client, correctly=True)
    await client.post(
        f"/api/v1/learning/guest/diagnostic/{_DIAG_UID}/lead",
        json={"contact": "+7 900 444-44-44"},
    )

    rows = await lead_magnet_service.get_funnel(db)
    row = next(r for r in rows if r["course_uid"] == _DIAG_UID)
    assert row["total_questions"] == len(_TOPICS), "шагов столько же, сколько тем"
    assert row["started"] == 1
    assert row["completed"] == 1
    assert row["leads"] == 1
    assert row["lead_rate"] == 1.0


# ─── отбор варианта ─────────────────────────────────────────────────────────

def test_variant_pick_is_stable_and_spread():
    """Отбор варианта: один посетитель — всегда тот же, разные — вразнобой.

    Свойство проверяется на функции, а не через ручку: через ручку «два человека
    получили разное» — совпадение с вероятностью 1/3, и тест был бы плавающим.
    """
    from uuid import UUID

    from app.services.guest_diagnostic_service import _pick_variant

    sessions = [UUID(int=i) for i in range(1, 61)]

    # Одна и та же сессия — один и тот же вариант, сколько ни спрашивай.
    for s in sessions[:5]:
        picks = {_pick_variant(s, "t1", 3) for _ in range(10)}
        assert len(picks) == 1

    # По шестидесяти посетителям должны использоваться все три варианта: иначе
    # «случайный отбор» на деле раздавал бы всем одну и ту же задачу.
    spread = {_pick_variant(s, "t1", 3) for s in sessions}
    assert spread == {0, 1, 2}

    # Тема входит в ключ: иначе человек получил бы один и тот же номер варианта
    # во всех темах разом.
    same_session = sessions[0]
    by_topic = {_pick_variant(same_session, f"t{i}", 3) for i in range(1, 20)}
    assert len(by_topic) > 1

    # Без сессии — первый вариант, без падения.
    assert _pick_variant(None, "t1", 3) == 0
