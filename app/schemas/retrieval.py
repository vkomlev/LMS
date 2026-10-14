"""tsk-992 (П3): контракт повторения по следам ошибок — рубильник курса,
список «пора повторить» и сдача повтора."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.checking import CheckResult, StudentAnswer


class CourseRetrievalConfig(BaseModel):
    """Формат `courses.retrieval_config` (JSONB) и тело `PUT /courses/{id}/retrieval`.

    Действует на КОРНЕВОМ курсе — по нему ученик идёт по программе. Пустое
    поле (NULL) читается как `{"enabled": false}`.
    """

    enabled: bool = Field(False, description="Включено ли повторение на курсе")

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"enabled": True}, {"enabled": False}]}
    )


class ReviewDueItem(BaseModel):
    """Задание, которое пора повторить."""

    task_id: int = Field(..., description="ID задания")
    external_uid: Optional[str] = Field(None, description="Внешний код задания")
    title: str = Field(..., description="Название задания (или начало условия)")
    task_type: Optional[str] = Field(None, description="Тип задания (SA, SC, …)")
    interval_stage: int = Field(
        ..., ge=0, le=2, description="Стадия интервала: 0 → 3 дня, 1 → 7, 2 → 21"
    )
    due_at: datetime = Field(..., description="Когда подошёл срок повтора")
    days_since_solved: Optional[int] = Field(
        None, ge=0, description="Сколько дней назад ученик решал задание в последний раз"
    )


class ReviewDueResponse(BaseModel):
    """Ответ `GET /learning/courses/{course_id}/review-due`."""

    course_id: int = Field(..., description="Корневой курс")
    items: List[ReviewDueItem] = Field(
        default_factory=list,
        description="Задания, которые пора повторить; пусто, если повторение выключено",
    )


class ReviewAnswerRequest(BaseModel):
    """Тело `POST /learning/courses/{course_id}/review/{task_id}/answer`."""

    student_id: int = Field(..., description="ID ученика")
    answer: StudentAnswer = Field(..., description="Ответ ученика")


class ReviewAnswerResponse(BaseModel):
    """Итог сдачи повтора."""

    task_id: int = Field(..., description="ID задания")
    task_result_id: int = Field(..., description="Записанный результат (source_system=retrieval)")
    attempt_id: int = Field(..., description="Курсовая попытка, в которую лёг ответ")
    check_result: CheckResult = Field(..., description="Результат проверки ответа")
    is_correct: bool = Field(..., description="Повтор засчитан")
    interval_stage: int = Field(..., ge=0, le=2, description="Стадия после повтора")
    next_due_at: Optional[datetime] = Field(
        None, description="Срок следующего повтора; null — задание закреплено"
    )
    completed: bool = Field(..., description="Задание закреплено (верный повтор на стадии 2)")
