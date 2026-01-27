# app/schemas/teacher_courses.py

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class TeacherCourseCreate(BaseModel):
    """Схема для создания связи преподаватель ↔ курс."""
    teacher_id: int = Field(..., description="ID преподавателя", examples=[16, 17])
    course_id: int = Field(..., description="ID курса", examples=[1, 2, 3])
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"teacher_id": 16, "course_id": 1},
                {"teacher_id": 17, "course_id": 2}
            ]
        }
    )


class TeacherCourseRead(BaseModel):
    """Схема для чтения связи преподаватель ↔ курс."""
    teacher_id: int = Field(..., description="ID преподавателя", examples=[16, 17])
    course_id: int = Field(..., description="ID курса", examples=[1, 2, 3])
    linked_at: datetime = Field(..., description="Дата и время привязки", examples=["2026-01-26T14:21:50.221Z"])
    
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {"teacher_id": 16, "course_id": 1, "linked_at": "2026-01-26T14:21:50.221Z"}
            ]
        }
    )
