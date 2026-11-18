"""Контракты карты прогресса ЕГЭ/ОГЭ (tsk-1523), без API и persistence."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.task_content import TaskContent

ExamKind = Literal["ege", "oge"]
RunStatus = Literal["in_progress", "paused", "review", "completed"]
CellColor = Literal["green", "yellow", "red", "orange"]
InitialOutcome = Literal["correct", "wrong", "blank"]
ReflectionReason = Literal[
    "missing_rule", "algorithm_error", "calculation_error", "misread_condition",
    "carelessness", "time_shortage", "other",
]


class ExamScaleVersion(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    source_url: str = Field(min_length=1, max_length=2000)
    primary_max: int = Field(ge=1)
    primary_to_result: dict[str, int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_complete_scale(self):
        missing = [str(score) for score in range(self.primary_max + 1) if str(score) not in self.primary_to_result]
        if missing:
            raise ValueError(f"Шкала не содержит первичные баллы: {', '.join(missing)}")
        return self


class ExamVariantItemWrite(BaseModel):
    position: int = Field(ge=1)
    exam_number: int = Field(ge=1)
    task_external_uid: str = Field(min_length=1, max_length=255)
    primary_score: int = Field(ge=1)


class ExamVariantPublish(BaseModel):
    external_uid: str = Field(min_length=1, max_length=255)
    exam_kind: ExamKind
    root_course_uid: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    sequence: int = Field(ge=1)
    published_version: int = Field(ge=1)
    scale: ExamScaleVersion
    items: list[ExamVariantItemWrite] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_items(self):
        positions = [item.position for item in self.items]
        tasks = [item.task_external_uid for item in self.items]
        if positions != list(range(1, len(self.items) + 1)):
            raise ValueError("positions должны быть последовательными с 1")
        if len(set(tasks)) != len(tasks):
            raise ValueError("task_external_uid должен быть уникален")
        grouped_scores: dict[int, list[int]] = {}
        for item in self.items:
            grouped_scores.setdefault(item.exam_number, []).append(item.primary_score)
        if any(len(set(scores)) != 1 for scores in grouped_scores.values()):
            raise ValueError("primary_score внутри номера должен совпадать")
        if sum(scores[0] for scores in grouped_scores.values()) != self.scale.primary_max:
            raise ValueError("Сумма primary_score по номерам не совпадает со шкалой")
        return self


class ExamAnswerAccepted(BaseModel):
    accepted: bool
    run_id: int
    item_id: int
    correct: bool | None = None
    resolution: Literal["rno", "late"] | None = None


class ExamVariantPublishResponse(BaseModel):
    variant_id: int
    published_version: int
    created: bool


class ExamRunItemRead(BaseModel):
    item_id: int
    exam_number: int
    task_id: int
    initial_outcome: InitialOutcome | None
    initial_score: int | None
    review_attempt_count: int = Field(ge=0)
    color: CellColor | None
    review_state: Literal["not_started", "in_progress", "resolved", "late_resolved"] | None
    has_video_review: bool
    available_phase: Literal["initial", "review", "late"] | None
    is_skipped: bool = False
    content: TaskContent


class ExamRunRead(BaseModel):
    run_id: int
    variant_id: int
    root_course_id: int
    exam_kind: ExamKind
    status: RunStatus
    started_at: datetime
    active_seconds: int = Field(ge=0)
    initial_finished_at: datetime | None
    review_finished_at: datetime | None
    items: list[ExamRunItemRead]


class ExamAnswerRequest(BaseModel):
    phase: Literal["initial", "review", "late"]
    client_request_id: str = Field(min_length=8, max_length=100)
    answer: dict
    reflection_reason: ReflectionReason | None = None
    reflection_comment: str | None = Field(default=None, max_length=500)


class ExamMapCell(BaseModel):
    run_id: int
    exam_number: int
    color: CellColor
    review_state: Literal["not_started", "in_progress", "resolved", "late_resolved"]


class ExamMapColumn(BaseModel):
    run_id: int
    variant_id: int
    title: str
    sequence: int
    started_at: datetime
    active_seconds: int = Field(ge=0)
    primary_score: int = Field(ge=0)
    result_label: str
    cells: list[ExamMapCell]


class ExamVariantRead(BaseModel):
    variant_id: int
    title: str
    sequence: int
    run_id: int | None
    can_start: bool


class ExamMapRead(BaseModel):
    exam_kind: ExamKind
    exam_numbers: list[int]
    columns: list[ExamMapColumn]


class ExamReviewQueueStudent(BaseModel):
    student_id: int
    full_name: str | None
    run_id: int


class ExamReviewQueueItem(BaseModel):
    task_id: int
    exam_kind: ExamKind
    exam_number: int
    title: str
    student_count: int = Field(ge=1)
    red_count: int = Field(ge=1)
    students: list[ExamReviewQueueStudent]
