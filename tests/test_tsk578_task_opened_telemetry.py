"""tsk-578: телеметрия «задание открыто» и переход pace на реальное время.

Покрываем:
1. `start-or-get-attempt` пишет `task_opened` в `learning_events` при КАЖДОМ
   вызове — и для новой попытки, и для переиспользования существующей
   (повторное открытие — не шум, см. `record_task_opened`).
2. `topic_mastery_service` берёт реальный темп (событие → сдача), когда пар
   достаточно (`MIN_REAL_PACE_SAMPLES`), и БЛИЖАЙШЕЕ ПЕРЕД сдачей открытие,
   а не первое по времени.
3. При недостатке реальных пар — прежний прокси-фолбэк (обратная
   совместимость с tsk-577), с явным `pace_source`.
"""
from __future__ import annotations

import json
import random

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.models.users import Users
from app.services.auth import identity_link_service
from app.services.learning_events_service import record_task_opened
from app.services.topic_mastery_service import (
    MIN_REAL_PACE_SAMPLES,
    PACE_SOURCE_PROXY,
    PACE_SOURCE_REAL,
    topic_overview,
    topic_tasks,
)

pytestmark = pytest.mark.asyncio

_settings = Settings()


def _headers() -> dict[str, str]:
    return {"X-API-Key": next(iter(_settings.valid_api_keys))}


# ── helpers (по образцу test_tsk577_topic_mastery.py) ───────────────────────


async def _student(db, prefix: str) -> int:
    email = f"{prefix}-{random.randint(10**8, 10**10)}@example.com"
    u = Users(email=email, password_hash=None, full_name=prefix, tg_id=None)
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", email)
    await db.commit()
    return u.id


async def _course(db, title: str) -> int:
    res = await db.execute(text(
        "INSERT INTO courses (title, access_level, is_required, course_uid) "
        "VALUES (:t,'self_guided',false,:u) RETURNING id"
    ), {"t": title, "u": f"tsk578-{random.randint(10**8, 10**10)}"})
    cid = int(res.scalar_one())
    await db.commit()
    return cid


async def _task(db, course_id: int, stem: str = "Условие задания") -> int:
    did = (await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))).scalar()
    if did is None:
        pytest.skip("нет ни одной difficulty")
    res = await db.execute(text(
        "INSERT INTO tasks (task_content, course_id, difficulty_id, external_uid) "
        "VALUES (jsonb_build_object('type','SA','stem', CAST(:s AS text)), :c, :d, :u) "
        "RETURNING id"
    ), {"s": stem, "c": course_id, "d": did,
        "u": f"tsk578-{random.randint(10**8, 10**10)}"})
    tid = int(res.scalar_one())
    await db.commit()
    return tid


async def _attempt(db, *, user_id: int, course_id: int) -> int:
    res = await db.execute(text(
        "INSERT INTO attempts (user_id, course_id) VALUES (:u,:c) RETURNING id"
    ), {"u": user_id, "c": course_id})
    aid = int(res.scalar_one())
    await db.commit()
    return aid


async def _open_event(db, *, user_id: int, task_id: int, attempt_id: int,
                       seconds_ago: int) -> None:
    # CAST(:payload AS jsonb), не jsonb_build_object(...) с параметрами внутри:
    # asyncpg не может вывести тип параметра внутри вариативной функции
    # (IndeterminateDatatypeError) — тот же приём, что в record_task_opened.
    payload = json.dumps({"task_id": task_id, "attempt_id": attempt_id, "is_new_attempt": True})
    await db.execute(text("""
        INSERT INTO learning_events (student_id, event_type, payload, created_at)
        VALUES (:u, 'task_opened', CAST(:payload AS jsonb),
                now() - make_interval(secs => :off))
    """), {"u": user_id, "payload": payload, "off": seconds_ago})
    await db.commit()


async def _submission(db, *, user_id: int, task_id: int, attempt_id: int,
                       seconds_ago: int, is_correct: bool = True) -> None:
    await db.execute(text("""
        INSERT INTO task_results (user_id, task_id, attempt_id, answer_json,
                                  score, max_score, is_correct,
                                  submitted_at, received_at, source_system)
        VALUES (:u,:t,:a, CAST('{"answer":"x"}' AS jsonb), :s, 1, :ok,
                now() - make_interval(secs => :off),
                now() - make_interval(secs => :off), 'spw_web')
    """), {"u": user_id, "t": task_id, "a": attempt_id,
           "s": 1 if is_correct else 0, "ok": is_correct, "off": seconds_ago})
    await db.commit()


