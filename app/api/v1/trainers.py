"""Интерактивные тренажёры `/api/v1/trainers/*` (tsk-1247).

Обе ручки публичные: тренажёр открывают ученик в курсе, гость на витрине и
посетитель чужого сайта во встройке. Сценарий хранится в материале узла типа
`trainer` (`app/services/trainers_service.py`). При выключенном
`TRAINERS_ENABLED` обе ручки отвечают 404 — так же, как на неизвестный slug.
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_bare_db, get_current_user
from app.core.config import Settings
from app.schemas.trainer_scenario import SLUG_PATTERN
from app.schemas.trainers import TrainerEventsRequest, TrainerEventsResponse, TrainerRead
from app.services import trainers_service
from app.services.rate_limit_service import get_redis, is_rate_limited
from app.utils.exceptions import DomainError

logger = logging.getLogger("api.trainers")
router = APIRouter(prefix="/trainers", tags=["trainers"])
_settings = Settings()

#: Кеш сценария у клиента и на прокси. Правка сценария видна через пять минут;
#: ETag избавляет от повторной загрузки ~30 КБ, пока сценарий не менялся.
_CACHE_CONTROL = "public, max-age=300"
#: Лимиты по IP. Чтение — как у гостевой выдачи заданий. События идут пакетом
#: раз в уровень (бриф Р7); запас — на класс за одним NAT.
_READ_LIMIT = (600, 60)
_EVENTS_LIMIT = (120, 60)
#: Второй ключ лимита событий — на того, кто пишет: ученик, гостевая сессия,
#: у встройки — slug + IP. Пакет идёт раз в уровень, 30 в минуту — с запасом;
#: без него один IP заливал бы `guest_attempt` до 24 000 строк в минуту.
_EVENTS_PER_WRITER_LIMIT = (30, 60)

_NOT_FOUND = "Тренажёр не найден"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _limit(request: Request, name: str, rule: tuple[int, int], key: str | None = None) -> None:
    """429 при превышении. `key` — свой ключ лимита; без него — IP клиента."""
    key = key or _client_ip(request)
    if not key:
        return
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(redis, f"{name}:{key}", max_requests=rule[0], window_seconds=rule[1]):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов")


async def _load(db: AsyncSession, slug: str) -> dict:
    if not trainers_service.trainers_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    trainer = await trainers_service.find_trainer(db, slug)
    if trainer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return trainer


@router.get(
    "/{slug}",
    response_model=TrainerRead,
    summary="Сценарий тренажёра (публично: ученик, гость, встройка)",
    responses={
        304: {"description": "Сценарий не менялся (If-None-Match совпал с ETag)"},
        404: {"description": "Тренажёра нет, он выключен или TRAINERS_ENABLED=false"},
        429: {"description": "Лимит запросов с IP"},
    },
)
async def get_trainer(
    request: Request,
    response: Response,
    slug: str = Path(..., pattern=SLUG_PATTERN),
    if_none_match: str | None = Header(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> TrainerRead | Response:
    """Отдать сценарий `trainer-scenario/1` с версией (ETag) для кеша.

    Ответы шагов в сценарии не скрываются (бриф Р3): движок проверяет ответ в
    браузере, защита — выгодность честного пути, а не секретность.
    """
    await _limit(request, "trainer_read", _READ_LIMIT)
    trainer = await _load(db, slug)
    content = trainer["content"]
    version = trainers_service.scenario_version(content["scenario"])
    etag = f'"{version}"'
    headers = {"ETag": etag, "Cache-Control": _CACHE_CONTROL}
    if if_none_match and etag in [t.strip().removeprefix("W/") for t in if_none_match.split(",")]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return TrainerRead(
        slug=content["slug"],
        title=trainers_service.trainer_title(content, trainer["title"]),
        scenario=content["scenario"],
        version=version,
    )


@router.post(
    "/{slug}/events",
    response_model=TrainerEventsResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="События тренажёра пакетом (ученик / гость / встройка)",
    responses={
        400: {"description": "context=guest без действующей cookie guest_session"},
        401: {"description": "context=student без входа"},
        404: {"description": "Тренажёра нет, он выключен или TRAINERS_ENABLED=false"},
        422: {"description": "Больше 200 событий, неизвестный код ошибки, ввод длиннее 64 символов"},
        429: {"description": "Лимит запросов с IP"},
    },
)
async def post_trainer_events(
    request: Request,
    body: TrainerEventsRequest,
    slug: str = Path(..., pattern=SLUG_PATTERN),
    guest_session: str | None = Cookie(default=None),
    session_token: str | None = Cookie(default=None, alias="session"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_bare_db),
) -> TrainerEventsResponse:
    """Принять пакет событий (≤ 200) и записать по контексту.

    * `student` — ученик из токена входа (cookie `session` или Bearer); сервисный
      ключ не принимается: событие обязано принадлежать человеку.
    * `guest` — по cookie `guest_session` (как ответы гостя на демо-задачу).
    * `embed` — cookie не читаются вовсе; пишутся только обезличенные счётчики.
    """
    await _limit(request, "trainer_events", _EVENTS_LIMIT)
    trainer = await _load(db, slug)
    trainers_service.check_misconceptions(
        body.events, trainers_service.misconception_codes(trainer["content"])
    )

    try:
        if body.context == "student":
            user = await get_current_user(
                db=db, session_token=session_token, authorization=authorization,
                token=None, x_api_key=None, api_key=None,
            )
            await _limit(request, "trainer_events_user", _EVENTS_PER_WRITER_LIMIT, str(user.id))
            accepted = await trainers_service.record_student_events(db, user.id, trainer, body.events)
        elif body.context == "guest":
            if not guest_session:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Требуется cookie guest_session. Сначала вызовите POST /learning/guest/session.",
                )
            try:
                gs_uuid = UUID(guest_session)
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Некорректный формат guest_session cookie."
                ) from exc
            await _limit(request, "trainer_events_guest", _EVENTS_PER_WRITER_LIMIT, str(gs_uuid))
            accepted = await trainers_service.record_guest_events(db, gs_uuid, trainer, body.events)
        else:
            await _limit(
                request, "trainer_events_embed", _EVENTS_PER_WRITER_LIMIT,
                f"{slug}:{_client_ip(request) or '-'}",
            )
            accepted = await trainers_service.record_embed_counters(db, trainer, body.events)
    except (HTTPException, DomainError):
        await db.rollback()
        raise
    await db.commit()
    logger.info(
        "trainer_events slug=%s context=%s received=%s accepted=%s",
        slug, body.context, len(body.events), accepted,
    )
    return TrainerEventsResponse(accepted=accepted)
