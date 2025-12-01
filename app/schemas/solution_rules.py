from __future__ import annotations

from typing import List, Optional, Literal

from pydantic import BaseModel, Field


ScoringMode = Literal["all_or_nothing", "partial", "custom"]


class PartialRule(BaseModel):
    """
    Правило частичного оценивания для задач с множественным выбором (MC)
    или сложных схем проверки.
    """

    selected: List[str] = Field(
        ...,
        description="Набор ID вариантов ответа, для которых применяется данное правило.",
    )
    score: int = Field(
        ...,
        description="Баллы, которые начисляются при таком наборе выбранных вариантов.",
    )


class ShortAnswerAccepted(BaseModel):
    """
    Допустимый вариант короткого ответа (SA/SA_COM).
    """

    value: str = Field(
        ...,
        description="Строковое представление корректного ответа (например '4' или 'четыре').",
    )
    score: int = Field(
        ...,
        description="Баллы за этот вариант ответа (может быть меньше максимума для частичных совпадений).",
    )


class ShortAnswerRules(BaseModel):
    """
    Правила проверки короткого ответа (SA/SA_COM).
    """

    normalization: List[str] = Field(
        default_factory=lambda: ["trim", "lower"],
        description=(
            "Список шагов нормализации строки перед сравнением "
            "(например: trim, lower, collapse_spaces)."
        ),
    )
    accepted_answers: List[ShortAnswerAccepted] = Field(
        default_factory=list,
        description="Список допустимых ответов и баллов за них.",
    )
    use_regex: bool = Field(
        default=False,
        description="Если true, допускается проверка по регулярному выражению.",
    )
    regex: Optional[str] = Field(
        default=None,
        description="Регулярное выражение для проверки ответа (если use_regex = true).",
    )


class TextRubricItem(BaseModel):
    """
    Критерий оценивания развёрнутого ответа (TA).
    """

    id: str = Field(
        ...,
        description="Устойчивый ID критерия (например 'content', 'style').",
    )
    title: str = Field(
        ...,
        description="Человекочитаемое название критерия.",
    )
    max_score: int = Field(
        ...,
        description="Максимальный балл по данному критерию.",
    )


class TextAnswerRules(BaseModel):
    """
    Настройки проверки развёрнутых ответов (TA).
    """

    auto_check: bool = Field(
        default=False,
        description="Флаг возможности автопроверки. Обычно false, оценка ручная.",
    )
    rubric: List[TextRubricItem] = Field(
        default_factory=list,
        description="Набор критериев оценивания для ручной или комбинированной проверки.",
    )


class PenaltiesRules(BaseModel):
    """
    Правила штрафов за различные типы ошибок.
    """

    wrong_answer: int = Field(
        default=0,
        description="Штраф за заведомо неверный ответ.",
    )
    missing_answer: int = Field(
        default=0,
        description="Штраф за отсутствие ответа.",
    )
    extra_wrong_mc: int = Field(
        default=0,
        description="Штраф за лишние неверные варианты при множественном выборе.",
    )


class SolutionRules(BaseModel):
    """
    Структура JSON-поля tasks.solution_rules.

    Описывает, как задача проверяется и как начисляются баллы.
    """

    max_score: int = Field(
        ...,
        description="Полный балл за задачу (должен совпадать с tasks.max_score).",
    )
    scoring_mode: ScoringMode = Field(
        default="all_or_nothing",
        description="Режим оценивания: all_or_nothing | partial | custom.",
    )
    auto_check: bool = Field(
        default=True,
        description="Можно ли выполнить полную проверку автоматически.",
    )
    manual_review_required: bool = Field(
        default=False,
        description="Требуется ли обязательная ручная дооценка (даже при автопроверке).",
    )

    # Для задач с выбором (SC/MC)
    correct_options: List[str] = Field(
        default_factory=list,
        description="Список ID правильных вариантов ответа для задач с выбором.",
    )
    partial_rules: List[PartialRule] = Field(
        default_factory=list,
        description="Правила частичного оценивания для сложных случаев (обычно MC).",
    )

    # Для короткого ответа
    short_answer: Optional[ShortAnswerRules] = Field(
        default=None,
        description="Правила проверки короткого ответа (SA/SA_COM).",
    )

    # Для развёрнутого ответа
    text_answer: Optional[TextAnswerRules] = Field(
        default=None,
        description="Настройки проверки развёрнутых ответов (TA).",
    )

    penalties: PenaltiesRules = Field(
        default_factory=PenaltiesRules,
        description="Настройки штрафов за неверные/отсутствующие ответы.",
    )
