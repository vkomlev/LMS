"""tsk-314/tsk-553: формат конфига выборки заданий по сложности на подкурс
и контракт эндпоинта кабинета методиста, читающего/пишущего это поле."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CourseSamplingConfig(BaseModel):
    """Формат `courses.sampling_config` (JSONB).

    `threshold` — порог И размер итоговой выборки при превышении: если
    EASY+NORMAL заданий подкурса больше `threshold`, студенту выдаётся ровно
    `threshold` заданий (случайная стабильная часть), иначе — все.
    `easy_ratio` — доля EASY в итоговой выборке (0..1), NORMAL — остаток.

    THEORY и любая сложность вне EASY/NORMAL (HARD/PROJECT) выборке не
    подлежат — выдаются всегда целиком, эта настройка их не касается.
    """

    enabled: bool = Field(False, description="Включена ли выборка на этом подкурсе")
    threshold: int = Field(
        ...,
        ge=1,
        description=(
            "Порог EASY+NORMAL заданий подкурса; выше него выдаётся "
            "случайная часть размером threshold"
        ),
    )
    easy_ratio: float = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Доля EASY в итоговой выборке (0.5 = поровну EASY/NORMAL)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"enabled": True, "threshold": 40, "easy_ratio": 0.5},
                {"enabled": False, "threshold": 40, "easy_ratio": 0.5},
            ]
        }
    )


class CourseSamplingSettingsRequest(BaseModel):
    """Тело `PATCH /courses/{id}/sampling` (tsk-553).

    `sampling_config = null` сбрасывает поле (прежнее поведение движка —
    все задания, без выборки); иначе — полная замена конфига (не partial-
    merge: методист правит все три поля разом через одну форму настроек,
    частичное обновление тут только добавило бы риск забытого старого
    `threshold` при переключении `enabled`).
    """

    sampling_config: Optional[CourseSamplingConfig] = Field(
        None,
        description="Новый конфиг целиком, либо null — сбросить (прежнее поведение)",
    )


class CourseSamplingSettingsResponse(BaseModel):
    """Ответ `GET`/`PATCH /courses/{id}/sampling` (tsk-553)."""

    sampling_config: Optional[CourseSamplingConfig] = Field(
        None, description="Текущий конфиг; null — выборка никогда не настраивалась"
    )
    easy_normal_count: int = Field(
        ...,
        ge=0,
        description=(
            "Сколько сейчас в подкурсе активных EASY+NORMAL заданий — "
            "подсказка методисту при выборе порога (сам порог система не "
            "предлагает и не хардкодит)"
        ),
    )
