from __future__ import annotations
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.schemas.courses import CourseRead
    from app.schemas.users import UserRead
else:
    from app.schemas.courses import CourseRead
    from app.schemas.users import UserRead  # Импортируем для model_rebuild


class UserCourseCreate(BaseModel):
    """
    Создание связи пользователь ↔ курс.

    Правила:
    - Если `order_number` не указан (null), он проставится автоматически триггером БД.
    - (user_id, course_id) уникальны (PK), дубликаты запрещены.
    """
    user_id: int = Field(..., description="ID пользователя", examples=[3])
    course_id: int = Field(..., description="ID курса", examples=[1])
    order_number: Optional[int] = Field(
        None,
        description="Порядковый номер курса у пользователя. null = авто-нумерация триггером БД.",
        examples=[None, 1, 2],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"user_id": 3, "course_id": 1, "order_number": None},
                {"user_id": 3, "course_id": 2, "order_number": 1},
            ]
        }
    )


class UserCourseUpdate(BaseModel):
    """Обновление связи пользователь ↔ курс (обычно меняем только order_number)."""
    order_number: Optional[int] = Field(
        None,
        description="Новый порядковый номер. null = поставить в конец (триггер пересчитает).",
        examples=[1, 2, None],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"order_number": 1},
            ]
        }
    )


class UserCourseRead(BaseModel):
    user_id: int
    course_id: int
    added_at: datetime
    order_number: Optional[int]

    model_config = ConfigDict(from_attributes=True)


class UserCourseBulkCreate(BaseModel):
    """Схема для массовой привязки курсов к пользователю."""
    course_ids: List[int] = Field(
        ...,
        min_length=1,
        description="Список ID курсов для привязки (дубликаты допустимы, но будут проигнорированы на уровне логики/БД).",
        examples=[[1, 2, 3]],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"course_ids": [1, 2, 3]},
            ]
        }
    )


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
    course_id: int = Field(..., description="ID курса", examples=[1])
    order_number: int = Field(..., ge=1, description="Новый порядковый номер (>= 1)", examples=[1, 2, 3])


class UserCourseReorderRequest(BaseModel):
    """Схема для переупорядочивания курсов пользователя."""
    course_orders: List[CourseOrderItem] = Field(
        ...,
        min_length=1,
        description="Список курсов с их порядковыми номерами"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "course_orders": [
                        {"course_id": 1, "order_number": 1},
                        {"course_id": 2, "order_number": 2},
                    ]
                }
            ]
        }
    )


class UserCourseWithUser(BaseModel):
    """
    Схема привязки пользователя к курсу с информацией о пользователе.
    
    Используется в ответе GET /courses/{course_id}/users для предоставления
    полной информации о студенте курса, включая его личные данные.
    """
    user_id: int = Field(..., description="ID пользователя", examples=[3])
    course_id: int = Field(..., description="ID курса", examples=[2])
    added_at: datetime = Field(..., description="Дата и время привязки пользователя к курсу")
    order_number: Optional[int] = Field(None, description="Порядковый номер курса у пользователя", examples=[1, 2, None])
    user: UserRead = Field(..., description="Полная информация о пользователе")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "user_id": 3,
                    "course_id": 2,
                    "added_at": "2025-01-15T10:30:00Z",
                    "order_number": 1,
                    "user": {
                        "id": 3,
                        "email": "student@example.com",
                        "full_name": "Иван Иванов",
                        "tg_id": 123456789,
                        "created_at": "2025-01-10T08:00:00Z"
                    }
                }
            ]
        }
    )


class CourseUsersResponse(BaseModel):
    """
    Схема для списка пользователей курса.
    
    Используется в ответе GET /courses/{course_id}/users.
    Содержит информацию о курсе и список всех студентов, привязанных к нему.
    """
    course_id: int = Field(..., description="ID курса", examples=[2])
    course_title: str = Field(..., description="Название курса", examples=["Анализ данных"])
    users: List[UserCourseWithUser] = Field(..., description="Список студентов курса с полной информацией о каждом")
    total: int = Field(..., description="Общее количество студентов курса (для пагинации)", examples=[3, 10])

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "course_id": 2,
                    "course_title": "Анализ данных",
                    "total": 3,
                    "users": [
                        {
                            "user_id": 4,
                            "course_id": 2,
                            "added_at": "2025-02-06T16:29:40.005259Z",
                            "order_number": None,
                            "user": {
                                "id": 4,
                                "email": "student@example.com",
                                "full_name": "Иван Иванов",
                                "tg_id": None,
                                "created_at": "2025-02-06T11:42:52.667736Z"
                            }
                        }
                    ]
                }
            ]
        }
    )


# Rebuild models для разрешения forward references
UserCourseWithCourse.model_rebuild()
UserCourseListResponse.model_rebuild()
UserCourseWithUser.model_rebuild()
CourseUsersResponse.model_rebuild()