# app/api/v1/user_courses.py

from fastapi import APIRouter, Depends, HTTPException, Body, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.user_courses import (
    UserCourseCreate,
    UserCourseRead,
    UserCourseUpdate,
)
from app.services.user_courses_service import UserCoursesService

router = APIRouter(prefix="/user-courses", tags=["user_courses"])
service = UserCoursesService()


@router.post(
    "/",
    response_model=UserCourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать связь пользователь ↔ курс",
    responses={
        201: {"description": "Связь создана"},
        400: {"description": "Дубликат связи или некорректные данные"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def create_user_course(
    obj_in: UserCourseCreate = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Создать связь пользователя и курса.

    Примечание:
    - Если `order_number` не задан (null), он проставится автоматически триггером БД.
    """
    return await service.create(db, obj_in.dict())


@router.get(
    "/{user_id}/{course_id}",
    response_model=UserCourseRead,
    summary="Получить связь пользователь ↔ курс по составному ключу",
    responses={
        200: {"description": "Связь найдена"},
        404: {"description": "Not found"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def read_user_course(
    user_id: int,
    course_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Получить связь пользователя и курса по составному ключу.
    """
    obj = await service.get_by_keys(
        db, {"user_id": user_id, "course_id": course_id}
    )
    if not obj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return obj


@router.put(
    "/{user_id}/{course_id}",
    response_model=UserCourseRead,
    summary="Обновить связь пользователь ↔ курс",
    responses={
        200: {"description": "Связь обновлена"},
        404: {"description": "Not found"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def update_user_course(
    user_id: int,
    course_id: int,
    obj_in: UserCourseUpdate = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Обновить связь пользователя и курса.
    """
    updated = await service.update_by_keys(
        db,
        {"user_id": user_id, "course_id": course_id},
        obj_in.dict(exclude_unset=True),
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return updated


@router.delete(
    "/{user_id}/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить связь пользователь ↔ курс",
    responses={
        204: {"description": "Связь удалена"},
        404: {"description": "Not found"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def delete_user_course(
    user_id: int,
    course_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Удалить связь пользователя и курса.
    """
    deleted = await service.delete_by_keys(
        db, {"user_id": user_id, "course_id": course_id}
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
