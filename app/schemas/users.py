from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict, Field


class UserCreate(BaseModel):
    """Схема для создания пользователя."""
    email: EmailStr = Field(..., description="Email пользователя (уникальный)", examples=["student@example.com"])
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
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"full_name": "Новое Имя"},
                {"email": "newemail@example.com", "full_name": "Обновленное Имя"},
                {"tg_id": 987654321}
            ]
        }
    )


class UserRead(BaseModel):
    """Схема для чтения информации о пользователе."""
    id: int = Field(..., description="ID пользователя в системе", examples=[1, 13, 16])
    email: EmailStr = Field(..., description="Email пользователя", examples=["student@example.com"])
    full_name: Optional[str] = Field(None, description="Полное имя пользователя", examples=["Иван Иванов", None])
    tg_id: Optional[int] = Field(None, description="Telegram ID пользователя", examples=[123456789, None])
    created_at: datetime = Field(..., description="Дата и время регистрации пользователя", examples=["2026-01-26T14:21:50.221Z"])

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

class Config:
    model_config = ConfigDict(from_attributes=True)