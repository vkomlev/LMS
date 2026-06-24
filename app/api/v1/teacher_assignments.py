"""
Ручное назначение курса ученику учителем в один клик (tsk-031).

Teacher-only: роль teacher/methodist/admin или сервисный токен.
Идемпотентно: повторный вызов не создаёт дубль и не ошибается.
Модель — docs/ai/adr/0002-course-assignment-trigger-rules.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.assignment_rules import ManualAssignRequest, ManualAssignResponse
from app.services import assignment_rules_service
from app.utils.exceptions import DomainError

logger = logging.getLogger("api.teacher_assignments")

router = APIRouter(tags=["teacher_assignments"])

# Роли с полным доступом (могут назначать курс любому ученику).
_ELEVATED_ROLES = {"admin", "methodist", "методист"}
# Преподавательские роли (доступ ограничен своими учениками).
_TEACHER_ROLES = {"teacher", "преподаватель"}


async def _ensure_can_assign(
    db: AsyncSession, current_user: CurrentUser, student_id: int
) -> None:
    """
    Проверить право на ручное назначение курса данному ученику.

    Иерархия доступа:
    - сервисный токен (бот/админ-интеграция) — полный доступ;
    - роль admin/methodist — полный доступ (любой ученик);
    - роль teacher — только к своим ученикам (связь ``student_teacher_links``);
    - иначе — 403.
    """
    if current_user.is_service:
        return

    from app.models.association_tables import t_user_roles
    from app.models.roles import Roles

    stmt = (
        select(Roles.name)
        .join(t_user_roles, Roles.id == t_user_roles.c.role_id)
        .where(t_user_roles.c.user_id == current_user.id)
    )
    roles = {str(row[0]).lower().strip() for row in (await db.execute(stmt)).fetchall()}

    if roles & _ELEVATED_ROLES:
        return

    if roles & _TEACHER_ROLES:
        linked = (
            await db.execute(
                text(
                    "SELECT 1 FROM student_teacher_links "
                    "WHERE student_id = :sid AND teacher_id = :tid"
                ),
                {"sid": student_id, "tid": current_user.id},
            )
        ).fetchone()
        if linked is not None:
            return
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Преподаватель может назначать курсы только своим ученикам",
        )

    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "Назначать курсы может только преподаватель/методист/админ",
    )


@router.post(
    "/teacher/students/{student_id}/assignments",
    response_model=ManualAssignResponse,
    status_code=status.HTTP_200_OK,
    summary="Назначить курс ученику (учитель, в один клик)",
    description=(
        "Идемпотентно привязать курс к ученику от лица преподавателя.\n\n"
        "- Указывается ровно один из `course_id` / `course_uid` "
        "(`course_uid` совпадает с кодом публикатора, например `wp:vvodnyy-python`).\n"
        "- Повторный вызов не создаёт дубль: вернётся `already_enrolled=true`.\n"
        "- Доступ: admin/methodist или сервисный токен — любому ученику; "
        "teacher — только своим ученикам (связь student_teacher_links)."
    ),
    responses={
        200: {"description": "Курс назначен (или уже был назначен)"},
        403: {"description": "Недостаточно прав"},
        404: {"description": "Ученик или курс не найден"},
    },
)
async def assign_course_to_student_endpoint(
    student_id: int = Path(..., description="ID ученика", examples=[42]),
    payload: ManualAssignRequest = Body(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_bare_db),
) -> ManualAssignResponse:
    """Назначить курс ученику вручную (teacher-only, идемпотентно)."""
    await _ensure_can_assign(db, current_user, student_id)

    # Проверка существования ученика — явный 404 вместо ошибки FK.
    student_row = (
        await db.execute(
            text("SELECT 1 FROM users WHERE id = :sid"),
            {"sid": student_id},
        )
    ).fetchone()
    if student_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Ученик id={student_id} не найден")

    detail = {"reason": payload.reason} if payload.reason else None
    assigned_by = None if current_user.is_service else current_user.id

    try:
        result = await assignment_rules_service.assign_course_to_student(
            db,
            student_id=student_id,
            course_id=payload.course_id,
            course_uid=payload.course_uid,
            source="manual_teacher",
            assigned_by=assigned_by,
            detail=detail,
            skip_event_if_enrolled=True,
        )
    except DomainError as e:
        raise HTTPException(e.status_code or status.HTTP_400_BAD_REQUEST, str(e.detail))

    logger.info(
        "manual assign: teacher=%s student=%s course=%s already_enrolled=%s",
        assigned_by, student_id, result.course_id, result.already_enrolled,
    )
    return ManualAssignResponse(
        student_id=student_id,
        course_id=result.course_id,
        already_enrolled=result.already_enrolled,
        event_id=result.event_id,
    )
