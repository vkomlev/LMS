# app/api/v1/teacher_courses.py

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Body
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.users import UserRead
from app.schemas.courses import CourseRead
from app.schemas.teacher_courses import TeacherCourseCreate, TeacherCourseRead
from app.services.teacher_courses_service import TeacherCoursesService
from app.utils.pagination import Page, build_page
from app.utils.exceptions import DomainError

router = APIRouter(tags=["teacher_courses"])
service = TeacherCoursesService()


@router.get(
    "/courses/{course_id}/teachers",
    response_model=Page[UserRead],  # type: ignore[name-defined]
    summary="Список преподавателей курса",
    description=(
        "Получить список всех преподавателей, привязанных к указанному курсу, "
        "с пагинацией и сортировкой.\n\n"
        "**Особенности:**\n"
        "- Поддерживает пагинацию через параметры `skip` и `limit`\n"
        "- Поддерживает сортировку по `linked_at`, `email`, `full_name`\n"
        "- ⚠️ ВАЖНО: При привязке преподавателя к родительскому курсу, "
        "триггер БД автоматически привяжет всех детей курса\n\n"
        "**Использование:**\n"
        "Полезно для отображения списка преподавателей курса в интерфейсе управления курсами."
    ),
    responses={
        200: {
            "description": "Список преподавателей успешно получен",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {
                                "id": 16,
                                "email": "test_teacher_1@example.com",
                                "full_name": "Преподаватель Тестовый 1",
                                "tg_id": None,
                                "created_at": "2026-01-26T14:21:50.253Z"
                            }
                        ],
                        "meta": {
                            "total": 1,
                            "limit": 50,
                            "offset": 0
                        }
                    }
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
        404: {"description": "Курс с указанным ID не найден"},
    },
)
async def list_course_teachers(
    course_id: int = Path(..., description="ID курса", examples=[1, 2, 3]),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска (пагинация)", examples=[0, 50, 100]),
    limit: int = Query(50, ge=1, le=200, description="Максимальное количество записей на странице", examples=[50, 100]),
    sort_by: str = Query("linked_at", description="Поле для сортировки", examples=["linked_at", "email", "full_name"]),
    order: str = Query("desc", description="Направление сортировки (asc или desc)", examples=["asc", "desc"]),
    db: AsyncSession = Depends(get_db),
) -> Page[UserRead]:
    """
    Вернуть всех преподавателей, привязанных к курсу, с пагинацией и сортировкой.
    
    **Параметры пути:**
    - `course_id` (int, обязательный): ID курса
    
    **Query параметры:**
    - `skip` (int, опционально, по умолчанию 0): Количество записей для пропуска
    - `limit` (int, опционально, по умолчанию 50): Максимальное количество записей на странице
    - `sort_by` (str, опционально, по умолчанию "linked_at"): Поле для сортировки
      - `linked_at` - по дате привязки (по умолчанию)
      - `email` - по email преподавателя
      - `full_name` - по ФИО преподавателя
    - `order` (str, опционально, по умолчанию "desc"): Направление сортировки (`asc` или `desc`)
    
    **Ответ:**
    Возвращает объект `Page[UserRead]` с полями:
    - `items`: массив объектов `UserRead` с информацией о преподавателях
    - `meta`: метаданные пагинации (total, limit, offset)
    
    Если у курса нет привязанных преподавателей, возвращается пустой массив в `items`.
    
    **Коды ответов:**
    - `200` - Список получен успешно (может быть пустым)
    - `403` - Неверный или отсутствующий API ключ
    - `404` - Курс не найден
    """
    items, total = await service.list_teachers(
        db, course_id, skip=skip, limit=limit, sort_by=sort_by, order=order
    )
    return build_page(items, total, limit, skip)


