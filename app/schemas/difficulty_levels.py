from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class DifficultyLevelCreate(BaseModel):
    name: str
    weight: int


class DifficultyLevelUpdate(BaseModel):
    name: Optional[str] = None
    weight: Optional[int] = None


class DifficultyLevelRead(BaseModel):
    id: int
    name: str
    weight: int

    class Config:
        model_config = ConfigDict(from_attributes=True)