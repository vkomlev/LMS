"""tsk-1338: человекочитаемые имена для текстов уведомлений.

Уведомления (`notifications`) читают люди в кабинете и в ботах — голые
номера («Студент 412 завершил курс 17») им ничего не говорят. Здесь собраны
дешёвые точечные запросы «id → подпись», общие для всех сервисов, которые
создают уведомления через `inbox_service.create_for_user`.

Ни одна функция не возвращает номер вместо имени: при отсутствии строки —
нейтральная подпись («Ученик», «Задание», «Курс»).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.task_title import humanize_task_title

logger = logging.getLogger(__name__)

STUDENT_FALLBACK = "Ученик"
TEACHER_FALLBACK = "Преподаватель"


async def user_display_name(
    db: AsyncSession, user_id: Optional[int], *, fallback: str = STUDENT_FALLBACK
) -> str:
    """Имя пользователя: full_name → email → нейтральная подпись (tsk-1338)."""
    if user_id is None:
        return fallback
    row = (
        await db.execute(
            text("SELECT full_name, email FROM users WHERE id = :id"),
            {"id": int(user_id)},
        )
    ).first()
    if row is None:
        return fallback
    name = (row[0] or "").strip()
    if name:
        return name
    email = (row[1] or "").strip()
    return email or fallback


async def task_title(db: AsyncSession, task_id: Optional[int]) -> str:
    """Подпись задания: curated title → условие → external_uid (tsk-1338)."""
    if task_id is None:
        return "Задание"
    row = (
        await db.execute(
            text(
                "SELECT task_content->>'title', task_content->>'stem', external_uid "
                "FROM tasks WHERE id = :id"
            ),
            {"id": int(task_id)},
        )
    ).first()
    if row is None:
        return "Задание"
    return humanize_task_title(int(task_id), row[0], row[1], row[2])


async def material_title(db: AsyncSession, material_id: Optional[int]) -> str:
    """Название материала (tsk-1338)."""
    if material_id is None:
        return "Материал"
    row = (
        await db.execute(
            text("SELECT title FROM materials WHERE id = :id"), {"id": int(material_id)}
        )
    ).first()
    return (row[0] if row and row[0] else None) or "Материал"


async def course_title(db: AsyncSession, course_id: Optional[int]) -> str:
    """Название курса (tsk-1338)."""
    if course_id is None:
        return "Курс"
    row = (
        await db.execute(
            text("SELECT title FROM courses WHERE id = :id"), {"id": int(course_id)}
        )
    ).first()
    return (row[0] if row and row[0] else None) or "Курс"
