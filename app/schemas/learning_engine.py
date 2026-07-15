"""
Схемы результатов Learning Engine V1 (сервисный слой, этап 2).

Типы для next-item, task state, course state без привязки к REST.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Optional


NextItemType = Literal[
    "material",
    "task",
    "none",
    "blocked_dependency",
    "blocked_limit",
]


@dataclass(frozen=True)
class NextItemResult:
    """Результат определения следующего шага для студента."""

    type: NextItemType
    course_id: Optional[int] = None
    # Корневой курс дерева, в котором лежит элемент (root). Отличается от
    # course_id, когда элемент находится в листовом подкурсе. Нужен SPW для
    # построения навигации по корневым курсам (tsk-127).
    root_course_id: Optional[int] = None
    material_id: Optional[int] = None
    task_id: Optional[int] = None
    reason: Optional[str] = None
    dependency_course_id: Optional[int] = None


TaskStateType = Literal[
    "OPEN",
    "IN_PROGRESS",
    "PASSED",
    "FAILED",
    "BLOCKED_LIMIT",
]


@dataclass(frozen=True)
class TaskStateResult:
    """Состояние задания по правилу последней завершённой попытки."""

    state: TaskStateType
    last_attempt_id: Optional[int] = None
    last_score: Optional[int] = None
    last_max_score: Optional[int] = None
    last_finished_at: Optional[datetime] = None
    attempts_used: int = 0
    attempts_limit_effective: int = 3
    # Сохранённый ответ ученика из последнего task_result (tsk-222): нужен SPW,
    # чтобы показать «Мой ответ» read-only на пройденном/на-проверке задании.
    # Это ответ САМОГО ученика (StudentAnswer), эталон сюда не попадает.
    last_answer_json: Optional[dict[str, Any]] = None
    last_is_correct: Optional[bool] = None
    last_checked_at: Optional[datetime] = None


CourseStateType = Literal[
    "NOT_STARTED",
    "IN_PROGRESS",
    "COMPLETED",
    "BLOCKED_DEPENDENCY",
]


@dataclass(frozen=True)
class CourseState:
    """Состояние студента по курсу (для маршрутизации)."""

    state: CourseStateType
    course_id: int
