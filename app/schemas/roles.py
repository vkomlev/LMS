from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class RoleCreate(BaseModel):
    name: str


class RoleUpdate(BaseModel):
    name: Optional[str] = None


class RoleRead(BaseModel):
    id: int
    name: str

    class Config:
        model_config = ConfigDict(from_attributes=True)
