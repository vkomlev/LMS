from __future__ import annotations
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict


class UserCreate(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    tg_id: Optional[int] = None


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    tg_id: Optional[int] = None


class UserRead(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str]
    tg_id: Optional[int]
    created_at: datetime

class UserID(BaseModel):
    """
    Только идентификатор пользователя.
    """
    id: int

    model_config = ConfigDict(from_attributes=True)

class Config:
    model_config = ConfigDict(from_attributes=True)