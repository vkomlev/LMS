"""Гость ветки квиза в ученическом боте (tsk-1139, Ф4).

Бот зовёт эти функции через `/integrations/quiz-funnel/bot/*` сервисным ключом:
/start по токену → PDF ветки; опросчик забирает, кому пора напомнить; кнопки
«Отписаться» и «Записаться на пробное».

График напоминаний — +1, +3, +7 дней от старта, затем стоп (решение оператора
26.09). Не шлём отписавшимся и уже записавшимся на пробное.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.courses import Courses
from app.models.guest_session import GuestSession
from app.models.lead import Lead, LeadSource
from app.models.quiz_funnel import QuizFunnelBotLead, QuizFunnelBranch
from app.services import quiz_funnel_service
from app.services.lead_magnet_service import LEAD_MAGNET_SOURCE_CODE, find_lead

logger = logging.getLogger(__name__)

#: Смещения напоминаний от старта в боте, по шагам: демо, пробное, последнее.
REMINDER_OFFSETS_DAYS = (1, 3, 7)


@dataclass
class BotStart:
    """Что показать гостю после /start."""

    bot_lead_id: int
    branch_code: str
    pdf_url: Optional[str]
    trial_requested: bool


@dataclass
class DueReminder:
    """Кому и какое по счёту напоминание отправить."""

    bot_lead_id: int
    tg_id: int
    branch_code: str
    step: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_at(started_at: datetime, step: int) -> Optional[datetime]:
    """Когда слать напоминание номер ``step`` (с нуля); None — график исчерпан."""
    if step >= len(REMINDER_OFFSETS_DAYS):
        return None
    return started_at + timedelta(days=REMINDER_OFFSETS_DAYS[step])


async def _load(db: AsyncSession, bot_lead_id: int, tg_id: int) -> QuizFunnelBotLead:
    """Строка гостя, принадлежащая этому tg. Чужой id — как несуществующий."""
    row = await db.get(QuizFunnelBotLead, bot_lead_id)
    if row is None or row.tg_id != tg_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Гость бота не найден.")
    return row


async def _branch_code(db: AsyncSession, quiz_course_id: int) -> str:
    branch = await db.get(QuizFunnelBranch, quiz_course_id)
    return branch.branch_code if branch else "unknown"


async def start(
    db: AsyncSession, *, token: str, tg_id: int, tg_username: Optional[str]
) -> BotStart:
    """Привязать стартовую ссылку к tg и отдать PDF ветки.

    Ссылку могут переслать: первый запустивший становится гостем (ему идут
    напоминания), остальные получают PDF без привязки — подарок не жалко.

    :raises HTTPException 404: воронка выключена или токен неизвестен.
    """
    if not quiz_funnel_service.is_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Воронка выключена.")
    row = (
        await db.execute(select(QuizFunnelBotLead).where(QuizFunnelBotLead.start_token == token))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ссылка устарела.")
    branch = await db.get(QuizFunnelBranch, row.quiz_course_id)

    if row.tg_id is None:
        now = _now()
        row.tg_id = tg_id
        row.tg_username = tg_username
        row.started_at = now
        row.reminder_step = 0
        row.next_reminder_at = _next_at(now, 0)
        await db.flush()
        logger.info("tsk-1139: гость бота bot_lead=%s tg_id=%s", row.id, tg_id)
    elif row.tg_id != tg_id:
        logger.info("tsk-1139: пересланная ссылка bot_lead=%s tg_id=%s", row.id, tg_id)

    return BotStart(
        bot_lead_id=row.id,
        branch_code=branch.branch_code if branch else "unknown",
        pdf_url=branch.pdf_url if branch else None,
        trial_requested=row.trial_requested_at is not None,
    )


async def list_due(db: AsyncSession, limit: int = 50) -> List[DueReminder]:
    """Гости, которым пора напомнить. Пусто, если напоминания выключены."""
    if not quiz_funnel_service.reminders_enabled():
        return []
    rows = (
        await db.execute(
            select(QuizFunnelBotLead, QuizFunnelBranch.branch_code)
            .join(
                QuizFunnelBranch,
                QuizFunnelBranch.quiz_course_id == QuizFunnelBotLead.quiz_course_id,
            )
            .where(
                QuizFunnelBotLead.next_reminder_at <= _now(),
                QuizFunnelBotLead.unsubscribed_at.is_(None),
                QuizFunnelBotLead.trial_requested_at.is_(None),
                QuizFunnelBotLead.tg_id.is_not(None),
            )
            .order_by(QuizFunnelBotLead.next_reminder_at)
            .limit(limit)
        )
    ).all()
    return [
        DueReminder(bot_lead_id=r.id, tg_id=int(r.tg_id), branch_code=code, step=r.reminder_step)
        for r, code in rows
    ]


async def mark_sent(db: AsyncSession, *, bot_lead_id: int, tg_id: int, step: int) -> None:
    """Отметить отправку шага и назначить следующий. Повтор того же шага — без эффекта."""
    row = await _load(db, bot_lead_id, tg_id)
    if row.reminder_step != step or row.started_at is None:
        return
    row.reminder_step = step + 1
    row.next_reminder_at = _next_at(row.started_at, step + 1)
    await db.flush()


async def unsubscribe(db: AsyncSession, *, bot_lead_id: int, tg_id: int) -> None:
    """Больше не напоминать."""
    row = await _load(db, bot_lead_id, tg_id)
    if row.unsubscribed_at is None:
        row.unsubscribed_at = _now()
    row.next_reminder_at = None
    await db.flush()


async def request_trial(db: AsyncSession, *, bot_lead_id: int, tg_id: int) -> int:
    """Запись на пробное из бота: отметка у гостя и заявка канала «Квиз на сайте».

    Заявка та же, что у квиза (сессия + ветка), если она уже есть; иначе новая
    с контактом в Telegram. Напоминания после записи прекращаются.

    :return: id заявки.
    """
    row = await _load(db, bot_lead_id, tg_id)
    now = _now()
    first_time = row.trial_requested_at is None
    row.trial_requested_at = row.trial_requested_at or now
    row.next_reminder_at = None

    course = await db.get(Courses, row.quiz_course_id)
    session = await db.get(GuestSession, row.guest_session_id)
    branch_code = await _branch_code(db, row.quiz_course_id)
    contact = f"@{row.tg_username}" if row.tg_username else f"tg:{row.tg_id}"
    note = f"Запись на пробное из бота (ветка {branch_code}), Telegram {contact}."
    attribution = {
        **((session.attribution or {}) if session else {}),
        "branch": branch_code,
        "bot": True,
        "trial_requested": True,
    }

    lead = await find_lead(db, row.guest_session_id, row.quiz_course_id)
    if lead is None:
        source_id = (
            await db.execute(
                select(LeadSource.id).where(LeadSource.code == LEAD_MAGNET_SOURCE_CODE)
            )
        ).scalar_one_or_none()
        if source_id is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Приём заявок недоступен.")
        lead = Lead(
            source_id=source_id,
            source_detail=course.course_uid if course else None,
            contact=contact,
            note=note,
            guest_session_id=row.guest_session_id,
            quiz_course_id=row.quiz_course_id,
            attribution=attribution,
        )
        db.add(lead)
    else:
        lead.attribution = {**(lead.attribution or {}), **attribution}
        if first_time:
            lead.note = f"{lead.note or ''}\n{note}".strip()
    await db.flush()
    row.lead_id = lead.id
    await db.flush()
    logger.info("tsk-1139: пробное из бота bot_lead=%s lead_id=%s", row.id, lead.id)
    return lead.id
