from __future__ import annotations
from typing import Any, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class MessageCreate(BaseModel):
    message_type: str
    content: Any
    sender_id: Optional[int] = None
    recipient_id: int
    source_system: Optional[str] = None


class MessageUpdate(BaseModel):
    content: Optional[Any] = None
    is_read: Optional[bool] = None


class MessageRead(BaseModel):
    id: int
    message_type: str
    content: Any
    sender_id: Optional[int]
    recipient_id: int
    sent_at: datetime
    is_read: bool
    source_system: str

    class Config:
        model_config = ConfigDict(from_attributes=True)
