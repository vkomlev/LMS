from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.task_content import TaskType, TaskContent
from app.schemas.solution_rules import SolutionRules


class StudentResponse(BaseModel):
    """
    Ответ ученика в терминах конкретной задачи.

    Для разных типов задач используются разные поля:
    - SC/MC: selected_option_ids;
    - SA/SA_COM: value;
    - TA: text.
    """

    selected_option_ids: Optional[List[str]] = Field(
        default=None,
        description="Список выбранных ID вариантов ответа (SC/MC).",
    )
    value: Optional[str] = Field(
        default=None,
        description="Краткий текстовый/числовой ответ (SA/SA_COM).",
    )
    text: Optional[str] = Field(
        default=None,
        description="Развёрнутый ответ (TA).",
    )
    meta: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Произвольные метаданные (время, номер попытки, источник и т.п.).",
    )


class StudentAnswer(BaseModel):
    """
    Обёртка вокруг ответа ученика.

    Может использоваться как для stateless-проверки, так и внутри попыток (attempts).
    """

    task_id: Optional[int] = Field(
        default=None,
        description="ID задачи в БД (если известен).",
    )
    external_uid: Optional[str] = Field(
        default=None,
        description="Внешний устойчивый идентификатор задачи (если используется).",
    )
    type: TaskType = Field(
        ...,
        description="Тип задачи, должен совпадать с task_content.type.",
    )
    response: StudentResponse = Field(
        ...,
        description="Структура с конкретным ответом ученика.",
    )


class CheckResultDetails(BaseModel):
    """
    Расширенная информация о проверке ответа.

    Поля опциональны и используются по ситуации:
    - для SC/MC: correct_options, user_options;
    - для SA/SA_COM: matched_short_answer;
    - для TA: rubric_scores.
    """

    correct_options: Optional[List[str]] = Field(
        default=None,
        description="Список правильных вариантов (для задач с выбором).",
    )
    user_options: Optional[List[str]] = Field(
        default=None,
        description="Список выбранных учеником вариантов (SC/MC).",
    )
    matched_short_answer: Optional[str] = Field(
        default=None,
        description="Строка, с которой был сопоставлен короткий ответ (если найдено совпадение).",
    )
    rubric_scores: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Список оценок по рубрикам для развёрнутых ответов (TA).",
    )


class CheckFeedback(BaseModel):
    """
    Текстовая обратная связь по результатам проверки.
    """

    general: Optional[str] = Field(
        default=None,
        description="Общая обратная связь для ученика.",
    )
    by_option: Optional[Dict[str, str]] = Field(
        default=None,
        description="Обратная связь по конкретным вариантам ответа (ключ — ID варианта).",
    )


class CheckResult(BaseModel):
    """
    Результат проверки одного ответа ученика.
    """

    is_correct: Optional[bool] = Field(
        default=None,
        description=(
            "Флаг правильности ответа. "
            "Для задач, требующих только ручной проверки (TA), может быть None."
        ),
    )
    score: int = Field(
        ...,
        description="Набранный балл за ответ.",
    )
    max_score: int = Field(
        ...,
        description="Максимальный балл за данную задачу.",
    )
    details: Optional[CheckResultDetails] = Field(
        default=None,
        description="Расширенная информация о проверке.",
    )
    feedback: Optional[CheckFeedback] = Field(
        default=None,
        description="Текстовая обратная связь для ученика.",
    )


# ---------- Stateless-проверка ----------


class TaskWithAnswer(BaseModel):
    """
    Описание задачи + правил проверки + ответа ученика
    для stateless-проверки (без привязки к БД).
    """

    task_content: TaskContent = Field(
        ...,
        description="JSON-описание задания (как в tasks.task_content).",
    )
    solution_rules: SolutionRules = Field(
        ...,
        description="JSON-правила проверки (как в tasks.solution_rules).",
    )
    answer: StudentAnswer = Field(
        ...,
        description="Ответ ученика на задачу.",
    )


class SingleCheckRequest(TaskWithAnswer):
    """
    Запрос на проверку одной задачи в stateless-режиме.
    Наследуемся от TaskWithAnswer, чтобы не дублировать поля.
    """

    pass


class BatchCheckRequest(BaseModel):
    """
    Запрос на пакетную stateless-проверку задач.
    """

    items: List[TaskWithAnswer] = Field(
        ...,
        description="Список задач с ответами для пакетной проверки.",
    )


class BatchCheckItemResult(BaseModel):
    """
    Результат проверки одной задачи внутри батча.
    """

    index: int = Field(
        ...,
        description="Индекс элемента в исходном списке (BatchCheckRequest.items).",
    )
    result: CheckResult = Field(
        ...,
        description="Результат проверки данной задачи.",
    )


class BatchCheckResponse(BaseModel):
    """
    Ответ на пакетную stateless-проверку задач.
    """

    results: List[BatchCheckItemResult] = Field(
        ...,
        description="Список результатов проверки по всем элементам запроса.",
    )
