from __future__ import annotations

from typing import Any, Optional, List, Literal

from pydantic import BaseModel, ConfigDict


class TaskCreate(BaseModel):
    """
    Схема создания задания.

    external_uid и max_score опциональны, чтобы не ломать существующие клиенты:
    - external_uid — устойчивый ID из внешней системы (для импорта),
    - max_score   — максимальный балл за задачу.
    """
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any] = None

    external_uid: Optional[str] = None
    max_score: Optional[int] = None


class TaskUpdate(BaseModel):
    """
    Схема обновления задания (полевая, все поля опциональны).
    """
    task_content: Optional[Any] = None
    course_id: Optional[int] = None
    difficulty_id: Optional[int] = None
    solution_rules: Optional[Any] = None

    external_uid: Optional[str] = None
    max_score: Optional[int] = None


class TaskRead(BaseModel):
    """
    Схема чтения задания.
    """
    id: int
    task_content: Any
    course_id: int
    difficulty_id: int
    solution_rules: Optional[Any]

    external_uid: Optional[str] = None
    max_score: Optional[int] = None

    class Config:
        model_config = ConfigDict(from_attributes=True)

class TaskUpsertItem(BaseModel):
    """
    Один элемент для массового upsert'а задачи.
    Поля соответствуют структуре TaskCreate, плюс обязательный external_uid.
    """
    external_uid: str
    course_id: int
    difficulty_id: int
    task_content: Any
    solution_rules: Any | None = None
    max_score: int | None = None


class TaskBulkUpsertRequest(BaseModel):
    """
    Тело запроса для массового upsert'а задач.
    """
    items: List[TaskUpsertItem]


class TaskBulkUpsertResultItem(BaseModel):
    """
    Один элемент результата bulk-upsert'а.
    """
    external_uid: str
    action: Literal["created", "updated"]
    id: int


class TaskBulkUpsertResponse(BaseModel):
    """
    Ответ bulk-upsert'а задач.
    """
    results: List[TaskBulkUpsertResultItem]
