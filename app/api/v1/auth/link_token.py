"""Эндпоинт выпуска одноразового link_token для привязки identity (Phase Y-3).

Авторизованный пользователь запрашивает короткоживущий (TTL 5 мин) one-time
токен, который будет передан в /me/identity/{kind}/link при подтверждении владения
новой identity (TG initData / VK PKCE / magic-link).

См. tech-spec Y-3 (LMS backend) §5.5, §7.7.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import require_authenticated
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.auth import LinkTokenIssueRequest, LinkTokenIssueResponse
from app.services.audit_service import log_event
from app.services.auth import link_token_service
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/link-token", tags=["auth"])
_settings = Settings()


@router.post(
    "/issue",
    response_model=LinkTokenIssueResponse,
    status_code=status.HTTP_200_OK,
)
async def issue_link_token(
    body: LinkTokenIssueRequest,
    request: Request,
    current_user: CurrentUser = Depends(require_authenticated),
    db=Depends(get_async_db),
) -> LinkTokenIssueResponse:
    """Выпустить one-time link_token для current_user под заданный kind."""
    ip = request.client.host if request.client else "unknown"
    redis = get_redis(_settings.redis_url)

    # Rate-limit 10/мин per user (см. tech-spec §5.5)
    rl_key = f"link_token_issue:user:{current_user.id}"
    if await is_rate_limited(redis, rl_key, max_requests=10, window_seconds=60):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много запросов на выпуск link_token",
        )

    try:
        raw_token, expires_at = await link_token_service.issue(
            redis, user_id=current_user.id, kind=body.kind
        )
    except link_token_service.LinkTokenServiceUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервис привязки временно недоступен. Попробуйте через минуту.",
        )

    await log_event(
        db,
        "auth.link_token.issued",
        user_id=current_user.id,
        ip=ip,
        details={"kind": body.kind},
    )
    await db.commit()

    return LinkTokenIssueResponse(link_token=raw_token, expires_at=expires_at)
