from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime  # импортируем datetime для использования в модели

from pydantic import BaseModel, Field, ConfigDict

from app.schemas.checking import StudentAnswer, CheckResult


class AttemptCreate(BaseModel):
    """
    Схема создания попытки.

    Используется при stateful-проверке:
    - POST /api/v1/attempts
    """
    user_id: int = Field(
        ...,
        description="ID пользователя, который проходит попытку.",
    )
    course_id: Optional[int] = Field(
        default=None,
        description="ID курса, если попытка привязана к конкретному курсу.",
    )
    source_system: Optional[str] = Field(
        default="lms",
        description="Источник создания попытки (lms_web, tg_bot, import и т.п.).",
    )
    meta: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Произвольные метаданные (таймлимит, название контрольной и т.п.).",
    )


class AttemptUpdate(BaseModel):
    """
    Схема обновления попытки (частичное обновление).

    В первую очередь пригодится для:
    - установки finished_at,
    - изменения статуса в meta (например, 'finished': true).
    """
    course_id: Optional[int] = Field(
        default=None,
        description="Обновлённый ID курса (если нужно).",
    )
    source_system: Optional[str] = Field(
        default=None,
        description="Обновлённый источник попытки.",
    )
    meta: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Обновлённый JSON метаданных.",
    )
    # finished_at трогает доменная логика, а не клиент.


class AttemptRead(BaseModel):
    """
    Базовое представление попытки для ответов API.
    """

    id: int = Field(..., description="ID попытки", examples=[1, 5])
    user_id: int = Field(..., description="ID пользователя, который проходит попытку", examples=[10, 15])
    course_id: Optional[int] = Field(None, description="ID курса, если попытка привязана к курсу", examples=[1, 5, None])

    # 👇 ключевая правка: datetime вместо str
    created_at: Optional[datetime] = Field(None, description="Время создания попытки", examples=["2026-02-16T12:00:00Z"])
    finished_at: Optional[datetime] = Field(None, description="Время завершения попытки (null, если попытка не завершена)", examples=["2026-02-16T13:00:00Z", None])

    source_system: Optional[str] = Field(None, description="Источник создания попытки", examples=["web", "tg_bot", "lms"])
    meta: Optional[Dict[str, Any]] = Field(None, description="Произвольные метаданные попытки", examples=[{}, {"time_limit": 3600, "task_ids": [1, 2, 3]}])

    # Learning Engine V1, этап 4
    time_expired: bool = Field(
        default=False,
        description="Попытка помечена как просроченная по tasks.time_limit_sec",
    )
    # Learning Engine V1, этап 3.5
    cancelled_at: Optional[datetime] = Field(
        None,
        description="Время аннулирования попытки (null, если не отменена)",
    )
    cancel_reason: Optional[str] = Field(
        None,
        description="Причина аннулирования (опционально)",
    )

    model_config = ConfigDict(from_attributes=True)


# ---------- Аннулирование попытки (этап 3.5) ----------


class AttemptCancelRequest(BaseModel):
    """Тело запроса для POST /attempts/{id}/cancel (опционально)."""

    reason: Optional[str] = Field(
        None,
        description="Причина аннулирования (например, user_exit_to_main_menu).",
    )


class AttemptCancelResponse(BaseModel):
    """Ответ для POST /attempts/{id}/cancel."""

    attempt_id: int = Field(..., description="ID попытки")
    status: str = Field("cancelled", description="Статус: cancelled")
    cancelled_at: Optional[datetime] = Field(
        ...,
        description="Время аннулирования (ISO8601)",
    )
    already_cancelled: bool = Field(
        False,
        description="True, если попытка уже была отменена (идемпотентный вызов)",
    )


# ---------- Результаты по задачам внутри попытки ----------


class AttemptTaskResultShort(BaseModel):
    """
    Краткая информация о результате по конкретной задаче
    внутри попытки (для GET /attempts/{id} и summary).
    """

    task_id: int = Field(..., description="ID задачи", examples=[1, 5])
    score: int = Field(..., description="Набранный балл", examples=[10, 5, 0])
    max_score: int = Field(..., description="Максимальный балл", examples=[10, 20])
    is_correct: Optional[bool] = Field(
        default=None,
        description="True/False/None (для задач с ручной проверкой)",
        examples=[True, False, None],
    )
    answer_json: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Сохранённый ответ ученика по задаче (как в task_results.answer_json)",
        examples=[{"type": "SC", "response": {"selected_option_ids": ["A"]}}, None],
    )


