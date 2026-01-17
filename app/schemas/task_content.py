from __future__ import annotations

from typing import List, Optional, Literal

from pydantic import BaseModel, Field, model_validator


TaskType = Literal["SC", "MC", "SA", "SA_COM", "TA"]


class TaskMedia(BaseModel):
    """
    Мультимедийные ресурсы, связанные с заданием.
    """

    image_url: Optional[str] = Field(
        default=None,
        description="URL изображения для задания (если есть).",
    )
    audio_url: Optional[str] = Field(
        default=None,
        description="URL аудио для задания (если есть).",
    )
    video_url: Optional[str] = Field(
        default=None,
        description="URL видео для задания (если есть).",
    )


class TaskOption(BaseModel):
    """
    Вариант ответа для задач с выбором (SC/MC/SA_COM).
    """

    id: str = Field(
        ...,
        description="Устойчивый ID варианта ответа (используется в правилах проверки и ответах ученика). Обычно A, B, C, D...",
        examples=["A", "B", "C", "opt1", "opt2"],
    )
    text: str = Field(
        ...,
        description="Текст варианта ответа.",
        examples=[
            "Именованная область памяти для хранения данных",
            "Функция для вывода данных",
            "Тип данных",
        ],
    )
    explanation: Optional[str] = Field(
        default=None,
        description="Пояснение методиста к варианту (опционально, для обратной связи). Показывается ученику после ответа.",
        examples=[
            "Правильно! Переменная действительно хранит данные в памяти.",
            "Неверно. Функция print() используется для вывода, а не переменная.",
            None,
        ],
    )
    is_active: bool = Field(
        default=True,
        description="Флаг активности варианта. Можно использовать для временного скрытия варианта.",
        examples=[True, False],
    )


class TaskContent(BaseModel):
    """
    Структура JSON-поля tasks.task_content.

    Описывает то, что видит ученик: формулировка, подсказки, варианты, теги и т.п.
    """

    type: TaskType = Field(
        ...,
        description="Тип задачи: SC | MC | SA | SA_COM | TA.",
        examples=["SC", "MC", "SA", "SA_COM", "TA"],
    )
    code: Optional[str] = Field(
        default=None,
        description="Внутренний код задачи (опционально, может дублировать external_uid).",
        examples=["PY-VAR-001", "TASK-001", None],
    )
    title: Optional[str] = Field(
        default=None,
        description="Краткое название задания (для списков, навигации).",
        examples=["Переменные Python", "Основы программирования", None],
    )
    stem: str = Field(
        ...,
        description="Основная формулировка вопроса/задачи.",
        examples=[
            "Что такое переменная в Python?",
            "Какой оператор используется для целочисленного деления?",
            "Объясните разницу между методами append() и extend() для списков.",
        ],
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Дополнительное пояснение или подсказка для ученика.",
        examples=[
            "Переменная хранит значение, которое можно изменять",
            "Оператор // возвращает целую часть от деления",
            None,
        ],
    )

    media: Optional[TaskMedia] = Field(
        default=None,
        description="Мультимедийные материалы для задания (изображение, аудио, видео).",
    )

    options: Optional[List[TaskOption]] = Field(
        default=None,
        description="Варианты ответа для задач с выбором (SC/MC/SA_COM). Обязательно для SC/MC (минимум 2 варианта).",
        examples=[
            [
                {"id": "A", "text": "Именованная область памяти для хранения данных", "is_active": True},
                {"id": "B", "text": "Функция для вывода данных", "is_active": True},
                {"id": "C", "text": "Тип данных", "is_active": True},
            ],
            [
                {"id": "A", "text": "list()", "is_active": True},
                {"id": "B", "text": "[]", "is_active": True},
                {"id": "C", "text": "[1, 2, 3]", "is_active": True},
                {"id": "D", "text": "list(range(3))", "is_active": True},
            ],
            None,
        ],
    )

    tags: Optional[List[str]] = Field(
        default=None,
        description="Список тегов (темы, EGE-коды, произвольные метки).",
    )
    difficulty_code: Optional[str] = Field(
        default=None,
        description="Код сложности (дублирует привязку к difficulties.code, например 'Easy', 'Normal').",
    )
    course_uid: Optional[str] = Field(
        default=None,
        description="Внешний код курса (courses.course_uid), используется для импорта.",
    )

    @model_validator(mode="after")
    def validate_by_type(self) -> "TaskContent":
        """
        Базовая валидация структуры в зависимости от типа задачи.

        - SC/MC: должны быть как минимум 2 варианта;
        - SA/TA: options не обязательны.
        - Проверка уникальности options[].id.
        """
        if self.type in ("SC", "MC"):
            if not self.options or len(self.options) < 2:
                raise ValueError(
                    "Для задач типов SC/MC необходимо указать минимум два варианта ответа в поле 'options'."
                )
        
        # Валидация уникальности options[].id
        if self.options:
            option_ids = [opt.id for opt in self.options]
            if len(option_ids) != len(set(option_ids)):
                duplicates = [opt_id for opt_id in option_ids if option_ids.count(opt_id) > 1]
                raise ValueError(
                    f"ID вариантов ответа должны быть уникальными. "
                    f"Найдены дубликаты: {', '.join(set(duplicates))}"
                )
        
        return self
