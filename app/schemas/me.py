"""Pydantic схемы для /me эндпоинта."""
from pydantic import BaseModel


class MeResponse(BaseModel):
    id: int
    email: str | None
    tg_id: str | None
    is_service: bool

    model_config = {"from_attributes": True}