class AttemptWithResults(BaseModel):
    """
    Детальное представление попытки:
    - сама попытка,
    - список результатов по задачам,
    - суммарные баллы.
    Learning Engine V1 (этап 4): опционально attempts_used, attempts_limit_effective, last_based_status.
    """

    attempt: AttemptRead = Field(
        ...,
        description="Метаданные попытки.",
    )
    results: List[AttemptTaskResultShort] = Field(
        ...,
        description="Результаты по задачам в рамках попытки.",
    )
    total_score: int = Field(
        ...,
        description="Суммарный набранный балл по всем задачам попытки.",
    )
    total_max_score: int = Field(
        ...,
        description="Суммарный максимальный балл по всем задачам попытки.",
    )
    # Learning Engine V1, этап 4 (optional, backward-compatible)
    attempts_used: Optional[int] = Field(
        None,
        description="Число завершённых попыток по задаче контекста (по первой задане попытки).",
    )
    attempts_limit_effective: Optional[int] = Field(
        None,
        description="Эффективный лимит попыток: override -> task.max_attempts -> 3.",
    )
    last_based_status: Optional[str] = Field(
        None,
        description="Статус по последней завершённой попытке: PASSED | FAILED | BLOCKED_LIMIT | IN_PROGRESS.",
    )


# ---------- Схемы для POST /attempts/{id}/answers ----------


class AttemptAnswerItem(BaseModel):
    """
    Один ответ в рамках попытки.

    Требуем, чтобы был указан хотя бы task_id или external_uid.
    Тип задачи и сама структура ответа — через StudentAnswer.
    """

    task_id: int | None = Field(
        default=None,
        description="ID задачи в БД. Обязателен, если не указан external_uid.",
        examples=[1, 5, None],
    )
    external_uid: str | None = Field(
        default=None,
        description="Внешний устойчивый ID задачи. Обязателен, если не указан task_id.",
        examples=["TASK-SC-001", "TASK-MC-002", None],
    )
    answer: StudentAnswer = Field(
        ...,
        description=(
            "Ответ ученика на данную задачу. "
            "Поля type/response должны соответствовать task_content."
        ),
        examples=[
            {
                "type": "SC",
                "response": {"selected_option_ids": ["A"]}
            },
            {
                "type": "MC",
                "response": {"selected_option_ids": ["A", "B"]}
            },
        ],
    )


class AttemptAnswersRequest(BaseModel):
    """
    Тело запроса для POST /attempts/{id}/answers.
    """

    items: List[AttemptAnswerItem] = Field(
        ...,
        description="Список ответов по задачам внутри попытки.",
    )


class AttemptAnswerResult(BaseModel):
    """
    Один элемент результата проверки внутри попытки.
    """

    task_id: int = Field(
        ...,
        description="ID задачи, к которой относится результат.",
    )
    check_result: CheckResult = Field(
        ...,
        description="Результат проверки ответа по данной задаче.",
    )


class AttemptAnswersResponse(BaseModel):
    """
    Ответ для POST /attempts/{id}/answers.
    """

    attempt_id: int = Field(..., description="ID попытки", examples=[1, 5])
    results: List[AttemptAnswerResult] = Field(
        ..., description="Результаты проверки по каждой задаче", examples=[[]]
    )
    total_score_delta: int = Field(
        ...,
        description="Суммарный набранный балл только по этим присланным ответам",
        examples=[15, 25, 0],
    )
    total_max_score_delta: int = Field(
        ...,
        description="Суммарный максимальный балл только по этим присланным ответам",
        examples=[20, 30, 0],
    )


class AttemptFinishResponse(AttemptWithResults):
    """
    Ответ для POST /attempts/{id}/finish.

    Наследуемся от AttemptWithResults — возвращаем полную картину:
    попытка + все результаты + суммы баллов.
    """

    pass
