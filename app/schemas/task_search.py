"""Схема ответа «поиск задания в кабинете преподавателя» (tsk-353).

Краткая карточка результата поиска — ровно то, что нужно преподавателю на живом
уроке, чтобы опознать нужное задание среди списка и кликнуть в уже существующую
детальную карточку (tsk-349, ``GET /teacher/students/{student_id}/tasks/{task_id}/history``).
Сама схема НЕ содержит правила проверки/эталона — это отдаёт только эндпоинт
истории; поиск лишь помогает найти ``task_id``.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TaskSearchResult(BaseModel):
    """Одна карточка результата поиска."""

    task_id: int
    visible_id: str = Field(
        ..., description="Видимый ученику/учителю номер задания, формат id-<task_id> (tsk-309/311)"
    )
    title: str = Field(
        ..., description="Человекочитаемый заголовок/обрезанное условие (см. humanize_task_title)"
    )
    task_type: Optional[str] = Field(default=None, description="Тип задания (SC/MC/SA/SA_COM/TBL_COM/TA/…)")
    course_id: int
    course_title: Optional[str] = None
    difficulty: Optional[str] = Field(default=None, description="Название уровня сложности (difficulties.name_ru)")


class TaskSearchResponse(BaseModel):
    """Ответ поиска — запрос + список найденных заданий (может быть пустым)."""

    query: str
    results: List[TaskSearchResult] = Field(...)
