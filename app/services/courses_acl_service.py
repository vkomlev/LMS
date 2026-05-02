"""Y-5.2: ACL helper для GET /courses/{id}/materials и /tasks/by-course/{id}.

Параллель к tasks_acl_service / materials_acl_service. Курс-level access:
проверка что user_id зачислен в course_id (или есть ancestor в дереве
user_courses + course_parents).
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser

logger = logging.getLogger(__name__)


async def _user_has_extended_role(db: AsyncSession, user_id: int) -> bool:
    res = await db.execute(
        text(
            "SELECT 1 FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = :uid "
            "  AND r.name IN ('admin','methodist','teacher') "
            "LIMIT 1"
        ),
        {"uid": user_id},
    )
    return res.fetchone() is not None


async def _user_has_course_in_tree(
    db: AsyncSession, user_id: int, course_id: int
) -> bool:
    res = await db.execute(
        text(
            """
            WITH RECURSIVE user_course_tree AS (
                SELECT course_id
                FROM user_courses
                WHERE user_id = :uid AND is_active = true
                UNION ALL
                SELECT cp.course_id
                FROM course_parents cp
                JOIN user_course_tree uct
                  ON cp.parent_course_id = uct.course_id
            )
            SELECT 1 FROM user_course_tree
            WHERE course_id = :tcid
            LIMIT 1
            """
        ),
        {"uid": user_id, "tcid": course_id},
    )
    return res.fetchone() is not None


async def assert_course_access(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    course_id: int,
) -> None:
    """Проверить доступ к course (Y-5.2 fix).

    Raises HTTPException 403 если current_user не имеет права видеть
    список задач/материалов курса.
    is_service / extended-role bypass'ит проверку.
    student имеет доступ если course_id лежит в дереве user_courses.
    """
    if current_user.is_service:
        return

    has_extended = await _user_has_extended_role(db, current_user.id)
    if has_extended:
        return

    in_tree = await _user_has_course_in_tree(db, current_user.id, course_id)
    if not in_tree:
        logger.info(
            "Y-5.2: deny student user_id=%s course_id=%s "
            "(не в дереве user_courses)",
            current_user.id, course_id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к курсу запрещён: вы не зачислены в этот курс",
        )
