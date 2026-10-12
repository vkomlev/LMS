"""Автопродление лимита попыток при первой блокировке (tsk-1309).

**Зачем.** Разбор 12.10 (`docs/proposals/courses-audit-insights-2026-10.md`):
за 90 дней 421 блокировка по лимиту, 98 % из них сотрудник закрыл продлением,
после продления 90 % учеников задание решили. Лимит 3 не останавливал, а
заставлял человека нажать «продлить» и держал ученика в ожидании (до 10 ч у
каждого десятого).

**Правило.** Первая блокировка пары (ученик, задание) снимается сама: лимит
поднимается на базовый (как режим «выдать столько же» у преподавателя).
Вторая блокировка той же пары идёт человеку прежней заявкой `blocked_limit` —
сигнал «ученик буксует» не теряется, просто приходит после шести неудач, а не
трёх.

**След для преподавателя.** Закрытая заявка `blocked_limit` с
`context_json.trigger = 'auto_extend'`: в очередь не попадает (закрыта), в
ленте активности видна отдельной строкой «выдано автоматически». Она же —
признак «автопродление по паре уже было», отдельной таблицы не нужно.

Не продлевается: квиз-вопросы и диагностические задания (одна попытка — это
замер, повтор бессмысленен), задание с уже открытой заявкой (ей занимается
человек).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.schemas.task_content import QUIZ_TASK_TYPES
from app.services import inbox_service
from app.services.help_requests_service import resolve_assigned_teacher
from app.services.learning_engine_service import DEFAULT_MAX_ATTEMPTS
from app.services.learning_events_service import record_task_limit_override

logger = logging.getLogger(__name__)

AUTO_EXTEND_TRIGGER = "auto_extend"


async def try_auto_extend(
    db: AsyncSession,
    *,
    student_id: int,
    task_id: int,
    course_id: Optional[int],
    attempt_id: Optional[int],
    attempts_used: int,
    attempts_limit_effective: int,
) -> Optional[int]:
    """Снять первую блокировку пары автоматически.

    :returns: новый эффективный лимит, если продлили; ``None`` — продление не
        положено, вызывающий создаёт обычную заявку ``blocked_limit``.
    Коммит — за вызывающим (как у ``get_or_create_blocked_limit_help_request``).
    """
    if not Settings().attempts_auto_extend_enabled:
        return None

    task = (
        await db.execute(
            text(
                "SELECT task_content->>'type' AS typ, max_attempts, "
                "       solution_rules->>'diagnostic' = 'true' AS diagnostic "
                "FROM tasks WHERE id = :task_id"
            ),
            {"task_id": task_id},
        )
    ).mappings().fetchone()
    if task is None or task["typ"] in QUIZ_TASK_TYPES or task["diagnostic"]:
        return None

    # Та же сериализация по паре, что у заявки и ручного продления.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": student_id, "k2": task_id},
    )
    seen = (
        await db.execute(
            text("""
                SELECT 1 FROM help_requests
                WHERE student_id = :s AND task_id = :t AND request_type = 'blocked_limit'
                  AND (status = 'open' OR context_json->>'trigger' = :trigger)
                LIMIT 1
            """),
            {"s": student_id, "t": task_id, "trigger": AUTO_EXTEND_TRIGGER},
        )
    ).fetchone()
    if seen is not None:
        return None

    base = int(task["max_attempts"]) if task["max_attempts"] is not None else DEFAULT_MAX_ATTEMPTS
    new_limit = attempts_limit_effective + base
    reason = f"Автопродление: +{base} (стало {new_limit})"

    await db.execute(
        text("""
            INSERT INTO student_task_limit_override
            (student_id, task_id, max_attempts_override, reason, updated_by, updated_at)
            VALUES (:s, :t, :lim, :reason, NULL, now())
            ON CONFLICT (student_id, task_id)
            DO UPDATE SET max_attempts_override = EXCLUDED.max_attempts_override,
                          reason = EXCLUDED.reason, updated_by = NULL, updated_at = now()
        """),
        {"s": student_id, "t": task_id, "lim": new_limit, "reason": reason},
    )
    await record_task_limit_override(
        db, student_id, task_id, new_limit, reason, None,
        mode=AUTO_EXTEND_TRIGGER,
        previous_max_attempts_override=attempts_limit_effective,
        base_attempts_added=base,
    )
    context = {
        "trigger": AUTO_EXTEND_TRIGGER,
        "attempts_used": attempts_used,
        "attempts_limit_effective": attempts_limit_effective,
        "base_attempts_added": base,
        "new_limit": new_limit,
    }
    await db.execute(
        text("""
            INSERT INTO help_requests
            (status, request_type, auto_created, context_json, student_id, task_id,
             course_id, attempt_id, assigned_teacher_id, created_at, updated_at,
             closed_at, resolution_comment)
            VALUES ('closed', 'blocked_limit', true, CAST(:ctx AS jsonb), :s, :t,
                    :course_id, :attempt_id, :assigned, now(), now(), now(), :reason)
        """),
        {
            "ctx": json.dumps(context),
            "s": student_id,
            "t": task_id,
            "course_id": course_id,
            "attempt_id": attempt_id,
            "assigned": await resolve_assigned_teacher(db, student_id, course_id),
            "reason": reason,
        },
    )
    await inbox_service.create_for_user(
        db,
        user_id=student_id,
        kind="task_limit_override",
        title="Ещё попытки по заданию",
        content=(
            f"Добавили {base} попытки. Перед следующей загляните в материал урока "
            "или спросите ИИ-наставника — так быстрее найти ошибку."
        ),
        payload={"task_id": task_id, "max_attempts_override": new_limit, "mode": AUTO_EXTEND_TRIGGER},
        created_by=None,
    )
    logger.info(
        "attempt auto-extend: student=%s task=%s limit %s -> %s",
        student_id, task_id, attempts_limit_effective, new_limit,
    )
    return new_limit
