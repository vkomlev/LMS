"""Dataclass текущего аутентифицированного пользователя."""
from dataclasses import dataclass, field


@dataclass
class CurrentUser:
    id: int
    is_service: bool = False
    # tsk-1547: сервисный ключ с capability вычитки черновиков подсказок
    # (HINT_REVIEW_API_KEY). Отличать от человека и от прочих сервисных
    # ключей — review-эндпоинт пускает только такой.
    service_hint_review: bool = False
    tg_id: str | None = None
    email: str | None = None
