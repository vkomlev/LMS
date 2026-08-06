# app/api/v1/course_dependencies.py

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Body, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.schemas.courses import CourseRead, CourseDependenciesBulkCreate, CourseDependencyImpact
from app.services.course_dependencies_service import CourseDependenciesService

router = APIRouter(
    prefix="/courses/{course_id}/dependencies",
    tags=["course_dependencies"],
)

# tsk-433 Волна 2.3: зависимости курсов («ЕГЭ проходится после Python для ЕГЭ»)
# висели на legacy `get_db` (APIKeyQuery — только `?api_key=` в query), то есть
# были доступны ТГ-ботам и недоступны кабинету методиста по cookie. Чтение
# оставляем и преподавателю (ему полезно понимать порядок прохождения),
# изменение — только методисту и админу. `is_service` в require_role проходит
# без проверки роли, поэтому боты продолжают работать.
_READ_GATE = require_role("teacher", "methodist", "admin")
_WRITE_GATE = require_role("methodist", "admin")

service = CourseDependenciesService()


@router.get("/", response_model=List[CourseRead])
async def list_course_dependencies(
    course_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_READ_GATE),
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
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_WRITE_GATE),
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
            db, course_id, payload.required_course_ids,
            auto_assign=payload.auto_assign,
        )
        return [CourseRead.model_validate(dep) for dep in dependencies]
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))


@router.get(
    "/{required_course_id}/impact",
    response_model=CourseDependencyImpact,
    summary="Превью влияния добавления зависимости (tsk-231)",
    responses={
        200: {"description": "Число уже зачисленных студентов, которых заблокирует добавление"},
        403: {"description": "Invalid or missing API Key"},
    },
)
async def preview_course_dependency_impact(
    course_id: int,
    required_course_id: int,
    auto_assign: bool = Query(
        True,
        description="Режим будущей зависимости — превью обязано совпадать с тем, что реально произойдёт",
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_WRITE_GATE),
) -> CourseDependencyImpact:
    """
    Read-only превью ДО `POST /{required_course_id}`: сколько уже зачисленных
    на course_id учеников будет немедленно заблокировано новой зависимостью.

    При `auto_assign=true` блокировка глобальная и мгновенная (решение
    оператора, план tsk-231) — действует на всех, не только на тех, кто
    провалил задание. При `auto_assign=false` (фаза 6) мгновенно заблокированных
    нет: курс блокирует только тех, кому его назначат адресно.
    """
    count = await service.count_affected_students(db, course_id, auto_assign=auto_assign)
    return CourseDependencyImpact(affected_students_count=count)


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
    auto_assign: bool = Query(
        True,
        description=(
            "true (умолчание) — пререквизит для всех: требуемый курс раздаётся "
            "автоматически каждому, кто получает course_id, и блокирует весь поток. "
            "false — выдаётся точечно (мини-курс повторения, tsk-231): курс никому "
            "не раздаётся и блокирует только тех, кому методист назначил его адресно."
        ),
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_WRITE_GATE),
) -> None:
    """
    Добавить зависимость: course_id зависит от required_course_id.
    """
    try:
        await service.add_dependency(
            db, course_id, required_course_id, auto_assign=auto_assign
        )
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
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_WRITE_GATE),
) -> None:
    """
    Удалить зависимость: course_id → required_course_id.
    """
    await service.remove_dependency(db, course_id, required_course_id)
