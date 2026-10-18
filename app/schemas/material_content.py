# app/schemas/material_content.py
"""
Pydantic-схемы для JSONB-поля materials.content.
Структура зависит от типа материала (materials.type).
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


MaterialType = Literal[
    "text", "video", "audio", "image", "link", "pdf", "office_document",
    "script", "document",
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


class ScriptSourceItem(BaseModel):
    """Один источник: программа или скрипт (py, js, ps1 и т.д.)."""
    type: Optional[Literal["file", "url", "telegram"]] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    telegram_file_id: Optional[str] = None
    format: Optional[Literal["py", "js", "ts", "ps1", "sh", "bat", "cmd", "sql", "yaml", "json"]] = None
    file_size_bytes: Optional[int] = None


class DocumentSourceItem(BaseModel):
    """Один источник документа (docx, xlsx, pptx, txt и т.д.)."""
    type: Optional[Literal["file", "url", "telegram"]] = None
    url: Optional[str] = None
    file_path: Optional[str] = None
    telegram_file_id: Optional[str] = None
    format: Optional[Literal["docx", "xlsx", "pptx", "txt", "odt", "ods", "odp"]] = None
    file_size_bytes: Optional[int] = None


# ---------- Контент по типам материала ----------


class MaterialCheckpointOption(BaseModel):
    """Вариант ответа контрольной точки `kind="single"` (tsk-998)."""
    id: str = Field(..., min_length=1, max_length=16, description="Стабильный id варианта (a, b, c…)")
    text: str = Field(..., max_length=2000, description="Текст варианта (HTML)")


#: Ключ контрольной точки: тот же алфавит, что у публикатора CB (`checkpoint_id`).
CHECKPOINT_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class MaterialCheckpoint(BaseModel):
    """Контрольная точка внутри текстового материала (tsk-998, П9).

    Пишет публикатор ContentBackbone. `answer`/`explanation` хранятся в
    материале, но ученику не отдаются (`material_checkpoints_service.student_content`).
    Место в HTML — пустая метка `<div data-cb-checkpoint="{id}"></div>`;
    `after_block` — сколько блоков теории перед точкой (справочно).
    """
    id: str = Field(..., pattern=CHECKPOINT_ID_PATTERN, description="Стабильный id точки")
    after_block: int = Field(default=0, ge=0, description="Сколько блоков теории перед точкой")
    question: str = Field(..., min_length=1, max_length=20000, description="Вопрос (HTML)")
    kind: Literal["single", "short"] = Field(..., description="single — выбор одного; short — короткий ответ")
    options: Optional[List[MaterialCheckpointOption]] = Field(
        default=None, max_length=16, description="Варианты (только для single)",
    )
    answer: Union[str, List[str]] = Field(
        ...,
        description="single — id верного варианта; short — список допустимых ответов",
    )
    explanation: Optional[str] = Field(default=None, max_length=5000, description="Пояснение после ответа")

    @field_validator("answer")
    @classmethod
    def _answer_not_blank(cls, v: Union[str, List[str]]) -> Union[str, List[str]]:
        values = [v] if isinstance(v, str) else v
        if not values or any(not str(x).strip() or len(str(x)) > 200 for x in values):
            raise ValueError("answer: нужен непустой ответ (каждый не длиннее 200 символов)")
        return v

    @model_validator(mode="after")
    def _kind_shape(self) -> "MaterialCheckpoint":
        if self.kind == "single":
            if not self.options or len(self.options) < 2:
                raise ValueError(f"точка {self.id}: для single нужно не меньше двух вариантов")
            ids = [o.id for o in self.options]
            if len(set(ids)) != len(ids):
                raise ValueError(f"точка {self.id}: id вариантов повторяются")
            if not isinstance(self.answer, str) or self.answer not in ids:
                raise ValueError(f"точка {self.id}: answer должен быть id одного из вариантов")
        else:
            if self.options:
                raise ValueError(f"точка {self.id}: у short вариантов быть не должно")
            if isinstance(self.answer, str):
                self.answer = [self.answer]
        return self


class TextContent(BaseModel):
    """Контент для type='text'."""
    text: str = Field(..., description="Основной текст материала")
    format: Optional[Literal["markdown", "html", "plain"]] = Field(
        default="plain",
        description="Формат текста",
    )
    # tsk-998: контрольные точки «проверь себя» (необязательно). Без них ключа
    # в content нет вовсе — материалы без П9 хранятся как раньше.
    checkpoints: Optional[List[MaterialCheckpoint]] = Field(
        default=None,
        max_length=20,
        description="Контрольные точки внутри материала (tsk-998); ученику — без answer/explanation",
    )

    @field_validator("checkpoints")
    @classmethod
    def _unique_checkpoint_ids(
        cls, v: Optional[List[MaterialCheckpoint]]
    ) -> Optional[List[MaterialCheckpoint]]:
        if v:
            ids = [cp.id for cp in v]
            if len(set(ids)) != len(ids):
                raise ValueError("id контрольных точек в материале повторяются")
        return v


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


class ScriptContent(BaseModel):
    """Контент для type='script' — программы и скрипты (py, js, ps1 и т.д.)."""
    sources: List[ScriptSourceItem] = Field(
        default_factory=list,
        description="Список источников (файл, URL)",
    )
    default_source: int = Field(default=0)


class DocumentContent(BaseModel):
    """Контент для type='document' — документы (docx, xlsx, pptx, txt)."""
    sources: List[DocumentSourceItem] = Field(
        default_factory=list,
        description="Список источников (файл, URL)",
    )
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
    "script": ScriptContent,
    "document": DocumentContent,
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
    dumped = obj.model_dump()
    # tsk-998: пустой `checkpoints` не записывается — content материалов без
    # контрольных точек остаётся байт в байт прежним (и bulk-upsert не считает
    # их изменёнными).
    if material_type == "text" and dumped.get("checkpoints") is None:
        dumped.pop("checkpoints", None)
    if material_type == "text" and dumped.get("checkpoints"):
        for cp in dumped["checkpoints"]:
            for key in ("options", "explanation"):
                if cp.get(key) is None:
                    cp.pop(key, None)
    return dumped


def get_content_model_for_type(material_type: str) -> type[BaseModel] | None:
    """Возвращает Pydantic-модель для типа материала или None."""
    return CONTENT_MODELS.get(material_type)
