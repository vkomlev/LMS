"""Pydantic схемы для /me эндпоинтов (Phase Y-1 + Y-3 + Y-6.2)."""
from datetime import date, datetime
from typing import Literal, Union

from pydantic import BaseModel, Field


class MeResponse(BaseModel):
    id: int
    email: str | None
    tg_id: str | None
    is_service: bool

    model_config = {"from_attributes": True}


# ── Phase Y-3: /me/identities ────────────────────────────────────────────────

class IdentityRead(BaseModel):
    """Identity link для public read (с masked value)."""

    kind: Literal["email", "tg", "vk"]
    value_masked: str
    created_at: datetime
    last_used_at: datetime | None


# ── Phase Y-3: /me/courses ───────────────────────────────────────────────────

class CourseProgress(BaseModel):
    tasks_total: int
    tasks_done: int
    materials_total: int
    materials_done: int
    percent: int


class CourseWithProgressRead(BaseModel):
    course_id: int
    course_uid: str | None
    title: str
    order_number: int | None
    progress: CourseProgress
    last_active_at: datetime | None
    is_completed: bool


# ── Phase Y-3: /me/last-position ─────────────────────────────────────────────

class LastPositionRead(BaseModel):
    course_id: int
    course_uid: str | None
    course_title: str
    # Корневой курс дерева (root) для построения навигации в SPW. Отличается от
    # course_id, когда элемент в листовом подкурсе. Если корень не определён —
    # совпадает с листовым course_id/course_uid (tsk-127).
    root_course_id: int | None = None
    root_course_uid: str | None = None
    type: Literal["task", "material", "course_completed", "none"]
    task_id: int | None = None
    external_uid: str | None = None
    material_id: int | None = None
    last_active_at: datetime


# ── Phase Y-3: /me/streak ────────────────────────────────────────────────────

class StreakRead(BaseModel):
    streak_days: int
    last_active_date: date | None
    today_active: bool


# ── Phase Y-4: /me/history ───────────────────────────────────────────────────

class HistoryItem(BaseModel):
    """Запись истории попыток ученика."""

    task_result_id: int
    task_id: int
    task_external_uid: str | None
    course_id: int | None
    course_uid: str | None
    course_title: str | None
    task_title: str | None
    type: str | None
    status: Literal["pending_review", "passed", "failed"]
    score: int | None
    max_score: int | None
    comment: str | None
    received_at: datetime
    submitted_at: datetime
    checked_at: datetime | None


# ── Phase Y-6.2: /me/courses/{course_id}/syllabus-states ─────────────────────

SyllabusTaskStatus = Literal[
    "passed",
    "pending_review",
    "failed",
    "blocked_limit",
    "in_progress",
    "not_started",
    "skipped",
]

SyllabusMaterialStatus = Literal["completed", "not_started", "skipped"]
RequirementLevel = Literal["skippable", "recommended", "required"]


class SyllabusTaskItem(BaseModel):
    """Состояние задания в syllabus-дереве курса."""

    kind: Literal["task"] = "task"
    task_id: int
    course_id: int = Field(..., description="ID owner-курса (subcourse, не root)")
    status: SyllabusTaskStatus
    requirement_level: RequirementLevel
    is_active: bool = True
    attempts_used: int
    attempts_limit_effective: int
    last_score: int | None
    last_max_score: int | None
    last_submitted_at: datetime | None


class SyllabusMaterialItem(BaseModel):
    """Состояние материала в syllabus-дереве курса."""

    kind: Literal["material"] = "material"
    material_id: int
    course_id: int = Field(..., description="ID owner-курса (subcourse, не root)")
    status: SyllabusMaterialStatus
    requirement_level: RequirementLevel
    is_active: bool = True
    completed_at: datetime | None


SyllabusItem = Union[SyllabusTaskItem, SyllabusMaterialItem]


class SyllabusSectionMeta(BaseModel):
    """Метаданные подкурса в syllabus — для рендера sticky-headers и иерархии (Phase Y-6.2)."""

    course_id: int
    title: str
    depth: int = Field(..., description="0 для root, 1+ для подкурсов")
    parent_course_id: int | None = Field(
        None, description="None для root; для подкурса — ID непосредственного родителя в обходе"
    )
    order_number: int | None = Field(
        None, description="course_parents.order_number (для отладки/UI sort внутри одного уровня)"
    )


class SyllabusStatesResponse(BaseModel):
    """Снимок состояний всех задач+материалов поддерева курса для рендера syllabus.

    Phase Y-6.2: SPW использует для рендера дерева курса с per-item статусами
    (passed / pending_review / failed / blocked / in_progress / not_started)
    и для блокировки subcourse-узлов через `blocked_courses` (course_dependencies
    не выполнены).

    `sections` (Y-6.2 ext): depth-first walk дерева с titles+depth — нужен SPW
    для рендера sticky-headers подкурсов (`/courses/{id}/tree` legacy
    service-key only, недоступен под cookie auth).
    """

    course_id: int
    items: list[SyllabusItem]
    blocked_courses: list[int]
    sections: list[SyllabusSectionMeta] = Field(
        default_factory=list,
        description=(
            "Depth-first walk дерева курса с metadata подкурсов "
            "(course_id, title, depth, parent_course_id, order_number). "
            "Order — тот же, по которому emit'ятся items. Phase Y-6.2 SPW."
        ),
    )
