from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class DifficultyLevelCreate(BaseModel):
    """
    Схема создания уровня сложности.
    uid — уникальный идентификатор для импорта (маппинг при импорте заданий).
    """
    uid: str = Field(
        ...,
        description="Уникальный идентификатор (например theory, easy, normal, hard, project)",
    )
    code: str = Field(
        ...,
        description="Код уровня сложности (например, 'Theory', 'Easy', 'Normal', 'Hard', 'Project')",
    )
    name_ru: str = Field(
        ...,
        description="Русское имя уровня сложности (например, 'Теория', 'Легко', 'Нормально', ...')",
    )
    weight: int = Field(
        ...,
        description="Вес уровня сложности (1..5)",
    )


class DifficultyLevelUpdate(BaseModel):
    """
    Схема частичного обновления уровня сложности.
    Все поля опциональны.
    """
    uid: Optional[str] = Field(
        None,
        description="Уникальный идентификатор для импорта",
    )
    code: Optional[str] = Field(
        None,
        description="Код уровня сложности",
    )
    name_ru: Optional[str] = Field(
        None,
        description="Русское имя уровня сложности",
    )
    weight: Optional[int] = Field(
        None,
        description="Вес уровня сложности (1..5)",
    )


class DifficultyLevelRead(BaseModel):
    """
    Схема чтения уровня сложности.
    uid — для маппинга при импорте заданий (колонка difficulty_uid в таблице).
    """
    id: int
    uid: str
    code: str
    name_ru: str
    weight: int

    model_config = ConfigDict(from_attributes=True)
