# app/api/v1/course_dependencies.py

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.courses import CourseRead, CourseDependenciesBulkCreate
from app.services.course_dependencies_service import CourseDependenciesService

router = APIRouter(
    prefix="/courses/{course_id}/dependencies",
    tags=["course_dependencies"],
)

service = CourseDependenciesService()


@router.get("/", response_model=List[CourseRead])
async def list_course_dependencies(
    course_id: int,
    db: AsyncSession = Depends(get_db),
) -> List[CourseRead]:
    """
    Получить все курсы, от которых зависит данный курс.

    Статусы:
    - 200: список зависимостей (может быть пустым);
    - 403: Invalid or missing API Key.
    """
    return await service.list_dependencies(db, course_id)

@router.post(
    "/bulk",
    response_model=List[CourseRead],
    status_code=status.HTTP_201_CREATED,
    summary="Массовое добавление зависимостей курса",
    responses={
        201: {
            "description": "Зависимости успешно добавлены",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "id": 2,
                            "title": "Python: Продвинутый уровень",
                            "access_level": "auto_check",
                            "description": "Генераторы, декораторы",
                            "parent_course_id": 1,
                            "created_at": "2025-01-15T10:00:00Z",
                            "is_required": False,
                            "course_uid": "COURSE-PY-02",
                        }
                    ]
                }
            }
        },
        404: {"description": "Курс не найден"},
        400: {"description": "Некорректные данные (пустой список, self-dependency)"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def bulk_add_course_dependencies(
    course_id: int,
    payload: CourseDependenciesBulkCreate = Body(
        ...,
        description="Список ID курсов-зависимостей",
        examples=[
            {
                "summary": "Добавить несколько зависимостей",
                "value": {"required_course_ids": [2, 3, 4]},
            }
        ],
    ),
    db: AsyncSession = Depends(get_db),
) -> List[CourseRead]:
    """
    Массовое добавление зависимостей для курса.
    
    Правила:
    - Все зависимости из списка добавляются к курсу
    - Уже существующие зависимости пропускаются (не создаются дубликаты)
    - Self-dependency автоматически пропускается
    - Несуществующие курсы пропускаются
    
    Возвращает список успешно добавленных зависимостей.
    
    Ошибки:
    - 404: Курс не найден
    - 400: Пустой список зависимостей
    """
    try:
        dependencies = await service.bulk_add_dependencies(
            db, course_id, payload.required_course_ids
        )
        return [CourseRead.model_validate(dep) for dep in dependencies]
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.post(
    "/{required_course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Добавить зависимость курса",
    responses={
        204: {"description": "Зависимость добавлена"},
        404: {"description": "Курс или required_course не найдены"},
        400: {"description": "Некорректная зависимость (например, self-dependency)"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def add_course_dependency(
    course_id: int,
    required_course_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Добавить зависимость: course_id зависит от required_course_id.
    """
    try:
        await service.add_dependency(db, course_id, required_course_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.delete(
    "/{required_course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить зависимость курса",
    responses={
        204: {"description": "Зависимость удалена (или не существовала)"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def remove_course_dependency(
    course_id: int,
    required_course_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Удалить зависимость: course_id → required_course_id.
    """
    await service.remove_dependency(db, course_id, required_course_id)
