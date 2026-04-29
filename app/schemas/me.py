"""Pydantic схемы для /me эндпоинтов (Phase Y-1 + Y-3)."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


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
