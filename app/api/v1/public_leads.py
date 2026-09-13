"""Публичная форма захвата заявки на продающих лендингах сайта (tsk-929).

Кнопка «Записаться» на лендингах victor-komlev.ru вела напрямую в личный
Telegram (`t.me/Vvkomlev`) — заявка нигде не фиксировалась (0 по каналу
«Сайт» за всё время, см. `tsk-827`). Решение оператора 13.09: форма встаёт
ПЕРЕД переходом в Telegram, а не заменяет его — посетитель оставляет контакт,
заявка сразу пишется в LMS, а дальше открывается диалог как раньше.

Без cookie гостевой сессии (в отличие от `guest_quiz.py`): форма вызывается
JS-фетчем с чужого домена (WordPress), и cookie с домена LMS туда не долетит
без дополнительной SameSite/Secure возни, которая здесь ничего не даёт —
дедуп по сессии тут не нужен, повторная заявка того же человека не страшнее,
чем сейчас (маркетолог видит дубли в кабинете так же, как по Авито).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_async_db
from app.schemas.lead import WebsiteLeadRequest, WebsiteLeadResponse
from app.services import lead_service
from app.services.rate_limit_service import get_redis, is_rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/public/leads", tags=["public_leads"])
_settings = Settings()

#: Код канала — справочник заведён миграцией 20260801_120000_tsk505_pricing_and_leads.
_WEBSITE_SOURCE_CODE = "website"

#: Живой человек оставляет контакт с лендинга один раз, редко — дважды (поправил
#: телефон). Как у гостевого квиза (`quiz_lead`): щедро для человека, тесно для спама.
_IP_LIMIT = 10
_WINDOW_SECONDS = 3600


def _client_ip(request: Request) -> str:
    """IP клиента из-за реверс-прокси — `request.client.host` был бы адресом прокси."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post(
    "",
    response_model=WebsiteLeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Заявка с формы на продающем лендинге сайта",
)
async def submit_website_lead(
    body: WebsiteLeadRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> WebsiteLeadResponse:
    """Принять контакт с формы на лендинге перед переходом в Telegram.

    Honeypot `hp`: бот заполняет все поля формы, человек скрытое CSS-полем не
    видит и не трогает. Заполнено — молча отвечаем так же, как при успехе, но
    лида не заводим: явная ошибка подсказала бы боту, какое поле убрать.
    """
    if body.hp:
        logger.info("website_lead: honeypot сработал, page=%s", body.page)
        return WebsiteLeadResponse(lead_id=0)

    ip = _client_ip(request)
    redis = get_redis(_settings.redis_url)
    if await is_rate_limited(
        redis, f"website_lead:{ip}", max_requests=_IP_LIMIT, window_seconds=_WINDOW_SECONDS
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много заявок с этого адреса"
        )

    source_id = await lead_service.get_source_id_by_code(db, _WEBSITE_SOURCE_CODE)
    if source_id is None:
        logger.error("website_lead: в справочнике нет канала '%s'", _WEBSITE_SOURCE_CODE)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Приём заявок временно недоступен."
        )

    lead_id = await lead_service.create_lead(
        db,
        source_id=source_id,
        source_detail=body.page,
        full_name=body.full_name,
        contact=body.contact,
        note=None,
        created_by=None,
    )
    logger.info("website_lead: заявка с лендинга page=%s lead_id=%s", body.page, lead_id)
    return WebsiteLeadResponse(lead_id=lead_id)
