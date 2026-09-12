"""tsk-888: сводный счётчик для методиста — очерёдность внимания (tsk-648) и
«пора усложнить» (tsk-649) по всей школе за период, а не по одному occurrence.

Проверяем, что агрегация правильно переиспользует существующие загрузчики и
пороги (не переизобретает их), и что окно периода реально режет старые/новые
события — то самое свойство, которого нет у per-occurrence списка.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services import teacher_lesson_summary_service as svc
from tests.test_teacher_lesson_summary_tsk022_410 import (
    _create_occurrence_with_participant,
    _enroll_student,
    _insert_help_request,
    _insert_task_result,
    _new_course,
    _new_task,
    _new_user,
)

UTC = timezone.utc


async def _idle_episode(
    db, *, occurrence_id: int, student_id: int, silent_since: datetime, resolved_at: datetime,
) -> None:
    await db.execute(
        text(
            "INSERT INTO lesson_idle_episode "
            "  (occurrence_id, student_id, kind, silent_since, detected_at, resolved_at) "
            "VALUES (:o, :s, 'idle', :since, :since, :resolved)"
        ),
        {"o": occurrence_id, "s": student_id, "since": silent_since, "resolved": resolved_at},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_idle_within_window_is_counted(db):
    """Молчал НА занятии, которое случилось за окно, — попадает в счётчик."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888a")
    student_id, _ = await _new_user(db, role="student", name="a888a")
    now = datetime.now(UTC)

    occ_at = now - timedelta(days=2)
    occ = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=occ_at, status="confirmed",
    )
    await _idle_episode(
        db, occurrence_id=occ, student_id=student_id,
        silent_since=occ_at + timedelta(minutes=5), resolved_at=occ_at + timedelta(minutes=20),
    )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["participants_in_window"] == 1
    assert summary["students_flagged"] == 1
    assert summary["by_reason"] == {"idle_last_lesson": 1}


@pytest.mark.asyncio
async def test_lesson_outside_window_is_not_counted(db):
    """Занятие ЗА пределами окна — не участвует в счётчике вовсе (не только
    его повод: сам участник не попадает в знаменатель)."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888b")
    student_id, _ = await _new_user(db, role="student", name="a888b")
    now = datetime.now(UTC)

    occ_at = now - timedelta(days=10)
    occ = await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id, scheduled_at=occ_at, status="confirmed",
    )
    await _idle_episode(
        db, occurrence_id=occ, student_id=student_id,
        silent_since=occ_at + timedelta(minutes=5), resolved_at=occ_at + timedelta(minutes=20),
    )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["participants_in_window"] == 0
    assert summary["students_flagged"] == 0


@pytest.mark.asyncio
async def test_stuck_within_window_is_counted(db):
    """Стоит на задании (3 неверные попытки, не решено) — тот же порог, что
    в per-occurrence сводке (``stuck_tasks_service``), просто за окно школы."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888c")
    student_id, _ = await _new_user(db, role="student", name="a888c")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk888 стоит")
    task_id = await _new_task(db, course_id=course_id, uid="a888c")

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=1), status="confirmed",
    )
    for i in range(3):
        await _insert_task_result(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=False, submitted_at=now - timedelta(hours=i + 1),
        )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["students_flagged"] == 1
    assert summary["by_reason"] == {"stuck": 1}


@pytest.mark.asyncio
async def test_single_miss_in_window_is_not_a_reason(db):
    """Один пропуск в окне — не повод, тот же порог `_MISSED_STREAK_FOR_ATTENTION`,
    что в tsk-648."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888d")
    student_id, _ = await _new_user(db, role="student", name="a888d")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=2), status="no_show",
    )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["participants_in_window"] == 1
    assert summary["students_flagged"] == 0


@pytest.mark.asyncio
async def test_two_misses_in_a_row_within_window_is_counted(db):
    """Два пропуска подряд, ОБА в окне, — засчитываются поводом окна."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888e")
    student_id, _ = await _new_user(db, role="student", name="a888e")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=5), status="no_show",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=2), status="no_show",
    )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["students_flagged"] == 1
    assert summary["by_reason"] == {"missed_last_lesson": 1}


