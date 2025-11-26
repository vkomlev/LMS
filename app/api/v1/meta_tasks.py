from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.meta_tasks import TasksMetaResponse
from app.services.difficulty_levels_service import DifficultyLevelsService
from app.services.courses_service import CoursesService

router = APIRouter(tags=["meta"])

difficulty_service = DifficultyLevelsService()
courses_service = CoursesService()

# Жёстко заданные типы задач для импорта
TASK_TYPES: List[str] = ["SC", "MC", "SA", "SA_COM", "TA"]


@router.get(
    "/meta/tasks",
    response_model=TasksMetaResponse,
    summary="Получить справочные данные для задач одним запросом",
)
async def get_tasks_meta(
    db: AsyncSession = Depends(get_db),
) -> TasksMetaResponse:
    """
    Возвращает набор справочников для работы с задачами и их импортом:

    - difficulties: уровни сложности (difficulties);
    - courses: курсы;
    - tags: список тегов (пока пустой, под расширение);
    - task_types: коды типов задач;
    - version: версия формата метаданных (для будущей эволюции).
    """
    # Берём «разумный максимум» — если справочники вырастут, можно будет
    # либо увеличить лимиты, либо сделать отдельную пагинацию.
    difficulties, _ = await difficulty_service.paginate(
        db,
        limit=1000,
        offset=0,
    )
    courses, _ = await courses_service.paginate(
        db,
        limit=1000,
        offset=0,
    )

    return TasksMetaResponse(
        difficulties=difficulties,
        courses=courses,
        tags=[],          # пока тегов нет / не используем
        task_types=TASK_TYPES,
        version=1,
    )
