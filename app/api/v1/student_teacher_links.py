# app/api/v1/student_teacher_links.py

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.users import UserRead
from app.services.student_teacher_links_service import (
    StudentTeacherLinksService,
)

router = APIRouter(tags=["student_teacher_links"])
service = StudentTeacherLinksService()


# ---------- Студент → его преподаватели ----------

@router.get(
    "/users/{student_id}/teachers",
    response_model=List[UserRead],
    summary="Список преподавателей студента",
    description=(
        "Получить список всех преподавателей, привязанных к указанному студенту.\n\n"
        "**Использование:**\n"
        "Полезно для отображения списка преподавателей студента в интерфейсе управления студентами."
    ),
    responses={
        200: {
            "description": "Список преподавателей успешно получен",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 16,
                            "email": "test_teacher_1@example.com",
                            "full_name": "Преподаватель Тестовый 1",
                            "tg_id": None,
                            "created_at": "2026-01-26T14:21:50.253Z"
                        }
                    ]
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
        404: {"description": "Студент с указанным ID не найден"},
    },
)
async def list_student_teachers(
    student_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[UserRead]:
    """
    Вернуть всех преподавателей, привязанных к студенту.
    
    **Параметры пути:**
    - `student_id` (int, обязательный): ID студента
    
    **Ответ:**
    Возвращает массив объектов `UserRead` с информацией о преподавателях.
    Если у студента нет привязанных преподавателей, возвращается пустой массив.
    
    **Коды ответов:**
    - `200` - Список получен успешно (может быть пустым)
    - `403` - Неверный или отсутствующий API ключ
    - `404` - Студент не найден (если проверка выполняется на уровне сервиса)
    """
    return await service.list_teachers(db, student_id)


@router.post(
    "/users/{student_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Добавить связь студент↔преподаватель",
    description=(
        "Привязать преподавателя к студенту.\n\n"
        "**Особенности:**\n"
        "- Связь создается в обе стороны (студент видит преподавателя, преподаватель видит студента)\n"
        "- Если связь уже существует, операция выполняется без ошибки (idempotent)\n"
        "- Оба пользователя должны существовать в системе\n\n"
        "**Использование:**\n"
        "Используется для назначения преподавателя студенту из интерфейса управления студентами."
    ),
    responses={
        204: {
            "description": "Связь успешно создана (или уже существовала)"
        },
        404: {
            "description": "Студент или преподаватель не найден",
            "content": {
                "application/json": {
                    "example": {"detail": "User or Role not found"}
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def add_student_teacher_link(
    student_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Привязать преподавателя к студенту.
    
    **Параметры пути:**
    - `student_id` (int, обязательный): ID студента
    - `teacher_id` (int, обязательный): ID преподавателя
    
    **Ответ:**
    Возвращает статус `204 No Content` при успешном создании связи.
    
    **Коды ответов:**
    - `204` - Связь успешно создана
    - `404` - Студент или преподаватель не найден
    - `403` - Неверный или отсутствующий API ключ
    
    **Примечания:**
    - Операция идемпотентна: повторный вызов с теми же параметрами не вызовет ошибку
    - После создания связи студент появится в списке студентов преподавателя
    """
    try:
        await service.add_link(db, student_id, teacher_id)
    except ValueError as e:
        # Аналогично user_roles/course_dependencies: 404 на not found
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete(
    "/users/{student_id}/teachers/{teacher_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить связь студент↔преподаватель",
    description=(
        "Удалить связь между студентом и преподавателем.\n\n"
        "**Особенности:**\n"
        "- Операция идемпотентна: если связи не было, возвращается 204 без ошибки\n"
        "- Связь удаляется в обе стороны\n\n"
        "**Использование:**\n"
        "Используется для отвязки преподавателя от студента из интерфейса управления студентами."
    ),
    responses={
        204: {
            "description": "Связь успешно удалена (или не существовала)"
        },
        403: {"description": "Invalid or missing API Key"},
    },
)
async def remove_student_teacher_link(
    student_id: int,
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Удалить связь между студентом и преподавателем.
    
    **Параметры пути:**
    - `student_id` (int, обязательный): ID студента
    - `teacher_id` (int, обязательный): ID преподавателя
    
    **Ответ:**
    Возвращает статус `204 No Content` при успешном удалении связи.
    
    **Коды ответов:**
    - `204` - Связь успешно удалена (или не существовала)
    - `403` - Неверный или отсутствующий API ключ
    
    **Примечания:**
    - Операция идемпотентна: если связи не было, ошибки не будет
    - После удаления связи студент исчезнет из списка студентов преподавателя
    """
    await service.remove_link(db, student_id, teacher_id)


# ---------- Преподаватель → его студенты ----------

@router.get(
    "/users/{teacher_id}/students",
    response_model=List[UserRead],
    summary="Список студентов преподавателя",
    description=(
        "Получить список всех студентов, привязанных к указанному преподавателю.\n\n"
        "**Использование:**\n"
        "Полезно для отображения списка студентов преподавателя в интерфейсе управления преподавателями."
    ),
    responses={
        200: {
            "description": "Список студентов успешно получен",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 13,
                            "email": "test_student_1@example.com",
                            "full_name": "Студент Тестовый 1",
                            "tg_id": None,
                            "created_at": "2026-01-26T14:21:50.221Z"
                        }
                    ]
                }
            }
        },
        403: {"description": "Invalid or missing API Key"},
        404: {"description": "Преподаватель с указанным ID не найден"},
    },
)
async def list_teacher_students(
    teacher_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[UserRead]:
    """
    Вернуть всех студентов, привязанных к преподавателю.
    
    **Параметры пути:**
    - `teacher_id` (int, обязательный): ID преподавателя
    
    **Ответ:**
    Возвращает массив объектов `UserRead` с информацией о студентах.
    Если у преподавателя нет привязанных студентов, возвращается пустой массив.
    
    **Коды ответов:**
    - `200` - Список получен успешно (может быть пустым)
    - `403` - Неверный или отсутствующий API ключ
    - `404` - Преподаватель не найден (если проверка выполняется на уровне сервиса)
    """
    return await service.list_students(db, teacher_id)
