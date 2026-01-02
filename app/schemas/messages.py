from __future__ import annotations
from typing import Any, Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    message_type: str
    content: Any
    sender_id: Optional[int] = None
    recipient_id: int
    source_system: Optional[str] = None

    # 🔽 новое: ссылки на сообщения
    reply_to_id: Optional[int] = None
    thread_id: Optional[int] = None
    forwarded_from_id: Optional[int] = None

    # 🔽 новое: вложение
    attachment_url: Optional[str] = None
    attachment_id: Optional[str] = None



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

    # 🔽 новое: ссылки на сообщения
    reply_to_id: Optional[int] = None
    thread_id: Optional[int] = None
    forwarded_from_id: Optional[int] = None

    # 🔽 новое: вложение
    attachment_url: Optional[str] = None
    attachment_id: Optional[str] = None

    class Config:
        model_config = ConfigDict(from_attributes=True)

class MarkReadRequest(BaseModel):
    user_id: int = Field(..., description="Кто помечает (получатель сообщений)")
    message_ids: List[int] = Field(..., min_length=1)

class MarkReadBySenderRequest(BaseModel):
    user_id: int
    sender_id: int

class MarkReadResponse(BaseModel):
    updated_count: int
