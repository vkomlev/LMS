from __future__ import annotations

from typing import Any, Optional
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskResultCreate(BaseModel):
    """
    Схема создания результата по задаче.

    Новые поля (attempt_id, answer_json, max_score, is_correct, source_system)
    делаем опциональными, чтобы не ломать существующие вызовы.
    """
    score: int = Field(..., description="Набранный балл за задачу", ge=0, examples=[10, 5, 0])
    user_id: int = Field(..., description="ID пользователя, выполнившего задачу", examples=[10, 15])
    task_id: int = Field(..., description="ID задачи", examples=[1, 5])
    metrics: Optional[Any] = Field(None, description="Метрики качества ответа (произвольный JSON)", examples=[{}, {"comment": "Хороший ответ"}])
    count_retry: Optional[int] = Field(0, description="Количество попыток выполнения задачи", ge=0, examples=[0, 1, 2])

    attempt_id: Optional[int] = Field(None, description="ID попытки, если результат привязан к попытке", examples=[1, 5])
    answer_json: Optional[Any] = Field(None, description="Исходный ответ ученика (StudentAnswer)", examples=[{"type": "SC", "response": {"selected_option_ids": ["A"]}}])
    max_score: Optional[int] = Field(None, description="Максимальный балл за задачу на момент проверки", ge=0, examples=[10, 20])
    is_correct: Optional[bool] = Field(None, description="Флаг правильности ответа (null для задач с ручной проверкой)", examples=[True, False, None])
    source_system: Optional[str] = Field("system", description="Источник системы, записавшей результат", examples=["web", "tg_bot", "system"])


class TaskResultUpdate(BaseModel):
    """
    Схема обновления результата по задаче.
    """
    score: Optional[int] = Field(None, description="Набранный балл за задачу", ge=0, examples=[10, 5, 0])
    metrics: Optional[Any] = Field(None, description="Метрики качества ответа (произвольный JSON)", examples=[{}, {"comment": "Обновленный комментарий"}])
    count_retry: Optional[int] = Field(None, description="Количество попыток выполнения задачи", ge=0, examples=[0, 1, 2])

    attempt_id: Optional[int] = Field(None, description="ID попытки", examples=[1, 5])
    answer_json: Optional[Any] = Field(None, description="Исходный ответ ученика (StudentAnswer)", examples=[{"type": "SC", "response": {"selected_option_ids": ["A"]}}])
    max_score: Optional[int] = Field(None, description="Максимальный балл за задачу", ge=0, examples=[10, 20])
    is_correct: Optional[bool] = Field(None, description="Флаг правильности ответа", examples=[True, False, None])
    checked_at: Optional[datetime] = Field(None, description="Время проверки результата", examples=["2026-02-16T13:00:00Z"])
    checked_by: Optional[int] = Field(None, description="ID пользователя, выполнившего проверку (null для автоматической проверки)", examples=[2, 5, None])
    source_system: Optional[str] = Field(None, description="Источник системы, записавшей результат", examples=["web", "tg_bot", "system"])


class TaskResultRead(BaseModel):
    """
    Схема чтения результата по задаче.
    """
    id: int = Field(..., description="ID результата", examples=[1, 5])
    score: int = Field(..., description="Набранный балл за задачу", examples=[10, 5, 0])
    user_id: int = Field(..., description="ID пользователя, выполнившего задачу", examples=[10, 15])
    task_id: int = Field(..., description="ID задачи", examples=[1, 5])
    submitted_at: datetime = Field(..., description="Время сдачи ответа", examples=["2026-02-16T12:00:00Z"])
    metrics: Optional[Any] = Field(None, description="Метрики качества ответа (произвольный JSON)", examples=[{}, {"comment": "Хороший ответ"}])
    count_retry: int = Field(..., description="Количество попыток выполнения задачи", examples=[0, 1, 2])
    received_at: datetime = Field(..., description="Время начала выполнения задачи", examples=["2026-02-16T12:00:00Z"])

    attempt_id: Optional[int] = Field(None, description="ID попытки, если результат привязан к попытке", examples=[1, 5, None])
    answer_json: Optional[Any] = Field(None, description="Исходный ответ ученика (StudentAnswer)", examples=[{"type": "SC", "response": {"selected_option_ids": ["A"]}}, None])
    max_score: Optional[int] = Field(None, description="Максимальный балл за задачу на момент проверки", examples=[10, 20, None])
    is_correct: Optional[bool] = Field(None, description="Флаг правильности ответа (null для задач с ручной проверкой)", examples=[True, False, None])
    checked_at: Optional[datetime] = Field(None, description="Время проверки результата (null для непроверенных)", examples=["2026-02-16T12:00:05Z", None])
    checked_by: Optional[int] = Field(None, description="ID пользователя, выполнившего проверку (null для автоматической проверки)", examples=[2, 5, None])
    source_system: str = Field(..., description="Источник системы, записавшей результат", examples=["web", "tg_bot", "system"])

    model_config = ConfigDict(from_attributes=True)
