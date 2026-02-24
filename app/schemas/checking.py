from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, Field, BeforeValidator

from app.schemas.task_content import TaskType, TaskContent
from app.schemas.solution_rules import SolutionRules

logger = logging.getLogger(__name__)


def _normalize_answer_type(v: Any) -> TaskType:
    """
    Нормализует тип ответа: алиас SA+COM приводится к каноническому SA_COM с логом deprecation.
    """
    if v == "SA+COM":
        logger.warning(
            "Deprecation: тип ответа 'SA+COM' устарел, используйте 'SA_COM'. "
            "Алиас будет удалён в будущей версии."
        )
        return "SA_COM"
    return v


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
        examples=[["A"], ["A", "B"], None],
    )
    value: Optional[str] = Field(
        default=None,
        description="Краткий текстовый/числовой ответ (SA/SA_COM).",
        examples=["42", "Python", None],
    )
    text: Optional[str] = Field(
        default=None,
        description="Развёрнутый ответ (TA).",
        examples=["Развернутый ответ ученика...", None],
    )
    meta: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Произвольные метаданные (время, номер попытки, источник и т.п.).",
        examples=[{}, {"attempt_number": 1}, None],
    )


class StudentAnswer(BaseModel):
    """
    Обёртка вокруг ответа ученика.

    Может использоваться как для stateless-проверки, так и внутри попыток (attempts).
    """

    task_id: Optional[int] = Field(
        default=None,
        description="ID задачи в БД (если известен).",
        examples=[1, 5, None],
    )
    external_uid: Optional[str] = Field(
        default=None,
        description="Внешний устойчивый идентификатор задачи (если используется).",
        examples=["TASK-SC-001", "TASK-MC-002", None],
    )
    type: Annotated[TaskType, BeforeValidator(_normalize_answer_type)] = Field(
        ...,
        description="Тип задачи, должен совпадать с task_content.type. Допустим алиас SA+COM (deprecated).",
        examples=["SC", "MC", "SA", "SA_COM", "TA"],
    )
    response: StudentResponse = Field(
        ...,
        description="Структура с конкретным ответом ученика.",
        examples=[
            {"selected_option_ids": ["A"]},
            {"value": "42"},
            {"text": "Развернутый ответ..."},
        ],
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
        examples=[["A"], ["A", "B"], None],
    )
    user_options: Optional[List[str]] = Field(
        default=None,
        description="Список выбранных учеником вариантов (SC/MC).",
        examples=[["A"], ["A", "B"], None],
    )
    matched_short_answer: Optional[str] = Field(
        default=None,
        description="Строка, с которой был сопоставлен короткий ответ (если найдено совпадение).",
        examples=["42", "Python", None],
    )
    rubric_scores: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Список оценок по рубрикам для развёрнутых ответов (TA).",
        examples=[[{"id": "content", "score": 5}], None],
    )


class CheckFeedback(BaseModel):
    """
    Текстовая обратная связь по результатам проверки.
    """

    general: Optional[str] = Field(
        default=None,
        description="Общая обратная связь для ученика.",
        examples=["Правильно!", "Частично правильно", None],
    )
    by_option: Optional[Dict[str, str]] = Field(
        default=None,
        description="Обратная связь по конкретным вариантам ответа (ключ — ID варианта).",
        examples=[{"A": "Правильно!", "B": "Неверно"}, None],
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
        examples=[True, False, None],
    )
    score: int = Field(
        ...,
        description="Набранный балл за ответ.",
        examples=[10, 5, 0],
    )
    max_score: int = Field(
        ...,
        description="Максимальный балл за данную задачу.",
        examples=[10, 20],
    )
    details: Optional[CheckResultDetails] = Field(
        default=None,
        description="Расширенная информация о проверке.",
        examples=[{"correct_options": ["A"], "user_options": ["A"]}, None],
    )
    feedback: Optional[CheckFeedback] = Field(
        default=None,
        description="Текстовая обратная связь для ученика.",
        examples=[{"general": "Правильно!"}, None],
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
