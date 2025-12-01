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
        description="Устойчивый ID варианта ответа (используется в правилах проверки и ответах ученика).",
    )
    text: str = Field(
        ...,
        description="Текст варианта ответа.",
    )
    explanation: Optional[str] = Field(
        default=None,
        description="Пояснение методиста к варианту (опционально, для обратной связи).",
    )
    is_active: bool = Field(
        default=True,
        description="Флаг активности варианта. Можно использовать для временного скрытия варианта.",
    )


class TaskContent(BaseModel):
    """
    Структура JSON-поля tasks.task_content.

    Описывает то, что видит ученик: формулировка, подсказки, варианты, теги и т.п.
    """

    type: TaskType = Field(
        ...,
        description="Тип задачи: SC | MC | SA | SA_COM | TA.",
    )
    code: Optional[str] = Field(
        default=None,
        description="Внутренний код задачи (опционально, может дублировать external_uid).",
    )
    title: Optional[str] = Field(
        default=None,
        description="Краткое название задания (для списков, навигации).",
    )
    stem: str = Field(
        ...,
        description="Основная формулировка вопроса/задачи.",
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Дополнительное пояснение или подсказка для ученика.",
    )

    media: Optional[TaskMedia] = Field(
        default=None,
        description="Мультимедийные материалы для задания (изображение, аудио, видео).",
    )

    options: Optional[List[TaskOption]] = Field(
        default=None,
        description="Варианты ответа для задач с выбором (SC/MC/SA_COM).",
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
        """
        if self.type in ("SC", "MC"):
            if not self.options or len(self.options) < 2:
                raise ValueError(
                    "Для задач типов SC/MC необходимо указать минимум два варианта ответа в поле 'options'."
                )
        return self
