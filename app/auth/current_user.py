"""Dataclass текущего аутентифицированного пользователя."""
from dataclasses import dataclass, field


@dataclass
class CurrentUser:
    id: int
    is_service: bool = False
    tg_id: str | None = None
    email: str | None = None
