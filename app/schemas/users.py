from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator

from app.schemas.me import ProfileCategory, normalize_city, validate_timezone


class UserCreate(BaseModel):
    """Схема для создания пользователя.

    После M1 (Phase Y-1) email nullable; после Y-1.5 auto-create —
    TG/VK пользователи могут не иметь email.
    """
    email: Optional[EmailStr] = Field(
        None,
        description="Email пользователя (опционально после M1; уникален среди не-NULL)",
        examples=["student@example.com", None],
    )
    password_hash: Optional[str] = Field(
        None,
        description="Хэш пароля. Если не передан, сохраняется пустая строка (например, для пользователей из Telegram без пароля).",
        examples=["$2b$12$...", None],
    )
    full_name: Optional[str] = Field(None, description="Полное имя пользователя", examples=["Иван Иванов", "Петр Петров"])
    tg_id: Optional[int] = Field(None, description="Telegram ID пользователя", examples=[123456789, None])
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"email": "student@example.com", "full_name": "Иван Иванов", "tg_id": None},
                {"email": "teacher@example.com", "full_name": "Петр Петров", "tg_id": 123456789}
            ]
        }
    )


class UserUpdate(BaseModel):
    """Схема для обновления пользователя (частичное обновление - все поля опциональны)."""
    email: Optional[EmailStr] = Field(None, description="Email пользователя", examples=["newemail@example.com", None])
    full_name: Optional[str] = Field(None, description="Полное имя пользователя", examples=["Новое Имя", None])
    tg_id: Optional[int] = Field(None, description="Telegram ID пользователя", examples=[987654321, None])
    # tsk-563: доп. поля профиля — те же, что ученик сам редактирует в
    # PATCH /me (tsk-427). Кросс-валидация "класс только у школьника" и
    # каскадный сброс school_grade — не здесь (формат/диапазон), а в
    # me_service.update_profile_extra, который переиспользует обработчик
    # PATCH /users/{id} (app/api/v1/users.py) вместо дублирования правил.
    category: Optional[ProfileCategory] = Field(
        None, description="Категория ученика", examples=["school_student", None]
    )
    school_grade: Optional[int] = Field(
        None, ge=1, le=11,
        description="Класс (1-11) — только для category=school_student",
        examples=[9, None],
    )
    city: Optional[str] = Field(None, max_length=255, description="Город", examples=["Москва", None])
    timezone: Optional[str] = Field(
        None, description="Часовой пояс, IANA-идентификатор", examples=["Europe/Moscow", None]
    )

    @field_validator("city")
    @classmethod
    def _strip_city(cls, v: Optional[str]) -> Optional[str]:
        return normalize_city(v)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        return validate_timezone(v)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"full_name": "Новое Имя"},
                {"email": "newemail@example.com", "full_name": "Обновленное Имя"},
                {"tg_id": 987654321},
                {"category": "school_student", "school_grade": 9, "city": "Москва", "timezone": "Europe/Moscow"},
            ]
        }
    )


class UserRead(BaseModel):
    """Схема для чтения информации о пользователе.

    После M1 (Phase Y-1) email nullable; auto-create через TG/VK
    в Y-1.5 создаёт users с email=NULL.
    """
    id: int = Field(..., description="ID пользователя в системе", examples=[1, 13, 16])
    email: Optional[EmailStr] = Field(None, description="Email пользователя (nullable)", examples=["student@example.com", None])
    full_name: Optional[str] = Field(None, description="Полное имя пользователя", examples=["Иван Иванов", None])
    tg_id: Optional[int] = Field(None, description="Telegram ID пользователя", examples=[123456789, None])
    created_at: datetime = Field(..., description="Дата и время регистрации пользователя", examples=["2026-01-26T14:21:50.221Z"])
    # tsk-432. Отдаём во ВСЕХ выдачах людей, а не только администратору:
    # заблокированный человек по решению остаётся видимым в списках, и пометка
    # «вход закрыт» нужна там же, где его имя. Поле добавочное — потребители,
    # которые о нём не знают, продолжают работать.
    blocked_at: Optional[datetime] = Field(
        None,
        description="Когда закрыт вход. NULL — доступ открыт",
        examples=["2026-07-31T12:00:00Z", None],
    )
    # tsk-563: доп. поля профиля — видны в карточке методисту/админу так же,
    # как самому ученику в MeResponse (tsk-427).
    category: Optional[ProfileCategory] = Field(
        None, description="Категория ученика", examples=["school_student", None]
    )
    school_grade: Optional[int] = Field(
        None, description="Класс (1-11) — только для category=school_student", examples=[9, None]
    )
    city: Optional[str] = Field(None, description="Город", examples=["Москва", None])
    timezone: Optional[str] = Field(
        None, description="Часовой пояс, IANA-идентификатор", examples=["Europe/Moscow", None]
    )

    @field_validator("email", mode="before")
    @classmethod
    def _blank_email_as_none(cls, value: object) -> object:
        """Считать пустую строку в users.email отсутствием почты.

        tsk-363: одна такая строка в БД роняла весь ответ списка
        пользователей 500-й ошибкой на валидации EmailStr, а не только
        собственную запись. Источник течи закрыт в vk_oauth_service,
        валидатор защищает выдачу от исторических строк.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 13,
                    "email": "test_student_1@example.com",
                    "full_name": "Студент Тестовый 1",
                    "tg_id": None,
                    "created_at": "2026-01-26T14:21:50.221Z"
                }
            ]
        }
    )

class UserID(BaseModel):
    """
    Только идентификатор пользователя.
    """
    id: int

    model_config = ConfigDict(from_attributes=True)