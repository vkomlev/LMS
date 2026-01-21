from __future__ import annotations
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class AccessLevel(str, Enum):
    self_guided = "self_guided"
    auto_check = "auto_check"
    manual_check = "manual_check"
    group_sessions = "group_sessions"
    personal_teacher = "personal_teacher"


class CourseCreate(BaseModel):
    """
    Создание курса.

    Правила:
    - `parent_course_id` может быть `null` → курс корневой.
    - Валидация циклов в иерархии выполняется триггером БД.
    """
    title: str = Field(..., description="Название курса", examples=["Основы Python"])
    access_level: AccessLevel = Field(..., description="Тип доступа/проверки", examples=["auto_check"])
    description: Optional[str] = Field(None, description="Описание курса", examples=["Введение в Python: переменные, типы, условия, циклы"])
    parent_course_id: Optional[int] = Field(
        None,
        description="ID родительского курса (если курс является модулем внутри другого курса). null = корневой курс.",
        examples=[None, 1],
    )
    is_required: bool = Field(False, description="Обязательный ли курс", examples=[False])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Основы Python",
                    "access_level": "auto_check",
                    "description": "Введение в Python: переменные, типы, условия, циклы",
                    "parent_course_id": None,
                    "is_required": False,
                }
            ]
        }
    )


class CourseUpdate(BaseModel):
    """
    Частичное/полное обновление курса.

    Правила:
    - Любое поле может быть опущено.
    - Валидация циклов (если меняется parent) выполняется триггером БД.
    """
    title: Optional[str] = Field(None, description="Название курса", examples=["Python: продвинутый уровень"])
    access_level: Optional[AccessLevel] = Field(None, description="Тип доступа/проверки", examples=["manual_check"])
    description: Optional[str] = Field(None, description="Описание курса")
    parent_course_id: Optional[int] = Field(
        None,
        description="ID родительского курса. null = сделать курс корневым.",
        examples=[None, 1],
    )
    is_required: Optional[bool] = Field(None, description="Обязательный ли курс", examples=[True, False])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Python: продвинутый уровень",
                    "description": "Генераторы, декораторы, контекстные менеджеры",
                    "is_required": False,
                }
            ]
        }
    )


class CourseRead(BaseModel):
    id: int
    title: str
    access_level: AccessLevel
    description: Optional[str]
    parent_course_id: Optional[int]
    created_at: datetime
    is_required: bool
    course_uid: str | None = Field(
        None,
        description="Внешний код курса (для импорта/интеграций), например 'COURSE-PY-01'. Может быть null для старых курсов.",
        examples=["COURSE-PY-01", None],
    )

    model_config = ConfigDict(from_attributes=True)


class CourseReadWithChildren(CourseRead):
    """Схема курса с вложенными детьми (прямые потомки)."""
    children: List["CourseReadWithChildren"] = []

    model_config = ConfigDict(from_attributes=True)


class CourseTreeRead(CourseRead):
    """Схема курса с полным деревом потомков (рекурсивная структура)."""
    children: List["CourseTreeRead"] = []

    model_config = ConfigDict(from_attributes=True)


# Обновление моделей для корректной работы рекурсивных типов
CourseReadWithChildren.model_rebuild()
CourseTreeRead.model_rebuild()


class CourseMoveRequest(BaseModel):
    """
    Перемещение курса в иерархии.

    Правила:
    - `new_parent_id = null` → курс становится корневым.
    - Триггер БД запрещает циклы и самоссылки.
    """
    new_parent_id: Optional[int] = Field(
        None,
        description="ID нового родительского курса. Если null, курс становится корневым.",
        examples=[None, 10],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"new_parent_id": 10},
                {"new_parent_id": None},
            ]
        }
    )
