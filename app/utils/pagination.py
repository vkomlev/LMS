# app/utils/pagination.py
from __future__ import annotations

from typing import Generic, List, Sequence, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PageMeta(BaseModel):
    """Мета-информация о странице результатов."""
    model_config = ConfigDict(
        populate_by_name=True,
        frozen=True,
        extra="forbid",
    )

    total: int = Field(..., ge=0, description="Общее количество записей по запросу")
    limit: int = Field(..., ge=0, description="Сколько записей запрошено на страницу")
    offset: int = Field(..., ge=0, description="Смещение, с которого начинаются записи")


class Page(BaseModel, Generic[T]):
    """Пакет результатов с метаданными пагинации."""
    model_config = ConfigDict(
        from_attributes=True,  # корректно собирать из ORM-моделей
        extra="forbid",
    )

    items: List[T]
    meta: PageMeta


def build_page(items: Sequence[T], total: int, limit: int, offset: int) -> Page[T]:
    """
    Сконструировать объект Page[T].

    Args:
        items: Список элементов текущей страницы (уже после limit/offset).
        total: Общее количество элементов без учёта limit/offset.
        limit: Лимит на страницу.
        offset: Смещение элементов.

    Returns:
        Page[T]: Структура ответа с данными и метой.
    """
    return Page[T](items=list(items), meta=PageMeta(total=total, limit=limit, offset=offset))