@router.get(
    "/users/{teacher_id}/courses",
    response_model=Page[CourseRead],  # type: ignore[name-defined]
    summary="Список курсов преподавателя",
    description=(
        "Получить список всех курсов, привязанных к указанному преподавателю, "
        "с пагинацией и сортировкой.\n\n"
        "**Особенности:**\n"
        "- Поддерживает пагинацию через параметры `skip` и `limit`\n"
        "- Поддерживает сортировку по `linked_at`, `title`, `created_at`\n"
        "- ⚠️ ВАЖНО: При привязке преподавателя к родительскому курсу, "
        "триггер БД автоматически привяжет всех детей курса\n\n"
        "**Использование:**\n"
        "Полезно для отображения списка курсов преподавателя в интерфейсе управления преподавателями."
    ),
    responses={
        200: {
            "description": "Список курсов успешно получен",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {
                                "id": 1,
                                "title": "Основы Python",
                                "access_level": "auto_check",
                                "description": "Введение в Python",
                                "parent_course_ids": [],
                                "created_at": "2025-02-06T11:42:52.674613Z",
                                "is_required": False,
                                "course_uid": "COURSE-PY-01"
                            }
                        ],
                        "meta": {
                            "total": 1,
                            "limit": 50,
                            "offset": 0
                        }
                    }
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
        404: {"description": "Преподаватель с указанным ID не найден"},
    },
)
async def list_teacher_courses(
    teacher_id: int = Path(..., description="ID преподавателя", examples=[16, 17]),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска (пагинация)", examples=[0, 50, 100]),
    limit: int = Query(50, ge=1, le=200, description="Максимальное количество записей на странице", examples=[50, 100]),
    sort_by: str = Query("linked_at", description="Поле для сортировки", examples=["linked_at", "title", "created_at"]),
    order: str = Query("desc", description="Направление сортировки (asc или desc)", examples=["asc", "desc"]),
    db: AsyncSession = Depends(get_db),
) -> Page[CourseRead]:
    """
    Вернуть все курсы, привязанные к преподавателю, с пагинацией и сортировкой.
    
    **Параметры пути:**
    - `teacher_id` (int, обязательный): ID преподавателя
    
    **Query параметры:**
    - `skip` (int, опционально, по умолчанию 0): Количество записей для пропуска
    - `limit` (int, опционально, по умолчанию 50): Максимальное количество записей на странице
    - `sort_by` (str, опционально, по умолчанию "linked_at"): Поле для сортировки
      - `linked_at` - по дате привязки (по умолчанию)
      - `title` - по названию курса
      - `created_at` - по дате создания курса
    - `order` (str, опционально, по умолчанию "desc"): Направление сортировки (`asc` или `desc`)
    
    **Ответ:**
    Возвращает объект `Page[CourseRead]` с полями:
    - `items`: массив объектов `CourseRead` с информацией о курсах
    - `meta`: метаданные пагинации (total, limit, offset)
    
    Если у преподавателя нет привязанных курсов, возвращается пустой массив в `items`.
    
    **Коды ответов:**
    - `200` - Список получен успешно (может быть пустым)
    - `403` - Неверный или отсутствующий API ключ
    - `404` - Преподаватель не найден
    """
    items, total = await service.list_courses(
        db, teacher_id, skip=skip, limit=limit, sort_by=sort_by, order=order
    )
    return build_page(items, total, limit, skip)


