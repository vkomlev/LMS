"""Каналы уведомлений ВКонтакте и MAX (tsk-1253, этап Б).

Кабинет ученика:
- `GET /me/messengers` — какие каналы доступны школе и какие подключены;
- `POST /me/messengers/{channel}/link` — ссылка с одноразовым токеном (5 минут);
- `DELETE /me/messengers/{channel}` — «Отключить уведомления».

События сервисов (без сессии, проверка секретом):
- `POST /integrations/vk/callback` — Callback API сообщества ВК;
- `POST /integrations/max/webhook` — вебхук бота MAX.

Логика — в `app.services.messenger_channel_service`.
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_db, require_authenticated, require_role
from app.auth.current_user import CurrentUser
from app.core.config import Settings
from app.services import messenger_channel_service as svc
from app.services.auth import link_token_service

logger = logging.getLogger(__name__)

me_router = APIRouter(prefix="/me/messengers", tags=["me"])
hook_router = APIRouter(prefix="/integrations", tags=["messenger-hooks"])


class MessengerState(BaseModel):
    """Канал в кабинете: показывать ли кнопку и подключён ли."""

    channel: Literal["vk", "max"]
    available: bool
    linked: bool


class MessengerLinkResponse(BaseModel):
    """Ссылка привязки с одноразовым токеном."""

    url: str
    expires_at: datetime


@me_router.get("", response_model=list[MessengerState])
async def messenger_states(
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> list[MessengerState]:
    """Состояние каналов ВК и MAX для кабинета."""
    return [MessengerState(channel=s.channel, available=s.available, linked=s.linked)
            for s in await svc.states(db, current_user.id)]


@me_router.post("/{channel}/link", response_model=MessengerLinkResponse)
async def issue_messenger_link(
    channel: Literal["vk", "max"],
    current_user: CurrentUser = Depends(require_authenticated),
) -> MessengerLinkResponse:
    """Выдать ссылку на сообщество ВК / бота MAX (действует 5 минут)."""
    try:
        url, expires_at = await svc.issue_link(current_user.id, channel)
    except svc.ChannelNotConfiguredError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Канал у школы не подключён")
    except link_token_service.LinkTokenServiceUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервис привязки временно недоступен. Попробуйте через минуту.",
        )
    return MessengerLinkResponse(url=url, expires_at=expires_at)


@me_router.delete("/{channel}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def disable_messenger(
    channel: Literal["vk", "max"],
    current_user: CurrentUser = Depends(require_authenticated),
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """Отключить уведомления в канале (сразу)."""
    await svc.disable_by_user(db, current_user.id, channel)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _bind_and_reply(db: AsyncSession, channel: svc.Channel, token: str, external_id: str) -> None:
    """Привязка по токену из события + ответ человеку в переписке."""
    try:
        await svc.bind(db, channel, token, external_id)
        await svc.reply_safe(channel, external_id, svc.LINKED_TEXT)
    except link_token_service.LinkTokenError:
        await svc.reply_safe(channel, external_id, svc.EXPIRED_TEXT)
    except svc.ChannelConflictError:
        await db.rollback()
        await svc.reply_safe(channel, external_id, svc.CONFLICT_TEXT)


async def _stop_and_reply(db: AsyncSession, channel: svc.Channel, external_id: str) -> None:
    if await svc.disable_by_external(db, channel, external_id, "stop_word") is not None:
        await svc.reply_safe(channel, external_id, svc.STOPPED_TEXT)


@hook_router.post("/vk/callback", response_class=PlainTextResponse)
async def vk_callback(request: Request, db: AsyncSession = Depends(get_async_db)) -> str:
    """Callback API сообщества. ВК ждёт ровно «ok», иначе повторяет событие."""
    s = Settings()
    if not svc.configured("vk", s):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    body: dict[str, Any] = await request.json()
    if not hmac.compare_digest(str(body.get("secret", "")), s.vk_callback_secret) \
            or str(body.get("group_id")) != s.vk_group_id.strip().lstrip("-"):
        logger.warning("tsk-1253: событие ВК с неверным секретом или сообществом отклонено")
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    kind = body.get("type")
    obj = body.get("object") or {}
    try:
        if kind == "confirmation":
            return s.vk_callback_confirmation
        if kind == "message_new":
            msg = obj.get("message") or {}
            from_id = str(msg.get("from_id", ""))
            if from_id and not from_id.startswith("-"):
                ref = (msg.get("ref") or "").strip()
                body_text = msg.get("text") or ""
                if ref:
                    await _bind_and_reply(db, "vk", ref, from_id)
                elif svc.is_stop_word(body_text):
                    await _stop_and_reply(db, "vk", from_id)
                else:
                    # tsk-1398: ответ на вопрос о занятии или сообщение преподавателю.
                    await svc.route_inbound(db, channel="vk", external_id=from_id, body=body_text)
        elif kind == "message_allow":
            key = (obj.get("key") or "").strip()
            if key and obj.get("user_id"):
                await _bind_and_reply(db, "vk", key, str(obj["user_id"]))
        elif kind == "message_deny" and obj.get("user_id"):
            await svc.disable_by_external(db, "vk", str(obj["user_id"]), "message_deny")
    except Exception:
        # «ok» всё равно: иначе ВК будет слать то же событие снова и снова.
        logger.exception("tsk-1253: событие ВК %s не обработано", kind)
        await db.rollback()
    return "ok"


@hook_router.post("/max/webhook")
async def max_webhook(request: Request, db: AsyncSession = Depends(get_async_db)) -> dict[str, bool]:
    """Вебхук бота MAX: секрет в заголовке X-Max-Bot-Api-Secret."""
    s = Settings()
    if not svc.configured("max", s):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    got = request.headers.get("X-Max-Bot-Api-Secret", "")
    if not hmac.compare_digest(got, s.max_webhook_secret):
        logger.warning("tsk-1253: событие MAX с неверным секретом отклонено")
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    body: dict[str, Any] = await request.json()
    kind = body.get("update_type")
    try:
        if kind == "bot_started":
            user_id = str((body.get("user") or {}).get("user_id", ""))
            payload = (body.get("payload") or "").strip()
            if user_id and payload:
                await _bind_and_reply(db, "max", payload, user_id)
        elif kind == "bot_stopped":
            user_id = str((body.get("user") or {}).get("user_id", ""))
            if user_id:
                await svc.disable_by_external(db, "max", user_id, "bot_stopped")
        elif kind == "message_created":
            msg = body.get("message") or {}
            sender = msg.get("sender") or {}
            user_id = str(sender.get("user_id", ""))
            text_ = ((msg.get("body") or {}).get("text") or "")
            if not user_id or sender.get("is_bot"):
                pass
            elif svc.is_stop_word(text_):
                await _stop_and_reply(db, "max", user_id)
            else:
                # tsk-1398: ответ на вопрос о занятии или сообщение преподавателю.
                await svc.route_inbound(db, channel="max", external_id=user_id, body=text_)
    except Exception:
        logger.exception("tsk-1253: событие MAX %s не обработано", kind)
        await db.rollback()
    return {"ok": True}


# ─── tsk-1398, этап 5: экстренная связь с учеником ─────────────────────────

staff_router = APIRouter(prefix="/teacher/students", tags=["teacher"])


class UrgentMessageRequest(BaseModel):
    """Текст срочного сообщения ученику."""

    text: str = Field(min_length=1, max_length=3000)


class UrgentMessageResponse(BaseModel):
    """Итог: сообщение в переписке и каналы, куда оно ушло."""

    message_id: int
    delivered: list[str]
    available: list[str]


@staff_router.get("/{student_id}/urgent-channels", response_model=list[str])
async def urgent_channels(
    student_id: int,
    _: CurrentUser = Depends(require_role("teacher", "methodist", "admin")),
    db: AsyncSession = Depends(get_async_db),
) -> list[str]:
    """Куда дойдёт срочное сообщение (пусто — у ученика нет каналов)."""
    return [c for c, _ in await svc.student_channels(db, student_id)]


@staff_router.post("/{student_id}/urgent-message", response_model=UrgentMessageResponse)
async def send_urgent_message(
    student_id: int,
    body: UrgentMessageRequest,
    current_user: CurrentUser = Depends(require_role("teacher", "methodist", "admin")),
    db: AsyncSession = Depends(get_async_db),
) -> UrgentMessageResponse:
    """Написать ученику во все его мессенджеры сразу, минуя кабинет.

    Сообщение сохраняется в переписке (история), ответ ученика из мессенджера
    ляжет в ту же ветку.
    """
    from sqlalchemy import text as sql

    from app.services import notification_names
    from app.services.messages_service import MessagesService

    is_student = (await db.execute(sql(
        "SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
        "WHERE ur.user_id = :u AND r.name = 'student'"
    ), {"u": student_id})).first()
    if is_student is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ученик не найден")
    available = [c for c, _ in await svc.student_channels(db, student_id)]
    if not available:
        raise HTTPException(status.HTTP_409_CONFLICT, "У ученика не подключён ни один мессенджер")
    msg = await MessagesService().send_message(
        db, message_type="text", content={"text": body.text},
        recipient_id=student_id, sender_id=current_user.id, source_system="urgent",
    )
    await db.commit()
    name = await notification_names.user_display_name(db, current_user.id)
    delivered = await svc.send_urgent(db, student_id=student_id, sender_name=name, body=body.text)
    logger.info("tsk-1398: срочное сообщение %s ученику %s, каналы %s", msg.id, student_id, delivered)
    return UrgentMessageResponse(message_id=msg.id, delivered=delivered, available=available)
