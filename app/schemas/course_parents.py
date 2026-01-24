# app/schemas/course_parents.py

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class ParentCourseWithOrder(BaseModel):
    """Родительский курс с порядковым номером."""
    parent_course_id: int = Field(..., description="ID родительского курса", examples=[1])
    order_number: Optional[int] = Field(
        None,
        description="Порядковый номер подкурса внутри родителя. Если не указан, устанавливается автоматически триггером БД.",
        examples=[1, 2, 3, None],
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"parent_course_id": 1, "order_number": 1},
                {"parent_course_id": 2, "order_number": None},
            ]
        }
    )


class CourseParentOrderUpdate(BaseModel):
    """Схема для изменения порядкового номера подкурса у родителя."""
    order_number: Optional[int] = Field(
        ...,
        description="Новый порядковый номер подкурса внутри родительского курса. Если null, устанавливается автоматически триггером БД.",
        examples=[1, 2, 3],
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"order_number": 1},
                {"order_number": 2},
            ]
        }
    )