@pytest.mark.asyncio
async def test_old_streak_outside_window_does_not_look_fresh_every_week(db):
    """Серия пропусков, случившаяся ЦЕЛИКОМ до начала окна, не должна каждую
    неделю выглядеть новым поводом — иначе накопительный счётчик превращается
    в тот самый вечный рейтинг, которого просила избежать постановка tsk-648.

    Занятие внутри окна у того же ученика есть (иначе он не попал бы в
    знаменатель), но оно НЕ пропущено — свежих поводов нет."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888f")
    student_id, _ = await _new_user(db, role="student", name="a888f")
    now = datetime.now(UTC)

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=20), status="no_show",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=17), status="no_show",
    )
    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=2), status="confirmed",
    )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["students_flagged"] == 0


@pytest.mark.asyncio
async def test_help_asked_within_window_is_counted(db):
    """Заявка помощи, созданная за окно, — повод (тот же COUNT, что в
    ``load_homework_window``, батчем на всех сразу)."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888g")
    student_id, _ = await _new_user(db, role="student", name="a888g")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk888 помощь")
    task_id = await _new_task(db, course_id=course_id, uid="a888g")

    await _create_occurrence_with_participant(
        db, student_id=student_id, teacher_id=teacher_id,
        scheduled_at=now - timedelta(days=3), status="confirmed",
    )
    await _insert_help_request(
        db, student_id=student_id, task_id=task_id, course_id=course_id,
        teacher_id=teacher_id, created_at=now - timedelta(days=1),
    )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["students_flagged"] == 1
    assert summary["by_reason"] == {"help_asked": 1}


@pytest.mark.asyncio
async def test_multiple_students_tally_by_reason(db):
    """Двое разных учеников с разными поводами — счётчик суммирует по
    каждому поводу отдельно, а не сваливает в одну кучу."""
    teacher_id, _ = await _new_user(db, role="teacher", name="a888h")
    silent_id, _ = await _new_user(db, role="student", name="a888h1")
    calm_id, _ = await _new_user(db, role="student", name="a888h2")
    now = datetime.now(UTC)

    occ_at = now - timedelta(days=1)
    occ1 = await _create_occurrence_with_participant(
        db, student_id=silent_id, teacher_id=teacher_id, scheduled_at=occ_at, status="confirmed",
    )
    await _idle_episode(
        db, occurrence_id=occ1, student_id=silent_id,
        silent_since=occ_at + timedelta(minutes=5), resolved_at=occ_at + timedelta(minutes=20),
    )
    await _create_occurrence_with_participant(
        db, student_id=calm_id, teacher_id=teacher_id, scheduled_at=occ_at, status="confirmed",
    )

    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["participants_in_window"] == 2
    assert summary["students_flagged"] == 1
    assert summary["by_reason"] == {"idle_last_lesson": 1}


@pytest.mark.asyncio
async def test_empty_window_returns_zeroes_not_error(db):
    """Без единого занятия за окно — нули, а не деление на пустое множество."""
    summary = await svc.get_school_attention_summary(db, window_days=7)
    assert summary["participants_in_window"] == 0
    assert summary["students_flagged"] == 0
    assert summary["by_reason"] == {}


# ============================ Пора усложнить (школа) ============================


async def _submit_harder(db, *, student_id, task_id, course_id, is_correct, at):
    attempt_id = (
        await db.execute(
            text(
                "INSERT INTO attempts (user_id, course_id, root_course_id, source_system) "
                "VALUES (:u, :c, :c, 'spw_web') RETURNING id"
            ),
            {"u": student_id, "c": course_id},
        )
    ).scalar()
    await db.execute(
        text(
            "INSERT INTO task_results (user_id, task_id, attempt_id, score, max_score, "
            "  is_correct, submitted_at, received_at, count_retry, checked_at, source_system) "
            "VALUES (:u, :t, :a, :sc, 10, :ok, :ts, :ts, 0, :ts, 'spw_web')"
        ),
        {
            "u": student_id, "t": task_id, "a": attempt_id,
            "sc": 10 if is_correct else 0, "ok": is_correct, "ts": at,
        },
    )
    await db.commit()