@router.post(
    "/courses/{course_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Привязать преподавателя к курсу",
    description=(
        "Привязать преподавателя к курсу.\n\n"
        "**Особенности:**\n"
        "- ⚠️ ВАЖНО: Триггер БД автоматически привяжет всех детей курса к преподавателю\n"
        "- Если связь уже существует, операция выполняется без ошибки (idempotent)\n"
        "- Оба объекта должны существовать в системе\n\n"
        "**Использование:**\n"
        "Используется для назначения преподавателя курсу из интерфейса управления курсами или преподавателями."
    ),
    responses={
        204: {
            "description": "Связь успешно создана (или уже существовала)"
        },
        404: {
            "description": "Преподаватель или курс не найден",
            "content": {
                "application/json": {
                    "example": {"detail": "Преподаватель с ID 16 не найден"}
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def add_teacher_course_link(
    course_id: int = Path(..., description="ID курса", examples=[1, 2, 3]),
    teacher_id: int = Path(..., description="ID преподавателя", examples=[16, 17]),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Привязать преподавателя к курсу.
    
    **Параметры пути:**
    - `course_id` (int, обязательный): ID курса
    - `teacher_id` (int, обязательный): ID преподавателя
    
    **Ответ:**
    Возвращает статус `204 No Content` при успешном создании связи.
    
    **Коды ответов:**
    - `204` - Связь успешно создана (или уже существовала)
    - `404` - Преподаватель или курс не найден
    - `403` - Неверный или отсутствующий API ключ
    
    **Примечания:**
    - Операция идемпотентна: повторный вызов с теми же параметрами не вызовет ошибку
    - ⚠️ ВАЖНО: Триггер БД автоматически привяжет всех детей курса к преподавателю
    - После создания связи преподаватель появится в списке преподавателей курса
    """
    try:
        await service.add_link(db, teacher_id, course_id)
    except DomainError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete(
    "/courses/{course_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отвязать преподавателя от курса",
    description=(
        "Отвязать преподавателя от курса.\n\n"
        "**Особенности:**\n"
        "- ⚠️ ВАЖНО: Триггер БД автоматически отвяжет всех детей курса от преподавателя\n"
        "- Операция идемпотентна: если связи не было, возвращается 204 без ошибки\n\n"
        "**Использование:**\n"
        "Используется для отвязки преподавателя от курса из интерфейса управления курсами или преподавателями."
    ),
    responses={
        204: {
            "description": "Связь успешно удалена (или не существовала)"
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def remove_teacher_course_link(
    course_id: int = Path(..., description="ID курса", examples=[1, 2, 3]),
    teacher_id: int = Path(..., description="ID преподавателя", examples=[16, 17]),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Отвязать преподавателя от курса.
    
    **Параметры пути:**
    - `course_id` (int, обязательный): ID курса
    - `teacher_id` (int, обязательный): ID преподавателя
    
    **Ответ:**
    Возвращает статус `204 No Content` при успешном удалении связи.
    
    **Коды ответов:**
    - `204` - Связь успешно удалена (или не существовала)
    - `403` - Неверный или отсутствующий API ключ
    
    **Примечания:**
    - Операция идемпотентна: если связи не было, ошибки не будет
    - ⚠️ ВАЖНО: Триггер БД автоматически отвяжет всех детей курса от преподавателя
    - После удаления связи преподаватель исчезнет из списка преподавателей курса
    """
    await service.remove_link(db, teacher_id, course_id)


# ========== Альтернативный RESTful подход ==========

@router.get(
    "/teacher-courses/",
    response_model=Page[TeacherCourseRead],  # type: ignore[name-defined]
    summary="Список всех связей преподаватель ↔ курс",
    description=(
        "Получить список всех связей преподавателей с курсами с пагинацией, "
        "фильтрацией и сортировкой.\n\n"
        "**Особенности:**\n"
        "- Поддерживает фильтрацию по `teacher_id` и/или `course_id`\n"
        "- Поддерживает пагинацию через параметры `skip` и `limit`\n"
        "- Поддерживает сортировку по `linked_at`, `teacher_id`, `course_id`\n"
        "- ⚠️ ВАЖНО: При привязке преподавателя к родительскому курсу, "
        "триггер БД автоматически привяжет всех детей курса\n\n"
        "**Использование:**\n"
        "Полезно для получения всех связей с возможностью фильтрации и сортировки."
    ),
    responses={
        200: {
            "description": "Список связей успешно получен",
            "content": {
                "application/json": {
                    "example": {
                        "items": [
                            {
                                "teacher_id": 16,
                                "course_id": 1,
                                "linked_at": "2026-01-26T14:21:50.221Z"
                            }
                        ],
                        "meta": {
                            "total": 1,
                            "limit": 50,
                            "offset": 0
                        }
                    }
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def list_all_teacher_courses(
    teacher_id: Optional[int] = Query(None, description="Фильтр по ID преподавателя", examples=[16, 17]),
    course_id: Optional[int] = Query(None, description="Фильтр по ID курса", examples=[1, 2, 3]),
    skip: int = Query(0, ge=0, description="Количество записей для пропуска (пагинация)", examples=[0, 50, 100]),
    limit: int = Query(50, ge=1, le=200, description="Максимальное количество записей на странице", examples=[50, 100]),
    sort_by: str = Query("linked_at", description="Поле для сортировки", examples=["linked_at", "teacher_id", "course_id"]),
    order: str = Query("desc", description="Направление сортировки (asc или desc)", examples=["asc", "desc"]),
    db: AsyncSession = Depends(get_db),
) -> Page[TeacherCourseRead]:
    """
    Вернуть все связи преподавателей с курсами с пагинацией, фильтрацией и сортировкой.
    
    **Query параметры:**
    - `teacher_id` (int, опционально): Фильтр по ID преподавателя
    - `course_id` (int, опционально): Фильтр по ID курса
    - `skip` (int, опционально, по умолчанию 0): Количество записей для пропуска
    - `limit` (int, опционально, по умолчанию 50): Максимальное количество записей на странице
    - `sort_by` (str, опционально, по умолчанию "linked_at"): Поле для сортировки
      - `linked_at` - по дате привязки (по умолчанию)
      - `teacher_id` - по ID преподавателя
      - `course_id` - по ID курса
    - `order` (str, опционально, по умолчанию "desc"): Направление сортировки (`asc` или `desc`)
    
    **Ответ:**
    Возвращает объект `Page[TeacherCourseRead]` с полями:
    - `items`: массив объектов `TeacherCourseRead` с информацией о связях
    - `meta`: метаданные пагинации (total, limit, offset)
    
    Если связей нет, возвращается пустой массив в `items`.
    
    **Коды ответов:**
    - `200` - Список получен успешно (может быть пустым)
    - `403` - Неверный или отсутствующий API ключ
    """
    # Получаем список связей из репозитория
    links = await service.repo.list(
        db, skip=skip, limit=limit, teacher_id=teacher_id, course_id=course_id,
        sort_by=sort_by, order=order
    )
    
    # Преобразуем кортежи в TeacherCourseRead
    items = [
        TeacherCourseRead(
            teacher_id=link[0],
            course_id=link[1],
            linked_at=link[2]
        )
        for link in links
    ]
    
    # Подсчет общего количества
    total = await service.repo.count(db, teacher_id=teacher_id, course_id=course_id)
    
    return build_page(items, total, limit, skip)


@router.post(
    "/teacher-courses/",
    response_model=TeacherCourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать связь преподаватель ↔ курс",
    description=(
        "Создать связь между преподавателем и курсом.\n\n"
        "**Особенности:**\n"
        "- ⚠️ ВАЖНО: Триггер БД автоматически привяжет всех детей курса к преподавателю\n"
        "- Если связь уже существует, возвращается существующая связь (idempotent)\n"
        "- Оба объекта должны существовать в системе\n\n"
        "**Использование:**\n"
        "Используется для создания связи преподавателя с курсом через RESTful API."
    ),
    responses={
        201: {
            "description": "Связь успешно создана",
            "content": {
                "application/json": {
                    "example": {
                        "teacher_id": 16,
                        "course_id": 1,
                        "linked_at": "2026-01-26T14:21:50.221Z"
                    }
                }
            }
        },
        404: {
            "description": "Преподаватель или курс не найден",
            "content": {
                "application/json": {
                    "example": {"detail": "Преподаватель с ID 16 не найден"}
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def create_teacher_course_link(
    obj_in: TeacherCourseCreate = Body(
        ...,
        description="Данные для создания связи преподаватель ↔ курс",
        examples=[
            {"teacher_id": 16, "course_id": 1},
            {"teacher_id": 17, "course_id": 2}
        ]
    ),
    db: AsyncSession = Depends(get_db),
) -> TeacherCourseRead:
    """
    Создать связь между преподавателем и курсом.
    
    **Тело запроса:**
    - `teacher_id` (int, обязательный): ID преподавателя
    - `course_id` (int, обязательный): ID курса
    
    **Ответ:**
    Возвращает объект `TeacherCourseRead` с информацией о созданной связи.
    
    **Коды ответов:**
    - `201` - Связь успешно создана
    - `404` - Преподаватель или курс не найден
    - `403` - Неверный или отсутствующий API ключ
    
    **Примечания:**
    - Операция идемпотентна: если связь уже существует, возвращается существующая
    - ⚠️ ВАЖНО: Триггер БД автоматически привяжет всех детей курса к преподавателю
    """
    try:
        # Создаем связь
        created = await service.add_link(db, obj_in.teacher_id, obj_in.course_id)
        
        # Получаем созданную связь
        link = await service.repo.get_link(db, obj_in.teacher_id, obj_in.course_id)
        if not link:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Связь была создана, но не удалось получить данные"
            )
        
        return TeacherCourseRead(
            teacher_id=link[0],
            course_id=link[1],
            linked_at=link[2]
        )
    except DomainError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete(
    "/teacher-courses/{teacher_id}/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить связь преподаватель ↔ курс",
    description=(
        "Удалить связь между преподавателем и курсом.\n\n"
        "**Особенности:**\n"
        "- ⚠️ ВАЖНО: Триггер БД автоматически отвяжет всех детей курса от преподавателя\n"
        "- Операция идемпотентна: если связи не было, возвращается 204 без ошибки\n\n"
        "**Использование:**\n"
        "Используется для удаления связи преподавателя с курсом через RESTful API."
    ),
    responses={
        204: {
            "description": "Связь успешно удалена (или не существовала)"
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def delete_teacher_course_link(
    teacher_id: int = Path(..., description="ID преподавателя", examples=[16, 17]),
    course_id: int = Path(..., description="ID курса", examples=[1, 2, 3]),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Удалить связь между преподавателем и курсом.
    
    **Параметры пути:**
    - `teacher_id` (int, обязательный): ID преподавателя
    - `course_id` (int, обязательный): ID курса
    
    **Ответ:**
    Возвращает статус `204 No Content` при успешном удалении связи.
    
    **Коды ответов:**
    - `204` - Связь успешно удалена (или не существовала)
    - `403` - Неверный или отсутствующий API ключ
    
    **Примечания:**
    - Операция идемпотентна: если связи не было, ошибки не будет
    - ⚠️ ВАЖНО: Триггер БД автоматически отвяжет всех детей курса от преподавателя
    """
    await service.remove_link(db, teacher_id, course_id)
