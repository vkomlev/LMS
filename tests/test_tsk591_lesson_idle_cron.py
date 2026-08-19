"""tsk-591: тик простоя на настоящей БД — эпизоды, уведомления, лента, пульс.

Что проверяется:

* тик открывает эпизод и пишет уведомление преподавателю занятия;
* повторный проход НЕ создаёт второй эпизод и не шлёт второе уведомление
  (иначе лента и почтовый ящик забились бы повторами одного простоя);
* возвращение ученика закрывает эпизод — преподаватель не бежит зря;
* участник на перерыве/в отказе пропускается;
* эпизод виден в ленте преподавателя и НЕ виден постороннему преподавателю;
* эндпоинт пульса пишет присутствие и отвечает интервалом.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.auth.current_user import CurrentUser
from app.models.users import Users
from app.services import teacher_activity_feed_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.lesson_idle_cron_service import lesson_idle_cron_tick

_TAG = "tsk591"


async def _new_user(db, role: str | None, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**8, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    if role is not None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :u, r.id FROM roles r WHERE r.name = :role ON CONFLICT DO NOTHING"
            ),
            {"u": u.id, "role": role},
        )
    await db.flush()
    return u.id, token


async def _lesson(db, teacher_id: int, *, started_min_ago: int = 30) -> int:
    occurrence_id = (
        await db.execute(
            text(
                "INSERT INTO lesson_occurrence (slot_id, teacher_id, scheduled_at, duration_minutes) "
                "VALUES (NULL, :t, now() - make_interval(mins => CAST(:ago AS int)), 90) RETURNING id"
            ),
            {"t": teacher_id, "ago": started_min_ago},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_teacher (occurrence_id, teacher_id, is_active) "
            "VALUES (:o, :t, true)"
        ),
        {"o": occurrence_id, "t": teacher_id},
    )
    return int(occurrence_id)


async def _participant(db, occurrence_id: int, student_id: int, status: str = "confirmed") -> None:
    await db.execute(
        text(
            "INSERT INTO lesson_occurrence_participant (occurrence_id, student_id, status) "
            "VALUES (:o, :s, :st)"
        ),
        {"o": occurrence_id, "s": student_id, "st": status},
    )


async def _worked(db, student_id: int, *, minutes_ago: int) -> None:
    """Содержательное действие ученика — открытие задания N минут назад."""
    await db.execute(
        text(
            "INSERT INTO learning_events (student_id, event_type, payload, created_at) "
            "VALUES (:s, 'task_opened', '{\"task_id\": 1}'::jsonb, "
            "        now() - make_interval(mins => CAST(:ago AS int)))"
        ),
        {"s": student_id, "ago": minutes_ago},
    )


async def _presence(
    db, student_id: int, *, seen_min_ago: int, interacted_min_ago: int | None = None
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO student_presence (
                student_id, last_seen_at, last_interaction_at, context, updated_at
            )
            VALUES (
                :s, now() - make_interval(mins => CAST(:seen AS int)),
                CASE WHEN CAST(:inter AS int) IS NULL THEN NULL
                     ELSE now() - make_interval(mins => CAST(:inter AS int)) END,
                'task', now()
            )
            ON CONFLICT (student_id) DO UPDATE SET
                last_seen_at = EXCLUDED.last_seen_at,
                last_interaction_at = EXCLUDED.last_interaction_at,
                updated_at = now()
            """
        ),
        {"s": student_id, "seen": seen_min_ago, "inter": interacted_min_ago},
    )


async def _episodes(db, occurrence_id: int) -> list[dict]:
    rows = (
        await db.execute(
            text(
                "SELECT id, student_id, kind, resolved_at FROM lesson_idle_episode "
                "WHERE occurrence_id = :o ORDER BY id"
            ),
            {"o": occurrence_id},
        )
    ).mappings().fetchall()
    return [dict(r) for r in rows]


async def _idle_notifications(db, teacher_id: int, occurrence_id: int) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM notifications WHERE user_id = :t "
                    "AND kind = 'student_idle' AND (payload->>'occurrence_id')::int = :o"
                ),
                {"t": teacher_id, "o": occurrence_id},
            )
        ).scalar()
    )


@pytest_asyncio.fixture
async def scene(db):
    """Занятие идёт полчаса, ученик работал 15 минут назад и затих."""
    teacher_id, _ = await _new_user(db, "teacher", "teacher")
    student_id, student_token = await _new_user(db, None, "student")
    occurrence_id = await _lesson(db, teacher_id)
    await _participant(db, occurrence_id, student_id)
    await db.flush()
    return {
        "teacher_id": teacher_id,
        "student_id": student_id,
        "student_token": student_token,
        "occurrence_id": occurrence_id,
    }


