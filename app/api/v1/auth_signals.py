# app/api/v1/auth_signals.py
"""Попытки входа, о которых оператору стоит знать (tsk-755).

Сегодня платформа отвечает одинаково на любой адрес — «письмо отправлено», — и
это сделано намеренно: иначе по форме входа можно было бы перебором узнать, кто
у нас учится. Цена скрытности в том, что человек, ошибшийся в своём же адресе,
ждёт письма, которого не будет, и никто об этом не знает.

Живой случай 01.09.2026: ученик не мог войти с 26 августа — набирал `1791`
вместо `1701` в собственном адресе. Ещё двое за ту же неделю заказывали ссылки
на адреса, которых нет ни у кого. Все три попытки лежали в журнале, но добраться
до них можно было только запросом к базе.

Раздел read-only: он ничего не чинит, а показывает, кому написать или кого
переспросить. Доступ — персонал школы; для ученика этот список был бы ровно тем
перебором, от которого мы закрываемся.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_role
from app.auth.current_user import CurrentUser
from app.models.audit_event import AuditEvent
from app.schemas.auth_signals import (
    UnknownRecipientAttempt,
    UnknownRecipientAttemptsResponse,
)

router = APIRouter(prefix="/auth/signals", tags=["auth"])

_STAFF_GATE = require_role("teacher", "methodist", "admin")

_MAX_DAYS = 90


@router.get("/unknown-recipients", response_model=UnknownRecipientAttemptsResponse)
async def list_unknown_recipient_attempts(
    days: int = Query(14, ge=1, le=_MAX_DAYS, description="Глубина окна в днях"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_async_db),
    _current_user: CurrentUser = Depends(_STAFF_GATE),
) -> UnknownRecipientAttemptsResponse:
    """Ссылки для входа, заказанные на адреса, которых нет ни у кого.

    Адрес отдаётся целиком, а не маской: половина смысла раздела — увидеть
    опечатку («arttur1791» вместо «arttur1701») и понять, кто это. Раздел
    закрыт ролью персонала, поэтому чужих адресов посторонний тут не увидит.

    Повторные заказы на один и тот же адрес схлопываются в одну строку с числом
    попыток и временем последней: человек, который не может войти, жмёт кнопку
    не один раз, и десять одинаковых строк только мешают увидеть остальных.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        select(AuditEvent.details, AuditEvent.ts, AuditEvent.ip)
        .where(
            AuditEvent.event_type == "magic_link_sent",
            AuditEvent.ts >= since,
            AuditEvent.details["recipient_known"].astext == "false",
        )
        .order_by(AuditEvent.ts.desc())
    )).all()

    grouped: dict[str, UnknownRecipientAttempt] = {}
    for details, ts, ip in rows:
        email = (details or {}).get("email")
        if not email:
            continue
        seen = grouped.get(email)
        if seen is None:
            grouped[email] = UnknownRecipientAttempt(
                email=email,
                attempts=1,
                first_attempt_at=ts,
                last_attempt_at=ts,
                last_ip=str(ip) if ip else None,
            )
            continue
        seen.attempts += 1
        # Строки идут от новых к старым, поэтому самая ранняя — последняя виденная.
        seen.first_attempt_at = ts

    items = sorted(grouped.values(), key=lambda a: a.last_attempt_at, reverse=True)
    return UnknownRecipientAttemptsResponse(
        window_days=days,
        total=len(items),
        items=items[:limit],
    )
