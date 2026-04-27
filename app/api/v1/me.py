"""Эндпоинт /me — профиль текущего пользователя."""
from fastapi import APIRouter, Depends

from app.api.deps import require_authenticated
from app.auth.current_user import CurrentUser
from app.schemas.me import MeResponse

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeResponse)
async def get_me(
    current_user: CurrentUser = Depends(require_authenticated),
) -> MeResponse:
    """Вернуть профиль аутентифицированного пользователя."""
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        tg_id=current_user.tg_id,
        is_service=current_user.is_service,
    )
