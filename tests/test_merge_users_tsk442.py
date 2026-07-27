"""tsk-442: слияние двух учётных записей (scripts/merge_users.py).

Покрывает три класса переноса на реальных FK/уникальных ограничениях БД:
- простой FK со своим `id` PK (social_posts.user_id) — прямой UPDATE;
- составной PK с риском конфликта (user_courses(user_id, course_id)) —
  target уже имеет строку по тому же course_id → source-строка должна
  быть удалена, а не перенесена (иначе PK violation);
- сессия (user_session) — не переносится, а удаляется у source.

Плюс: деактивация source (is_active=false, merged_into_user_id=target),
защита от повторного/обратного слияния уже неактивной учётки, и НЕ-перенос
append-only аудит-логов (audit_event/attendance_event) — регрессия на
реальный прод-инцидент: `audit_event` физически защищена DB-триггером
(`RAISE EXCEPTION 'audit_event is append-only'`), первый боевой прогон
автослияния упал именно на попытке UPDATE этой таблицы.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models.attendance_event import AttendanceEvent
from app.models.audit_event import AuditEvent
from app.models.courses import Courses
from app.models.lesson_occurrence import LessonOccurrence
from app.models.social_posts import SocialPosts
from app.models.user_courses import UserCourses
from app.models.user_session import UserSession
from app.models.users import Users

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "merge_users_script", Path(__file__).resolve().parent.parent / "scripts" / "merge_users.py",
)
merge_users = importlib.util.module_from_spec(_SPEC)
sys.modules["merge_users_script"] = merge_users
_SPEC.loader.exec_module(merge_users)


async def _create_student(db, prefix: str) -> int:
    suffix = random.randint(10**8, 10**10)
    u = Users(email=None, password_hash=None, full_name=f"{prefix}-user", tg_id=None)
    db.add(u)
    await db.flush()
    return u.id


async def _create_course(db, prefix: str) -> int:
    c = Courses(title=f"{prefix}-{random.randint(10**8, 10**10)}", access_level="self_guided")
    db.add(c)
    await db.flush()
    return c.id


@pytest.mark.asyncio
async def test_merge_moves_simple_fk_and_skips_conflicting_and_drops_sessions(db):
    source_id = await _create_student(db, "tsk442-src")
    target_id = await _create_student(db, "tsk442-tgt")
    course_shared_id = await _create_course(db, "tsk442-shared")
    course_only_source_id = await _create_course(db, "tsk442-onlysrc")

    # Простой FK: одна строка у source, должна переехать к target как есть.
    db.add(SocialPosts(user_id=source_id, content="tsk442 test post"))

    # Конфликт на составном PK: у source и target уже есть строка по ОДНОМУ
    # и тому же курсу — source-строка должна быть удалена (не перенесена).
    db.add(UserCourses(user_id=source_id, course_id=course_shared_id))
    db.add(UserCourses(user_id=target_id, course_id=course_shared_id))
    # А эта — по курсу, которого у target нет — должна переехать.
    db.add(UserCourses(user_id=source_id, course_id=course_only_source_id))

    # Сессия source — не переносится, должна быть удалена.
    db.add(
        UserSession(
            id=uuid4(), user_id=source_id, token_hash=b"x" * 32,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    await db.commit()

    await merge_users._apply(db, source_id, target_id)
    await db.commit()

    # social_posts переехал.
    sp = (
        await db.execute(
            text("SELECT user_id FROM social_posts WHERE content = 'tsk442 test post'"),
        )
    ).scalar_one_or_none()
    assert sp == target_id

    # user_courses: конфликтующая строка source удалена (осталась только у target),
    # неконфликтующая — переехала к target.
    rows = (
        await db.execute(
            text("SELECT user_id, course_id FROM user_courses WHERE course_id IN (:a, :b)"),
            {"a": course_shared_id, "b": course_only_source_id},
        )
    ).all()
    pairs = {(r.user_id, r.course_id) for r in rows}
    assert pairs == {(target_id, course_shared_id), (target_id, course_only_source_id)}

    # Сессия source удалена, у target сессий не появилось.
    session_count = (
        await db.execute(
            text("SELECT COUNT(*) FROM user_session WHERE user_id IN (:s, :t)"),
            {"s": source_id, "t": target_id},
        )
    ).scalar_one()
    assert session_count == 0

    # Деактивация.
    row = await merge_users._fetch_user(db, source_id)
    assert row.is_active is False
    assert row.merged_into_user_id == target_id


@pytest.mark.asyncio
async def test_merge_does_not_touch_append_only_audit_logs(db):
    """Регрессия на прод-инцидент: audit_event/attendance_event НЕ
    переносятся при слиянии — остаются на source (учётка деактивируется,
    но не удаляется, FK остаётся валиден). audit_event вдобавок физически
    заблокирована DB-триггером на UPDATE — если бы merge_users попытался
    её тронуть, вся транзакция слияния упала бы."""
    source_id = await _create_student(db, "tsk442-audit-src")
    target_id = await _create_student(db, "tsk442-audit-tgt")

    db.add(AuditEvent(user_id=source_id, event_type="user.login"))

    occurrence = LessonOccurrence(
        slot_id=None, teacher_id=target_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        duration_minutes=60,
    )
    db.add(occurrence)
    await db.flush()
    db.add(AttendanceEvent(occurrence_id=occurrence.id, actor_user_id=source_id, action="joined"))
    await db.commit()

    await merge_users._apply(db, source_id, target_id)
    await db.commit()

    audit_user_id = (
        await db.execute(
            text("SELECT user_id FROM audit_event WHERE event_type = 'user.login' AND user_id = :s"),
            {"s": source_id},
        )
    ).scalar_one_or_none()
    assert audit_user_id == source_id  # осталась на source, не переехала

    attendance_actor = (
        await db.execute(
            text("SELECT actor_user_id FROM attendance_event WHERE occurrence_id = :o"),
            {"o": occurrence.id},
        )
    ).scalar_one_or_none()
    assert attendance_actor == source_id  # осталась на source, не переехала

    # Учётка при этом всё равно корректно деактивирована.
    row = await merge_users._fetch_user(db, source_id)
    assert row.is_active is False
    assert row.merged_into_user_id == target_id


@pytest.mark.asyncio
async def test_run_refuses_to_merge_already_inactive_source(db, db_session_factory, capsys):
    source_id = await _create_student(db, "tsk442-inactive")
    target_id = await _create_student(db, "tsk442-target2")
    other_target_id = await _create_student(db, "tsk442-target3")
    await db.execute(
        text("UPDATE users SET is_active=false, merged_into_user_id=:t WHERE id=:s"),
        {"t": target_id, "s": source_id},
    )
    await db.commit()

    await merge_users._run(
        source_id, other_target_id, apply=True, session_factory=db_session_factory,
    )

    row = await merge_users._fetch_user(db, source_id)
    assert row.merged_into_user_id == target_id  # не перезаписалось
