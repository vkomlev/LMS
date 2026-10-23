"""tsk-1255: черновик импорта урока из файлов автора (docx).

Черновик живёт у клиента: разбор отдаёт его, методист правит в предпросмотре,
публикация принимает обратно. Поэтому ключи здесь относительные (`uid` —
суффикс вроде `HW-03`), а полный `external_uid` собирает сервер от курса-
родителя: присланный JSON не может задеть объекты чужих курсов.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

NodeSuffix = Literal["PRE", "KON", "POST", "HW"]
_UID = r"^[A-Z0-9][A-Z0-9-]{0,39}$"


class LessonImportNode(BaseModel):
    """Узел урока (подкурс)."""

    suffix: NodeSuffix
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=5000)
    access_level: Literal["auto_check", "manual_check", "self_guided"]
    order_number: int = Field(..., ge=1, le=100)


class LessonImportMaterial(BaseModel):
    """Материал узла: видео или текст (HTML)."""

    uid: str = Field(..., pattern=_UID)
    node: NodeSuffix
    type: Literal["video", "text"]
    title: str = Field(..., min_length=1, max_length=500)
    content: Dict[str, Any]
    order_position: Optional[int] = Field(None, ge=1)


class LessonImportTask(BaseModel):
    """Задание узла в формате пакетного импорта заданий."""

    uid: str = Field(..., pattern=_UID)
    node: NodeSuffix
    difficulty_uid: str = Field(..., min_length=1, max_length=100)
    max_score: int = Field(..., ge=0, le=1000)
    requirement_level: Optional[Literal["required", "recommended", "skippable"]] = None
    max_attempts: Optional[int] = Field(None, ge=1, le=100)
    key_confirmed: bool = Field(
        False,
        description="Ключ автора проверен методистом в предпросмотре; на запись не влияет",
    )
    task_content: Dict[str, Any]
    solution_rules: Dict[str, Any]


class LessonImportDraft(BaseModel):
    """Черновик урока: результат разбора и тело публикации."""

    lesson_num: int = Field(..., ge=1, le=999)
    lesson_title: str = Field(..., min_length=1, max_length=500)
    nodes: List[LessonImportNode] = Field(default_factory=list, max_length=4)
    materials: List[LessonImportMaterial] = Field(default_factory=list, max_length=50)
    tasks: List[LessonImportTask] = Field(default_factory=list, max_length=300)
    warnings: List[str] = Field(default_factory=list)


class LessonImportPublishResult(BaseModel):
    """Итог публикации: что создано и где смотреть урок."""

    lesson_course_id: int
    lesson_course_uid: str
    node_ids: Dict[str, int]
    materials_created: int
    materials_updated: int
    tasks_created: int
    tasks_updated: int
    warnings: List[str] = Field(default_factory=list)


class LessonImportJob(BaseModel):
    """Фоновый разбор презентации: состояние и, когда готово, разметка блока."""

    id: str
    status: Literal["queued", "running", "ready", "failed"]
    error: Optional[str] = None
    html: Optional[str] = Field(None, description="Разметка SlideShowBlock для текстового материала")
    states: Optional[int] = Field(None, description="Сколько кадров (состояний по щелчкам)")
    screens: Optional[int] = Field(None, description="Сколько слайдов автора")