@pytest.mark.asyncio
async def test_idle_episode_opened_and_teacher_notified(db, db_session_factory, scene):
    """Ученик работал и затих в открытом кабинете → эпизод idle + уведомление."""
    await _worked(db, scene["student_id"], minutes_ago=15)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=15)
    await db.flush()

    summary = await lesson_idle_cron_tick(session_factory=db_session_factory)

    assert summary["locked"] is True
    assert summary["opened"] == 1
    episodes = await _episodes(db, scene["occurrence_id"])
    assert len(episodes) == 1
    assert episodes[0]["kind"] == "idle"
    assert episodes[0]["resolved_at"] is None
    assert await _idle_notifications(db, scene["teacher_id"], scene["occurrence_id"]) == 1


@pytest.mark.asyncio
async def test_second_tick_does_not_duplicate(db, db_session_factory, scene):
    """Простой продолжается — второй эпизод и второе уведомление не заводятся.

    Ровно требование задачи: событие создаётся ОДИН раз на простой, иначе тик
    раз в 3 минуты забил бы ленту повторами одного и того же.
    """
    await _worked(db, scene["student_id"], minutes_ago=15)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=15)
    await db.flush()

    await lesson_idle_cron_tick(session_factory=db_session_factory)
    second = await lesson_idle_cron_tick(session_factory=db_session_factory)

    assert second["opened"] == 0
    assert len(await _episodes(db, scene["occurrence_id"])) == 1
    assert await _idle_notifications(db, scene["teacher_id"], scene["occurrence_id"]) == 1


@pytest.mark.asyncio
async def test_return_resolves_episode(db, db_session_factory, scene):
    """Ученик вернулся к работе → эпизод закрывается, второго уведомления нет."""
    await _worked(db, scene["student_id"], minutes_ago=15)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=15)
    await db.flush()
    await lesson_idle_cron_tick(session_factory=db_session_factory)

    # Признак жизни позже начала тишины: снова что-то сделал.
    await _worked(db, scene["student_id"], minutes_ago=0)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=0)
    await db.flush()

    summary = await lesson_idle_cron_tick(session_factory=db_session_factory)

    assert summary["resolved"] == 1
    episodes = await _episodes(db, scene["occurrence_id"])
    assert len(episodes) == 1
    assert episodes[0]["resolved_at"] is not None
    assert await _idle_notifications(db, scene["teacher_id"], scene["occurrence_id"]) == 1


@pytest.mark.asyncio
async def test_away_when_presence_is_gone(db, db_session_factory, scene):
    """Работал и пропал из кабинета → эпизод away («вне системы»)."""
    await _worked(db, scene["student_id"], minutes_ago=20)
    await _presence(db, scene["student_id"], seen_min_ago=18, interacted_min_ago=20)
    await db.flush()

    summary = await lesson_idle_cron_tick(session_factory=db_session_factory)

    assert summary["opened"] == 1
    episodes = await _episodes(db, scene["occurrence_id"])
    assert episodes[0]["kind"] == "away"


@pytest.mark.asyncio
async def test_frontal_lesson_start_is_silent(db, db_session_factory, scene):
    """Ученик ещё не начинал работать — тревоги нет, хотя кабинет открыт.

    Первые минуты урока молчат все. Тревога здесь означала бы, что сигнал
    срабатывает на каждом занятии и ему перестанут верить.
    """
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=25)
    await db.flush()

    summary = await lesson_idle_cron_tick(session_factory=db_session_factory)

    assert summary["opened"] == 0
    assert await _episodes(db, scene["occurrence_id"]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["declined", "on_break", "no_show", "rescheduled"])
async def test_excused_participant_is_skipped(db, db_session_factory, scene, status: str):
    """Отказ, перерыв, неявка, перенос — тишина ожидаема, тревоги нет."""
    await db.execute(
        text(
            "UPDATE lesson_occurrence_participant SET status = :st "
            "WHERE occurrence_id = :o AND student_id = :s"
        ),
        {"st": status, "o": scene["occurrence_id"], "s": scene["student_id"]},
    )
    await _worked(db, scene["student_id"], minutes_ago=20)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=20)
    await db.flush()

    summary = await lesson_idle_cron_tick(session_factory=db_session_factory)

    assert summary["opened"] == 0
    assert await _episodes(db, scene["occurrence_id"]) == []


