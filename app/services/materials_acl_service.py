"""Y-5.1: ACL helper для GET /materials/{id} с cookie-auth.

Параллель к `tasks_acl_service.py` (Y-4 post-S5). Разблокирует SPW frontend:
запрос `GET /api/v1/materials/{id}` через student cookie ранее получал
403 «Invalid or missing API Key» от `Depends(get_db)` (legacy service-key
gate в CRUD router). Теперь cookie auth работает с ACL по дереву
`user_courses` + `course_parents` (recursive).

Правила доступа (тождественны tasks_acl_service):
- `current_user.is_service` (X-API-Key) → bypass (TG_LMS, ContentBackbone CLI).
- Methodist / admin / teacher (любая extended-роль) — bypass.
- Student / без расширенных ролей — material доступен, если его `course_id`
  лежит в дереве `user_courses` пользователя (root или потомок через
  `course_parents`).
- Иначе → 403.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.current_user import CurrentUser

logger = logging.getLogger(__name__)


async def _user_has_extended_role(db: AsyncSession, user_id: int) -> bool:
    """True если у user есть роль admin / methodist / teacher (любая)."""
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
    """True если course_id лежит в дереве user_courses пользователя."""
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


async def assert_material_access(
    db: AsyncSession,
    *,
    current_user: CurrentUser,
    material_course_id: int | None,
) -> None:
    """Проверить доступ к material (Y-5.1 fix).

    Raises HTTPException 403 если current_user не имеет права видеть material.
    is_service / extended-role bypass'ит проверку.
    student имеет доступ если material.course_id лежит в дереве user_courses.
    """
    # Service-key (X-API-Key) — bypass для backward compat (TG_LMS, CB CLI).
    if current_user.is_service:
        return

    has_extended = await _user_has_extended_role(db, current_user.id)
    if has_extended:
        return

    # Student-level: material должен иметь course_id и попадать в дерево user_courses.
    if material_course_id is None:
        logger.info(
            "Y-5.1: material без course_id; student user_id=%s deny",
            current_user.id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к материалу запрещён: материал не привязан к курсу",
        )

    in_tree = await _user_has_course_in_tree(db, current_user.id, material_course_id)
    if not in_tree:
        logger.info(
            "Y-5.1: deny student user_id=%s material.course_id=%s "
            "(не в дереве user_courses)",
            current_user.id, material_course_id,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Доступ к материалу запрещён: вы не зачислены в этот курс",
        )
