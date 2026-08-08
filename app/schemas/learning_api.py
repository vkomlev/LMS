"""
Pydantic-схемы запросов и ответов Learning API (этап 3).

Эндпоинты: next-item, materials/complete, tasks/start-or-get-attempt,
tasks/state, request-help, teacher/task-limits/override.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ----- Next item -----

NextItemType = Literal[
    "material", "task", "none", "blocked_dependency", "blocked_limit"
]


class NextItemResponse(BaseModel):
    type: NextItemType
    course_id: Optional[int] = None
    root_course_id: Optional[int] = Field(
        None,
        description="Корневой курс дерева элемента (root). Отличается от course_id, "
        "если элемент в листовом подкурсе. SPW строит навигацию по корням (tsk-127).",
    )
    material_id: Optional[int] = None
    task_id: Optional[int] = None
    reason: Optional[str] = None
    dependency_course_id: Optional[int] = None
    dependency_course_title: Optional[str] = Field(
        None, description="tsk-231: название курса-зависимости для UI (SPW/TG_LMS)."
    )
    dependency_course_uid: Optional[str] = None


# ----- Material complete -----

class MaterialCompleteRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")


class MaterialCompleteResponse(BaseModel):
    ok: bool = True
    student_id: int
    material_id: int
    status: Literal["completed"] = "completed"
    completed_at: Optional[datetime] = None


# ----- Skip item -----

class LearningSkipRequest(BaseModel):
    student_id: int = Field(..., description="ID СЃС‚СѓРґРµРЅС‚Р°")


class LearningSkipResponse(BaseModel):
    ok: bool = True
    student_id: int
    kind: Literal["material", "task"]
    material_id: Optional[int] = None
    task_id: Optional[int] = None
    status: Literal["skipped"] = "skipped"
    skipped_at: datetime


# ----- Start or get attempt -----

class StartOrGetAttemptRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    source_system: str = Field(default="learning_api", description="Источник")
    root_course_id: Optional[int] = Field(
        default=None,
        description=(
            "Корневой курс, которым ученик пришёл к заданию (tsk-264). Узел графа "
            "переиспользуется несколькими курсами, поэтому попытки считаются в "
            "границах корня: новый курс — свежие попытки. Клиент знает корень из "
            "дерева/URL. Если не передан — сервер определяет его сам, когда узел "
            "лежит ровно в одном активном курсе ученика."
        ),
    )


class StartOrGetAttemptResponse(BaseModel):
    attempt_id: int
    user_id: int
    course_id: Optional[int] = None
    root_course_id: Optional[int] = Field(
        default=None,
        description=(
            "Корневой курс, в границах которого считаются попытки (tsk-264). "
            "NULL — путь неизвестен: попытка не расходует лимит ни в одном курсе."
        ),
    )
    created_at: datetime
    finished_at: Optional[datetime] = None
    source_system: str


# ----- Task state -----

TaskStateType = Literal[
    "OPEN", "IN_PROGRESS", "PASSED", "FAILED", "BLOCKED_LIMIT"
]


class TaskStateResponse(BaseModel):
    task_id: int
    student_id: int
    state: TaskStateType
    last_attempt_id: Optional[int] = None
    last_score: Optional[int] = None
    last_max_score: Optional[int] = None
    last_finished_at: Optional[datetime] = None
    attempts_used: int = 0
    attempts_limit_effective: int = 3
    # tsk-222: сохранённый ответ ученика по последнему task_result. SPW показывает
    # его как «Мой ответ» (read-only) на пройденном/на-проверке/заблокированном
    # задании. Содержит только ответ ученика (StudentAnswer), эталон не раскрывается.
    last_answer_json: Optional[dict[str, Any]] = Field(
        default=None,
        description="Сохранённый ответ ученика (task_results.answer_json) последнего результата",
    )
    last_is_correct: Optional[bool] = Field(
        default=None,
        description="is_correct последнего результата (None до ручной проверки SA_COM/TA)",
    )
    last_checked_at: Optional[datetime] = Field(
        default=None,
        description="checked_at последнего результата (None = на проверке у учителя)",
    )
    # tsk-227: флаг обязательного вложения из solution_rules.requires_attachment.
    # Клиент (SPW/TG_LMS) по нему включает обязательную загрузку файла и блокирует
    # submit без вложения. Сервер — источник истины (форс на сдаче), это лишь UX-сигнал.
    requires_attachment: bool = Field(
        default=False,
        description=(
            "Требуется ли обязательное вложение для зачёта (solution_rules.requires_attachment, "
            "tsk-227). Клиент показывает обязательную загрузку файла."
        ),
    )
    # tsk-396: гибридный режим проверки. Клиент по нему объясняет ученику, что
    # числовая часть сверится сразу, а зачёт поставит преподаватель после
    # проверки диаграммы — иначе `is_correct=null` при непустом feedback
    # выглядит как «проверка сломалась». Как и requires_attachment — UX-сигнал,
    # не гейт: сервер источник истины (score держится нулевым до ручной оценки).
    partial_auto_check: bool = Field(
        default=False,
        description=(
            "Гибридный режим: часть ответа проверяется автоматически сразу, финальный "
            "зачёт — за преподавателем (solution_rules.partial_auto_check, tsk-396)."
        ),
    )
    # tsk-547: есть ли у задания эталон для сверки `response.value`. Ученику
    # `solution_rules` не отдаются (видимость полей — отдельный слой, tsk-460),
    # поэтому «поле ответа бессмысленно» без такого сигнала на клиенте
    # не вычислить. Как и requires_attachment — это UX-сигнал, не гейт.
    has_reference_answer: bool = Field(
        default=True,
        description=(
            "Есть ли эталон для сверки response.value. False только у типов с коротким/"
            "табличным ответом (SA/SA_COM/TBL_COM) без заведённого эталона: отвечать "
            "нужно комментарием или файлом, и клиент может не показывать поле ввода "
            "ответа (tsk-547). Для остальных типов всегда true. Default true — "
            "сохраняет прежнее поведение клиента, если поле не пришло."
        ),
    )


# ----- Request help -----

class RequestHelpRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    message: Optional[str] = Field(default=None, max_length=2000)


class RequestHelpResponse(BaseModel):
    ok: bool = True
    event_id: int
    deduplicated: bool = False
    request_id: Optional[int] = Field(None, description="ID заявки в help_requests (этап 3.8, опционально)")


# ----- Лестница помощи, сторона ученика (tsk-303) -----


class StudentHelpReplyItem(BaseModel):
    """Ответ преподавателя в заявке — так, как его видит ученик."""
    body: str
    created_at: datetime


class StudentHelpRequestResponse(BaseModel):
    """Текущая заявка помощи ученика по заданию.

    Признаки `can_*` считает сервер: гейты уровней лестницы — часть правил, и
    второй их экземпляр в клиенте неизбежно с ними разъедется.
    """
    request_id: int
    status: str = Field(..., description="open | closed")
    request_type: str = Field(..., description="manual_help | individual_review")
    message: Optional[str] = Field(None, description="Текст исходного вопроса ученика")
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    resolution_comment: Optional[str] = None
    reopen_count: int = Field(0, description="Сколько раз ученик возвращал заявку")
    webinar_link: Optional[str] = Field(
        None, description="Ссылка на разбор; живёт, пока заявка открыта"
    )
    review_understood: Optional[bool] = Field(
        None, description="Оценка после разбора: None — ещё не оценивал"
    )
    escalated_to_methodist_at: Optional[datetime] = None
    replies: list[StudentHelpReplyItem] = Field(default_factory=list)
    can_reopen: bool = False
    can_request_individual_review: bool = False
    can_rate_review: bool = False


class HelpRequestReopenResponse(BaseModel):
    """Ответ POST /learning/help-requests/{id}/reopen."""
    request_id: int
    status: str = "open"
    reopen_count: int
    can_request_individual_review: bool = True


class IndividualReviewResponse(BaseModel):
    """Ответ POST /learning/help-requests/{id}/request-individual-review."""
    request_id: int
    request_type: str = "individual_review"
    already: bool = Field(False, description="true — разбор был запрошен ранее (повторный клик)")


class RateReviewRequest(BaseModel):
    """Тело POST /learning/help-requests/{id}/rate-review."""
    understood: bool = Field(..., description="true — после разбора всё понятно")


class RateReviewResponse(BaseModel):
    """Ответ на оценку разбора."""
    request_id: int
    understood: bool
    status: str = Field(..., description="closed при understood=true, иначе open")
    escalated: bool = Field(False, description="true — заявка ушла методисту")


# ----- Hint events (этап 3.6) -----

HintType = Literal["text", "video"]
HintAction = Literal["open"]


class HintEventRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    attempt_id: int = Field(..., description="ID попытки")
    hint_type: HintType = Field(..., description="Тип подсказки: text | video")
    hint_index: int = Field(..., ge=0, description="Индекс подсказки (0-based)")
    action: HintAction = Field("open", description="Действие (open; enum с возможностью расширения)")
    source: str = Field(..., description="Источник события, например student_execute")


class HintEventResponse(BaseModel):
    ok: bool = True
    deduplicated: bool = False
    event_id: int = Field(..., description="ID записи в learning_events")


# ----- Teacher override -----

class TaskLimitOverrideRequest(BaseModel):
    student_id: int = Field(..., description="ID студента")
    task_id: int = Field(..., description="ID задания")
    mode: Literal["explicit", "grant_same_again"] = Field(
        default="explicit",
        description=(
            "explicit — задать точное число (max_attempts_override обязателен; "
            "путь ручного ввода на /teacher/help-requests и бот). "
            "grant_same_again — добавить БАЗОВЫЙ лимит задания (tasks.max_attempts "
            "или DEFAULT_MAX_ATTEMPTS, без учёта текущего override) к текущему "
            "эффективному лимиту; число считает сервер (tsk-335)."
        ),
    )
    max_attempts_override: Optional[int] = Field(
        default=None,
        gt=0,
        description="Обязателен при mode=explicit; запрещён при mode=grant_same_again",
    )
    reason: Optional[str] = None
    updated_by: int = Field(..., description="ID учителя/методиста")

    @model_validator(mode="after")
    def _check_mode_fields(self) -> "TaskLimitOverrideRequest":
        if self.mode == "explicit" and self.max_attempts_override is None:
            raise ValueError("max_attempts_override обязателен при mode=explicit")
        if self.mode == "grant_same_again" and self.max_attempts_override is not None:
            raise ValueError(
                "max_attempts_override запрещён при mode=grant_same_again — "
                "число вычисляет сервер"
            )
        return self


class TaskLimitOverrideResponse(BaseModel):
    ok: bool = True
    student_id: int
    task_id: int
    max_attempts_override: int = Field(description="Итоговый лимит попыток после операции")
    previous_max_attempts_override: Optional[int] = Field(
        default=None, description="Значение до операции; null — override не было"
    )
    mode: Literal["explicit", "grant_same_again"] = "explicit"
    base_attempts_added: Optional[int] = Field(
        default=None, description="Сколько добавлено к эффективному лимиту (только grant_same_again)"
    )
    already: bool = Field(
        default=False,
        description="True — сработал дебаунс повторного клика, состояние не менялось",
    )
    updated_at: datetime
