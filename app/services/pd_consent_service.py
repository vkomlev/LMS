"""Согласие на обработку ПДн: проверка тела запроса и запись в журнал (tsk-1335).

Порядок у вызывающего всегда один:
1. `check_consent(...)` ДО создания основной сущности — отказ (422) не должен
   оставлять за собой лида или пользователя;
2. создать сущность (лид, пользователь, magic-link) без коммита;
3. `record_consent(...)` в той же транзакции; коммит — общий.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.pd_consent import PdConsent
from app.schemas.pd_consent import PdConsentFields

logger = logging.getLogger(__name__)
_settings = Settings()

_LOCAL_PROXY_HOSTS = frozenset({"127.0.0.1", "::1"})

MSG_UNDER_14 = "Попроси родителя заполнить форму — нужен его контакт и согласие."
MSG_MINOR_REGISTRATION = (
    "Ученик младше 18 регистрируется после договора и согласия родителя. "
    "Оставь заявку — мы свяжемся с родителем."
)
MSG_CONSENT_REQUIRED = "Нужно согласие на обработку персональных данных."


class PdConsentError(HTTPException):
    """422 с машинным кодом причины: `{"code": ..., "message": ...}`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": code, "message": message},
        )


def client_ip(request: Request) -> Optional[str]:
    """IP посетителя за реверс-прокси.

    Заголовкам верим, только если запрос пришёл с локального прокси (nginx на
    127.0.0.1/::1, см. deploy/vps/nginx-lms.conf): сначала `X-Real-IP` (nginx
    ставит `$remote_addr`, клиент его не подделает), иначе ПОСЛЕДНИЙ адрес
    `X-Forwarded-For` (его дописал прокси; первые клиент мог подставить сам).
    Не с локального адреса — заголовки игнорируются, берём `client.host`.
    Нераспознаваемое значение даёт None: колонка `ip` типа inet.
    """
    host = request.client.host if request.client else None
    candidate = host
    if host in _LOCAL_PROXY_HOSTS:
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        forwarded = request.headers.get("x-forwarded-for") or ""
        if real_ip:
            candidate = real_ip
        elif forwarded.strip():
            candidate = forwarded.split(",")[-1].strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        logger.info("client_ip: нераспознанный IP %r — без адреса", candidate)
        return None


def check_consent(
    fields: PdConsentFields,
    *,
    source: str,
    registration: bool = False,
    enforce: bool = True,
) -> bool:
    """Проверить поля согласия; вернуть True, если согласие надо записать.

    :param registration: регистрируется НОВЫЙ пользователь — несовершеннолетним
        отказ (`pd_minor_registration`).
    :param enforce: False — только записать, если пришло (мини-приложение TG,
        вход существующего пользователя); ничего не блокирует.
    :raises PdConsentError: `pd_minor_registration`, `pd_under_14`,
        `pd_consent_required`.
    """
    role = fields.filler_role
    if enforce and registration and role in ("teen_14_17", "under_14"):
        raise PdConsentError("pd_minor_registration", MSG_MINOR_REGISTRATION)
    if role == "under_14":
        if enforce:
            raise PdConsentError("pd_under_14", MSG_UNDER_14)
        logger.info("pd_consent: source=%s filler_role=under_14 — не записываем", source)
        return False
    if fields.consent is not True or role is None:
        if enforce and _settings.pd_consent_required:
            raise PdConsentError("pd_consent_required", MSG_CONSENT_REQUIRED)
        logger.info(
            "pd_consent: source=%s без согласия (consent=%s, filler_role=%s) — пропускаем",
            source, fields.consent, role,
        )
        return False
    return True


async def record_consent(
    db: AsyncSession,
    request: Request,
    fields: PdConsentFields,
    *,
    source: str,
    page: Optional[str] = None,
    lead_id: Optional[int] = None,
    user_id: Optional[int] = None,
    guest_session_id: Optional[UUID] = None,
    email: Optional[str] = None,
) -> int:
    """Дописать строку журнала в текущей транзакции (без коммита); вернуть id.

    Вызывать только после `check_consent(...) is True`.
    """
    if fields.filler_role is None or fields.filler_role == "under_14":
        raise ValueError("record_consent: недопустимая filler_role для записи")
    row = PdConsent(
        filler_role=fields.filler_role,
        doc_consent_version=_settings.pd_consent_version,
        doc_policy_version=_settings.pd_policy_version,
        source=source,
        page=page,
        lead_id=lead_id,
        user_id=user_id,
        guest_session_id=guest_session_id,
        email=email,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.add(row)
    await db.flush()
    logger.info(
        "pd_consent: записано id=%s source=%s role=%s lead_id=%s user_id=%s",
        row.id, source, fields.filler_role, lead_id, user_id,
    )
    return int(row.id)


async def attach_user(db: AsyncSession, consent_id: int, user_id: int) -> None:
    """Проставить пользователя согласию, данному до его создания (magic-link)."""
    await db.execute(
        update(PdConsent)
        .where(PdConsent.id == consent_id, PdConsent.user_id.is_(None))
        .values(user_id=user_id)
    )
