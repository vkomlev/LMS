# app/schemas/material_content.py
"""
Pydantic-схемы для JSONB-поля materials.content.
Структура зависит от типа материала (materials.type).
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, Field


MaterialType = Literal[
    "text", "video", "audio", "image", "link", "pdf", "office_document"
]

# ---------- Общие типы источников ----------


class VideoSourceItem(BaseModel):
    """Один источник видео (файл, URL, Telegram, YouTube, Vimeo)."""
    type: Optional[Literal["file", "url", "telegram", "youtube", "vimeo"]] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    telegram_file_id: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    quality: Optional[Literal["1080p", "720p", "480p"]] = None


class AudioSourceItem(BaseModel):
    """Один источник аудио."""
    type: Optional[Literal["file", "url", "telegram"]] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    telegram_file_id: Optional[str] = None
    duration_seconds: Optional[int] = None


class ImageSourceItem(BaseModel):
    """Один источник изображения."""
    type: Optional[Literal["file", "url", "telegram"]] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    telegram_file_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    alt_text: Optional[str] = None


class PdfSourceItem(BaseModel):
    """Один источник PDF."""
    type: Optional[Literal["file", "url", "telegram"]] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    telegram_file_id: Optional[str] = None
    pages_count: Optional[int] = None
    file_size_bytes: Optional[int] = None


class OfficeDocumentSourceItem(BaseModel):
    """Один источник офисного документа."""
    type: Optional[Literal["file", "url", "telegram"]] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    telegram_file_id: Optional[str] = None
    format: Optional[Literal["docx", "xlsx", "pptx", "odt", "ods", "odp"]] = None
    file_size_bytes: Optional[int] = None


# ---------- Контент по типам материала ----------


class TextContent(BaseModel):
    """Контент для type='text'."""
    text: str = Field(..., description="Основной текст материала")
    format: Optional[Literal["markdown", "html", "plain"]] = Field(
        default="plain",
        description="Формат текста",
    )


class VideoContent(BaseModel):
    """Контент для type='video'."""
    sources: List[VideoSourceItem] = Field(
        default_factory=list,
        description="Список источников (файл, URL, Telegram, YouTube, Vimeo)",
    )
    default_source: int = Field(
        default=0,
        description="Индекс источника по умолчанию",
    )


class AudioContent(BaseModel):
    """Контент для type='audio'."""
    sources: List[AudioSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


class ImageContent(BaseModel):
    """Контент для type='image'."""
    sources: List[ImageSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


class LinkContent(BaseModel):
    """Контент для type='link'."""
    url: str = Field(..., description="URL ссылки")
    title: Optional[str] = None
    description: Optional[str] = None
    preview_image: Optional[str] = None


class PdfContent(BaseModel):
    """Контент для type='pdf'."""
    sources: List[PdfSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


class OfficeDocumentContent(BaseModel):
    """Контент для type='office_document'."""
    sources: List[OfficeDocumentSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


# ---------- Валидация контента по типу материала ----------

CONTENT_MODELS: dict[str, type[BaseModel]] = {
    "text": TextContent,
    "video": VideoContent,
    "audio": AudioContent,
    "image": ImageContent,
    "link": LinkContent,
    "pdf": PdfContent,
    "office_document": OfficeDocumentContent,
}


def validate_material_content(material_type: str, content: Any) -> dict[str, Any]:
    """
    Валидирует content по типу материала.
    Возвращает валидированный dict для записи в JSONB.
    Выбрасывает ValueError при несоответствии структуры.
    """
    if material_type not in CONTENT_MODELS:
        raise ValueError(f"Неизвестный тип материала: {material_type}")
    model = CONTENT_MODELS[material_type]
    if not isinstance(content, dict):
        raise ValueError(f"content должен быть объектом (dict), получено: {type(content)}")
    obj = model.model_validate(content)
    return obj.model_dump()


def get_content_model_for_type(material_type: str) -> type[BaseModel] | None:
    """Возвращает Pydantic-модель для типа материала или None."""
    return CONTENT_MODELS.get(material_type)