@pytest.mark.asyncio
async def test_finished_lesson_is_not_scanned(db, db_session_factory, scene):
    """Занятие закончилось — тишина после него не наша забота."""
    await db.execute(
        text(
            "UPDATE lesson_occurrence SET scheduled_at = now() - make_interval(mins => 200) "
            "WHERE id = :o"
        ),
        {"o": scene["occurrence_id"]},
    )
    await _worked(db, scene["student_id"], minutes_ago=20)
    await db.flush()

    summary = await lesson_idle_cron_tick(session_factory=db_session_factory)

    assert summary["opened"] == 0


@pytest.mark.asyncio
async def test_episode_appears_in_teacher_feed(db, db_session_factory, scene):
    """Эпизод виден в ленте преподавателя занятия и скрыт от постороннего."""
    await _worked(db, scene["student_id"], minutes_ago=15)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=15)
    await db.flush()
    await lesson_idle_cron_tick(session_factory=db_session_factory)

    events, _, _ = await teacher_activity_feed_service.get_activity_feed(
        db, CurrentUser(id=scene["teacher_id"], is_service=False), limit=50
    )
    idle = [e for e in events if e["type"] == "student_idle"]
    assert len(idle) == 1
    assert idle[0]["outcome"] == "ongoing"
    assert "без действий" in idle[0]["summary"]

    stranger_id, _ = await _new_user(db, "teacher", "stranger")
    await db.flush()
    other_events, _, _ = await teacher_activity_feed_service.get_activity_feed(
        db, CurrentUser(id=stranger_id, is_service=False), limit=50
    )
    assert [e for e in other_events if e["type"] == "student_idle"] == []


@pytest.mark.asyncio
async def test_resolved_episode_reads_as_returned(db, db_session_factory, scene):
    """Закрытый эпизод в ленте помечен «уже вернулся»."""
    await _worked(db, scene["student_id"], minutes_ago=15)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=15)
    await db.flush()
    await lesson_idle_cron_tick(session_factory=db_session_factory)
    await _worked(db, scene["student_id"], minutes_ago=0)
    await _presence(db, scene["student_id"], seen_min_ago=0, interacted_min_ago=0)
    await db.flush()
    await lesson_idle_cron_tick(session_factory=db_session_factory)

    events, _, _ = await teacher_activity_feed_service.get_activity_feed(
        db, CurrentUser(id=scene["teacher_id"], is_service=False), limit=50
    )
    idle = [e for e in events if e["type"] == "student_idle"]
    assert len(idle) == 1
    assert idle[0]["outcome"] == "resolved"
    assert "вернулся" in idle[0]["summary"]


@pytest.mark.asyncio
async def test_presence_endpoint_writes_pulse(client, db, scene):
    """POST /me/presence пишет строку присутствия и отвечает интервалом."""
    resp = await client.post(
        "/api/v1/me/presence",
        json={"interacted": True, "context": "task", "task_id": 1},
        cookies={"session": scene["student_token"]},
    )
    assert resp.status_code == 200
    assert resp.json()["next_ping_seconds"] > 0

    row = (
        await db.execute(
            text(
                "SELECT last_seen_at, last_interaction_at, context FROM student_presence "
                "WHERE student_id = :s"
            ),
            {"s": scene["student_id"]},
        )
    ).mappings().fetchone()
    assert row is not None
    assert row["context"] == "task"
    assert row["last_interaction_at"] is not None


@pytest.mark.asyncio
async def test_presence_without_interaction_keeps_previous_mark(client, db, scene):
    """Пульс без взаимодействия не стирает прежнюю отметку «был за экраном».

    Иначе «поработал, потом просто смотрит в экран» мгновенно превращалось бы
    в «никогда ничего не делал», и порог тишины отсчитывался бы не с того места.
    """
    await client.post(
        "/api/v1/me/presence",
        json={"interacted": True, "context": "task"},
        cookies={"session": scene["student_token"]},
    )
    await client.post(
        "/api/v1/me/presence",
        json={"interacted": False, "context": "task"},
        cookies={"session": scene["student_token"]},
    )

    row = (
        await db.execute(
            text(
                "SELECT last_interaction_at FROM student_presence WHERE student_id = :s"
            ),
            {"s": scene["student_id"]},
        )
    ).mappings().fetchone()
    assert row["last_interaction_at"] is not None


@pytest.mark.asyncio
async def test_presence_requires_login(client):
    """Без входа пульс не принимается."""
    resp = await client.post("/api/v1/me/presence", json={"interacted": True})
    assert resp.status_code == 401
