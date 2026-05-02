"""Embed-API `/embed-api/*` (Phase Y-5).

Display-only iframe для встраивания на внешние сайты (например WP).
JWT URL-token (HS256, TTL 5 мин, single-use через Redis) выдаётся
`POST /embed-api/auth/issue`; iframe читает задачу через
`GET /embed-api/courses/{course_uid}/task/{external_uid}?token=...`.

Удалены legacy Y-1 stubs `POST /embed/session` + `POST /embed/session/{id}/attempts`
(см. cross-project drift; cf. tech-spec Y-5 §6.3).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db
from app.core.config import Settings
from app.models.courses import Courses
from app.models.tasks import Tasks
from app.schemas.embed_api import (
    EmbedAuthIssueRequest,
    EmbedAuthIssueResponse,
    EmbedTaskOption,
    EmbedTaskResponse,
)
from app.schemas.task_content import TaskContent
from app.services.auth.embed_token_service import (
    EmbedSecretMissing,
    EmbedTokenConsumed,
    EmbedTokenInvalid,
    consume_token,
    issue_token,
)
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/embed-api", tags=["embed-api"])
_settings = Settings()

_GUEST_ALLOWED_TYPES: tuple[str, ...] = ("SA", "SC", "MC")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _build_deeplink(course_uid: str, external_uid: str) -> str:
    """Построить deeplink_url для CTA «Решить в SPW»."""
    base = _settings.public_base_url.rstrip("/")
    return (
        f"{base}/courses/{course_uid}/task/{external_uid}"
        f"?utm_source=wp-embed&utm_medium=cta&utm_campaign=task_solve"
    )


# ── POST /embed-api/auth/issue ─────────────────────────────────────────────

@router.post(
    "/auth/issue",
    response_model=EmbedAuthIssueResponse,
)
async def issue_embed_token(
    body: EmbedAuthIssueRequest,
    request: Request,
    db: AsyncSession = Depends(get_bare_db),
) -> EmbedAuthIssueResponse:
    """Выдать одноразовый JWT для embed-iframe.

    Requires `course_uid` ∈ public-demo + `external_uid` существует в этом курсе.
    """
    if not _settings.embed_jwt_secret:
        # Fail-secure: не выдаём токены без секрета
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Embed-token сервис не настроен (CB_EMBED_JWT_SECRET).",
        )

    ip = _client_ip(request)
    redis = get_redis(_settings.redis_url)
    if ip and await is_rate_limited(
        redis, f"embed_issue:{ip}", max_requests=60, window_seconds=60
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    # Проверяем существование course (public-demo) и task (по course_id+external_uid)
    course_row = await db.execute(
        select(Courses).where(
            Courses.course_uid == body.course_uid,
            Courses.is_public_demo.is_(True),
        )
    )
    course = course_row.scalar_one_or_none()
    if course is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Курс не найден среди публичных демо.",
        )
    task_row = await db.execute(
        select(Tasks).where(
            Tasks.course_id == course.id,
            Tasks.external_uid == body.external_uid,
        )
    )
    task = task_row.scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Задача не найдена в указанном демо-курсе.",
        )

    issued = await issue_token(
        redis=redis,
        secret=_settings.embed_jwt_secret,
        course_uid=body.course_uid,
        external_uid=body.external_uid,
        ttl_sec=_settings.embed_jwt_ttl_sec,
    )
    return EmbedAuthIssueResponse(token=issued.token, expires_at=issued.expires_at)


# ── GET /embed-api/courses/{course_uid}/task/{external_uid}?token=... ──────

@router.get(
    "/courses/{course_uid}/task/{external_uid}",
    response_model=EmbedTaskResponse,
)
async def read_embed_task(
    request: Request,
    course_uid: str = Path(..., description="course_uid публичного demo-курса"),
    external_uid: str = Path(..., description="external_uid задачи"),
    token: str = Query(..., description="Одноразовый JWT, выданный /embed-api/auth/issue"),
    db: AsyncSession = Depends(get_bare_db),
) -> EmbedTaskResponse:
    """Прочитать display-only payload задачи для встраивания в iframe.

    Single-use enforce: повторный read с тем же token → 401 token_consumed.
    Payload не содержит correct_answer / solution_rules.
    """
    ip = _client_ip(request)
    redis = get_redis(_settings.redis_url)
    if ip and await is_rate_limited(
        redis, f"embed_read:{ip}", max_requests=600, window_seconds=60
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")

    try:
        await consume_token(
            redis=redis,
            secret=_settings.embed_jwt_secret,
            token=token,
            expected_course_uid=course_uid,
            expected_external_uid=external_uid,
        )
    except EmbedSecretMissing as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Embed-token сервис не настроен.",
        ) from exc
    except EmbedTokenConsumed as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Токен уже использован",
        ) from exc
    except EmbedTokenInvalid as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Токен недействителен или истёк",
        ) from exc

    # Загрузка задачи через тот же ACL что в /learning/guest/task
    course_row = await db.execute(
        select(Courses).where(
            Courses.course_uid == course_uid,
            Courses.is_public_demo.is_(True),
        )
    )
    course = course_row.scalar_one_or_none()
    if course is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Курс не найден среди публичных демо.",
        )
    task_row = await db.execute(
        select(Tasks).where(
            Tasks.course_id == course.id,
            Tasks.external_uid == external_uid,
        )
    )
    task = task_row.scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Задача не найдена.",
        )

    try:
        content = TaskContent.model_validate(task.task_content)
    except Exception as exc:
        logger.warning(
            "embed_api: некорректный task_content task_id=%s: %s", task.id, exc
        )
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Задача недоступна.",
        ) from exc

    if content.type not in _GUEST_ALLOWED_TYPES:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Тип задачи не поддерживается во встраиваемом просмотре.",
        )

    options: list[EmbedTaskOption] | None = None
    if content.type in ("SC", "MC") and content.options:
        options = [
            EmbedTaskOption(id=opt.id, label=opt.text)
            for opt in content.options
            if opt.is_active
        ]

    return EmbedTaskResponse(
        task_id=task.id,
        external_uid=external_uid,
        course_uid=course_uid,
        type=content.type,  # type: ignore[arg-type]
        stem=content.stem,
        options=options,
        deeplink_url=_build_deeplink(course_uid, external_uid),
    )
