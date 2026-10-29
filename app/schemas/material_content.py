# app/schemas/material_content.py
"""
Pydantic-схемы для JSONB-поля materials.content.
Структура зависит от типа материала (materials.type).
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.trainer_scenario import SLUG_PATTERN, validate_scenario


MaterialType = Literal[
    "text", "video", "audio", "image", "link", "pdf", "office_document",
    "script", "document", "trainer",
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


class TrainerCheckpoint(BaseModel):
    """Развилка после теории «Всё понятно?» (tsk-1247).

    `materials.content.checkpoint` у материала-теории. «Да» — ученик идёт к
    элементу узла после тренажёра, «Нет» — открывается тренажёр
    `trainer_material_id`. Не путать с `checkpoints` (tsk-998, вопросы внутри
    текста): это разные механизмы с разными рубильниками.
    """
    model_config = ConfigDict(extra="forbid")

    question: str = Field(default="Всё понятно?", min_length=1, max_length=500)
    trainer_material_id: int = Field(..., gt=0, description="id материала типа trainer в этом узле")


class _WithTrainerCheckpoint(BaseModel):
    """Необязательная развилка tsk-1247 у любого материала-теории."""
    checkpoint: Optional[TrainerCheckpoint] = Field(
        default=None,
        description="tsk-1247: развилка «Всё понятно?» → тренажёр (при TRAINERS_ENABLED)",
    )


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


class TextContent(_WithTrainerCheckpoint):
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


class VideoContent(_WithTrainerCheckpoint):
    """Контент для type='video'."""
    sources: List[VideoSourceItem] = Field(
        default_factory=list,
        description="Список источников (файл, URL, Telegram, YouTube, Vimeo)",
    )
    default_source: int = Field(
        default=0,
        description="Индекс источника по умолчанию",
    )


class AudioContent(_WithTrainerCheckpoint):
    """Контент для type='audio'."""
    sources: List[AudioSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


class ImageContent(_WithTrainerCheckpoint):
    """Контент для type='image'."""
    sources: List[ImageSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


class LinkContent(_WithTrainerCheckpoint):
    """Контент для type='link'."""
    url: str = Field(..., description="URL ссылки")
    title: Optional[str] = None
    description: Optional[str] = None
    preview_image: Optional[str] = None


class PdfContent(_WithTrainerCheckpoint):
    """Контент для type='pdf'."""
    sources: List[PdfSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


class OfficeDocumentContent(_WithTrainerCheckpoint):
    """Контент для type='office_document'."""
    sources: List[OfficeDocumentSourceItem] = Field(default_factory=list)
    default_source: int = Field(default=0)


class ScriptContent(_WithTrainerCheckpoint):
    """Контент для type='script' — программы и скрипты (py, js, ps1 и т.д.)."""
    sources: List[ScriptSourceItem] = Field(
        default_factory=list,
        description="Список источников (файл, URL)",
    )
    default_source: int = Field(default=0)


class DocumentContent(_WithTrainerCheckpoint):
    """Контент для type='document' — документы (docx, xlsx, pptx, txt)."""
    sources: List[DocumentSourceItem] = Field(
        default_factory=list,
        description="Список источников (файл, URL)",
    )
    default_source: int = Field(default=0)


class TrainerContent(BaseModel):
    """Контент для type='trainer' (tsk-1247): `{slug, scenario}`.

    `scenario` — сценарий `trainer-scenario/1`, проверяется схемой целиком
    (`app/schemas/trainer_scenario.py`); `slug` обязан совпадать со
    `scenario.slug` — по нему тренажёр отдаёт `GET /trainers/{slug}`.
    """
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., pattern=SLUG_PATTERN, description="Адрес тренажёра (/t/{slug})")
    scenario: dict[str, Any] = Field(..., description="Сценарий trainer-scenario/1")

    @model_validator(mode="after")
    def _scenario_valid(self) -> "TrainerContent":
        parsed = validate_scenario(self.scenario)
        if parsed.slug != self.slug:
            raise ValueError(
                f"slug материала «{self.slug}» не совпадает со scenario.slug «{parsed.slug}»"
            )
        return self


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
    "trainer": TrainerContent,
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
    if material_type == "trainer":
        # tsk-1247: сценарий хранится как прислан (порядок ключей и значения
        # по умолчанию не дописываются) — движок SPW читает его по README §1.
        return {"slug": content["slug"], "scenario": content["scenario"]}
    dumped = obj.model_dump()
    # tsk-1247: пустая развилка не записывается — content без неё байт в байт
    # прежний (bulk-upsert не считает такие материалы изменёнными).
    if dumped.get("checkpoint") is None:
        dumped.pop("checkpoint", None)
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
