"""
Learning Engine V1: запись событий в learning_events и операции прогресса.

- Запись событий (help_requested, task_limit_override) с антидублированием.
- Отметка материала как completed (student_material_progress).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

HELP_DEDUPE_MINUTES = 5
HINT_DEDUPE_MINUTES = 5


async def record_help_requested(
    db: AsyncSession,
    student_id: int,
    task_id: int,
    message: Optional[str] = None,
) -> tuple[int, bool]:
    """
    Записать событие help_requested. Дедуп: если за последние N минут есть
    событие с тем же (student_id, task_id, message), вернуть его event_id и deduplicated=True.

    Атомарность дедупа: advisory lock по (student_id, task_id) сериализует
    параллельные вызовы, чтобы не вставить дубликат.

    Returns:
        (event_id, deduplicated)
    """
    # Сериализация по (student_id, task_id) для атомарного check-then-insert
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": student_id, "k2": task_id},
    )

    since = datetime.now(timezone.utc) - timedelta(minutes=HELP_DEDUPE_MINUTES)
    msg_normalized = (message or "").strip() or None

    # Проверка дубликата: то же student_id, task_id, payload.message за окно
    r = await db.execute(
        text("""
            SELECT id FROM learning_events
            WHERE student_id = :student_id
              AND event_type = 'help_requested'
              AND created_at >= :since
              AND (
                (payload->>'task_id')::int = :task_id
                AND (payload->>'message' IS NOT DISTINCT FROM :msg)
              )
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {
            "student_id": student_id,
            "task_id": task_id,
            "msg": msg_normalized,
            "since": since,
        },
    )
    row = r.fetchone()
    if row is not None:
        return (int(row[0]), True)

    payload: dict[str, Any] = {"task_id": task_id}
    if message is not None:
        payload["message"] = message[:2000] if len(message) > 2000 else message

    r = await db.execute(
        text("""
            INSERT INTO learning_events (student_id, event_type, payload, created_at)
            VALUES (:student_id, 'help_requested', CAST(:payload AS jsonb), now())
            RETURNING id
        """),
        {"student_id": student_id, "payload": json.dumps(payload)},
    )
    event_id = r.scalar()
    return (int(event_id), False)


async def record_hint_open(
    db: AsyncSession,
    student_id: int,
    attempt_id: int,
    task_id: int,
    hint_type: str,
    hint_index: int,
    action: str,
    source: str,
) -> tuple[int, bool]:
    """
    Записать событие открытия подсказки (hint_open). Дедуп по ключу
    (attempt_id, task_id, hint_type, hint_index, action) в окне HINT_DEDUPE_MINUTES.

    Returns:
        (event_id, deduplicated)
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
        {"k1": attempt_id, "k2": task_id},
    )

    since = datetime.now(timezone.utc) - timedelta(minutes=HINT_DEDUPE_MINUTES)

    r = await db.execute(
        text("""
            SELECT id FROM learning_events
            WHERE student_id = :student_id
              AND event_type = 'hint_open'
              AND created_at >= :since
              AND (payload->>'attempt_id')::int = :attempt_id
              AND (payload->>'task_id')::int = :task_id
              AND payload->>'hint_type' = :hint_type
              AND (payload->>'hint_index')::int = :hint_index
              AND payload->>'action' = :action
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {
            "student_id": student_id,
            "since": since,
            "attempt_id": attempt_id,
            "task_id": task_id,
            "hint_type": hint_type,
            "hint_index": hint_index,
            "action": action,
        },
    )
    row = r.fetchone()
    if row is not None:
        return (int(row[0]), True)

    payload: dict[str, Any] = {
        "attempt_id": attempt_id,
        "task_id": task_id,
        "hint_type": hint_type,
        "hint_index": hint_index,
        "action": action,
        "source": source[:500] if len(source) > 500 else source,
    }

    r = await db.execute(
        text("""
            INSERT INTO learning_events (student_id, event_type, payload, created_at)
            VALUES (:student_id, 'hint_open', CAST(:payload AS jsonb), now())
            RETURNING id
        """),
        {"student_id": student_id, "payload": json.dumps(payload)},
    )
    event_id = r.scalar()
    return (int(event_id), False)


async def get_hint_open_counts(
    db: AsyncSession,
    *,
    task_id: Optional[int] = None,
    user_id: Optional[int] = None,
    task_ids: Optional[list[int]] = None,
) -> tuple[int, int, int]:
    """
    Подсчёт событий hint_open для аналитики. Возвращает (total, text_count, video_count).
    """
    conditions = ["event_type = 'hint_open'"]
    params: dict[str, Any] = {}
    if task_id is not None:
        conditions.append("(payload->>'task_id')::int = :task_id")
        params["task_id"] = task_id
    if task_ids is not None and len(task_ids) > 0:
        conditions.append("(payload->>'task_id')::int = ANY(:task_ids)")
        params["task_ids"] = task_ids
    if user_id is not None:
        conditions.append("student_id = :user_id")
        params["user_id"] = user_id

    where_sql = " AND ".join(conditions)
    r = await db.execute(
        text(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE payload->>'hint_type' = 'text') AS text_count,
                COUNT(*) FILTER (WHERE payload->>'hint_type' = 'video') AS video_count
            FROM learning_events
            WHERE {where_sql}
        """),
        params,
    )
    row = r.fetchone()
    if not row:
        return (0, 0, 0)
    return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


async def record_task_limit_override(
    db: AsyncSession,
    student_id: int,
    task_id: int,
    max_attempts_override: int,
    reason: Optional[str],
    updated_by: int,
) -> None:
    """
    Записать событие task_limit_override в learning_events.
    Вызывается после upsert в student_task_limit_override.
    """
    payload: dict[str, Any] = {
        "task_id": task_id,
        "max_attempts_override": max_attempts_override,
        "updated_by": updated_by,
    }
    if reason is not None:
        payload["reason"] = reason

    await db.execute(
        text("""
            INSERT INTO learning_events (student_id, event_type, payload, created_at)
            VALUES (:student_id, 'task_limit_override', CAST(:payload AS jsonb), now())
        """),
        {"student_id": student_id, "payload": json.dumps(payload)},
    )


async def set_material_completed(
    db: AsyncSession,
    student_id: int,
    material_id: int,
) -> datetime:
    """
    Идемпотентный upsert в student_material_progress: status='completed', completed_at=now().
    Возвращает completed_at (текущее значение после upsert).
    """
    await db.execute(
        text("""
            INSERT INTO student_material_progress (student_id, material_id, status, completed_at)
            VALUES (:student_id, :material_id, 'completed', now())
            ON CONFLICT (student_id, material_id)
            DO UPDATE SET status = 'completed', completed_at = COALESCE(
                student_material_progress.completed_at, now()
            )
        """),
        {"student_id": student_id, "material_id": material_id},
    )
    r = await db.execute(
        text("""
            SELECT completed_at FROM student_material_progress
            WHERE student_id = :student_id AND material_id = :material_id
        """),
        {"student_id": student_id, "material_id": material_id},
    )
    row = r.fetchone()
    return row[0] if row and row[0] else datetime.now(timezone.utc)
