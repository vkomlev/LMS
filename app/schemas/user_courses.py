from __future__ import annotations
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.schemas.courses import CourseRead
else:
    from app.schemas.courses import CourseRead  # Импортируем для model_rebuild


class UserCourseCreate(BaseModel):
    user_id: int
    course_id: int
    order_number: Optional[int] = None


class UserCourseUpdate(BaseModel):
    order_number: Optional[int] = None


class UserCourseRead(BaseModel):
    user_id: int
    course_id: int
    added_at: datetime
    order_number: Optional[int]

    model_config = ConfigDict(from_attributes=True)


class UserCourseBulkCreate(BaseModel):
    """Схема для массовой привязки курсов к пользователю."""
    course_ids: List[int] = Field(..., min_length=1, description="Список ID курсов для привязки")


class UserCourseWithCourse(BaseModel):
    """Схема привязки пользователя к курсу с информацией о курсе."""
    user_id: int
    course_id: int
    added_at: datetime
    order_number: Optional[int]
    course: "CourseRead"  # type: ignore

    model_config = ConfigDict(from_attributes=True)


class UserCourseListResponse(BaseModel):
    """Схема для списка курсов пользователя с информацией о курсах."""
    user_id: int
    courses: List[UserCourseWithCourse]

    model_config = ConfigDict(from_attributes=True)


class CourseOrderItem(BaseModel):
    """Элемент для переупорядочивания курса."""
    course_id: int
    order_number: int = Field(..., ge=1, description="Порядковый номер курса")


class UserCourseReorderRequest(BaseModel):
    """Схема для переупорядочивания курсов пользователя."""
    course_orders: List[CourseOrderItem] = Field(
        ...,
        min_length=1,
        description="Список курсов с их порядковыми номерами"
    )


# Rebuild models для разрешения forward references
UserCourseWithCourse.model_rebuild()
UserCourseListResponse.model_rebuild()
