from __future__ import annotations
from typing import Any, Optional 
from pydantic import BaseModel, ConfigDict


class MaterialCreate(BaseModel):
    type: str
    content: Any
    order_position: int
    course_id: int


class MaterialUpdate(BaseModel):
    type: Optional[str] = None
    content: Optional[Any] = None
    order_position: Optional[int] = None
    course_id: Optional[int] = None


class MaterialRead(BaseModel):
    id: int
    type: str
    content: Any
    order_position: int
    course_id: int

    class Config:
        model_config = ConfigDict(from_attributes=True)
