from __future__ import annotations

from typing import List, Optional, Literal

from pydantic import BaseModel, Field, model_validator


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
        examples=["8", "28", "len", "len()", "двадцать восемь"],
    )
    score: int = Field(
        ...,
        description="Баллы за этот вариант ответа (может быть меньше максимума для частичных совпадений).",
        examples=[5, 10, 15],
        ge=0,
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
        examples=[["trim", "lower"], ["trim", "lower", "collapse_spaces"]],
    )
    accepted_answers: List[ShortAnswerAccepted] = Field(
        default_factory=list,
        description="Список допустимых ответов и баллов за них.",
        examples=[
            [{"value": "8", "score": 10}, {"value": "28", "score": 10}],
            [{"value": "len", "score": 5}, {"value": "len()", "score": 5}],
            [],
        ],
    )
    use_regex: bool = Field(
        default=False,
        description="Если true, допускается проверка по регулярному выражению.",
        examples=[False, True],
    )
    regex: Optional[str] = Field(
        default=None,
        description="Регулярное выражение для проверки ответа (если use_regex = true).",
        examples=[None, r"^\d+$", r"^[A-Z][a-z]+$"],
    )


class TextRubricItem(BaseModel):
    """
    Критерий оценивания развёрнутого ответа (TA).
    """

    id: str = Field(
        ...,
        description="Устойчивый ID критерия (например 'content', 'style').",
        examples=["content", "style", "grammar", "logic"],
    )
    title: str = Field(
        ...,
        description="Человекочитаемое название критерия.",
        examples=["Содержание", "Стиль изложения", "Грамматика", "Логика рассуждений"],
    )
    max_score: int = Field(
        ...,
        description="Максимальный балл по данному критерию.",
        examples=[5, 10, 15],
        gt=0,
    )


class TextAnswerRules(BaseModel):
    """
    Настройки проверки развёрнутых ответов (TA).
    """

    auto_check: bool = Field(
        default=False,
        description="Флаг возможности автопроверки. Обычно false, оценка ручная.",
        examples=[False, True],
    )
    rubric: List[TextRubricItem] = Field(
        default_factory=list,
        description="Набор критериев оценивания для ручной или комбинированной проверки.",
        examples=[
            [
                {"id": "content", "title": "Содержание", "max_score": 10},
                {"id": "style", "title": "Стиль изложения", "max_score": 5},
            ],
            [],
        ],
    )


class PenaltiesRules(BaseModel):
    """
    Правила штрафов за различные типы ошибок.
    """

    wrong_answer: int = Field(
        default=0,
        description="Штраф за заведомо неверный ответ. Вычитается из базового балла (0 для неправильного ответа).",
        examples=[0, 1, 2, 5],
        ge=0,
    )
    missing_answer: int = Field(
        default=0,
        description="Штраф за отсутствие ответа. Вычитается из базового балла (0 для отсутствия ответа).",
        examples=[0, 1, 3, 5],
        ge=0,
    )
    extra_wrong_mc: int = Field(
        default=0,
        description="Штраф за каждый лишний неверный вариант при множественном выборе (MC). Вычитается из частичного балла.",
        examples=[0, 1, 2, 4],
        ge=0,
    )


class SolutionRules(BaseModel):
    """
    Структура JSON-поля tasks.solution_rules.

    Описывает, как задача проверяется и как начисляются баллы.
    """

    max_score: int = Field(
        ...,
        description="Полный балл за задачу (должен совпадать с tasks.max_score).",
        gt=0,
        examples=[5, 10, 15, 20],
    )
    scoring_mode: ScoringMode = Field(
        default="all_or_nothing",
        description="Режим оценивания: all_or_nothing | partial | custom.",
        examples=["all_or_nothing", "partial", "custom"],
    )
    auto_check: bool = Field(
        default=True,
        description="Можно ли выполнить полную проверку автоматически.",
        examples=[True, False],
    )
    manual_review_required: bool = Field(
        default=False,
        description="Требуется ли обязательная ручная дооценка (даже при автопроверке).",
        examples=[False, True],
    )

    # Для задач с выбором (SC/MC)
    correct_options: List[str] = Field(
        default_factory=list,
        description="Список ID правильных вариантов ответа для задач с выбором. Для SC должен быть ровно один элемент.",
        examples=[["A"], ["A", "B"], ["opt1", "opt2", "opt3"], []],
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

    @model_validator(mode="after")
    def validate_max_score(self) -> "SolutionRules":
        """
        Валидация max_score: должен быть положительным числом.
        """
        if self.max_score <= 0:
            raise ValueError("max_score должен быть положительным числом")
        return self

    def validate_with_task_content(self, task_content: "TaskContent") -> None:
        """
        Валидирует соответствие correct_options и options[].id из task_content.
        
        Вызывается из сервиса при создании/обновлении задачи.
        
        Args:
            task_content: Схема содержимого задачи (TaskContent).
            
        Raises:
            ValueError: Если correct_options не соответствуют options[].id.
        """
        from app.schemas.task_content import TaskContent
        
        # Для задач с выбором (SC/MC) проверяем соответствие
        if task_content.type in ("SC", "MC"):
            if not task_content.options:
                raise ValueError(
                    f"Для задач типа {task_content.type} необходимо указать варианты ответа в task_content.options"
                )
            
            # Получаем все доступные ID вариантов
            available_option_ids = {opt.id for opt in task_content.options}
            
            # Проверяем, что все correct_options существуют в options
            invalid_options = set(self.correct_options) - available_option_ids
            if invalid_options:
                raise ValueError(
                    f"correct_options содержат несуществующие ID вариантов: {', '.join(sorted(invalid_options))}. "
                    f"Доступные ID: {', '.join(sorted(available_option_ids))}"
                )
            
            # Для SC проверяем, что выбран ровно один правильный вариант
            if task_content.type == "SC" and len(self.correct_options) != 1:
                raise ValueError(
                    f"Для задач типа SC должен быть указан ровно один правильный вариант. "
                    f"Указано: {len(self.correct_options)}"
                )
            
            # Проверяем partial_rules
            for rule in self.partial_rules:
                invalid_in_rule = set(rule.selected) - available_option_ids
                if invalid_in_rule:
                    raise ValueError(
                        f"partial_rules содержат несуществующие ID вариантов: {', '.join(sorted(invalid_in_rule))}"
                    )
