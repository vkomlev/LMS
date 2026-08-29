"""Служебный вход лидов для соседних систем (tsk-718).

Зачем отдельный вход, а не `POST /marketer/leads`. Кабинет маркетолога
намеренно закрыт для сервисных ключей: его гейт отбивает вызов с `X-API-Key`
ещё до обработчика, потому что кабинет — рабочее место человека. Машине нужен
свой вход, и у него другой договор:

- пускает **только сервисный ключ** — это интерфейс между системами, а не
  страница кабинета;
- **идемпотентен**: повторный вызов с той же парой «источник + внешний номер»
  возвращает уже заведённого лида, а не второго. Один человек пишет с площадки
  по нескольким объявлениям и в разное время, и каждое обращение не должно
  становиться новым лидом.

Первый потребитель — переписка Авито в AvitoManager. Переписка целиком сюда не
приезжает: только ссылка на беседу, источник (объявление, город, линейка) и
первое сообщение.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, get_current_user
from app.auth.current_user import CurrentUser
from app.schemas.lead import ExternalLeadCreateRequest, ExternalLeadResponse
from app.services import lead_service

router = APIRouter(prefix="/integrations", tags=["integrations_leads"])


async def _service_only(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Пускать только сервисный ключ.

    Человеку сюда не нужно: у него есть кабинет маркетолога с полной карточкой
    лида. Открыть вход и людям значило бы завести вторую дверь в те же данные
    с другими правилами.
    """
    if not current_user.is_service:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Служебный вход доступен только сервисному ключу",
        )
    return current_user


@router.post(
    "/leads",
    response_model=ExternalLeadResponse,
    summary="Завести лида из внешней системы",
    description=(
        "Идемпотентно по паре `external_source` + `external_id`: повторный "
        "вызов возвращает уже заведённого лида с `created = false`."
    ),
)
async def ingest_lead(
    body: ExternalLeadCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUser = Depends(_service_only),
) -> ExternalLeadResponse:
    source_id = await lead_service.get_source_id_by_code(db, body.source_code)
    if source_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Канал привлечения «{body.source_code}» не найден",
        )
    if lead_service.requires_detail(body.source_code) and not (
        body.source_detail or ""
    ).strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Для канала «Другое» нужно указать, откуда именно пришёл лид",
        )
    lead_id, created = await lead_service.ingest_external_lead(
        db,
        external_source=body.external_source,
        external_id=body.external_id,
        source_id=source_id,
        source_detail=body.source_detail,
        full_name=body.full_name,
        contact=body.contact,
        note=body.note,
    )
    return ExternalLeadResponse(lead_id=lead_id, created=created)
