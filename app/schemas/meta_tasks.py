from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict

from app.schemas.difficulty_levels import DifficultyLevelRead
from app.schemas.courses import CourseRead


class TasksMetaResponse(BaseModel):
    """
    Справочная информация для импорта/редактирования задач.

    Возвращает:
      - список уровней сложности;
      - список курсов;
      - список тегов (пока пустой, под расширение);
      - список типов задач;
      - версию формата метаданных.
    """

    difficulties: List[DifficultyLevelRead]
    courses: List[CourseRead]
    tags: List[str]
    task_types: List[str]
    version: int

    model_config = ConfigDict(from_attributes=True)