async def _harder_task(db, *, course_id: int, difficulty_id: int = 3) -> int:
    return (
        await db.execute(
            text(
                "INSERT INTO tasks (task_content, solution_rules, course_id, "
                "  difficulty_id, external_uid, max_score, order_position) "
                "VALUES (CAST(:tc AS jsonb), CAST(:sr AS jsonb), :cid, :did, :uid, 10, 1) "
                "RETURNING id"
            ),
            {
                "tc": json.dumps({"type": "SA", "stem": "tsk888 условие"}),
                "sr": json.dumps({"max_score": 10}),
                "cid": course_id,
                "did": difficulty_id,
                "uid": f"tsk888-{random.randint(10**8, 10**10)}",
            },
        )
    ).scalar()


@pytest.mark.asyncio
async def test_ready_for_harder_school_wide_lists_active_qualifying_student(db):
    """Ученик, проходящий порог tsk-649, виден в школьном списке, если он
    АКТИВНЫЙ (``retention_service.list_active_student_ids`` — запись в
    ``user_courses`` с ``is_active=true``)."""
    student_id, _ = await _new_user(db, role="student", name="a888i")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk888 усложнить")
    await _enroll_student(db, student_id=student_id, course_id=course_id)

    for i in range(25):
        task_id = await _harder_task(db, course_id=course_id)
        await _submit_harder(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=True, at=now - timedelta(days=1, minutes=i),
        )

    summary = await svc.get_school_ready_for_harder_summary(db)
    ids = {row["student_id"] for row in summary["students"]}
    assert student_id in ids
    row = next(r for r in summary["students"] if r["student_id"] == student_id)
    assert row["tasks"] == 25
    assert row["percent"] == 100


@pytest.mark.asyncio
async def test_ready_for_harder_ignores_inactive_student(db):
    """Тот же профиль, но ученик НЕ активен (нет записи в ``user_courses``) —
    в школьный список не попадает: список считается по активным, как и
    остальные школьные замеры (``retention_service``)."""
    student_id, _ = await _new_user(db, role="student", name="a888j")
    now = datetime.now(UTC)
    course_id = await _new_course(db, "tsk888 неактивный")
    # Намеренно НЕ вызываем _enroll_student — нет активной записи user_courses.

    for i in range(25):
        task_id = await _harder_task(db, course_id=course_id)
        await _submit_harder(
            db, student_id=student_id, task_id=task_id, course_id=course_id,
            is_correct=True, at=now - timedelta(days=1, minutes=i),
        )

    summary = await svc.get_school_ready_for_harder_summary(db)
    ids = {row["student_id"] for row in summary["students"]}
    assert student_id not in ids


# ============================ Эндпоинты (гейт) ============================


@pytest.mark.asyncio
async def test_attention_summary_endpoint_denies_teacher(client, db):
    """Гейт методистский: у преподавателя своего расписания эндпоинт не про
    его группу, а про всю школу — доступа нет."""
    _teacher_id, token = await _new_user(db, role="teacher", name="a888k")
    resp = await client.get(
        "/api/v1/methodist/attention-summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_attention_summary_endpoint_ok_for_methodist(client, db):
    """Методист получает те же поля, что отдаёт сервисная функция."""
    _methodist_id, token = await _new_user(db, role="methodist", name="a888l")
    resp = await client.get(
        "/api/v1/methodist/attention-summary",
        params={"window_days": 7},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_days"] == 7
    assert set(body) == {
        "window_days", "window_from", "window_to",
        "participants_in_window", "students_flagged", "by_reason",
    }


@pytest.mark.asyncio
async def test_ready_for_harder_summary_endpoint_ok_for_methodist(client, db):
    _methodist_id, token = await _new_user(db, role="methodist", name="a888m")
    resp = await client.get(
        "/api/v1/methodist/ready-for-harder-summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"window_days", "active_students", "students"}
