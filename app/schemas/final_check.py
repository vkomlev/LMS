"""tsk-994 (П5): контракт итогового среза после курса — рубильник курса,
состояние среза, старт и ответ на задание среза."""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.checking import CheckResult, StudentAnswer

#: Размер среза по умолчанию и пределы, которые принимает рубильник.
DEFAULT_FINAL_CHECK_SIZE = 10
MAX_FINAL_CHECK_SIZE = 50


class CourseFinalCheckConfig(BaseModel):
    """Формат `courses.final_check_config` (JSONB) и тело `PUT /courses/{id}/final-check`.

    Действует на КОРНЕВОМ курсе. Пустое поле (NULL) читается как
    `{"enabled": false, "size": 10}`.
    """

    enabled: bool = Field(False, description="Включён ли итоговый срез на курсе")
    size: int = Field(
        DEFAULT_FINAL_CHECK_SIZE,
        ge=1,
        le=MAX_FINAL_CHECK_SIZE,
        description="Сколько заданий в срезе (из пройденных учеником)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"enabled": True, "size": 10}, {"enabled": False, "size": 10}]
        }
    )


class FinalCheckItem(BaseModel):
    """Задание среза."""

    task_id: int = Field(..., description="ID задания")
    external_uid: Optional[str] = Field(None, description="Внешний код задания")
    title: str = Field(..., description="Название задания (или начало условия)")
    task_type: Optional[str] = Field(None, description="Тип задания (SA, SC, …)")
    chapter_id: int = Field(..., description="Глава курса (подкурс первого уровня или сам корень)")
    chapter_title: str = Field(..., description="Название главы")
    answered: bool = Field(False, description="Ответ на задание в срезе уже дан")
    is_correct: Optional[bool] = Field(
        None, description="Верно ли отвечено; null — ещё не отвечено"
    )


class FinalCheckChapterErrors(BaseModel):
    """Глава, в которой в срезе есть ошибки."""

    course_id: int = Field(..., description="ID главы (курса)")
    course_uid: Optional[str] = Field(None, description="Код главы")
    title: str = Field(..., description="Название главы")
    task_ids: List[int] = Field(default_factory=list, description="Ошибочные задания главы")


class FinalCheckResult(BaseModel):
    """Итог среза — только диагностика (Р4): статус курса он не меняет."""

    total: int = Field(..., ge=0, description="Заданий в срезе")
    correct: int = Field(..., ge=0, description="Верных ответов")
    ratio: float = Field(..., ge=0, le=1, description="Доля верных")
    skill_retained: bool = Field(
        ..., description="«Навык сохранился» — верных не меньше 80 %"
    )
    chapters_with_errors: List[FinalCheckChapterErrors] = Field(
        default_factory=list, description="Главы с ошибками, в порядке программы"
    )
    scheduled_for_review: List[int] = Field(
        default_factory=list,
        description=(
            "Ошибочные задания, поставленные на повторение (если на курсе включено "
            "повторение tsk-992)"
        ),
    )


class FinalCheckRun(BaseModel):
    """Прогон среза: зафиксированный состав, ответы и итог."""

    run_id: int = Field(..., description="ID прогона")
    course_id: int = Field(..., description="Корневой курс")
    started_at: datetime = Field(..., description="Когда срез открыт впервые")
    finished_at: Optional[datetime] = Field(None, description="Когда отвечено последнее задание")
    items: List[FinalCheckItem] = Field(default_factory=list, description="Задания среза")
    result: Optional[FinalCheckResult] = Field(None, description="Итог; null — срез не окончен")


FinalCheckStatus = Literal["disabled", "locked", "closed", "ready", "in_progress", "finished"]


class FinalCheckStatusResponse(BaseModel):
    """Ответ `GET /learning/courses/{course_id}/final-check`."""

    course_id: int = Field(..., description="Корневой курс")
    status: FinalCheckStatus = Field(
        ...,
        description=(
            "disabled — срез на курсе выключен; locked — курс ещё не завершён; "
            "closed — тариф закрывает работу в курсе (выпускник, tsk-673); "
            "ready — можно начать; in_progress — начат; finished — пройден"
        ),
    )
    size: int = Field(..., ge=0, description="Размер среза по настройке курса")
    run: Optional[FinalCheckRun] = Field(None, description="Прогон, если срез уже открывали")


class FinalCheckStartRequest(BaseModel):
    """Тело `POST /learning/courses/{course_id}/final-check/start`."""

    student_id: int = Field(..., description="ID ученика")


class FinalCheckAnswerRequest(BaseModel):
    """Тело `POST /learning/courses/{course_id}/final-check/{task_id}/answer`."""

    student_id: int = Field(..., description="ID ученика")
    answer: StudentAnswer = Field(..., description="Ответ ученика")


class FinalCheckAnswerResponse(BaseModel):
    """Итог ответа на задание среза."""

    task_id: int = Field(..., description="ID задания")
    task_result_id: int = Field(..., description="Записанный результат (source_system=final_check)")
    check_result: CheckResult = Field(..., description="Результат проверки ответа")
    is_correct: bool = Field(..., description="Ответ верный")
    run: FinalCheckRun = Field(..., description="Прогон после ответа (с итогом, если это был последний)")