async def _proxy_submissions(db, *, user_id: int, task_id: int, attempt_id: int,
                              count: int, pace_seconds: int = 60) -> None:
    """Сдачи с фиксированным шагом — прежний прокси считает по их промежуткам."""
    for i in range(count):
        await _submission(
            db, user_id=user_id, task_id=task_id, attempt_id=attempt_id,
            seconds_ago=(count - i) * pace_seconds,
        )


async def _cleanup(db, user_ids: list[int], course_ids: list[int]) -> None:
    await db.execute(text("DELETE FROM task_results WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM learning_events WHERE student_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM attempts WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM user_session WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM identity_link WHERE user_id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:i)"), {"i": user_ids})
    await db.execute(text("DELETE FROM tasks WHERE course_id = ANY(:i)"), {"i": course_ids})
    await db.execute(text("DELETE FROM courses WHERE id = ANY(:i)"), {"i": course_ids})
    await db.commit()


def _find_topic(overview: dict, course_id: int) -> dict | None:
    return next((t for t in overview["topics"] if t["course_id"] == course_id), None)


# ── 1. запись события из start-or-get-attempt ────────────────────────────────


async def test_start_or_get_attempt_records_task_opened_new_and_existing(client, db):
    """Новая попытка и её переиспользование — оба вызова пишут task_opened.

    Повторное открытие — ценный сигнал (см. docstring `record_task_opened`), а
    не шум: событие пишется КАЖДЫЙ раз, is_new_attempt различает случаи.
    """
    course = await _course(db, "tsk578: старт попытки")
    task = await _task(db, course)
    student = await _student(db, "tsk578-start")
    try:
        resp1 = await client.post(
            f"/api/v1/learning/tasks/{task}/start-or-get-attempt",
            json={"student_id": student, "source_system": "test_tsk578"},
            headers=_headers(),
        )
        assert resp1.status_code == 200, resp1.text
        attempt_id = resp1.json()["attempt_id"]

        resp2 = await client.post(
            f"/api/v1/learning/tasks/{task}/start-or-get-attempt",
            json={"student_id": student, "source_system": "test_tsk578"},
            headers=_headers(),
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["attempt_id"] == attempt_id, (
            "повторный вызов обязан вернуть ту же незавершённую попытку"
        )

        rows = (await db.execute(text("""
            SELECT (payload->>'is_new_attempt')::bool AS is_new,
                   (payload->>'attempt_id')::int AS attempt_id,
                   (payload->>'task_id')::int AS task_id
            FROM learning_events
            WHERE student_id = :s AND event_type = 'task_opened'
            ORDER BY id
        """), {"s": student})).mappings().all()

        assert len(rows) == 2, f"ожидалось 2 события (без дедупа), получено {len(rows)}"
        assert rows[0]["is_new"] is True, "первый вызов создаёт новую попытку"
        assert rows[1]["is_new"] is False, "второй вызов переиспользует попытку"
        assert all(r["attempt_id"] == attempt_id for r in rows)
        assert all(r["task_id"] == task for r in rows)
    finally:
        await _cleanup(db, [student], [course])


async def test_record_task_opened_payload_shape(db):
    """Прямой вызов сервиса — форма payload, независимо от HTTP-слоя."""
    course = await _course(db, "tsk578: форма payload")
    task = await _task(db, course)
    student = await _student(db, "tsk578-payload")
    try:
        attempt_id = await _attempt(db, user_id=student, course_id=course)
        await record_task_opened(
            db, student_id=student, task_id=task,
            attempt_id=attempt_id, is_new_attempt=True,
        )
        await db.commit()

        row = (await db.execute(text("""
            SELECT student_id, event_type, payload
            FROM learning_events
            WHERE student_id = :s AND event_type = 'task_opened'
        """), {"s": student})).mappings().one()

        assert row["student_id"] == student
        assert row["payload"]["task_id"] == task
        assert row["payload"]["attempt_id"] == attempt_id
        assert row["payload"]["is_new_attempt"] is True
    finally:
        await _cleanup(db, [student], [course])


# ── 2. pace-CTE: реальный источник, ближайшее открытие перед сдачей ─────────


async def test_real_pace_used_and_picks_nearest_opening_before_submission(db):
    """При достаточной выборке темп берётся из реальных событий, а не прокси.

    У КАЖДОЙ сдачи два открытия: старое (далеко за пределами часа-кэпа — если
    бы алгоритм по ошибке взял его, темп получился бы огромным и его бы
    отсеял `pace_cap`) и свежее (15 с до сдачи). Ближайшее ПЕРЕД сдачей — то,
    что нужно использовать.
    """
    course = await _course(db, "tsk578: реальный темп, ближайшее открытие")
    task = await _task(db, course)
    student = await _student(db, "tsk578-real")
    try:
        attempt_id = await _attempt(db, user_id=student, course_id=course)
        near_gap = 15
        for i in range(MIN_REAL_PACE_SAMPLES):
            # Сдачи разнесены по времени, чтобы не совпасть в одну секунду.
            base_ago = (MIN_REAL_PACE_SAMPLES - i) * 300
            await _open_event(
                db, user_id=student, task_id=task, attempt_id=attempt_id,
                seconds_ago=base_ago + 7200,  # далеко за pace_cap (3600с)
            )
            await _open_event(
                db, user_id=student, task_id=task, attempt_id=attempt_id,
                seconds_ago=base_ago + near_gap,
            )
            await _submission(
                db, user_id=student, task_id=task, attempt_id=attempt_id,
                seconds_ago=base_ago,
            )

        topic = _find_topic(await topic_overview(db, days=7), course)
        assert topic is not None
        assert topic["pace_source"] == PACE_SOURCE_REAL, (
            f"ожидался реальный источник при {MIN_REAL_PACE_SAMPLES} парах, "
            f"получен {topic['pace_source']}"
        )
        assert topic["median_pace_seconds"] is not None
        assert abs(topic["median_pace_seconds"] - near_gap) < 2, (
            "взято не ближайшее перед сдачей открытие: "
            f"темп {topic['median_pace_seconds']} с вместо ~{near_gap} с"
        )
    finally:
        await _cleanup(db, [student], [course])


async def test_falls_back_to_proxy_when_real_samples_insufficient(db):
    """Реальных пар меньше порога — используется прежний прокси (tsk-577).

    Обратная совместимость: при деплое реальных пар 0 у всех тем, поведение
    экрана не должно меняться, пока телеметрия не накопится.
    """
    course = await _course(db, "tsk578: недостаточно реальных пар")
    task = await _task(db, course)
    student = await _student(db, "tsk578-fallback")
    try:
        attempt_id = await _attempt(db, user_id=student, course_id=course)
        # Меньше порога — не должно хватить на переход на реальный источник.
        assert MIN_REAL_PACE_SAMPLES > 1
        await _open_event(db, user_id=student, task_id=task, attempt_id=attempt_id, seconds_ago=15)
        await _submission(db, user_id=student, task_id=task, attempt_id=attempt_id, seconds_ago=0)

        # Прокси-сигнал: обычные сдачи без событий открытия, фиксированный шаг.
        await _proxy_submissions(
            db, user_id=student, task_id=task, attempt_id=attempt_id,
            count=10, pace_seconds=60,
        )

        topic = _find_topic(await topic_overview(db, days=7), course)
        assert topic is not None
        assert topic["pace_source"] == PACE_SOURCE_PROXY, (
            f"ожидался прокси при 1 реальной паре (< {MIN_REAL_PACE_SAMPLES}), "
            f"получен {topic['pace_source']}"
        )
        assert topic["median_pace_seconds"] is not None
    finally:
        await _cleanup(db, [student], [course])


async def test_topic_tasks_expose_pace_source(db):
    """Разбор темы (`topic_tasks`) отдаёт `pace_source` так же, как обзор."""
    course = await _course(db, "tsk578: pace_source в разборе темы")
    task = await _task(db, course)
    student = await _student(db, "tsk578-tasks")
    try:
        attempt_id = await _attempt(db, user_id=student, course_id=course)
        for i in range(MIN_REAL_PACE_SAMPLES):
            base_ago = (MIN_REAL_PACE_SAMPLES - i) * 300
            await _open_event(
                db, user_id=student, task_id=task, attempt_id=attempt_id,
                seconds_ago=base_ago + 12,
            )
            await _submission(
                db, user_id=student, task_id=task, attempt_id=attempt_id,
                seconds_ago=base_ago,
            )

        rows = await topic_tasks(db, course_id=course, days=7)
        row = next((r for r in rows if r["task_id"] == task), None)
        assert row is not None
        assert row["pace_source"] == PACE_SOURCE_REAL
        assert row["median_pace_seconds"] is not None
    finally:
        await _cleanup(db, [student], [course])
