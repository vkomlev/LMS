# app/schemas/materials.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.material_content import (
    MaterialType,
    validate_material_content,
)


# ---------- CRUD схемы ----------


class MaterialCreate(BaseModel):
    """Создание материала. Контент валидируется по полю type."""
    course_id: int = Field(..., description="ID курса")
    title: str = Field(..., max_length=500, description="Заголовок материала")
    type: str = Field(
        ...,
        description="Тип материала: text, video, audio, image, link, pdf, office_document",
    )
    content: Any = Field(..., description="Содержимое (структура зависит от type)")
    description: Optional[str] = None
    caption: Optional[str] = None
    order_position: Optional[int] = Field(
        default=None,
        description="Позиция в курсе (NULL = в конец, триггер БД установит автоматически)",
    )
    is_active: bool = True
    external_uid: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_content_by_type(self) -> "MaterialCreate":
        if self.content is not None:
            try:
                validated = validate_material_content(self.type, self.content)
                object.__setattr__(self, "content", validated)
            except Exception as e:
                raise ValueError(f"Некорректная структура content для типа '{self.type}': {e}")
        return self


class MaterialUpdate(BaseModel):
    """Обновление материала (частичное). При передаче type и content — контент валидируется."""
    course_id: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = None
    caption: Optional[str] = None
    type: Optional[str] = None
    content: Optional[Any] = None
    order_position: Optional[int] = None
    is_active: Optional[bool] = None
    external_uid: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_content_by_type(self) -> "MaterialUpdate":
        if self.type is not None and self.content is not None:
            try:
                validated = validate_material_content(self.type, self.content)
                object.__setattr__(self, "content", validated)
            except Exception as e:
                raise ValueError(f"Некорректная структура content для типа '{self.type}': {e}")
        return self


class MaterialRead(BaseModel):
    """Чтение материала (ответ API)."""
    id: int
    course_id: int
    title: str
    description: Optional[str] = None
    caption: Optional[str] = None
    type: str
    content: Any
    order_position: Optional[int] = None
    is_active: bool
    external_uid: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- Операции: reorder, move, bulk-update, copy ----------


class MaterialOrderItem(BaseModel):
    """Элемент списка порядка материалов при reorder."""
    material_id: int = Field(..., description="ID материала")
    order_position: int = Field(..., ge=1, description="Новая позиция в курсе")


class MaterialReorderRequest(BaseModel):
    """Запрос на изменение порядка материалов курса."""
    material_orders: List[MaterialOrderItem] = Field(
        ...,
        description="Список пар (material_id, order_position) для установки нового порядка",
    )


class MaterialMoveRequest(BaseModel):
    """Запрос на перемещение материала (в другую позицию или в другой курс)."""
    new_order_position: Optional[int] = Field(
        default=None,
        ge=1,
        description="Новая позиция в курсе. При переносе в другой курс можно не передавать — материал встанет в конец. В рамках того же курса обязателен.",
    )
    course_id: Optional[int] = Field(
        default=None,
        description="ID курса назначения. Если не указан — перемещение внутри текущего курса (только смена позиции).",
    )


class MaterialBulkUpdateRequest(BaseModel):
    """Запрос на массовое обновление активности материалов."""
    material_ids: List[int] = Field(..., description="ID материалов для обновления")
    is_active: bool = Field(..., description="Новое значение is_active")


class MaterialCopyRequest(BaseModel):
    """Запрос на копирование материала в другой курс."""
    target_course_id: int = Field(..., description="ID курса, в который копировать")
    order_position: Optional[int] = Field(
        default=None,
        description="Позиция в целевом курсе (NULL = в конец)",
    )


class MaterialsListResponse(BaseModel):
    """Ответ: список материалов курса с пагинацией."""
    items: List[MaterialRead] = Field(default_factory=list)
    total: int = Field(..., ge=0)
    skip: int = Field(..., ge=0)
    limit: int = Field(..., ge=0)


class MaterialOrderRead(BaseModel):
    """Элемент ответа reorder: id материала и его новая позиция."""
    id: int
    order_position: int


class MaterialReorderResponse(BaseModel):
    """Ответ на изменение порядка материалов."""
    updated: int = Field(..., ge=0)
    materials: List[MaterialOrderRead] = Field(default_factory=list)


class MaterialBulkUpdateResponse(BaseModel):
    """Ответ на массовое обновление активности."""
    updated: int = Field(..., ge=0)


# ---------- Импорт из Google Sheets ----------


class MaterialsGoogleSheetsImportRequest(BaseModel):
    """
    Запрос на импорт материалов из Google Sheets.
    Курс для каждой строки задаётся полем course_uid в таблице (многокурсовой импорт).
    """
    spreadsheet_url: str = Field(
        ...,
        description="URL таблицы Google Sheets или spreadsheet_id",
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description="Название листа. По умолчанию 'Materials'.",
    )
    column_mapping: Optional[Dict[str, str]] = Field(
        default=None,
        description="Маппинг колонок таблицы на поля: course_uid, external_uid, title, type, url, ...",
    )
    dry_run: bool = Field(
        default=False,
        description="Режим проверки без сохранения в БД",
    )


class MaterialsGoogleSheetsImportError(BaseModel):
    """Ошибка импорта одной строки (глобальный список или внутри by_course)."""
    row: int = Field(..., description="Номер строки в таблице (начиная с 1)")
    error: str = Field(..., description="Текст ошибки")
    course_uid: Optional[str] = Field(default=None, description="course_uid строки, если известен")
    external_uid: Optional[str] = Field(default=None, description="external_uid материала, если известен")


class MaterialsImportByCourseItem(BaseModel):
    """Сводка импорта по одному курсу (элемент by_course)."""
    course_uid: str = Field(..., description="Код курса")
    course_id: int = Field(..., description="ID курса в БД")
    imported: int = Field(..., ge=0, description="Количество созданных материалов")
    updated: int = Field(..., ge=0, description="Количество обновлённых материалов")
    errors: List[MaterialsGoogleSheetsImportError] = Field(
        default_factory=list,
        description="Ошибки по строкам этого курса",
    )


class MaterialsGoogleSheetsImportResponse(BaseModel):
    """Ответ на импорт материалов из Google Sheets."""
    imported: int = Field(..., ge=0, description="Всего создано материалов")
    updated: int = Field(..., ge=0, description="Всего обновлено материалов")
    total_rows: int = Field(..., ge=0, description="Всего строк данных в таблице")
    by_course: List[MaterialsImportByCourseItem] = Field(
        default_factory=list,
        description="Разбивка по курсам: курс, счётчики, ошибки по строкам курса",
    )
    errors: List[MaterialsGoogleSheetsImportError] = Field(
        default_factory=list,
        description="Глобальные ошибки (курс не найден, ошибка разбора и т.п.)",
    )
