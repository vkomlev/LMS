"""tsk-1249: индивидуальные курсы ученика — выдача, список, снятие (кабинет методиста)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.services import individual_courses_service as service

router = APIRouter(prefix="/users/{user_id}/individual-courses", tags=["individual_courses"])

# Те же роли, что у зачисления на курсы в карточке человека (tsk-433 Волна 3.2).
_GATE = require_role("methodist", "admin")


class IndividualCourseIssue(BaseModel):
    """Выдать готовый курс (`course_id`) ИЛИ собрать новый (`title` + `topic_ids`)."""

    course_id: Optional[int] = Field(None, description="Готовый корневой курс")
    title: Optional[str] = Field(None, max_length=255, description="Название нового курса")
    topic_ids: Optional[List[int]] = Field(
        None, max_length=30, description="Темы банка по порядку — станут подкурсами нового курса"
    )
    lock_root_ids: Optional[List[int]] = Field(
        None,
        max_length=30,
        description="Какие основные курсы ученика закрыть; не указано — все его активные курсы",
    )


class IndividualCourseRead(BaseModel):
    """Индивидуальный курс ученика и закрытые им курсы."""

    course_id: int
    title: str
    course_uid: Optional[str]
    is_active: bool = Field(..., description="Выдача действует (false — снят методистом)")
    state: str = Field(..., description="NOT_STARTED | IN_PROGRESS | COMPLETED")
    locked_root_ids: List[int]
    locked_root_titles: List[str]
    topic_ids: List[int]
    topic_titles: List[str]


@router.get("", response_model=List[IndividualCourseRead], summary="Индивидуальные курсы ученика")
async def list_individual_courses(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_GATE),
) -> List[IndividualCourseRead]:
    """Курсы ученика, которые точечно закрывают его основные курсы (tsk-231 фаза 6)."""
    items = await service.list_for_student(db, user_id)
    return [IndividualCourseRead(**vars(i)) for i in items]


@router.post(
    "",
    response_model=IndividualCourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Выдать индивидуальный курс с замком на основные",
    responses={
        400: {"description": "Не указан ни курс, ни темы (или указано и то, и то)"},
        404: {"description": "Нет ученика или темы"},
        409: {"description": "Курс вложенный, выключен, ученик выпускник или нечего закрывать"},
    },
)
async def issue_individual_course(
    user_id: int,
    payload: IndividualCourseIssue,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_GATE),
) -> IndividualCourseRead:
    """Одна транзакция: курс (готовый или собранный) → запись ученика → замки."""
    course_id = await service.issue(
        db, user_id,
        course_id=payload.course_id, title=payload.title,
        topic_ids=payload.topic_ids, lock_root_ids=payload.lock_root_ids,
    )
    items = await service.list_for_student(db, user_id)
    issued = next((i for i in items if i.course_id == course_id), None)
    if issued is None:  # замки только что записаны — сюда попасть не должно
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Выдача не найдена после записи")
    return IndividualCourseRead(**vars(issued))


@router.delete(
    "/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Снять индивидуальный курс (замки перестают действовать)",
)
async def withdraw_individual_course(
    user_id: int,
    course_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_GATE),
) -> None:
    """Отключить выдачу; прогресс ученика сохраняется, повторная выдача вернёт замки."""
    await service.withdraw(db, user_id, course_id)
