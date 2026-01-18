from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, Body, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.schemas.user_courses import (
    UserCourseBulkCreate,
    UserCourseListResponse,
    UserCourseReorderRequest,
    UserCourseRead,
    UserCourseWithCourse,
)
from app.services.user_courses_service import UserCoursesService
from app.models.user_courses import UserCourses
from sqlalchemy import select
from app.schemas.courses import CourseRead

router = APIRouter(tags=["user_courses"])

user_courses_service = UserCoursesService()


@router.get(
    "/users/{user_id}/courses",
    response_model=UserCourseListResponse,
    summary="Получить список курсов пользователя",
    responses={
        200: {
            "description": "Список курсов пользователя с информацией о курсах",
        },
        404: {
            "description": "Пользователь не найден",
        },
    },
)
async def get_user_courses_endpoint(
    user_id: int,
    order_by_order: bool = Query(True, description="Сортировать по order_number (True) или по added_at (False)"),
    db: AsyncSession = Depends(get_db),
) -> UserCourseListResponse:
    """
    Получить список курсов пользователя с информацией о курсах.
    
    Параметры:
    - order_by_order: Если True, сортировать по order_number, иначе по added_at.
    
    Возвращает список курсов с полной информацией о каждом курсе.
    """
    # Получаем связи пользователя с курсами с явной загрузкой курсов
    stmt = select(UserCourses).where(UserCourses.user_id == user_id)
    
    if order_by_order:
        stmt = stmt.order_by(
            UserCourses.order_number.asc().nulls_last(),
            UserCourses.added_at.asc()
        )
    else:
        stmt = stmt.order_by(UserCourses.added_at.asc())
    
    # Явно загружаем связанные курсы
    stmt = stmt.options(selectinload(UserCourses.course))
    
    result = await db.execute(stmt)
    user_courses = list(result.scalars().all())
    
    # Формируем ответ с информацией о курсах
    courses_list = []
    for uc in user_courses:
        # Проверяем, что курс загружен
        if uc.course is None:
            # Если курс не найден, пропускаем эту запись
            continue
        
        # Преобразуем курс в схему
        course_read = CourseRead.model_validate(uc.course)
        
        course_data = UserCourseWithCourse(
            user_id=uc.user_id,
            course_id=uc.course_id,
            added_at=uc.added_at,
            order_number=uc.order_number,
            course=course_read,
        )
        courses_list.append(course_data)
    
    return UserCourseListResponse(
        user_id=user_id,
        courses=courses_list,
    )


@router.post(
    "/users/{user_id}/courses/bulk",
    response_model=List[UserCourseRead],
    summary="Массовая привязка курсов к пользователю",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {
            "description": "Курсы успешно привязаны к пользователю",
        },
        400: {
            "description": "Ошибка валидации (пустой список курсов, дубликаты)",
        },
        404: {
            "description": "Пользователь не найден",
        },
    },
)
async def bulk_assign_courses_endpoint(
    user_id: int,
    payload: UserCourseBulkCreate = Body(
        ...,
        description="Список ID курсов для привязки",
        examples=[
            {
                "summary": "Привязать несколько курсов",
                "value": {"course_ids": [1, 2, 3]},
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> List[UserCourseRead]:
    """
    Массовая привязка курсов к пользователю.
    
    Правила:
    - Курсы привязываются с автоматической нумерацией (order_number устанавливается триггером БД).
    - Если курс уже привязан к пользователю, он пропускается (не создается дубликат).
    - Порядок в списке course_ids определяет порядок order_number.
    
    Ошибки:
    - 400: Пустой список курсов или некорректные данные.
    - 404: Пользователь не найден.
    """
    created_user_courses = await user_courses_service.bulk_assign_courses(
        db, user_id, payload.course_ids
    )
    
    return [UserCourseRead.model_validate(uc) for uc in created_user_courses]


@router.patch(
    "/users/{user_id}/courses/reorder",
    response_model=List[UserCourseRead],
    summary="Переупорядочить курсы пользователя",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "description": "Курсы успешно переупорядочены",
        },
        400: {
            "description": "Ошибка валидации (некорректные порядковые номера, курсы не принадлежат пользователю)",
        },
        404: {
            "description": "Пользователь не найден",
        },
    },
)
async def reorder_user_courses_endpoint(
    user_id: int,
    payload: UserCourseReorderRequest = Body(
        ...,
        description="Список курсов с их порядковыми номерами",
        examples=[
            {
                "summary": "Переупорядочить курсы",
                "value": {
                    "course_orders": [
                        {"course_id": 1, "order_number": 1},
                        {"course_id": 2, "order_number": 2},
                        {"course_id": 3, "order_number": 3},
                    ]
                },
            },
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> List[UserCourseRead]:
    """
    Переупорядочить курсы пользователя (явное обновление order_number).
    
    Правила:
    - Каждый курс должен быть указан с его новым порядковым номером.
    - Порядковые номера должны быть уникальными и начинаться с 1.
    - Все указанные курсы должны быть привязаны к пользователю.
    
    Ошибки:
    - 400: Некорректные порядковые номера или курсы не принадлежат пользователю.
    - 404: Пользователь не найден.
    """
    # Преобразуем список CourseOrderItem в список словарей для сервиса
    course_orders = [
        {"course_id": item.course_id, "order_number": item.order_number}
        for item in payload.course_orders
    ]
    
    updated_user_courses = await user_courses_service.reorder_courses(
        db, user_id, course_orders
    )
    
    return [UserCourseRead.model_validate(uc) for uc in updated_user_courses]
