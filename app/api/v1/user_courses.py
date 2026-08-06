# app/api/v1/user_courses.py

from fastapi import APIRouter, Depends, HTTPException, Body, Response, status, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.user_courses import (
    UserCourseCreate,
    UserCourseRead,
    UserCourseUpdate,
)
from app.services.user_courses_service import UserCoursesService

router = APIRouter(prefix="/user-courses", tags=["user_courses"])
service = UserCoursesService()

# tsk-433 Волна 3.2: отчисление ученика с курса доступно кабинету методиста.
# Живёт под префиксом `/user-courses`, а не рядом с зачислением
# (`POST /users/{id}/courses/bulk`) — из-за этого при разведке волны его легко
# принять за отсутствующий. Роли те же, что на остальных записях по людям.
_PEOPLE_WRITE_GATE = require_role("methodist", "admin")


@router.post(
    "/",
    response_model=UserCourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать связь пользователь ↔ курс",
    description=(
        "Привязать студента к курсу.\n\n"
        "**Особенности:**\n"
        "- Если `order_number` не указан (null), он проставится автоматически триггером БД\n"
        "- Пара (user_id, course_id) должна быть уникальной (составной PK)\n"
        "- При попытке создать дубликат связи возвращается ошибка 409 Conflict\n\n"
        "**Использование:**\n"
        "Используется для привязки студента к курсу как из меню курсов, так и из меню студентов."
    ),
    responses={
        201: {
            "description": "Связь успешно создана",
            "content": {
                "application/json": {
                    "example": {
                        "user_id": 13,
                        "course_id": 1,
                        "added_at": "2026-01-26T15:00:00Z",
                        "order_number": 1
                    }
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
        409: {
            "description": "Связь уже существует (студент уже привязан к курсу)",
            "content": {
                "application/json": {
                    "example": {
                        "error": "domain_error",
                        "detail": "Курс уже назначен этому ученику",
                        "payload": {"user_id": 13, "course_id": 1},
                    }
                }
            }
        },
        422: {"description": "Ошибка валидации данных запроса"},
    },
)
async def create_user_course(
    obj_in: UserCourseCreate = Body(..., description="Данные для создания связи пользователя и курса"),
    db: AsyncSession = Depends(get_db),
):
    """
    Создать связь пользователя и курса.
    
    **Тело запроса:**
    - `user_id` (int, обязательный): ID пользователя (студента)
    - `course_id` (int, обязательный): ID курса
    - `order_number` (int, опционально): Порядковый номер курса у пользователя. Если не указан, проставится автоматически
    
    **Ответ:**
    Возвращает объект `UserCourseRead` с информацией о созданной связи.
    
    **Коды ответов:**
    - `201` - Связь успешно создана
    - `403` - Неверный или отсутствующий API ключ
    - `409` - Дубликат связи: студент уже привязан к курсу (tsk-574)
    - `422` - Ошибка валидации данных запроса
    """
    return await service.create(db, obj_in.model_dump())


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
        obj_in.model_dump(exclude_unset=True),
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return updated


@router.delete(
    "/{user_id}/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить связь пользователь ↔ курс",
    description=(
        "Отвязать студента от курса.\n\n"
        "**Особенности:**\n"
        "- Операция идемпотентна: если связи не было, возвращается 404\n"
        "- После удаления студент больше не будет видеть курс в своем списке\n\n"
        "**Использование:**\n"
        "Используется для удаления студента из курса как из меню курсов, так и из меню студентов."
    ),
    responses={
        204: {
            "description": "Связь успешно удалена"
        },
        404: {
            "description": "Связь не найдена (студент не привязан к курсу)",
            "content": {
                "application/json": {
                    "example": {"detail": "Not found"}
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def delete_user_course(
    user_id: int = Path(..., description="ID пользователя (студента)", examples=[13, 14, 15]),
    course_id: int = Path(..., description="ID курса", examples=[1, 2, 3]),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_PEOPLE_WRITE_GATE),
):
    """
    Удалить связь пользователя и курса.
    
    **Параметры пути:**
    - `user_id` (int, обязательный): ID пользователя (студента)
    - `course_id` (int, обязательный): ID курса
    
    **Ответ:**
    Возвращает статус `204 No Content` при успешном удалении связи.
    
    **Коды ответов:**
    - `204` - Связь успешно удалена
    - `404` - Связь не найдена (студент не привязан к курсу)
    - `403` - Неверный или отсутствующий API ключ
    """
    deleted = await service.delete_by_keys(
        db, {"user_id": user_id, "course_id": course_id}
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
