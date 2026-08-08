"""tsk-032 (удержание между занятиями: недельная серия + личные вехи).

Два слоя:

1. **Чистая логика серии** (`compute_state`, без БД) — там, где живут ошибки
   границ: понедельник, разрыв недели, лучший прогон, «милость понедельника».
2. **Определение события на настоящей БД** — что серия считает ровно то же,
   что метрика `between_lessons` дашборда родителя: время урока вычтено,
   ручной источник и отменённая попытка не в счёт. Плюс идемпотентность
   фиксации вех и гейт эндпоинта.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.lesson_occurrence import LessonOccurrence
from app.models.lesson_occurrence_participant import LessonOccurrenceParticipant
from app.models.users import Users
from app.services import retention_service
from app.services.auth import identity_link_service
from app.services.auth.session_service import create_session
from app.services.retention_achievements_cron_service import (
    retention_achievements_cron_tick,
)

UTC = timezone.utc
_TAG = "tsk032"

# Опорный понедельник для чистых тестов логики (реальная дата не важна —
# важно, что это понедельник, и от него отсчитываются недели).
_MON = date(2026, 8, 3)
_WED = _MON + timedelta(days=2)
_SUN = _MON + timedelta(days=6)


def _ev(*days: date) -> list[tuple[date, str, int]]:
    """События вида (дата, вид, id элемента) — по одному элементу на день."""
    return [(d, "task", i + 1) for i, d in enumerate(days)]


# ====================== Слой 1: чистая логика серии ======================


def test_streak_counts_consecutive_active_weeks():
    """Три недели подряд с активностью → серия 3."""
    events = _ev(_MON - timedelta(days=14), _MON - timedelta(days=7), _WED)
    state = retention_service.compute_state(events, today_msk=_WED)
    assert state["weekly_streak"] == 3
    assert state["best_weekly_streak"] == 3
    assert state["current_week_active"] is True


def test_gap_week_breaks_streak():
    """Пропущенная неделя обрывает серию — считается только свежий прогон."""
    events = _ev(
        _MON - timedelta(days=28),  # -4 недели
        _MON - timedelta(days=21),  # -3 недели
        # -2 недели пропущена
        _MON - timedelta(days=7),   # прошлая неделя
        _WED,                       # текущая
    )
    state = retention_service.compute_state(events, today_msk=_WED)
    assert state["weekly_streak"] == 2, "серия должна считаться от разрыва, а не за всё время"
    assert state["best_weekly_streak"] == 2


def test_monday_grace_keeps_streak_before_first_activity():
    """Понедельник, ученик ещё ничего не сделал на этой неделе — серия
    держится за прошлую неделю, а не обнуляется.

    Без этой оговорки серия всей школы визуально обнулялась бы каждый
    понедельник в 00:00, до того как у ученика была возможность что-то
    сделать."""
    events = _ev(_MON - timedelta(days=14), _MON - timedelta(days=7))
    state = retention_service.compute_state(events, today_msk=_MON)
    assert state["weekly_streak"] == 2
    assert state["current_week_active"] is False


def test_streak_zero_after_two_silent_weeks():
    """Ни текущая, ни прошлая неделя не активны → серия 0, но рекорд остаётся."""
    events = _ev(_MON - timedelta(days=21), _MON - timedelta(days=14))
    state = retention_service.compute_state(events, today_msk=_WED)
    assert state["weekly_streak"] == 0
    assert state["best_weekly_streak"] == 2, "рекорд не отбирается при обрыве"


def test_sunday_and_monday_are_different_weeks():
    """Воскресенье и следующий понедельник — РАЗНЫЕ недели (границы ISO).

    Ошибка на этой границе дала бы серию 1 вместо 2 (или наоборот) у всех,
    кто занимается по выходным."""
    events = _ev(_SUN, _SUN + timedelta(days=1))
    state = retention_service.compute_state(events, today_msk=_SUN + timedelta(days=1))
    assert state["weekly_streak"] == 2


def test_many_days_in_one_week_is_still_one_week():
    """Пять дней активности внутри одной недели — это серия 1, не 5."""
    events = _ev(_MON, _MON + timedelta(days=1), _WED, _MON + timedelta(days=3), _MON + timedelta(days=4))
    state = retention_service.compute_state(events, today_msk=_MON + timedelta(days=4))
    assert state["weekly_streak"] == 1
    assert state["current_week_days"] == 5
    assert state["current_week_items"] == 5


def test_same_item_on_two_days_counts_once():
    """Одно задание, верно сданное в два разных дня, — один элемент.

    Иначе объёмные вехи («50 шагов») набирались бы повторными сдачами одного
    и того же задания, а метрика дашборда рядом считает DISTINCT."""
    events = [(_MON, "task", 7), (_WED, "task", 7)]
    state = retention_service.compute_state(events, today_msk=_WED)
    assert state["items_total"] == 1
    assert state["current_week_items"] == 1
    assert state["current_week_days"] == 2, "дни активности при этом РАЗНЫЕ"


def test_empty_history_is_zero_not_error():
    state = retention_service.compute_state([], today_msk=_WED)
    assert state["weekly_streak"] == 0
    assert state["items_total"] == 0
    assert state["last_active_date"] is None


def test_broken_condition_never_awards():
    """Битое или незнакомое условие в каталоге — НЕ «достижение получено».

    Опечатка в данных иначе раздала бы веху всей школе."""
    state = retention_service.compute_state(_ev(_WED), today_msk=_WED)
    assert retention_service._is_earned({"type": "weekly_streak"}, state) is False
    assert retention_service._is_earned({"type": "weekly_streak", "weeks": "3"}, state) is False
    assert retention_service._is_earned({"type": "unknown_rule", "n": 1}, state) is False
    assert retention_service._is_earned("не объект", state) is False
    assert retention_service._is_earned({"type": "between_lessons_items", "count": 0}, state) is False


def test_streak_milestone_survives_broken_streak():
    """Веха, взятая по рекорду, не отбирается после обрыва серии."""
    events = _ev(_MON - timedelta(days=35), _MON - timedelta(days=28), _MON - timedelta(days=21))
    state = retention_service.compute_state(events, today_msk=_WED)
    assert state["weekly_streak"] == 0
    assert retention_service._is_earned({"type": "weekly_streak", "weeks": 3}, state) is True


# ====================== Слой 2: определение события в БД ======================


async def _new_user(db, *, name: str) -> tuple[int, str]:
    u = Users(
        email=f"{_TAG}-{name}-{random.randint(10**9, 10**10)}@example.com",
        password_hash=None,
        full_name=f"{_TAG}-{name}",
        tg_id=None,
    )
    db.add(u)
    await db.flush()
    await identity_link_service.upsert_identity(db, u.id, "email", u.email)
    token, _, _ = await create_session(db, user_id=u.id)
    await db.commit()
    return u.id, token


async def _new_course(db, title: str) -> int:
    return (
        await db.execute(
            text("INSERT INTO courses (title, access_level) VALUES (:t, 'self_guided') RETURNING id"),
            {"t": title},
        )
    ).scalar()


async def _enroll(db, *, student_id: int, course_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO user_courses (user_id, course_id, is_active) "
            "VALUES (:u, :c, true) ON CONFLICT DO NOTHING"
        ),
        {"u": student_id, "c": course_id},
    )
    await db.commit()


async def _new_task(db, *, course_id: int, uid: str) -> int:
    difficulty_id = (
        await db.execute(text("SELECT id FROM difficulties ORDER BY id LIMIT 1"))
    ).scalar()
    assert difficulty_id is not None, "нет difficulties — граф не собрать"
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA", "stem": f"{_TAG} условие {uid}"}),
                "sr": json.dumps({"max_score": 10, "accepted_answers": [f"{_TAG}-{uid}"]}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"{_TAG}-{uid}-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()


async def _submit(
    db, *, student_id: int, task_id: int, course_id: int, submitted_at: datetime,
    is_correct: bool = True, source_system: str = "test", cancelled: bool = False,
) -> None:
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'test') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    if cancelled:
        await db.execute(
            text("UPDATE attempts SET cancelled_at = :ts WHERE id = :a"),
            {"ts": submitted_at, "a": attempt_id},
        )
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, 0, :ts, :src)"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct,
            "ts": submitted_at, "src": source_system,
        },
    )
    await db.commit()


async def _lesson(
    db, *, student_id: int, teacher_id: int, scheduled_at: datetime, duration_minutes: int = 60,
) -> int:
    occ = LessonOccurrence(
        slot_id=None, teacher_id=teacher_id, scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
    )
    db.add(occ)
    await db.flush()
    db.add(
        LessonOccurrenceParticipant(
            occurrence_id=occ.id, student_id=student_id, status="scheduled",
        )
    )
    occ_id = occ.id
    await db.commit()
    return occ_id


@pytest.mark.asyncio
async def test_in_class_work_does_not_count_as_between_lessons(db):
    """Ответ, сданный ВО ВРЕМЯ урока, в серию не идёт.

    Это и есть отличие от существующей суточной серии `/me/streak`: та
    считает любую сдачу, поэтому удержание между занятиями ею не измеряется."""
    student_id, _ = await _new_user(db, name="student")
    teacher_id, _ = await _new_user(db, name="teacher")
    course_id = await _new_course(db, f"{_TAG} курс")
    await _enroll(db, student_id=student_id, course_id=course_id)

    lesson_at = datetime.now(UTC) - timedelta(days=1)
    await _lesson(db, student_id=student_id, teacher_id=teacher_id, scheduled_at=lesson_at)

    in_class = await _new_task(db, course_id=course_id, uid="in-class")
    outside = await _new_task(db, course_id=course_id, uid="outside")
    # Внутри окна занятия (+20 минут от начала при длительности 60).
    await _submit(db, student_id=student_id, task_id=in_class, course_id=course_id,
                  submitted_at=lesson_at + timedelta(minutes=20))
    # Вне окна (через 5 часов после начала).
    await _submit(db, student_id=student_id, task_id=outside, course_id=course_id,
                  submitted_at=lesson_at + timedelta(hours=5))

    events = (await retention_service.load_events(db, student_ids=[student_id]))[student_id]
    item_ids = {item_id for _d, _kind, item_id in events}
    assert outside in item_ids
    assert in_class not in item_ids, "работа на уроке попала в активность между занятиями"


@pytest.mark.asyncio
async def test_manual_wrong_and_cancelled_are_excluded(db):
    """Ручная отметка преподавателя, неверный ответ и отменённая попытка
    в активность между занятиями не идут — те же условия, что у метрики
    `between_lessons` дашборда."""
    student_id, _ = await _new_user(db, name="student")
    course_id = await _new_course(db, f"{_TAG} курс")
    await _enroll(db, student_id=student_id, course_id=course_id)
    now = datetime.now(UTC) - timedelta(hours=2)

    good = await _new_task(db, course_id=course_id, uid="good")
    manual = await _new_task(db, course_id=course_id, uid="manual")
    wrong = await _new_task(db, course_id=course_id, uid="wrong")
    cancelled = await _new_task(db, course_id=course_id, uid="cancelled")

    await _submit(db, student_id=student_id, task_id=good, course_id=course_id, submitted_at=now)
    await _submit(db, student_id=student_id, task_id=manual, course_id=course_id,
                  submitted_at=now, source_system="manual_teacher")
    await _submit(db, student_id=student_id, task_id=wrong, course_id=course_id,
                  submitted_at=now, is_correct=False)
    await _submit(db, student_id=student_id, task_id=cancelled, course_id=course_id,
                  submitted_at=now, cancelled=True)

    events = (await retention_service.load_events(db, student_ids=[student_id]))[student_id]
    item_ids = {item_id for _d, _kind, item_id in events}
    assert item_ids == {good}


@pytest.mark.asyncio
async def test_retention_endpoint_returns_state_and_requires_auth(db, client):
    """`GET /me/retention` отдаёт своё состояние авторизованному и 401 гостю."""
    student_id, token = await _new_user(db, name="student")
    course_id = await _new_course(db, f"{_TAG} курс")
    await _enroll(db, student_id=student_id, course_id=course_id)
    task_id = await _new_task(db, course_id=course_id, uid="ep")
    await _submit(db, student_id=student_id, task_id=task_id, course_id=course_id,
                  submitted_at=datetime.now(UTC) - timedelta(hours=1))

    anon = await client.get("/api/v1/me/retention")
    assert anon.status_code == 401

    resp = await client.get(
        "/api/v1/me/retention", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["weekly_streak"] == 1
    assert body["current_week_active"] is True
    assert body["items_between_lessons_total"] == 1
    # Веха «Неделя между занятиями» выполнена и видна СРАЗУ, не дожидаясь тика.
    assert any(a["name"] == "Неделя между занятиями" for a in body["achievements"])
    # Соревновательных данных в ответе нет вообще — механики неcоревновательные.
    assert "peers" not in body and "rank" not in body and "leaderboard" not in body


@pytest.mark.asyncio
async def test_award_tick_is_idempotent(db, db_session_factory):
    """Повторный тик не двоит вехи (PK + ON CONFLICT DO NOTHING)."""
    student_id, _ = await _new_user(db, name="student")
    course_id = await _new_course(db, f"{_TAG} курс")
    await _enroll(db, student_id=student_id, course_id=course_id)
    task_id = await _new_task(db, course_id=course_id, uid="award")
    await _submit(db, student_id=student_id, task_id=task_id, course_id=course_id,
                  submitted_at=datetime.now(UTC) - timedelta(hours=1))

    first = await retention_achievements_cron_tick(session_factory=db_session_factory)
    assert first["locked"] is True

    rows_after_first = (
        await db.execute(
            text("SELECT count(*) FROM user_achievements WHERE user_id = :u"),
            {"u": student_id},
        )
    ).scalar()
    assert rows_after_first >= 1, "веха «Неделя между занятиями» должна зафиксироваться"

    await retention_achievements_cron_tick(session_factory=db_session_factory)
    rows_after_second = (
        await db.execute(
            text("SELECT count(*) FROM user_achievements WHERE user_id = :u"),
            {"u": student_id},
        )
    ).scalar()
    assert rows_after_second == rows_after_first, "повторный тик задвоил вехи"


@pytest.mark.asyncio
async def test_earned_at_appears_after_tick(db, db_session_factory):
    """До тика веха видна с `earned_at = None`, после тика — с датой."""
    student_id, _ = await _new_user(db, name="student")
    course_id = await _new_course(db, f"{_TAG} курс")
    await _enroll(db, student_id=student_id, course_id=course_id)
    task_id = await _new_task(db, course_id=course_id, uid="earned")
    await _submit(db, student_id=student_id, task_id=task_id, course_id=course_id,
                  submitted_at=datetime.now(UTC) - timedelta(hours=1))

    before = await retention_service.get_retention(db, student_id=student_id)
    week_badge = [a for a in before["achievements"] if a["name"] == "Неделя между занятиями"]
    assert week_badge and week_badge[0]["earned_at"] is None

    await retention_achievements_cron_tick(session_factory=db_session_factory)

    after = await retention_service.get_retention(db, student_id=student_id)
    week_badge = [a for a in after["achievements"] if a["name"] == "Неделя между занятиями"]
    assert week_badge and week_badge[0]["earned_at"] is not None
